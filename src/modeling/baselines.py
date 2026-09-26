"""Transparent non-ML baselines and chronological Phase 3A evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .core_features import DEFAULT_DERIVED_ROOT, DEFAULT_SAMPLE_ROOT


BASELINE_VERSION = "phase3a_transparent_baselines_v1"
FIXED_COLD_START_FALLBACK = 10.0
HISTORY_WINDOWS = (3, 5, 8)
BASELINE_COLUMNS = {
    "season_average": "prediction_season_average",
    "last_3": "prediction_last_3",
    "last_5": "prediction_last_5",
    "ewma": "prediction_ewma",
    "fp_per_min_x_minutes": "prediction_fp_per_min_x_minutes",
    "season_recent_blend": "prediction_season_recent_blend",
}


@dataclass(frozen=True, slots=True)
class BacktestResult:
    generated_at: str
    baseline_version: str
    evaluation_semantics: str
    row_count: int
    primary_history_window_seasons: int
    best_mae_baseline: str
    metrics: list[dict[str, Any]]
    ranking_metrics: list[dict[str, Any]]
    by_season: list[dict[str, Any]]
    by_minutes: list[dict[str, Any]]
    by_history_depth: list[dict[str, Any]]
    by_era: list[dict[str, Any]]
    history_window_comparison: list[dict[str, Any]]


def generate_baseline_predictions(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
) -> int:
    """Generate six fixed, non-ML predictions from pregame features only."""

    frames: list[pd.DataFrame] = []
    with connect_database(database_path) as connection:
        source = connection.execute(
            """
            SELECT
                player_game_id, season, season_start_year, round_number,
                game_id, target_game_time, feature_cutoff_time, player_id,
                team_id, opponent_team_id, home_away, target_minutes,
                standardized_fantasy_points, actual_fantasy_points,
                target_rule_version, target_rule_status,
                feature_pipeline_version, career_el_games_before,
                season_games_before, fantasy_id, fantasy_position,
                fantasy_matchday, fantasy_credits_pre_matchday,
                fantasy_price_status, season_fp_avg_before,
                last_3_fp_avg, last_5_fp_avg, fp_ewma,
                last_5_fp_per_min, minutes_ewma,
                career_el_fp_avg_before_3s,
                career_el_fp_avg_before_5s,
                career_el_fp_avg_before_8s
            FROM ml_player_game_core_features_with_fantasy_v1
            ORDER BY target_game_time, game_id, player_id
            """
        ).df()
        for history_window in HISTORY_WINDOWS:
            frame = source.copy()
            career_column = f"career_el_fp_avg_before_{history_window}s"
            fallback = (
                frame["season_fp_avg_before"]
                .combine_first(frame[career_column])
                .fillna(FIXED_COLD_START_FALLBACK)
            )
            frame["history_window_seasons"] = history_window
            frame["prediction_season_average"] = fallback
            frame["prediction_last_3"] = frame["last_3_fp_avg"].combine_first(
                fallback
            )
            frame["prediction_last_5"] = frame["last_5_fp_avg"].combine_first(
                fallback
            )
            frame["prediction_ewma"] = frame["fp_ewma"].combine_first(fallback)
            rate_minutes = frame["last_5_fp_per_min"] * frame["minutes_ewma"]
            frame["prediction_fp_per_min_x_minutes"] = rate_minutes.combine_first(
                frame["prediction_last_5"]
            )
            both = frame["season_fp_avg_before"].notna() & frame[
                "last_5_fp_avg"
            ].notna()
            frame["prediction_season_recent_blend"] = fallback
            frame.loc[both, "prediction_season_recent_blend"] = 0.5 * (
                frame.loc[both, "season_fp_avg_before"]
                + frame.loc[both, "last_5_fp_avg"]
            )
            frames.append(frame)
        predictions = pd.concat(frames, ignore_index=True)
        predictions["baseline_version"] = BASELINE_VERSION
        predictions["evaluation_semantics"] = "CONDITIONAL_ON_PLAYING"
        keep = [
            "player_game_id",
            "season",
            "season_start_year",
            "round_number",
            "game_id",
            "target_game_time",
            "feature_cutoff_time",
            "player_id",
            "team_id",
            "opponent_team_id",
            "home_away",
            "target_minutes",
            "standardized_fantasy_points",
            "actual_fantasy_points",
            "target_rule_version",
            "target_rule_status",
            "feature_pipeline_version",
            "career_el_games_before",
            "season_games_before",
            "fantasy_id",
            "fantasy_position",
            "fantasy_matchday",
            "fantasy_credits_pre_matchday",
            "fantasy_price_status",
            "history_window_seasons",
            *BASELINE_COLUMNS.values(),
            "baseline_version",
            "evaluation_semantics",
        ]
        predictions = predictions[keep]
        connection.register("phase3_predictions_frame", predictions)
        connection.execute(
            """
            CREATE OR REPLACE TABLE ml_baseline_predictions_v1 AS
            SELECT * FROM phase3_predictions_frame
            ORDER BY history_window_seasons, target_game_time, game_id, player_id
            """
        )
        connection.unregister("phase3_predictions_frame")
        connection.execute(
            """
            CREATE OR REPLACE VIEW ml_baseline_predictions AS
            SELECT * FROM ml_baseline_predictions_v1
            """
        )
        select_predictions = ",\n".join(
            f"prediction.{column}, "
            f"prediction.{column} / fantasy_credits_pre_matchday "
            f"AS {column}_per_credit"
            for column in BASELINE_COLUMNS.values()
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE ml_e2025_fantasy_evaluation_v1 AS
            SELECT
                prediction.fantasy_matchday AS matchday,
                prediction.round_number,
                prediction.game_id,
                prediction.target_game_time,
                prediction.player_id,
                prediction.fantasy_id,
                prediction.standardized_fantasy_points AS actual_fantasy_points,
                prediction.fantasy_credits_pre_matchday AS credits,
                prediction.fantasy_position AS position,
                prediction.team_id AS team,
                prediction.opponent_team_id AS opponent,
                {select_predictions},
                prediction.target_rule_version,
                prediction.feature_pipeline_version,
                prediction.baseline_version
            FROM ml_baseline_predictions_v1 AS prediction
            WHERE prediction.season = 'E2025'
              AND prediction.history_window_seasons = 8
              AND prediction.fantasy_credits_pre_matchday IS NOT NULL
            ORDER BY matchday, target_game_time, game_id, player_id
            """
        )
        derived_root.mkdir(parents=True, exist_ok=True)
        for table in (
            "ml_baseline_predictions_v1",
            "ml_e2025_fantasy_evaluation_v1",
        ):
            connection.execute(
                f"COPY (SELECT * FROM {table}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(derived_root / f"{table}.parquet")],
            )
    return len(predictions)


