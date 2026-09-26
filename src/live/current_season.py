"""Incremental, idempotent current-season EuroLeague basketball updates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

from duckdb import ConstraintException

from src.data.euroleague_client import APIResponse, EuroLeagueClient
from src.data.normalizers import extract_xml_rows, normalize_games, normalize_roster_players
from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.db.ingestion import (
    CanonicalIngestor,
    _datetime_or_none,
    _ingest_boxscore,
    _ingest_game,
    _ingest_pbp,
    _ingest_roster,
    _ingest_shots,
)
from src.db.raw_store import DEFAULT_RAW_ROOT, archive_bytes


@dataclass(frozen=True, slots=True)
class CurrentSeasonUpdateResult:
    season_code: str
    schedule_games: int
    new_games: int
    updated_games: int
    unchanged_games: int
    rescheduled_games: int
    completed_games_ingested: int
    boxscores_ingested: int
    pbp_ingested: int
    shots_ingested: int
    roster_rows: int
    network_requests: int
    warnings: tuple[str, ...]
    elapsed_seconds: float
    snapshot_batch_id: str


def current_season_code(now: datetime | None = None) -> str:
    instant = now or datetime.now(UTC)
    return f"E{instant.year if instant.month >= 7 else instant.year - 1}"


def update_current_season(
    season_code: str | None = None,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    raw_root: Path = DEFAULT_RAW_ROOT,
    timeout_seconds: float = 30.0,
    include_rich: bool = True,
    dry_run: bool = False,
    max_round_metadata_requests: int = 2,
    client: EuroLeagueClient | None = None,
    captured_at: datetime | None = None,
) -> CurrentSeasonUpdateResult:
    """Fetch schedule state, then download only new/changed/missing game facts."""

    started = time.monotonic()
    observed = _as_utc(captured_at or datetime.now(UTC))
    season_code = (season_code or current_season_code(observed)).upper()
    if len(season_code) != 5 or season_code[0] != "E" or not season_code[1:].isdigit():
        raise ValueError("season_code must look like E2026")
    if max_round_metadata_requests <= 0:
        raise ValueError("max_round_metadata_requests must be positive")
    season = int(season_code[1:])
    initialize_database(database_path)
    warnings: list[str] = []
    state = {"network": 0, "box": 0, "pbp": 0, "shots": 0, "completed": 0}
    owned = client is None
    api = client or EuroLeagueClient(timeout_seconds=timeout_seconds)
    snapshot_batch_id = stable_id(
        "live_schedule_batch", season_code, observed.astimezone(UTC).isoformat()
    )
    try:
        schedule_response = api.schedule(season)
        state["network"] += 1
        results_response = api.results(season)
        state["network"] += 1
        schedule_rows = extract_xml_rows(schedule_response.text, "item")
        result_rows = extract_xml_rows(results_response.text, "game")
        if not schedule_rows:
            raise ValueError(f"Official schedule is empty for {season_code}")
        result_by_code = {
            int(row["gamenumber"]): row
            for row in result_rows
            if str(row.get("gamenumber", "")).isdigit()
        }
        with connect_database(database_path, read_only=True) as connection:
            previous = {
                int(row[0]): {
                    "played": bool(row[1]), "tip": row[2], "local": row[3],
                    "home": str(row[4]), "away": str(row[5]),
                    "home_score": row[6], "away_score": row[7],
                    "status": row[8], "fingerprint": str(row[9]),
                    "official_game_id": row[10],
                }
                for row in connection.execute(
                    """
                    SELECT game_code, played, scheduled_tip_time, local_game_date,
                           home_team_id, away_team_id, home_score, away_score,
                           game_status, row_fingerprint, official_game_id
                    FROM current_live_schedule WHERE season_code=?
                    """,
                    [season_code],
                ).fetchall()
            }
        prelim = [
            _schedule_candidate(row, result_by_code.get(int(row.get("game") or 0)))
            for row in schedule_rows
            if str(row.get("game", "")).isdigit()
        ]
        for candidate in prelim:
            candidate["season_code"] = season_code
            candidate["canonical_game_id"] = stable_id(
                "game", "E", season_code, candidate["game_code"]
            )
        classifications = {
            int(row["game_code"]): _classify_schedule_row(row, previous.get(int(row["game_code"])))
            for row in prelim
        }
        changed_rounds = sorted({
            int(row["round_number"])
            for row in prelim
            if classifications[int(row["game_code"])] != "UNCHANGED_GAME"
        })
        # Missing canonical components are retried even when schedule metadata is unchanged.
        with connect_database(database_path, read_only=True) as connection:
            missing_codes = {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT schedule.game_code
                    FROM current_live_schedule AS schedule
                    LEFT JOIN current_games AS game USING (canonical_game_id)
                    LEFT JOIN player_game_stats AS stat USING (canonical_game_id)
                    WHERE schedule.season_code=? AND schedule.played
                    GROUP BY schedule.game_code, game.played, schedule.played,
                             game.home_score, schedule.home_score,
                             game.away_score, schedule.away_score
                    HAVING count(stat.player_game_id)=0
                        OR game.played IS DISTINCT FROM schedule.played
                        OR game.home_score IS DISTINCT FROM schedule.home_score
                        OR game.away_score IS DISTINCT FROM schedule.away_score
                    """,
                    [season_code],
                ).fetchall()
            }
        changed_rounds.extend(
            int(row["round_number"]) for row in prelim if int(row["game_code"]) in missing_codes
        )
        changed_rounds.extend(
            int(row["round_number"])
            for row in prelim
            if previous.get(int(row["game_code"]), {}).get("tip") is None
        )
        changed_rounds = sorted(set(changed_rounds))[:max_round_metadata_requests]
        if dry_run:
            return _result(
                season_code, prelim, classifications, state, 0, warnings,
                started, snapshot_batch_id,
            )

        round_games: dict[int, Mapping[str, Any]] = {}
        round_responses: dict[int, APIResponse] = {}
        for round_number in changed_rounds:
            try:
                response = api.round_games(season, round_number)
                state["network"] += 1
                round_responses[round_number] = response
                for game in response.payload.get("data", []):
                    if isinstance(game, Mapping) and game.get("gameCode") is not None:
                        round_games[int(game["gameCode"])] = game
            except Exception as error:
                warnings.append(f"round {round_number} metadata unavailable: {error}")

        roster_response: APIResponse | None = None
        try:
            roster_response = api.season_players(season)
            state["network"] += 1
        except Exception as error:
            warnings.append(f"roster unavailable: {error}")

        with connect_database(database_path) as connection:
            ingestor = CanonicalIngestor(
                connection,
                source="live_current_season_update",
                command="python -m scripts.update_current_season",
                competition_code="E",
                season_code=season_code,
            )
            try:
                schedule_artifact = _archive_response(
                    ingestor, schedule_response, raw_root, season_code, observed,
                    "schedule", "xml",
                )
                _archive_response(
                    ingestor, results_response, raw_root, season_code, observed,
                    "results", "xml",
                )
                round_artifacts: dict[int, str] = {}
                for round_number, response in round_responses.items():
                    round_artifacts[round_number] = _archive_response(
                        ingestor, response, raw_root, season_code, observed,
                        f"round_{round_number}", "json",
                    )
                season_id = ingestor.ensure_season(season_code)
                normalized_by_code = {
                    int(raw["gameCode"]): normalized
                    for raw, normalized in zip(
                        round_games.values(), normalize_games(round_games.values()), strict=True
                    )
                }
                for candidate in prelim:
                    game_code = int(candidate["game_code"])
                    ingestor.ensure_team(
                        candidate["home_team_code"], candidate["home_team_name"],
                        season_code=season_code,
                    )
                    ingestor.ensure_team(
                        candidate["away_team_code"], candidate["away_team_name"],
                        season_code=season_code,
                    )
                    normalized = normalized_by_code.get(game_code)
                    if normalized is not None:
                        game_id = _upsert_mutable_game(
                            ingestor, normalized, season_id,
                            round_artifacts[int(candidate["round_number"])],
                        )
                        candidate = _candidate_from_normalized(candidate, normalized, game_id)
                        if previous.get(game_code, {}).get("tip") is None and classifications[game_code] == "UNCHANGED_GAME":
                            classifications[game_code] = "UPDATED_GAME"
                    else:
                        game_id = stable_id("game", "E", season_code, game_code)
                        candidate["canonical_game_id"] = game_id
                        # Round-detail requests are deliberately bounded. Do not
                        # erase authoritative metadata learned by an earlier run
                        # merely because this game was not in today's bounded
                        # detail batch.
                        prior = previous.get(game_code, {})
                        candidate["scheduled_tip_time"] = prior.get("tip")
                        candidate["official_game_id"] = prior.get("official_game_id")
                    _insert_schedule_snapshot(
                        connection, candidate, classifications[game_code],
                        snapshot_batch_id, observed, schedule_artifact,
                    )

                roster_count = 0
                if roster_response is not None:
                    roster_artifact = _archive_response(
                        ingestor, roster_response, raw_root, season_code, observed,
                        "roster", "json",
                    )
                    roster_rows = normalize_roster_players(roster_response.payload)
                    roster_count = len(roster_rows)
                    _ingest_roster(ingestor, roster_rows, season_id, roster_artifact)
                    _insert_roster_snapshot(
                        connection, ingestor, roster_rows, season_code,
                        snapshot_batch_id, observed, roster_artifact,
                    )

                completed = [
                    row for row in prelim
                    if row["played"] and int(row["game_code"]) in round_games
                ]
                for candidate in completed:
                    game_code = int(candidate["game_code"])
                    game = round_games[game_code]
                    game_id = stable_id("game", "E", season_code, game_code)
                    counts = connection.execute(
                        """
                        SELECT
                          (SELECT count(*) FROM player_game_stats WHERE canonical_game_id=?),
                          (SELECT count(*) FROM play_by_play_events WHERE canonical_game_id=?),
                          (SELECT count(*) FROM shots WHERE canonical_game_id=?)
                        """,
                        [game_id, game_id, game_id],
                    ).fetchone()
                    if int(counts[0]) == 0:
                        try:
                            box = api.game_boxscore(season, game_code)
                            state["network"] += 1
                            box_artifact = _archive_response(
                                ingestor, box, raw_root, season_code, observed,
                                f"boxscore_{game_code}", "json", game_code,
                            )
                            _ingest_boxscore(ingestor, game, box.payload, box_artifact)
                            state["box"] += 1
                            state["completed"] += 1
                        except Exception as error:
                            warnings.append(f"boxscore {game_code} unavailable: {error}")
                    if include_rich and int(counts[1]) == 0:
                        try:
                            pbp = api.game_play_by_play(season, game_code)
                            state["network"] += 1
                            pbp_artifact = _archive_response(
                                ingestor, pbp, raw_root, season_code, observed,
                                f"play_by_play_{game_code}", "json", game_code,
                            )
                            _ingest_pbp(ingestor, season_code, game_code, pbp.payload, pbp_artifact)
                            state["pbp"] += 1
                        except Exception as error:
                            warnings.append(f"PBP {game_code} quarantined/unavailable: {error}")
                    if include_rich and int(counts[2]) == 0:
                        try:
                            shots = api.game_shots(season, game_code)
                            state["network"] += 1
                            shot_artifact = _archive_response(
                                ingestor, shots, raw_root, season_code, observed,
                                f"shots_{game_code}", "json", game_code,
                            )
                            _ingest_shots(ingestor, season_code, game_code, shots.payload, shot_artifact)
                            state["shots"] += 1
                        except Exception as error:
                            warnings.append(f"shots {game_code} quarantined/unavailable: {error}")
                ingestor.finish()
            except BaseException as error:
                ingestor.fail(error)
                raise
        return _result(
            season_code, prelim, classifications, state, roster_count, warnings,
            started, snapshot_batch_id,
        )
    finally:
        if owned:
            api.close()


