"""Frozen Phase 4A manifests and nested chronological split definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database


PHASE4A_PROTOCOL_VERSION = "phase4a_nested_walk_forward_v1"
PHASE4A_DATASET_VERSION = (
    "phase3b_primary_"
    "c02fd9e781cdc090f6bedd2c16b2ca47d3d47007d79ed25ab9c8cd342f8c64b3"
)
PRIMARY_TARGET_COLUMN = "actual_fantasy_points"
STANDARDIZED_TARGET_COLUMN = "standardized_fantasy_points"
PRIMARY_METRIC = "mae"
DEFAULT_RANDOM_SEED = 17
STABILITY_SEEDS = (17, 29, 43)
MIN_ONOFF_POSSESSIONS = 20.0


CORE_NUMERIC_FEATURES = (
    "season_start_year", "round_number",
    "career_el_games_before", "season_games_before",
    "has_1_prior_game", "has_3_prior_games", "has_5_prior_games",
    "has_10_prior_games", "career_el_fp_avg_before", "season_fp_avg_before",
    "season_fp_std_before", "last_1_fp", "last_3_fp_avg", "last_3_fp_n",
    "last_5_fp_avg", "last_5_fp_n", "last_10_fp_avg", "last_10_fp_n",
    "last_3_fp_median", "last_5_fp_median", "last_5_fp_std",
    "last_10_fp_std", "last_5_fp_min", "last_5_fp_max", "fp_ewma",
    "season_minutes_avg_before", "last_1_minutes", "last_3_minutes_avg",
    "last_5_minutes_avg", "last_10_minutes_avg", "minutes_ewma",
    "minutes_trend", "season_start_rate_before", "last_5_start_rate",
    "season_fp_per_min_before", "last_3_fp_per_min", "last_5_fp_per_min",
    "last_10_fp_per_min", "points_per_min", "rebounds_per_min",
    "oreb_per_min", "dreb_per_min", "assists_per_min", "steals_per_min",
    "blocks_per_min", "turnovers_per_min", "ast_to_ratio",
    "season_2p_pct_before", "season_3p_pct_before", "season_ft_pct_before",
    "season_fg_pct_before", "season_efg_pct_before", "season_ts_pct_before",
    "last_5_2p_pct", "last_5_3p_pct", "last_5_ft_pct", "last_5_fg_pct",
    "last_5_efg_pct", "last_5_ts_pct", "fga_per_min", "two_pa_per_min",
    "three_pa_per_min", "fta_per_min", "player_age",
    "days_since_previous_el_game", "player_games_last_7_calendar_days_el",
    "player_minutes_last_7_calendar_days_el", "career_el_fp_avg_before_3s",
    "career_el_fp_avg_before_5s", "career_el_fp_avg_before_8s", "home_game",
    "away_game", "team_games_before", "team_season_pace_before",
    "team_last5_pace", "team_season_ortg_before", "team_last5_ortg",
    "team_season_drtg_before", "team_last5_drtg", "team_efg_before",
    "team_tov_rate_before", "team_oreb_rate_before", "team_ft_rate_before",
    "team_ast_rate_before", "opp_games_before", "opp_season_pace_before",
    "opp_last5_pace", "opp_season_ortg_before", "opp_last5_ortg",
    "opp_season_drtg_before", "opp_last5_drtg", "opp_efg_allowed",
    "opp_rebound_rate_allowed", "opp_assist_rate_allowed",
    "opp_turnover_forced_rate", "opp_3pa_rate_allowed", "opp_ft_rate_allowed",
    "opp_fp_allowed_per_player_game", "opp_fp_allowed_last5_games",
    "team_days_since_previous_el_game",
)

CORE_CATEGORICAL_FEATURES = (
    "season", "home_away", "team_id", "opponent_team_id",
    "fantasy_position", "player_id",
)

ROTATION_FEATURES = (
    "rot_games_before", "rot_last3_games_n", "rot_last5_games_n",
    "rot_last1_stint_count", "rot_last3_stint_count_avg",
    "rot_last5_stint_count_avg", "rot_last3_avg_stint_minutes",
    "rot_last5_avg_stint_minutes", "rot_last3_longest_stint_minutes_avg",
    "rot_last5_longest_stint_minutes_avg",
    "rot_last3_first_sub_out_minutes_avg",
    "rot_last5_first_sub_out_minutes_avg",
    "rot_last3_first_entry_minutes_avg",
    "rot_last5_first_entry_minutes_avg", "rot_last5_first_stint_minutes_avg",
    "rot_last5_minutes_avg", "rot_last5_minutes_share",
    "rot_season_minutes_share_before", "rot_last5_closing_lineup_rate",
    "rot_season_closing_lineup_rate_before", "rot_last5_starter_rate",
    "rot_last5_minutes_std", "rot_last5_stint_count_std", "rot_minutes_trend",
    "rot_minutes_ewma", "rot_role_stability",
)

ONOFF_FEATURES = (
    "onoff_games_before", "onoff_last5_games_n",
    "onoff_season_on_possessions_n", "onoff_season_off_possessions_n",
    "onoff_season_on_ortg", "onoff_season_on_drtg",
    "onoff_season_on_net_rating", "onoff_season_off_ortg",
    "onoff_season_off_drtg", "onoff_season_off_net_rating",
    "onoff_season_ortg_diff", "onoff_season_drtg_diff",
    "onoff_season_net_rating_diff", "onoff_season_on_court_pace",
    "onoff_last5_on_possessions_n", "onoff_last5_on_court_net_rating",
    "onoff_last10_on_possessions_n", "onoff_last10_on_court_net_rating",
)

LINEUP_TEAMMATE_FEATURES = (
    "lineup_games_before", "lineup_last5_games_n",
    "lineup_last5_unique_lineups_avg", "lineup_last5_top_lineup_share_avg",
    "lineup_last5_top3_lineup_share_avg", "lineup_last5_entropy_avg",
    "lineup_last5_player_top_lineup_share_avg",
    "lineup_last5_continuity_avg", "teammate_last5_top_shared_minutes_avg",
    "teammate_last5_top3_shared_minutes_avg",
    "teammate_last5_minutes_concentration_avg",
    "teammate_last5_stable_count_avg",
)

PLAYER_SHOT_FEATURES = (
    "shot_games_before", "shot_last5_games_n", "shot_last10_games_n",
    "shot_attempts_before", "shot_last5_attempts_n", "shot_last10_attempts_n",
    "shot_season_rim_attempts", "shot_season_paint_attempts",
    "shot_season_midrange_attempts", "shot_season_corner3_attempts",
    "shot_season_above_break3_attempts", "shot_season_rim_attempt_rate",
    "shot_season_paint_attempt_rate", "shot_season_midrange_attempt_rate",
    "shot_season_corner3_attempt_rate",
    "shot_season_above_break3_attempt_rate", "shot_last5_rim_attempt_rate",
    "shot_last5_paint_attempt_rate", "shot_last5_midrange_attempt_rate",
    "shot_last5_corner3_attempt_rate", "shot_last5_above_break3_attempt_rate",
    "shot_avg_distance_m", "shot_rim_fg_pct", "shot_paint_fg_pct",
    "shot_midrange_fg_pct", "shot_corner3_fg_pct",
    "shot_above_break3_fg_pct", "shot_3pa_share", "shot_2pa_share",
    "shot_points_per_shot", "shot_zone_concentration", "shot_profile_entropy",
    "shot_recent_vs_season_mix_change",
)

OPPONENT_SHOT_FEATURES = (
    "opp_shot_games_before", "opp_shot_last5_games_n",
    "opp_shot_attempts_before", "opp_shot_season_rim_attempts_allowed",
    "opp_shot_season_paint_attempts_allowed",
    "opp_shot_season_midrange_attempts_allowed",
    "opp_shot_season_corner3_attempts_allowed",
    "opp_shot_season_above_break3_attempts_allowed",
    "opp_shot_season_rim_attempt_rate_allowed",
    "opp_shot_season_paint_attempt_rate_allowed",
    "opp_shot_season_midrange_attempt_rate_allowed",
    "opp_shot_season_corner3_attempt_rate_allowed",
    "opp_shot_season_above_break3_attempt_rate_allowed",
    "opp_shot_rim_fg_allowed", "opp_shot_paint_fg_allowed",
    "opp_shot_midrange_fg_allowed", "opp_shot_corner3_fg_allowed",
    "opp_shot_above_break3_fg_allowed", "opp_shot_avg_distance_allowed_m",
    "opp_shot_last5_rim_attempt_rate_allowed",
    "opp_shot_last5_paint_attempt_rate_allowed",
    "opp_shot_last5_midrange_attempt_rate_allowed",
    "opp_shot_last5_corner3_attempt_rate_allowed",
    "opp_shot_last5_above_break3_attempt_rate_allowed",
)

INTERACTION_FEATURES = (
    "interaction_rim_style", "interaction_3pa_style",
    "interaction_corner3_style",
)

FORBIDDEN_MODEL_INPUTS = frozenset({
    "target_minutes", "target_started", "standardized_fantasy_points",
    "actual_fantasy_points", "fantasy_credits_pre_matchday", "fantasy_id",
    "fantasy_matchday", "game_id", "game_code", "model_row_id",
    "player_game_id", "target_game_time", "feature_cutoff_time",
    "local_game_date", "target_rule_version", "target_rule_status",
    "feature_pipeline_version", "rich_feature_pipeline_version",
    "player_history_max_game_time", "team_history_max_game_time",
    "opp_history_max_game_time", "rot_source_max_game_time_us",
    "onoff_source_max_game_time_us", "lineup_source_max_game_time_us",
    "shot_source_max_game_time_us", "opp_shot_source_max_game_time_us",
    "fantasy_price_status", "fantasy_source_artifact_id",
    "phase3b_primary_eval_eligible",
})


FEATURE_MANIFEST_ADDITIONS = {
    "CORE": (),
    "CORE_ROTATION": ROTATION_FEATURES,
    "CORE_ONOFF": ONOFF_FEATURES,
    "CORE_LINEUP_TEAMMATE": LINEUP_TEAMMATE_FEATURES,
    "CORE_PLAYER_SHOT": PLAYER_SHOT_FEATURES,
    "CORE_OPPONENT_SHOT": OPPONENT_SHOT_FEATURES,
    "CORE_ROTATION_ONOFF": ROTATION_FEATURES + ONOFF_FEATURES,
    "CORE_SHOT_ALL": PLAYER_SHOT_FEATURES + OPPONENT_SHOT_FEATURES
    + INTERACTION_FEATURES,
    "CORE_ALL_RICH": ROTATION_FEATURES + ONOFF_FEATURES
    + LINEUP_TEAMMATE_FEATURES + PLAYER_SHOT_FEATURES
    + OPPONENT_SHOT_FEATURES + INTERACTION_FEATURES,
}


RIDGE_PARAMETER_GRID = (
    {"alpha": 1.0}, {"alpha": 10.0}, {"alpha": 100.0},
)
CATBOOST_PARAMETER_GRID = (
    {"depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
     "loss_function": "RMSE"},
    {"depth": 7, "learning_rate": 0.035, "l2_leaf_reg": 10.0,
     "loss_function": "RMSE"},
    {"depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
     "loss_function": "MAE"},
)
XGBOOST_PARAMETER_GRID = (
    {"max_depth": 3, "learning_rate": 0.04, "min_child_weight": 8.0,
     "subsample": 0.85, "colsample_bytree": 0.85, "reg_lambda": 10.0,
     "reg_alpha": 0.2},
    {"max_depth": 4, "learning_rate": 0.035, "min_child_weight": 10.0,
     "subsample": 0.85, "colsample_bytree": 0.8, "reg_lambda": 12.0,
     "reg_alpha": 0.5},
    {"max_depth": 5, "learning_rate": 0.03, "min_child_weight": 12.0,
     "subsample": 0.8, "colsample_bytree": 0.8, "reg_lambda": 15.0,
     "reg_alpha": 0.5},
)
LIGHTGBM_PARAMETER_GRID = (
    {"num_leaves": 15, "max_depth": 5, "learning_rate": 0.035,
     "min_child_samples": 60, "subsample": 0.85, "colsample_bytree": 0.85,
     "reg_lambda": 10.0, "reg_alpha": 0.2},
    {"num_leaves": 31, "max_depth": 6, "learning_rate": 0.03,
     "min_child_samples": 80, "subsample": 0.85, "colsample_bytree": 0.8,
     "reg_lambda": 12.0, "reg_alpha": 0.5},
    {"num_leaves": 15, "max_depth": 4, "learning_rate": 0.04,
     "min_child_samples": 50, "subsample": 0.9, "colsample_bytree": 0.9,
     "reg_lambda": 8.0, "reg_alpha": 0.1},
)


@dataclass(frozen=True, slots=True)
class OuterFold:
    fold_id: str
    outer_test_season: str
    verified_train_seasons: tuple[str, ...]
    inner_validation_policy: str


OUTER_FOLDS = (
    OuterFold("outer_e2023", "E2023", ("E2022",), "last_25pct_E2022_times"),
    OuterFold("outer_e2024", "E2024", ("E2022", "E2023"), "full_E2023"),
    OuterFold(
        "outer_e2025", "E2025", ("E2022", "E2023", "E2024"), "full_E2024"
    ),
)


def feature_manifest(name: str, *, include_player_id: bool = True) -> dict[str, Any]:
    if name not in FEATURE_MANIFEST_ADDITIONS:
        raise KeyError(name)
    categorical = list(CORE_CATEGORICAL_FEATURES)
    if not include_player_id:
        categorical.remove("player_id")
    numeric = [*CORE_NUMERIC_FEATURES, *FEATURE_MANIFEST_ADDITIONS[name]]
    all_features = [*numeric, *categorical]
    forbidden = sorted(set(all_features) & FORBIDDEN_MODEL_INPUTS)
    if forbidden:
        raise ValueError(f"Forbidden model inputs in {name}: {forbidden}")
    if len(all_features) != len(set(all_features)):
        raise ValueError(f"Duplicate features in manifest {name}")
    digest = hashlib.sha256("\n".join(all_features).encode()).hexdigest()
    return {
        "name": name,
        "numeric": numeric,
        "categorical": categorical,
        "features": all_features,
        "feature_count": len(all_features),
        "includes_player_id": include_player_id,
        "sha256": digest,
    }


def load_phase4a_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    primary_only: bool = True,
) -> pd.DataFrame:
    with connect_database(database_path, read_only=True) as connection:
        if primary_only:
            frame = connection.execute(
                "SELECT * FROM ml_phase3b_primary_evaluation_v1 "
                "ORDER BY target_game_time, game_id, player_id"
            ).df()
        else:
            frame = connection.execute(
                "SELECT combined.*, prediction.prediction_season_average, "
                "prediction.prediction_last_3, prediction.prediction_last_5, "
                "prediction.prediction_ewma, "
                "prediction.prediction_fp_per_min_x_minutes, "
                "prediction.prediction_season_recent_blend, "
                "prediction.baseline_version "
                "FROM ml_player_game_core_plus_rich_v1 AS combined "
                "LEFT JOIN ml_baseline_predictions_v1 AS prediction "
                "ON prediction.player_game_id=combined.player_game_id "
                "AND prediction.history_window_seasons=8 "
                "ORDER BY combined.target_game_time, combined.game_id, "
                "combined.player_id"
            ).df()
    frame["target_game_time"] = pd.to_datetime(frame["target_game_time"], utc=True)
    frame["feature_cutoff_time"] = pd.to_datetime(
        frame["feature_cutoff_time"], utc=True
    )
    return frame


def build_outer_fold_indices(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Build group-safe nested splits without consulting any target values."""

    folds: list[dict[str, Any]] = []
    for definition in OUTER_FOLDS:
        outer_train = frame.index[frame["season"].isin(definition.verified_train_seasons)]
        outer_test = frame.index[frame["season"] == definition.outer_test_season]
        if definition.fold_id == "outer_e2023":
            train_times = sorted(frame.loc[outer_train, "target_game_time"].unique())
            split_at = train_times[int(len(train_times) * 0.75)]
            inner_train = outer_train[
                frame.loc[outer_train, "target_game_time"] < split_at
            ]
            inner_validation = outer_train[
                frame.loc[outer_train, "target_game_time"] >= split_at
            ]
        else:
            validation_season = definition.verified_train_seasons[-1]
            inner_validation = frame.index[frame["season"] == validation_season]
            inner_train = frame.index[
                frame["season"].isin(definition.verified_train_seasons[:-1])
            ]
        fold = {
            "definition": definition,
            "inner_train": inner_train,
            "inner_validation": inner_validation,
            "outer_train": outer_train,
            "outer_test": outer_test,
        }
        validate_fold(frame, fold)
        folds.append(fold)
    return folds


