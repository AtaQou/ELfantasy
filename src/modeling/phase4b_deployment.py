"""Deterministic packaging of the already-frozen Phase 4B inference bundle.

This module performs no search. It reconstructs the accepted chronological
models from retained selections, requires row-level reproduction, then refits
the three deployment components on all eligible pre-E2026 history.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH

from .fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint
from .ml_experiments import (
    _catboost_feature_frame,
    fit_final_model,
    predict_model,
    regression_metrics,
)
from .ml_protocol import (
    DEFAULT_RANDOM_SEED,
    PHASE4A_DATASET_VERSION,
    PHASE4A_PROTOCOL_VERSION,
    PRIMARY_TARGET_COLUMN,
    feature_manifest,
)
from .phase4b_models import (
    add_older_history,
    fit_final_component,
    predict_component,
    training_target,
)
from .phase4b_protocol import (
    PHASE4B_DATASET_VERSION,
    PHASE4B_PROTOCOL_VERSION,
    build_phase4b_folds,
    load_phase4b_frame,
    phase4b_feature_manifest,
    validate_phase4b_frame,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_IDENTIFIER = "phase4b_conditional_performance_frozen_v1"
DEFAULT_BUNDLE_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase4b" / "frozen_prediction"
)
FINAL_MODEL_REFERENCE = PROJECT_ROOT / "data" / "samples" / "phase4b" / "final_model.json"
DIRECT_SELECTION_REFERENCE = (
    PROJECT_ROOT / "data" / "derived" / "phase4a" / "experiments" /
    "p4a__catboost__core_rotation__verified_only__player__s17" / "complete.json"
)
REFERENCE_ROOT = PROJECT_ROOT / "data" / "derived" / "phase4b"
STRICT_PREDICTION_TOLERANCE = 1e-12
ACCEPTED_MAE = 5.808649555104744
LATEST_FROZEN_FOLD = "outer_e2025"


def package_frozen_phase4b_bundle(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    output_root: Path = DEFAULT_BUNDLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Reconstruct, prove, and serialize the frozen deployment bundle."""

    if output_root.exists():
        manifest = output_root / "manifest.json"
        if manifest.is_file() and not force:
            return json.loads(manifest.read_text(encoding="utf-8"))
        if not force:
            raise FileExistsError(f"Refusing to overwrite incomplete bundle: {output_root}")
    primary = load_phase4b_frame(database_path, primary_only=True)
    full_history = load_phase4b_frame(database_path, primary_only=False)
    validate_phase4b_frame(primary)
    folds = build_phase4b_folds(primary)
    frozen = _read_json(FINAL_MODEL_REFERENCE)
    direct_reference = _read_json(DIRECT_SELECTION_REFERENCE)

    with TemporaryDirectory(prefix="phase4b_deployment_") as directory:
        staging = Path(directory) / output_root.name
        staging.mkdir(parents=True)
        validation_root = staging / "validation_models"
        reproduction = _reconstruct_reference_models(
            primary, full_history, folds, frozen, direct_reference, validation_root
        )
        if not reproduction["passed"]:
            raise RuntimeError(
                "Frozen row-level reproduction failed; no deployment bundle was emitted"
            )
        deployment = _fit_deployment_models(
            primary, full_history, frozen, direct_reference, staging
        )
        feature_manifests = {
            "direct": feature_manifest("CORE_ROTATION", include_player_id=True),
            "minutes": phase4b_feature_manifest("MINUTES_ROLE"),
            "production": phase4b_feature_manifest("PRODUCTION"),
        }
        preprocessing = {
            "catboost_categorical_missing_value": "__MISSING__",
            "numeric_missing_value": "NaN",
            "feature_order_is_manifest_order": True,
            "categorical_features": {
                name: value["categorical"] for name, value in feature_manifests.items()
            },
            "training_state_fingerprints": {
                name: component["training_state_fingerprint"]
                for name, component in deployment.items()
            },
        }
        _write_json(staging / "feature_manifests.json", feature_manifests)
        _write_json(staging / "preprocessing.json", preprocessing)
        _write_json(staging / "reproduction.json", reproduction)
        code_files = [
            "src/modeling/ml_experiments.py", "src/modeling/ml_protocol.py",
            "src/modeling/phase4b_models.py", "src/modeling/phase4b_protocol.py",
            "src/modeling/phase4b_deployment.py",
        ]
        code_hashes = {name: _file_sha(PROJECT_ROOT / name) for name in code_files}
        code_protocol_fingerprint = _json_sha(code_hashes)
        file_hashes = {
            str(path.relative_to(staging)): _file_sha(path)
            for path in sorted(staging.rglob("*")) if path.is_file()
        }
        bundle_fingerprint = _json_sha(file_hashes)
        manifest = {
            "status": "FROZEN",
            "bundle_identifier": BUNDLE_IDENTIFIER,
            "performance_model_version": BUNDLE_IDENTIFIER,
            "protocol_version": PHASE4B_PROTOCOL_VERSION,
            "phase4a_direct_protocol_version": PHASE4A_PROTOCOL_VERSION,
            "deployment_protocol": "latest_frozen_fold_configuration_refit_all_pre_E2026",
            "random_seed": DEFAULT_RANDOM_SEED,
            "model_library_version": "catboost==1.2.10",
            "created_at": datetime.now(UTC).isoformat(),
            "source_fitted_objects_recovered": False,
            "reconstruction_kind": "DETERMINISTIC_REFIT_FROM_FROZEN_SELECTIONS",
            "dataset_versions": {
                "phase4a": PHASE4A_DATASET_VERSION,
                "phase4b": PHASE4B_DATASET_VERSION,
            },
            "training_scoring_rule_version": TARGET_RULE_VERSION,
            "training_scoring_rule_fingerprint": scoring_rule_fingerprint(),
            "hybrid": {"weight_direct": 0.25, "weight_decomposed": 0.75},
            "components": deployment,
            "validation_models": reproduction["serialized_models"],
            "reproduction_gate": {
                key: reproduction[key] for key in (
                    "passed", "tolerance", "rows", "maximum_absolute_difference",
                    "mean_absolute_difference", "reproduced_metrics",
                    "accepted_metrics", "component_differences",
                )
            },
            "training_data_fingerprint": _json_sha({
                name: component["training_data_fingerprint"]
                for name, component in deployment.items()
            }),
            "code_files": code_hashes,
            "code_protocol_fingerprint": code_protocol_fingerprint,
            "files": file_hashes,
            "bundle_fingerprint": bundle_fingerprint,
            "frozen_reference_prediction_fingerprint": frozen["prediction_fingerprint"],
        }
        _write_json(staging / "manifest.json", manifest)
        output_root.parent.mkdir(parents=True, exist_ok=True)
        if output_root.exists():
            backup = output_root.with_name(output_root.name + ".previous")
            if backup.exists():
                raise FileExistsError(f"Refusing to overwrite bundle backup: {backup}")
            output_root.rename(backup)
            try:
                shutil.move(str(staging), str(output_root))
            except BaseException:
                backup.rename(output_root)
                raise
            shutil.rmtree(backup)
        else:
            shutil.move(str(staging), str(output_root))
    return manifest