def _schedule_candidate(row: Mapping[str, Any], result: Mapping[str, Any] | None) -> dict[str, Any]:
    game_code = int(row["game"])
    played_text = str(row.get("played", "")).casefold()
    result_played = str((result or {}).get("played", "")).casefold() == "true"
    played = played_text == "true" or result_played
    local = _schedule_local_datetime(row.get("date"), row.get("startime"))
    home_score = _integer((result or {}).get("scorea"))
    away_score = _integer((result or {}).get("scoreb"))
    payload = {
        "season_code": None,
        "canonical_game_id": None,
        "official_game_id": None,
        "game_code": game_code,
        "phase_code": str(row.get("round") or "") or None,
        "round_number": int(row.get("gameday") or 0),
        "scheduled_tip_time": None,
        "local_game_date": local,
        "home_team_code": str(row.get("homecode") or "").strip(),
        "away_team_code": str(row.get("awaycode") or "").strip(),
        "home_team_name": str(row.get("hometeam") or "").strip(),
        "away_team_name": str(row.get("awayteam") or "").strip(),
        "home_score": home_score,
        "away_score": away_score,
        "game_status": "Played" if played else (
            "Confirmed" if str(row.get("confirmeddate", "")).casefold() == "true" else "Scheduled"
        ),
        "played": played,
    }
    return payload


