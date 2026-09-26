"""Calibration, uncertainty, and diagnostics for Phase 4B predictions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from .ml_experiments import ranking_metrics, regression_metrics
from .phase4b_protocol import (
    NOMINAL_INTERVAL_COVERAGE,
    apply_role_change_class,
    role_change_thresholds,
)


@dataclass(slots=True)
class Calibrator:
    method: str
    transform: Callable[[np.ndarray], np.ndarray]
    parameters: dict[str, Any]


def select_hybrid_weight(
    direct_inner: dict[str, pd.DataFrame],
    decomposed_inner: dict[str, pd.DataFrame],
    weights: tuple[float, ...] = (0.25, 0.50, 0.75),
) -> tuple[float, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for weight in weights:
        fold_scores = []
        total_absolute_error = 0.0
        total_rows = 0
        for fold_id in sorted(direct_inner):
            direct, decomp = align_two(
                direct_inner[fold_id], decomposed_inner[fold_id]
            )
            predicted = weight * direct["predicted_fp"].to_numpy(float) + (
                1.0 - weight
            ) * decomp["predicted_fp"].to_numpy(float)
            actual = direct["actual_fp"].to_numpy(float)
            mae = float(np.mean(np.abs(actual - predicted)))
            fold_scores.append({"outer_fold": fold_id, "mae": mae, "rows": len(actual)})
            total_absolute_error += float(np.sum(np.abs(actual - predicted)))
            total_rows += len(actual)
        rows.append({
            "weight_direct": weight,
            "weight_decomposed": 1.0 - weight,
            "inner_mae": total_absolute_error / total_rows,
            "folds": fold_scores,
            "outer_test_rows_seen": 0,
        })
    selected = min(rows, key=lambda row: (row["inner_mae"], row["weight_direct"]))
    return float(selected["weight_direct"]), rows


def hybrid_predictions(
    direct: pd.DataFrame,
    decomposed: pd.DataFrame,
    weight_direct: float,
    *,
    experiment_id: str,
) -> pd.DataFrame:
    left, right = align_two(direct, decomposed)
    result = left.copy()
    result["predicted_fp"] = (
        weight_direct * left["predicted_fp"].to_numpy(float)
        + (1.0 - weight_direct) * right["predicted_fp"].to_numpy(float)
    )
    result["predicted_minutes"] = right.get("predicted_minutes", np.nan)
    result["predicted_fp_per_min"] = right.get("predicted_fp_per_min", np.nan)
    result["experiment_id"] = experiment_id
    result["model_version"] = experiment_id
    result["architecture"] = "hybrid"
    result["weight_direct"] = weight_direct
    return result


def calibrate_architecture(
    inner: dict[str, pd.DataFrame],
    outer: pd.DataFrame,
    *,
    experiment_prefix: str,
) -> dict[str, Any]:
    """Select calibration using late inner validation, never outer targets."""

    methods = ("none", "median_shift", "affine", "isotonic")
    method_evaluations: list[dict[str, Any]] = []
    for method in methods:
        absolute_error = 0.0
        rows = 0
        fold_metrics = []
        for fold_id, frame in sorted(inner.items()):
            fit, evaluation = chronological_calibration_split(frame)
            calibrator = fit_calibrator(
                method, fit["predicted_fp"].to_numpy(float),
                fit["actual_fp"].to_numpy(float),
            )
            predicted = calibrator.transform(evaluation["predicted_fp"].to_numpy(float))
            actual = evaluation["actual_fp"].to_numpy(float)
            mae = float(np.mean(np.abs(actual - predicted)))
            absolute_error += float(np.sum(np.abs(actual - predicted)))
            rows += len(actual)
            fold_metrics.append({"outer_fold": fold_id, "mae": mae, "rows": len(actual)})
        method_evaluations.append({
            "method": method, "inner_calibration_eval_mae": absolute_error / rows,
            "inner_calibration_eval_rows": rows, "fold_metrics": fold_metrics,
            "outer_test_rows_seen": 0,
        })
    selected_method = min(
        method_evaluations,
        key=lambda row: (row["inner_calibration_eval_mae"], methods.index(row["method"])),
    )["method"]
    outer_by_method: dict[str, pd.DataFrame] = {}
    parameters_by_method: dict[str, list[dict[str, Any]]] = {}
    for method in methods:
        method_parts: list[pd.DataFrame] = []
        method_parameters: list[dict[str, Any]] = []
        for fold_id, inner_frame in sorted(inner.items()):
            calibrator = fit_calibrator(
                method, inner_frame["predicted_fp"].to_numpy(float),
                inner_frame["actual_fp"].to_numpy(float),
            )
            fold_outer = outer[outer["outer_fold"] == fold_id].copy()
            fold_outer["predicted_fp_uncalibrated"] = fold_outer["predicted_fp"]
            fold_outer["predicted_fp"] = calibrator.transform(
                fold_outer["predicted_fp"].to_numpy(float)
            )
            fold_outer["experiment_id"] = f"{experiment_prefix}__{method}"
            fold_outer["model_version"] = fold_outer["experiment_id"]
            fold_outer["calibration_method"] = method
            method_parts.append(fold_outer)
            method_parameters.append({
                "outer_fold": fold_id, "method": method,
                "parameters": calibrator.parameters,
                "calibration_fit_rows": len(inner_frame),
                "calibration_fit_max_time": pd.to_datetime(
                    inner_frame["target_game_time"], utc=True
                ).max().isoformat(),
                "outer_test_min_time": pd.to_datetime(
                    fold_outer["target_game_time"], utc=True
                ).min().isoformat(),
                "outer_test_rows_seen": 0,
            })
        outer_by_method[method] = pd.concat(method_parts, ignore_index=True)
        parameters_by_method[method] = method_parameters
    outer_parts: list[pd.DataFrame] = []
    interval_inner: dict[str, pd.DataFrame] = {}
    parameters: list[dict[str, Any]] = []
    for fold_id, inner_frame in sorted(inner.items()):
        calibrator = fit_calibrator(
            selected_method,
            inner_frame["predicted_fp"].to_numpy(float),
            inner_frame["actual_fp"].to_numpy(float),
        )
        fold_outer = outer[outer["outer_fold"] == fold_id].copy()
        fold_outer["predicted_fp_uncalibrated"] = fold_outer["predicted_fp"]
        fold_outer["predicted_fp"] = calibrator.transform(
            fold_outer["predicted_fp"].to_numpy(float)
        )
        fold_outer["experiment_id"] = f"{experiment_prefix}__{selected_method}"
        fold_outer["model_version"] = fold_outer["experiment_id"]
        fold_outer["calibration_method"] = selected_method
        outer_parts.append(fold_outer)
        parameters.append({
            "outer_fold": fold_id, "method": selected_method,
            "parameters": calibrator.parameters,
            "calibration_fit_rows": len(inner_frame),
            "calibration_fit_max_time": pd.to_datetime(
                inner_frame["target_game_time"], utc=True
            ).max().isoformat(),
            "outer_test_min_time": pd.to_datetime(
                fold_outer["target_game_time"], utc=True
            ).min().isoformat(),
            "outer_test_rows_seen": 0,
        })
        fit, evaluation = chronological_calibration_split(inner_frame)
        interval_calibrator = fit_calibrator(
            selected_method, fit["predicted_fp"].to_numpy(float),
            fit["actual_fp"].to_numpy(float),
        )
        calibrated_eval = evaluation.copy()
        calibrated_eval["predicted_fp"] = interval_calibrator.transform(
            calibrated_eval["predicted_fp"].to_numpy(float)
        )
        interval_inner[fold_id] = calibrated_eval
    return {
        "selected_method": selected_method,
        "method_evaluations": method_evaluations,
        "parameters": parameters,
        "parameters_by_method": parameters_by_method,
        "outer_predictions_by_method": outer_by_method,
        "outer_predictions": pd.concat(outer_parts, ignore_index=True),
        "interval_inner_predictions": interval_inner,
    }


def chronological_calibration_split(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = frame.sort_values(["target_game_time", "game_id", "model_row_id"])
    unique_times = sorted(pd.to_datetime(ordered["target_game_time"], utc=True).unique())
    boundary = unique_times[max(1, len(unique_times) // 2)]
    times = pd.to_datetime(ordered["target_game_time"], utc=True)
    fit = ordered[times < boundary].copy()
    evaluation = ordered[times >= boundary].copy()
    if fit.empty or evaluation.empty:
        raise ValueError("Calibration split is empty")
    if pd.to_datetime(fit["target_game_time"], utc=True).max() >= pd.to_datetime(
        evaluation["target_game_time"], utc=True
    ).min():
        raise ValueError("Calibration fit/evaluation split is not chronological")
    return fit, evaluation


def fit_calibrator(method: str, predicted: np.ndarray, actual: np.ndarray) -> Calibrator:
    predicted = np.asarray(predicted, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if method == "none":
        return Calibrator(method, lambda values: np.asarray(values, dtype=float), {})
    if method == "median_shift":
        shift = float(np.median(actual - predicted))
        return Calibrator(
            method, lambda values, shift=shift: np.asarray(values, dtype=float) + shift,
            {"shift": shift},
        )
    if method == "affine":
        slope, intercept = np.polyfit(predicted, actual, 1)
        slope = float(slope)
        intercept = float(intercept)
        return Calibrator(
            method,
            lambda values, slope=slope, intercept=intercept: (
                slope * np.asarray(values, dtype=float) + intercept
            ),
            {"slope": slope, "intercept": intercept},
        )
    if method == "isotonic":
        model = IsotonicRegression(out_of_bounds="clip")
        model.fit(predicted, actual)
        return Calibrator(
            method,
            lambda values, model=model: np.asarray(model.predict(values), dtype=float),
            {
                "x_thresholds": [float(value) for value in model.X_thresholds_],
                "y_thresholds": [float(value) for value in model.y_thresholds_],
            },
        )
    raise KeyError(method)


def conformal_intervals(
    inner: dict[str, pd.DataFrame],
    outer: pd.DataFrame,
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    *,
    experiment_id: str,
    coverage: float = NOMINAL_INTERVAL_COVERAGE,
) -> dict[str, Any]:
    """Fit role-conditional split-conformal radii on chronological inner residuals."""

    parts: list[pd.DataFrame] = []
    audit: list[dict[str, Any]] = []
    fold_lookup = {fold["definition"].fold_id: fold for fold in folds}
    for fold_id, calibration in sorted(inner.items()):
        fold = fold_lookup[fold_id]
        inner_train = primary.loc[fold["inner_train"]]
        outer_train = primary.loc[fold["outer_train"]]
        inner_thresholds = role_change_thresholds(inner_train)
        outer_thresholds = role_change_thresholds(outer_train)
        calibration = calibration.copy()
        calibration["role_change_class"] = apply_role_change_class(
            calibration, inner_thresholds
        )
        calibration["absolute_residual"] = np.abs(
            calibration["actual_fp"].to_numpy(float)
            - calibration["predicted_fp"].to_numpy(float)
        )
        global_radius = finite_sample_radius(
            calibration["absolute_residual"].to_numpy(float), coverage
        )
        radii: dict[str, float] = {}
        for role, group in calibration.groupby("role_change_class", sort=True):
            radii[str(role)] = (
                finite_sample_radius(group["absolute_residual"].to_numpy(float), coverage)
                if len(group) >= 100 else global_radius
            )
        fold_outer = outer[outer["outer_fold"] == fold_id].copy()
        fold_outer["role_change_class"] = apply_role_change_class(
            fold_outer, outer_thresholds
        )
        radius = fold_outer["role_change_class"].map(radii).fillna(global_radius).to_numpy(float)
        fold_outer["floor_fp"] = fold_outer["predicted_fp"].to_numpy(float) - radius
        fold_outer["ceiling_fp"] = fold_outer["predicted_fp"].to_numpy(float) + radius
        fold_outer["p10_fp"] = fold_outer["floor_fp"]
        fold_outer["p50_fp"] = fold_outer["predicted_fp"]
        fold_outer["p90_fp"] = fold_outer["ceiling_fp"]
        fold_outer["interval_width"] = 2.0 * radius
        fold_outer["interval_covered"] = (
            (fold_outer["actual_fp"] >= fold_outer["floor_fp"])
            & (fold_outer["actual_fp"] <= fold_outer["ceiling_fp"])
        )
        fold_outer["uncertainty_method"] = "role_group_split_conformal_80"
        fold_outer["experiment_id"] = experiment_id
        fold_outer["model_version"] = experiment_id
        parts.append(fold_outer)
        audit.append({
            "outer_fold": fold_id, "nominal_coverage": coverage,
            "calibration_rows": len(calibration),
            "calibration_fit_max_time": pd.to_datetime(
                calibration["target_game_time"], utc=True
            ).max().isoformat(),
            "outer_test_min_time": pd.to_datetime(
                fold_outer["target_game_time"], utc=True
            ).min().isoformat(),
            "global_radius": global_radius, "role_radii": radii,
            "inner_role_thresholds": list(inner_thresholds),
            "outer_train_role_thresholds": list(outer_thresholds),
            "outer_test_residuals_used": 0,
        })
    predictions = pd.concat(parts, ignore_index=True)
    return {
        "predictions": predictions,
        "audit": audit,
        "evaluation": interval_evaluation(predictions),
    }


def finite_sample_radius(residuals: np.ndarray, coverage: float) -> float:
    residuals = np.sort(np.asarray(residuals, dtype=float))
    if residuals.size == 0:
        raise ValueError("Cannot calibrate interval without residuals")
    rank = min(residuals.size, math.ceil((residuals.size + 1) * coverage))
    return float(residuals[rank - 1])


def interval_evaluation(frame: pd.DataFrame) -> dict[str, Any]:
    def summarize(group: pd.DataFrame) -> dict[str, Any]:
        return {
            "rows": len(group),
            "coverage": float(group["interval_covered"].mean()),
            "mean_width": float(group["interval_width"].mean()),
            "median_width": float(group["interval_width"].median()),
        }

    result: dict[str, Any] = {"overall": summarize(frame)}
    result["by_season"] = [
        {"season": str(key), **summarize(group)}
        for key, group in frame.groupby("season", sort=True)
    ]
    banded = frame.copy()
    banded["minutes_band"] = banded["target_minutes"].map(minutes_band)
    result["by_minutes_band"] = [
        {"minutes_band": str(key), **summarize(group)}
        for key, group in banded.groupby("minutes_band", sort=False)
    ]
    result["by_role_change"] = [
        {"role_change_class": str(key), **summarize(group)}
        for key, group in frame.groupby("role_change_class", sort=True)
    ]
    high = frame[frame["actual_fp"] >= 25.0]
    result["actual_fp_25_plus"] = summarize(high) if not high.empty else None
    return result


def architecture_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    result = regression_metrics(
        frame["actual_fp"].to_numpy(float), frame["predicted_fp"].to_numpy(float)
    )
    result.update(ranking_metrics(frame))
    result["rows"] = len(frame)
    result["maximum_prediction"] = float(frame["predicted_fp"].max())
    result["minimum_prediction"] = float(frame["predicted_fp"].min())
    result["maximum_actual"] = float(frame["actual_fp"].max())
    result.update(high_performance_recall(frame))
    return result


def high_performance_recall(frame: pd.DataFrame) -> dict[str, Any]:
    mapped = frame.dropna(subset=["fantasy_matchday"])
    outputs: dict[str, Any] = {}
    for threshold, label in ((25.0, "25_plus"), (30.0, "30_plus")):
        captured20 = captured30 = available = 0
        for _, group in mapped.groupby(["season", "fantasy_matchday"], sort=True):
            actual = set(group.loc[group["actual_fp"] >= threshold, "model_row_id"])
            if not actual:
                continue
            predicted20 = set(group.nlargest(min(20, len(group)), "predicted_fp")["model_row_id"])
            predicted30 = set(group.nlargest(min(30, len(group)), "predicted_fp")["model_row_id"])
            captured20 += len(actual & predicted20)
            captured30 += len(actual & predicted30)
            available += len(actual)
        outputs[f"recall_{label}_predicted_top20"] = (
            captured20 / available if available else None
        )
        outputs[f"recall_{label}_predicted_top30"] = (
            captured30 / available if available else None
        )
        outputs[f"mapped_actual_{label}_rows"] = available
    return outputs


def grouped_metrics(
    frame: pd.DataFrame, group_columns: list[str]
) -> list[dict[str, Any]]:
    rows = []
    for keys, group in frame.groupby(group_columns, sort=True, dropna=False):
        values = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(group_columns, values, strict=True))
        row.update(regression_metrics(group["actual_fp"], group["predicted_fp"]))
        row["rows"] = len(group)
        rows.append(row)
    return rows


def outcome_band_metrics(frame: pd.DataFrame) -> list[dict[str, Any]]:
    copy = frame.copy()
    copy["actual_fp_band"] = pd.cut(
        copy["actual_fp"],
        bins=[-np.inf, 5, 10, 15, 20, 25, 30, np.inf],
        labels=["<5", "5-10", "10-15", "15-20", "20-25", "25-30", "30+"],
        right=False,
    ).astype(str)
    return grouped_metrics(copy, ["actual_fp_band"])


def prediction_bin_metrics(frame: pd.DataFrame) -> list[dict[str, Any]]:
    copy = frame.copy()
    copy["prediction_bin"] = pd.cut(
        copy["predicted_fp"],
        bins=[-np.inf, 5, 10, 15, 20, 25, np.inf],
        labels=["<5", "5-10", "10-15", "15-20", "20-25", "25+"],
        right=False,
    ).astype(str)
    return grouped_metrics(copy, ["prediction_bin"])


def minutes_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    metrics = regression_metrics(frame["actual_minutes"], frame["predicted_minutes"])
    metrics["rows"] = len(frame)
    metrics["maximum_actual_minutes"] = float(frame["actual_minutes"].max())
    metrics["maximum_predicted_minutes"] = float(frame["predicted_minutes"].max())
    copy = frame.copy()
    copy["minutes_band"] = copy["actual_minutes"].map(minutes_band)
    metrics["actual_minutes_bands"] = minutes_group_metrics(copy, "minutes_band")
    predicted_bins = pd.cut(
        copy["predicted_minutes"],
        bins=[-np.inf, 10, 15, 20, 25, 30, np.inf],
        labels=["<10", "10-15", "15-20", "20-25", "25-30", "30+"],
        right=False,
    ).astype(str)
    copy["predicted_minutes_bin"] = predicted_bins
    metrics["predicted_minutes_bins"] = minutes_group_metrics(
        copy, "predicted_minutes_bin"
    )
    return metrics


def minutes_group_metrics(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows = []
    for key, group in frame.groupby(column, sort=False):
        row = {column: str(key)}
        row.update(regression_metrics(group["actual_minutes"], group["predicted_minutes"]))
        row["rows"] = len(group)
        rows.append(row)
    return rows


def oracle_diagnostics(decomposed: pd.DataFrame) -> dict[str, Any]:
    actual_minutes = decomposed["target_minutes"].to_numpy(float)
    predicted_minutes = decomposed["predicted_minutes"].to_numpy(float)
    predicted_rate = decomposed["predicted_fp_per_min"].to_numpy(float)
    actual_fp = decomposed["actual_fp"].to_numpy(float)
    actual_rate = actual_fp / actual_minutes
    oracle_minutes = actual_minutes * predicted_rate
    oracle_production = predicted_minutes * actual_rate
    minute_error = actual_minutes - predicted_minutes
    production_error = actual_rate - predicted_rate
    fp_error = actual_fp - decomposed["predicted_fp"].to_numpy(float)
    return {
        "oracle_minutes_diagnostic_only": regression_metrics(actual_fp, oracle_minutes),
        "oracle_production_diagnostic_only": regression_metrics(actual_fp, oracle_production),
        "minutes_error_fp_error_pearson": safe_correlation(minute_error, fp_error),
        "production_rate_error_fp_error_pearson": safe_correlation(
            production_error, fp_error
        ),
        "mean_absolute_minutes_component": float(np.mean(np.abs(
            minute_error * predicted_rate
        ))),
        "mean_absolute_production_component": float(np.mean(np.abs(
            actual_minutes * production_error
        ))),
    }


def paired_comparison(
    reference: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    bootstrap_samples: int = 1000,
    seed: int = 1704,
) -> dict[str, Any]:
    left, right = align_two(reference, candidate)
    reference_error = np.abs(
        left["actual_fp"].to_numpy(float) - left["predicted_fp"].to_numpy(float)
    )
    candidate_error = np.abs(
        right["actual_fp"].to_numpy(float) - right["predicted_fp"].to_numpy(float)
    )
    improvement = reference_error - candidate_error
    grouped = pd.DataFrame({
        "game_id": left["game_id"].astype(str).to_numpy(),
        "sum": improvement,
    }).groupby("game_id", sort=True)["sum"].agg(["sum", "count"])
    sums = grouped["sum"].to_numpy(float)
    counts = grouped["count"].to_numpy(int)
    rng = np.random.default_rng(seed)
    values = np.empty(bootstrap_samples, dtype=float)
    for index in range(bootstrap_samples):
        sampled = rng.integers(0, len(grouped), size=len(grouped))
        values[index] = float(np.sum(sums[sampled]) / np.sum(counts[sampled]))
    fold_rows = []
    comparison = left[["model_row_id", "outer_fold"]].copy()
    comparison["improvement"] = improvement
    for fold_id, group in comparison.groupby("outer_fold", sort=True):
        fold_rows.append({
            "outer_fold": str(fold_id),
            "mean_mae_improvement": float(group["improvement"].mean()),
        })
    return {
        "delta_mae_candidate_minus_reference": float(
            np.mean(candidate_error) - np.mean(reference_error)
        ),
        "percentage_mae_change": float(
            100.0 * (np.mean(candidate_error) / np.mean(reference_error) - 1.0)
        ),
        "delta_rmse_candidate_minus_reference": float(
            np.sqrt(np.mean((right["actual_fp"] - right["predicted_fp"]) ** 2))
            - np.sqrt(np.mean((left["actual_fp"] - left["predicted_fp"]) ** 2))
        ),
        "row_win_rate": float(np.mean(candidate_error < reference_error)),
        "mean_mae_improvement": float(np.mean(improvement)),
        "game_cluster_bootstrap_95": [
            float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))
        ],
        "folds": fold_rows,
    }


def stage_diagnostics(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for stage, group in frame.groupby("competition_stage", sort=True):
        row = {"competition_stage": str(stage), "rows": len(group)}
        row.update(regression_metrics(group["actual_fp"], group["predicted_fp"]))
        row["mean_minutes"] = float(group["target_minutes"].mean())
        row["mean_actual_fp"] = float(group["actual_fp"].mean())
        row["mean_role_change_score"] = float(group["role_change_score"].mean())
        rows.append(row)
    return rows


def safe_correlation(first: np.ndarray, second: np.ndarray) -> float | None:
    if len(first) < 2 or np.std(first) == 0.0 or np.std(second) == 0.0:
        return None
    value = np.corrcoef(first, second)[0, 1]
    return float(value) if np.isfinite(value) else None


def minutes_band(value: float) -> str:
    if value < 10:
        return "<10"
    if value < 20:
        return "10-20"
    if value < 25:
        return "20-25"
    if value < 30:
        return "25-30"
    return "30+"


def align_two(
    first: pd.DataFrame, second: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    left = first.sort_values("model_row_id").reset_index(drop=True)
    right = second.sort_values("model_row_id").reset_index(drop=True)
    if not left["model_row_id"].equals(right["model_row_id"]):
        raise ValueError("Prediction row universes do not align")
    if "actual_fp" in left and "actual_fp" in right and not np.allclose(
        left["actual_fp"], right["actual_fp"]
    ):
        raise ValueError("Aligned prediction targets differ")
    return left, right


def prediction_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for row in frame.sort_values(["outer_fold", "model_row_id"])[
        ["outer_fold", "model_row_id", "predicted_fp"]
    ].itertuples(index=False, name=None):
        digest.update(f"{row[0]}|{row[1]}|{float(row[2]):.10f}\n".encode())
    return digest.hexdigest()
