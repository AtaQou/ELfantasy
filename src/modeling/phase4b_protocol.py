"""Frozen Phase 4B protocol, manifests, and leakage-safe context features."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .ml_protocol import (
    OUTER_FOLDS,
    PHASE4A_DATASET_VERSION,
    PLAYER_SHOT_FEATURES,
    ROTATION_FEATURES,
    build_outer_fold_indices,
    load_phase4a_frame,
)


PHASE4B_PROTOCOL_VERSION = "phase4b_conditional_performance_v1"
PHASE4B_DATASET_VERSION = PHASE4A_DATASET_VERSION
PHASE4B_RANDOM_SEED = 17
PHASE4A_WINNER_EXPERIMENT_ID = (
    "p4a__catboost__core_rotation__verified_only__player__s17"
)
PHASE4A_WINNER_MAE = 5.8272
SAME_ROW_BASELINE_MAE = 6.0130
NOMINAL_INTERVAL_COVERAGE = 0.80


MINUTES_ROLE_NUMERIC = (
    "season_start_year", "round_number", "career_el_games_before",
    "season_games_before", "has_1_prior_game", "has_3_prior_games",
    "has_5_prior_games", "has_10_prior_games",
    "season_minutes_avg_before", "last_1_minutes", "last_3_minutes_avg",
    "last_5_minutes_avg", "last_10_minutes_avg", "minutes_ewma",
    "minutes_trend", "season_start_rate_before", "last_5_start_rate",
    "season_fp_avg_before", "last_3_fp_avg", "last_5_fp_avg",
    "last_10_fp_avg", "fp_ewma", "player_age",
    "days_since_previous_el_game", "player_games_last_7_calendar_days_el",
    "player_minutes_last_7_calendar_days_el", "home_game", "away_game",
    "team_games_before", "team_season_pace_before", "team_last5_pace",
    "team_season_ortg_before", "team_last5_ortg",
    "opp_games_before", "opp_season_pace_before", "opp_last5_pace",
    "opp_season_drtg_before", "opp_last5_drtg",
    "team_days_since_previous_el_game",
    *ROTATION_FEATURES,
    "role_change_score",
)

PRODUCTION_NUMERIC = (
    "season_start_year", "round_number", "career_el_games_before",
    "season_games_before", "has_1_prior_game", "has_3_prior_games",
    "has_5_prior_games", "has_10_prior_games",
    "career_el_fp_avg_before", "season_fp_avg_before",
    "season_fp_std_before", "last_1_fp", "last_3_fp_avg", "last_5_fp_avg",
    "last_10_fp_avg", "last_3_fp_median", "last_5_fp_median",
    "last_5_fp_std", "last_10_fp_std", "last_5_fp_min", "last_5_fp_max",
    "fp_ewma", "season_minutes_avg_before", "last_1_minutes",
    "last_3_minutes_avg", "last_5_minutes_avg", "last_10_minutes_avg",
    "minutes_ewma", "minutes_trend", "season_start_rate_before",
    "last_5_start_rate", "season_fp_per_min_before", "last_3_fp_per_min",
    "last_5_fp_per_min", "last_10_fp_per_min", "points_per_min",
    "rebounds_per_min", "oreb_per_min", "dreb_per_min", "assists_per_min",
    "steals_per_min", "blocks_per_min", "turnovers_per_min", "ast_to_ratio",
    "season_2p_pct_before", "season_3p_pct_before", "season_ft_pct_before",
    "season_fg_pct_before", "season_efg_pct_before", "season_ts_pct_before",
    "last_5_2p_pct", "last_5_3p_pct", "last_5_ft_pct", "last_5_fg_pct",
    "last_5_efg_pct", "last_5_ts_pct", "fga_per_min", "two_pa_per_min",
    "three_pa_per_min", "fta_per_min", "player_age",
    "days_since_previous_el_game", "player_games_last_7_calendar_days_el",
    "player_minutes_last_7_calendar_days_el",
    "career_el_fp_avg_before_3s", "career_el_fp_avg_before_5s",
    "career_el_fp_avg_before_8s", "home_game", "away_game",
    "team_season_pace_before", "team_last5_pace",
    "team_season_ortg_before", "team_last5_ortg",
    "team_season_drtg_before", "team_last5_drtg", "team_efg_before",
    "team_tov_rate_before", "team_oreb_rate_before", "team_ft_rate_before",
    "team_ast_rate_before", "opp_season_pace_before", "opp_last5_pace",
    "opp_season_ortg_before", "opp_last5_ortg",
    "opp_season_drtg_before", "opp_last5_drtg", "opp_efg_allowed",
    "opp_rebound_rate_allowed", "opp_assist_rate_allowed",
    "opp_turnover_forced_rate", "opp_3pa_rate_allowed",
    "opp_ft_rate_allowed", "opp_fp_allowed_per_player_game",
    "opp_fp_allowed_last5_games", "team_days_since_previous_el_game",
    *ROTATION_FEATURES,
    "role_change_score",
)

STRUCTURAL_CATEGORICAL = (
    "season", "home_away", "team_id", "opponent_team_id",
    "fantasy_position",
)
STAGE_NUMERIC = (
    "playoff_series_game_number", "pregame_elimination_game",
    "pregame_deciding_game", "postseason_game",
)
STAGE_CATEGORICAL = ("competition_stage",)

PHASE4B_FORBIDDEN_MODEL_INPUTS = frozenset({
    "target_minutes", "target_started", "actual_fantasy_points",
    "standardized_fantasy_points", "actual_fp_per_min",
    "production_target", "predicted_minutes", "predicted_fp",
    "game_id", "game_code", "model_row_id", "player_game_id", "player_id",
    "target_game_time", "feature_cutoff_time", "local_game_date",
    "fantasy_credits_pre_matchday", "fantasy_id", "fantasy_matchday",
    "phase_code", "home_score", "away_score",
})


def phase4b_feature_manifest(name: str) -> dict[str, Any]:
    """Return a targeted, deterministic Phase 4B feature manifest."""

    if name.startswith("MINUTES_ROLE"):
        numeric = list(MINUTES_ROLE_NUMERIC)
    elif name.startswith("PRODUCTION"):
        numeric = list(PRODUCTION_NUMERIC)
    else:
        raise KeyError(name)
    categorical = list(STRUCTURAL_CATEGORICAL)
    if "SHOT" in name:
        numeric.extend(PLAYER_SHOT_FEATURES)
    if "STAGE" in name:
        numeric.extend(STAGE_NUMERIC)
        categorical.extend(STAGE_CATEGORICAL)
    features = [*numeric, *categorical]
    if len(features) != len(set(features)):
        raise ValueError(f"Duplicate Phase 4B features in {name}")
    forbidden = sorted(set(features) & PHASE4B_FORBIDDEN_MODEL_INPUTS)
    if forbidden:
        raise ValueError(f"Forbidden Phase 4B inputs in {name}: {forbidden}")
    digest = hashlib.sha256("\n".join(features).encode()).hexdigest()
    return {
        "name": name,
        "numeric": numeric,
        "categorical": categorical,
        "features": features,
        "feature_count": len(features),
        "sha256": digest,
        "includes_player_id": False,
    }


def role_change_score(frame: pd.DataFrame) -> pd.Series:
    """Minute-equivalent historical role movement, using only lagged fields."""

    signals = pd.DataFrame(index=frame.index)
    signals["recent_vs_season"] = (
        pd.to_numeric(frame["minutes_ewma"], errors="coerce")
        - pd.to_numeric(frame["season_minutes_avg_before"], errors="coerce")
    ).abs()
    signals["core_trend"] = pd.to_numeric(
        frame["minutes_trend"], errors="coerce"
    ).abs()
    signals["rotation_trend"] = pd.to_numeric(
        frame["rot_minutes_trend"], errors="coerce"
    ).abs()
    signals["rotation_share_change"] = 40.0 * (
        pd.to_numeric(frame["rot_last5_minutes_share"], errors="coerce")
        - pd.to_numeric(
            frame["rot_season_minutes_share_before"], errors="coerce"
        )
    ).abs()
    signals["starter_change"] = 8.0 * (
        pd.to_numeric(frame["rot_last5_starter_rate"], errors="coerce")
        - pd.to_numeric(frame["season_start_rate_before"], errors="coerce")
    ).abs()
    signals["rotation_volatility"] = pd.to_numeric(
        frame["rot_last5_minutes_std"], errors="coerce"
    ).abs()
    return signals.mean(axis=1, skipna=True)


def role_change_thresholds(training: pd.DataFrame) -> tuple[float, float]:
    score = pd.to_numeric(training["role_change_score"], errors="coerce").dropna()
    if score.empty:
        raise ValueError("Cannot fit role-change thresholds without history")
    return float(score.quantile(0.60)), float(score.quantile(0.85))


def apply_role_change_class(
    frame: pd.DataFrame, thresholds: tuple[float, float]
) -> pd.Series:
    low, high = thresholds
    score = pd.to_numeric(frame["role_change_score"], errors="coerce")
    return pd.Series(
        np.select(
            [score >= high, score >= low],
            ["large_role_change", "moderate_role_change"],
            default="stable_role",
        ),
        index=frame.index,
        dtype="string",
    )


def competition_context(games: pd.DataFrame) -> pd.DataFrame:
    """Derive structural stage and series state from explicit schedule metadata.

    ``phase_code`` is the authoritative stage source. Final Four sub-stages use
    the scheduled order inside the explicit FF phase. Playoff series state uses
    only completed earlier games in the same matchup.
    """

    required = {
        "canonical_game_id", "season_code", "phase_code", "round_number",
        "game_date", "home_team_id", "away_team_id", "home_score", "away_score",
    }
    missing = sorted(required - set(games.columns))
    if missing:
        raise ValueError(f"Schedule context columns missing: {missing}")
    work = games.copy()
    work["phase_clean"] = (
        work["phase_code"].astype("string").str.replace(" ", "", regex=False).str.upper()
    )
    work["game_date"] = pd.to_datetime(work["game_date"], utc=True)
    work["competition_stage"] = work["phase_clean"].map({
        "RS": "REGULAR_SEASON", "PI": "PLAY_IN", "PO": "PLAYOFFS",
    }).fillna("UNKNOWN")
    for season, indices in work[work["phase_clean"] == "FF"].groupby(
        "season_code", sort=True
    ).groups.items():
        ff = work.loc[indices]
        semifinal_round = ff["round_number"].min()
        semifinal = ff.index[ff["round_number"] == semifinal_round]
        work.loc[semifinal, "competition_stage"] = "FINAL_FOUR_SEMIFINAL"
        later = ff[ff["round_number"] > semifinal_round].sort_values(
            ["game_date", "canonical_game_id"]
        )
        if not later.empty:
            work.loc[later.index, "competition_stage"] = "FINAL_FOUR_THIRD_PLACE"
            final_round = later["round_number"].max()
            final_round_rows = later[later["round_number"] == final_round]
            final_index = final_round_rows.sort_values(
                ["game_date", "canonical_game_id"]
            ).index[-1]
            work.loc[final_index, "competition_stage"] = "FINAL_FOUR_FINAL"

    work["playoff_series_game_number"] = np.nan
    work["pregame_elimination_game"] = False
    work["pregame_deciding_game"] = False
    playoff = work[work["phase_clean"] == "PO"].copy()
    playoff["series_key"] = playoff.apply(
        lambda row: "|".join(sorted((str(row["home_team_id"]), str(row["away_team_id"])))),
        axis=1,
    )
    for _, group in playoff.groupby(["season_code", "series_key"], sort=True):
        ordered = group.sort_values(["game_date", "canonical_game_id"])
        wins: dict[str, int] = {}
        for number, (index, row) in enumerate(ordered.iterrows(), start=1):
            home = str(row["home_team_id"])
            away = str(row["away_team_id"])
            home_wins = wins.get(home, 0)
            away_wins = wins.get(away, 0)
            work.loc[index, "playoff_series_game_number"] = float(number)
            work.loc[index, "pregame_elimination_game"] = bool(
                home_wins == 2 or away_wins == 2
            )
            work.loc[index, "pregame_deciding_game"] = bool(
                home_wins == 2 and away_wins == 2
            )
            if pd.notna(row["home_score"]) and pd.notna(row["away_score"]):
                winner = home if float(row["home_score"]) > float(row["away_score"]) else away
                wins[winner] = wins.get(winner, 0) + 1
    single_elimination = work["competition_stage"].isin({
        "PLAY_IN", "FINAL_FOUR_SEMIFINAL", "FINAL_FOUR_FINAL",
    })
    work.loc[single_elimination, "pregame_elimination_game"] = True
    work.loc[work["competition_stage"].isin({"PLAY_IN", "FINAL_FOUR_FINAL"}),
             "pregame_deciding_game"] = True
    work["postseason_game"] = work["competition_stage"].ne("REGULAR_SEASON")
    return work[[
        "canonical_game_id", "competition_stage", "playoff_series_game_number",
        "pregame_elimination_game", "pregame_deciding_game", "postseason_game",
        "phase_clean",
    ]].rename(columns={
        "canonical_game_id": "game_id", "phase_clean": "phase_code",
    })


def load_phase4b_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    primary_only: bool = True,
) -> pd.DataFrame:
    frame = load_phase4a_frame(database_path, primary_only=primary_only)
    with connect_database(database_path, read_only=True) as connection:
        games = connection.execute(
            "SELECT canonical_game_id, season_code, phase_code, round_number, "
            "game_date, home_team_id, away_team_id, home_score, away_score "
            "FROM games WHERE game_date IS NOT NULL"
        ).df()
    context = competition_context(games)
    frame = frame.merge(context, on="game_id", how="left", validate="many_to_one")
    if frame["competition_stage"].isna().any():
        raise ValueError("Phase 4B frame has rows without explicit schedule stage")
    frame["role_change_score"] = role_change_score(frame)
    for column in ("pregame_elimination_game", "pregame_deciding_game", "postseason_game"):
        frame[column] = frame[column].astype(float)
    return frame.sort_values(
        ["target_game_time", "game_id", "player_id"]
    ).reset_index(drop=True)


def validate_phase4b_frame(frame: pd.DataFrame) -> None:
    if frame["model_row_id"].duplicated().any():
        raise ValueError("Duplicate Phase 4B model rows")
    if (pd.to_numeric(frame["target_minutes"], errors="coerce") <= 0).any():
        raise ValueError("Conditional-on-playing frame contains non-positive minutes")
    allowed = {
        "REGULAR_SEASON", "PLAY_IN", "PLAYOFFS", "FINAL_FOUR_SEMIFINAL",
        "FINAL_FOUR_FINAL", "FINAL_FOUR_THIRD_PLACE",
    }
    unexpected = sorted(set(frame["competition_stage"].dropna()) - allowed)
    if unexpected:
        raise ValueError(f"Unexpected competition stages: {unexpected}")
    for name in (
        "MINUTES_ROLE", "MINUTES_ROLE_STAGE", "PRODUCTION",
        "PRODUCTION_SHOT", "PRODUCTION_STAGE",
    ):
        manifest = phase4b_feature_manifest(name)
        missing = sorted(set(manifest["features"]) - set(frame.columns))
        if missing:
            raise ValueError(f"Missing {name} columns: {missing}")


def phase4b_protocol_payload() -> dict[str, Any]:
    manifests = {
        name: phase4b_feature_manifest(name)
        for name in (
            "MINUTES_ROLE", "MINUTES_ROLE_STAGE", "PRODUCTION",
            "PRODUCTION_SHOT", "PRODUCTION_STAGE",
        )
    }
    return {
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
        "dataset_version": PHASE4B_DATASET_VERSION,
        "population": "conditional_on_playing",
        "outer_folds": [asdict(fold) for fold in OUTER_FOLDS],
        "primary_metric": "mae",
        "random_seed": PHASE4B_RANDOM_SEED,
        "phase4a_winner_experiment_id": PHASE4A_WINNER_EXPERIMENT_ID,
        "feature_manifests": manifests,
        "minutes_candidates": [
            "ridge_role", "catboost_role", "xgboost_role",
            "catboost_role_stage", "catboost_role_older_history",
        ],
        "production_candidates": [
            "ridge_raw", "xgboost_raw", "catboost_raw", "catboost_weighted",
            "catboost_stabilized", "catboost_weighted_shot",
            "catboost_weighted_stage", "catboost_weighted_older_history",
        ],
        "production_targets": {
            "raw": "actual_fp / actual_minutes (minutes must be > 0)",
            "weighted": "raw FP/min with training weight actual_minutes / train_mean_minutes",
            "stabilized": "(actual_fp + k * train_total_fp/train_total_minutes) / (actual_minutes + k)",
            "stabilized_k_grid": [5.0, 10.0],
        },
        "hybrid_weight_grid": [0.25, 0.50, 0.75],
        "direct_loss_candidates": ["MAE", "RMSE", "Huber:delta=5.0"],
        "calibration_candidates": ["none", "median_shift", "affine", "isotonic"],
        "interval_method": (
            "chronological inner-validation split-conformal absolute residual; "
            "80% role-change-group conditional intervals with global fallback"
        ),
        "stage_source": (
            "games.phase_code plus schedule ordering; playoff series state uses "
            "only earlier completed games"
        ),
        "selection_isolation": (
            "model/target/manifest choices, boosting iterations, blend weights, "
            "calibration parameters, and interval radii use inner history only"
        ),
    }


def write_phase4b_protocol(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(phase4b_protocol_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_phase4b_folds(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return build_outer_fold_indices(frame)


def require_columns(columns: Iterable[str], available: Iterable[str]) -> None:
    missing = sorted(set(columns) - set(available))
    if missing:
        raise ValueError(f"Required Phase 4B columns missing: {missing}")
