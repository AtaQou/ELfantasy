"""Leakage-safe, secondary Fantasy price intelligence for Phase 8B.

The frozen player/strategy objective is never changed here.  The model is trained
only on validated E2025 point-in-time market snapshots and predicts the next
published price from information available before the current Matchday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRICE_MODEL_VERSION = "phase8b_e2025_next_price_ridge_v1"
DEFAULT_E2025_PREDICTIONS = (
    PROJECT_ROOT / "data" / "derived" / "phase7" / "research"
    / "e2025_full_market_predictions.parquet"
)
DEFAULT_ARTIFACT_PATH = (
    PROJECT_ROOT / "data" / "derived" / "phase8b" / "price_model"
    / f"{PRICE_MODEL_VERSION}.json"
)
PRICE_FEATURES = (
    "credits", "credit_delta_1", "credit_delta_2", "expected_fp",
    "expected_minutes", "previous_actual_fp", "previous_actual_minutes",
    "recent_actual_fp3", "matchday_number",
)
CHRONOLOGICAL_FOLDS = ((20, 21, 25), (25, 26, 30), (30, 31, 37))


@dataclass(frozen=True, slots=True)
class PriceAudit:
    acceptance_status: str
    training_rows: int
    validation: Mapping[str, Any]
    leading_inputs: tuple[Mapping[str, Any], ...]
    artifact_fingerprint: str
    artifact_path: str


def build_price_training_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    predictions_path: Path | str = DEFAULT_E2025_PREDICTIONS,
) -> pd.DataFrame:
    """Create an E2025-only, point-in-time frame with a one-Matchday-ahead target."""

    with connect_database(database_path, read_only=True) as connection:
        market = connection.execute(
            """
            SELECT season_code, matchday_number, player_id, fantasy_entity_id,
                   fantasy_name, credits, observed_at
            FROM leakage_safe_fantasy_market
            WHERE season_code='E2025'
              AND credits_valid_pre_matchday
              AND credits IS NOT NULL
            QUALIFY row_number() OVER (
                PARTITION BY season_code, matchday_number, fantasy_entity_id
                ORDER BY observed_at DESC, snapshot_record_id DESC
            )=1
            """
        ).df()
        prediction = connection.execute(
            """
            SELECT round_number AS matchday_number, player_id,
                   avg(expected_fp) AS expected_fp,
                   avg(baseline_expected_minutes) AS expected_minutes,
                   avg(actual_fp) FILTER (WHERE played) AS actual_fp,
                   avg(actual_minutes) FILTER (WHERE played) AS actual_minutes
            FROM read_parquet(?)
            WHERE season='E2025'
            GROUP BY 1, 2
            """,
            [str(predictions_path)],
        ).df()
    frame = market.merge(prediction, how="left", on=["matchday_number", "player_id"])
    frame = frame.sort_values(["player_id", "matchday_number"]).reset_index(drop=True)
    grouped = frame.groupby("player_id", dropna=False)
    frame["previous_credits"] = grouped["credits"].shift(1)
    frame["previous2_credits"] = grouped["credits"].shift(2)
    frame["next_credits"] = grouped["credits"].shift(-1)
    frame["next_matchday"] = grouped["matchday_number"].shift(-1)
    frame["previous_actual_fp"] = grouped["actual_fp"].shift(1)
    frame["previous_actual_minutes"] = grouped["actual_minutes"].shift(1)
    frame["recent_actual_fp3"] = grouped["actual_fp"].transform(
        lambda values: values.shift(1).rolling(3, min_periods=1).mean()
    )
    frame["credit_delta_1"] = frame["credits"] - frame["previous_credits"]
    frame["credit_delta_2"] = frame["previous_credits"] - frame["previous2_credits"]
    frame["target_delta"] = frame["next_credits"] - frame["credits"]
    return frame[
        (frame["next_matchday"] == frame["matchday_number"] + 1)
        & frame["player_id"].notna()
    ].copy()


def audit_and_train_price_model(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    predictions_path: Path | str = DEFAULT_E2025_PREDICTIONS,
    artifact_path: Path | str = DEFAULT_ARTIFACT_PATH,
    created_at: datetime | None = None,
) -> PriceAudit:
    """Validate chronologically, fit the secondary model, and persist its manifest."""

    initialize_database(database_path)
    data = build_price_training_frame(database_path, predictions_path)
    regression = _regression_pipeline()
    classification = _classification_pipeline()
    fold_rows: list[dict[str, Any]] = []
    probability_rows: list[dict[str, float]] = []
    for train_end, test_start, test_end in CHRONOLOGICAL_FOLDS:
        train = data[data["matchday_number"] <= train_end]
        test = data[data["matchday_number"].between(test_start, test_end)]
        if train.empty or test.empty:
            continue
        no_change = test["credits"].to_numpy(float)
        fitted = clone(regression).fit(train[list(PRICE_FEATURES)], train["next_credits"])
        predicted = fitted.predict(test[list(PRICE_FEATURES)])
        fold_rows.extend((
            _fold_metrics("no_change", test_start, test_end, test["next_credits"], no_change),
            _fold_metrics("ridge", test_start, test_end, test["next_credits"], predicted),
        ))
        for label, positive in (("increase", train["target_delta"] > 0),
                                ("decrease", train["target_delta"] < 0)):
            classifier = clone(classification).fit(train[list(PRICE_FEATURES)], positive.astype(int))
            probabilities = classifier.predict_proba(test[list(PRICE_FEATURES)])[:, 1]
            actual = (test["target_delta"] > 0) if label == "increase" else (
                test["target_delta"] < 0
            )
            probability_rows.extend({
                "label": label, "actual": float(value), "probability": float(probability)
            } for value, probability in zip(actual, probabilities, strict=True))

    fold_frame = pd.DataFrame(fold_rows)
    pooled = {
        name: {
            "mae": float(np.average(group["mae"], weights=group["rows"])),
            "rmse": float(np.sqrt(np.average(group["rmse"] ** 2, weights=group["rows"]))),
        }
        for name, group in fold_frame.groupby("model")
    }
    ridge_folds = fold_frame[fold_frame["model"].eq("ridge")].set_index("fold")
    base_folds = fold_frame[fold_frame["model"].eq("no_change")].set_index("fold")
    rmse_wins = int((ridge_folds["rmse"] < base_folds["rmse"]).sum())
    mae_wins = int((ridge_folds["mae"] < base_folds["mae"]).sum())
    improves_rmse = pooled["ridge"]["rmse"] < pooled["no_change"]["rmse"] and (
        rmse_wins == len(CHRONOLOGICAL_FOLDS)
    )
    improves_mae = pooled["ridge"]["mae"] < pooled["no_change"]["mae"]
    acceptance = (
        "ACCEPTED" if improves_rmse and improves_mae else
        "ACCEPTED_SECONDARY_RMSE_ONLY" if improves_rmse else "REJECTED"
    )

    fitted_regression = regression.fit(data[list(PRICE_FEATURES)], data["next_credits"])
    fitted_increase = classification.fit(
        data[list(PRICE_FEATURES)], (data["target_delta"] > 0).astype(int)
    )
    fitted_decrease = clone(classification).fit(
        data[list(PRICE_FEATURES)], (data["target_delta"] < 0).astype(int)
    )
    probability_frame = pd.DataFrame(probability_rows)
    probability_metrics = {}
    for label, group in probability_frame.groupby("label"):
        rate = float(group["actual"].mean())
        probability_metrics[label] = {
            "observed_rate": rate,
            "brier": float(brier_score_loss(group["actual"], group["probability"])),
            "constant_rate_brier": float(rate * (1.0 - rate)),
        }
    model_payload = {
        "version": PRICE_MODEL_VERSION,
        "training_season": "E2025",
        "training_cutoff_matchday": 37,
        "training_rows": len(data),
        "features": list(PRICE_FEATURES),
        "regression": _linear_payload(fitted_regression),
        "increase_classifier": _linear_payload(fitted_increase),
        "decrease_classifier": _linear_payload(fitted_decrease),
        "validation": {
            "folds": fold_rows,
            "pooled": pooled,
            "rmse_fold_wins": rmse_wins,
            "mae_fold_wins": mae_wins,
            "probability": probability_metrics,
        },
        "acceptance_status": acceptance,
        "purpose": "secondary_price_intelligence_only",
        "primary_phase7_objective_changed": False,
    }
    fingerprint = _fingerprint(model_payload)
    model_payload["artifact_fingerprint"] = fingerprint
    destination = Path(artifact_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing.get("artifact_fingerprint") != fingerprint:
            raise ValueError(f"immutable price artifact conflict: {destination}")
    else:
        destination.write_text(json.dumps(model_payload, indent=2) + "\n", encoding="utf-8")
    now = created_at or datetime.now(UTC)
    leading = tuple(sorted(
        ({"feature": feature, "absolute_standardized_coefficient": abs(coefficient)}
         for feature, coefficient in zip(
             PRICE_FEATURES, model_payload["regression"]["coefficient"], strict=True
         )),
        key=lambda item: item["absolute_standardized_coefficient"], reverse=True,
    )[:5])
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO fantasy_price_model_manifests VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT DO NOTHING
            """,
            [PRICE_MODEL_VERSION, "E2025", 37, len(data), json.dumps(PRICE_FEATURES),
             json.dumps(fold_rows), json.dumps(model_payload["validation"]),
             json.dumps({key: model_payload[key] for key in (
                 "regression", "increase_classifier", "decrease_classifier"
             )}), acceptance, fingerprint, str(destination), now],
        )
    return PriceAudit(acceptance, len(data), model_payload["validation"], leading,
                      fingerprint, str(destination))