def verify_serialized_reproduction(
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
) -> dict[str, Any]:
    """Score historical outer rows with serialized validation models."""

    predictions = serialized_validation_predictions(
        database_path=database_path, bundle_root=bundle_root
    )
    return _compare_with_references(predictions)


def serialized_validation_predictions(
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
) -> pd.DataFrame:
    """Return predictions made by the serialized chronological fold models."""

    manifest = _read_json(bundle_root / "manifest.json")
    primary = load_phase4b_frame(database_path, primary_only=True)
    folds = build_phase4b_folds(primary)
    return _predict_serialized_validation_models(primary, folds, manifest, bundle_root)


def _reconstruct_reference_models(
    primary: pd.DataFrame,
    full_history: pd.DataFrame,
    folds: list[dict[str, Any]],
    frozen: dict[str, Any],
    direct_reference: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    root.mkdir(parents=True)
    predictions: list[pd.DataFrame] = []
    serialized: dict[str, dict[str, Any]] = {}
    for fold in folds:
        fold_id = fold["definition"].fold_id
        fold_root = root / fold_id
        fold_root.mkdir()
        test = primary.loc[fold["outer_test"]].copy()
        direct_train = primary.loc[fold["outer_train"]].copy()
        direct_selection = direct_reference["selections"][fold_id]
        direct_manifest = feature_manifest("CORE_ROTATION", include_player_id=True)
        direct_model, direct_state, _ = fit_final_model(
            model_type="catboost", manifest=direct_manifest, train=direct_train,
            train_target=PRIMARY_TARGET_COLUMN,
            parameters=direct_selection["parameters"],
            final_iterations=direct_selection["final_iterations"], seed=DEFAULT_RANDOM_SEED,
        )
        direct_path = fold_root / "direct.json"
        _save_canonical_catboost(direct_model, direct_path)
        direct_model = _load_canonical_catboost(direct_path)
        direct_prediction = predict_model("catboost", direct_model, test, direct_manifest)

        component_train = add_older_history(
            primary.loc[fold["outer_train"]].copy(), full_history,
            cutoff=test["target_game_time"].min(),
        )
        minute_selection = _fold_selection(frozen["minutes_component"], fold_id)
        minute_parameters = json.loads(minute_selection["parameters"])
        minute_manifest = phase4b_feature_manifest("MINUTES_ROLE")
        minute_target = training_target(
            component_train, "minutes", "minutes", minute_parameters
        )
        minute_model, minute_state, _ = fit_final_component(
            "catboost", minute_manifest, component_train, minute_target["values"],
            minute_parameters, minute_selection["final_iterations"],
            minute_target["weights"], seed=DEFAULT_RANDOM_SEED,
        )
        minute_path = fold_root / "minutes.json"
        _save_canonical_catboost(minute_model, minute_path)
        minute_model = _load_canonical_catboost(minute_path)
        minute_prediction = predict_component(
            "catboost", minute_model, test, minute_manifest
        )

        production_selection = _fold_selection(
            frozen["production_component"], fold_id
        )
        production_parameters = json.loads(production_selection["parameters"])
        production_manifest = phase4b_feature_manifest("PRODUCTION")
        production_target = training_target(
            component_train, "production", "weighted", production_parameters
        )
        production_model, production_state, _ = fit_final_component(
            "catboost", production_manifest, component_train,
            production_target["values"], production_parameters,
            production_selection["final_iterations"], production_target["weights"],
            seed=DEFAULT_RANDOM_SEED,
        )
        production_path = fold_root / "production.json"
        _save_canonical_catboost(production_model, production_path)
        production_model = _load_canonical_catboost(production_path)
        production_prediction = predict_component(
            "catboost", production_model, test, production_manifest
        )
        predictions.append(pd.DataFrame({
            "model_row_id": test["model_row_id"].to_numpy(),
            "outer_fold": fold_id,
            "actual_fp": test["actual_fantasy_points"].to_numpy(float),
            "direct_prediction": direct_prediction,
            "predicted_minutes": minute_prediction,
            "predicted_fp_per_min": production_prediction,
        }))
        serialized[fold_id] = {
            "direct": _serialized_model_entry(root.parent, direct_path, direct_state),
            "minutes": _serialized_model_entry(root.parent, minute_path, minute_state),
            "production": _serialized_model_entry(root.parent, production_path, production_state),
        }
    result = _compare_with_references(pd.concat(predictions, ignore_index=True))
    result["serialized_models"] = serialized
    return result


def _predict_serialized_validation_models(
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    manifest: dict[str, Any],
    root: Path,
) -> pd.DataFrame:
    from catboost import CatBoostRegressor

    rows: list[pd.DataFrame] = []
    manifests = {
        "direct": feature_manifest("CORE_ROTATION", include_player_id=True),
        "minutes": phase4b_feature_manifest("MINUTES_ROLE"),
        "production": phase4b_feature_manifest("PRODUCTION"),
    }
    for fold in folds:
        fold_id = fold["definition"].fold_id
        test = primary.loc[fold["outer_test"]].copy()
        predicted: dict[str, np.ndarray] = {}
        for name, value in manifest["validation_models"][fold_id].items():
            path = root / value["model_path"]
            if _file_sha(path) != value["model_sha256"]:
                raise RuntimeError(f"Corrupt serialized validation model: {path}")
            model = CatBoostRegressor()
            model.load_model(str(path), format="json")
            predicted[name] = np.asarray(
                model.predict(_catboost_feature_frame(test, manifests[name])), dtype=float
            )
        rows.append(pd.DataFrame({
            "model_row_id": test["model_row_id"].to_numpy(),
            "outer_fold": fold_id,
            "actual_fp": test["actual_fantasy_points"].to_numpy(float),
            "direct_prediction": predicted["direct"],
            "predicted_minutes": predicted["minutes"],
            "predicted_fp_per_min": predicted["production"],
        }))
    return pd.concat(rows, ignore_index=True)


def _compare_with_references(predictions: pd.DataFrame) -> dict[str, Any]:
    with duckdb.connect() as connection:
        direct = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_fp FROM read_parquet(?)",
            [str(DIRECT_SELECTION_REFERENCE.parent / "predictions.parquet")],
        ).df()
        minutes = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_minutes FROM read_parquet(?) "
            "WHERE experiment_id='p4b__minutes__catboost__role_older_history'",
            [str(REFERENCE_ROOT / "ml_phase4b_minutes_predictions_v1.parquet")],
        ).df()
        production = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_fp_per_min FROM read_parquet(?) "
            "WHERE experiment_id='p4b__production__catboost__weighted_older_history'",
            [str(REFERENCE_ROOT / "ml_phase4b_production_predictions_v1.parquet")],
        ).df()
        hybrid = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_fp,actual_fp FROM read_parquet(?) "
            "WHERE architecture='hybrid'",
            [str(REFERENCE_ROOT / "ml_phase4b_outer_predictions_v1.parquet")],
        ).df()
    merged = predictions.merge(
        direct.rename(columns={"predicted_fp": "reference_direct"}),
        on=["model_row_id", "outer_fold"], validate="one_to_one",
    ).merge(
        minutes.rename(columns={"predicted_minutes": "reference_minutes"}),
        on=["model_row_id", "outer_fold"], validate="one_to_one",
    ).merge(
        production.rename(columns={"predicted_fp_per_min": "reference_production"}),
        on=["model_row_id", "outer_fold"], validate="one_to_one",
    ).merge(
        hybrid.rename(columns={"predicted_fp": "reference_hybrid",
                               "actual_fp": "reference_actual"}),
        on=["model_row_id", "outer_fold"], validate="one_to_one",
    )
    merged["reconstructed_hybrid"] = (
        0.25 * merged["direct_prediction"].to_numpy(float)
        + 0.75 * (
            merged["predicted_minutes"].to_numpy(float)
            * merged["predicted_fp_per_min"].to_numpy(float)
        )
    )
    component_pairs = {
        "direct": ("direct_prediction", "reference_direct"),
        "minutes": ("predicted_minutes", "reference_minutes"),
        "production": ("predicted_fp_per_min", "reference_production"),
        "hybrid": ("reconstructed_hybrid", "reference_hybrid"),
    }
    differences = {}
    for name, (current, reference) in component_pairs.items():
        difference = np.abs(
            merged[current].to_numpy(float) - merged[reference].to_numpy(float)
        )
        differences[name] = {
            "maximum_absolute_difference": float(np.max(difference)),
            "mean_absolute_difference": float(np.mean(difference)),
            "bitwise_identical": bool(np.array_equal(
                merged[current].to_numpy(float), merged[reference].to_numpy(float)
            )),
        }
    metrics = regression_metrics(
        merged["reference_actual"].to_numpy(float),
        merged["reconstructed_hybrid"].to_numpy(float),
    )
    accepted = _read_json(FINAL_MODEL_REFERENCE)["metrics"]
    maximum = differences["hybrid"]["maximum_absolute_difference"]
    mean = differences["hybrid"]["mean_absolute_difference"]
    passed = (
        maximum <= STRICT_PREDICTION_TOLERANCE
        and abs(float(metrics["mae"]) - ACCEPTED_MAE) <= STRICT_PREDICTION_TOLERANCE
        and all(differences[name]["maximum_absolute_difference"]
                <= STRICT_PREDICTION_TOLERANCE
                for name in ("direct", "minutes", "production"))
    )
    return {
        "passed": bool(passed), "tolerance": STRICT_PREDICTION_TOLERANCE,
        "rows": len(merged), "maximum_absolute_difference": maximum,
        "mean_absolute_difference": mean, "component_differences": differences,
        "reproduced_metrics": metrics,
        "accepted_metrics": {
            key: accepted[key] for key in ("mae", "rmse", "spearman", "pearson")
        },
    }


