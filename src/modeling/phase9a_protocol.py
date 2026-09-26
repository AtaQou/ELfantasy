"""Frozen research contract for the Phase 9A stat-line challenger.

Phase 9A is deliberately a challenger.  Nothing in this module changes the
Phase 6C or Phase 7 contracts, and a deployment bundle may only be created by
the runner after the chronological outer gate passes.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Iterable, Mapping

from .fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint
from .ml_protocol import OUTER_FOLDS


PHASE9A_PROTOCOL_VERSION = "phase9a_statline_game_environment_v1"
PHASE9A_MODEL_VERSION = "phase9a_statline_game_environment_frozen_v1"
PHASE9A_FEATURE_VERSION = "phase9a_existing_data_pregame_v1"
PHASE9A_SIMULATION_VERSION = "phase9a_hierarchical_statline_mc_v1"
PHASE9A_RANDOM_SEED = 29
PHASE9A_SIMULATIONS = 512

PRIMARY_SEASONS = ("E2022", "E2023", "E2024", "E2025")
EVALUATION_SEASONS = ("E2023", "E2024", "E2025")
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
UPSIDE_THRESHOLDS = (20.0, 25.0, 30.0, 35.0, 40.0)
DOWNSIDE_THRESHOLDS = (5.0, 10.0, 15.0)

OPPORTUNITY_COMPONENTS = (
    "two_points_attempted",
    "three_points_attempted",
    "free_throws_attempted",
    "offensive_rebounds",
    "defensive_rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls_drawn",
    "fouls_committed",
    "blocks_received",
)
MADE_COMPONENTS = (
    "two_points_made", "three_points_made", "free_throws_made",
)
DERIVED_COMPONENTS = ("points", "total_rebounds")
STAT_COMPONENTS = (
    "minutes", *MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS,
    *DERIVED_COMPONENTS,
)

GAME_ENVIRONMENT_TARGETS = (
    "game_possessions",
    "team_points",
    "opponent_points",
    "team_fga",
    "team_two_pa",
    "team_three_pa",
    "team_fta",
    "team_assists",
    "team_turnovers",
    "team_offensive_rebounds",
    "team_defensive_rebounds_available",
    "team_fouls_drawn",
    "assisted_field_goal_rate",
    "creation_concentration",
    "three_point_attempt_rate",
)

PBP_STYLE_TARGETS = (
    "transition_shot_rate",
    "turnover_generated_shot_rate",
    "second_chance_shot_rate",
    "rim_attempt_rate",
    "paint_attempt_rate",
    "midrange_attempt_rate",
    "corner_three_attempt_rate",
    "above_break_three_attempt_rate",
)

PBP_FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "transition": (
        "transition_shot_rate", "turnover_generated_shot_rate",
    ),
    "second_chance": ("second_chance_shot_rate",),
    "shot_zone_mix": (
        "rim_attempt_rate", "paint_attempt_rate", "midrange_attempt_rate",
        "corner_three_attempt_rate", "above_break_three_attempt_rate",
    ),
}

UNSUPPORTED_PBP_CONCEPTS = {
    "early_clock_shot_tendency": (
        "The local feed stores event clock values but no validated possession "
        "start boundary; fast-break and turnover flags are retained instead."
    ),
    "average_possession_length": (
        "pbp_possessions_v1 contains a box-score possession estimate, not "
        "event-level possession start/end durations."
    ),
    "named_tactics": (
        "ISO, motion, PnR, and defender assignments are not directly observed."
    ),
}

# Numeric-only, compact player profile.  All values are lagged or pre-game.
BASE_PLAYER_FEATURES = (
    "season_start_year", "round_number", "career_el_games_before",
    "season_games_before", "has_1_prior_game", "has_3_prior_games",
    "has_5_prior_games", "has_10_prior_games", "home_game", "away_game",
    "career_el_fp_avg_before", "season_fp_avg_before", "season_fp_std_before",
    "last_1_fp", "last_3_fp_avg", "last_5_fp_avg", "last_10_fp_avg",
    "last_5_fp_std", "fp_ewma", "season_minutes_avg_before",
    "last_1_minutes", "last_3_minutes_avg", "last_5_minutes_avg",
    "last_10_minutes_avg", "minutes_ewma", "minutes_trend",
    "season_start_rate_before", "last_5_start_rate",
    "season_fp_per_min_before", "last_3_fp_per_min", "last_5_fp_per_min",
    "last_10_fp_per_min", "points_per_min", "rebounds_per_min",
    "oreb_per_min", "dreb_per_min", "assists_per_min", "steals_per_min",
    "blocks_per_min", "turnovers_per_min", "fga_per_min",
    "two_pa_per_min", "three_pa_per_min", "fta_per_min",
    "season_2p_pct_before", "season_3p_pct_before", "season_ft_pct_before",
    "last_5_2p_pct", "last_5_3p_pct", "last_5_ft_pct", "player_age",
    "days_since_previous_el_game", "player_games_last_7_calendar_days_el",
    "player_minutes_last_7_calendar_days_el", "team_days_since_previous_el_game",
    "team_season_pace_before", "team_last5_pace", "team_season_ortg_before",
    "team_last5_ortg", "team_season_drtg_before", "team_last5_drtg",
    "team_efg_before", "team_tov_rate_before", "team_oreb_rate_before",
    "team_ft_rate_before", "team_ast_rate_before", "opp_season_pace_before",
    "opp_last5_pace", "opp_season_ortg_before", "opp_last5_ortg",
    "opp_season_drtg_before", "opp_last5_drtg", "opp_efg_allowed",
    "opp_rebound_rate_allowed", "opp_assist_rate_allowed",
    "opp_turnover_forced_rate", "opp_3pa_rate_allowed", "opp_ft_rate_allowed",
    "rot_last5_minutes_avg", "rot_last5_minutes_share",
    "rot_season_minutes_share_before", "rot_last5_closing_lineup_rate",
    "rot_last5_starter_rate", "rot_last5_minutes_std", "rot_minutes_trend",
    "rot_minutes_ewma", "rot_role_stability", "role_change_score",
    "p6c_usage_season_before", "p6c_usage_last1", "p6c_usage_last3",
    "p6c_usage_last5", "p6c_usage_last10", "p6c_usage_last5_std",
    "p6c_usage_recent_change", "p6c_fga_share_last5", "p6c_2pa_share_last5",
    "p6c_3pa_share_last5", "p6c_fta_share_last5",
    "p6c_turnover_share_last5", "p6c_fouls_drawn_share_last5",
    "p6c_points_share_last5", "p6c_oreb_share_last5", "p6c_dreb_share_last5",
    "p6c_assist_share_last5", "p6c_creation_involvement_last5",
    "expected_usage_next_game", "p6c_rotation_depth_last5",
)

AVAILABILITY_FEATURES = (
    "number_players_out", "number_rotation_players_out",
    "missing_expected_minutes", "missing_rotation_share",
    "missing_starter_minutes", "missing_bench_minutes", "missing_recent_starts",
    "missing_fp_before", "missing_fp_per_min_before", "missing_points_rate",
    "missing_assist_rate", "missing_rebound_rate",
    "missing_ballhandling_proxy", "missing_rebounding_proxy",
    "available_rotation_players", "remaining_high_role_players",
    "remaining_role_concentration", "same_position_competition",
    "same_position_missing_minutes", "prior_absence_response_games",
    "prior_absence_response_avg_delta",
)

ENVIRONMENT_PREDICTION_FEATURES = tuple(
    f"p9_env_{name}" for name in (*GAME_ENVIRONMENT_TARGETS, *PBP_STYLE_TARGETS)
)

INTERACTION_FEATURES = (
    "p9_three_profile_x_three_pa_env",
    "p9_rebound_profile_x_miss_env",
    "p9_creator_profile_x_possessions",
    "p9_creator_profile_x_assists_env",
    "p9_foul_draw_profile_x_fta_env",
    "p9_efficiency_x_assisted_env",
)

FORBIDDEN_MODEL_INPUTS = frozenset({
    "target_minutes", "target_started", "actual_fantasy_points",
    "standardized_fantasy_points", "actual_usage_next_game",
    "actual_usage_next_game_raw", "points", "two_points_made",
    "two_points_attempted", "three_points_made", "three_points_attempted",
    "free_throws_made", "free_throws_attempted", "offensive_rebounds",
    "defensive_rebounds", "total_rebounds", "assists", "steals", "blocks",
    "turnovers", "fouls_drawn", "fouls_committed", "blocks_received",
    "team_won", "home_score", "away_score",
})


def player_feature_manifest(
    *, include_environment: bool, include_interactions: bool = True,
) -> dict[str, Any]:
    features = [*BASE_PLAYER_FEATURES, *AVAILABILITY_FEATURES]
    if include_environment:
        features.extend(ENVIRONMENT_PREDICTION_FEATURES)
        if include_interactions:
            features.extend(INTERACTION_FEATURES)
    features = list(dict.fromkeys(features))
    forbidden = sorted(set(features) & FORBIDDEN_MODEL_INPUTS)
    if forbidden:
        raise ValueError(f"forbidden Phase 9A player inputs: {forbidden}")
    return {
        "name": (
            "game_environment_statline" if include_environment
            else "statline_without_game_environment"
        ),
        "numeric": features,
        "features": features,
        "feature_count": len(features),
        "includes_environment": include_environment,
        "includes_interactions": include_environment and include_interactions,
        "sha256": hashlib.sha256("\n".join(features).encode()).hexdigest(),
    }


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PHASE9A_PROTOCOL_VERSION,
        "model_version": PHASE9A_MODEL_VERSION,
        "feature_version": PHASE9A_FEATURE_VERSION,
        "simulation_version": PHASE9A_SIMULATION_VERSION,
        "target_rule_version": TARGET_RULE_VERSION,
        "target_rule_fingerprint": scoring_rule_fingerprint(),
        "conditional_target": "verified Fantasy FP | actual_minutes > 0",
        "primary_seasons": list(PRIMARY_SEASONS),
        "evaluation_seasons": list(EVALUATION_SEASONS),
        "outer_folds": [asdict(fold) for fold in OUTER_FOLDS],
        "random_seed": PHASE9A_RANDOM_SEED,
        "simulations": PHASE9A_SIMULATIONS,
        "quantiles": list(QUANTILES),
        "upside_thresholds": list(UPSIDE_THRESHOLDS),
        "downside_thresholds": list(DOWNSIDE_THRESHOLDS),
        "game_environment_targets": list(GAME_ENVIRONMENT_TARGETS),
        "pbp_style_targets": list(PBP_STYLE_TARGETS),
        "pbp_feature_families": {
            key: list(value) for key, value in PBP_FEATURE_FAMILIES.items()
        },
        "unsupported_pbp_concepts": UNSUPPORTED_PBP_CONCEPTS,
        "stat_components": list(STAT_COMPONENTS),
        "opportunity_components": list(OPPORTUNITY_COMPONENTS),
        "dependency_model": (
            "minutes-first hierarchical sampling plus archetype/minute-band "
            "empirical joint residual vectors and beta-binomial conversion"
        ),
        "ensemble_policy": (
            "convex weights chosen only on each outer fold's chronological "
            "inner validation predictions"
        ),
        "freeze_gate": (
            "inner-selected architecture must beat frozen Phase 6C pooled MAE, "
            "not degrade pooled RMSE/CRPS, and improve MAE in every outer fold"
        ),
        "exclusions": [
            "new external data", "betting markets", "projected lineups",
            "domestic leagues", "tracking data", "guessed defender matchups",
            "raw event-sequence simulation", "multi-Matchday planner",
        ],
    }


def protocol_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    encoded = json.dumps(
        dict(payload or protocol_payload()), sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_columns(columns: Iterable[str], available: Iterable[str]) -> None:
    missing = sorted(set(columns) - set(available))
    if missing:
        raise ValueError(f"required Phase 9A columns missing: {missing}")
