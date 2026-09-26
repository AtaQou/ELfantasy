"""Attach realized outcomes to immutable live predictions and evaluate them."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id


def evaluate_prediction_run(
    prediction_run_id: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    attach_outcomes: bool = True,
    attached_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate only completed, genuinely later outcomes; prediction rows stay untouched."""

    initialize_database(database_path)
    now = attached_at or datetime.now(UTC)
    with connect_database(database_path) as connection:
        frame = connection.execute(
            """
            SELECT prediction.live_prediction_id, prediction.scenario_id,
                   prediction.canonical_game_id AS game_id,
                   prediction.canonical_player_id AS player_id,
                   prediction.prediction_generated_at,
                   prediction.scheduled_tip_time,
                   prediction.expected_fp_before_adjustment,
                   prediction.expected_fp,
                   prediction.prediction_status,
                   target.player_game_id, target.actual_fantasy_points,
                   target.target_minutes AS actual_minutes,
                   game.played, game.game_date
            FROM live_player_predictions AS prediction
            JOIN current_games AS game ON game.canonical_game_id=prediction.canonical_game_id
            LEFT JOIN ml_player_game_targets_v1 AS target
              ON target.game_id=prediction.canonical_game_id
             AND target.player_id=prediction.canonical_player_id
            WHERE prediction.prediction_run_id=?
            ORDER BY prediction.scenario_id, prediction.canonical_game_id,
                     prediction.canonical_player_id
            """,
            [prediction_run_id],
        ).df()
        if frame.empty:
            raise ValueError(f"unknown or empty prediction run: {prediction_run_id}")
        eligible = frame[
            frame.played.fillna(False)
            & frame.actual_fantasy_points.notna()
            & frame.prediction_status.eq("SCORED")
            & (frame.prediction_generated_at < frame.game_date)
        ].copy()
        if attach_outcomes:
            for row in eligible.itertuples():
                payload = {
                    "prediction": row.live_prediction_id,
                    "actual_fp": float(row.actual_fantasy_points),
                    "actual_minutes": _optional_float(row.actual_minutes),
                    "source_player_game_id": row.player_game_id,
                }
                fingerprint = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                attachment_id = stable_id(
                    "live_prediction_outcome", row.live_prediction_id, fingerprint
                )
                connection.execute(
                    """
                    INSERT INTO live_prediction_outcomes VALUES (
                      ?, ?, ?, ?, 'CANONICAL_VERIFIED_TARGET', ?, ?, ?
                    ) ON CONFLICT (live_prediction_id) DO NOTHING
                    """,
                    [attachment_id, row.live_prediction_id,
                     float(row.actual_fantasy_points), _optional_float(row.actual_minutes),
                     row.player_game_id, now, fingerprint],
                )
    if eligible.empty:
        return {
            "prediction_run_id": prediction_run_id,
            "status": "NO_COMPLETED_PROSPECTIVE_OUTCOMES",
            "eligible_rows": 0,
            "metrics": None,
        }
    return {
        "prediction_run_id": prediction_run_id,
        "status": "EVALUATED",
        "eligible_rows": len(eligible),
        "metrics": {
            "availability_adjusted": _metrics(eligible.actual_fantasy_points, eligible.expected_fp),
            "unadjusted": _metrics(
                eligible.actual_fantasy_points, eligible.expected_fp_before_adjustment
            ),
            "ranking_capture": _ranking_capture(eligible),
        },
    }


def _metrics(actual: pd.Series, predicted: pd.Series) -> dict[str, float | None]:
    valid = actual.notna() & predicted.notna()
    a = actual[valid].to_numpy(float)
    p = predicted[valid].to_numpy(float)
    if not len(a):
        return {"rows": 0, "mae": None, "rmse": None, "spearman": None, "pearson": None}
    return {
        "rows": len(a),
        "mae": float(np.mean(np.abs(a - p))),
        "rmse": float(np.sqrt(np.mean((a - p) ** 2))),
        "spearman": _correlation(a, p, "spearman"),
        "pearson": _correlation(a, p, "pearson"),
    }


def _correlation(actual: np.ndarray, predicted: np.ndarray, method: str) -> float | None:
    if len(actual) < 2 or np.std(actual) == 0 or np.std(predicted) == 0:
        return None
    value = pd.Series(actual).corr(pd.Series(predicted), method=method)
    return float(value) if pd.notna(value) else None


def _ranking_capture(frame: pd.DataFrame) -> dict[str, float | int]:
    captures: list[float] = []
    for _, group in frame.groupby(["scenario_id", "game_id"], sort=False):
        count = min(10, len(group))
        if count == 0:
            continue
        actual = set(group.nlargest(count, "actual_fantasy_points").live_prediction_id)
        predicted = set(group.nlargest(count, "expected_fp").live_prediction_id)
        captures.append(len(actual & predicted) / count)
    return {
        "groups": len(captures),
        "mean_top10_overlap": float(np.mean(captures)) if captures else 0.0,
    }


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None
