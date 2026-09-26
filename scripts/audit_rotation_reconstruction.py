#!/usr/bin/env python3
"""Audit whether retained EuroLeague play-by-play can reconstruct rotations.

The audit is deliberately offline: it reads the retained raw box-score and
play-by-play fixtures, reconstructs on-court states from starters and explicit
``IN``/``OUT`` events, and reconciles the resulting seconds to the official
box score.  It also records ordering traps that matter when play events are
later attributed to a lineup.

Run from the project root:

    python -m scripts.audit_rotation_reconstruction
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_DIRECTORY = PROJECT_ROOT / "data" / "samples" / "e2025_rounds_1_2"
DEFAULT_OVERTIME_DIRECTORY = (
    PROJECT_ROOT / "data" / "samples" / "rotation_overtime_probe"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "samples" / "rotation_reconstruction_summary.json"

OVERTIME_PROBE_SOURCE_URLS = {
    "game_170_boxscore.json": "https://live.euroleague.net/api/Boxscore?gamecode=170&seasoncode=E2023",
    "game_170_header.json": "https://live.euroleague.net/api/Header?gamecode=170&seasoncode=E2023",
    "game_170_play_by_play.json": "https://live.euroleague.net/api/PlaybyPlay?gamecode=170&seasoncode=E2023",
}

REGULATION_PERIODS = (
    ("FirstQuarter", 1),
    ("SecondQuarter", 2),
    ("ThirdQuarter", 3),
    ("ForthQuarter", 4),
)
SUBSTITUTION_TYPES = frozenset({"IN", "OUT"})
TIMEOUT_TYPES = frozenset({"TOUT", "TOUT_TV"})
PERIOD_BEGIN = "BP"
PERIOD_END = "EP"
GAME_END = "EG"


class RotationAuditError(ValueError):
    """Raised when an input fixture is malformed rather than merely inconsistent."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_clock_seconds(value: Any) -> int:
    """Parse a remaining-clock ``MM:SS`` value into seconds."""

    text = _text(value)
    try:
        minutes_text, seconds_text = text.split(":", maxsplit=1)
        minutes = int(minutes_text)
        seconds = int(seconds_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RotationAuditError(f"Invalid game clock: {value!r}") from exc
    if minutes < 0 or not 0 <= seconds < 60:
        raise RotationAuditError(f"Invalid game clock: {value!r}")
    return minutes * 60 + seconds


def parse_boxscore_seconds(value: Any) -> int:
    """Parse official player minutes; ``DNP`` is an explicit zero."""

    text = _text(value)
    if text.upper() == "DNP":
        return 0
    return parse_clock_seconds(text)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _periods(payload: Mapping[str, Any]) -> list[tuple[int, str, int, list[Mapping[str, Any]]]]:
    """Return period number, source name, length, and source-ordered events."""

    periods: list[tuple[int, str, int, list[Mapping[str, Any]]]] = []
    for source_name, period_number in REGULATION_PERIODS:
        events = [
            _mapping(event) for event in _list(payload.get(source_name))
        ]
        if events:
            periods.append((period_number, source_name, 600, events))

    extra_time = _list(payload.get("ExtraTime"))
    if extra_time and all(isinstance(period, list) for period in extra_time):
        for offset, raw_events in enumerate(extra_time, start=1):
            events = [_mapping(event) for event in raw_events]
            if events:
                periods.append((4 + offset, f"ExtraTime{offset}", 300, events))
    elif extra_time:
        grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for raw_event in extra_time:
            event = _mapping(raw_event)
            try:
                elapsed_minute = int(float(event.get("MINUTE")))
            except (TypeError, ValueError):
                elapsed_minute = 41
            overtime_offset = max(0, (elapsed_minute - 41) // 5)
            # In the flat legacy feed, an overtime EP/EG marker uses the first
            # elapsed-minute number of the *next* period (46, 51, 56, 61).
            # Timed 05:00 substitutions and BP at that same minute belong to
            # the next overtime; the closing marker belongs to the previous.
            if (
                _text(event.get("PLAYTYPE")) in {PERIOD_END, GAME_END}
                and elapsed_minute > 41
                and (elapsed_minute - 41) % 5 == 0
            ):
                overtime_offset = max(0, overtime_offset - 1)
            period_number = 5 + overtime_offset
            grouped[period_number].append(event)
        for period_number in sorted(grouped):
            periods.append(
                (period_number, "ExtraTime", 300, grouped[period_number])
            )
    return periods


def _boxscore_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    players_by_team: dict[str, dict[str, dict[str, Any]]] = {}
    starters_by_team: dict[str, set[str]] = {}
    duplicate_player_keys: list[dict[str, str]] = []

    for side in _list(payload.get("Stats")):
        player_rows = [
            _mapping(player) for player in _list(_mapping(side).get("PlayersStats"))
        ]
        if not player_rows:
            raise RotationAuditError("Box score contains a side without PlayersStats")
        team_id = _text(player_rows[0].get("Team"))
        if not team_id:
            raise RotationAuditError("Box-score player rows do not contain a team code")
        players: dict[str, dict[str, Any]] = {}
        starters: set[str] = set()
        for player in player_rows:
            player_id = _text(player.get("Player_ID"))
            if not player_id:
                raise RotationAuditError(f"{team_id} box score contains a blank player ID")
            if player_id in players:
                duplicate_player_keys.append({"team_id": team_id, "player_id": player_id})
            minutes_raw = _text(player.get("Minutes"))
            players[player_id] = {
                "minutes_seconds": parse_boxscore_seconds(minutes_raw),
                "minutes_raw": minutes_raw,
                "did_not_play": minutes_raw.upper() == "DNP",
                "is_starter": player.get("IsStarter") in (1, True, "1", "true", "True"),
            }
            if players[player_id]["is_starter"]:
                starters.add(player_id)
        players_by_team[team_id] = players
        starters_by_team[team_id] = starters

    if len(players_by_team) != 2:
        raise RotationAuditError(
            f"Expected two box-score teams, found {len(players_by_team)}"
        )
    return {
        "players_by_team": players_by_team,
        "starters_by_team": starters_by_team,
        "duplicate_player_keys": duplicate_player_keys,
    }


def _event_number(event: Mapping[str, Any]) -> int | None:
    try:
        return int(event.get("NUMBEROFPLAY"))
    except (TypeError, ValueError):
        return None


def _canonical_event(event: Mapping[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _transaction_metrics(
    periods: Sequence[tuple[int, str, int, list[Mapping[str, Any]]]],
    official_players: set[str],
    dnp_players: set[str],
) -> tuple[dict[str, Any], dict[tuple[int, int, str], list[tuple[int, Mapping[str, Any]]]]]:
    groups: dict[
        tuple[int, int, str], list[tuple[int, Mapping[str, Any]]]
    ] = defaultdict(list)
    missing_fields = 0
    non_boxscore_players = 0
    dnp_substitutions = 0

    for period_number, _, _, events in periods:
        for source_index, event in enumerate(events):
            play_type = _text(event.get("PLAYTYPE"))
            if play_type not in SUBSTITUTION_TYPES:
                continue
            team_id = _text(event.get("CODETEAM"))
            player_id = _text(event.get("PLAYER_ID"))
            try:
                clock = parse_clock_seconds(event.get("MARKERTIME"))
            except RotationAuditError:
                clock = -1
            if not team_id or not player_id or clock < 0:
                missing_fields += 1
            if player_id and player_id not in official_players:
                non_boxscore_players += 1
            if player_id in dnp_players:
                dnp_substitutions += 1
            groups[(period_number, clock, team_id)].append((source_index, event))

    pair_sizes: Counter[int] = Counter()
    balanced = 0
    contiguous = 0
    quarter_start = 0
    zero_clock = 0
    for (period_number, clock, _), entries in groups.items():
        del period_number
        ins = sum(_text(event.get("PLAYTYPE")) == "IN" for _, event in entries)
        outs = sum(_text(event.get("PLAYTYPE")) == "OUT" for _, event in entries)
        if ins == outs:
            balanced += 1
        pair_sizes[ins] += 1
        indices = [index for index, _ in entries]
        if indices and max(indices) - min(indices) + 1 == len(indices):
            contiguous += 1
        quarter_start += int(clock == 600)
        zero_clock += int(clock == 0)

    metrics = {
        "substitution_events": sum(len(entries) for entries in groups.values()),
        "in_events": sum(
            _text(event.get("PLAYTYPE")) == "IN"
            for entries in groups.values()
            for _, event in entries
        ),
        "out_events": sum(
            _text(event.get("PLAYTYPE")) == "OUT"
            for entries in groups.values()
            for _, event in entries
        ),
        "transaction_groups": len(groups),
        "balanced_transaction_groups": balanced,
        "unbalanced_transaction_groups": len(groups) - balanced,
        "pair_size_distribution": {
            str(size): count for size, count in sorted(pair_sizes.items())
        },
        "multi_player_transaction_groups": sum(
            count for size, count in pair_sizes.items() if size > 1
        ),
        "contiguous_transaction_groups": contiguous,
        "interleaved_transaction_groups": len(groups) - contiguous,
        "quarter_start_transaction_groups": quarter_start,
        "zero_clock_transaction_groups": zero_clock,
        "missing_substitution_fields": missing_fields,
        "non_boxscore_substitution_players": non_boxscore_players,
        "dnp_substitution_events": dnp_substitutions,
    }
    return metrics, groups


def _ordering_metrics(
    periods: Sequence[tuple[int, str, int, list[Mapping[str, Any]]]],
) -> dict[str, Any]:
    event_numbers: list[int] = []
    event_number_inversions = 0
    clock_increases = 0
    substitution_clock_increases = 0
    canonical_events: list[str] = []

    for _, _, _, events in periods:
        period_numbers = [number for event in events if (number := _event_number(event)) is not None]
        event_numbers.extend(period_numbers)
        event_number_inversions += sum(
            later < earlier
            for earlier, later in zip(period_numbers, period_numbers[1:])
        )

        timed_clocks = [
            parse_clock_seconds(event.get("MARKERTIME"))
            for event in events
            if _text(event.get("MARKERTIME"))
        ]
        clock_increases += sum(
            later > earlier
            for earlier, later in zip(timed_clocks, timed_clocks[1:])
        )
        substitution_clocks = [
            parse_clock_seconds(event.get("MARKERTIME"))
            for event in events
            if _text(event.get("PLAYTYPE")) in SUBSTITUTION_TYPES
            and _text(event.get("MARKERTIME"))
        ]
        substitution_clock_increases += sum(
            later > earlier
            for earlier, later in zip(
                substitution_clocks, substitution_clocks[1:]
            )
        )
        canonical_events.extend(_canonical_event(event) for event in events)

    number_counts = Counter(event_numbers)
    duplicate_numbers = sum(count - 1 for count in number_counts.values() if count > 1)
    event_number_gaps = 0
    if event_numbers:
        event_number_gaps = max(event_numbers) - min(event_numbers) + 1 - len(set(event_numbers))
    canonical_counts = Counter(canonical_events)
    duplicate_events = sum(
        count - 1 for count in canonical_counts.values() if count > 1
    )
    return {
        "duplicate_event_numbers": duplicate_numbers,
        "event_number_gaps": event_number_gaps,
        "event_number_inversions": event_number_inversions,
        "fully_duplicated_events": duplicate_events,
        "clock_increases_in_source_order": clock_increases,
        "substitution_clock_increases_in_source_order": substitution_clock_increases,
    }


def _timeout_metrics(
    periods: Sequence[tuple[int, str, int, list[Mapping[str, Any]]]],
) -> dict[str, int]:
    timeouts = 0
    with_any_substitution = 0
    with_same_team_substitution = 0
    directly_adjacent = 0

    for _, _, _, events in periods:
        for source_index, event in enumerate(events):
            if _text(event.get("PLAYTYPE")) not in TIMEOUT_TYPES:
                continue
            timeouts += 1
            clock = _text(event.get("MARKERTIME"))
            team_id = _text(event.get("CODETEAM"))
            same_clock_subs = [
                candidate
                for candidate in events
                if _text(candidate.get("MARKERTIME")) == clock
                and _text(candidate.get("PLAYTYPE")) in SUBSTITUTION_TYPES
            ]
            with_any_substitution += int(bool(same_clock_subs))
            with_same_team_substitution += int(
                any(_text(candidate.get("CODETEAM")) == team_id for candidate in same_clock_subs)
            )
            neighbours = (
                events[index]
                for index in (source_index - 1, source_index + 1)
                if 0 <= index < len(events)
            )
            directly_adjacent += int(
                any(
                    _text(candidate.get("MARKERTIME")) == clock
                    and _text(candidate.get("PLAYTYPE")) in SUBSTITUTION_TYPES
                    for candidate in neighbours
                )
            )
    return {
        "timeout_events": timeouts,
        "timeouts_with_any_substitution_at_same_clock": with_any_substitution,
        "timeouts_with_same_team_substitution_at_same_clock": with_same_team_substitution,
        "timeouts_directly_adjacent_to_substitution": directly_adjacent,
    }


def _actor_boundary_metrics(
    periods: Sequence[tuple[int, str, int, list[Mapping[str, Any]]]],
    starters_by_team: Mapping[str, set[str]],
    official_players: set[str],
) -> dict[str, Any]:
    state = {team_id: set(starters) for team_id, starters in starters_by_team.items()}
    real_actor_events = 0
    pseudo_actor_events = 0
    pseudo_actor_ids: set[str] = set()
    boundary_mismatches = 0

    for _, _, _, events in periods:
        clocks = sorted(
            {
                parse_clock_seconds(event.get("MARKERTIME"))
                for event in events
                if _text(event.get("MARKERTIME"))
            },
            reverse=True,
        )
        for clock in clocks:
            at_clock = [
                event
                for event in events
                if _text(event.get("MARKERTIME"))
                and parse_clock_seconds(event.get("MARKERTIME")) == clock
            ]
            before = {team_id: set(players) for team_id, players in state.items()}
            for team_id in state:
                outs = {
                    _text(event.get("PLAYER_ID"))
                    for event in at_clock
                    if _text(event.get("PLAYTYPE")) == "OUT"
                    and _text(event.get("CODETEAM")) == team_id
                }
                ins = {
                    _text(event.get("PLAYER_ID"))
                    for event in at_clock
                    if _text(event.get("PLAYTYPE")) == "IN"
                    and _text(event.get("CODETEAM")) == team_id
                }
                state[team_id].difference_update(outs)
                state[team_id].update(ins)
            after = {team_id: set(players) for team_id, players in state.items()}

            for event in at_clock:
                if _text(event.get("PLAYTYPE")) in SUBSTITUTION_TYPES:
                    continue
                player_id = _text(event.get("PLAYER_ID"))
                if not player_id:
                    continue
                team_id = _text(event.get("CODETEAM"))
                if player_id not in official_players:
                    pseudo_actor_events += 1
                    pseudo_actor_ids.add(player_id)
                    continue
                real_actor_events += 1
                if player_id not in before.get(team_id, set()) and player_id not in after.get(
                    team_id, set()
                ):
                    boundary_mismatches += 1

    return {
        "real_player_tagged_non_substitution_events": real_actor_events,
        "pseudo_actor_events": pseudo_actor_events,
        "pseudo_actor_ids": sorted(pseudo_actor_ids),
        "actor_outside_pre_and_post_lineup": boundary_mismatches,
    }


def audit_game(
    game_code: int,
    boxscore: Mapping[str, Any],
    play_by_play: Mapping[str, Any],
    *,
    header: Mapping[str, Any] | None = None,
    season_code: str | None = None,
) -> dict[str, Any]:
    """Audit one game and return only JSON-serializable evidence."""

    periods = _periods(play_by_play)
    if not periods:
        raise RotationAuditError(f"Game {game_code} has no play-by-play periods")
    context = _boxscore_context(boxscore)
    header_payload = _mapping(header)
    observed_season_code = season_code or _text(
        header_payload.get("CompetitionReducedName")
    )
    players_by_team: dict[str, dict[str, dict[str, Any]]] = context["players_by_team"]
    starters_by_team: dict[str, set[str]] = context["starters_by_team"]
    official_players = {
        player_id for players in players_by_team.values() for player_id in players
    }
    dnp_players = {
        player_id
        for players in players_by_team.values()
        for player_id, player in players.items()
        if player["did_not_play"]
    }

    transaction_metrics, transaction_groups = _transaction_metrics(
        periods, official_players, dnp_players
    )
    ordering = _ordering_metrics(periods)
    timeouts = _timeout_metrics(periods)

    state = {team_id: set(starters) for team_id, starters in starters_by_team.items()}
    reconstructed: dict[str, Counter[str]] = {
        team_id: Counter() for team_id in players_by_team
    }
    invalid_out_events = 0
    invalid_in_events = 0
    settled_non_five_transactions = 0
    unsettled_period_ends = 0
    events_with_transient_non_five = 0
    non_sub_events_during_transient_non_five = 0
    raw_sequence_actor_mismatches = 0
    last_index_by_group = {
        key: max(index for index, _ in entries)
        for key, entries in transaction_groups.items()
    }

    for period_number, _, period_length, events in periods:
        previous_clock = {team_id: period_length for team_id in state}
        for source_index, event in enumerate(events):
            play_type = _text(event.get("PLAYTYPE"))
            team_id = _text(event.get("CODETEAM"))
            player_id = _text(event.get("PLAYER_ID"))
            clock_text = _text(event.get("MARKERTIME"))

            if play_type in SUBSTITUTION_TYPES and team_id in state and clock_text:
                clock = parse_clock_seconds(clock_text)
                if clock < previous_clock[team_id]:
                    if len(state[team_id]) != 5:
                        unsettled_period_ends += 1
                    elapsed = previous_clock[team_id] - clock
                    for active_player in state[team_id]:
                        reconstructed[team_id][active_player] += elapsed
                    previous_clock[team_id] = clock

                if play_type == "OUT":
                    if player_id not in state[team_id]:
                        invalid_out_events += 1
                    state[team_id].discard(player_id)
                else:
                    if player_id in state[team_id]:
                        invalid_in_events += 1
                    state[team_id].add(player_id)

                group_key = (period_number, clock, team_id)
                if source_index == last_index_by_group.get(group_key) and len(state[team_id]) != 5:
                    settled_non_five_transactions += 1
            elif player_id and player_id in official_players:
                if player_id not in state.get(team_id, set()):
                    raw_sequence_actor_mismatches += 1

            transient = any(len(players) != 5 for players in state.values())
            events_with_transient_non_five += int(transient)
            if transient and play_type not in SUBSTITUTION_TYPES:
                non_sub_events_during_transient_non_five += 1

        for team_id in state:
            if len(state[team_id]) != 5:
                unsettled_period_ends += 1
            for active_player in state[team_id]:
                reconstructed[team_id][active_player] += previous_clock[team_id]

    minute_discrepancies: list[dict[str, Any]] = []
    official_total_seconds = 0
    reconstructed_total_seconds = 0
    exact_player_rows = 0
    team_minute_checks: list[dict[str, Any]] = []
    for team_id, players in players_by_team.items():
        official_team_seconds = sum(
            player["minutes_seconds"] for player in players.values()
        )
        reconstructed_team_seconds = sum(reconstructed[team_id].values())
        expected_team_seconds = sum(period_length for _, _, period_length, _ in periods) * 5
        official_total_seconds += official_team_seconds
        reconstructed_total_seconds += reconstructed_team_seconds
        team_minute_checks.append(
            {
                "team_id": team_id,
                "official_player_seconds": official_team_seconds,
                "reconstructed_player_seconds": reconstructed_team_seconds,
                "expected_player_seconds": expected_team_seconds,
                "difference_seconds": reconstructed_team_seconds - official_team_seconds,
            }
        )
        for player_id in sorted(set(players) | set(reconstructed[team_id])):
            official_seconds = players.get(player_id, {}).get("minutes_seconds", 0)
            reconstructed_seconds = reconstructed[team_id].get(player_id, 0)
            difference = reconstructed_seconds - official_seconds
            if difference == 0:
                exact_player_rows += 1
            else:
                minute_discrepancies.append(
                    {
                        "team_id": team_id,
                        "player_id": player_id,
                        "official_seconds": official_seconds,
                        "reconstructed_seconds": reconstructed_seconds,
                        "difference_seconds": difference,
                    }
                )

    all_events = [event for _, _, _, events in periods for event in events]
    period_markers = {
        "begin_period_events": sum(
            _text(event.get("PLAYTYPE")) == PERIOD_BEGIN for event in all_events
        ),
        "end_period_events": sum(
            _text(event.get("PLAYTYPE")) == PERIOD_END for event in all_events
        ),
        "end_game_events": sum(
            _text(event.get("PLAYTYPE")) == GAME_END for event in all_events
        ),
    }
    actor_metrics = _actor_boundary_metrics(
        periods, starters_by_team, official_players
    )
    actor_metrics["raw_sequence_actor_mismatches"] = raw_sequence_actor_mismatches

    starter_counts = {
        team_id: len(starters) for team_id, starters in starters_by_team.items()
    }
    boxscore_overtime_fields = {
        key
        for team_periods in _list(boxscore.get("ByQuarter"))
        for key in _mapping(team_periods)
        if re.fullmatch(r"Extra\d+", str(key))
    }
    boxscore_overtime_periods = max(
        (int(str(key).removeprefix("Extra")) for key in boxscore_overtime_fields),
        default=0,
    )
    header_game_seconds: int | None = None
    if _text(header_payload.get("GameTime")):
        header_game_seconds = parse_boxscore_seconds(header_payload.get("GameTime"))
    expected_game_seconds = sum(period_length for _, _, period_length, _ in periods)
    header_checks = {
        "header_available": bool(header_payload),
        "header_game_seconds": header_game_seconds,
        "pbp_period_seconds": expected_game_seconds,
        "game_time_matches_pbp_periods": header_game_seconds is None
        or header_game_seconds == expected_game_seconds,
        "boxscore_overtime_periods": boxscore_overtime_periods,
        "pbp_overtime_periods": sum(
            period_number > 4 for period_number, _, _, _ in periods
        ),
    }
    header_checks["boxscore_overtime_periods_match_pbp"] = (
        header_checks["boxscore_overtime_periods"]
        == header_checks["pbp_overtime_periods"]
    )
    distinct_lineup_boundaries = {
        (period_number, clock)
        for period_number, clock, _ in transaction_groups
    }
    game_invariants = {
        "exactly_five_starters_per_team": all(count == 5 for count in starter_counts.values()),
        "balanced_substitution_transactions": transaction_metrics[
            "unbalanced_transaction_groups"
        ]
        == 0,
        "valid_substitution_membership": invalid_out_events == 0
        and invalid_in_events == 0,
        "settled_lineups_have_five_players": settled_non_five_transactions == 0
        and unsettled_period_ends == 0,
        "all_player_minutes_match_exactly": not minute_discrepancies,
        "team_seconds_match_expected": all(
            check["official_player_seconds"] == check["expected_player_seconds"]
            and check["reconstructed_player_seconds"] == check["expected_player_seconds"]
            for check in team_minute_checks
        ),
        "substitution_fields_complete": transaction_metrics[
            "missing_substitution_fields"
        ]
        == 0,
        "substitution_players_are_in_boxscore": transaction_metrics[
            "non_boxscore_substitution_players"
        ]
        == 0,
        "dnp_players_never_substituted": transaction_metrics["dnp_substitution_events"]
        == 0,
        "real_actors_in_pre_or_post_lineup": actor_metrics[
            "actor_outside_pre_and_post_lineup"
        ]
        == 0,
        "header_game_time_matches_pbp_periods": header_checks[
            "game_time_matches_pbp_periods"
        ],
        "boxscore_overtime_periods_match_pbp": header_checks[
            "boxscore_overtime_periods_match_pbp"
        ],
    }
    absolute_minute_errors = [
        abs(int(discrepancy["difference_seconds"]))
        for discrepancy in minute_discrepancies
    ]

    return {
        "season_code": observed_season_code,
        "game_code": game_code,
        "period_count": len(periods),
        "overtime_periods": sum(period_number > 4 for period_number, _, _, _ in periods),
        "event_count": len(all_events),
        "boxscore_player_rows": sum(len(players) for players in players_by_team.values()),
        "participating_player_rows": sum(
            not player["did_not_play"]
            for players in players_by_team.values()
            for player in players.values()
        ),
        "dnp_player_rows": len(dnp_players),
        "starter_counts": starter_counts,
        "period_markers": period_markers,
        "header_checks": header_checks,
        "transactions": transaction_metrics,
        "ordering": ordering,
        "timeouts": timeouts,
        "actors": actor_metrics,
        "invalid_out_events": invalid_out_events,
        "invalid_in_events": invalid_in_events,
        "settled_non_five_transactions": settled_non_five_transactions,
        "unsettled_period_ends": unsettled_period_ends,
        "events_with_transient_non_five_after_event": events_with_transient_non_five,
        "non_substitution_events_during_transient_non_five": non_sub_events_during_transient_non_five,
        "combined_lineup_segments": len(distinct_lineup_boundaries) + 1,
        "exact_player_minute_rows": exact_player_rows,
        "minute_discrepancies": minute_discrepancies,
        "maximum_absolute_player_minute_error_seconds": max(
            absolute_minute_errors, default=0
        ),
        "total_absolute_player_minute_error_seconds": sum(absolute_minute_errors),
        "official_total_player_seconds": official_total_seconds,
        "reconstructed_total_player_seconds": reconstructed_total_seconds,
        "team_minute_checks": team_minute_checks,
        "duplicate_boxscore_player_keys": context["duplicate_player_keys"],
        "invariants": game_invariants,
    }


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationAuditError(f"Could not read {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RotationAuditError(f"Expected a JSON object in {path}")
    return payload


def _csv_counts(path: Path, game_code_field: str = "game_code") -> tuple[int, Counter[int], set[str]]:
    if not path.exists():
        raise RotationAuditError(f"Missing normalized fixture: {path}")
    counts: Counter[int] = Counter()
    fields: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields.update(reader.fieldnames or [])
        total = 0
        for row in reader:
            total += 1
            try:
                counts[int(row[game_code_field])] += 1
            except (KeyError, TypeError, ValueError) as exc:
                raise RotationAuditError(
                    f"Invalid {game_code_field} in {path}"
                ) from exc
    return total, counts, fields


def _sum_nested(games: Sequence[Mapping[str, Any]], section: str, field: str) -> int:
    return sum(int(_mapping(game.get(section)).get(field, 0)) for game in games)


def _audit_raw_directory(raw_directory: Path) -> list[dict[str, Any]]:
    """Audit every Boxscore/PlaybyPlay pair in one raw-fixture directory."""

    pattern = re.compile(r"game_(\d+)_boxscore\.json$")
    game_codes = sorted(
        int(match.group(1))
        for path in raw_directory.glob("game_*_boxscore.json")
        if (match := pattern.match(path.name))
        and (raw_directory / f"game_{match.group(1)}_play_by_play.json").exists()
    )
    if not game_codes:
        raise RotationAuditError(f"No paired raw fixtures found in {raw_directory}")

    games: list[dict[str, Any]] = []
    for game_code in game_codes:
        header_path = raw_directory / f"game_{game_code}_header.json"
        header = _read_json(header_path) if header_path.exists() else {}
        games.append(
            audit_game(
            game_code,
            _read_json(raw_directory / f"game_{game_code}_boxscore.json"),
            _read_json(raw_directory / f"game_{game_code}_play_by_play.json"),
                header=header,
                season_code=_text(header.get("CompetitionReducedName")) or None,
            )
        )
    return games


def _file_provenance(path: Path, source_url: str) -> dict[str, Any]:
    raw_bytes = path.read_bytes()
    try:
        relative_path = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative_path = str(path)
    return {
        "relative_path": relative_path,
        "source_url": source_url,
        "bytes": len(raw_bytes),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def audit_sample(
    sample_directory: Path = DEFAULT_SAMPLE_DIRECTORY,
    overtime_directory: Path = DEFAULT_OVERTIME_DIRECTORY,
) -> dict[str, Any]:
    """Audit the retained regulation sample and bounded real overtime probe."""

    regulation_games = _audit_raw_directory(sample_directory / "raw")
    overtime_games = _audit_raw_directory(overtime_directory)
    games = regulation_games + overtime_games

    normalized_pbp_total, normalized_pbp_by_game, pbp_fields = _csv_counts(
        sample_directory / "play_by_play.csv"
    )
    normalized_player_total, normalized_player_by_game, _ = _csv_counts(
        sample_directory / "player_boxscores.csv"
    )
    raw_pbp_by_game = Counter(
        {
            int(game["game_code"]): int(game["event_count"])
            for game in regulation_games
        }
    )
    raw_player_by_game = Counter(
        {
            int(game["game_code"]): int(game["boxscore_player_rows"])
            for game in regulation_games
        }
    )
    fixture_alignment = {
        "normalized_scope_game_identifiers": [
            f"{game['season_code']}_{game['game_code']}" for game in regulation_games
        ],
        "raw_only_scope_game_identifiers": [
            f"{game['season_code']}_{game['game_code']}" for game in overtime_games
        ],
        "normalized_play_by_play_rows": normalized_pbp_total,
        "raw_play_by_play_events": sum(raw_pbp_by_game.values()),
        "play_by_play_counts_match_by_game": normalized_pbp_by_game == raw_pbp_by_game,
        "normalized_player_boxscore_rows": normalized_player_total,
        "raw_player_boxscore_rows": sum(raw_player_by_game.values()),
        "player_boxscore_counts_match_by_game": normalized_player_by_game
        == raw_player_by_game,
        "normalized_has_source_sequence": "source_sequence" in pbp_fields,
        "normalized_has_source_period": "source_period" in pbp_fields,
    }

    pair_size_distribution: Counter[int] = Counter()
    for game in games:
        pair_size_distribution.update(
            {
                int(size): count
                for size, count in _mapping(
                    _mapping(game.get("transactions")).get("pair_size_distribution")
                ).items()
            }
        )

    totals = {
        "games": len(games),
        "team_games": len(games) * 2,
        "regulation_games": sum(not game["overtime_periods"] for game in games),
        "overtime_games": sum(bool(game["overtime_periods"]) for game in games),
        "play_by_play_events": sum(int(game["event_count"]) for game in games),
        "boxscore_player_rows": sum(int(game["boxscore_player_rows"]) for game in games),
        "participating_player_rows": sum(
            int(game["participating_player_rows"]) for game in games
        ),
        "dnp_player_rows": sum(int(game["dnp_player_rows"]) for game in games),
        "substitution_events": _sum_nested(games, "transactions", "substitution_events"),
        "in_events": _sum_nested(games, "transactions", "in_events"),
        "out_events": _sum_nested(games, "transactions", "out_events"),
        "transaction_groups": _sum_nested(games, "transactions", "transaction_groups"),
        "unbalanced_transaction_groups": _sum_nested(
            games, "transactions", "unbalanced_transaction_groups"
        ),
        "multi_player_transaction_groups": _sum_nested(
            games, "transactions", "multi_player_transaction_groups"
        ),
        "contiguous_transaction_groups": _sum_nested(
            games, "transactions", "contiguous_transaction_groups"
        ),
        "interleaved_transaction_groups": _sum_nested(
            games, "transactions", "interleaved_transaction_groups"
        ),
        "quarter_start_transaction_groups": _sum_nested(
            games, "transactions", "quarter_start_transaction_groups"
        ),
        "zero_clock_transaction_groups": _sum_nested(
            games, "transactions", "zero_clock_transaction_groups"
        ),
        "missing_substitution_fields": _sum_nested(
            games, "transactions", "missing_substitution_fields"
        ),
        "non_boxscore_substitution_players": _sum_nested(
            games, "transactions", "non_boxscore_substitution_players"
        ),
        "dnp_substitution_events": _sum_nested(
            games, "transactions", "dnp_substitution_events"
        ),
        "pair_size_distribution": {
            str(size): count for size, count in sorted(pair_size_distribution.items())
        },
        "timeout_events": _sum_nested(games, "timeouts", "timeout_events"),
        "timeouts_with_any_substitution_at_same_clock": _sum_nested(
            games, "timeouts", "timeouts_with_any_substitution_at_same_clock"
        ),
        "timeouts_with_same_team_substitution_at_same_clock": _sum_nested(
            games, "timeouts", "timeouts_with_same_team_substitution_at_same_clock"
        ),
        "timeouts_directly_adjacent_to_substitution": _sum_nested(
            games, "timeouts", "timeouts_directly_adjacent_to_substitution"
        ),
        "begin_period_events": _sum_nested(games, "period_markers", "begin_period_events"),
        "end_period_events": _sum_nested(games, "period_markers", "end_period_events"),
        "end_game_events": _sum_nested(games, "period_markers", "end_game_events"),
        "duplicate_event_numbers": _sum_nested(games, "ordering", "duplicate_event_numbers"),
        "event_number_gaps": _sum_nested(games, "ordering", "event_number_gaps"),
        "event_number_inversions": _sum_nested(
            games, "ordering", "event_number_inversions"
        ),
        "fully_duplicated_events": _sum_nested(
            games, "ordering", "fully_duplicated_events"
        ),
        "clock_increases_in_source_order": _sum_nested(
            games, "ordering", "clock_increases_in_source_order"
        ),
        "substitution_clock_increases_in_source_order": _sum_nested(
            games, "ordering", "substitution_clock_increases_in_source_order"
        ),
        "events_with_transient_non_five_after_event": sum(
            int(game["events_with_transient_non_five_after_event"]) for game in games
        ),
        "non_substitution_events_during_transient_non_five": sum(
            int(game["non_substitution_events_during_transient_non_five"])
            for game in games
        ),
        "invalid_out_events": sum(int(game["invalid_out_events"]) for game in games),
        "invalid_in_events": sum(int(game["invalid_in_events"]) for game in games),
        "settled_non_five_transactions": sum(
            int(game["settled_non_five_transactions"]) for game in games
        ),
        "unsettled_period_ends": sum(
            int(game["unsettled_period_ends"]) for game in games
        ),
        "real_player_tagged_non_substitution_events": _sum_nested(
            games, "actors", "real_player_tagged_non_substitution_events"
        ),
        "pseudo_actor_events": _sum_nested(games, "actors", "pseudo_actor_events"),
        "actor_outside_pre_and_post_lineup": _sum_nested(
            games, "actors", "actor_outside_pre_and_post_lineup"
        ),
        "raw_sequence_actor_mismatches": _sum_nested(
            games, "actors", "raw_sequence_actor_mismatches"
        ),
        "combined_lineup_segments": sum(
            int(game["combined_lineup_segments"]) for game in games
        ),
        "exact_player_minute_rows": sum(
            int(game["exact_player_minute_rows"]) for game in games
        ),
        "minute_discrepancy_rows": sum(
            len(_list(game.get("minute_discrepancies"))) for game in games
        ),
        "maximum_absolute_player_minute_error_seconds": max(
            int(game["maximum_absolute_player_minute_error_seconds"])
            for game in games
        ),
        "total_absolute_player_minute_error_seconds": sum(
            int(game["total_absolute_player_minute_error_seconds"])
            for game in games
        ),
        "official_total_player_seconds": sum(
            int(game["official_total_player_seconds"]) for game in games
        ),
        "reconstructed_total_player_seconds": sum(
            int(game["reconstructed_total_player_seconds"]) for game in games
        ),
    }

    invariant_names = sorted(
        {
            name
            for game in games
            for name in _mapping(game.get("invariants"))
        }
    )
    core_invariants = {
        name: all(bool(_mapping(game.get("invariants")).get(name)) for game in games)
        for name in invariant_names
    }
    core_invariants["raw_and_normalized_fixture_counts_match"] = all(
        (
            fixture_alignment["play_by_play_counts_match_by_game"],
            fixture_alignment["player_boxscore_counts_match_by_game"],
            fixture_alignment["normalized_has_source_sequence"],
            fixture_alignment["normalized_has_source_period"],
        )
    )
    core_passed = all(core_invariants.values())
    structural_invariants = {
        name: passed
        for name, passed in core_invariants.items()
        if name != "all_player_minutes_match_exactly"
    }
    structural_passed = all(structural_invariants.values())

    overtime_provenance = [
        _file_provenance(overtime_directory / filename, source_url)
        for filename, source_url in OVERTIME_PROBE_SOURCE_URLS.items()
    ]
    game_identifiers = [
        f"{game['season_code']}_{game['game_code']}" for game in games
    ]

    return {
        "audit_version": 2,
        "verdict": "POSSIBLE WITH CAVEATS" if structural_passed else "UNRELIABLE",
        "retained_sample_assessment": (
            "RELIABLE"
            if core_passed
            else "POSSIBLE WITH CAVEATS"
            if structural_passed
            else "UNRELIABLE"
        ),
        "scope": {
            "competition": "EuroLeague",
            "season_codes": sorted(
                {str(game["season_code"]) for game in games if game["season_code"]}
            ),
            "game_identifiers": game_identifiers,
            "fixture_sets": [
                {
                    "relative_directory": "data/samples/e2025_rounds_1_2",
                    "role": "three regulation games with raw and normalized fixtures",
                    "game_identifiers": [
                        f"{game['season_code']}_{game['game_code']}"
                        for game in regulation_games
                    ],
                },
                {
                    "relative_directory": "data/samples/rotation_overtime_probe",
                    "role": "bounded official four-overtime raw fixture",
                    "game_identifiers": [
                        f"{game['season_code']}_{game['game_code']}"
                        for game in overtime_games
                    ],
                },
            ],
            "network_calls": 0,
            "coverage_note": (
                "Three E2025 regulation games and the E2023/170 four-overtime game; "
                "this is a fixture audit, not an exhaustive historical guarantee."
            ),
        },
        "provenance": {
            "overtime_probe": {
                "season_code": "E2023",
                "game_code": 170,
                "files": overtime_provenance,
            }
        },
        "totals": totals,
        "fixture_alignment": fixture_alignment,
        "core_invariants": core_invariants,
        "core_invariants_passed": core_passed,
        "structural_invariants": structural_invariants,
        "structural_invariants_passed": structural_passed,
        "games": games,
        "known_caveats": [
            "Only four games across E2023 and E2025 are retained in the audited sample.",
            "The real four-overtime fixture has four player-minute discrepancies of exactly 60 seconds even though both team totals and every structural lineup invariant reconcile.",
            "Event numbers and full event clocks are not monotonic enough to define source order.",
            "Same-clock substitution transactions can be interleaved with free throws, opponent substitutions, or timeouts.",
            "A small number of same-clock statistical events are recorded after the credited player has been substituted out.",
            "Older seasons require a stratified integrity audit before historical lineups are treated as production data.",
        ],
        "recommended_ordering_algorithm": [
            "Order API period containers explicitly: FirstQuarter, SecondQuarter, ThirdQuarter, ForthQuarter, then each overtime period.",
            "Initialize Quarter 1 from the five box-score starters and carry the ending lineup into later periods.",
            "Preserve source_sequence and source_period; never sort by NUMBEROFPLAY.",
            "Accrue stint time from decreasing remaining clocks: 600 seconds in regulation and 300 seconds in overtime.",
            "Process equal-clock IN/OUT events in source order and publish a lineup only when the pending team state returns to five.",
            "Use explicit substitutions rather than timeout events as lineup boundaries.",
            "Retain lineup_before and lineup_after for events at a substitution clock and flag ambiguous same-clock attribution.",
            "Quarantine games that fail membership, five-player-state, clock-order, or official-minute reconciliation checks.",
        ],
    }


def write_summary(summary: Mapping[str, Any], output: Path = DEFAULT_OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconstruct retained EuroLeague rotations and audit core invariants."
    )
    parser.add_argument(
        "--sample-directory",
        type=Path,
        default=DEFAULT_SAMPLE_DIRECTORY,
        help="Directory containing raw fixtures and normalized CSVs",
    )
    parser.add_argument(
        "--overtime-directory",
        type=Path,
        default=DEFAULT_OVERTIME_DIRECTORY,
        help="Directory containing the bounded raw overtime fixture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Machine-readable JSON summary path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = audit_sample(args.sample_directory, args.overtime_directory)
        write_summary(summary, args.output)
    except RotationAuditError as exc:
        print(f"Rotation audit failed: {exc}")
        return 2

    totals = _mapping(summary.get("totals"))
    print(
        f"{summary['verdict']}: {totals.get('games')} games, "
        f"{totals.get('transaction_groups')} substitution transactions, "
        f"{totals.get('exact_player_minute_rows')}/"
        f"{totals.get('boxscore_player_rows')} exact player-minute rows."
    )
    print(f"Wrote {args.output}")
    return 0 if summary["core_invariants_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