def _fit_deployment_models(
    primary: pd.DataFrame,
    full_history: pd.DataFrame,
    frozen: dict[str, Any],
    direct_reference: dict[str, Any],
    root: Path,
) -> dict[str, dict[str, Any]]:
    cutoff = pd.to_datetime(primary["target_game_time"], utc=True).max() + pd.Timedelta(1, "ns")
    component_train = add_older_history(primary.copy(), full_history, cutoff=cutoff)
    direct_selection = direct_reference["selections"][LATEST_FROZEN_FOLD]
    direct_manifest = feature_manifest("CORE_ROTATION", include_player_id=True)
    direct_model, direct_state, _ = fit_final_model(
        model_type="catboost", manifest=direct_manifest, train=primary,
        train_target=PRIMARY_TARGET_COLUMN, parameters=direct_selection["parameters"],
        final_iterations=direct_selection["final_iterations"], seed=DEFAULT_RANDOM_SEED,
    )
    direct_path = root / "direct.json"
    _save_canonical_catboost(direct_model, direct_path)

    minute_selection = _fold_selection(frozen["minutes_component"], LATEST_FROZEN_FOLD)
    minute_parameters = json.loads(minute_selection["parameters"])
    minute_manifest = phase4b_feature_manifest("MINUTES_ROLE")
    minute_target = training_target(
        component_train, "minutes", "minutes", minute_parameters
    )
    minute_model, minute_state, _ = fit_final_component(
        "catboost", minute_manifest, component_train, minute_target["values"],
        minute_parameters, minute_selection["final_iterations"], minute_target["weights"],
        seed=DEFAULT_RANDOM_SEED,
    )
    minute_path = root / "minutes.json"
    _save_canonical_catboost(minute_model, minute_path)

    production_selection = _fold_selection(
        frozen["production_component"], LATEST_FROZEN_FOLD
    )
    production_parameters = json.loads(production_selection["parameters"])
    production_manifest = phase4b_feature_manifest("PRODUCTION")
    production_target = training_target(
        component_train, "production", "weighted", production_parameters
    )
    production_model, production_state, _ = fit_final_component(
        "catboost", production_manifest, component_train,
        production_target["values"], production_parameters,
        production_selection["final_iterations"], production_target["weights"],
        seed=DEFAULT_RANDOM_SEED,
    )
    production_path = root / "production.json"
    _save_canonical_catboost(production_model, production_path)
    return {
        "direct": _deployment_entry(
            root, direct_path, direct_manifest, primary, PRIMARY_TARGET_COLUMN,
            direct_selection, direct_state,
            "p4a__catboost__core_rotation__verified_only__player__s17",
        ),
        "minutes": _deployment_entry(
            root, minute_path, minute_manifest, component_train, "target_minutes",
            minute_selection, minute_state,
            frozen["minutes_component"]["experiment_id"],
        ),
        "production": _deployment_entry(
            root, production_path, production_manifest, component_train,
            "actual_fantasy_points/target_minutes", production_selection,
            production_state, frozen["production_component"]["experiment_id"],
            target_strategy="weighted",
        ),
    }


