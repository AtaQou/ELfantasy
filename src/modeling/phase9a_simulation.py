"""Hierarchical stat-line sampling and distribution diagnostics for Phase 9A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

from .phase9a_protocol import (
    DOWNSIDE_THRESHOLDS,
    OPPORTUNITY_COMPONENTS,
    QUANTILES,
    UPSIDE_THRESHOLDS,
)


@dataclass(slots=True)
class ResidualBank:
    """Chronological validation residuals used as a joint empirical copula."""

    minutes_residual: np.ndarray
    standardized_opportunity_residual: np.ndarray
    archetype: np.ndarray
    expected_minute_band: np.ndarray

    def validate(self) -> None:
        rows = len(self.minutes_residual)
        if self.standardized_opportunity_residual.shape != (
            rows, len(OPPORTUNITY_COMPONENTS)
        ):
            raise ValueError("opportunity residual bank has the wrong shape")
        if len(self.archetype) != rows or len(self.expected_minute_band) != rows:
            raise ValueError("residual-bank labels have the wrong length")
        if rows < 100 or not np.isfinite(self.minutes_residual).all():
            raise ValueError("residual bank is too small or non-finite")
        if not np.isfinite(self.standardized_opportunity_residual).all():
            raise ValueError("opportunity residual bank is non-finite")


def minute_band(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return np.digitize(array, (10.0, 20.0, 25.0, 30.0)).astype(np.int8)


def build_residual_bank(
    *,
    actual_minutes: Sequence[float],
    predicted_minutes: Sequence[float],
    actual_opportunities: np.ndarray,
    predicted_opportunities: np.ndarray,
    archetype: Sequence[str],
) -> ResidualBank:
    actual = np.asarray(actual_opportunities, dtype=float)
    predicted = np.clip(np.asarray(predicted_opportunities, dtype=float), 0.0, None)
    if actual.shape != predicted.shape or actual.shape[1] != len(OPPORTUNITY_COMPONENTS):
        raise ValueError("component matrices have the wrong shape")
    scale = np.sqrt(predicted + 0.5)
    residual = np.clip((actual - predicted) / scale, -8.0, 8.0)
    bank = ResidualBank(
        minutes_residual=np.clip(
            np.asarray(actual_minutes, float) - np.asarray(predicted_minutes, float),
            -25.0, 25.0,
        ),
        standardized_opportunity_residual=residual,
        archetype=np.asarray(archetype, dtype=str),
        expected_minute_band=minute_band(predicted_minutes),
    )
    bank.validate()
    return bank


def efficiency_parameters(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return beta-binomial alpha/beta parameters from pre-game history only."""

    probabilities = []
    effective_samples = []
    definitions = (
        ("season_2p_pct_before", "last_5_2p_pct", "two_pa_per_min", 0.54),
        ("season_3p_pct_before", "last_5_3p_pct", "three_pa_per_min", 0.36),
        ("season_ft_pct_before", "last_5_ft_pct", "fta_per_min", 0.78),
    )
    games = pd.to_numeric(frame["season_games_before"], errors="coerce").fillna(0).to_numpy(float)
    minutes = pd.to_numeric(
        frame["season_minutes_avg_before"], errors="coerce"
    ).fillna(15.0).clip(1.0, 40.0).to_numpy(float)
    for season_column, recent_column, rate_column, prior in definitions:
        season = pd.to_numeric(frame[season_column], errors="coerce").to_numpy(float)
        recent = pd.to_numeric(frame[recent_column], errors="coerce").to_numpy(float)
        rate = pd.to_numeric(frame[rate_column], errors="coerce").fillna(0.0).clip(0.0, 2.0).to_numpy(float)
        season = np.where(np.isfinite(season), season, prior)
        recent = np.where(np.isfinite(recent), recent, season)
        probability = np.clip(0.7 * season + 0.3 * recent, 0.03, 0.97)
        sample = np.clip(games * minutes * rate, 0.0, 180.0)
        probabilities.append(probability)
        effective_samples.append(sample)
    probability_matrix = np.column_stack(probabilities)
    sample_matrix = np.column_stack(effective_samples)
    prior_strength = np.asarray((12.0, 16.0, 12.0))
    prior_mean = np.asarray((0.54, 0.36, 0.78))
    alpha = probability_matrix * sample_matrix + prior_mean * prior_strength
    beta = (1.0 - probability_matrix) * sample_matrix + (1.0 - prior_mean) * prior_strength
    return np.clip(alpha, 0.1, None), np.clip(beta, 0.1, None)


