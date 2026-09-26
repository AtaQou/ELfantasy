"""Fail-closed loading of frozen Phase 4B and Phase 5B inference artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any

import numpy as np
import pandas as pd

from src.modeling.ml_experiments import _catboost_feature_frame, _raw_feature_frame
from src.modeling.ml_protocol import feature_manifest
from src.modeling.phase4b_protocol import PHASE4B_PROTOCOL_VERSION, phase4b_feature_manifest
from src.modeling.phase5b_protocol import (
    PHASE5B_PROTOCOL_VERSION,
    redistribution_manifest,
)
from src.modeling.fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE4B_ARTIFACT_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase4b" / "frozen_prediction"
)
DEFAULT_PHASE5B_ARTIFACT_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase5b" / "frozen_redistribution"
)
EXPECTED_PERFORMANCE_VERSION = "phase4b_conditional_performance_frozen_v1"
EXPECTED_REDISTRIBUTION_VERSION = "phase5b_known_absence_redistribution_frozen_v1"
EXPECTED_PHASE4B_COMPONENTS = {
    "direct": {
        "iterations": 191,
        "hyperparameters": {
            "depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
            "loss_function": "MAE",
        },
    },
    "minutes": {
        "iterations": 337,
        "hyperparameters": {
            "depth": 5, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
            "loss_function": "MAE",
        },
    },
    "production": {
        "iterations": 353,
        "hyperparameters": {
            "depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
            "loss_function": "MAE",
        },
    },
}


class ModelArtifactMismatch(RuntimeError):
    """A required frozen inference artifact is missing or incompatible."""

    code = "MODEL_ARTIFACT_MISMATCH"


@dataclass(frozen=True, slots=True)
class ArtifactValidation:
    performance_model_version: str
    redistribution_model_version: str
    performance_artifact_fingerprint: str
    redistribution_artifact_fingerprint: str
    feature_manifest_fingerprint: str
    performance_bundle_identifier: str = ""
    performance_bundle_fingerprint: str = ""
    reproduction_gate_passed: bool = False
    training_scoring_rule_version: str = ""


class FrozenPerformanceBundle:
    """Three immutable point-model components used by the frozen hybrid."""

    def __init__(self, root: Path, payload: dict[str, Any]) -> None:
        self.root = root
        self.payload = payload
        self.performance_model_version = str(payload["performance_model_version"])
        self.weight_direct = float(payload["hybrid"]["weight_direct"])
        self.weight_decomposed = float(payload["hybrid"]["weight_decomposed"])
        self._models: dict[str, Any] = {}

    def predict_components(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=frame.index)
        result["direct_prediction"] = self._predict("direct", frame)
        result["baseline_expected_minutes_if_available"] = np.clip(
            self._predict("minutes", frame), 0.0, 40.0
        )
        result["expected_fp_per_min"] = self._predict("production", frame)
        finite = np.isfinite(result.to_numpy(float)).all(axis=1)
        result["component_predictions_finite"] = finite
        return result

    def _predict(self, name: str, frame: pd.DataFrame) -> np.ndarray:
        component = self.payload["components"][name]
        manifest = component["feature_manifest"]
        model = self._models.get(name)
        if model is None:
            model_path = _resolve_model_path(self.root, component["model_path"])
            if component["model_type"] == "catboost":
                from catboost import CatBoostRegressor

                model = CatBoostRegressor()
                model.load_model(
                    str(model_path), format=str(component.get("model_format", "cbm"))
                )
            else:
                with model_path.open("rb") as handle:
                    model = pickle.load(handle)
            self._models[name] = model
        features = (
            _catboost_feature_frame(frame, manifest)
            if component["model_type"] == "catboost"
            else _raw_feature_frame(frame, manifest)
        )
        return np.asarray(model.predict(features), dtype=float)


def load_and_validate_frozen_artifacts(
    *,
    phase4b_root: Path = DEFAULT_PHASE4B_ARTIFACT_ROOT,
    phase5b_root: Path = DEFAULT_PHASE5B_ARTIFACT_ROOT,
) -> tuple[FrozenPerformanceBundle, ArtifactValidation]:
    phase4_payload, phase4_fingerprint = _validate_phase4b(phase4b_root)
    phase5_fingerprint = _validate_phase5b(phase5b_root)
    manifests = {
        name: phase4_payload["components"][name]["feature_manifest"]["sha256"]
        for name in ("direct", "minutes", "production")
    }
    manifests["redistribution"] = redistribution_manifest(False)["sha256"]
    feature_fingerprint = _json_sha(manifests)
    validation = ArtifactValidation(
        performance_model_version=EXPECTED_PERFORMANCE_VERSION,
        redistribution_model_version=EXPECTED_REDISTRIBUTION_VERSION,
        performance_artifact_fingerprint=phase4_fingerprint,
        redistribution_artifact_fingerprint=phase5_fingerprint,
        feature_manifest_fingerprint=feature_fingerprint,
        performance_bundle_identifier=str(phase4_payload["bundle_identifier"]),
        performance_bundle_fingerprint=str(phase4_payload["bundle_fingerprint"]),
        reproduction_gate_passed=True,
        training_scoring_rule_version=str(
            phase4_payload["training_scoring_rule_version"]
        ),
    )
    return FrozenPerformanceBundle(phase4b_root, phase4_payload), validation


def validate_redistribution_artifact(
    root: Path = DEFAULT_PHASE5B_ARTIFACT_ROOT,
) -> str:
    """Validate Phase 5B independently for diagnostics and empty-slate runs."""

    return _validate_phase5b(root)


def _validate_phase4b(root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ModelArtifactMismatch(
            f"missing frozen Phase 4B deployment manifest: {manifest_path}"
        )
    payload = _read_json(manifest_path)
    if payload.get("status") != "FROZEN":
        raise ModelArtifactMismatch("Phase 4B deployment artifact is not FROZEN")
    if payload.get("performance_model_version") != EXPECTED_PERFORMANCE_VERSION:
        raise ModelArtifactMismatch("Phase 4B performance model version mismatch")
    if payload.get("protocol_version") != PHASE4B_PROTOCOL_VERSION:
        raise ModelArtifactMismatch("Phase 4B protocol version mismatch")
    hybrid = payload.get("hybrid", {})
    if not np.isclose(float(hybrid.get("weight_direct", -1)), 0.25):
        raise ModelArtifactMismatch("Phase 4B direct weight is not frozen at 0.25")
    if not np.isclose(float(hybrid.get("weight_decomposed", -1)), 0.75):
        raise ModelArtifactMismatch("Phase 4B decomposed weight is not frozen at 0.75")
    if payload.get("bundle_identifier") != EXPECTED_PERFORMANCE_VERSION:
        raise ModelArtifactMismatch("Phase 4B bundle identifier mismatch")
    if payload.get("random_seed") != 17:
        raise ModelArtifactMismatch("Phase 4B random seed mismatch")
    if payload.get("training_scoring_rule_version") != TARGET_RULE_VERSION:
        raise ModelArtifactMismatch("Phase 4B training scoring-rule version mismatch")
    if payload.get("training_scoring_rule_fingerprint") != scoring_rule_fingerprint():
        raise ModelArtifactMismatch("Phase 4B training scoring-rule fingerprint mismatch")
    expected_manifests = {
        "direct": feature_manifest("CORE_ROTATION", include_player_id=True),
        "minutes": phase4b_feature_manifest("MINUTES_ROLE"),
        "production": phase4b_feature_manifest("PRODUCTION"),
    }
    components = payload.get("components")
    if not isinstance(components, dict):
        raise ModelArtifactMismatch("Phase 4B deployment components are missing")
    artifact_parts: dict[str, str] = {"manifest": _file_sha(manifest_path)}
    for name, expected in expected_manifests.items():
        component = components.get(name)
        if not isinstance(component, dict):
            raise ModelArtifactMismatch(f"Phase 4B {name} component is missing")
        manifest = component.get("feature_manifest", {})
        if manifest.get("sha256") != expected["sha256"]:
            raise ModelArtifactMismatch(f"Phase 4B {name} feature manifest mismatch")
        if component.get("model_type") not in {"catboost", "pickle"}:
            raise ModelArtifactMismatch(f"unsupported Phase 4B {name} model type")
        if component.get("model_type") == "catboost" and component.get("model_format") != "json":
            raise ModelArtifactMismatch(f"Phase 4B {name} model format mismatch")
        expected_component = EXPECTED_PHASE4B_COMPONENTS[name]
        if component.get("iterations") != expected_component["iterations"]:
            raise ModelArtifactMismatch(f"Phase 4B {name} iteration count mismatch")
        if component.get("hyperparameters") != expected_component["hyperparameters"]:
            raise ModelArtifactMismatch(f"Phase 4B {name} hyperparameter mismatch")
        path = _resolve_model_path(root, component.get("model_path"))
        if not path.is_file():
            raise ModelArtifactMismatch(f"missing Phase 4B {name} model: {path}")
        digest = _file_sha(path)
        if component.get("model_sha256") != digest:
            raise ModelArtifactMismatch(f"Phase 4B {name} model fingerprint mismatch")
        artifact_parts[name] = digest
    reproduction = payload.get("reproduction_gate", {})
    if reproduction.get("passed") is not True:
        raise ModelArtifactMismatch("Phase 4B row-level reproduction gate did not pass")
    if float(reproduction.get("maximum_absolute_difference", np.inf)) > 1e-12:
        raise ModelArtifactMismatch("Phase 4B row-level reproduction tolerance exceeded")
    if not np.isclose(
        float(reproduction.get("reproduced_metrics", {}).get("mae", np.nan)),
        5.808649555104744, rtol=0.0, atol=1e-12,
    ):
        raise ModelArtifactMismatch("Phase 4B reproduced MAE mismatch")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ModelArtifactMismatch("Phase 4B bundle file manifest is missing")
    verified_files: dict[str, str] = {}
    for relative, expected_hash in sorted(files.items()):
        path = _resolve_model_path(root, relative)
        if not path.is_file():
            raise ModelArtifactMismatch(f"missing Phase 4B bundle file: {path}")
        actual_hash = _file_sha(path)
        if actual_hash != expected_hash:
            raise ModelArtifactMismatch(f"Phase 4B bundle file fingerprint mismatch: {relative}")
        verified_files[str(relative)] = actual_hash
    bundle_fingerprint = _json_sha(verified_files)
    if payload.get("bundle_fingerprint") != bundle_fingerprint:
        raise ModelArtifactMismatch("Phase 4B bundle fingerprint mismatch")
    code_files = payload.get("code_files")
    if not isinstance(code_files, dict) or not code_files:
        raise ModelArtifactMismatch("Phase 4B code fingerprint manifest is missing")
    actual_code_hashes = {}
    for relative, expected_hash in sorted(code_files.items()):
        path = PROJECT_ROOT / relative
        if not path.is_file() or _file_sha(path) != expected_hash:
            raise ModelArtifactMismatch(f"Phase 4B code fingerprint mismatch: {relative}")
        actual_code_hashes[relative] = expected_hash
    if payload.get("code_protocol_fingerprint") != _json_sha(actual_code_hashes):
        raise ModelArtifactMismatch("Phase 4B code/protocol fingerprint mismatch")
    return payload, _json_sha(artifact_parts)


def _validate_phase5b(root: Path) -> str:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ModelArtifactMismatch(
            f"missing frozen Phase 5B redistribution manifest: {manifest_path}"
        )
    payload = _read_json(manifest_path)
    if payload.get("status") != "FROZEN":
        raise ModelArtifactMismatch("Phase 5B redistribution artifact is not FROZEN")
    if payload.get("redistribution_model_version") != EXPECTED_REDISTRIBUTION_VERSION:
        raise ModelArtifactMismatch("Phase 5B redistribution model version mismatch")
    if payload.get("protocol_version", PHASE5B_PROTOCOL_VERSION) != PHASE5B_PROTOCOL_VERSION:
        raise ModelArtifactMismatch("Phase 5B protocol version mismatch")
    expected = redistribution_manifest(False)
    if payload.get("feature_manifest", {}).get("sha256") != expected["sha256"]:
        raise ModelArtifactMismatch("Phase 5B feature manifest mismatch")
    model_path = _resolve_model_path(root, payload.get("model_path"))
    if not model_path.is_file():
        raise ModelArtifactMismatch(f"missing Phase 5B model: {model_path}")
    model_sha = _file_sha(model_path)
    if payload.get("model_sha256") != model_sha:
        raise ModelArtifactMismatch("Phase 5B model fingerprint mismatch")
    return _json_sha({"manifest": _file_sha(manifest_path), "model": model_sha})


def _resolve_model_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        return root / "__MISSING_MODEL__"
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = PROJECT_ROOT / path
    return project_path if project_path.is_file() else root / path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelArtifactMismatch(f"invalid model manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ModelArtifactMismatch(f"model manifest is not an object: {path}")
    return payload


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
