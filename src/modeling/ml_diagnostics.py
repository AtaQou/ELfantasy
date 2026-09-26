"""Post-hoc diagnostics over genuine Phase 4A outer-fold predictions."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .ml_experiments import experiment_metrics, regression_metrics, ranking_metrics
from .ml_protocol import DEFAULT_RANDOM_SEED, feature_family
from .ml_runner import PHASE4A_SAMPLE_ROOT


BASELINE_NAME = "phase3a_season_recent_blend"
PRIMARY_EXPERIMENTS = {
    "ridge_core": "p4a__ridge__core__verified_only__player__s17",
    "ridge_all_rich": "p4a__ridge__core_all_rich__verified_only__player__s17",
    "catboost_core": "p4a__catboost__core__verified_only__player__s17",
    "catboost_all_rich": "p4a__catboost__core_all_rich__verified_only__player__s17",
    "xgboost_core": "p4a__xgboost__core__verified_only__player__s17",
    "xgboost_all_rich": "p4a__xgboost__core_all_rich__verified_only__player__s17",
    "lightgbm_core": "p4a__lightgbm__core__verified_only__player__s17",
    "lightgbm_all_rich": "p4a__lightgbm__core_all_rich__verified_only__player__s17",
    "catboost_rotation": "p4a__catboost__core_rotation__verified_only__player__s17",
}
ABLATION_EXPERIMENTS = {
    "CORE": PRIMARY_EXPERIMENTS["catboost_core"],
    "CORE_ROTATION": PRIMARY_EXPERIMENTS["catboost_rotation"],
    "CORE_ONOFF": "p4a__catboost__core_onoff__verified_only__player__s17",
    "CORE_LINEUP_TEAMMATE": (
        "p4a__catboost__core_lineup_teammate__verified_only__player__s17"
    ),
    "CORE_PLAYER_SHOT": (
        "p4a__catboost__core_player_shot__verified_only__player__s17"
    ),
    "CORE_OPPONENT_SHOT": (
        "p4a__catboost__core_opponent_shot__verified_only__player__s17"
    ),
    "CORE_ROTATION_ONOFF": (
        "p4a__catboost__core_rotation_onoff__verified_only__player__s17"
    ),
    "CORE_SHOT_ALL": "p4a__catboost__core_shot_all__verified_only__player__s17",
    "CORE_ALL_RICH": PRIMARY_EXPERIMENTS["catboost_all_rich"],
}


def generate_phase4a_diagnostics(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    sample_root: Path = PHASE4A_SAMPLE_ROOT,
) -> dict[str, Any]:
    with connect_database(database_path, read_only=True) as connection:
        predictions = connection.execute(
            "SELECT * FROM ml_phase4a_outer_predictions_v1 "
            "ORDER BY experiment_id, target_game_time, game_id, player_id"
        ).df()
        importance = connection.execute(
            "SELECT * FROM ml_phase4a_feature_importance_v1"
        ).df()
        experiments = connection.execute(
            "SELECT * FROM ml_phase4a_experiments_v1"
        ).df()
        primary = connection.execute(
            "SELECT actual_fantasy_points, prediction_season_recent_blend "
            "FROM ml_phase3b_primary_evaluation_v1"
        ).df()
    predictions["target_game_time"] = pd.to_datetime(
        predictions["target_game_time"], utc=True
    )
    _validate_prediction_universe(predictions)
    reference = _experiment(predictions, PRIMARY_EXPERIMENTS["catboost_rotation"])
    baseline = _baseline_predictions(reference)

    global_baseline = regression_metrics(
        primary["actual_fantasy_points"].to_numpy(float),
        primary["prediction_season_recent_blend"].to_numpy(float),
    )
    global_baseline["rows"] = len(primary)
    global_baseline["mae_expected"] = 5.995291989129302
    global_baseline["mae_reproduction_difference"] = (
        global_baseline["mae"] - global_baseline["mae_expected"]
    )

    experiment_rows = []
    for eid, group in predictions.groupby("experiment_id", sort=True):
        metrics = experiment_metrics(group)
        summary = experiments.loc[experiments["experiment_id"] == eid].iloc[0]
        experiment_rows.append({
            "experiment_id": eid,
            "model_type": summary["model_type"],
            "feature_set": summary["feature_set"],
            "training_target_mode": summary["training_target_mode"],
            "includes_player_id": bool(summary["includes_player_id"]),
            "random_seed": int(summary["random_seed"]),
            "feature_count": int(summary["feature_count"]),
            **metrics,
        })
    experiment_rows.append({
        "experiment_id": BASELINE_NAME, "model_type": "baseline",
        "feature_set": "PHASE3A_BLEND", "training_target_mode": "fixed",
        "includes_player_id": False, "random_seed": None, "feature_count": 0,
        **experiment_metrics(baseline),
    })

    serious_ids = [BASELINE_NAME, *PRIMARY_EXPERIMENTS.values()]
    serious = pd.concat([
        baseline.assign(experiment_id=BASELINE_NAME),
        predictions[predictions["experiment_id"].isin(PRIMARY_EXPERIMENTS.values())],
    ], ignore_index=True)
    folds = _group_metrics(serious, ["experiment_id", "outer_fold"])
    seasons = _group_metrics(serious, ["experiment_id", "season"])
    minute_bands = _band_metrics(serious, "minutes_band", _minutes_band)
    history_bands = _band_metrics(serious, "history_depth", _history_band)
    bias_bins = _bias_bins(serious)
    player_errors = _player_errors(serious)
    paired = _paired_comparisons(predictions, baseline)
    residual_correlations = _residual_correlations(predictions)
    seed_stability = _seed_stability(predictions)
    identity = _identity_diagnostics(predictions)
    standardized = _standardized_history_diagnostics(predictions)
    ablations = _ablation_metrics(predictions)
    importance_result = _importance_diagnostics(importance)
    distribution = _prediction_distribution(serious)

    winner_id = PRIMARY_EXPERIMENTS["catboost_rotation"]
    winner = _experiment(predictions, winner_id)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "global_phase3b_baseline_reproduction": global_baseline,
        "outer_test_baseline": experiment_metrics(baseline),
        "outer_rows": len(reference),
        "experiments": experiment_rows,
        "fold_metrics": folds,
        "season_metrics": seasons,
        "minute_band_metrics": minute_bands,
        "history_depth_metrics": history_bands,
        "prediction_bin_bias": bias_bins,
        "player_errors_min_15": player_errors,
        "paired_vs_baseline": paired,
        "residual_correlations": residual_correlations,
        "seed_stability": seed_stability,
        "identity_experiment": identity,
        "standardized_history_experiment": standardized,
        "catboost_ablations": ablations,
        "feature_importance": importance_result,
        "prediction_distribution": distribution,
        "winner": {
            "experiment_id": winner_id,
            "metrics": experiment_metrics(winner),
            "same_row_baseline_metrics": experiment_metrics(baseline),
            "paired_comparison": next(
                row for row in paired if row["experiment_id"] == winner_id
            ),
        },
        "interpretation": (
            "All diagnostics use genuine outer-test predictions. Error analysis "
            "was not used to retrain, retune, recalibrate, or alter manifests."
        ),
    }
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "diagnostics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return result


def _baseline_predictions(reference: pd.DataFrame) -> pd.DataFrame:
    result = reference.copy()
    result["predicted_fp"] = result["prediction_season_recent_blend"].astype(float)
    result["absolute_error"] = np.abs(result["actual_fp"] - result["predicted_fp"])
    result["residual"] = result["actual_fp"] - result["predicted_fp"]
    result["model_type"] = "baseline"
    result["feature_set"] = "PHASE3A_BLEND"
    return result


def _experiment(predictions: pd.DataFrame, eid: str) -> pd.DataFrame:
    result = predictions[predictions["experiment_id"] == eid].copy()
    if result.empty:
        raise KeyError(eid)
    return result


def _group_metrics(frame: pd.DataFrame, groups: list[str]) -> list[dict[str, Any]]:
    rows = []
    for keys, group in frame.groupby(groups, sort=True, dropna=False):
        values = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(groups, values, strict=True))
        row.update(regression_metrics(group["actual_fp"], group["predicted_fp"]))
        row["rows"] = len(group)
        rows.append(row)
    return rows


def _band_metrics(
    frame: pd.DataFrame, name: str, function: Any
) -> list[dict[str, Any]]:
    copy = frame.copy()
    copy[name] = copy.apply(function, axis=1)
    return _group_metrics(copy, ["experiment_id", name])


def _minutes_band(row: pd.Series) -> str:
    value = float(row["target_minutes"])
    if value < 10:
        return "<10"
    if value < 20:
        return "10-20"
    if value < 25:
        return "20-25"
    if value < 30:
        return "25-30"
    return "30+"


def _history_band(row: pd.Series) -> str:
    value = int(row["career_el_games_before"])
    if value == 0:
        return "cold_start"
    if value <= 2:
        return "1-2"
    if value <= 4:
        return "3-4"
    if value <= 9:
        return "5-9"
    return "10+"


def _bias_bins(frame: pd.DataFrame) -> list[dict[str, Any]]:
    copy = frame.copy()
    copy["prediction_bin"] = pd.cut(
        copy["predicted_fp"],
        bins=[-np.inf, 5, 10, 15, 20, 25, np.inf],
        labels=["<5", "5-10", "10-15", "15-20", "20-25", "25+"],
        right=False,
    ).astype(str)
    return _group_metrics(copy, ["experiment_id", "prediction_bin"])


def _player_errors(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for (eid, player_id), group in frame.groupby(
        ["experiment_id", "player_id"], sort=True
    ):
        if len(group) < 15:
            continue
        residual = group["actual_fp"] - group["predicted_fp"]
        rows.append({
            "experiment_id": eid, "player_id": player_id, "rows": len(group),
            "mae": float(np.mean(np.abs(residual))),
            "mean_residual": float(np.mean(residual)),
            "residual_std": float(np.std(residual, ddof=1)),
        })
    return rows


def _paired_comparisons(
    predictions: pd.DataFrame, baseline: pd.DataFrame
) -> list[dict[str, Any]]:
    base = baseline.set_index("model_row_id")
    rows = []
    for eid, group in predictions.groupby("experiment_id", sort=True):
        aligned = group.set_index("model_row_id").join(
            base[["absolute_error"]].rename(
                columns={"absolute_error": "baseline_absolute_error"}
            ), how="inner"
        )
        improvement = (
            aligned["baseline_absolute_error"] - aligned["absolute_error"]
        )
        low, high = _cluster_bootstrap_interval(aligned, improvement)
        rows.append({
            "experiment_id": eid, "rows": len(aligned),
            "mean_mae_improvement": float(improvement.mean()),
            "median_row_absolute_error_improvement": float(improvement.median()),
            "rows_improved_rate": float((improvement > 0).mean()),
            "mae_improvement_percent": float(
                100 * improvement.mean() / aligned["baseline_absolute_error"].mean()
            ),
            "game_cluster_bootstrap_ci95_low": low,
            "game_cluster_bootstrap_ci95_high": high,
            "bootstrap_seed": 202604,
            "bootstrap_replicates": 2000,
        })
    return rows


def _cluster_bootstrap_interval(
    frame: pd.DataFrame, improvement: pd.Series
) -> tuple[float, float]:
    work = pd.DataFrame({
        "game_id": frame["game_id"].to_numpy(),
        "improvement": improvement.to_numpy(float),
    })
    grouped = work.groupby("game_id")["improvement"].agg(["sum", "count"])
    sums = grouped["sum"].to_numpy(float)
    counts = grouped["count"].to_numpy(float)
    rng = np.random.default_rng(202604)
    estimates = np.empty(2000, dtype=float)
    size = len(grouped)
    for index in range(len(estimates)):
        sampled = rng.integers(0, size, size=size)
        estimates[index] = sums[sampled].sum() / counts[sampled].sum()
    return tuple(float(value) for value in np.quantile(estimates, [0.025, 0.975]))


def _residual_correlations(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    ids = [
        PRIMARY_EXPERIMENTS["catboost_rotation"],
        PRIMARY_EXPERIMENTS["catboost_core"],
        PRIMARY_EXPERIMENTS["xgboost_core"],
        PRIMARY_EXPERIMENTS["lightgbm_core"],
        PRIMARY_EXPERIMENTS["ridge_core"],
    ]
    wide = predictions[predictions["experiment_id"].isin(ids)].pivot(
        index="model_row_id", columns="experiment_id", values="residual"
    )
    rows = []
    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1:]:
            rows.append({
                "left": left, "right": right, "rows": int(wide[[left,right]].dropna().shape[0]),
                "pearson": float(wide[left].corr(wide[right], method="pearson")),
                "spearman": float(wide[left].corr(wide[right], method="spearman")),
            })
    return rows


def _seed_stability(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for model_type in ("catboost", "xgboost"):
        group = predictions[
            (predictions["model_type"] == model_type)
            & (predictions["feature_set"] == "CORE_ALL_RICH")
            & (predictions["training_target_mode"] == "verified_only")
            & predictions["experiment_id"].str.contains("__player__")
        ]
        values = []
        for seed, seed_rows in group.groupby("random_seed"):
            metrics = experiment_metrics(seed_rows)
            values.append({"seed": int(seed), **metrics})
        for metric in ("mae", "rmse", "spearman", "pearson"):
            observed = np.array([row[metric] for row in values], dtype=float)
            rows.append({
                "model_type": model_type, "metric": metric, "seeds": values,
                "mean": float(observed.mean()), "std": float(observed.std(ddof=0)),
                "min": float(observed.min()), "max": float(observed.max()),
            })
    return rows


def _identity_diagnostics(predictions: pd.DataFrame) -> dict[str, Any]:
    with_id = _experiment(predictions, PRIMARY_EXPERIMENTS["catboost_all_rich"])
    without_id = _experiment(
        predictions, "p4a__catboost__core_all_rich__verified_only__no_player__s17"
    )
    return {
        "with_player_id": experiment_metrics(with_id),
        "without_player_id": experiment_metrics(without_id),
        "by_history": _group_metrics(
            pd.concat([
                with_id.assign(identity_variant="with_player_id"),
                without_id.assign(identity_variant="without_player_id"),
            ]).assign(
                history_group=lambda value: np.where(
                    value["career_el_games_before"] >= 10, "established_10+",
                    "low_history_under_10"
                )
            ),
            ["identity_variant", "history_group"],
        ),
    }


def _standardized_history_diagnostics(predictions: pd.DataFrame) -> dict[str, Any]:
    verified = _experiment(predictions, PRIMARY_EXPERIMENTS["catboost_all_rich"])
    standardized = _experiment(
        predictions,
        "p4a__catboost__core_all_rich__verified_plus_standardized__player__s17",
    )
    return {
        "verified_only": experiment_metrics(verified),
        "verified_plus_standardized": experiment_metrics(standardized),
        "mae_difference_standardized_minus_verified": (
            experiment_metrics(standardized)["mae"]
            - experiment_metrics(verified)["mae"]
        ),
        "by_fold": _group_metrics(
            pd.concat([verified, standardized]),
            ["training_target_mode", "outer_fold"],
        ),
    }


def _ablation_metrics(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    core = experiment_metrics(_experiment(predictions, ABLATION_EXPERIMENTS["CORE"]))
    rows = []
    for feature_set, eid in ABLATION_EXPERIMENTS.items():
        values = experiment_metrics(_experiment(predictions, eid))
        rows.append({
            "feature_set": feature_set, "experiment_id": eid, **values,
            "delta_mae_vs_core": values["mae"] - core["mae"],
            "mae_improvement_vs_core_percent": (
                100 * (core["mae"] - values["mae"]) / core["mae"]
            ),
        })
    return rows


def _importance_diagnostics(importance: pd.DataFrame) -> dict[str, Any]:
    winner_id = PRIMARY_EXPERIMENTS["catboost_rotation"]
    selected = importance[importance["experiment_id"] == winner_id].copy()
    raw = selected.groupby(["outer_fold", "raw_feature", "feature_family"])[
        "normalized_importance"
    ].sum().reset_index()
    mean_raw = raw.groupby(["raw_feature", "feature_family"])[
        "normalized_importance"
    ].agg(["mean", "std"]).reset_index().sort_values("mean", ascending=False)
    family = raw.groupby(["outer_fold", "feature_family"])[
        "normalized_importance"
    ].sum().reset_index().groupby("feature_family")[
        "normalized_importance"
    ].agg(["mean", "std"]).reset_index().sort_values("mean", ascending=False)
    pivot = raw.pivot(index="raw_feature", columns="outer_fold", values="normalized_importance").fillna(0)
    correlations = []
    folds = list(pivot.columns)
    for left_index, left in enumerate(folds):
        for right in folds[left_index + 1:]:
            correlations.append({
                "left": left, "right": right,
                "spearman": float(pivot[left].corr(pivot[right], method="spearman")),
            })
    return {
        "winner_experiment_id": winner_id,
        "top_raw_features": mean_raw.head(30).to_dict("records"),
        "family_importance": family.to_dict("records"),
        "fold_rank_correlations": correlations,
    }


def _prediction_distribution(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for eid, group in frame.groupby("experiment_id", sort=True):
        rows.append({
            "experiment_id": eid, "rows": len(group),
            "prediction_min": float(group["predicted_fp"].min()),
            "prediction_max": float(group["predicted_fp"].max()),
            "prediction_mean": float(group["predicted_fp"].mean()),
            "actual_mean": float(group["actual_fp"].mean()),
            "mean_residual": float((group["actual_fp"]-group["predicted_fp"]).mean()),
            "negative_prediction_rate": float((group["predicted_fp"] < 0).mean()),
        })
    return rows


def _validate_prediction_universe(predictions: pd.DataFrame) -> None:
    expected = None
    for eid, group in predictions.groupby("experiment_id"):
        ids = frozenset(group["model_row_id"])
        if len(ids) != len(group):
            raise ValueError(f"Duplicate predictions in {eid}")
        if expected is None:
            expected = ids
        elif ids != expected:
            raise ValueError(f"Experiment row universe differs: {eid}")
        if (group["target_game_time"] < group["feature_cutoff_time"]).any():
            raise ValueError(f"Cutoff after target in {eid}")


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if pd.isna(value):
        return None
    raise TypeError(type(value).__name__)
