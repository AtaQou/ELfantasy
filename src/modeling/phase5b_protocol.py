"""Frozen Phase 5B protocol and transparent basketball constraints.

Historical zero-minute rows are externally supplied rotation-absence scenarios.
They are never interpreted as injuries or as evidence that the system predicted
non-participation.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

import numpy as np


PHASE5B_PROTOCOL_VERSION = "phase5b_known_absence_redistribution_v1"
PHASE5B_DATASET_VERSION = "phase5b_rotation_absence_dataset_v1"
PHASE5B_RANDOM_SEED = 17
REGULATION_TEAM_MINUTES = 200.0
REGULATION_PLAYER_MAX_MINUTES = 40.0
NON_TRIVIAL_ROLE_MINUTES = 1.0
MEANINGFUL_GAIN_MINUTES = 3.0
FINAL_PHASE4B_MODEL_VERSION = "phase4b_conditional_performance_frozen_v1"
DEFAULT_RECONCILIATION = "bounded_additive_simplex_200_v1"


STATUS_DECISIONS = {
    "AVAILABLE": "PLAY",
    "PROBABLE": "PLAY",
    "OUT": "OUT",
    "SUSPENDED": "OUT",
    "NOT_REGISTERED": "OUT",
    "LIMITED": "LIMITED",
    "QUESTIONABLE": "USER_DECISION",
    "DOUBTFUL": "USER_DECISION",
    "GAME_TIME_DECISION": "USER_DECISION",
    "UNKNOWN": "USER_DECISION",
}


BASELINE_CORE_NUMERIC = (
    "season_start_year", "round_number", "career_el_games_before",
    "season_games_before", "last_1_minutes", "last_3_minutes_avg",
    "last_5_minutes_avg", "last_10_minutes_avg", "minutes_ewma",
    "minutes_trend", "season_minutes_avg_before",
    "career_minutes_avg_before", "season_start_rate_before",
    "last_5_start_rate", "season_fp_avg_before", "last_3_fp_avg",
    "last_5_fp_avg", "last_10_fp_avg", "fp_ewma",
    "season_fp_per_min_before", "last_5_fp_per_min", "points_per_min",
    "assists_per_min", "rebounds_per_min", "turnovers_per_min",
    "days_since_previous_el_game", "player_games_last_7_calendar_days_el",
    "player_minutes_last_7_calendar_days_el", "home_game", "away_game",
    "team_season_pace_before", "team_last5_pace",
    "team_season_ortg_before", "team_last5_ortg",
    "opp_season_drtg_before", "opp_last5_drtg",
)
BASELINE_ROTATION_NUMERIC = (
    "rot_games_before", "rot_last5_minutes_avg", "rot_last5_minutes_share",
    "rot_season_minutes_share_before", "rot_last5_closing_lineup_rate",
    "rot_last5_starter_rate", "rot_last5_minutes_std",
    "rot_last5_stint_count_avg", "rot_minutes_trend", "rot_minutes_ewma",
    "rot_role_stability",
)
BASELINE_CATEGORICAL = (
    "season", "home_away", "team_id", "opponent_team_id", "broad_position",
)


RECIPIENT_NUMERIC = (
    "baseline_expected_minutes", "career_el_games_before",
    "season_games_before", "last_1_minutes", "last_3_minutes_avg",
    "last_5_minutes_avg", "last_10_minutes_avg", "minutes_ewma",
    "minutes_trend", "season_minutes_avg_before", "season_start_rate_before",
    "last_5_start_rate", "season_fp_avg_before", "last_3_fp_avg",
    "last_5_fp_avg", "last_10_fp_avg", "fp_ewma",
    "season_fp_per_min_before", "last_5_fp_per_min", "points_per_min",
    "assists_per_min", "rebounds_per_min", "turnovers_per_min",
    "rot_games_before", "rot_last5_minutes_avg", "rot_last5_minutes_share",
    "rot_last5_closing_lineup_rate", "rot_last5_starter_rate",
    "rot_last5_minutes_std", "rot_minutes_trend", "rot_minutes_ewma",
    "rot_role_stability", "historical_role_rank",
    "number_players_out", "number_rotation_players_out",
    "missing_expected_minutes", "missing_rotation_share",
    "missing_starter_minutes", "missing_bench_minutes",
    "missing_recent_starts", "missing_fp_before",
    "missing_fp_per_min_before", "missing_points_rate",
    "missing_assist_rate", "missing_rebound_rate",
    "missing_ballhandling_proxy", "missing_rebounding_proxy",
    "missing_guard_minutes", "missing_forward_minutes",
    "missing_center_minutes", "available_rotation_players",
    "remaining_player_count", "remaining_starter_count",
    "remaining_guard_depth", "remaining_forward_depth",
    "remaining_center_depth", "remaining_high_role_players",
    "remaining_expected_minutes_mean", "remaining_expected_minutes_std",
    "remaining_expected_minutes_max", "remaining_role_concentration",
    "team_role_concentration_before", "team_rotation_depth_before",
    "same_position_competition", "same_position_missing_minutes",
    "historical_shared_games_with_absent", "historical_shared_minutes_with_absent",
    "prior_absence_response_games", "prior_absence_response_avg_delta",
)
RECIPIENT_ROTATION_SUPPLEMENT = (
    "rot_last5_stint_count_avg", "rot_last5_first_entry_minutes_avg",
    "rot_last5_first_sub_out_minutes_avg",
)
RECIPIENT_CATEGORICAL = (
    "season", "home_away", "team_id", "opponent_team_id",
    "broad_position", "dominant_missing_position",
)


FORBIDDEN_REDISTRIBUTION_INPUTS = frozenset({
    "actual_minutes", "actual_regulation_minutes", "minutes_delta",
    "target_minutes", "target_started", "actual_fantasy_points",
    "target_teammate_minutes", "target_teammate_fp", "target_lineup_outcome",
    "target_substitution_pattern", "player_id", "game_id", "player_game_id",
    "model_row_id", "target_game_time", "feature_cutoff_time",
    "fantasy_credits_pre_matchday", "current_fantasy_credits",
})


def baseline_manifest(include_rotation: bool = False) -> dict[str, Any]:
    numeric = [*BASELINE_CORE_NUMERIC]
    if include_rotation:
        numeric.extend(BASELINE_ROTATION_NUMERIC)
    return _manifest(
        "BASELINE_CORE_ROTATION" if include_rotation else "BASELINE_CORE",
        numeric,
        BASELINE_CATEGORICAL,
    )


def redistribution_manifest(include_rotation: bool = False) -> dict[str, Any]:
    numeric = [*RECIPIENT_NUMERIC]
    if include_rotation:
        numeric.extend(RECIPIENT_ROTATION_SUPPLEMENT)
    return _manifest(
        "REDISTRIBUTION_CORE_ROTATION" if include_rotation else "REDISTRIBUTION_CORE",
        numeric,
        RECIPIENT_CATEGORICAL,
    )


def _manifest(
    name: str,
    numeric: Iterable[str],
    categorical: Iterable[str],
) -> dict[str, Any]:
    numeric_values = list(numeric)
    categorical_values = list(categorical)
    features = [*numeric_values, *categorical_values]
    if len(features) != len(set(features)):
        raise ValueError(f"Duplicate Phase 5B features in {name}")
    validate_redistribution_feature_names(features, context=name)
    return {
        "name": name,
        "numeric": numeric_values,
        "categorical": categorical_values,
        "features": features,
        "feature_count": len(features),
        "sha256": hashlib.sha256("\n".join(features).encode()).hexdigest(),
        "includes_player_id": False,
        "includes_fantasy_credits": False,
    }


def validate_redistribution_feature_names(
    features: Iterable[str], *, context: str = "redistribution model",
) -> None:
    """Fail closed when a proposed feature list contains target-game outcomes."""

    forbidden = sorted(set(features) & FORBIDDEN_REDISTRIBUTION_INPUTS)
    if forbidden:
        raise ValueError(f"Forbidden Phase 5B inputs in {context}: {forbidden}")


def regulation_equivalent_minutes(actual_minutes: np.ndarray) -> np.ndarray:
    """Scale one historical team allocation to a 200-minute regulation budget."""

    values = np.asarray(actual_minutes, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("Actual team minutes must be one-dimensional, finite, and nonnegative")
    total = float(values.sum())
    if total <= 0:
        raise ValueError("Actual team minutes must have a positive total")
    return values * (REGULATION_TEAM_MINUTES / total)


def normalize_baseline_minutes(raw_minutes: np.ndarray) -> np.ndarray:
    """Preserve relative pre-game roles while reconciling a team to 200 minutes."""

    values = np.asarray(raw_minutes, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("Baseline minutes must be one-dimensional and finite")
    values = np.maximum(values, 0.0)
    if float(values.sum()) <= 0:
        values = np.ones_like(values)
    scaled = values * (REGULATION_TEAM_MINUTES / float(values.sum()))
    if np.all(scaled <= REGULATION_PLAYER_MAX_MINUTES + 1e-10):
        return scaled
    return capped_simplex_projection(scaled)


def capped_simplex_projection(
    raw_minutes: np.ndarray,
    *,
    total: float = REGULATION_TEAM_MINUTES,
    lower: np.ndarray | float = 0.0,
    upper: np.ndarray | float = REGULATION_PLAYER_MAX_MINUTES,
) -> np.ndarray:
    """Euclidean projection onto bounded player minutes with an exact team sum."""

    raw = np.asarray(raw_minutes, dtype=float)
    if raw.ndim != 1 or not np.all(np.isfinite(raw)):
        raise ValueError("Raw adjusted minutes must be one-dimensional and finite")
    lo = np.broadcast_to(np.asarray(lower, dtype=float), raw.shape).copy()
    hi = np.broadcast_to(np.asarray(upper, dtype=float), raw.shape).copy()
    if np.any(lo < 0) or np.any(hi > REGULATION_PLAYER_MAX_MINUTES) or np.any(lo > hi):
        raise ValueError("Player-minute bounds are invalid")
    if float(lo.sum()) > total + 1e-9 or float(hi.sum()) < total - 1e-9:
        raise ValueError("Player-minute bounds cannot satisfy the requested team total")
    low = float(np.min(lo - raw) - total)
    high = float(np.max(hi - raw) + total)
    for _ in range(100):
        shift = (low + high) / 2.0
        candidate = np.clip(raw + shift, lo, hi)
        if float(candidate.sum()) < total:
            low = shift
        else:
            high = shift
    result = np.clip(raw + (low + high) / 2.0, lo, hi)
    residual = total - float(result.sum())
    if abs(residual) > 1e-8:
        free = np.where((result > lo + 1e-10) & (result < hi - 1e-10))[0]
        if not len(free):
            raise ValueError("Projection has no free player for numerical reconciliation")
        result[free[0]] += residual
    if abs(float(result.sum()) - total) > 1e-7:
        raise ValueError("Team-minute projection did not conserve the budget")
    return result


def default_status_decision(normalized_status: str) -> str:
    try:
        return STATUS_DECISIONS[normalized_status.upper()]
    except KeyError as error:
        raise ValueError(f"Unsupported availability status: {normalized_status}") from error
