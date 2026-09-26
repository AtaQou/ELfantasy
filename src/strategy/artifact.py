"""Immutable packaging and validation for the frozen Phase 7 strategy layer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.strategy.rules import FantasyRules, load_rules


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE7_MODEL_VERSION = "phase7_dynamic_strategy_engine_frozen_v1"
DEFAULT_PHASE7_ARTIFACT_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase7"
    / "frozen_dynamic_strategy_engine"
)
PHASE7_CODE_FILES = (
    "src/strategy/rules.py",
    "src/strategy/simulation.py",
    "src/strategy/optimizer.py",
    "src/strategy/coach.py",
    "src/strategy/historical.py",
    "src/strategy/engine.py",
    "src/strategy/backtest.py",
    "src/strategy/risk_analysis.py",
    "src/strategy/artifact.py",
    "scripts/optimize_fantasy.py",
    "scripts/run_phase7_backtest.py",
)


def package_phase7_artifact(
    *,
    rules: FantasyRules | None = None,
    phase7_root: Path | str = PROJECT_ROOT / "data" / "derived" / "phase7",
    artifact_root: Path | str = DEFAULT_PHASE7_ARTIFACT_ROOT,
) -> Path:
    rules = rules or load_rules()
    root = Path(phase7_root)
    destination = Path(artifact_root)
    summary_path = root / "research" / "e2025_strategy_backtest_summary.json"
    prediction_path = root / "research" / "e2025_full_market_predictions.parquet"
    backtest_path = root / "research" / "e2025_strategy_backtest.parquet"
    coach_path = root / "research" / "coach_distribution_model.pkl"
    dependence_path = root / "research" / "residual_dependence_validation.json"
    calibration_path = root / "research" / "monte_carlo_marginal_calibration.json"
    risk_path = root / "research" / "e2025_risk_analysis.json"
    tail_ablation_path = (
        root / "research" / "e2025_tail_probability_ablations.parquet"
    )
    required = (
        summary_path, prediction_path, backtest_path, coach_path,
        dependence_path, calibration_path, risk_path, tail_ablation_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Phase 7 package inputs missing: {missing}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    files = {
        str(path.relative_to(PROJECT_ROOT)): _file_sha(path) for path in required
    }
    code_files = {
        path: _file_sha(PROJECT_ROOT / path) for path in PHASE7_CODE_FILES
    }
    payload: dict[str, Any] = {
        "status": "FROZEN",
        "strategy_model_version": PHASE7_MODEL_VERSION,
        "created_at": "2026-08-23T00:00:00+03:00",
        "predictive_layer": "phase6c_predictive_uplift_frozen_v1",
        "predictive_layer_modified": False,
        "ruleset_version": rules.ruleset_version,
        "rules_fingerprint": rules.fingerprint,
        "simulation_version": "phase7_piecewise_inverse_cdf_mc_v1",
        "optimizer_version": "phase7_dynamic_strategy_engine_v1",
        "historical_replay_seasons": ["E2025"],
        "production_gates": dict(rules.production_gates),
        "conditional_on_playing_player_layer": True,
        "historical_dnp_outcome_semantics": "REALIZED_ZERO_WITHOUT_PRE_CUTOFF_EXCLUSION",
        "dependence_mode": "GAME_GAUSSIAN_COPULA_VALIDATED",
        "dependence_rho": -0.015,
        "backtest_summary": summary,
        "files": files,
        "code_files": code_files,
    }
    payload["bundle_fingerprint"] = _json_sha({
        "files": files, "code_files": code_files,
        "rules": rules.fingerprint, "summary": summary.get("results_fingerprint"),
    })
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    content = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != content:
        raise ValueError(f"immutable Phase 7 artifact conflict: {manifest_path}")
    if not manifest_path.exists():
        manifest_path.write_text(content, encoding="utf-8")
    validate_phase7_artifact(destination)
    return manifest_path


def validate_phase7_artifact(
    artifact_root: Path | str = DEFAULT_PHASE7_ARTIFACT_ROOT,
) -> Mapping[str, Any]:
    root = Path(artifact_root)
    manifest_path = root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("status") != "FROZEN":
        raise ValueError("Phase 7 strategy artifact is not frozen")
    if payload.get("strategy_model_version") != PHASE7_MODEL_VERSION:
        raise ValueError("Phase 7 strategy artifact version mismatch")
    if payload.get("predictive_layer_modified") is not False:
        raise ValueError("Phase 7 artifact does not preserve the frozen predictive layer")
    rules = load_rules()
    if payload.get("rules_fingerprint") != rules.fingerprint:
        raise ValueError("Phase 7 rules fingerprint mismatch")
    for relative, expected in payload.get("files", {}).items():
        if _file_sha(PROJECT_ROOT / relative) != expected:
            raise ValueError(f"Phase 7 data artifact mismatch: {relative}")
    for relative, expected in payload.get("code_files", {}).items():
        if _file_sha(PROJECT_ROOT / relative) != expected:
            raise ValueError(f"Phase 7 code artifact mismatch: {relative}")
    actual_bundle = _json_sha({
        "files": payload["files"], "code_files": payload["code_files"],
        "rules": rules.fingerprint,
        "summary": payload["backtest_summary"].get("results_fingerprint"),
    })
    if payload.get("bundle_fingerprint") != actual_bundle:
        raise ValueError("Phase 7 bundle fingerprint mismatch")
    return payload


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
