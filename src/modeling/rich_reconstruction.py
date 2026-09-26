"""Deterministic Phase 3B reconstruction of rotations and lineup context."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.db.ids import stable_id


ROTATION_RECONSTRUCTION_VERSION = "phase3b_rotation_v1"
MINUTE_NEAR_EXACT_TOLERANCE_SECONDS = 5
SUBSTITUTION_TYPES = frozenset({"IN", "OUT"})
POSSESSION_EVENT_TYPES = frozenset(
    {"2FGM", "2FGA", "3FGM", "3FGA", "FTM", "FTA", "O", "TO"}
)


@dataclass(frozen=True, slots=True)
class GameContext:
    game_id: str
    season: str
    game_code: int
    game_time_us: int | None
    overtime_count: int
    home_team_id: str
    away_team_id: str
    starters: dict[str, frozenset[str]]
    official_seconds: dict[str, int]
    invalid_clock: bool
    rotation_discrepancy: bool


@dataclass(frozen=True, slots=True)
class ReconstructionSummary:
    pbp_games: int
    structurally_reconstructed_games: int
    usable_rotation_games: int
    quarantined_rotation_games: int
    exact_minute_games: int
    near_exact_minute_games: int
    player_minute_rows: int
    exact_player_minute_rows: int
    near_exact_player_minute_rows: int
    outside_tolerance_player_rows: int
    maximum_absolute_error_seconds: int
    player_stints: int
    lineup_stints: int
    possession_estimate_rows: int
    estimated_possession_equivalents: float
    reconstruction_version: str


def parse_game_clock(value: Any) -> int:
    """Parse a valid remaining `MM:SS` clock, rejecting provider sentinels."""

    if not isinstance(value, str) or ":" not in value:
        raise ValueError(f"Invalid PBP clock: {value!r}")
    minutes_text, seconds_text = value.strip().split(":", maxsplit=1)
    minutes, seconds = int(minutes_text), int(seconds_text)
    if minutes < 0 or seconds < 0 or seconds >= 60:
        raise ValueError(f"Invalid PBP clock: {value!r}")
    return minutes * 60 + seconds


def period_length_seconds(period: int) -> int:
    if period < 1:
        raise ValueError("Period must be positive")
    return 600 if period <= 4 else 300


def period_offset_seconds(period: int) -> int:
    if period < 1:
        raise ValueError("Period must be positive")
    return (period - 1) * 600 if period <= 4 else 2400 + (period - 5) * 300


def generate_rotation_intermediates(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    season_end: str = "E2025",
) -> ReconstructionSummary:
    """Reconstruct every retained rich-season game and materialize derived tables."""

    with connect_database(database_path) as connection:
        contexts = _load_contexts(connection)
        all_lineups: list[dict[str, Any]] = []
        all_players: list[dict[str, Any]] = []
        all_possessions: list[dict[str, Any]] = []
        all_minute_audits: list[dict[str, Any]] = []
        audits: list[dict[str, Any]] = []
        game_events: list[dict[str, Any]] = []
        current_game: str | None = None

        cursor = connection.execute(
            f"""
            SELECT event.canonical_game_id, event.period, event.source_sequence,
                   event.game_clock, event.canonical_team_id,
                   event.canonical_player_id, event.play_type,
                   event.home_score, event.away_score
            FROM play_by_play_events AS event
            JOIN games AS game USING (canonical_game_id)
            WHERE game.season_code BETWEEN 'E2021' AND '{season_end}'
            ORDER BY event.canonical_game_id, event.period, event.source_sequence
            """
        )
        while rows := cursor.fetchmany(50_000):
            for row in rows:
                game_id = str(row[0])
                if current_game is not None and game_id != current_game:
                    _append_game_result(
                        contexts[current_game], game_events,
                        all_lineups, all_players, all_possessions,
                        all_minute_audits, audits,
                    )
                    game_events = []
                current_game = game_id
                game_events.append(
                    {
                        "period": int(row[1]),
                        "sequence": int(row[2]),
                        "clock": row[3],
                        "team_id": str(row[4]) if row[4] is not None else None,
                        "player_id": str(row[5]) if row[5] is not None else None,
                        "play_type": str(row[6]) if row[6] is not None else None,
                        "home_score": row[7],
                        "away_score": row[8],
                    }
                )
        if current_game is not None:
            _append_game_result(
                contexts[current_game], game_events,
                all_lineups, all_players, all_possessions,
                all_minute_audits, audits,
            )

        _replace_table(connection, "pbp_rotation_game_audit_v1", audits)
        _replace_table(
            connection, "pbp_rotation_player_minute_audit_v1", all_minute_audits
        )
        _replace_table(connection, "pbp_lineup_stints_v1", all_lineups)
        _replace_table(connection, "pbp_player_stints_v1", all_players)
        _replace_table(
            connection, "pbp_lineup_possession_components_v1", all_possessions
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE pbp_possessions_v1 AS
            SELECT game_id, season, game_time_us, team_id, opponent_team_id,
                   sum(fga) AS fga, sum(offensive_rebounds) AS offensive_rebounds,
                   sum(turnovers) AS turnovers, sum(fta) AS fta,
                   sum(fga) - sum(offensive_rebounds) + sum(turnovers)
                       + 0.44 * sum(fta) AS estimated_possessions,
                   'FGA-OREB+TO+0.44*FTA_TEAM_GAME_PROXY'
                       AS possession_definition,
                   min(rotation_reconstruction_version)
                       AS rotation_reconstruction_version
            FROM pbp_lineup_possession_components_v1
            GROUP BY game_id, season, game_time_us, team_id, opponent_team_id
            ORDER BY game_id, team_id
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW pbp_rotation_game_audit AS
            SELECT * FROM pbp_rotation_game_audit_v1;
            CREATE OR REPLACE VIEW pbp_rotation_player_minute_audit AS
            SELECT * FROM pbp_rotation_player_minute_audit_v1;
            CREATE OR REPLACE VIEW pbp_lineup_stints AS
            SELECT * FROM pbp_lineup_stints_v1;
            CREATE OR REPLACE VIEW pbp_player_stints AS
            SELECT * FROM pbp_player_stints_v1;
            CREATE OR REPLACE VIEW pbp_lineup_possession_components AS
            SELECT * FROM pbp_lineup_possession_components_v1;
            CREATE OR REPLACE VIEW pbp_possessions AS
            SELECT * FROM pbp_possessions_v1;
            """
        )
        row = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE structurally_reconstructed),
                   count(*) FILTER (WHERE rotation_usable),
                   count(*) FILTER (WHERE NOT rotation_usable),
                   count(*) FILTER (WHERE exact_minute_agreement),
                   count(*) FILTER (WHERE near_exact_minute_agreement),
                   sum(player_rows), sum(exact_player_rows),
                   sum(near_exact_player_rows), sum(outside_tolerance_player_rows),
                   max(maximum_absolute_error_seconds)
            FROM pbp_rotation_game_audit_v1
            """
        ).fetchone()
        possessions = connection.execute(
            """
            SELECT count(*), sum(estimated_possessions)
            FROM pbp_possessions_v1
            """
        ).fetchone()
        return ReconstructionSummary(
            pbp_games=int(row[0]),
            structurally_reconstructed_games=int(row[1]),
            usable_rotation_games=int(row[2]),
            quarantined_rotation_games=int(row[3]),
            exact_minute_games=int(row[4]),
            near_exact_minute_games=int(row[5]),
            player_minute_rows=int(row[6] or 0),
            exact_player_minute_rows=int(row[7] or 0),
            near_exact_player_minute_rows=int(row[8] or 0),
            outside_tolerance_player_rows=int(row[9] or 0),
            maximum_absolute_error_seconds=int(row[10] or 0),
            player_stints=len(all_players),
            lineup_stints=len(all_lineups),
            possession_estimate_rows=int(possessions[0]),
            estimated_possession_equivalents=float(possessions[1] or 0),
            reconstruction_version=ROTATION_RECONSTRUCTION_VERSION,
        )


def reconstruction_fingerprint(connection: Any) -> str:
    digest = hashlib.sha256()
    for table, columns in (
        (
            "pbp_player_stints_v1",
            "game_id, team_id, player_id, stint_number, start_second, end_second",
        ),
        (
            "pbp_lineup_stints_v1",
            "game_id, team_id, lineup_stint_number, start_second, end_second, lineup_key",
        ),
        (
            "pbp_rotation_game_audit_v1",
            "game_id, rotation_usable, quarantine_reason, maximum_absolute_error_seconds",
        ),
    ):
        rows = connection.execute(
            f"SELECT {columns} FROM {table} ORDER BY ALL"
        ).fetchall()
        for row in rows:
            digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _load_contexts(connection: Any) -> dict[str, GameContext]:
    anomaly_rows = connection.execute(
        """
        SELECT entity_id, anomaly_code FROM data_anomalies
        WHERE quarantined AND anomaly_code IN (
            'INVALID_PBP_CLOCK', 'ROTATION_PLAYER_MINUTE_DISCREPANCY'
        )
        """
    ).fetchall()
    invalid_clock = {str(row[0]) for row in anomaly_rows if row[1] == "INVALID_PBP_CLOCK"}
    discrepancy = {
        str(row[0])
        for row in anomaly_rows
        if row[1] == "ROTATION_PLAYER_MINUTE_DISCREPANCY"
    }
    game_rows = connection.execute(
        """
        SELECT DISTINCT game.canonical_game_id, game.season_code, game.game_code,
               epoch_us(game.game_date), coalesce(game.overtime_count, 0),
               game.home_team_id, game.away_team_id
        FROM games AS game
        JOIN play_by_play_events AS event USING (canonical_game_id)
        WHERE game.season_code BETWEEN 'E2021' AND 'E2025'
        """
    ).fetchall()
    players = connection.execute(
        """
        SELECT stat.canonical_game_id, stat.canonical_team_id,
               stat.canonical_player_id, stat.starter, stat.minutes_raw,
               stat.did_not_play
        FROM player_game_stats AS stat
        JOIN games AS game USING (canonical_game_id)
        WHERE game.season_code BETWEEN 'E2021' AND 'E2025'
        """
    ).fetchall()
    starters: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    official: dict[str, dict[str, int]] = defaultdict(dict)
    for game_id, team_id, player_id, starter, minutes_raw, did_not_play in players:
        game_key, team_key, player_key = str(game_id), str(team_id), str(player_id)
        if bool(starter):
            starters[game_key][team_key].add(player_key)
        if not bool(did_not_play):
            official[game_key][player_key] = _boxscore_seconds(minutes_raw)
    return {
        str(row[0]): GameContext(
            game_id=str(row[0]), season=str(row[1]), game_code=int(row[2]),
            game_time_us=int(row[3]) if row[3] is not None else None,
            overtime_count=int(row[4]),
            home_team_id=str(row[5]), away_team_id=str(row[6]),
            starters={
                team: frozenset(starters[str(row[0])][team])
                for team in (str(row[5]), str(row[6]))
            },
            official_seconds=official[str(row[0])],
            invalid_clock=str(row[0]) in invalid_clock,
            rotation_discrepancy=str(row[0]) in discrepancy,
        )
        for row in game_rows
    }


def _boxscore_seconds(value: Any) -> int:
    if not isinstance(value, str) or value.upper() == "DNP":
        return 0
    minutes, seconds = value.strip().split(":", maxsplit=1)
    return int(minutes) * 60 + int(seconds)


def _append_game_result(
    context: GameContext,
    events: list[dict[str, Any]],
    lineups: list[dict[str, Any]],
    players: list[dict[str, Any]],
    possessions: list[dict[str, Any]],
    minute_audits: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> None:
    result = reconstruct_game(context, events)
    audits.append(result["audit"])
    minute_audits.extend(result["minute_audit"])
    if result["audit"]["rotation_usable"]:
        lineups.extend(result["lineups"])
        players.extend(result["players"])
        possessions.extend(result["possessions"])


def reconstruct_game(
    context: GameContext,
    events: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Reconstruct one game without repairing malformed substitution state."""

    event_rows = list(events)
    reasons: list[str] = []
    if context.invalid_clock:
        reasons.append("INVALID_PBP_CLOCK")
    if context.rotation_discrepancy:
        reasons.append("KNOWN_ROTATION_MINUTE_DISCREPANCY")
    teams = (context.home_team_id, context.away_team_id)
    if any(len(context.starters.get(team, ())) != 5 for team in teams):
        reasons.append("NOT_EXACTLY_FIVE_STARTERS")
    expected_periods = 4 + context.overtime_count
    observed_periods = {int(row["period"]) for row in event_rows}
    if not set(range(1, expected_periods + 1)).issubset(observed_periods):
        reasons.append("MISSING_PBP_PERIOD")
    transactions: dict[tuple[int, int, str], dict[str, set[str]]] = defaultdict(
        lambda: {"IN": set(), "OUT": set()}
    )
    timed_events: list[dict[str, Any]] = []
    for event in event_rows:
        clock = event.get("clock")
        if not clock:
            continue
        try:
            seconds = parse_game_clock(clock)
        except (TypeError, ValueError):
            reasons.append("INVALID_PBP_CLOCK")
            continue
        period = int(event["period"])
        if seconds > period_length_seconds(period):
            reasons.append("CLOCK_EXCEEDS_PERIOD")
            continue
        row = dict(event)
        row["clock_seconds"] = seconds
        timed_events.append(row)
        if row.get("play_type") in SUBSTITUTION_TYPES:
            if not row.get("team_id") or not row.get("player_id"):
                reasons.append("INCOMPLETE_SUBSTITUTION")
                continue
            transactions[(period, seconds, row["team_id"])][row["play_type"]].add(
                row["player_id"]
            )
    for transaction in transactions.values():
        # A player can be recorded OUT and IN at the same dead-ball clock when
        # several substitutions are interleaved. That is a valid net no-op,
        # not an impossible duplicate state transition.
        unchanged = transaction["IN"] & transaction["OUT"]
        transaction["IN"].difference_update(unchanged)
        transaction["OUT"].difference_update(unchanged)
        if len(transaction["IN"]) != len(transaction["OUT"]):
            reasons.append("UNBALANCED_SUBSTITUTION_TRANSACTION")

    state = {team: set(context.starters.get(team, ())) for team in teams}
    combined: list[dict[str, Any]] = []
    structurally_valid = not reasons
    if structurally_valid:
        for period in range(1, expected_periods + 1):
            length = period_length_seconds(period)
            previous_clock = length
            clocks = sorted(
                {
                    clock
                    for transaction_period, clock, _ in transactions
                    if transaction_period == period
                },
                reverse=True,
            )
            for clock in clocks:
                if clock < previous_clock:
                    _add_combined_interval(
                        combined, context, period, previous_clock, clock, state,
                    )
                for team in teams:
                    transaction = transactions.get((period, clock, team))
                    if not transaction:
                        continue
                    if not transaction["OUT"].issubset(state[team]):
                        reasons.append("SUBSTITUTION_OUT_NOT_ON_COURT")
                    if transaction["IN"] & state[team]:
                        reasons.append("SUBSTITUTION_IN_ALREADY_ON_COURT")
                    state[team].difference_update(transaction["OUT"])
                    state[team].update(transaction["IN"])
                    if len(state[team]) != 5:
                        reasons.append("SETTLED_LINEUP_NOT_FIVE")
                previous_clock = clock
            if previous_clock > 0:
                _add_combined_interval(combined, context, period, previous_clock, 0, state)
        structurally_valid = not reasons

    if structurally_valid:
        _assign_events_to_intervals(combined, timed_events, context)
        lineup_rows, possession_rows = _team_lineup_rows(combined, context)
        player_rows = _player_stint_rows(combined, context)
    else:
        lineup_rows, possession_rows, player_rows = [], [], []

    reconstructed_seconds: dict[str, int] = defaultdict(int)
    for row in player_rows:
        reconstructed_seconds[row["player_id"]] += int(row["duration_seconds"])
    # Minute agreement is meaningful only after the lineup state has passed all
    # structural checks. Malformed games remain visible in the game audit, but
    # are not misreported as thousands of seconds of player-minute error.
    errors = (
        {
            player: reconstructed_seconds.get(player, 0) - official
            for player, official in context.official_seconds.items()
        }
        if structurally_valid
        else {}
    )
    exact = sum(error == 0 for error in errors.values())
    near = sum(abs(error) <= MINUTE_NEAR_EXACT_TOLERANCE_SECONDS for error in errors.values())
    outside = sum(abs(error) > MINUTE_NEAR_EXACT_TOLERANCE_SECONDS for error in errors.values())
    max_error = max((abs(error) for error in errors.values()), default=0)
    if structurally_valid and outside:
        reasons.append("PLAYER_MINUTES_OUTSIDE_TOLERANCE")
    usable = structurally_valid and not outside and not context.rotation_discrepancy
    if not usable:
        lineup_rows, possession_rows, player_rows = [], [], []
    player_teams = {
        row["player_id"]: row["team_id"] for row in player_rows
    } if usable else {
        row["player_id"]: row["team_id"]
        for row in _player_stint_rows(combined, context)
    } if structurally_valid else {}
    minute_audit = [
        {
            "game_id": context.game_id,
            "season": context.season,
            "game_code": context.game_code,
            "game_time_us": context.game_time_us,
            "team_id": player_teams.get(player),
            "player_id": player,
            "official_seconds": context.official_seconds[player],
            "reconstructed_seconds": context.official_seconds[player] + error,
            "difference_seconds": error,
            "absolute_difference_seconds": abs(error),
            "exact_agreement": error == 0,
            "near_exact_agreement": (
                abs(error) <= MINUTE_NEAR_EXACT_TOLERANCE_SECONDS
            ),
            "within_tolerance": (
                abs(error) <= MINUTE_NEAR_EXACT_TOLERANCE_SECONDS
            ),
            "rotation_usable": usable,
            "rotation_reconstruction_version": ROTATION_RECONSTRUCTION_VERSION,
        }
        for player, error in errors.items()
    ]
    audit = {
        "game_id": context.game_id,
        "season": context.season,
        "game_code": context.game_code,
        "game_time_us": context.game_time_us,
        "event_count": len(event_rows),
        "period_count": len(observed_periods),
        "overtime_count": context.overtime_count,
        "structurally_reconstructed": structurally_valid,
        "rotation_usable": usable,
        "quarantine_reason": "|".join(sorted(set(reasons))) or None,
        "player_rows": len(errors),
        "exact_player_rows": exact,
        "near_exact_player_rows": near,
        "outside_tolerance_player_rows": outside,
        "exact_minute_agreement": structurally_valid and exact == len(errors),
        "near_exact_minute_agreement": structurally_valid and near == len(errors),
        "maximum_absolute_error_seconds": max_error,
        "total_absolute_error_seconds": sum(abs(error) for error in errors.values()),
        "rotation_reconstruction_version": ROTATION_RECONSTRUCTION_VERSION,
    }
    return {
        "audit": audit,
        "lineups": lineup_rows,
        "players": player_rows,
        "possessions": possession_rows,
        "minute_audit": minute_audit,
    }