def _deployment_entry(
    root: Path,
    path: Path,
    manifest: dict[str, Any],
    train: pd.DataFrame,
    target: str,
    selection: dict[str, Any],
    state: str,
    experiment_id: str,
    *,
    target_strategy: str | None = None,
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id, "model_type": "catboost",
        "model_format": "json",
        "model_path": str(path.relative_to(root)), "model_sha256": _file_sha(path),
        "feature_manifest": manifest, "preprocessing_path": "preprocessing.json",
        "feature_manifests_path": "feature_manifests.json",
        "training_rows": len(train), "training_max_time": train.target_game_time.max().isoformat(),
        "training_data_fingerprint": _frame_fingerprint(train, manifest, target),
        "training_state_fingerprint": state, "target": target,
        "target_strategy": target_strategy,
        "selected_from_fold": LATEST_FROZEN_FOLD,
        "hyperparameters": (
            json.loads(selection["parameters"])
            if isinstance(selection["parameters"], str)
            else selection["parameters"]
        ),
        "iterations": int(selection["final_iterations"]),
    }


def _serialized_model_entry(root: Path, path: Path, state: str) -> dict[str, Any]:
    return {
        "model_type": "catboost", "model_format": "json",
        "model_path": str(path.relative_to(root)),
        "model_sha256": _file_sha(path), "training_state_fingerprint": state,
    }


