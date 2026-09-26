"""Pure analysis helpers for sanitized Fantasy market responses."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .fantasy_security import contains_sensitive_artifact_value
from .normalizers import schema_inventory


USEFUL_FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "fantasy_player_id": ("id", "player_id"),
    "official_player_id_candidate": (
        "euroleague_id",
        "external_id",
        "official_id",
        "player_code",
    ),
    "player_name": ("first_name", "last_name", "name"),
    "team": ("team", "team_id", "club", "club_id"),
    "position": ("position", "position_id"),
    "current_price": ("quotation", "price", "credits", "current_price"),
    "previous_price": ("previous_quotation", "previous_price", "old_price"),
    "price_change": ("quotation_change", "price_change", "variation"),
    "fantasy_average": ("avg_pts", "avg_fantasy_pts", "average"),
    "recent_scores": ("recent_scores", "fantasy_pts", "stats_items"),
    "availability": (
        "active",
        "availability",
        "is_injured",
        "probability_of_playing",
        "status",
    ),
    "market_state": ("is_on_trade", "market", "on_market"),
    "starter_bench": ("started_from_bench", "court_position", "is_starter"),
    "round_matchday": ("round", "matchday", "gameweek", "turn"),
    "season_competition": ("season", "competition", "league"),
    "timestamps": ("created_at", "updated_at", "timestamp", "started_at"),
}

STATUS_FIELD_TERMS = (
    "active",
    "availability",
    "injur",
    "market",
    "probability",
    "started_from_bench",
    "status",
    "trade",
)

CURRENT_POSITION_NAMES = frozenset({"Guard", "Forward", "Center", "Head Coach"})


class FantasyMarketValidationError(ValueError):
    """Raised when a live response is not a complete, safe market snapshot."""


@dataclass(frozen=True, slots=True)
class FantasyMarketValidation:
    entity_count: int
    player_count: int
    coach_count: int
    valid_credit_count: int
    team_count: int
    position_counts: dict[str, int]
    turn_numbers: tuple[int, ...]
    available_status_fields: tuple[str, ...]


def market_records(payload: Any) -> list[dict[str, Any]]:
    """Locate the market record array without assuming one envelope variant."""

    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []

    direct = payload.get("data")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, Mapping)]
    if isinstance(direct, Mapping):
        for key in ("players", "items", "rows", "data"):
            nested = direct.get(key)
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, Mapping)]
    for key in ("players", "items", "rows"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, Mapping)]
    return []


def validate_current_market_payload(
    payload: Any,
    *,
    configured_team_ids: Iterable[int],
    minimum_players: int = 100,
    minimum_teams: int = 10,
) -> FantasyMarketValidation:
    """Reject empty, malformed, duplicate, or obviously partial live markets.

    Validation completes before raw archival or database mutation. The thresholds
    are deliberately well below a normal EuroLeague market while still preventing
    an error payload or partial page from replacing a valid live snapshot.
    """

    if minimum_players <= 0 or minimum_teams <= 0:
        raise ValueError("Fantasy market validation thresholds must be positive")
    rows = market_records(payload)
    if not rows:
        raise FantasyMarketValidationError(
            "Authenticated Fantasy response contained no market records"
        )

    configured = {int(value) for value in configured_team_ids}
    if len(configured) < minimum_teams:
        raise FantasyMarketValidationError(
            "Fantasy configuration does not contain a reasonable team catalog"
        )
    fantasy_ids: list[str] = []
    market_team_ids: set[int] = set()
    position_counts: Counter[str] = Counter()
    turn_numbers: set[int] = set()
    available_status_fields: set[str] = set()
    valid_credit_count = 0

    for index, row in enumerate(rows):
        fantasy_id = str(row.get("id") or "").strip()
        if not fantasy_id:
            raise FantasyMarketValidationError(
                f"Fantasy market row {index} has no stable entity ID"
            )
        fantasy_ids.append(fantasy_id)
        if not any(str(row.get(key) or "").strip() for key in ("first_name", "last_name")):
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has no name"
            )

        position = row.get("position")
        position_name = (
            str(position.get("name") or "").strip()
            if isinstance(position, Mapping)
            else ""
        )
        if position_name not in CURRENT_POSITION_NAMES:
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has unrecognized position {position_name!r}"
            )
        position_counts[position_name] += 1

        team = row.get("team")
        if not isinstance(team, Mapping):
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has no team object"
            )
        try:
            team_id = int(team.get("id"))
        except (TypeError, ValueError) as error:
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has an invalid team ID"
            ) from error
        if team_id not in configured:
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} references team {team_id} outside the official config"
            )
        if not str(team.get("name") or "").strip():
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has an unnamed team"
            )
        market_team_ids.add(team_id)

        credit = row.get("quotation")
        if (
            isinstance(credit, bool)
            or not isinstance(credit, (int, float))
            or not math.isfinite(float(credit))
            or float(credit) <= 0
            or float(credit) > 100
        ):
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has invalid credits"
            )
        valid_credit_count += 1

        turn = row.get("round")
        if not isinstance(turn, Mapping):
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has no current Turn metadata"
            )
        try:
            turn_id = int(turn.get("id"))
            turn_number = int(turn.get("number"))
        except (TypeError, ValueError) as error:
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has invalid Turn metadata"
            ) from error
        if turn_id <= 0 or turn_number <= 0:
            raise FantasyMarketValidationError(
                f"Fantasy entity {fantasy_id} has non-positive Turn metadata"
            )
        turn_numbers.add(turn_number)

        for field in ("is_injured", "probability_of_playing", "started_from_bench"):
            if field in row and row[field] is not None:
                available_status_fields.add(field)

    duplicates = [
        fantasy_id
        for fantasy_id, count in Counter(fantasy_ids).items()
        if count > 1
    ]
    if duplicates:
        raise FantasyMarketValidationError(
            f"Fantasy market contains {len(duplicates)} duplicate entity IDs"
        )
    player_count = sum(
        position_counts[name] for name in ("Guard", "Forward", "Center")
    )
    coach_count = position_counts["Head Coach"]
    if player_count < minimum_players:
        raise FantasyMarketValidationError(
            f"Fantasy market is implausibly small ({player_count} players)"
        )
    if len(market_team_ids) < minimum_teams:
        raise FantasyMarketValidationError(
            f"Fantasy market covers only {len(market_team_ids)} teams"
        )
    missing_teams = configured - market_team_ids
    if missing_teams:
        raise FantasyMarketValidationError(
            f"Fantasy market is incomplete: {len(missing_teams)} configured teams are absent"
        )
    if coach_count not in {0, len(configured)}:
        raise FantasyMarketValidationError(
            f"Fantasy coach market is partial ({coach_count}/{len(configured)} teams)"
        )
    return FantasyMarketValidation(
        entity_count=len(rows),
        player_count=player_count,
        coach_count=coach_count,
        valid_credit_count=valid_credit_count,
        team_count=len(market_team_ids),
        position_counts=dict(sorted(position_counts.items())),
        turn_numbers=tuple(sorted(turn_numbers)),
        available_status_fields=tuple(sorted(available_status_fields)),
    )


def summarize_market_payload(payload: Any) -> dict[str, Any]:
    records = market_records(payload)
    inventory = schema_inventory(payload)
    paths = list(inventory)
    useful_paths: dict[str, list[str]] = {}
    for feature, terms in USEFUL_FIELD_TERMS.items():
        useful_paths[feature] = sorted(
            path
            for path in paths
            if any(_path_terminal(path) == term for term in terms)
        )

    return {
        "record_count": len(records),
        "top_level_fields": sorted(payload) if isinstance(payload, Mapping) else [],
        "record_fields": sorted({str(key) for record in records for key in record}),
        "useful_field_paths": useful_paths,
        "value_summary": market_value_summary(records),
        "status_value_summary": status_value_summary(records),
        "schema_inventory": inventory,
    }


def market_value_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    positions = Counter(
        str(record.get("position", {}).get("name"))
        for record in rows
        if isinstance(record.get("position"), Mapping)
    )
    rounds = Counter(
        (
            record.get("round", {}).get("id"),
            record.get("round", {}).get("number"),
        )
        for record in rows
        if isinstance(record.get("round"), Mapping)
    )
    quotations = [
        value
        for record in rows
        if isinstance((value := record.get("quotation")), (int, float))
        and not isinstance(value, bool)
    ]
    team_ids = {
        record["team"].get("id")
        for record in rows
        if isinstance(record.get("team"), Mapping)
        and record["team"].get("id") is not None
    }
    return {
        "unique_fantasy_ids": len(
            {record.get("id") for record in rows if record.get("id") is not None}
        ),
        "unique_team_ids": len(team_ids),
        "position_counts": dict(sorted(positions.items())),
        "round_counts": {
            f"id={round_id},number={number}": count
            for (round_id, number), count in sorted(rounds.items(), key=str)
        },
        "quotation": {
            "numeric_count": len(quotations),
            "null_count": sum(record.get("quotation") is None for record in rows),
            "minimum": min(quotations) if quotations else None,
            "maximum": max(quotations) if quotations else None,
        },
    }


def status_value_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    observed: dict[str, Counter[str]] = {}
    for record in records:
        for path, value in _walk_scalars(record):
            terminal = _path_terminal(path)
            if not any(term in terminal for term in STATUS_FIELD_TERMS):
                continue
            if value is None or isinstance(value, (bool, int, float, str)):
                counter = observed.setdefault(path, Counter())
                counter[_safe_scalar_label(value)] += 1
    return {
        path: dict(counter.most_common(25)) for path, counter in sorted(observed.items())
    }


def compare_market_payloads(current: Any, historical: Any) -> dict[str, Any]:
    """Compare prices/positions/status by Fantasy ID without saving whole history."""

    current_records = _records_by_id(market_records(current))
    historical_records = _records_by_id(market_records(historical))
    shared = sorted(current_records.keys() & historical_records.keys(), key=str)
    price_changes = 0
    position_changes = 0
    injured_flag_changes = 0
    probability_changes = 0
    started_from_bench_changes = 0
    average_changes = 0
    team_changes = 0
    team_id_changes = 0
    team_home_away_changes = 0
    opponent_changes = 0
    opponent_id_changes = 0
    round_changes = 0
    any_status_changes = 0
    for player_id in shared:
        current_row = current_records[player_id]
        historical_row = historical_records[player_id]
        price_changes += _first_present(current_row, ("quotation", "price", "credits")) != (
            _first_present(historical_row, ("quotation", "price", "credits"))
        )
        position_changes += _stable_json(current_row.get("position")) != _stable_json(
            historical_row.get("position")
        )
        injured_changed = current_row.get("is_injured") != historical_row.get(
            "is_injured"
        )
        probability_changed = current_row.get(
            "probability_of_playing"
        ) != historical_row.get("probability_of_playing")
        bench_changed = current_row.get("started_from_bench") != historical_row.get(
            "started_from_bench"
        )
        injured_flag_changes += injured_changed
        probability_changes += probability_changed
        started_from_bench_changes += bench_changed
        any_status_changes += injured_changed or probability_changed or bench_changed
        average_changes += current_row.get("avg_pts") != historical_row.get("avg_pts")
        team_changes += _stable_json(current_row.get("team")) != _stable_json(
            historical_row.get("team")
        )
        team_id_changes += _nested_value(current_row, "team", "id") != _nested_value(
            historical_row, "team", "id"
        )
        team_home_away_changes += _nested_value(
            current_row, "team", "position"
        ) != _nested_value(historical_row, "team", "position")
        opponent_changes += _stable_json(current_row.get("opponent")) != _stable_json(
            historical_row.get("opponent")
        )
        opponent_id_changes += _nested_value(
            current_row, "opponent", "id"
        ) != _nested_value(historical_row, "opponent", "id")
        round_changes += _stable_json(current_row.get("round")) != _stable_json(
            historical_row.get("round")
        )
    return {
        "current_records": len(current_records),
        "historical_records": len(historical_records),
        "shared_player_ids": len(shared),
        "players_with_different_price": price_changes,
        "players_with_different_position": position_changes,
        "players_with_different_is_injured": injured_flag_changes,
        "players_with_different_probability_of_playing": probability_changes,
        "players_with_different_started_from_bench": started_from_bench_changes,
        "players_with_different_injury_or_status": any_status_changes,
        "players_with_different_average_points": average_changes,
        "players_with_different_team_object": team_changes,
        "players_with_different_team_id": team_id_changes,
        "players_with_different_team_home_away": team_home_away_changes,
        "players_with_different_opponent_object": opponent_changes,
        "players_with_different_opponent_id": opponent_id_changes,
        "players_with_different_round": round_changes,
    }


def extract_config_matchdays(payload: Any) -> list[dict[str, int]]:
    data = payload.get("data", payload) if isinstance(payload, Mapping) else {}
    if not isinstance(data, Mapping) or not isinstance(data.get("matchdays"), list):
        return []
    rows: list[dict[str, int]] = []
    for item in data["matchdays"]:
        if not isinstance(item, Mapping):
            continue
        matchday_id, number = item.get("id"), item.get("number")
        if isinstance(matchday_id, int) and isinstance(number, int):
            rows.append({"id": matchday_id, "number": number})
    return sorted(rows, key=lambda row: row["number"])


def representative_historical_matchdays(
    matchdays: list[dict[str, int]], current_matchday_id: int, limit: int = 3
) -> list[dict[str, int]]:
    candidates = [row for row in matchdays if row["id"] != current_matchday_id]
    if not candidates or limit <= 0:
        return []
    desired = [candidates[-1], candidates[len(candidates) // 2], candidates[0]]
    unique: list[dict[str, int]] = []
    seen: set[int] = set()
    for row in desired:
        if row["id"] not in seen:
            unique.append(row)
            seen.add(row["id"])
        if len(unique) >= limit:
            break
    return unique


def sanitized_payload_sha256(payload: Any) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(serialized).hexdigest()


def write_sanitized_json(path: Path, payload: Any) -> None:
    if contains_sensitive_artifact_value(payload):
        raise ValueError("Refusing to write a payload that still contains sensitive data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _records_by_id(records: Iterable[Mapping[str, Any]]) -> dict[Any, Mapping[str, Any]]:
    return {
        record["id"]: record
        for record in records
        if record.get("id") is not None
    }


def _first_present(record: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _walk_scalars(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _walk_scalars(nested, child)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_scalars(nested, f"{path}[]")
    else:
        yield path, value


def _path_terminal(path: str) -> str:
    return path.rsplit(".", maxsplit=1)[-1].replace("[]", "").lower()


def _safe_scalar_label(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    text = str(value)
    return text if len(text) <= 80 else "[LONG VALUE OMITTED]"


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _nested_value(record: Mapping[str, Any], field: str, nested: str) -> Any:
    value = record.get(field)
    return value.get(nested) if isinstance(value, Mapping) else None
