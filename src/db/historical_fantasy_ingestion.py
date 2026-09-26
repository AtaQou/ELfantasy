"""Canonical writer for official historical Fantasy Stats observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from src.data.historical_fantasy_stats import (
    FetchedResponse,
    HISTORICAL_STATS_PARSER_VERSION,
    legacy_stats_records,
)

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .fantasy_ingestion import (
    _resolve_fantasy_team_parts,
    _seed_exact_fantasy_team_aliases,
)
from .ids import stable_id
from .ingestion import (
    CanonicalIngestor,
    IngestionSummary,
    _float_or_none,
    _int_or_none,
    _text_or_none,
    seed_manual_team_aliases,
)
from .raw_store import DEFAULT_RAW_ROOT, archive_bytes


LEGACY_STATS_PROVIDER = "dunkest_euroleague_stats_legacy"
LEGACY_STATS_SOURCE = "OFFICIAL_DUNKEST_HISTORICAL_STATS_API"
POSITION_NAMES = {"G": "Guard", "F": "Forward", "C": "Center"}


@dataclass(frozen=True, slots=True)
class LegacyStatsSnapshotSpec:
    season_code: str
    source_season_id: int
    fantasy_matchday: int
    competition_round: int
    date_from: date
    date_to: date
    credits_valid_pre_matchday: bool
    position_valid_as_of_matchday: bool


class HistoricalFantasyMarketWriter:
    """Register exact raw responses and extend the existing Fantasy market tables."""

    def __init__(
        self,
        season_code: str,
        database_path: Path | str = DEFAULT_DATABASE_PATH,
        *,
        raw_root: Path = DEFAULT_RAW_ROOT,
    ) -> None:
        initialize_database(database_path)
        self.season_code = season_code
        self.raw_root = raw_root
        self.connection = connect_database(database_path)
        self.ingestor = CanonicalIngestor(
            self.connection,
            source="historical_fantasy_stats",
            command="python -m scripts.collect_historical_fantasy_market",
            competition_code="E",
            season_code=season_code,
        )
        self.ingestor.ensure_season(season_code)
        seed_manual_team_aliases(self.ingestor)
        self._closed = False

    def __enter__(self) -> "HistoricalFantasyMarketWriter":
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        if self._closed:
            return
        if isinstance(error, BaseException):
            self.ingestor.fail(error)
        else:
            self.ingestor.finish()
        self.connection.close()
        self._closed = True

    def finish(self) -> IngestionSummary:
        if self._closed:
            raise RuntimeError("Historical writer is already closed")
        result = self.ingestor.finish()
        self.connection.close()
        self._closed = True
        return result

    def register_response(
        self,
        response: FetchedResponse,
        *,
        relative_target: Path,
        endpoint: str,
        matchday_number: int | None,
        parser_schema_version: str = HISTORICAL_STATS_PARSER_VERSION,
        source: str = "official_dunkest_stats",
    ) -> str:
        artifact = archive_bytes(
            response.content, relative_target, raw_root=self.raw_root
        )
        artifact_id = self.ingestor.register_artifact(
            artifact,
            source=source,
            endpoint=endpoint,
            source_url=response.url,
            competition_code="E",
            season_code=self.season_code,
            matchday_number=matchday_number,
            fetched_at=response.fetched_at,
            media_type=(response.media_type or "application/octet-stream").split(";", 1)[0],
            is_sanitized=False,
        )
        parameters = parse_qs(urlparse(response.url).query, keep_blank_values=True)
        self.ingestor.insert_ignore(
            "raw_artifact_request_provenance",
            {
                "artifact_id": artifact_id,
                "request_parameters_json": json.dumps(parameters, sort_keys=True),
                "parser_schema_version": parser_schema_version,
            },
        )
        return artifact_id

    def ingest_legacy_date_response(
        self,
        response: FetchedResponse,
        spec: LegacyStatsSnapshotSpec,
        *,
        window_number: int,
    ) -> tuple[str, int]:
        target = Path(
            "fantasy_stats",
            spec.season_code,
            f"matchday_{spec.fantasy_matchday:02d}",
            (
                f"all_players_window_{window_number:02d}_"
                f"{spec.date_from.isoformat()}_{spec.date_to.isoformat()}.json"
            ),
        )
        artifact_id = self.register_response(
            response,
            relative_target=target,
            endpoint="/api/stats/table",
            matchday_number=spec.fantasy_matchday,
        )
        rows = legacy_stats_records(response.json())
        synthetic = [
            {
                "team": {
                    "id": row.get("team_id"),
                    "name": row.get("team_name"),
                    "abbreviation": row.get("team_code"),
                }
            }
            for row in rows
        ]
        _seed_exact_fantasy_team_aliases(
            self.ingestor, synthetic, spec.season_code
        )
        records: list[dict[str, Any]] = []
        semantics_records: list[dict[str, Any]] = []
        for row in rows:
            fantasy_id = str(row.get("id") or "").strip()
            if not fantasy_id:
                raise ValueError("Historical Stats row is missing its player ID")
            first_name = _text_or_none(row.get("first_name"))
            last_name = _text_or_none(row.get("last_name"))
            display_name = " ".join(
                value for value in (first_name, last_name) if value
            )
            if not display_name:
                raise ValueError("Historical Stats row is missing its player name")
            credits = _float_or_none(row.get("cr"))
            if credits is None or credits < 0:
                raise ValueError(f"Invalid historical Stats credits for {fantasy_id}")
            entity_id = stable_id(
                "fantasy_entity", LEGACY_STATS_PROVIDER, fantasy_id
            )
            self.ingestor.insert_ignore(
                "fantasy_entities",
                {
                    "fantasy_entity_id": entity_id,
                    "fantasy_provider": LEGACY_STATS_PROVIDER,
                    "fantasy_id": fantasy_id,
                    "entity_type": "PLAYER",
                    "display_name": display_name,
                    "first_observed_at": response.fetched_at,
                    "last_observed_at": response.fetched_at,
                },
            )
            self.connection.execute(
                """
                UPDATE fantasy_entities
                SET first_observed_at = least(first_observed_at, ?),
                    last_observed_at = greatest(last_observed_at, ?)
                WHERE fantasy_entity_id = ?
                """,
                [response.fetched_at, response.fetched_at, entity_id],
            )
            team_id = _resolve_fantasy_team_parts(
                self.ingestor,
                row.get("team_id"),
                row.get("team_name"),
                row.get("team_code"),
                spec.season_code,
            )
            raw_position = _text_or_none(row.get("position"))
            position_name = POSITION_NAMES.get(raw_position or "", raw_position)
            snapshot_id = stable_id(
                "historical_fantasy_stats_snapshot",
                LEGACY_STATS_PROVIDER,
                spec.season_code,
                spec.fantasy_matchday,
                fantasy_id,
            )
            batch_id = stable_id(
                "historical_fantasy_stats_batch",
                spec.season_code,
                spec.fantasy_matchday,
                spec.date_from.isoformat(),
                spec.date_to.isoformat(),
                artifact_id,
            )
            records.append(
                {
                    "snapshot_record_id": snapshot_id,
                    "snapshot_batch_id": batch_id,
                    "observed_at": response.fetched_at,
                    "season_code": spec.season_code,
                    "competition_id": None,
                    "players_list_id": None,
                    "matchday_id": spec.fantasy_matchday,
                    "matchday_number": spec.fantasy_matchday,
                    "turn_id": None,
                    "turn_number": None,
                    "fantasy_entity_id": entity_id,
                    "fantasy_team_provider_id": _text_or_none(row.get("team_id")),
                    "canonical_team_id": team_id,
                    "fantasy_opponent_provider_id": None,
                    "opponent_team_id": None,
                    "team_name_raw": _text_or_none(row.get("team_name")),
                    "opponent_name_raw": None,
                    "position_id": _int_or_none(row.get("position_id")),
                    "position_name": position_name,
                    "credits": credits,
                    "overlay_average_points": None,
                    "overlay_popularity": None,
                    "jersey_number": None,
                    "overlay_is_injured": None,
                    "overlay_probability_of_playing": None,
                    "overlay_started_from_bench": None,
                    "overlay_is_on_fire": None,
                    "status_valid_as_of_matchday": False,
                    "status_semantics": "NOT_RETURNED_BY_HISTORICAL_STATS_API",
                    "position_semantics": (
                        "HISTORICAL_SEASON_FANTASY_POSITION"
                        if spec.position_valid_as_of_matchday
                        else "RETURNED_POSITION_HISTORICAL_SEMANTICS_UNPROVEN"
                    ),
                    "price_semantics": (
                        "PRE_MATCHDAY_PRICE_VALIDATED_BY_CREDIT_MOVEMENT"
                        if spec.credits_valid_pre_matchday
                        else "SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME"
                    ),
                    "market_available": None,
                    "source_artifact_id": artifact_id,
                    "ingestion_run_id": self.ingestor.run_id,
                }
            )
            semantics_records.append(
                {
                    "snapshot_record_id": snapshot_id,
                    "source_system": LEGACY_STATS_SOURCE,
                    "source_season_id": str(spec.source_season_id),
                    "matchday_identifier_semantics": "DUNKEST_WEEK_NUMBER",
                    "competition_round": spec.competition_round,
                    "credits_valid_pre_matchday": spec.credits_valid_pre_matchday,
                    "position_valid_as_of_matchday": (
                        spec.position_valid_as_of_matchday
                    ),
                    "market_population_semantics": (
                        "PLAYED_PLAYERS_FROM_DATE_FILTER_NOT_FULL_MARKET"
                    ),
                    "source_request_parameters_json": json.dumps(
                        parse_qs(urlparse(response.url).query, keep_blank_values=True),
                        sort_keys=True,
                    ),
                }
            )
        inserted = self.ingestor.insert_many_ignore(
            "fantasy_market_snapshots", records
        )
        self.connection.executemany(
            """
            INSERT INTO fantasy_market_snapshot_semantics VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (snapshot_record_id) DO UPDATE SET
                source_system = excluded.source_system,
                source_season_id = excluded.source_season_id,
                matchday_identifier_semantics =
                    excluded.matchday_identifier_semantics,
                competition_round = excluded.competition_round,
                credits_valid_pre_matchday =
                    excluded.credits_valid_pre_matchday,
                position_valid_as_of_matchday =
                    excluded.position_valid_as_of_matchday,
                market_population_semantics =
                    excluded.market_population_semantics,
                source_request_parameters_json =
                    excluded.source_request_parameters_json
            """,
            [list(record.values()) for record in semantics_records],
        )
        self.connection.executemany(
            """
            UPDATE fantasy_market_snapshots
            SET price_semantics = ?, position_semantics = ?
            WHERE snapshot_record_id = ?
            """,
            [
                [
                    (
                        "PRE_MATCHDAY_PRICE_VALIDATED_BY_CREDIT_MOVEMENT"
                        if spec.credits_valid_pre_matchday
                        else "SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME"
                    ),
                    (
                        "HISTORICAL_SEASON_FANTASY_POSITION"
                        if spec.position_valid_as_of_matchday
                        else "RETURNED_POSITION_HISTORICAL_SEMANTICS_UNPROVEN"
                    ),
                    record["snapshot_record_id"],
                ]
                for record in semantics_records
            ],
        )
        self.ingestor.insert_ignore(
            "fantasy_matchday_round_mapping",
            {
                "mapping_id": stable_id(
                    "fantasy_matchday_round_mapping",
                    spec.season_code,
                    spec.fantasy_matchday,
                    spec.competition_round,
                ),
                "season_code": spec.season_code,
                "fantasy_matchday": spec.fantasy_matchday,
                "competition_round": spec.competition_round,
                "mapping_status": (
                    "PLAYER_DATE_CONTEXT_REQUIRED"
                    if window_number > 1
                    else "VALIDATED_DIRECT"
                ),
                "notes": (
                    "One-to-one official Stats week and EuroLeague round; "
                    "the player game date is retained for rescheduled-game windows."
                ),
                "source_artifact_id": artifact_id,
                "ingestion_run_id": self.ingestor.run_id,
            },
        )
        if window_number > 1:
            self.connection.execute(
                """
                UPDATE fantasy_matchday_round_mapping
                SET mapping_status = 'PLAYER_DATE_CONTEXT_REQUIRED',
                    notes = ?
                WHERE season_code = ? AND fantasy_matchday = ?
                  AND competition_round = ?
                """,
                [
                    (
                        "One-to-one official Stats week and EuroLeague round, "
                        "with multiple actual-date windows caused by a rescheduled game."
                    ),
                    spec.season_code,
                    spec.fantasy_matchday,
                    spec.competition_round,
                ],
            )
        return artifact_id, inserted


def mark_e2025_stats_validation(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    season_code: str,
    exact_matches: int,
    mismatches: int,
    validated_matchdays: Sequence[int],
    positions_validated: bool,
) -> None:
    """Promote existing market fields only after an all-intersection exact check."""

    if exact_matches <= 0 or mismatches != 0:
        raise ValueError("E2025 credit validation did not pass exactly")
    initialize_database(database_path)
    with connect_database(database_path) as connection:
        placeholders = ",".join("?" for _ in validated_matchdays)
        if not placeholders:
            raise ValueError("No validated matchdays were supplied")
        connection.execute(
            f"""
            UPDATE fantasy_market_snapshots
            SET price_semantics = 'PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS',
                position_semantics = CASE WHEN ?
                    THEN 'HISTORICAL_SEASON_FANTASY_POSITION_VALIDATED'
                    ELSE position_semantics END
            WHERE season_code = ? AND matchday_number IN ({placeholders})
            """,
            [
                positions_validated,
                season_code,
                *validated_matchdays,
            ],
        )
        connection.execute(
            f"""
            INSERT INTO fantasy_market_snapshot_semantics
            SELECT snapshot_record_id,
                   'FANTAKING_MATCHDAY_MARKET_API',
                   cast(competition_id AS VARCHAR),
                   'FANTAKING_MATCHDAY_ID',
                   matchday_number,
                   true,
                   ?,
                   'FULL_MARKET_RESPONSE',
                   json_object(
                       'players_list_id', players_list_id,
                       'matchday_id', matchday_id,
                       'page', 1,
                       'per_page', -1
                   )
            FROM fantasy_market_snapshots
            WHERE season_code = ? AND matchday_number IN ({placeholders})
            ON CONFLICT (snapshot_record_id) DO UPDATE SET
                source_system = excluded.source_system,
                source_season_id = excluded.source_season_id,
                matchday_identifier_semantics = excluded.matchday_identifier_semantics,
                competition_round = excluded.competition_round,
                credits_valid_pre_matchday = excluded.credits_valid_pre_matchday,
                position_valid_as_of_matchday = excluded.position_valid_as_of_matchday,
                market_population_semantics = excluded.market_population_semantics,
                source_request_parameters_json = excluded.source_request_parameters_json
            """,
            [positions_validated, season_code, *validated_matchdays],
        )
        for matchday in validated_matchdays:
            source = connection.execute(
                """
                SELECT artifact_id, ingestion_run_id
                FROM raw_artifacts
                WHERE source = 'official_fantaking_stats'
                  AND season_code = ? AND matchday_number = ?
                ORDER BY fetched_at DESC NULLS LAST, stored_at DESC
                LIMIT 1
                """,
                [season_code, matchday],
            ).fetchone()
            if source is None or source[1] is None:
                raise ValueError(
                    f"No E2025 Stats provenance for matchday {matchday}"
                )
            connection.execute(
                """
                INSERT INTO fantasy_matchday_round_mapping VALUES (
                    ?, ?, ?, ?, 'VALIDATED_DIRECT', ?, ?, ?
                )
                ON CONFLICT (season_code, fantasy_matchday, competition_round)
                DO UPDATE SET
                    mapping_status = excluded.mapping_status,
                    notes = excluded.notes,
                    source_artifact_id = excluded.source_artifact_id,
                    ingestion_run_id = excluded.ingestion_run_id
                """,
                [
                    stable_id(
                        "fantasy_matchday_round_mapping",
                        season_code,
                        matchday,
                        matchday,
                    ),
                    season_code,
                    matchday,
                    matchday,
                    (
                        "E2025 regular-season Fantasy matchday number maps "
                        "directly to the canonical EuroLeague competition round; "
                        "opaque source matchday IDs remain in raw provenance."
                    ),
                    str(source[0]),
                    str(source[1]),
                ],
            )