def evaluate_baselines(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> BacktestResult:
    with connect_database(database_path, read_only=True) as connection:
        frame = connection.execute(
            """
            SELECT * FROM ml_baseline_predictions_v1
            ORDER BY history_window_seasons, target_game_time, game_id, player_id
            """
        ).df()
    metrics = _metric_rows(frame, dimensions=())
    primary_metrics = [
        row for row in metrics if row["history_window_seasons"] == 8
    ]
    best = min(primary_metrics, key=lambda row: row["mae"])["baseline"]
    ranking = _ranking_rows(frame)
    by_season = _metric_rows(frame, dimensions=("season",), history_windows=(8,))
    frame["minutes_band"] = pd.cut(
        frame["target_minutes"],
        bins=[-np.inf, 10, 20, 25, 30, np.inf],
        labels=["<10", "10-20", "20-25", "25-30", "30+"],
        right=False,
    ).astype(str)
    by_minutes = _metric_rows(
        frame, dimensions=("minutes_band",), history_windows=(8,)
    )
    frame["history_depth"] = pd.cut(
        frame["career_el_games_before"],
        bins=[-1, 0, 2, 4, 9, np.inf],
        labels=["cold_start_0", "1-2", "3-4", "5-9", "10+"],
    ).astype(str)
    by_history = _metric_rows(
        frame, dimensions=("history_depth",), history_windows=(8,)
    )
    frame["era"] = np.where(
        frame["season_start_year"] <= 2021, "E2018-E2021", "E2022-E2025"
    )
    by_era = _metric_rows(frame, dimensions=("era",), history_windows=(8,))
    comparison = _metric_rows(frame, dimensions=())
    result = BacktestResult(
        generated_at=datetime.now(UTC).isoformat(),
        baseline_version=BASELINE_VERSION,
        evaluation_semantics="CONDITIONAL_ON_PLAYING",
        row_count=int((frame["history_window_seasons"] == 8).sum()),
        primary_history_window_seasons=8,
        best_mae_baseline=str(best),
        metrics=primary_metrics,
        ranking_metrics=[
            row for row in ranking if row["history_window_seasons"] == 8
        ],
        by_season=by_season,
        by_minutes=by_minutes,
        by_history_depth=by_history,
        by_era=by_era,
        history_window_comparison=comparison,
    )
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "baseline_results.json").write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def run_baseline_backtest(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> BacktestResult:
    generate_baseline_predictions(database_path, derived_root=derived_root)
    return evaluate_baselines(database_path, sample_root=sample_root)


def _metric_rows(
    frame: pd.DataFrame,
    *,
    dimensions: tuple[str, ...],
    history_windows: Iterable[int] = HISTORY_WINDOWS,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for history_window in history_windows:
        selected = frame[frame["history_window_seasons"] == history_window]
        if dimensions:
            grouper: Any = dimensions[0] if len(dimensions) == 1 else list(dimensions)
            groups = selected.groupby(grouper, observed=True, dropna=False)
        else:
            groups = [((), selected)]
        for keys, group in groups:
            if not isinstance(keys, tuple):
                keys = (keys,)
            dimension_values = dict(zip(dimensions, keys))
            actual = group["standardized_fantasy_points"].astype(float)
            for baseline, column in BASELINE_COLUMNS.items():
                prediction = group[column].astype(float)
                valid = actual.notna() & prediction.notna()
                left, right = actual[valid], prediction[valid]
                error = right - left
                results.append(
                    {
                        **{key: _json_value(value) for key, value in dimension_values.items()},
                        "history_window_seasons": history_window,
                        "baseline": baseline,
                        "rows": int(valid.sum()),
                        "mae": float(error.abs().mean()),
                        "rmse": float(math.sqrt((error**2).mean())),
                        "spearman": _correlation(left.rank(method="average"), right.rank(method="average")),
                        "pearson": _correlation(left, right),
                    }
                )
    return results


def _ranking_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for history_window in HISTORY_WINDOWS:
        selected = frame[frame["history_window_seasons"] == history_window]
        for baseline, column in BASELINE_COLUMNS.items():
            captured_10_20 = available_10 = captured_20_30 = available_20 = 0
            groups = 0
            for _, group in selected.groupby(["season", "round_number"], dropna=False):
                clean = group.dropna(subset=["standardized_fantasy_points", column])
                if clean.empty:
                    continue
                groups += 1
                actual10 = set(clean.nlargest(min(10, len(clean)), "standardized_fantasy_points")["player_game_id"])
                predicted20 = set(clean.nlargest(min(20, len(clean)), column)["player_game_id"])
                actual20 = set(clean.nlargest(min(20, len(clean)), "standardized_fantasy_points")["player_game_id"])
                predicted30 = set(clean.nlargest(min(30, len(clean)), column)["player_game_id"])
                captured_10_20 += len(actual10 & predicted20)
                available_10 += len(actual10)
                captured_20_30 += len(actual20 & predicted30)
                available_20 += len(actual20)
            results.append(
                {
                    "history_window_seasons": history_window,
                    "baseline": baseline,
                    "round_groups": groups,
                    "top10_actual_in_predicted_top20": captured_10_20 / available_10,
                    "top20_actual_in_predicted_top30": captured_20_30 / available_20,
                }
            )
    return results


def _correlation(left: pd.Series, right: pd.Series) -> float | None:
    if len(left) < 2 or left.nunique() < 2 or right.nunique() < 2:
        return None
    value = float(np.corrcoef(left.to_numpy(), right.to_numpy())[0, 1])
    return value if math.isfinite(value) else None


def _json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value
