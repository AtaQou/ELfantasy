"""Fail-closed loading and inference for the frozen Phase 6C uplift layer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.prediction_artifacts import ModelArtifactMismatch
from src.modeling.ml_experiments import _catboost_feature_frame
from src.modeling.phase6b_protocol import QUANTILES, THRESHOLDS
from src.modeling.phase6c_features import (
    add_expected_usage_interactions,
    attach_phase6c_features,
)
from src.modeling.phase6c_protocol import (
    DOWNSIDE_THRESHOLDS,
    PHASE6C_CALIBRATION_VERSION,
    PHASE6C_MODEL_VERSION,
    PHASE6C_PROTOCOL_VERSION,
    expected_usage_feature_manifest,
    phase6c_feature_manifest,
    protocol_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE6C_ARTIFACT_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase6c"
    / "frozen_predictive_uplift"
)


@dataclass(frozen=True, slots=True)
class PredictiveUpliftArtifactValidation:
    predictive_model_version: str
    calibration_version: str
    artifact_fingerprint: str
    bundle_fingerprint: str
    feature_manifest_fingerprint: str
    training_data_fingerprint: str
    code_protocol_fingerprint: str


class FrozenPredictiveUpliftBundle:
    """Expected usage, central uplift, and calibrated downside classifiers."""

    def __init__(self, root: Path, payload: Mapping[str, Any]) -> None:
        self.root = root
        self.payload = dict(payload)
        self.central_manifest = dict(payload["central_feature_manifest"])
        self.usage_manifest = dict(payload["expected_usage_feature_manifest"])
        self._models: dict[str, Any] = {}

    def prepare_features(
        self,
        frame: pd.DataFrame,
        *,
        database_path: Path | str = DEFAULT_DATABASE_PATH,
    ) -> pd.DataFrame:
        enriched = (
            frame.copy() if "p6c_usage_last5" in frame
            else attach_phase6c_features(frame, database_path)
        )
        usage_model = self._regressor("usage", self.payload["expected_usage_model"])
        predicted = usage_model.predict(
            _catboost_feature_frame(enriched, self.usage_manifest)
        )
        enriched["expected_usage_next_game"] = np.clip(predicted, 0.0, 100.0)
        return add_expected_usage_interactions(enriched)

    def predict_distribution(
        self,
        frame: pd.DataFrame,
        phase6b_distribution: pd.DataFrame,
        *,
        phase5b_location_delta: np.ndarray | pd.Series | float = 0.0,
        database_path: Path | str = DEFAULT_DATABASE_PATH,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Apply the Phase 6C location uplift and direct downside probabilities."""

        if len(frame) != len(phase6b_distribution):
            raise ValueError("Phase 6B/6C inference row counts differ")
        enriched = self.prepare_features(frame, database_path=database_path)
        central_model = self._regressor("central", self.payload["central_model"])
        central_before = np.asarray(central_model.predict(
            _catboost_feature_frame(enriched, self.central_manifest)
        ), dtype=float)
        phase6b_before = pd.to_numeric(
            phase6b_distribution[
                "phase6b_expected_fp_before_availability_adjustment"
            ], errors="coerce",
        ).to_numpy(float)
        uplift_delta = central_before - phase6b_before
        availability_delta = np.broadcast_to(
            np.asarray(phase5b_location_delta, dtype=float), len(enriched)
        )

        result = phase6b_distribution.copy()
        result["phase6b_expected_fp"] = phase6b_distribution["phase6b_expected_fp"]
        result["phase6c_expected_fp_before_availability_adjustment"] = central_before
        result["phase6c_expected_fp"] = central_before + availability_delta
        result["expected_usage_next_game"] = enriched["expected_usage_next_game"].to_numpy()
        result["phase6c_location_delta"] = uplift_delta
        for quantile in QUANTILES:
            column = f"p{int(quantile*100):02d}_fp"
            result[column] = pd.to_numeric(
                phase6b_distribution[column], errors="coerce"
            ).to_numpy(float) + uplift_delta

        upside_columns = [f"prob_fp_ge_{int(value)}" for value in THRESHOLDS]
        result[upside_columns] = _shift_survival(
            phase6b_distribution[upside_columns].to_numpy(float),
            np.asarray(THRESHOLDS, dtype=float), uplift_delta,
        )
        raw_downside = []
        for threshold in DOWNSIDE_THRESHOLDS:
            entry = self.payload["downside_models"][str(int(threshold))]
            model = self._classifier(f"le{int(threshold)}", entry)
            raw = model.predict_proba(
                _catboost_feature_frame(enriched, self.central_manifest)
            )[:, 1]
            calibrated = _sigmoid(
                float(entry["platt_coefficient"]) * _logit(raw)
                + float(entry["platt_intercept"])
            )
            raw_downside.append(calibrated)
        downside = _shift_cdf(
            np.column_stack(raw_downside),
            np.asarray(DOWNSIDE_THRESHOLDS, dtype=float), availability_delta,
        )
        downside = np.maximum.accumulate(np.clip(downside, 0.0, 1.0), axis=1)
        for index, threshold in enumerate(DOWNSIDE_THRESHOLDS):
            result[f"prob_fp_le_{int(threshold)}"] = downside[:, index]

        result["median_fp"] = result["p50_fp"]
        result["distribution_width"] = result["p90_fp"] - result["p10_fp"]
        result["p90_minus_p50"] = result["p90_fp"] - result["p50_fp"]
        result["p50_minus_p10"] = result["p50_fp"] - result["p10_fp"]
        result["p95_minus_expected"] = (
            result["p95_fp"] - result["phase6c_expected_fp"]
        )
        result["predictive_model_version"] = PHASE6C_MODEL_VERSION
        finite_columns = [
            "phase6c_expected_fp_before_availability_adjustment",
            "phase6c_expected_fp", "expected_usage_next_game",
            *[f"p{int(value * 100):02d}_fp" for value in QUANTILES],
            *upside_columns,
            *[f"prob_fp_le_{int(value)}" for value in DOWNSIDE_THRESHOLDS],
        ]
        result["predictive_predictions_finite"] = np.isfinite(
            result[finite_columns].to_numpy(float)
        ).all(axis=1)
        return result, enriched

    def _regressor(self, key: str, entry: Mapping[str, Any]) -> Any:
        model = self._models.get(key)
        if model is None:
            from catboost import CatBoostRegressor
            model = CatBoostRegressor()
            model.load_model(str(_resolve(self.root, str(entry["model_path"]))), format="json")
            self._models[key] = model
        return model

    def _classifier(self, key: str, entry: Mapping[str, Any]) -> Any:
        model = self._models.get(key)
        if model is None:
            from catboost import CatBoostClassifier
            model = CatBoostClassifier()
            model.load_model(str(_resolve(self.root, str(entry["model_path"]))), format="json")
            self._models[key] = model
        return model


