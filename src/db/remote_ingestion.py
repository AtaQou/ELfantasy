"""Bounded official-API season ingestion built on the Phase 1 client."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from src.data.euroleague_client import APIResponse, EuroLeagueClient
from src.data.normalizers import (
    extract_xml_rows,
    normalize_games,
    normalize_play_by_play,
    normalize_roster_players,
    normalize_shots,
)

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .ids import stable_id
from .ingestion import (
    CanonicalIngestor,
    IngestionSummary,
    _bool_or_none,
    _datetime_or_none,
    _ingest_boxscore,
    _ingest_game,
    _ingest_pbp,
    _ingest_roster,
    _ingest_shots,
    _int_or_none,
    _text_or_none,
)
from .raw_store import DEFAULT_RAW_ROOT, archive_bytes, describe_existing_file


def ingest_official_season(
    season: int,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    max_detailed_games: int | None = 2,
    raw_root: Path = DEFAULT_RAW_ROOT,
    timeout_seconds: float = 45.0,
) -> IngestionSummary:
    """Ingest selected rounds plus bounded detailed games for ``E{season}``.

    ``max_detailed_games=None`` explicitly opts into every played game and is
    the mechanism for eventual E2007+ ingestion.  The CLI never selects that
    mode unless ``--all-games`` is provided.
    """

    if season < 2007:
        raise ValueError("Detailed official ingestion is supported from E2007 onward")
    if max_detailed_games is not None and max_detailed_games <= 0:
        raise ValueError("max_detailed_games must be positive or None")
    initialize_database(database_path)
    season_code = f"E{season}"
    with connect_database(database_path) as connection:
        ingestor = CanonicalIngestor(
            connection,
            source="official_season_ingestion",
            command=f"python -m scripts.ingest_season --season {season_code}",
            competition_code="E",
            season_code=season_code,
        )
        try:
            with EuroLeagueClient(timeout_seconds=timeout_seconds) as client:
                results_response = client.results(season)
                results_artifact = _archive_response(
                    ingestor,
                    results_response,
                    raw_root / "euroleague" / season_code / "results" / "season.xml",
                    raw_root,
                    season_code,
                )
                results = extract_xml_rows(results_response.text, "game")
                played = [
                    row
                    for row in results
                    if _bool_or_none(row.get("played")) is True
                    and _game_code_from_result(row) is not None
                ]
                played.sort(key=lambda row: int(_game_code_from_result(row) or 0))
                selected = (
                    played
                    if max_detailed_games is None
                    else _representative_rows(played, max_detailed_games)
                )
                if not selected:
                    raise ValueError(f"No played games were available for {season_code}")

                roster_response = client.season_players(season)
                roster_artifact = _archive_response(
                    ingestor,
                    roster_response,
                    raw_root / "euroleague" / season_code / "rosters" / "season_players.json",
                    raw_root,
                    season_code,
                )
                season_id = ingestor.ensure_season(season_code)
                _ingest_roster(
                    ingestor,
                    normalize_roster_players(roster_response.payload),
                    season_id,
                    roster_artifact,
                )

                selected_games: dict[int, Mapping[str, Any]] = {}
                selected_rounds = sorted(
                    {
                        int(round_number)
                        for row in selected
                        if (round_number := _int_or_none(row.get("gameday"))) is not None
                    }
                )
                for round_number in selected_rounds:
                    response = client.round_games(season, round_number)
                    artifact_id = _archive_response(
                        ingestor,
                        response,
                        raw_root
                        / "euroleague"
                        / season_code
                        / "games"
                        / f"round_{round_number}.json",
                        raw_root,
                        season_code,
                    )
                    payload_rows = response.payload.get("data", [])
                    for game, normalized in zip(
                        payload_rows, normalize_games(payload_rows), strict=True
                    ):
                        _ingest_game(ingestor, normalized, season_id, artifact_id)
                        if int(game["gameCode"]) in {
                            _game_code_from_result(row) for row in selected
                        }:
                            selected_games[int(game["gameCode"])] = game

                # Older v2 seasons can be incomplete. Preserve a conservative
                # v1 fallback rather than silently dropping the selected game.
                for result in selected:
                    game_code = int(_game_code_from_result(result) or 0)
                    if game_code in selected_games:
                        continue
                    fallback = _v1_result_as_game(result, season_code)
                    _ingest_game(ingestor, fallback["normalized"], season_id, results_artifact)
                    selected_games[game_code] = fallback["raw"]

                for game_code, game in sorted(selected_games.items()):
                    box = client.game_boxscore(season, game_code)
                    if not isinstance(box.payload, Mapping) or not box.payload.get("Stats"):
                        raise ValueError(f"Empty detailed box score for {season_code}/{game_code}")
                    box_artifact = _archive_response(
                        ingestor,
                        box,
                        raw_root / "euroleague" / season_code / "boxscores" / f"{game_code}.json",
                        raw_root,
                        season_code,
                        game_code,
                    )
                    _ingest_boxscore(ingestor, game, box.payload, box_artifact)

                    pbp = client.game_play_by_play(season, game_code)
                    if not isinstance(pbp.payload, Mapping):
                        raise ValueError(f"Invalid play-by-play for {season_code}/{game_code}")
                    pbp_artifact = _archive_response(
                        ingestor,
                        pbp,
                        raw_root / "euroleague" / season_code / "play_by_play" / f"{game_code}.json",
                        raw_root,
                        season_code,
                        game_code,
                    )
                    _ingest_pbp(
                        ingestor, season_code, game_code, pbp.payload, pbp_artifact
                    )

                    shots = client.game_shots(season, game_code)
                    if not isinstance(shots.payload, Mapping):
                        raise ValueError(f"Invalid shot data for {season_code}/{game_code}")
                    shots_artifact = _archive_response(
                        ingestor,
                        shots,
                        raw_root / "euroleague" / season_code / "shots" / f"{game_code}.json",
                        raw_root,
                        season_code,
                        game_code,
                    )
                    _ingest_shots(
                        ingestor, season_code, game_code, shots.payload, shots_artifact
                    )
            return ingestor.finish()
        except BaseException as error:
            ingestor.fail(error)
            raise


def ingest_archived_official_season(
    season: int,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    raw_root: Path = DEFAULT_RAW_ROOT,
    components: frozenset[str] = frozenset({"core", "play_by_play", "shots"}),
) -> IngestionSummary:
    """Normalize selected components solely from immutable archived files."""

    if season < 2007:
        raise ValueError("Detailed official ingestion is supported from E2007 onward")
    unknown = components - {"core", "play_by_play", "shots"}
    if unknown or not components:
        raise ValueError(f"Unsupported archived components: {sorted(unknown)}")
    season_code = f"E{season}"
    season_root = raw_root / "euroleague" / season_code
    results_path = season_root / "results" / "season.xml"
    roster_path = season_root / "rosters" / "season_players.json"
    if "core" in components and (
        not results_path.is_file() or not roster_path.is_file()
    ):
        raise FileNotFoundError(
            f"Archived {season_code} results/roster are incomplete under {season_root}"
        )
    initialize_database(database_path)
    with connect_database(database_path) as connection:
        ingestor = CanonicalIngestor(
            connection,
            source="archived_official_season",
            command=(
                f"python -m scripts.ingest_archived_season --season {season_code} "
                f"--components {','.join(sorted(components))}"
            ),
            competition_code="E",
            season_code=season_code,
        )
        try:
            if "core" in components:
                _normalize_archived_core(
                    ingestor, season_code, season_root, results_path, roster_path
                )
            if "play_by_play" in components:
                for pbp_path in sorted(
                    (season_root / "play_by_play").glob("[0-9]*.json")
                ):
                    game_code = int(pbp_path.stem.split(".", maxsplit=1)[0])
                    payload = json.loads(pbp_path.read_text(encoding="utf-8"))
                    pbp_artifact = _register_archived_detail(
                        ingestor,
                        pbp_path,
                        "live_play_by_play",
                        season_code,
                        game_code,
                    )
                    game_id = stable_id("game", "E", season_code, game_code)
                    normalized_pbp = normalize_play_by_play(
                        payload, season_code=season_code, game_code=game_code
                    )
                    _record_invalid_pbp_clocks(
                        ingestor,
                        season_code,
                        game_code,
                        normalized_pbp,
                        pbp_artifact,
                    )
                    expected_count = len(normalized_pbp)
                    existing_count = int(
                        ingestor.connection.execute(
                            "SELECT count(*) FROM play_by_play_events "
                            "WHERE canonical_game_id = ?",
                            [game_id],
                        ).fetchone()[0]
                    )
                    if existing_count == expected_count:
                        continue
                    _ingest_pbp(
                        ingestor,
                        season_code,
                        game_code,
                        payload,
                        pbp_artifact,
                        known_absent=existing_count == 0,
                    )
            if "shots" in components:
                for shots_path in sorted(
                    (season_root / "shots").glob("[0-9]*.json")
                ):
                    game_code = int(shots_path.stem.split(".", maxsplit=1)[0])
                    payload = json.loads(shots_path.read_text(encoding="utf-8"))
                    shots_artifact = _register_archived_detail(
                        ingestor, shots_path, "live_shots", season_code, game_code
                    )
                    game_id = stable_id("game", "E", season_code, game_code)
                    expected_count = len(
                        normalize_shots(
                            payload, season_code=season_code, game_code=game_code
                        )
                    )
                    existing_count = int(
                        ingestor.connection.execute(
                            "SELECT count(*) FROM shots WHERE canonical_game_id = ?",
                            [game_id],
                        ).fetchone()[0]
                    )
                    if existing_count == expected_count:
                        continue
                    _ingest_shots(
                        ingestor,
                        season_code,
                        game_code,
                        payload,
                        shots_artifact,
                        known_absent=existing_count == 0,
                    )
            return ingestor.finish()
        except BaseException as error:
            ingestor.fail(error)
            raise


def _normalize_archived_core(
    ingestor: CanonicalIngestor,
    season_code: str,
    season_root: Path,
    results_path: Path,
    roster_path: Path,
) -> None:
    schedule_path = season_root / "schedules" / "season.xml"
    if schedule_path.is_file():
        ingestor.register_artifact(
            describe_existing_file(schedule_path),
            source="official_euroleague_api",
            endpoint="v1_schedule",
            source_url=(
                "https://api-live.euroleague.net/v1/schedules?"
                f"seasonCode={season_code}"
            ),
            competition_code="E",
            season_code=season_code,
            media_type="application/xml",
        )
    results_artifact = ingestor.register_artifact(
        describe_existing_file(results_path),
        source="official_euroleague_api",
        endpoint="v1_results",
        source_url=(
            "https://api-live.euroleague.net/v1/results?"
            f"seasonCode={season_code}"
        ),
        competition_code="E",
        season_code=season_code,
        media_type="application/xml",
    )
    results = extract_xml_rows(results_path.read_text(encoding="utf-8"), "game")
    roster_artifact = ingestor.register_artifact(
        describe_existing_file(roster_path),
        source="official_euroleague_api",
        endpoint="v2_season_players",
        source_url=(
            "https://api-live.euroleague.net/v2/competitions/E/players?"
            f"seasonCode={season_code}&Limit=1000&Offset=0"
        ),
        competition_code="E",
        season_code=season_code,
    )
    season_id = ingestor.ensure_season(season_code)
    _ingest_roster(
        ingestor,
        normalize_roster_players(json.loads(roster_path.read_text(encoding="utf-8"))),
        season_id,
        roster_artifact,
    )

    games: dict[int, Mapping[str, Any]] = {}
    for round_path in sorted((season_root / "games").glob("round_*.json")):
        payload = json.loads(round_path.read_text(encoding="utf-8"))
        artifact_id = ingestor.register_artifact(
            describe_existing_file(round_path),
            source="official_euroleague_api",
            endpoint="v2_round_games",
            source_url=None,
            competition_code="E",
            season_code=season_code,
        )
        payload_rows = payload.get("data", [])
        for game, normalized in zip(
            payload_rows, normalize_games(payload_rows), strict=True
        ):
            _ingest_game(ingestor, normalized, season_id, artifact_id)
            games[int(game["gameCode"])] = game

    result_by_code = {
        code: row
        for row in results
        if (code := _game_code_from_result(row)) is not None
    }
    for box_path in sorted((season_root / "boxscores").glob("[0-9]*.json")):
        game_code = int(box_path.stem.split(".", maxsplit=1)[0])
        game = games.get(game_code)
        if game is None:
            result = result_by_code.get(game_code)
            if result is None:
                raise ValueError(f"No game metadata archived for {season_code}/{game_code}")
            fallback = _v1_result_as_game(result, season_code)
            _ingest_game(ingestor, fallback["normalized"], season_id, results_artifact)
            game = fallback["raw"]
        box_artifact = _register_archived_detail(
            ingestor, box_path, "live_boxscore", season_code, game_code
        )
        box_payload = json.loads(box_path.read_text(encoding="utf-8"))
        if not _boxscore_has_players(box_payload):
            ingestor.insert_ignore(
                "data_anomalies",
                {
                    "anomaly_id": stable_id(
                        "anomaly",
                        "GAME",
                        stable_id("game", "E", season_code, game_code),
                        "EMPTY_SUPPORTED_BOXSCORE",
                    ),
                    "entity_type": "GAME",
                    "entity_id": stable_id("game", "E", season_code, game_code),
                    "anomaly_code": "EMPTY_SUPPORTED_BOXSCORE",
                    "severity": "ERROR",
                    "details_json": json.dumps(
                        {
                            "season_code": season_code,
                            "game_code": game_code,
                            "raw_path": str(box_path),
                            "policy": "preserved raw; excluded from player/team statistics",
                        }
                    ),
                    "detected_at": datetime.now(UTC),
                    "quarantined": True,
                    "resolved_at": None,
                    "resolution_notes": None,
                    "source_artifact_id": box_artifact,
                    "ingestion_run_id": ingestor.run_id,
                },
            )
            continue
        game_id = stable_id("game", "E", season_code, game_code)
        expected_players = sum(
            len(side.get("PlayersStats", []))
            for side in box_payload.get("Stats", [])
            if isinstance(side, Mapping)
        )
        existing_players = int(
            ingestor.connection.execute(
                "SELECT count(*) FROM player_game_stats WHERE canonical_game_id = ?",
                [game_id],
            ).fetchone()[0]
        )
        existing_teams = int(
            ingestor.connection.execute(
                "SELECT count(*) FROM team_game_stats WHERE canonical_game_id = ?",
                [game_id],
            ).fetchone()[0]
        )
        if existing_players == expected_players and existing_teams == 2:
            continue
        _ingest_boxscore(
            ingestor,
            game,
            box_payload,
            box_artifact,
        )


def _boxscore_has_players(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    sides = payload.get("Stats", [])
    if not isinstance(sides, list) or len(sides) != 2:
        return False
    return all(
        isinstance(side, Mapping) and bool(side.get("PlayersStats")) for side in sides
    )


def _record_invalid_pbp_clocks(
    ingestor: CanonicalIngestor,
    season_code: str,
    game_code: int,
    rows: Sequence[Mapping[str, Any]],
    artifact_id: str,
) -> None:
    invalid = [
        row
        for row in rows
        if (clock := str(row.get("game_clock") or "").strip())
        and re.fullmatch(r"[0-9]{2}:[0-9]{2}", clock) is None
    ]
    if not invalid:
        return
    game_id = stable_id("game", "E", season_code, game_code)
    ingestor.insert_ignore(
        "data_anomalies",
        {
            "anomaly_id": stable_id(
                "anomaly", "GAME", game_id, "INVALID_PBP_CLOCK"
            ),
            "entity_type": "GAME",
            "entity_id": game_id,
            "anomaly_code": "INVALID_PBP_CLOCK",
            "severity": "WARNING",
            "details_json": json.dumps(
                {
                    "season_code": season_code,
                    "game_code": game_code,
                    "event_count": len(invalid),
                    "values": sorted(
                        {str(row.get("game_clock")) for row in invalid}
                    ),
                    "source_sequences": [
                        int(row["source_sequence"]) for row in invalid
                    ],
                    "policy": (
                        "preserved raw; quarantine for clock/rotation features"
                    ),
                }
            ),
            "detected_at": datetime.now(UTC),
            "quarantined": True,
            "resolved_at": None,
            "resolution_notes": None,
            "source_artifact_id": artifact_id,
            "ingestion_run_id": ingestor.run_id,
        },
    )


def _register_archived_detail(
    ingestor: CanonicalIngestor,
    path: Path,
    endpoint: str,
    season_code: str,
    game_code: int,
) -> str:
    route = {
        "live_boxscore": "Boxscore",
        "live_play_by_play": "PlaybyPlay",
        "live_shots": "Points",
    }[endpoint]
    return ingestor.register_artifact(
        describe_existing_file(path),
        source="official_euroleague_api",
        endpoint=endpoint,
        source_url=(
            f"https://live.euroleague.net/api/{route}?"
            f"gamecode={game_code}&seasoncode={season_code}"
        ),
        competition_code="E",
        season_code=season_code,
        game_code=game_code,
    )


def _archive_response(
    ingestor: CanonicalIngestor,
    response: APIResponse,
    target: Path,
    raw_root: Path,
    season_code: str,
    game_code: int | None = None,
) -> str:
    stored = archive_bytes(
        response.text.encode("utf-8"),
        target.relative_to(raw_root),
        raw_root=raw_root,
    )
    return ingestor.register_artifact(
        stored,
        source="official_euroleague_api",
        endpoint=response.endpoint,
        source_url=response.url,
        competition_code="E",
        season_code=season_code,
        game_code=game_code,
        fetched_at=_datetime_or_none(response.fetched_at_utc),
        media_type=response.content_type,
    )


def _representative_rows(
    rows: Sequence[Mapping[str, Any]], count: int
) -> list[Mapping[str, Any]]:
    if count >= len(rows):
        return list(rows)
    if count == 1:
        return [rows[-1]]
    indexes = sorted({round(index * (len(rows) - 1) / (count - 1)) for index in range(count)})
    return [rows[index] for index in indexes]


def _game_code_from_result(row: Mapping[str, Any]) -> int | None:
    return _int_or_none(row.get("gamenumber") or row.get("gamecode"))


def _v1_result_as_game(
    row: Mapping[str, Any], season_code: str
) -> dict[str, Mapping[str, Any]]:
    game_code = int(_game_code_from_result(row) or 0)
    home_code = str(row.get("homecode") or "").strip()
    away_code = str(row.get("awaycode") or "").strip()
    played = _bool_or_none(row.get("played"))
    normalized = {
        "season_code": season_code,
        "competition_code": season_code[0],
        "game_id": None,
        "game_code": game_code,
        "game_identifier": f"{season_code}_{game_code}",
        "phase_code": _text_or_none(row.get("round") or row.get("group")),
        "round": _int_or_none(row.get("gameday")),
        "utc_date": None,
        "local_date": _parse_v1_local_datetime(row),
        "home_team_id": home_code,
        "home_team_name": row.get("hometeam") or home_code,
        "away_team_id": away_code,
        "away_team_name": row.get("awayteam") or away_code,
        "home_score": _int_or_none(row.get("homescore")),
        "away_score": _int_or_none(row.get("awayscore")),
        "overtime_periods": None,
        "game_status": "COMPLETED" if played else "SCHEDULED",
        "played": played,
        "venue_code": None,
        "venue_name": None,
        "venue_capacity": None,
        "neutral_venue": None,
    }
    raw = {
        "season": {"code": season_code},
        "round": normalized["round"],
        "id": None,
        "gameCode": game_code,
        "utcDate": None,
        "local": {"club": {"code": home_code}},
        "road": {"club": {"code": away_code}},
    }
    return {"normalized": normalized, "raw": raw}


def _parse_v1_local_datetime(row: Mapping[str, Any]) -> datetime | None:
    date_value = str(row.get("date") or "").strip()
    time_value = str(row.get("time") or row.get("startime") or "").strip()
    candidates = [
        (f"{date_value} {time_value}", "%b %d, %Y %H:%M"),
        (f"{date_value} {time_value}", "%d/%m/%Y %H:%M"),
        (date_value, "%Y-%m-%dT%H:%M:%S"),
    ]
    for value, pattern in candidates:
        try:
            return datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
    return None