def load_price_artifact(path: Path | str = DEFAULT_ARTIFACT_PATH) -> dict[str, Any] | None:
    source = Path(path)
    if not source.is_file():
        return None
    payload = json.loads(source.read_text(encoding="utf-8"))
    fingerprint = payload.pop("artifact_fingerprint", None)
    if fingerprint != _fingerprint(payload):
        raise ValueError("Phase 8B price artifact fingerprint mismatch")
    payload["artifact_fingerprint"] = fingerprint
    return payload


def register_price_artifact(
    artifact: Mapping[str, Any], database_path: Path | str = DEFAULT_DATABASE_PATH,
    *, artifact_path: Path | str = DEFAULT_ARTIFACT_PATH,
) -> None:
    """Register an already fingerprint-verified artifact in a target database."""

    initialize_database(database_path)
    validation = artifact["validation"]
    parameters = {key: artifact[key] for key in (
        "regression", "increase_classifier", "decrease_classifier"
    )}
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO fantasy_price_model_manifests VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT DO NOTHING
            """,
            [artifact["version"], "E2025", 37, int(artifact["training_rows"]),
             json.dumps(artifact["features"]), json.dumps(validation["folds"]),
             json.dumps(validation), json.dumps(parameters), artifact["acceptance_status"],
             artifact["artifact_fingerprint"], str(artifact_path), datetime.now(UTC)],
        )


def predict_price_rows(
    rows: Sequence[Mapping[str, Any]], artifact: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if artifact.get("acceptance_status") == "REJECTED":
        return []
    matrix = np.asarray([
        [_optional_float(row.get(feature)) for feature in PRICE_FEATURES] for row in rows
    ], dtype=float)
    expected = _predict_linear(matrix, artifact["regression"], logistic=False)
    increase = _predict_linear(matrix, artifact["increase_classifier"], logistic=True)
    decrease = _predict_linear(matrix, artifact["decrease_classifier"], logistic=True)
    output = []
    for row, next_price, probability_up, probability_down in zip(
        rows, expected, increase, decrease, strict=True
    ):
        current = float(row["credits"])
        output.append({
            "entity_id": str(row["entity_id"]),
            "player_id": str(row.get("player_id") or ""),
            "current_credits": current,
            "expected_next_price": float(max(0.0, next_price)),
            "expected_credit_change": float(next_price - current),
            "probability_increase": float(probability_up),
            "probability_decrease": float(probability_down),
            "price_model_version": str(artifact["version"]),
            "price_model_confidence": (
                "LOW" if artifact.get("acceptance_status") == "ACCEPTED_SECONDARY_RMSE_ONLY"
                else "VALIDATED"
            ),
            "price_primary_objective": False,
            "price_input": {feature: row.get(feature) for feature in PRICE_FEATURES},
        })
    return output


def _regression_pipeline() -> Pipeline:
    return Pipeline((
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", Ridge(alpha=10.0)),
    ))


def _classification_pipeline() -> Pipeline:
    return Pipeline((
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=0.25, max_iter=1000, random_state=8)),
    ))


def _fold_metrics(
    model: str, start: int, end: int, actual: pd.Series, predicted: np.ndarray,
) -> dict[str, Any]:
    return {
        "fold": f"MD{start}-{end}", "model": model, "rows": len(actual),
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(mean_squared_error(actual, predicted) ** 0.5),
    }


def _linear_payload(pipeline: Pipeline) -> dict[str, Any]:
    return {
        "imputer_median": pipeline.named_steps["impute"].statistics_.tolist(),
        "scaler_mean": pipeline.named_steps["scale"].mean_.tolist(),
        "scaler_scale": pipeline.named_steps["scale"].scale_.tolist(),
        "coefficient": pipeline.named_steps["model"].coef_.reshape(-1).tolist(),
        "intercept": float(np.asarray(pipeline.named_steps["model"].intercept_).reshape(-1)[0]),
    }


def _predict_linear(matrix: np.ndarray, payload: Mapping[str, Any], *, logistic: bool) -> np.ndarray:
    values = matrix.copy()
    medians = np.asarray(payload["imputer_median"], dtype=float)
    missing = ~np.isfinite(values)
    values[missing] = np.broadcast_to(medians, values.shape)[missing]
    scaled = (values - np.asarray(payload["scaler_mean"], dtype=float)) / np.asarray(
        payload["scaler_scale"], dtype=float
    )
    linear = scaled @ np.asarray(payload["coefficient"], dtype=float) + float(
        payload["intercept"]
    )
    if not logistic:
        return linear
    return 1.0 / (1.0 + np.exp(-np.clip(linear, -35.0, 35.0)))


def _optional_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed if np.isfinite(parsed) else float("nan")


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