def _add_combined_interval(
    rows: list[dict[str, Any]],
    context: GameContext,
    period: int,
    start_clock: int,
    end_clock: int,
    state: dict[str, set[str]],
) -> None:
    offset = period_offset_seconds(period)
    length = period_length_seconds(period)
    start = offset + length - start_clock
    end = offset + length - end_clock
    if end <= start:
        return
    rows.append(
        {
            "period": period,
            "start_second": start,
            "end_second": end,
            "duration_seconds": end - start,
            "home_lineup": tuple(sorted(state[context.home_team_id])),
            "away_lineup": tuple(sorted(state[context.away_team_id])),
            "events": [],
        }
    )


def _assign_events_to_intervals(
    intervals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    context: GameContext,
) -> None:
    by_period: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for interval in intervals:
        by_period[interval["period"]].append(interval)
    for event in events:
        if event.get("play_type") in SUBSTITUTION_TYPES:
            continue
        period = int(event["period"])
        absolute = (
            period_offset_seconds(period)
            + period_length_seconds(period)
            - int(event["clock_seconds"])
        )
        candidates = by_period.get(period, [])
        selected = None
        # Same-clock dead-ball events are assigned to the pre-transaction state;
        # a period-start event necessarily belongs to the post-start transaction.
        for interval in candidates:
            if interval["start_second"] < absolute <= interval["end_second"]:
                selected = interval
                break
        if selected is None:
            for interval in candidates:
                if interval["start_second"] <= absolute < interval["end_second"]:
                    selected = interval
                    break
        if selected is not None:
            selected["events"].append(event)