def validate_fold(frame: pd.DataFrame, fold: dict[str, Any]) -> None:
    roles = ("inner_train", "inner_validation", "outer_train", "outer_test")
    if any(len(fold[role]) == 0 for role in roles):
        raise ValueError(f"Empty split role in {fold['definition'].fold_id}")
    for earlier, later in (
        ("inner_train", "inner_validation"),
        ("outer_train", "outer_test"),
    ):
        if not (
            frame.loc[fold[earlier], "target_game_time"].max()
            < frame.loc[fold[later], "target_game_time"].min()
        ):
            raise ValueError(
                f"Non-chronological {earlier}/{later} in "
                f"{fold['definition'].fold_id}"
            )
        earlier_games = set(frame.loc[fold[earlier], "game_id"])
        later_games = set(frame.loc[fold[later], "game_id"])
        if earlier_games & later_games:
            raise ValueError(f"Game crosses {earlier}/{later} boundary")
    if not set(fold["inner_train"]).issubset(set(fold["outer_train"])):
        raise ValueError("Inner training rows are outside outer training")
    if not set(fold["inner_validation"]).issubset(set(fold["outer_train"])):
        raise ValueError("Inner validation rows are outside outer training")


def protocol_payload() -> dict[str, Any]:
    manifests = {
        name: feature_manifest(name)
        for name in FEATURE_MANIFEST_ADDITIONS
    }
    return {
        "protocol_version": PHASE4A_PROTOCOL_VERSION,
        "dataset_version": PHASE4A_DATASET_VERSION,
        "primary_target": PRIMARY_TARGET_COLUMN,
        "primary_metric": PRIMARY_METRIC,
        "random_seed": DEFAULT_RANDOM_SEED,
        "stability_seeds": list(STABILITY_SEEDS),
        "outer_folds": [asdict(fold) for fold in OUTER_FOLDS],
        "feature_manifests": manifests,
        "parameter_grids": {
            "ridge": RIDGE_PARAMETER_GRID,
            "catboost": CATBOOST_PARAMETER_GRID,
            "xgboost": XGBOOST_PARAMETER_GRID,
            "lightgbm": LIGHTGBM_PARAMETER_GRID,
        },
        "early_stopping": {
            "catboost": {"max_iterations": 1000, "patience": 60},
            "xgboost": {"max_estimators": 1200, "patience": 60},
            "lightgbm": {"max_estimators": 1200, "patience": 60},
        },
        "selection_rule": (
            "lowest mean outer-fold MAE; RMSE, Spearman, ranking, fold stability, "
            "history-depth behavior, and complexity are secondary tie-breakers"
        ),
        "predeclared_primary_models": {
            "ridge": ["CORE", "CORE_ALL_RICH"],
            "catboost": ["CORE", "CORE_ALL_RICH"],
            "xgboost": ["CORE", "CORE_ALL_RICH"],
            "lightgbm": ["CORE", "CORE_ALL_RICH"],
        },
        "predeclared_detailed_ablation_model": "catboost",
        "predeclared_catboost_ablations": list(FEATURE_MANIFEST_ADDITIONS),
        "predeclared_identity_experiment": "catboost_CORE_ALL_RICH_without_player_id",
        "predeclared_standardized_history_experiment": (
            "catboost_CORE_ALL_RICH_train_E2018_E2021_standardized_plus_verified"
        ),
        "outer_test_isolation": (
            "outer-test rows are loaded only for final prediction after inner "
            "configuration/iteration selection"
        ),
    }


def write_protocol_json(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(protocol_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def feature_family(feature: str) -> str:
    if feature in ROTATION_FEATURES:
        return "rotation"
    if feature in ONOFF_FEATURES:
        return "onoff"
    if feature in LINEUP_TEAMMATE_FEATURES:
        return "lineup_teammate"
    if feature in PLAYER_SHOT_FEATURES:
        return "player_shot"
    if feature in OPPONENT_SHOT_FEATURES:
        return "opponent_shot"
    if feature in INTERACTION_FEATURES:
        return "interaction"
    if feature in CORE_CATEGORICAL_FEATURES:
        return "identity_context" if feature in {
            "player_id", "team_id", "opponent_team_id"
        } else "structural"
    return "core"


def require_columns(columns: Iterable[str], available: Iterable[str]) -> None:
    missing = sorted(set(columns) - set(available))
    if missing:
        raise ValueError(f"Frozen feature columns missing: {missing}")
