"""Frozen protocol helpers for conditional probabilistic player outcomes."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH

from .fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint
from .ml_protocol import (
    DEFAULT_RANDOM_SEED,
    OUTER_FOLDS,
    PHASE4A_DATASET_VERSION,
    build_outer_fold_indices,
    feature_manifest,
)
from .phase4b_protocol import load_phase4b_frame


PHASE6B_PROTOCOL_VERSION = "phase6b_probabilistic_player_outcome_v1"
PHASE6B_MODEL_VERSION = "phase6b_probabilistic_player_outcome_frozen_v1"
PHASE6B_CALIBRATION_VERSION = "phase6b_chronological_calibration_v1"
PHASE6B_RANDOM_SEED = DEFAULT_RANDOM_SEED
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
THRESHOLDS = (20.0, 25.0, 30.0, 35.0, 40.0)
QUANTILE_FEATURE_CANDIDATES = (
    "CORE", "CORE_ROTATION", "CORE_ONOFF", "CORE_PLAYER_SHOT",
)
MODEL_FAMILY_CANDIDATES = ("catboost", "lightgbm")
MIN_CLASSIFIER_POSITIVES = 150
MIN_CLASSIFIER_POSITIVES_PER_OUTER_SEASON = 30
PRIMARY_DATASET_SEASONS = ("E2022", "E2023", "E2024", "E2025")
PRIMARY_EVALUATION_SEASONS = ("E2023", "E2024", "E2025")
CONDITIONAL_TARGET = "actual_fantasy_points | actual_minutes > 0"


def load_phase6b_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Load verified conditional-on-participation rows and fail on DNP leakage."""

    frame = load_phase4b_frame(database_path, primary_only=True)
    frame = frame[frame["season"].isin(PRIMARY_DATASET_SEASONS)].copy()
    minutes = pd.to_numeric(frame["target_minutes"], errors="coerce")
    target = pd.to_numeric(frame["actual_fantasy_points"], errors="coerce")
    if minutes.isna().any() or (minutes <= 0).any():
        raise ValueError("Phase 6B primary data contain DNP/non-positive-minute rows")
    if target.isna().any():
        raise ValueError("Phase 6B primary verified target is missing")
    if not (frame["feature_cutoff_time"] <= frame["target_game_time"]).all():
        raise ValueError("Phase 6B feature cutoff occurs after target-game time")
    source_columns = [
        "player_history_max_game_time", "team_history_max_game_time",
        "opp_history_max_game_time", "rot_source_max_game_time_us",
        "onoff_source_max_game_time_us", "lineup_source_max_game_time_us",
        "shot_source_max_game_time_us", "opp_shot_source_max_game_time_us",
    ]
    for column in source_columns:
        if column not in frame:
            continue
        observed = pd.to_datetime(
            frame[column], utc=True, errors="coerce",
            **({"unit": "us"} if column.endswith("_us") else {}),
        )
        if (observed.notna() & (observed >= frame["feature_cutoff_time"])).any():
            raise ValueError(f"Phase 6B {column} reaches the target-game cutoff")
    return frame.reset_index(drop=True)


def phase6b_folds(frame: pd.DataFrame) -> list[dict[str, Any]]:
    folds = build_outer_fold_indices(frame)
    expected = [fold.fold_id for fold in OUTER_FOLDS]
    observed = [fold["definition"].fold_id for fold in folds]
    if observed != expected:
        raise ValueError("Phase 6B chronological fold identity changed")
    return folds