def _classify_schedule_row(row: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    if previous is None:
        return "NEW_GAME"
    local = row.get("local_game_date")
    if local is not None and previous.get("local") is not None and local != previous["local"]:
        return "POSTPONED_RESCHEDULED_GAME"
    comparisons = (
        bool(row["played"]) != bool(previous["played"]),
        row.get("home_score") != previous.get("home_score"),
        row.get("away_score") != previous.get("away_score"),
        row.get("game_status") != previous.get("status"),
    )
    return "UPDATED_GAME" if any(comparisons) else "UNCHANGED_GAME"


def _candidate_from_normalized(
    candidate: dict[str, Any], normalized: Mapping[str, Any], game_id: str
) -> dict[str, Any]:
    candidate.update({
        "season_code": normalized["season_code"],
        "canonical_game_id": game_id,
        "official_game_id": normalized.get("game_id"),
        "phase_code": normalized.get("phase_code"),
        "round_number": normalized.get("round"),
        "scheduled_tip_time": _datetime_or_none(normalized.get("utc_date")),
        # Preserve the v1 schedule's local wall-clock value for like-for-like
        # reschedule detection. v2 supplies the authoritative UTC tip below,
        # but its `localDate` semantics differ for a small subset of venues.
        "home_score": normalized.get("home_score") if normalized.get("played") else None,
        "away_score": normalized.get("away_score") if normalized.get("played") else None,
        "game_status": "Played" if normalized.get("played") else normalized.get("game_status"),
        "played": bool(normalized.get("played")),
    })
    return candidate


def _upsert_mutable_game(
    ingestor: CanonicalIngestor,
    normalized: Mapping[str, Any],
    season_id: str,
    artifact_id: str,
) -> str:
    game_id = stable_id(
        "game", normalized.get("competition_code") or "E",
        normalized["season_code"], normalized["game_code"],
    )
    mutable = {
        "official_game_id": normalized.get("game_id"),
        "game_identifier": normalized.get("game_identifier"),
        "phase_code": normalized.get("phase_code"),
        "round_number": normalized.get("round"),
        "game_date": _datetime_or_none(normalized.get("utc_date")),
        "local_game_date": _datetime_or_none(normalized.get("local_date")),
        "home_score": normalized.get("home_score"),
        "away_score": normalized.get("away_score"),
        "overtime_count": normalized.get("overtime_periods"),
        "game_status": normalized.get("game_status"),
        "played": normalized.get("played"),
        "venue_code": normalized.get("venue_code"),
        "venue_name": normalized.get("venue_name"),
        "venue_capacity": normalized.get("venue_capacity"),
        "neutral_venue": normalized.get("neutral_venue"),
    }
    exists = ingestor.connection.execute(
        "SELECT 1 FROM games WHERE canonical_game_id=?", [game_id]
    ).fetchone()
    if exists is None:
        return _ingest_game(ingestor, normalized, season_id, artifact_id)

    home_id = ingestor.ensure_team(
        normalized["home_team_id"], normalized["home_team_name"],
        season_code=normalized["season_code"],
    )
    away_id = ingestor.ensure_team(
        normalized["away_team_id"], normalized["away_team_name"],
        season_code=normalized["season_code"],
    )
    mutable["home_team_id"] = home_id
    mutable["away_team_id"] = away_id

    # DuckDB may implement an UPDATE of a referenced PK row as delete+insert,
    # even when the PK itself is unchanged. Identify factual changes first and
    # avoid a no-op UPDATE entirely when live predictions reference the game.
    changed_flags = ingestor.connection.execute(
        "SELECT " + ", ".join(
            f"{column} IS DISTINCT FROM ?" for column in mutable
        ) + " FROM games WHERE canonical_game_id=?",
        [*mutable.values(), game_id],
    ).fetchone()
    changed = [
        column for column, differs in zip(mutable, changed_flags or (), strict=True)
        if bool(differs)
    ]
    if not changed:
        return game_id
    try:
        ingestor.connection.execute(
            "UPDATE games SET " + ", ".join(f"{column}=?" for column in changed)
            + ", source_artifact_id=?, ingestion_run_id=? WHERE canonical_game_id=?",
            [*(mutable[column] for column in changed), artifact_id, ingestor.run_id, game_id],
        )
    except ConstraintException as error:
        if "is still referenced by a foreign key" not in str(error):
            raise
        # The games CHECK constraint reads indexed team FKs, so even a score
        # update can replace the parent row in DuckDB. The caller always appends
        # the authoritative schedule observation; current_games exposes it to
        # live/history readers without deleting any prediction/statistic child.
        return game_id
    ingestor.updated += 1
    return game_id


def _insert_schedule_snapshot(
    connection: Any,
    candidate: Mapping[str, Any],
    schedule_state: str,
    batch_id: str,
    captured_at: datetime,
    artifact_id: str,
) -> None:
    game_id = str(candidate["canonical_game_id"])
    home_id = stable_id("team", "euroleague", candidate["home_team_code"])
    away_id = stable_id("team", "euroleague", candidate["away_team_code"])
    payload = {
        key: candidate.get(key)
        for key in (
            "game_code", "phase_code", "round_number", "scheduled_tip_time",
            "local_game_date", "home_score", "away_score", "game_status", "played",
        )
    }
    payload["home_team_id"] = home_id
    payload["away_team_id"] = away_id
    fingerprint = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode()
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO live_schedule_snapshots (
          schedule_snapshot_id, snapshot_batch_id, captured_at, season_code,
          canonical_game_id, official_game_id, game_code, phase_code,
          round_number, scheduled_tip_time, local_game_date, home_team_id,
          away_team_id, home_score, away_score, game_status, played,
          schedule_state, change_summary_json, source_identifier,
          source_artifact_id, row_fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  'official_euroleague_schedule', ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [stable_id("live_schedule_snapshot", batch_id, game_id), batch_id,
         captured_at, candidate["season_code"], game_id,
         candidate.get("official_game_id"), candidate["game_code"],
         candidate.get("phase_code"), candidate.get("round_number"),
         candidate.get("scheduled_tip_time"), candidate.get("local_game_date"),
         home_id, away_id, candidate.get("home_score"), candidate.get("away_score"),
         candidate.get("game_status"), candidate["played"], schedule_state,
         json.dumps({"classification": schedule_state}), artifact_id, fingerprint],
    )


def _insert_roster_snapshot(
    connection: Any,
    ingestor: CanonicalIngestor,
    rows: list[Mapping[str, Any]],
    season_code: str,
    batch_id: str,
    captured_at: datetime,
    artifact_id: str,
) -> None:
    captured_at = _as_utc(captured_at)
    for row in rows:
        player_id = ingestor.ensure_player(
            row.get("player_id"), row.get("player_name"),
            source="euroleague",
            birth_date=row.get("birth_date"),
            nationality_code=row.get("nationality_code"),
            nationality=row.get("nationality"),
            height_cm=row.get("height_cm"),
            weight_kg=row.get("weight_kg"),
        )
        team_id = ingestor.ensure_team(
            row.get("team_id"), row.get("team_name"), season_code=season_code,
        )
        active = row.get("active")
        valid_from = _datetime_or_none(row.get("membership_start"))
        valid_to = _datetime_or_none(row.get("membership_end"))
        if valid_from is not None:
            valid_from = _as_utc(valid_from)
        if valid_to is not None:
            valid_to = _as_utc(valid_to)
        status = "CURRENT_ROSTER" if active is not False and (
            valid_to is None or valid_to > captured_at
        ) else "LEFT_TEAM"
        payload = {
            "player": player_id, "team": team_id, "status": status,
            "active": active, "valid_from": row.get("membership_start"),
            "valid_to": row.get("membership_end"), "jersey": row.get("jersey_number"),
            "position": row.get("position_code"),
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, default=str, sort_keys=True).encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO live_roster_snapshots (
              roster_snapshot_id, snapshot_batch_id, captured_at, season_code,
              canonical_player_id, canonical_team_id, roster_status,
              source_active, valid_from, valid_to, jersey_number, position_code,
              position_name, source_identifier, source_artifact_id, row_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'official_euroleague_roster', ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [stable_id("live_roster_snapshot", batch_id, player_id, team_id),
             batch_id, captured_at, season_code, player_id, team_id, status, active,
             valid_from, valid_to,
             row.get("jersey_number"), row.get("position_code"), row.get("position"),
             artifact_id, fingerprint],
        )


def _as_utc(value: datetime) -> datetime:
    """Treat timezone-less ingestion timestamps as UTC; preserve explicit offsets.

    Only use for observation/membership instants, not schedule local wall times.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _archive_response(
    ingestor: CanonicalIngestor,
    response: APIResponse,
    raw_root: Path,
    season_code: str,
    captured_at: datetime,
    component: str,
    suffix: str,
    game_code: int | None = None,
) -> str:
    timestamp = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    artifact = archive_bytes(
        response.text.encode("utf-8"),
        Path("euroleague", season_code, "live", component, f"{timestamp}.{suffix}"),
        raw_root=raw_root,
    )
    return ingestor.register_artifact(
        artifact,
        source="official_euroleague_api_live",
        endpoint=response.endpoint,
        source_url=response.url,
        competition_code="E",
        season_code=season_code,
        game_code=game_code,
        fetched_at=captured_at,
        media_type="application/xml" if suffix == "xml" else "application/json",
    )


def _schedule_local_datetime(date_value: Any, time_value: Any) -> datetime | None:
    value = f"{date_value or ''} {time_value or ''}".strip()
    for pattern in ("%b %d, %Y %H:%M", "%b %d, %Y"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _result(
    season_code: str,
    rows: list[Mapping[str, Any]],
    classifications: Mapping[int, str],
    state: Mapping[str, int],
    roster_count: int,
    warnings: list[str],
    started: float,
    snapshot_batch_id: str,
) -> CurrentSeasonUpdateResult:
    counts = {name: list(classifications.values()).count(name) for name in set(classifications.values())}
    return CurrentSeasonUpdateResult(
        season_code=season_code,
        schedule_games=len(rows),
        new_games=counts.get("NEW_GAME", 0),
        updated_games=counts.get("UPDATED_GAME", 0),
        unchanged_games=counts.get("UNCHANGED_GAME", 0),
        rescheduled_games=counts.get("POSTPONED_RESCHEDULED_GAME", 0),
        completed_games_ingested=int(state["completed"]),
        boxscores_ingested=int(state["box"]),
        pbp_ingested=int(state["pbp"]),
        shots_ingested=int(state["shots"]),
        roster_rows=roster_count,
        network_requests=int(state["network"]),
        warnings=tuple(warnings),
        elapsed_seconds=time.monotonic() - started,
        snapshot_batch_id=snapshot_batch_id,
    )
