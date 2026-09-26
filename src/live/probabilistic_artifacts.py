"""Fail-closed loading and inference for the frozen Phase 6B distribution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.modeling.fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint
from src.modeling.ml_experiments import _catboost_feature_frame
from src.modeling.ml_protocol import feature_manifest
from src.modeling.phase6b_protocol import (
    PHASE6B_CALIBRATION_VERSION,
    PHASE6B_MODEL_VERSION,
    PHASE6B_PROTOCOL_VERSION,
    QUANTILES,
    THRESHOLDS,
    payload_fingerprint,
    reconcile_distribution,
    role_band,
)

from .prediction_artifacts import ModelArtifactMismatch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE6B_ARTIFACT_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase6b" / "frozen_prediction"
)


@dataclass(frozen=True, slots=True)
class ProbabilisticArtifactValidation:
    probabilistic_model_version: str
    calibration_version: str
    artifact_fingerprint: str
    bundle_fingerprint: str
    feature_manifest_fingerprint: str
    training_data_fingerprint: str
    code_protocol_fingerprint: str


class FrozenProbabilisticBundle:
    """Conditional mean, quantile, and threshold models plus reconciliation."""

    def __init__(self, root: Path, payload: Mapping[str, Any]) -> None:
        self.root = root
        self.payload = dict(payload)
        self.probabilistic_model_version = str(payload["probabilistic_model_version"])
        self.calibration_version = str(payload["calibration_version"])
        self.manifest = dict(payload["feature_manifest"])
        self._models: dict[str, Any] = {}

    def predict_distribution(
        self,
        frame: pd.DataFrame,
        *,
        fp_location_delta: np.ndarray | pd.Series | float = 0.0,
    ) -> pd.DataFrame:
        delta = np.broadcast_to(np.asarray(fp_location_delta, dtype=float), len(frame))
        expected_before = self._predict_regressor("mean", self.payload["mean_model"], frame)
        raw_quantiles = []
        roles = role_band(frame)
        for quantile in QUANTILES:
            entry = self.payload["quantile_models"][f"{quantile:.2f}"]
            raw = self._predict_regressor(f"q{quantile:.2f}", entry, frame)
            offsets = roles.map(entry["role_calibration_offsets"]).fillna(
                float(entry["calibration_offset"])
            ).to_numpy(float)
            raw_quantiles.append(raw + offsets + delta)
        quantile_matrix = np.column_stack(raw_quantiles)

        retained = np.asarray(self.payload["retained_direct_thresholds"], dtype=float)
        retained_probabilities = []
        for threshold in retained:
            entry = self.payload["classifier_models"][str(int(threshold))]
            raw = self._predict_classifier(f"ge{int(threshold)}", entry, frame)
            calibrated = _sigmoid(
                float(entry["platt_coefficient"]) * _logit(raw)
                + float(entry["platt_intercept"])
            )
            retained_probabilities.append(calibrated)
        probabilities = _shift_probability_curve(
            np.column_stack(retained_probabilities), retained, delta
        )
        final_quantiles, final_probabilities = reconcile_distribution(
            quantile_matrix, probabilities
        )
        result = pd.DataFrame(index=frame.index)
        result["phase6b_expected_fp_before_availability_adjustment"] = expected_before
        result["phase6b_expected_fp"] = expected_before + delta
        for index, quantile in enumerate(QUANTILES):
            result[f"p{int(quantile * 100):02d}_fp"] = final_quantiles[:, index]
        result["median_fp"] = result["p50_fp"]
        for index, threshold in enumerate(THRESHOLDS):
            result[f"prob_fp_ge_{int(threshold)}"] = final_probabilities[:, index]
        result["distribution_width"] = result["p90_fp"] - result["p10_fp"]
        result["p90_minus_p50"] = result["p90_fp"] - result["p50_fp"]
        result["p50_minus_p10"] = result["p50_fp"] - result["p10_fp"]
        result["p95_minus_expected"] = result["p95_fp"] - result["phase6b_expected_fp"]
        result["history_sample_count"] = pd.to_numeric(
            frame["career_el_games_before"], errors="coerce"
        ).fillna(0).astype(int)
        result["distribution_confidence"] = pd.cut(
            result.history_sample_count, [-np.inf, 4, 19, np.inf],
            labels=["LOW", "MEDIUM", "HIGH"],
        ).astype("string")
        result["probabilistic_predictions_finite"] = np.isfinite(
            result.select_dtypes(include=[np.number]).to_numpy(float)
        ).all(axis=1)
        return result

    def _predict_regressor(
        self, cache_key: str, entry: Mapping[str, Any], frame: pd.DataFrame,
    ) -> np.ndarray:
        model = self._model(cache_key, entry)
        if entry["model_type"] == "catboost":
            features = _catboost_feature_frame(frame, self.manifest)
        else:
            features = _lightgbm_frame(frame, self.manifest, model._phase6b_categories)
        return np.asarray(model.predict(features), dtype=float)

    def _predict_classifier(
        self, cache_key: str, entry: Mapping[str, Any], frame: pd.DataFrame,
    ) -> np.ndarray:
        model = self._model(cache_key, entry)
        return np.asarray(
            model.predict_proba(_catboost_feature_frame(frame, self.manifest))[:, 1],
            dtype=float,
        )

    def _model(self, cache_key: str, entry: Mapping[str, Any]) -> Any:
        model = self._models.get(cache_key)
        if model is not None:
            return model
        path = _resolve(self.root, str(entry["model_path"]))
        if entry["model_type"] == "catboost":
            from catboost import CatBoostClassifier, CatBoostRegressor

            model = (
                CatBoostClassifier() if cache_key.startswith("ge")
                else CatBoostRegressor()
            )
            model.load_model(str(path), format=str(entry["model_format"]))
        elif entry["model_type"] == "lightgbm":
            with path.open("rb") as handle:
                model = pickle.load(handle)
        else:
            raise ModelArtifactMismatch(f"unsupported Phase 6B model type: {entry['model_type']}")
        self._models[cache_key] = model
        return model


def load_and_validate_probabilistic_artifact(
    root: Path = DEFAULT_PHASE6B_ARTIFACT_ROOT,
) -> tuple[FrozenProbabilisticBundle, ProbabilisticArtifactValidation]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ModelArtifactMismatch(f"missing frozen Phase 6B manifest: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("status") != "FROZEN":
        raise ModelArtifactMismatch("Phase 6B artifact is not frozen")
    if payload.get("bundle_identifier") != PHASE6B_MODEL_VERSION:
        raise ModelArtifactMismatch("Phase 6B bundle identifier mismatch")
    if payload.get("probabilistic_model_version") != PHASE6B_MODEL_VERSION:
        raise ModelArtifactMismatch("Phase 6B model version mismatch")
    if payload.get("calibration_version") != PHASE6B_CALIBRATION_VERSION:
        raise ModelArtifactMismatch("Phase 6B calibration version mismatch")
    if payload.get("protocol_version") != PHASE6B_PROTOCOL_VERSION:
        raise ModelArtifactMismatch("Phase 6B protocol version mismatch")
    if payload.get("protocol_fingerprint") != payload_fingerprint():
        raise ModelArtifactMismatch("Phase 6B protocol fingerprint mismatch")
    if payload.get("target_rule_version") != TARGET_RULE_VERSION:
        raise ModelArtifactMismatch("Phase 6B target scoring version mismatch")
    if payload.get("target_rule_fingerprint") != scoring_rule_fingerprint():
        raise ModelArtifactMismatch("Phase 6B target scoring fingerprint mismatch")
    if payload.get("conditional_on_participation") is not True:
        raise ModelArtifactMismatch("Phase 6B conditional target flag mismatch")
    if payload.get("excludes_zero_minute_rows") is not True:
        raise ModelArtifactMismatch("Phase 6B DNP exclusion flag mismatch")
    expected_manifest = feature_manifest("CORE_ROTATION", include_player_id=True)
    if payload.get("feature_manifest", {}).get("sha256") != expected_manifest["sha256"]:
        raise ModelArtifactMismatch("Phase 6B feature manifest mismatch")
    if sorted(float(value) for value in payload.get("quantile_models", {})) != list(QUANTILES):
        raise ModelArtifactMismatch("Phase 6B quantile model set mismatch")
    retained = tuple(float(value) for value in payload.get("retained_direct_thresholds", []))
    if retained != (20.0, 25.0, 30.0, 35.0):
        raise ModelArtifactMismatch("Phase 6B retained classifier set mismatch")
    gate = payload.get("outer_evaluation_gate", {})
    if gate.get("passed") is not True or gate.get("rows") != 22399:
        raise ModelArtifactMismatch("Phase 6B outer evaluation gate failed")
    if float(gate.get("final_quantile_crossing_rate", 1)) != 0.0:
        raise ModelArtifactMismatch("Phase 6B frozen quantiles cross")
    if float(gate.get("final_probability_order_violation_rate", 1)) != 0.0:
        raise ModelArtifactMismatch("Phase 6B frozen probabilities are not monotonic")

    files = payload.get("files", {})
    if not isinstance(files, dict) or not files:
        raise ModelArtifactMismatch("Phase 6B file manifest is missing")
    verified_files: dict[str, str] = {}
    for relative, expected_hash in sorted(files.items()):
        path = _resolve(root, relative)
        if not path.is_file():
            raise ModelArtifactMismatch(f"missing Phase 6B artifact file: {relative}")
        digest = _file_sha(path)
        if digest != expected_hash:
            raise ModelArtifactMismatch(f"Phase 6B file fingerprint mismatch: {relative}")
        verified_files[relative] = digest
    code_files = payload.get("code_files", {})
    if not isinstance(code_files, dict) or not code_files:
        raise ModelArtifactMismatch("Phase 6B code manifest is missing")
    verified_code: dict[str, str] = {}
    for relative, expected_hash in sorted(code_files.items()):
        path = PROJECT_ROOT / relative
        if not path.is_file() or _file_sha(path) != expected_hash:
            raise ModelArtifactMismatch(f"Phase 6B code fingerprint mismatch: {relative}")
        verified_code[relative] = expected_hash
    code_fingerprint = _json_sha(verified_code)
    if payload.get("code_protocol_fingerprint") != code_fingerprint:
        raise ModelArtifactMismatch("Phase 6B code/protocol fingerprint mismatch")
    bundle_fingerprint = _json_sha({
        "files": verified_files, "code_files": verified_code,
        "protocol_fingerprint": payload["protocol_fingerprint"],
    })
    if payload.get("bundle_fingerprint") != bundle_fingerprint:
        raise ModelArtifactMismatch("Phase 6B bundle fingerprint mismatch")
    artifact_fingerprint = _json_sha({
        "manifest": _file_sha(manifest_path), "bundle": bundle_fingerprint,
    })
    validation = ProbabilisticArtifactValidation(
        PHASE6B_MODEL_VERSION, PHASE6B_CALIBRATION_VERSION,
        artifact_fingerprint, bundle_fingerprint, expected_manifest["sha256"],
        str(payload["training_data_fingerprint"]), code_fingerprint,
    )
    return FrozenProbabilisticBundle(root, payload), validation


def _shift_probability_curve(
    retained_probability: np.ndarray,
    retained_thresholds: np.ndarray,
    fp_delta: np.ndarray,
) -> np.ndarray:
    """Evaluate the calibrated survival curve at threshold minus location shift."""

    logits = _logit(retained_probability)
    output = np.empty((len(logits), len(THRESHOLDS)), dtype=float)
    targets = np.asarray(THRESHOLDS, dtype=float)
    for row in range(len(logits)):
        query = targets - fp_delta[row]
        values = np.interp(query, retained_thresholds, logits[row])
        left = query < retained_thresholds[0]
        right = query > retained_thresholds[-1]
        left_slope = (logits[row, 1] - logits[row, 0]) / (
            retained_thresholds[1] - retained_thresholds[0]
        )
        right_slope = (logits[row, -1] - logits[row, -2]) / (
            retained_thresholds[-1] - retained_thresholds[-2]
        )
        values[left] = logits[row, 0] + left_slope * (
            query[left] - retained_thresholds[0]
        )
        values[right] = logits[row, -1] + right_slope * (
            query[right] - retained_thresholds[-1]
        )
        output[row] = _sigmoid(values)
    return output


def _lightgbm_frame(
    frame: pd.DataFrame,
    manifest: Mapping[str, Any],
    categories: Mapping[str, list[str]],
) -> pd.DataFrame:
    data: dict[str, Any] = {}
    for column in manifest["numeric"]:
        data[column] = pd.to_numeric(frame[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
    for column in manifest["categorical"]:
        values = frame[column].astype("string").fillna("__MISSING__")
        data[column] = pd.Categorical(values, categories=categories[column])
    return pd.DataFrame(data, index=frame.index)[list(manifest["features"])]


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(values, float), -20, 20)))


def _resolve(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ModelArtifactMismatch("Phase 6B artifact path escapes bundle root")
    return path


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