def threshold_base_rates(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    target = frame["actual_fantasy_points"].to_numpy(float)
    for threshold in THRESHOLDS:
        positive = target >= threshold
        by_season = {
            season: int((frame.loc[frame.season.eq(season), "actual_fantasy_points"]
                         >= threshold).sum())
            for season in PRIMARY_DATASET_SEASONS
        }
        by_role = {}
        role = role_band(frame)
        for name in sorted(role.unique()):
            mask = role.eq(name)
            by_role[name] = {
                "rows": int(mask.sum()), "positives": int(positive[mask.to_numpy()].sum()),
                "positive_rate": float(positive[mask.to_numpy()].mean()),
            }
        retained = (
            int(positive.sum()) >= MIN_CLASSIFIER_POSITIVES
            and all(by_season[season] >= MIN_CLASSIFIER_POSITIVES_PER_OUTER_SEASON
                    for season in PRIMARY_EVALUATION_SEASONS)
        )
        output.append({
            "threshold": threshold, "rows": len(frame),
            "positives": int(positive.sum()), "positive_rate": float(positive.mean()),
            "positives_by_season": by_season, "by_role_band": by_role,
            "direct_classifier_retained": bool(retained),
            "retention_gate": {
                "minimum_total_positives": MIN_CLASSIFIER_POSITIVES,
                "minimum_positive_examples_per_outer_season": (
                    MIN_CLASSIFIER_POSITIVES_PER_OUTER_SEASON
                ),
            },
        })
    return output


def retained_classifier_thresholds(base_rates: Sequence[Mapping[str, Any]]) -> tuple[float, ...]:
    return tuple(
        float(row["threshold"]) for row in base_rates
        if bool(row["direct_classifier_retained"])
    )


def pinball_loss(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    residual = np.asarray(actual, float) - np.asarray(predicted, float)
    return float(np.mean(np.maximum(quantile * residual, (quantile - 1) * residual)))


def quantile_metrics(
    actual: np.ndarray, predicted: np.ndarray, quantile: float,
) -> dict[str, float | int]:
    actual_values = np.asarray(actual, float)
    predicted_values = np.asarray(predicted, float)
    coverage = float(np.mean(actual_values <= predicted_values))
    return {
        "rows": len(actual_values), "quantile": float(quantile),
        "pinball_loss": pinball_loss(actual_values, predicted_values, quantile),
        "empirical_coverage": coverage,
        "coverage_error": coverage - float(quantile),
    }


def finite_sample_quantile(values: np.ndarray, quantile: float) -> float:
    """Conservative order-statistic quantile without interpolation."""

    ordered = np.sort(np.asarray(values, dtype=float))
    if len(ordered) == 0:
        raise ValueError("cannot calibrate from an empty residual sample")
    rank = int(np.ceil((len(ordered) + 1) * quantile)) - 1
    return float(ordered[min(max(rank, 0), len(ordered) - 1)])


def reconcile_quantiles(values: np.ndarray) -> np.ndarray:
    """Increasing rearrangement of independently fitted row-wise quantiles."""

    array = np.asarray(values, dtype=float)
    if array.ndim != 2 or array.shape[1] != len(QUANTILES):
        raise ValueError("quantile matrix has the wrong shape")
    return np.sort(array, axis=1)


def quantile_crossing_rate(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=float)
    if array.ndim != 2 or array.shape[1] != len(QUANTILES):
        raise ValueError("quantile matrix has the wrong shape")
    return float(np.mean(np.any(np.diff(array, axis=1) < 0.0, axis=1)))


def decreasing_pava(values: np.ndarray) -> np.ndarray:
    """Euclidean isotonic projection onto a non-increasing probability curve."""

    y = np.asarray(values, dtype=float)
    if y.ndim != 1:
        raise ValueError("PAVA expects one row")
    blocks: list[list[float]] = []
    for index, value in enumerate(y):
        blocks.append([float(value), 1.0, float(index), float(index)])
        while len(blocks) >= 2 and blocks[-2][0] < blocks[-1][0]:
            right = blocks.pop()
            left = blocks.pop()
            weight = left[1] + right[1]
            blocks.append([
                (left[0] * left[1] + right[0] * right[1]) / weight,
                weight, left[2], right[3],
            ])
    result = np.empty(len(y), dtype=float)
    for mean, _, start, end in blocks:
        result[int(start):int(end) + 1] = mean
    return np.clip(result, 0.0, 1.0)


def reconcile_probabilities(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError("probability matrix must be two-dimensional")
    return np.vstack([decreasing_pava(row) for row in array])


def probability_violation_rate(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=float)
    return float(np.mean(np.any(np.diff(array, axis=1) > 0.0, axis=1)))


def quantile_survival_bounds(quantile_row: np.ndarray, threshold: float) -> tuple[float, float]:
    """Return survival bounds implied by the calibrated quantile grid."""

    q_values = np.asarray(quantile_row, dtype=float)
    if np.any(np.diff(q_values) < 0):
        raise ValueError("quantile row is not monotonic")
    cdf_levels = np.asarray(QUANTILES, dtype=float)
    if threshold <= q_values[0]:
        return 1.0 - cdf_levels[0], 1.0
    if threshold > q_values[-1]:
        return 0.0, 1.0 - cdf_levels[-1]
    index = int(np.searchsorted(q_values, threshold, side="left"))
    return 1.0 - cdf_levels[index], 1.0 - cdf_levels[index - 1]


def reconcile_distribution(
    quantiles: np.ndarray,
    probabilities: np.ndarray,
    *,
    thresholds: Sequence[float] = THRESHOLDS,
) -> tuple[np.ndarray, np.ndarray]:
    """Reconcile ordering and bound threshold survival by the quantile grid."""

    q_values = reconcile_quantiles(quantiles)
    p_values = reconcile_probabilities(probabilities)
    if p_values.shape != (len(q_values), len(thresholds)):
        raise ValueError("threshold probability matrix has the wrong shape")
    bounded = np.empty_like(p_values)
    for row_index in range(len(q_values)):
        for column, threshold in enumerate(thresholds):
            lower, upper = quantile_survival_bounds(q_values[row_index], threshold)
            bounded[row_index, column] = np.clip(
                p_values[row_index, column], lower, upper
            )
    bounded = reconcile_probabilities(bounded)
    if np.any(np.diff(q_values, axis=1) < -1e-12):
        raise ValueError("quantile reconciliation failed")
    if np.any(np.diff(bounded, axis=1) > 1e-12):
        raise ValueError("probability reconciliation failed")
    return q_values, np.clip(bounded, 0.0, 1.0)


def weighted_interval_score(actual: np.ndarray, quantile_values: np.ndarray) -> float:
    """WIS using central 50%, 80%, and 90% intervals plus the median."""

    y = np.asarray(actual, dtype=float)
    q = reconcile_quantiles(quantile_values)
    intervals = ((0.50, 1, 3), (0.20, 0, 4), (0.10, 0, 5))
    total = 0.5 * np.abs(y - q[:, 2])
    denominator = 0.5
    for alpha, lower_index, upper_index in intervals:
        lower = q[:, lower_index]
        upper = q[:, upper_index]
        score = (
            upper - lower
            + (2.0 / alpha) * (lower - y) * (y < lower)
            + (2.0 / alpha) * (y - upper) * (y > upper)
        )
        weight = alpha / 2.0
        total += weight * score
        denominator += weight
    return float(np.mean(total / denominator))


def role_band(frame: pd.DataFrame) -> pd.Series:
    expected = pd.to_numeric(frame["season_minutes_avg_before"], errors="coerce")
    return pd.cut(
        expected, bins=[-np.inf, 10, 20, 25, 30, np.inf],
        labels=["low_under_10", "medium_10_20", "role_20_25",
                "high_25_30", "star_30_plus"], right=False,
    ).astype("string").fillna("cold_unknown")


def actual_minute_band(frame: pd.DataFrame) -> pd.Series:
    minutes = pd.to_numeric(frame["target_minutes"], errors="coerce")
    return pd.cut(
        minutes, bins=[0, 5, 10, 15, 20, 25, 30, np.inf],
        labels=["0_5", "5_10", "10_15", "15_20", "20_25", "25_30", "30_plus"],
        right=False, include_lowest=True,
    ).astype("string")


def distribution_confidence(history_sample_count: Any) -> str:
    try:
        count = int(history_sample_count)
    except (TypeError, ValueError):
        return "LOW"
    if count < 5:
        return "LOW"
    if count < 20:
        return "MEDIUM"
    return "HIGH"


def protocol_payload() -> dict[str, Any]:
    manifests = {
        name: feature_manifest(name, include_player_id=True)
        for name in QUANTILE_FEATURE_CANDIDATES
    }
    return {
        "protocol_version": PHASE6B_PROTOCOL_VERSION,
        "model_version": PHASE6B_MODEL_VERSION,
        "calibration_version": PHASE6B_CALIBRATION_VERSION,
        "dataset_version": PHASE4A_DATASET_VERSION,
        "target_rule_version": TARGET_RULE_VERSION,
        "target_rule_fingerprint": scoring_rule_fingerprint(),
        "conditional_target": CONDITIONAL_TARGET,
        "primary_dataset_seasons": list(PRIMARY_DATASET_SEASONS),
        "primary_evaluation_seasons": list(PRIMARY_EVALUATION_SEASONS),
        "outer_folds": [asdict(fold) for fold in OUTER_FOLDS],
        "random_seed": PHASE6B_RANDOM_SEED,
        "quantiles": list(QUANTILES), "thresholds": list(THRESHOLDS),
        "feature_candidates": manifests,
        "model_family_candidates": list(MODEL_FAMILY_CANDIDATES),
        "classifier_sparsity_gate": {
            "minimum_total_positives": MIN_CLASSIFIER_POSITIVES,
            "minimum_positive_examples_per_outer_season": (
                MIN_CLASSIFIER_POSITIVES_PER_OUTER_SEASON
            ),
        },
        "selection": (
            "feature/model family selected by weighted chronological inner P90 "
            "pinball loss; outer seasons are evaluation only"
        ),
        "calibration": {
            "quantiles": (
                "finite-sample residual offsets by pregame role band from inner "
                "validation, minimum 100 rows with global fallback"
            ),
            "classifiers": "Platt logistic fit only on inner validation scores",
        },
        "reconciliation": {
            "quantiles": "row-wise increasing rearrangement",
            "probabilities": "row-wise decreasing PAVA",
            "joint": "classifier survival clipped to quantile-implied bounds",
        },
    }


def payload_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    encoded = json.dumps(
        dict(payload or protocol_payload()), sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_finite(values: Iterable[float], name: str) -> None:
    array = np.asarray(list(values), dtype=float)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