def _event_counts(events: list[dict[str, Any]], team_id: str) -> dict[str, float]:
    own = [event for event in events if event.get("team_id") == team_id]
    counts = {
        "fga": sum(event.get("play_type") in {"2FGM", "2FGA", "3FGM", "3FGA"} for event in own),
        "oreb": sum(event.get("play_type") == "O" for event in own),
        "turnovers": sum(event.get("play_type") == "TO" for event in own),
        "fta": sum(event.get("play_type") in {"FTM", "FTA"} for event in own),
        "points": sum(
            2 if event.get("play_type") == "2FGM"
            else 3 if event.get("play_type") == "3FGM"
            else 1 if event.get("play_type") == "FTM" else 0
            for event in own
        ),
    }
    counts["estimated_possessions"] = (
        counts["fga"] - counts["oreb"] + counts["turnovers"] + 0.44 * counts["fta"]
    )
    return counts


def _team_lineup_rows(
    intervals: list[dict[str, Any]], context: GameContext
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lineup_rows: list[dict[str, Any]] = []
    possession_rows: list[dict[str, Any]] = []
    counters: dict[str, int] = defaultdict(int)
    teams = (context.home_team_id, context.away_team_id)
    for interval_number, interval in enumerate(intervals, start=1):
        for team_id in teams:
            opponent = context.away_team_id if team_id == context.home_team_id else context.home_team_id
            lineup = interval["home_lineup"] if team_id == context.home_team_id else interval["away_lineup"]
            opponent_lineup = interval["away_lineup"] if team_id == context.home_team_id else interval["home_lineup"]
            own = _event_counts(interval["events"], team_id)
            against = _event_counts(interval["events"], opponent)
            counters[team_id] += 1
            lineup_key = "|".join(lineup)
            base = {
                "game_id": context.game_id, "season": context.season,
                "game_code": context.game_code, "game_time_us": context.game_time_us,
                "team_id": team_id, "opponent_team_id": opponent,
                "period": interval["period"],
                "lineup_stint_number": counters[team_id],
                "combined_interval_number": interval_number,
                "start_second": interval["start_second"],
                "end_second": interval["end_second"],
                "duration_seconds": interval["duration_seconds"],
                "lineup_key": lineup_key,
                "opponent_lineup_key": "|".join(opponent_lineup),
                "player_1_id": lineup[0], "player_2_id": lineup[1],
                "player_3_id": lineup[2], "player_4_id": lineup[3],
                "player_5_id": lineup[4],
                "points_for": own["points"], "points_against": against["points"],
                "rich_source_game_time_us": context.game_time_us,
                "rotation_reconstruction_version": ROTATION_RECONSTRUCTION_VERSION,
            }
            base["lineup_stint_id"] = stable_id(
                "phase3b-lineup-stint", context.game_id, team_id,
                counters[team_id], interval["start_second"], interval["end_second"],
                lineup_key,
            )
            lineup_rows.append(base)
            possession_rows.append(
                {
                    "lineup_stint_id": base["lineup_stint_id"],
                    "game_id": context.game_id, "season": context.season,
                    "game_time_us": context.game_time_us, "team_id": team_id,
                    "opponent_team_id": opponent,
                    "fga": own["fga"], "offensive_rebounds": own["oreb"],
                    "turnovers": own["turnovers"], "fta": own["fta"],
                    "estimated_possession_component": own["estimated_possessions"],
                    "opponent_estimated_possession_component": against["estimated_possessions"],
                    "estimated_game_possession_component": (
                        own["estimated_possessions"] + against["estimated_possessions"]
                    ) / 2.0,
                    "possession_component_definition": "FGA-OREB+TO+0.44*FTA_LINEUP_SEGMENT_COMPONENT",
                    "rotation_reconstruction_version": ROTATION_RECONSTRUCTION_VERSION,
                }
            )
    return lineup_rows, possession_rows


def _player_stint_rows(
    intervals: list[dict[str, Any]], context: GameContext
) -> list[dict[str, Any]]:
    on_intervals: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)
    for interval in intervals:
        for team_id, key in (
            (context.home_team_id, "home_lineup"),
            (context.away_team_id, "away_lineup"),
        ):
            for player_id in interval[key]:
                on_intervals[(team_id, player_id)].append(
                    (interval["start_second"], interval["end_second"], interval["period"])
                )
    rows: list[dict[str, Any]] = []
    for (team_id, player_id), spans in sorted(on_intervals.items()):
        merged: list[list[int]] = []
        for start, end, period in spans:
            if merged and merged[-1][1] == start:
                merged[-1][1] = end
                merged[-1][3] = period
            else:
                merged.append([start, end, period, period])
        for number, (start, end, start_period, end_period) in enumerate(merged, start=1):
            rows.append(
                {
                    "player_stint_id": stable_id(
                        "phase3b-player-stint", context.game_id, team_id,
                        player_id, number, start, end,
                    ),
                    "game_id": context.game_id, "season": context.season,
                    "game_code": context.game_code,
                    "game_time_us": context.game_time_us,
                    "team_id": team_id,
                    "opponent_team_id": (
                        context.away_team_id if team_id == context.home_team_id
                        else context.home_team_id
                    ),
                    "player_id": player_id, "stint_number": number,
                    "start_second": start, "end_second": end,
                    "duration_seconds": end - start,
                    "start_period": start_period, "end_period": end_period,
                    "rich_source_game_time_us": context.game_time_us,
                    "rotation_reconstruction_version": ROTATION_RECONSTRUCTION_VERSION,
                }
            )
    return rows


def _replace_table(connection: Any, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot materialize empty rich table {table}")
    frame = pd.DataFrame.from_records(rows)
    name = f"_{table}_frame"
    connection.register(name, frame)
    connection.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {name}")
    connection.unregister(name)
