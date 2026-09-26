"""Descriptive Phase 3B diagnostics and same-population baseline recalibration."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .baselines import BASELINE_COLUMNS, _metric_rows
from .rich_features import RICH_SAMPLE_ROOT


FAMILY_PREFIXES = {
    "rotation": ("rot_",),
    "onoff": ("onoff_",),
    "lineup": ("lineup_",),
    "teammate": ("teammate_",),
    "player_shot": ("shot_",),
    "opponent_shot": ("opp_shot_",),
    "interaction": ("interaction_",),
}


def generate_rich_diagnostics(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    sample_root: Path = RICH_SAMPLE_ROOT,
) -> dict[str, Any]:
    with connect_database(database_path, read_only=True) as connection:
        evaluation = connection.execute(
            "SELECT * FROM ml_phase3b_primary_evaluation_v1 "
            "ORDER BY target_game_time, game_id, player_id"
        ).df()
        all_rich = connection.execute(
            "SELECT * FROM ml_player_game_rich_features_v1 "
            "ORDER BY target_game_time, game_id, player_id"
        ).df()
        schema = [
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info('ml_player_game_rich_features_v1')"
            ).fetchall()
        ]
    feature_columns = _feature_columns(schema)
    evaluation["history_window_seasons"] = 8
    metrics = _metric_rows(evaluation, dimensions=(), history_windows=(8,))
    ranking = _ranking_primary(evaluation)
    evaluation["phase3a_blend_residual"] = (
        evaluation["actual_fantasy_points"]
        - evaluation["prediction_season_recent_blend"]
    )
    correlation_frame = evaluation[
        [*feature_columns, "actual_fantasy_points", "phase3a_blend_residual"]
    ].select_dtypes(include=[np.number, "bool"])
    target = correlation_frame["actual_fantasy_points"]
    residual = correlation_frame["phase3a_blend_residual"]
    pearson_target = correlation_frame.corrwith(target, method="pearson")
    pearson_residual = correlation_frame.corrwith(residual, method="pearson")
    ranked = correlation_frame.rank(method="average", na_option="keep")
    spearman_target = ranked.corrwith(ranked["actual_fantasy_points"])
    spearman_residual = ranked.corrwith(ranked["phase3a_blend_residual"])
    correlations: list[dict[str, Any]] = []
    for feature in feature_columns:
        correlations.append(
            {
                "feature": feature,
                "family": _family(feature),
                "rows": int(evaluation[feature].notna().sum()),
                "target_pearson": _series_value(pearson_target, feature),
                "target_spearman": _series_value(spearman_target, feature),
                "blend_residual_pearson": _series_value(pearson_residual, feature),
                "blend_residual_spearman": _series_value(spearman_residual, feature),
            }
        )
    family_missingness = []
    for family, prefixes in FAMILY_PREFIXES.items():
        columns = [
            column for column in feature_columns
            if column.startswith(prefixes)
        ]
        family_missingness.append(
            {
                "family": family,
                "feature_count": len(columns),
                "full_rows": len(all_rich),
                "full_missing_rate": _missing_rate(all_rich, columns),
                "primary_rows": len(evaluation),
                "primary_missing_rate": _missing_rate(evaluation, columns),
            }
        )
    redundancy = _redundancy(evaluation, feature_columns)
    season_stability = _season_stability(evaluation, feature_columns)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_semantics": "E2022_E2025_OFFICIAL_TARGET_CONDITIONAL_ON_PLAYING_ALL_MAJOR_RICH_AVAILABLE",
        "primary_rows": len(evaluation),
        "feature_count": len(feature_columns),
        "feature_count_by_family": {
            family: sum(_family(column) == family for column in feature_columns)
            for family in FAMILY_PREFIXES
        },
        "baseline_metrics": metrics,
        "ranking_metrics": [
            row for row in ranking if row["history_window_seasons"] == 8
        ],
        "family_missingness": family_missingness,
        "feature_correlations": correlations,
        "top_residual_correlations": sorted(
            correlations,
            key=lambda row: abs(row["blend_residual_spearman"] or 0),
            reverse=True,
        )[:30],
        "redundancy": redundancy,
        "season_stability": season_stability,
        "interpretation": (
            "Correlations are descriptive screening evidence, not proof of "
            "incremental predictive value. Controlled Phase 4 models are required."
        ),
    }
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "rich_diagnostics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _feature_columns(schema: list[str]) -> list[str]:
    columns = []
    for column in schema:
        if not any(column.startswith(prefixes) for prefixes in FAMILY_PREFIXES.values()):
            continue
        if column.endswith(("_version", "_source_max_game_time_us")):
            continue
        columns.append(column)
    return columns


def _family(feature: str) -> str:
    for family, prefixes in FAMILY_PREFIXES.items():
        if feature.startswith(prefixes):
            return family
    raise KeyError(feature)


def _series_value(series: pd.Series, key: str) -> float | None:
    if key not in series.index:
        return None
    value = float(series.loc[key])
    return value if math.isfinite(value) else None


def _ranking_primary(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for baseline, column in BASELINE_COLUMNS.items():
        captured_10_20 = available_10 = captured_20_30 = available_20 = groups = 0
        for _, group in frame.groupby(["season", "round_number"], dropna=False):
            clean = group.dropna(subset=["actual_fantasy_points", column])
            if clean.empty:
                continue
            groups += 1
            actual10 = set(clean.nlargest(min(10, len(clean)), "actual_fantasy_points")["player_game_id"])
            predicted20 = set(clean.nlargest(min(20, len(clean)), column)["player_game_id"])
            actual20 = set(clean.nlargest(min(20, len(clean)), "actual_fantasy_points")["player_game_id"])
            predicted30 = set(clean.nlargest(min(30, len(clean)), column)["player_game_id"])
            captured_10_20 += len(actual10 & predicted20); available_10 += len(actual10)
            captured_20_30 += len(actual20 & predicted30); available_20 += len(actual20)
        rows.append({
            "history_window_seasons": 8, "baseline": baseline,
            "round_groups": groups,
            "top10_actual_in_predicted_top20": captured_10_20 / available_10,
            "top20_actual_in_predicted_top30": captured_20_30 / available_20,
        })
    return rows


def _missing_rate(frame: pd.DataFrame, columns: list[str]) -> float | None:
    if not columns or frame.empty:
        return None
    return float(frame[columns].isna().to_numpy().mean())


def _redundancy(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    numeric = frame[columns].select_dtypes(include=[np.number, "bool"])
    exact: list[list[str]] = []
    signatures: dict[int, list[str]] = {}
    for column in numeric.columns:
        signature = int(pd.util.hash_pandas_object(
            numeric[column], index=False
        ).sum())
        signatures.setdefault(signature, []).append(str(column))
    for candidates in signatures.values():
        if len(candidates) < 2:
            continue
        left = candidates[0]
        for right in candidates[1:]:
            if numeric[left].equals(numeric[right]):
                exact.append([left, right])
    step = max(1, len(numeric) // 5_000)
    correlation = numeric.iloc[::step].corr(method="pearson", min_periods=100)
    near: list[dict[str, Any]] = []
    for left_index, left in enumerate(correlation.columns):
        for right in correlation.columns[left_index + 1 :]:
            value = correlation.loc[left, right]
            if pd.notna(value) and abs(float(value)) >= 0.98:
                near.append(
                    {"left": str(left), "right": str(right), "pearson": float(value)}
                )
    low_variance = [
        {
            "feature": str(column),
            "non_null": int(numeric[column].notna().sum()),
            "unique": int(numeric[column].nunique(dropna=True)),
        }
        for column in numeric
        if numeric[column].nunique(dropna=True) <= 2
    ]
    high_missing = [
        {"feature": column, "missing_rate": float(frame[column].isna().mean())}
        for column in columns
        if frame[column].isna().mean() >= 0.5
    ]
    return {
        "exact_duplicates": exact,
        "near_perfect_correlations": sorted(
            near, key=lambda row: abs(row["pearson"]), reverse=True
        ),
        "low_variance": low_variance,
        "at_least_50_percent_missing": high_missing,
    }


def _season_stability(
    frame: pd.DataFrame, columns: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for season, group in frame.groupby("season", observed=True):
        for column in columns:
            series = group[column]
            if not pd.api.types.is_numeric_dtype(series):
                continue
            rows.append(
                {
                    "season": str(season), "feature": column,
                    "rows": len(group), "missing_rate": float(series.isna().mean()),
                    "mean": _finite(series.mean()), "std": _finite(series.std()),
                }
            )
    return rows


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
