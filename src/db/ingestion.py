"""Idempotent raw-to-canonical ingestion for retained EuroLeague fixtures."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import uuid

from duckdb import DuckDBPyConnection
import pandas as pd

from src.data.normalizers import (
    normalize_games,
    normalize_player_boxscore,
    normalize_play_by_play,
    normalize_roster_players,
    normalize_shots,
    normalize_team_boxscore,
)

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .ids import canonical_official_player_code, normalized_text, stable_id
from .raw_store import DEFAULT_RAW_ROOT, StoredRawArtifact, archive_existing_file


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAT_COLUMNS = {
    "points": "points",
    "two_pointers_made": "two_points_made",
    "two_pointers_attempted": "two_points_attempted",
    "three_pointers_made": "three_points_made",
    "three_pointers_attempted": "three_points_attempted",
    "free_throws_made": "free_throws_made",
    "free_throws_attempted": "free_throws_attempted",
    "offensive_rebounds": "offensive_rebounds",
    "defensive_rebounds": "defensive_rebounds",
    "total_rebounds": "total_rebounds",
    "assists": "assists",
    "steals": "steals",
    "turnovers": "turnovers",
    "blocks": "blocks",
    "blocks_received": "blocks_received",
    "fouls_committed": "fouls_committed",
    "fouls_drawn": "fouls_drawn",
    "pir": "pir",
    "plus_minus": "plus_minus",
}
PSEUDO_PLAYER_CODES = {"CO_A", "CO_B"}


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    run_id: str
    inserted_records: int
    raw_artifacts: int
    status: str


class CanonicalIngestor:
    """A single explicit transaction/run for normalized source records."""

    def __init__(
        self,
        connection: DuckDBPyConnection,
        *,
        source: str,
        command: str,
        competition_code: str | None = None,
        season_code: str | None = None,
    ) -> None:
        self.connection = connection
        self.run_id = str(uuid.uuid4())
        self.source = source
        self.inserted = 0
        self.updated = 0
        self.artifact_count = 0
        self._started_at = datetime.now(UTC)
        self._team_codes = {
            str(code): str(team_id)
            for team_id, code in self.connection.execute(
                "SELECT canonical_team_id, official_team_code FROM teams "
                "WHERE official_team_code IS NOT NULL"
            ).fetchall()
        }
        self._player_codes = {
            str(code): str(player_id)
            for player_id, code in self.connection.execute(
                "SELECT canonical_player_id, official_player_code FROM players "
                "WHERE official_player_code IS NOT NULL"
            ).fetchall()
        }
        self._team_alias_ids = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT team_alias_id FROM team_aliases"
            ).fetchall()
        }
        self._player_alias_ids = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT player_alias_id FROM player_aliases"
            ).fetchall()
        }
        self.connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, source, command, started_at, status,
                competition_code, season_code
            ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)
            """,
            [
                self.run_id,
                source,
                command,
                self._started_at,
                competition_code,
                season_code,
            ],
        )

    def finish(self) -> IngestionSummary:
        self.connection.execute(
            """
            UPDATE ingestion_runs
            SET completed_at = ?, status = 'SUCCEEDED',
                records_downloaded = ?, records_inserted = ?
                , records_updated = ?
            WHERE run_id = ?
            """,
            [
                datetime.now(UTC),
                self.artifact_count,
                self.inserted,
                self.updated,
                self.run_id,
            ],
        )
        return IngestionSummary(
            run_id=self.run_id,
            inserted_records=self.inserted,
            raw_artifacts=self.artifact_count,
            status="SUCCEEDED",
        )

    def fail(self, error: BaseException) -> None:
        self.connection.execute(
            """
            UPDATE ingestion_runs
            SET completed_at = ?, status = 'FAILED', error_count = 1,
                errors_json = ?
            WHERE run_id = ?
            """,
            [
                datetime.now(UTC),
                json.dumps({"type": type(error).__name__, "message": str(error)}),
                self.run_id,
            ],
        )

    def insert_ignore(self, table: str, row: Mapping[str, Any]) -> bool:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        returned = self.connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
            "ON CONFLICT DO NOTHING RETURNING 1",
            [row[column] for column in columns],
        ).fetchall()
        inserted = bool(returned)
        self.inserted += int(inserted)
        return inserted

    def insert_many_ignore(
        self, table: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Batch rows with one prepared statement and count net new records."""

        if not rows:
            return 0
        columns = list(rows[0])
        if any(list(row) != columns for row in rows):
            raise ValueError(f"All batched {table} rows must have identical columns")
        before = int(
            self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        relation_name = f"_batch_{uuid.uuid4().hex}"
        frame = pd.DataFrame.from_records(rows, columns=columns)
        self.connection.register(relation_name, frame)
        try:
            self.connection.execute(
                f"INSERT INTO {table} BY NAME SELECT * FROM {relation_name} "
                "ON CONFLICT DO NOTHING"
            )
        finally:
            self.connection.unregister(relation_name)
        after = int(
            self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        added = after - before
        self.inserted += added
        return added

    def insert_many_known_absent(
        self, table: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """Fast batch insert after the caller proves the natural batch is absent."""

        if not rows:
            return 0
        columns = list(rows[0])
        if any(list(row) != columns for row in rows):
            raise ValueError(f"All batched {table} rows must have identical columns")
        relation_name = f"_batch_{uuid.uuid4().hex}"
        frame = pd.DataFrame.from_records(rows, columns=columns)
        self.connection.register(relation_name, frame)
        try:
            self.connection.execute(
                f"INSERT INTO {table} BY NAME SELECT * FROM {relation_name}"
            )
        finally:
            self.connection.unregister(relation_name)
        added = len(rows)
        self.inserted += added
        return added

    def register_artifact(
        self,
        artifact: StoredRawArtifact,
        *,
        source: str,
        endpoint: str,
        source_url: str | None,
        competition_code: str | None,
        season_code: str | None,
        game_code: int | None = None,
        matchday_number: int | None = None,
        fetched_at: datetime | None = None,
        media_type: str = "application/json",
        is_sanitized: bool = False,
    ) -> str:
        try:
            raw_path = artifact.path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            raw_path = str(artifact.path)
        artifact_id = stable_id(
            "raw_artifact",
            source,
            endpoint,
            season_code,
            game_code,
            matchday_number,
            raw_path,
            artifact.sha256,
        )
        if self.insert_ignore(
            "raw_artifacts",
            {
                "artifact_id": artifact_id,
                "source": source,
                "source_endpoint": endpoint,
                "source_url": source_url,
                "competition_code": competition_code,
                "season_code": season_code,
                "game_code": game_code,
                "matchday_number": matchday_number,
                "fetched_at": fetched_at,
                "stored_at": artifact.stored_at,
                "raw_path": raw_path,
                "content_sha256": artifact.sha256,
                "byte_count": artifact.byte_count,
                "media_type": media_type,
                "is_sanitized": is_sanitized,
                "auth_data_included": False,
                "ingestion_run_id": self.run_id,
            },
        ):
            self.artifact_count += 1
        return artifact_id

    def ensure_season(self, season_code: str) -> str:
        competition_code = season_code[0]
        start_year = int(season_code[1:])
        competition_id = stable_id("competition", competition_code)
        self.insert_ignore(
            "competitions",
            {
                "competition_id": competition_id,
                "competition_code": competition_code,
                "competition_name": (
                    "EuroLeague" if competition_code == "E" else "EuroCup"
                ),
            },
        )
        season_id = stable_id("season", competition_code, season_code)
        self.insert_ignore(
            "seasons",
            {
                "season_id": season_id,
                "competition_id": competition_id,
                "season_code": season_code,
                "season_label": f"{start_year}-{str(start_year + 1)[-2:]}",
                "start_year": start_year,
                "start_date": None,
                "end_date": None,
            },
        )
        return season_id

    def ensure_team(
        self,
        official_code: Any,
        name: Any,
        *,
        season_code: str | None,
        source: str = "euroleague",
    ) -> str:
        code = str(official_code or "").strip()
        if not code:
            raise ValueError("An official team code is required")
        canonical_team_id = self._team_codes.get(code)
        canonical_name = str(name or code).strip()
        if canonical_team_id is None:
            canonical_team_id = stable_id("team", "euroleague", code)
            self.insert_ignore(
                "teams",
                {
                    "canonical_team_id": canonical_team_id,
                    "official_team_code": code,
                    "canonical_name": canonical_name,
                    "country_code": None,
                    "created_from_source": source,
                },
            )
            self._team_codes[code] = canonical_team_id
        self.ensure_team_alias(
            canonical_team_id,
            source="euroleague",
            alias_type="CODE",
            alias_value=code,
            season_code=season_code,
            match_method="OFFICIAL_IDENTIFIER",
            manually_reviewed=False,
        )
        if canonical_name:
            self.ensure_team_alias(
                canonical_team_id,
                source="euroleague",
                alias_type="NAME",
                alias_value=canonical_name,
                season_code=season_code,
                match_method="OFFICIAL_SOURCE",
                manually_reviewed=False,
            )
        return canonical_team_id

    def ensure_team_alias(
        self,
        canonical_team_id: str,
        *,
        source: str,
        alias_type: str,
        alias_value: Any,
        season_code: str | None,
        match_method: str,
        manually_reviewed: bool,
        notes: str | None = None,
    ) -> None:
        value = str(alias_value or "").strip()
        if not value:
            return
        alias_id = stable_id(
            "team_alias", source, alias_type, normalized_text(value), season_code
        )
        if alias_id in self._team_alias_ids:
            return
        self.insert_ignore(
            "team_aliases",
            {
                "team_alias_id": alias_id,
                "canonical_team_id": canonical_team_id,
                "source": source,
                "alias_type": alias_type,
                "alias_value": value,
                "normalized_alias": normalized_text(value),
                "season_code": season_code,
                "valid_from": None,
                "valid_to": None,
                "match_method": match_method,
                "manually_reviewed": manually_reviewed,
                "notes": notes,
            },
        )
        self._team_alias_ids.add(alias_id)

    def ensure_player(
        self,
        official_code: Any,
        name: Any,
        *,
        source: str,
        birth_date: Any = None,
        nationality_code: Any = None,
        nationality: Any = None,
        height_cm: Any = None,
        weight_kg: Any = None,
    ) -> str:
        code = canonical_official_player_code(official_code)
        if not code:
            raise ValueError("An official player code is required")
        canonical_player_id = self._player_codes.get(code)
        if canonical_player_id is None:
            canonical_player_id = stable_id("player", "euroleague", code)
            self.insert_ignore(
                "players",
                {
                    "canonical_player_id": canonical_player_id,
                    "official_player_code": code,
                    "canonical_name": str(name or code).strip(),
                    "birth_date": _date_or_none(birth_date),
                    "nationality_code": _text_or_none(nationality_code),
                    "nationality": _text_or_none(nationality),
                    "height_cm": _int_or_none(height_cm),
                    "weight_kg": _int_or_none(weight_kg),
                    "created_from_source": source,
                },
            )
            self._player_codes[code] = canonical_player_id
        if name:
            self.ensure_player_alias(
                canonical_player_id,
                str(name),
                source=source,
                season_code=None,
                match_method="PROVIDER_NAME",
            )
        return canonical_player_id

    def ensure_player_alias(
        self,
        canonical_player_id: str,
        alias_value: str,
        *,
        source: str,
        season_code: str | None,
        match_method: str,
    ) -> None:
        value = alias_value.strip()
        if not value:
            return
        alias_id = stable_id(
            "player_alias",
            canonical_player_id,
            source,
            normalized_text(value),
            season_code,
        )
        if alias_id in self._player_alias_ids:
            return
        self.insert_ignore(
            "player_aliases",
            {
                "player_alias_id": alias_id,
                "canonical_player_id": canonical_player_id,
                "source": source,
                "alias_value": value,
                "normalized_alias": normalized_text(value),
                "season_code": season_code,
                "valid_from": None,
                "valid_to": None,
                "match_method": match_method,
                "manually_reviewed": False,
            },
        )
        self._player_alias_ids.add(alias_id)


def ingest_retained_samples(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> IngestionSummary:
    """Load the bounded E2025 sample and E2023 four-OT audit fixture."""

    initialize_database(database_path)
    with connect_database(database_path) as connection:
        ingestor = CanonicalIngestor(
            connection,
            source="retained_phase1_samples",
            command="python -m scripts.ingest_samples",
            competition_code="E",
        )
        try:
            _ingest_e2025_sample(ingestor, project_root, raw_root)
            _ingest_overtime_sample(ingestor, project_root, raw_root)
            _seed_known_rotation_anomaly(ingestor)
            return ingestor.finish()
        except Exception as error:
            ingestor.fail(error)
            raise


def _ingest_e2025_sample(
    ingestor: CanonicalIngestor,
    project_root: Path,
    raw_root: Path,
) -> None:
    sample = project_root / "data" / "samples" / "e2025_rounds_1_2"
    manifest = _read_json(sample / "manifest.json")
    metadata = {
        Path(item["relative_path"]).name: item for item in manifest["responses"]
    }
    raw_directory = sample / "raw"
    artifacts: dict[str, str] = {}
    for source_path in sorted(raw_directory.glob("*.json")):
        endpoint, game_code, relative_target = _euroleague_raw_target(
            source_path.name, "E2025"
        )
        stored = archive_existing_file(
            source_path, relative_target, raw_root=raw_root
        )
        item = metadata.get(source_path.name, {})
        artifacts[source_path.name] = ingestor.register_artifact(
            stored,
            source="official_euroleague_api",
            endpoint=str(item.get("endpoint") or endpoint),
            source_url=_text_or_none(item.get("url")),
            competition_code="E",
            season_code="E2025",
            game_code=game_code,
            fetched_at=_datetime_or_none(item.get("fetched_at_utc")),
            media_type=str(item.get("content_type") or "application/json"),
        )

    season_id = ingestor.ensure_season("E2025")
    roster_payload = _read_json(raw_directory / "season_players.json")
    _ingest_roster(
        ingestor,
        normalize_roster_players(roster_payload),
        season_id,
        artifacts["season_players.json"],
    )

    game_objects: dict[int, Mapping[str, Any]] = {}
    for round_number in (1, 2):
        name = f"round_{round_number}_games.json"
        payload = _read_json(raw_directory / name)
        source_artifact_id = artifacts[name]
        rows = normalize_games(payload.get("data", []))
        objects = {
            int(item["gameCode"]): item
            for item in payload.get("data", [])
            if item.get("gameCode") is not None
        }
        game_objects.update(objects)
        for row in rows:
            _ingest_game(ingestor, row, season_id, source_artifact_id)

    for game_code in manifest["selected_game_codes"]:
        game = game_objects[int(game_code)]
        box_name = f"game_{game_code}_boxscore.json"
        box_payload = _read_json(raw_directory / box_name)
        _ingest_boxscore(
            ingestor,
            game,
            box_payload,
            artifacts[box_name],
        )
        pbp_name = f"game_{game_code}_play_by_play.json"
        _ingest_pbp(
            ingestor,
            "E2025",
            int(game_code),
            _read_json(raw_directory / pbp_name),
            artifacts[pbp_name],
        )
        shots_name = f"game_{game_code}_shots.json"
        _ingest_shots(
            ingestor,
            "E2025",
            int(game_code),
            _read_json(raw_directory / shots_name),
            artifacts[shots_name],
        )


def _ingest_overtime_sample(
    ingestor: CanonicalIngestor,
    project_root: Path,
    raw_root: Path,
) -> None:
    sample = project_root / "data" / "samples" / "rotation_overtime_probe"
    provenance = _read_json(
        project_root / "data" / "samples" / "rotation_reconstruction_summary.json"
    )["provenance"]["overtime_probe"]["files"]
    provenance_by_name = {Path(item["relative_path"]).name: item for item in provenance}
    artifacts: dict[str, str] = {}
    for source_path in sorted(sample.glob("*.json")):
        endpoint, game_code, relative_target = _euroleague_raw_target(
            source_path.name, "E2023"
        )
        stored = archive_existing_file(source_path, relative_target, raw_root=raw_root)
        item = provenance_by_name[source_path.name]
        artifacts[source_path.name] = ingestor.register_artifact(
            stored,
            source="official_euroleague_api",
            endpoint=endpoint,
            source_url=item["source_url"],
            competition_code="E",
            season_code="E2023",
            game_code=game_code,
        )

    header = _read_json(sample / "game_170_header.json")
    season_id = ingestor.ensure_season("E2023")
    home_team_id = ingestor.ensure_team(
        header["CodeTeamA"], header["TeamA"], season_code="E2023"
    )
    away_team_id = ingestor.ensure_team(
        header["CodeTeamB"], header["TeamB"], season_code="E2023"
    )
    game_id = stable_id("game", "E", "E2023", 170)
    ingestor.insert_ignore(
        "games",
        {
            "canonical_game_id": game_id,
            "official_game_id": None,
            "season_id": season_id,
            "competition_code": "E",
            "season_code": "E2023",
            "game_code": 170,
            "game_identifier": "E2023_170",
            "phase_code": _text_or_none(header.get("PhaseReducedName")),
            "round_number": _int_or_none(header.get("Round")),
            "game_date": None,
            "local_game_date": _header_local_datetime(header),
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_score": _int_or_none(header.get("ScoreA")),
            "away_score": _int_or_none(header.get("ScoreB")),
            "overtime_count": 4,
            "game_status": "COMPLETED_FROM_LIVE_HEADER",
            "played": True,
            "venue_code": None,
            "venue_name": _text_or_none(header.get("Stadium")),
            "venue_capacity": _int_or_none(header.get("Capacity")),
            "neutral_venue": None,
            "source_artifact_id": artifacts["game_170_header.json"],
            "ingestion_run_id": ingestor.run_id,
        },
    )
    synthetic_game = {
        "season": {"code": "E2023"},
        "round": 19,
        "id": None,
        "gameCode": 170,
        "utcDate": None,
        "local": {"club": {"code": header["CodeTeamA"]}},
        "road": {"club": {"code": header["CodeTeamB"]}},
    }
    box_payload = _read_json(sample / "game_170_boxscore.json")
    _ingest_boxscore(
        ingestor,
        synthetic_game,
        box_payload,
        artifacts["game_170_boxscore.json"],
    )
    _ingest_pbp(
        ingestor,
        "E2023",
        170,
        _read_json(sample / "game_170_play_by_play.json"),
        artifacts["game_170_play_by_play.json"],
    )


def _ingest_roster(
    ingestor: CanonicalIngestor,
    rows: Sequence[Mapping[str, Any]],
    season_id: str,
    artifact_id: str,
) -> None:
    for row in rows:
        season_code = str(row["season_code"])
        team_id = ingestor.ensure_team(
            row["team_id"], row["team_name"], season_code=season_code
        )
        player_id = ingestor.ensure_player(
            row["player_id"],
            row["player_name"],
            source="euroleague_roster",
            birth_date=row.get("birth_date"),
            nationality_code=row.get("nationality_code"),
            nationality=row.get("nationality"),
            height_cm=row.get("height_cm"),
            weight_kg=row.get("weight_kg"),
        )
        for alias in (
            row.get("alias"),
            _passport_display_name(row),
        ):
            if alias:
                ingestor.ensure_player_alias(
                    player_id,
                    str(alias),
                    source="euroleague_roster",
                    season_code=season_code,
                    match_method="OFFICIAL_ROSTER_ALIAS",
                )
        valid_from = _datetime_or_none(row.get("membership_start"))
        membership_id = stable_id(
            "membership",
            player_id,
            team_id,
            season_id,
            valid_from,
            "euroleague_roster",
        )
        ingestor.insert_ignore(
            "player_team_memberships",
            {
                "membership_id": membership_id,
                "canonical_player_id": player_id,
                "canonical_team_id": team_id,
                "season_id": season_id,
                "valid_from": valid_from,
                "valid_to": _datetime_or_none(row.get("membership_end")),
                "jersey_number": _text_or_none(row.get("jersey_number")),
                "position_code": _text_or_none(row.get("position_code")),
                "position_name": _text_or_none(row.get("position")),
                "active": _bool_or_none(row.get("active")),
                "source": "official_euroleague_roster",
                "source_artifact_id": artifact_id,
                "ingestion_run_id": ingestor.run_id,
            },
        )


def _ingest_game(
    ingestor: CanonicalIngestor,
    row: Mapping[str, Any],
    season_id: str,
    artifact_id: str,
) -> str:
    season_code = str(row["season_code"])
    home_team_id = ingestor.ensure_team(
        row["home_team_id"], row["home_team_name"], season_code=season_code
    )
    away_team_id = ingestor.ensure_team(
        row["away_team_id"], row["away_team_name"], season_code=season_code
    )
    game_id = stable_id(
        "game", row.get("competition_code") or "E", season_code, row["game_code"]
    )
    values = {
        "canonical_game_id": game_id,
        "official_game_id": _text_or_none(row.get("game_id")),
        "season_id": season_id,
        "competition_code": row.get("competition_code") or "E",
        "season_code": season_code,
        "game_code": _int_or_none(row.get("game_code")),
        "game_identifier": _text_or_none(row.get("game_identifier")),
        "phase_code": _text_or_none(row.get("phase_code")),
        "round_number": _int_or_none(row.get("round")),
        "game_date": _datetime_or_none(row.get("utc_date")),
        "local_game_date": _datetime_or_none(row.get("local_date")),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": _int_or_none(row.get("home_score")),
        "away_score": _int_or_none(row.get("away_score")),
        "overtime_count": _int_or_none(row.get("overtime_periods")),
        "game_status": _text_or_none(row.get("game_status")),
        "played": _bool_or_none(row.get("played")),
        "venue_code": _text_or_none(row.get("venue_code")),
        "venue_name": _text_or_none(row.get("venue_name")),
        "venue_capacity": _int_or_none(row.get("venue_capacity")),
        "neutral_venue": _bool_or_none(row.get("neutral_venue")),
        "source_artifact_id": artifact_id,
        "ingestion_run_id": ingestor.run_id,
    }
    inserted = ingestor.insert_ignore(
        "games",
        values,
    )
    if not inserted and values["game_date"] is not None:
        existing = ingestor.connection.execute(
            "SELECT game_date IS NULL FROM games WHERE canonical_game_id = ?", [game_id]
        ).fetchone()
        if existing is not None and bool(existing[0]):
            ingestor.insert_ignore(
                "data_anomalies",
                {
                    "anomaly_id": stable_id(
                        "anomaly", "GAME", game_id, "BETTER_METADATA_REBUILD"
                    ),
                    "entity_type": "GAME",
                    "entity_id": game_id,
                    "anomaly_code": "BETTER_SOURCE_METADATA_REQUIRES_REBUILD",
                    "severity": "WARNING",
                    "details_json": json.dumps(
                        {
                            "existing_utc_tip_time": None,
                            "new_source_has_utc_tip_time": True,
                            "new_source_artifact_id": artifact_id,
                        }
                    ),
                    "detected_at": datetime.now(UTC),
                    "quarantined": False,
                    "resolved_at": None,
                    "resolution_notes": (
                        "Canonical facts are immutable after child rows exist; "
                        "rebuild deterministically from archived raw artifacts."
                    ),
                    "source_artifact_id": artifact_id,
                    "ingestion_run_id": ingestor.run_id,
                },
            )
    return game_id


def _ingest_boxscore(
    ingestor: CanonicalIngestor,
    game: Mapping[str, Any],
    payload: Mapping[str, Any],
    artifact_id: str,
) -> None:
    season_code = str(_mapping(game.get("season")).get("code"))
    game_code = int(game["gameCode"])
    game_id = stable_id("game", season_code[0], season_code, game_code)
    team_names: dict[str, str] = {}
    for side in payload.get("Stats", []):
        if not isinstance(side, Mapping):
            continue
        players = side.get("PlayersStats", [])
        if players and isinstance(players[0], Mapping):
            team_names[str(players[0].get("Team", "")).strip()] = str(
                side.get("Team") or ""
            ).strip()
    player_rows = normalize_player_boxscore(payload, game)
    for row in player_rows:
        team_id = ingestor.ensure_team(
            row["team_id"], team_names.get(str(row["team_id"])), season_code=season_code
        )
        opponent_id = ingestor.ensure_team(
            row["opponent_id"],
            team_names.get(str(row["opponent_id"])),
            season_code=season_code,
        )
        player_id = ingestor.ensure_player(
            row["player_id"], row["player_name"], source="euroleague_live_boxscore"
        )
        values: dict[str, Any] = {
            "player_game_id": stable_id("player_game", game_id, player_id, team_id),
            "canonical_game_id": game_id,
            "canonical_player_id": player_id,
            "canonical_team_id": team_id,
            "opponent_team_id": opponent_id,
            "raw_player_code": _text_or_none(row.get("player_id")),
            "home_away": row["home_away"],
            "jersey_number": _text_or_none(row.get("jersey_number")),
            "starter": _bool_or_none(row.get("is_starter")),
            "source_is_playing": _bool_or_none(row.get("is_playing_flag")),
            "minutes_raw": _text_or_none(row.get("minutes_raw")),
            "minutes": _float_or_none(row.get("minutes")),
            "did_not_play": bool(row.get("did_not_play")),
            "source_artifact_id": artifact_id,
            "ingestion_run_id": ingestor.run_id,
        }
        values.update(
            {
                target: _int_or_none(row.get(source))
                for source, target in STAT_COLUMNS.items()
            }
        )
        ingestor.insert_ignore("player_game_stats", values)

    for row in normalize_team_boxscore(payload, game):
        team_id = ingestor.ensure_team(
            row["team_id"], row["team_name"], season_code=season_code
        )
        opponent_id = ingestor.ensure_team(
            row["opponent_id"], team_names.get(str(row["opponent_id"])), season_code=season_code
        )
        values = {
            "team_game_id": stable_id("team_game", game_id, team_id),
            "canonical_game_id": game_id,
            "canonical_team_id": team_id,
            "opponent_team_id": opponent_id,
            "home_away": row["home_away"],
            "team_name_raw": _text_or_none(row.get("team_name")),
            "coach_name": _text_or_none(row.get("coach")),
            "minutes_raw": _text_or_none(row.get("minutes_raw")),
            "team_rebounds_offensive": _int_or_none(row.get("team_rebounds_offensive")),
            "team_rebounds_defensive": _int_or_none(row.get("team_rebounds_defensive")),
            "team_rebounds_total": _int_or_none(row.get("team_rebounds_total")),
            "source_artifact_id": artifact_id,
            "ingestion_run_id": ingestor.run_id,
        }
        values.update(
            {
                target: _int_or_none(row.get(source))
                for source, target in STAT_COLUMNS.items()
            }
        )
        ingestor.insert_ignore("team_game_stats", values)


def _ingest_pbp(
    ingestor: CanonicalIngestor,
    season_code: str,
    game_code: int,
    payload: Mapping[str, Any],
    artifact_id: str,
    *,
    known_absent: bool = False,
) -> None:
    game_id = stable_id("game", season_code[0], season_code, game_code)
    game_teams = _game_team_code_map(ingestor.connection, game_id)
    records: list[dict[str, Any]] = []
    for row in normalize_play_by_play(
        payload, season_code=season_code, game_code=game_code
    ):
        raw_team_code = _text_or_none(row.get("team_id"))
        raw_player_code = _text_or_none(row.get("player_id"))
        canonical_team_id = game_teams.get(raw_team_code) if raw_team_code else None
        canonical_player_id = None
        if raw_player_code and raw_player_code not in PSEUDO_PLAYER_CODES:
            canonical_player_id = ingestor.ensure_player(
                raw_player_code,
                row.get("player_name") or raw_player_code,
                source="euroleague_live_play_by_play",
            )
        records.append(
            {
                "pbp_event_id": stable_id(
                    "pbp_event", game_id, row["source_sequence"]
                ),
                "canonical_game_id": game_id,
                "period": _int_or_none(row.get("quarter")),
                "game_clock": _text_or_none(row.get("game_clock")),
                "elapsed_minute": _int_or_none(row.get("elapsed_minute")),
                "source_sequence": _int_or_none(row.get("source_sequence")),
                "source_period": row["source_period"],
                "raw_event_number": _int_or_none(row.get("event_number")),
                "canonical_team_id": canonical_team_id,
                "canonical_player_id": canonical_player_id,
                "raw_team_code": raw_team_code,
                "raw_player_code": raw_player_code,
                "event_type": _text_or_none(row.get("event_type")),
                "play_type": _text_or_none(row.get("play_type")),
                "player_name_raw": _text_or_none(row.get("player_name")),
                "team_name_raw": _text_or_none(row.get("team_name")),
                "jersey_number": _text_or_none(row.get("jersey_number")),
                "home_score": _int_or_none(row.get("home_score")),
                "away_score": _int_or_none(row.get("away_score")),
                "comment": _text_or_none(row.get("comment")),
                "play_info": _text_or_none(row.get("play_info")),
                "source_artifact_id": artifact_id,
                "ingestion_run_id": ingestor.run_id,
            }
        )
    if known_absent:
        ingestor.insert_many_known_absent("play_by_play_events", records)
    else:
        ingestor.insert_many_ignore("play_by_play_events", records)


def _ingest_shots(
    ingestor: CanonicalIngestor,
    season_code: str,
    game_code: int,
    payload: Mapping[str, Any],
    artifact_id: str,
    *,
    known_absent: bool = False,
) -> None:
    game_id = stable_id("game", season_code[0], season_code, game_code)
    game_teams = _game_team_code_map(ingestor.connection, game_id)
    records: list[dict[str, Any]] = []
    for sequence, row in enumerate(
        normalize_shots(payload, season_code=season_code, game_code=game_code),
        start=1,
    ):
        raw_team_code = _text_or_none(row.get("team_id"))
        raw_player_code = _text_or_none(row.get("player_id"))
        canonical_player_id = None
        if raw_player_code:
            canonical_player_id = ingestor.ensure_player(
                raw_player_code,
                row.get("player_name") or raw_player_code,
                source="euroleague_live_shots",
            )
        action_id = str(row.get("action_id") or "").upper()
        made = None
        if action_id.endswith("M"):
            made = True
        elif action_id.endswith("A"):
            made = False
        points_value = 3 if action_id.startswith("3") else 2 if action_id.startswith("2") else 1 if action_id.startswith("FT") else None
        records.append(
            {
                "shot_id": stable_id("shot", game_id, sequence),
                "canonical_game_id": game_id,
                "source_sequence": sequence,
                "raw_event_number": _int_or_none(row.get("event_number")),
                "canonical_team_id": game_teams.get(raw_team_code),
                "canonical_player_id": canonical_player_id,
                "raw_team_code": raw_team_code,
                "raw_player_code": raw_player_code,
                "player_name_raw": _text_or_none(row.get("player_name")),
                "action_id": _text_or_none(row.get("action_id")),
                "action": _text_or_none(row.get("action")),
                "points": _int_or_none(row.get("points")),
                "made": made,
                "points_value": points_value,
                "coordinate_x": _float_or_none(row.get("coordinate_x")),
                "coordinate_y": _float_or_none(row.get("coordinate_y")),
                "zone": _text_or_none(row.get("zone")),
                "fast_break": _bool_or_none(row.get("fast_break")),
                "second_chance": _bool_or_none(row.get("second_chance")),
                "points_off_turnover": _bool_or_none(row.get("points_off_turnover")),
                "period": _period_from_elapsed_minute(row.get("elapsed_minute")),
                "elapsed_minute": _int_or_none(row.get("elapsed_minute")),
                "game_clock": _text_or_none(row.get("game_clock")),
                "home_score": _int_or_none(row.get("home_score")),
                "away_score": _int_or_none(row.get("away_score")),
                "utc_timestamp_raw": _text_or_none(row.get("utc_timestamp_raw")),
                "source_artifact_id": artifact_id,
                "ingestion_run_id": ingestor.run_id,
            }
        )
    if known_absent:
        ingestor.insert_many_known_absent("shots", records)
    else:
        ingestor.insert_many_ignore("shots", records)


def _seed_known_rotation_anomaly(ingestor: CanonicalIngestor) -> None:
    game_id = stable_id("game", "E", "E2023", 170)
    ingestor.insert_ignore(
        "data_anomalies",
        {
            "anomaly_id": stable_id("anomaly", "GAME", game_id, "ROTATION_MINUTES"),
            "entity_type": "GAME",
            "entity_id": game_id,
            "anomaly_code": "ROTATION_PLAYER_MINUTE_DISCREPANCY",
            "severity": "WARNING",
            "details_json": json.dumps(
                {
                    "fixture": "E2023/170",
                    "verdict": "POSSIBLE WITH CAVEATS",
                    "player_rows_exact": 20,
                    "player_rows_total": 24,
                    "maximum_absolute_error_seconds": 60,
                    "team_totals_reconcile": True,
                }
            ),
            "detected_at": datetime.now(UTC),
            "quarantined": True,
            "resolved_at": None,
            "resolution_notes": None,
            "source_artifact_id": None,
            "ingestion_run_id": ingestor.run_id,
        },
    )
    ingestor.insert_ignore(
        "data_anomalies",
        {
            "anomaly_id": stable_id("anomaly", "GAME", game_id, "MISSING_UTC_TIP"),
            "entity_type": "GAME",
            "entity_id": game_id,
            "anomaly_code": "MISSING_TRUSTWORTHY_UTC_TIP_TIME",
            "severity": "ERROR",
            "details_json": json.dumps(
                {
                    "fixture": "E2023/170",
                    "local_header_time": "2024-01-05 20:45",
                    "timezone_offset_in_source": None,
                    "affected_use": "strict chronological/leakage-safe calculations",
                }
            ),
            "detected_at": datetime.now(UTC),
            "quarantined": True,
            "resolved_at": None,
            "resolution_notes": None,
            "source_artifact_id": None,
            "ingestion_run_id": ingestor.run_id,
        },
    )


def seed_manual_team_aliases(
    ingestor: CanonicalIngestor,
    *,
    reference_path: Path = PROJECT_ROOT / "data" / "reference" / "team_aliases.csv",
) -> None:
    """Load reviewed aliases from one explicit, versionable reference file."""

    with reference_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result = ingestor.connection.execute(
                "SELECT canonical_team_id FROM teams WHERE official_team_code = ?",
                [row["official_team_code"]],
            ).fetchone()
            if result is None:
                raise ValueError(
                    f"Alias target team {row['official_team_code']} is not canonicalized"
                )
            ingestor.ensure_team_alias(
                str(result[0]),
                source=row["source"],
                alias_type=row["alias_type"],
                alias_value=row["alias_value"],
                season_code=row["season_code"] or None,
                match_method=row["match_method"],
                manually_reviewed=row["manually_reviewed"].casefold() == "true",
                notes=row["notes"] or None,
            )


def _euroleague_raw_target(
    filename: str, season_code: str
) -> tuple[str, int | None, Path]:
    if filename.startswith("round_"):
        return "v2_round_games", None, Path("euroleague", season_code, "games", filename)
    if filename == "season_players.json":
        return "v2_season_players", None, Path(
            "euroleague", season_code, "rosters", filename
        )
    if not filename.startswith("game_"):
        raise ValueError(f"Unknown retained raw filename: {filename}")
    parts = filename.removesuffix(".json").split("_")
    game_code = int(parts[1])
    suffix = "_".join(parts[2:])
    directories = {
        "boxscore": ("live_boxscore", "boxscores"),
        "header": ("live_header", "headers"),
        "play_by_play": ("live_play_by_play", "play_by_play"),
        "shots": ("live_shots", "shots"),
        "v3_report": ("v3_game_report", "v3_reports"),
        "v3_stats": ("v3_game_stats", "v3_stats"),
        "v3_teams_comparison": ("v3_game_team_comparison", "v3_team_comparisons"),
    }
    endpoint, directory = directories[suffix]
    return endpoint, game_code, Path(
        "euroleague", season_code, directory, f"{game_code}.json"
    )


def _game_team_code_map(
    connection: DuckDBPyConnection, canonical_game_id: str
) -> dict[str, str]:
    row = connection.execute(
        """
        SELECT home.official_team_code, games.home_team_id,
               away.official_team_code, games.away_team_id
        FROM games
        JOIN teams AS home ON home.canonical_team_id = games.home_team_id
        JOIN teams AS away ON away.canonical_team_id = games.away_team_id
        WHERE games.canonical_game_id = ?
        """,
        [canonical_game_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Game not loaded before event data: {canonical_game_id}")
    return {str(row[0]): str(row[1]), str(row[2]): str(row[3])}


def _period_from_elapsed_minute(value: Any) -> int | None:
    minute = _int_or_none(value)
    if minute is None or minute <= 0:
        return None
    if minute <= 40:
        return (minute - 1) // 10 + 1
    return (minute - 41) // 5 + 5


def _passport_display_name(row: Mapping[str, Any]) -> str | None:
    given = _text_or_none(row.get("passport_name"))
    surname = _text_or_none(row.get("passport_surname"))
    return f"{given} {surname}" if given and surname else None


def _header_local_datetime(header: Mapping[str, Any]) -> datetime | None:
    date = str(header.get("Date") or "").strip()
    hour = str(header.get("Hour") or "").strip()
    if not date:
        return None
    try:
        return datetime.strptime(f"{date} {hour or '00:00'}", "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _date_or_none(value: Any) -> Any:
    parsed = _datetime_or_none(value)
    return parsed.date() if parsed else None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