def _frame_fingerprint(frame: pd.DataFrame, manifest: dict[str, Any], target: str) -> str:
    columns = ["model_row_id", *manifest["features"]]
    if target in frame.columns:
        columns.append(target)
    normalized = frame.sort_values(
        ["target_game_time", "game_id", "player_id"]
    )[columns].copy()
    digest = hashlib.sha256()
    digest.update("\n".join(columns).encode())
    digest.update(pd.util.hash_pandas_object(normalized, index=False).to_numpy().tobytes())
    if target == "actual_fantasy_points/target_minutes":
        values = (
            frame.sort_values(["target_game_time", "game_id", "player_id"])
            ["actual_fantasy_points"].to_numpy(float)
            / frame.sort_values(["target_game_time", "game_id", "player_id"])
            ["target_minutes"].to_numpy(float)
        )
        digest.update(values.tobytes())
    return digest.hexdigest()


def _fold_selection(component: dict[str, Any], fold_id: str) -> dict[str, Any]:
    return next(
        value for value in component["fold_selections"]
        if value["outer_fold"] == fold_id
    )


def _save_canonical_catboost(model: Any, path: Path) -> None:
    """Serialize inference state without CatBoost's volatile GUID/timestamp."""

    model.save_model(str(path), format="json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    model_info = payload.get("model_info", {})
    model_info.pop("model_guid", None)
    model_info.pop("train_finish_time", None)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _load_canonical_catboost(path: Path) -> Any:
    from catboost import CatBoostRegressor

    model = CatBoostRegressor()
    model.load_model(str(path), format="json")
    return model


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


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