def score_stat_arrays(statistics: Mapping[str, np.ndarray], team_won: np.ndarray) -> np.ndarray:
    """Vectorized verified Fantasy score; all inputs are simulated stat lines."""

    required = {
        "two_points_made", "two_points_attempted", "three_points_made",
        "three_points_attempted", "free_throws_made", "free_throws_attempted",
        "offensive_rebounds", "defensive_rebounds", "assists", "steals",
        "blocks", "turnovers", "fouls_drawn", "fouls_committed",
        "blocks_received",
    }
    missing = sorted(required - set(statistics))
    if missing:
        raise ValueError(f"missing simulated scoring inputs: {missing}")
    two_made = np.asarray(statistics["two_points_made"], float)
    two_attempted = np.asarray(statistics["two_points_attempted"], float)
    three_made = np.asarray(statistics["three_points_made"], float)
    three_attempted = np.asarray(statistics["three_points_attempted"], float)
    ft_made = np.asarray(statistics["free_throws_made"], float)
    ft_attempted = np.asarray(statistics["free_throws_attempted"], float)
    if (
        np.any(two_made > two_attempted)
        or np.any(three_made > three_attempted)
        or np.any(ft_made > ft_attempted)
    ):
        raise ValueError("simulated makes exceed attempts")
    points = 2.0 * two_made + 3.0 * three_made + ft_made
    rebounds = (
        np.asarray(statistics["offensive_rebounds"], float)
        + np.asarray(statistics["defensive_rebounds"], float)
    )
    base = (
        points + rebounds
        + np.asarray(statistics["assists"], float)
        + np.asarray(statistics["steals"], float)
        + np.asarray(statistics["blocks"], float)
        + np.asarray(statistics["fouls_drawn"], float)
        - np.asarray(statistics["turnovers"], float)
        - np.asarray(statistics["blocks_received"], float)
        - np.asarray(statistics["fouls_committed"], float)
        - (two_attempted - two_made)
        - (three_attempted - three_made)
        - (ft_attempted - ft_made)
    )
    return base + np.where(np.asarray(team_won, bool), 0.1 * np.abs(base), 0.0)


