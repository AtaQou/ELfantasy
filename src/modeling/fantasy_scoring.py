"""Versioned EuroLeague Fantasy player scoring.

The common formula is applied to canonical box scores for every retained season.
Only seasons whose applicable rule regime is independently established receive
the ``OFFICIAL_*`` rule status; earlier values are explicitly standardized,
counterfactual back-scores under the same formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Mapping


TARGET_RULE_VERSION = "ELFC_PLAYER_V1_STANDARDIZED_2025"
PLAYER_SCORING_RULE_SPECIFICATION = {
    "positive_one": [
        "points", "total_rebounds", "assists", "steals", "blocks",
        "fouls_drawn",
    ],
    "negative_one": [
        "turnovers", "blocks_received", "fouls_committed",
        "missed_two_point_attempts", "missed_three_point_attempts",
        "missed_free_throw_attempts",
    ],
    "team_win_bonus": "0.10 * abs(base_score)",
    "includes_overtime_statistics": True,
    "double_double_bonus": False,
    "triple_double_bonus": False,
    "excludes_game_management": [
        "captain", "bench", "substitutions", "transfers", "credits",
    ],
}
OFFICIAL_VALIDATED_SEASONS = frozenset({"E2022", "E2023", "E2024", "E2025"})

OFFICIAL_HISTORICAL_STATUS = "OFFICIAL_HISTORICAL_OUTCOME_VALIDATED"
OFFICIAL_CURRENT_STATUS = "OFFICIAL_CURRENT_RULE_AND_SAMPLES_VALIDATED"
STANDARDIZED_STATUS = "STANDARDIZED_COUNTERFACTUAL_HISTORICAL_RULE_UNVERIFIED"


def scoring_rule_fingerprint(specification: Mapping[str, object] | None = None) -> str:
    """Fingerprint player-game scoring independently of game-management rules."""

    payload = specification or PLAYER_SCORING_RULE_SPECIFICATION
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class FantasyScore:
    base_score: Decimal
    team_win_bonus: Decimal
    total: Decimal
    rule_version: str


def target_rule_status(season_code: str) -> str:
    if season_code in {"E2022", "E2023", "E2024"}:
        return OFFICIAL_HISTORICAL_STATUS
    if season_code == "E2025":
        return OFFICIAL_CURRENT_STATUS
    return STANDARDIZED_STATUS


def score_player_game(
    statistics: Mapping[str, int | float | Decimal], *, team_won: bool
) -> FantasyScore:
    """Score one player game under the verified common player formula.

    A winner receives ten percent of the absolute base score. This matters for
    negative games: a base score of -2 becomes -1.8, not -2.2.
    """

    points = _number(statistics, "points")
    rebounds = _number(statistics, "total_rebounds")
    assists = _number(statistics, "assists")
    steals = _number(statistics, "steals")
    blocks = _number(statistics, "blocks")
    fouls_drawn = _number(statistics, "fouls_drawn")
    turnovers = _number(statistics, "turnovers")
    blocks_received = _number(statistics, "blocks_received")
    fouls_committed = _number(statistics, "fouls_committed")
    two_missed = _number(statistics, "two_points_attempted") - _number(
        statistics, "two_points_made"
    )
    three_missed = _number(statistics, "three_points_attempted") - _number(
        statistics, "three_points_made"
    )
    free_throws_missed = _number(statistics, "free_throws_attempted") - _number(
        statistics, "free_throws_made"
    )
    if min(two_missed, three_missed, free_throws_missed) < 0:
        raise ValueError("Made shots cannot exceed attempted shots")
    base = (
        points
        + rebounds
        + assists
        + steals
        + blocks
        + fouls_drawn
        - turnovers
        - blocks_received
        - fouls_committed
        - two_missed
        - three_missed
        - free_throws_missed
    )
    bonus = abs(base) * Decimal("0.1") if team_won else Decimal("0")
    return FantasyScore(
        base_score=base,
        team_win_bonus=bonus,
        total=base + bonus,
        rule_version=TARGET_RULE_VERSION,
    )


def _number(values: Mapping[str, int | float | Decimal], key: str) -> Decimal:
    if key not in values or values[key] is None:
        raise ValueError(f"Missing scoring input: {key}")
    return Decimal(str(values[key]))