def load_and_validate_predictive_uplift_artifact(
    root: Path = DEFAULT_PHASE6C_ARTIFACT_ROOT,
) -> tuple[FrozenPredictiveUpliftBundle, PredictiveUpliftArtifactValidation]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ModelArtifactMismatch(f"missing frozen Phase 6C manifest: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "status": "FROZEN",
        "bundle_identifier": PHASE6C_MODEL_VERSION,
        "predictive_model_version": PHASE6C_MODEL_VERSION,
        "calibration_version": PHASE6C_CALIBRATION_VERSION,
        "protocol_version": PHASE6C_PROTOCOL_VERSION,
        "protocol_fingerprint": protocol_fingerprint(),
        "conditional_on_participation": True,
        "excludes_zero_minute_rows": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ModelArtifactMismatch(f"Phase 6C manifest mismatch: {key}")
    retained = tuple(str(value) for value in payload.get("retained_families", ()))
    central_manifest = phase6c_feature_manifest(retained)
    if payload.get("central_feature_manifest", {}).get("sha256") != central_manifest["sha256"]:
        raise ModelArtifactMismatch("Phase 6C central feature manifest mismatch")
    usage_manifest = expected_usage_feature_manifest()
    if payload.get("expected_usage_feature_manifest", {}).get("sha256") != usage_manifest["sha256"]:
        raise ModelArtifactMismatch("Phase 6C expected-usage manifest mismatch")
    if tuple(float(value) for value in payload.get("downside_thresholds", ())) != DOWNSIDE_THRESHOLDS:
        raise ModelArtifactMismatch("Phase 6C downside threshold set mismatch")
    gate = payload.get("outer_evaluation_gate", {})
    if gate.get("passed") is not True or gate.get("rows") != 22399:
        raise ModelArtifactMismatch("Phase 6C outer evaluation gate failed")
    if float(gate.get("phase6c_mae", np.inf)) >= float(gate.get("phase6b_mae", -np.inf)):
        raise ModelArtifactMismatch("Phase 6C frozen uplift does not improve central MAE")

    files = payload.get("files", {})
    verified_files: dict[str, str] = {}
    for relative, expected_hash in sorted(files.items()):
        path = _resolve(root, relative)
        if not path.is_file() or _file_sha(path) != expected_hash:
            raise ModelArtifactMismatch(f"Phase 6C file fingerprint mismatch: {relative}")
        verified_files[relative] = expected_hash
    code_files = payload.get("code_files", {})
    verified_code: dict[str, str] = {}
    for relative, expected_hash in sorted(code_files.items()):
        path = PROJECT_ROOT / relative
        if not path.is_file() or _file_sha(path) != expected_hash:
            raise ModelArtifactMismatch(f"Phase 6C code fingerprint mismatch: {relative}")
        verified_code[relative] = expected_hash
    code_fingerprint = _json_sha(verified_code)
    if payload.get("code_protocol_fingerprint") != code_fingerprint:
        raise ModelArtifactMismatch("Phase 6C code fingerprint mismatch")
    bundle_fingerprint = _json_sha({
        "files": verified_files, "code_files": verified_code,
        "protocol_fingerprint": payload["protocol_fingerprint"],
    })
    if payload.get("bundle_fingerprint") != bundle_fingerprint:
        raise ModelArtifactMismatch("Phase 6C bundle fingerprint mismatch")
    artifact_fingerprint = _json_sha({
        "manifest": _file_sha(manifest_path), "bundle": bundle_fingerprint,
    })
    validation = PredictiveUpliftArtifactValidation(
        PHASE6C_MODEL_VERSION, PHASE6C_CALIBRATION_VERSION,
        artifact_fingerprint, bundle_fingerprint, central_manifest["sha256"],
        str(payload["training_data_fingerprint"]), code_fingerprint,
    )
    return FrozenPredictiveUpliftBundle(root, payload), validation


def _shift_survival(probability: np.ndarray, thresholds: np.ndarray, delta: np.ndarray) -> np.ndarray:
    logits = _logit(probability)
    output = np.empty_like(logits)
    for row in range(len(logits)):
        query = thresholds - delta[row]
        values = _linear_logit_curve(logits[row], thresholds, query)
        output[row] = _sigmoid(values)
    return np.minimum.accumulate(np.clip(output, 0.0, 1.0), axis=1)


def _shift_cdf(probability: np.ndarray, thresholds: np.ndarray, delta: np.ndarray) -> np.ndarray:
    logits = _logit(probability)
    output = np.empty_like(logits)
    for row in range(len(logits)):
        query = thresholds - delta[row]
        output[row] = _sigmoid(_linear_logit_curve(logits[row], thresholds, query))
    return output


def _linear_logit_curve(values: np.ndarray, x: np.ndarray, query: np.ndarray) -> np.ndarray:
    fitted = np.interp(query, x, values)
    left = query < x[0]
    right = query > x[-1]
    left_slope = (values[1] - values[0]) / (x[1] - x[0])
    right_slope = (values[-1] - values[-2]) / (x[-1] - x[-2])
    fitted[left] = values[0] + left_slope * (query[left] - x[0])
    fitted[right] = values[-1] + right_slope * (query[right] - x[-1])
    return fitted


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -20, 20)))


def _resolve(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ModelArtifactMismatch("Phase 6C artifact path escapes bundle root")
    return path


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