def enforce_constraints(statistics: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Return integer, non-negative, internally consistent basketball counts."""

    output = {
        key: np.maximum(np.rint(np.asarray(value, float)), 0).astype(np.int16)
        for key, value in statistics.items()
    }
    for made, attempted in (
        ("two_points_made", "two_points_attempted"),
        ("three_points_made", "three_points_attempted"),
        ("free_throws_made", "free_throws_attempted"),
    ):
        if made in output and attempted in output:
            output[made] = np.minimum(output[made], output[attempted])
    if "offensive_rebounds" in output and "defensive_rebounds" in output:
        output["total_rebounds"] = (
            output["offensive_rebounds"] + output["defensive_rebounds"]
        )
    if all(key in output for key in ("two_points_made", "three_points_made", "free_throws_made")):
        output["points"] = (
            2 * output["two_points_made"]
            + 3 * output["three_points_made"]
            + output["free_throws_made"]
        )
    return output


def simulate_stat_lines(
    *,
    frame: pd.DataFrame,
    predicted_minutes: np.ndarray,
    predicted_opportunities: np.ndarray,
    residual_bank: ResidualBank,
    win_probability: np.ndarray,
    archetype: Sequence[str],
    simulations: int,
    probabilistic_minutes: bool,
    random_seed: int,
) -> tuple[np.ndarray, pd.DataFrame, np.ndarray]:
    """Sample plausible stat lines, then reconstruct Fantasy points.

    The same historical residual row supplies minutes and every opportunity
    component.  This preserves empirically observed dependence without fitting
    fragile player-specific covariance matrices.
    """

    residual_bank.validate()
    rows = len(frame)
    minutes_center = np.clip(np.asarray(predicted_minutes, float), 0.5, 45.0)
    opportunity_center = np.clip(np.asarray(predicted_opportunities, float), 0.0, None)
    if opportunity_center.shape != (rows, len(OPPORTUNITY_COMPONENTS)):
        raise ValueError("predicted opportunity matrix has the wrong shape")
    probability = np.clip(np.asarray(win_probability, float), 0.02, 0.98)
    labels = np.asarray(archetype, dtype=str)
    bands = minute_band(minutes_center)
    alpha, beta = efficiency_parameters(frame)
    rng = np.random.default_rng(random_seed)
    fantasy = np.empty((rows, simulations), dtype=np.float32)
    sampled_minutes = np.empty((rows, simulations), dtype=np.float32)
    component_sums = {
        name: np.zeros(rows, dtype=float)
        for name in (
            "minutes", *OPPORTUNITY_COMPONENTS, "two_points_made",
            "three_points_made", "free_throws_made", "points", "total_rebounds",
        )
    }

    fallback = np.arange(len(residual_bank.minutes_residual))
    for start in range(0, rows, 192):
        stop = min(start + 192, rows)
        width = stop - start
        selected = np.empty((width, simulations), dtype=np.int32)
        for local, row in enumerate(range(start, stop)):
            eligible = np.flatnonzero(
                (residual_bank.archetype == labels[row])
                & (residual_bank.expected_minute_band == bands[row])
            )
            if len(eligible) < 40:
                eligible = np.flatnonzero(residual_bank.archetype == labels[row])
            if len(eligible) < 40:
                eligible = fallback
            selected[local] = rng.choice(eligible, size=simulations, replace=True)

        if probabilistic_minutes:
            minute_values = (
                minutes_center[start:stop, None]
                + residual_bank.minutes_residual[selected]
            )
        else:
            minute_values = np.broadcast_to(
                minutes_center[start:stop, None], (width, simulations)
            ).copy()
        minute_values = np.clip(minute_values, 0.5, 50.0)
        sampled_minutes[start:stop] = minute_values
        scale = minute_values / minutes_center[start:stop, None]
        mean = opportunity_center[start:stop, None, :] * scale[:, :, None]
        residual = residual_bank.standardized_opportunity_residual[selected]
        counts = mean + residual * np.sqrt(mean + 0.5)
        counts = np.maximum(np.rint(counts), 0).astype(np.int16)
        counts[:, :, :3] = np.minimum(counts[:, :, :3], 45)
        counts[:, :, 3:] = np.minimum(counts[:, :, 3:], 25)
        stats = {
            name: counts[:, :, index]
            for index, name in enumerate(OPPORTUNITY_COMPONENTS)
        }
        conversion = np.empty((width, simulations, 3), dtype=float)
        for index in range(3):
            conversion[:, :, index] = rng.beta(
                alpha[start:stop, index, None],
                beta[start:stop, index, None],
                size=(width, simulations),
            )
        for index, (made, attempted) in enumerate((
            ("two_points_made", "two_points_attempted"),
            ("three_points_made", "three_points_attempted"),
            ("free_throws_made", "free_throws_attempted"),
        )):
            stats[made] = rng.binomial(stats[attempted], conversion[:, :, index])
        stats = enforce_constraints(stats)
        wins = rng.random((width, simulations)) < probability[start:stop, None]
        fantasy[start:stop] = score_stat_arrays(stats, wins).astype(np.float32)
        component_sums["minutes"][start:stop] = minute_values.mean(axis=1)
        for name in OPPORTUNITY_COMPONENTS:
            component_sums[name][start:stop] = stats[name].mean(axis=1)
        for name in (
            "two_points_made", "three_points_made", "free_throws_made",
            "points", "total_rebounds",
        ):
            component_sums[name][start:stop] = stats[name].mean(axis=1)

    component_mean = pd.DataFrame(component_sums, index=frame.index)
    return fantasy, component_mean, sampled_minutes


def summarize_distribution(samples: np.ndarray) -> pd.DataFrame:
    values = np.asarray(samples, dtype=float)
    if values.ndim != 2 or values.shape[1] < 100:
        raise ValueError("distribution samples have the wrong shape")
    output: dict[str, np.ndarray] = {
        "expected_fp": values.mean(axis=1),
    }
    for quantile in QUANTILES:
        output[f"p{int(quantile * 100):02d}_fp"] = np.quantile(
            values, quantile, axis=1
        )
    output["median_fp"] = output["p50_fp"]
    denominator = values.shape[1] + 1.0
    for threshold in UPSIDE_THRESHOLDS:
        output[f"prob_fp_ge_{int(threshold)}"] = (
            (values >= threshold).sum(axis=1) + 0.5
        ) / denominator
    for threshold in DOWNSIDE_THRESHOLDS:
        output[f"prob_fp_le_{int(threshold)}"] = (
            (values <= threshold).sum(axis=1) + 0.5
        ) / denominator
    output["distribution_width"] = output["p90_fp"] - output["p10_fp"]
    output["p90_minus_p50"] = output["p90_fp"] - output["p50_fp"]
    output["p50_minus_p10"] = output["p50_fp"] - output["p10_fp"]
    output["p95_minus_expected"] = output["p95_fp"] - output["expected_fp"]
    return pd.DataFrame(output)


def crps_from_samples(actual: Sequence[float], samples: np.ndarray) -> float:
    """Exact empirical CRPS in O(n log n) per row."""

    y = np.asarray(actual, float)
    ordered = np.sort(np.asarray(samples, float), axis=1)
    n = ordered.shape[1]
    first = np.mean(np.abs(ordered - y[:, None]), axis=1)
    weights = 2.0 * np.arange(1, n + 1) - n - 1.0
    second = (ordered * weights[None, :]).sum(axis=1) / (n * n)
    return float(np.mean(first - second))


def inverse_quantile_samples(
    quantile_values: np.ndarray, simulations: int,
) -> np.ndarray:
    """Deterministically approximate a distribution from the Phase 6C grid."""

    quantiles = np.sort(np.asarray(quantile_values, float), axis=1)
    if quantiles.shape[1] != len(QUANTILES):
        raise ValueError("quantile grid has the wrong width")
    levels = np.asarray((0.005, *QUANTILES, 0.995), float)
    lower = quantiles[:, :1] - 1.5 * (quantiles[:, 1:2] - quantiles[:, :1])
    upper = quantiles[:, -1:] + 1.5 * (quantiles[:, -1:] - quantiles[:, -2:-1])
    knots = np.column_stack((lower, quantiles, upper))
    u = (np.arange(simulations, dtype=float) + 0.5) / simulations
    output = np.empty((len(quantiles), simulations), dtype=np.float32)
    for index in range(len(quantiles)):
        output[index] = np.interp(u, levels, knots[index])
    return output


def blend_samples(
    baseline_samples: np.ndarray, challenger_samples: np.ndarray, weight: float,
) -> np.ndarray:
    """Comonotonic convex blend used after OOF weight learning."""

    baseline = np.sort(np.asarray(baseline_samples, float), axis=1)
    challenger = np.sort(np.asarray(challenger_samples, float), axis=1)
    if baseline.shape != challenger.shape:
        raise ValueError("ensemble distributions have different shapes")
    return ((1.0 - weight) * baseline + weight * challenger).astype(np.float32)


def regression_metrics(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    y = np.asarray(actual, float)
    p = np.asarray(predicted, float)
    residual = y - p
    return {
        "rows": len(y),
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual ** 2))),
        "bias_actual_minus_prediction": float(residual.mean()),
        "pearson": _safe_correlation(pearsonr, y, p),
        "spearman": _safe_correlation(spearmanr, y, p),
    }


def distribution_metrics(
    actual: Sequence[float], summary: pd.DataFrame, samples: np.ndarray | None,
) -> dict[str, Any]:
    y = np.asarray(actual, float)
    quantile_rows: dict[str, Any] = {}
    for quantile in QUANTILES:
        column = f"p{int(quantile * 100):02d}_fp"
        prediction = summary[column].to_numpy(float)
        residual = y - prediction
        loss = np.maximum(quantile * residual, (quantile - 1.0) * residual)
        quantile_rows[column.replace("_fp", "")] = {
            "pinball_loss": float(loss.mean()),
            "coverage": float(np.mean(y <= prediction)),
            "coverage_error": float(np.mean(y <= prediction) - quantile),
        }
    probabilities: dict[str, Any] = {}
    for direction, thresholds in (("ge", UPSIDE_THRESHOLDS), ("le", DOWNSIDE_THRESHOLDS)):
        for threshold in thresholds:
            labels = y >= threshold if direction == "ge" else y <= threshold
            column = f"prob_fp_{direction}_{int(threshold)}"
            probabilities[column] = probability_metrics(labels, summary[column])
    return {
        "mean_pinball_loss": float(np.mean([
            row["pinball_loss"] for row in quantile_rows.values()
        ])),
        "quantiles": quantile_rows,
        "probabilities": probabilities,
        "crps": crps_from_samples(y, samples) if samples is not None else None,
    }


def probability_metrics(labels: Sequence[bool], probability: Sequence[float]) -> dict[str, Any]:
    y = np.asarray(labels, int)
    p = np.clip(np.asarray(probability, float), 1e-6, 1.0 - 1e-6)
    slope: float | None = None
    intercept: float | None = None
    if len(np.unique(y)) == 2:
        calibration = LogisticRegression(C=1e6, solver="lbfgs", random_state=29)
        calibration.fit(np.log(p / (1.0 - p)).reshape(-1, 1), y)
        slope = float(calibration.coef_[0, 0])
        intercept = float(calibration.intercept_[0])
    return {
        "rows": len(y), "positives": int(y.sum()),
        "positive_rate": float(y.mean()), "mean_probability": float(p.mean()),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "calibration_slope": slope, "calibration_intercept": intercept,
    }


def _safe_correlation(function: Any, left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3 or np.nanstd(left) == 0 or np.nanstd(right) == 0:
        return None
    value = function(left, right).statistic
    return float(value) if np.isfinite(value) else None
