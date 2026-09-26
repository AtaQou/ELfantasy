"""Bounded chronological research and deployment packaging for Phase 6B."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import pickle
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from src.db.database import DEFAULT_DATABASE_PATH

from .ml_experiments import _catboost_feature_frame
from .ml_protocol import PRIMARY_TARGET_COLUMN, feature_manifest
from .phase4b_deployment import _save_canonical_catboost
from .phase4b_models import add_older_history
from .phase4b_protocol import load_phase4b_frame
from .phase6b_protocol import (
    MODEL_FAMILY_CANDIDATES,
    PHASE6B_CALIBRATION_VERSION,
    PHASE6B_MODEL_VERSION,
    PHASE6B_PROTOCOL_VERSION,
    PHASE6B_RANDOM_SEED,
    PRIMARY_DATASET_SEASONS,
    QUANTILE_FEATURE_CANDIDATES,
    QUANTILES,
    THRESHOLDS,
    actual_minute_band,
    finite_sample_quantile,
    load_phase6b_frame,
    payload_fingerprint,
    phase6b_folds,
    pinball_loss,
    probability_violation_rate,
    protocol_payload,
    quantile_crossing_rate,
    quantile_metrics,
    reconcile_distribution,
    retained_classifier_thresholds,
    role_band,
    threshold_base_rates,
    weighted_interval_score,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE6B_ROOT = PROJECT_ROOT / "data" / "derived" / "phase6b"
DEFAULT_RESEARCH_ROOT = DEFAULT_PHASE6B_ROOT / "research"
DEFAULT_BUNDLE_ROOT = DEFAULT_PHASE6B_ROOT / "frozen_prediction"
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase6b"
PHASE4B_REFERENCE_ROOT = PROJECT_ROOT / "data" / "derived" / "phase4b"
PHASE5B_IMPACT_PATH = (
    PROJECT_ROOT / "data" / "derived" / "phase5b"
    / "ml_phase5b_fantasy_impact_v1.parquet"
)
CENTRAL_SELECTION_RULE = {
    "minimum_rmse_relative_improvement": 0.005,
    "maximum_absolute_mean_bias": 0.20,
    "maximum_mae_degradation": 0.10,
}
PHASE6B_CODE_FILES = (
    "src/modeling/ml_experiments.py",
    "src/modeling/ml_protocol.py",
    "src/modeling/phase4b_protocol.py",
    "src/modeling/phase6b_protocol.py",
    "src/modeling/phase6b_runner.py",
)


@dataclass(slots=True)
class QuantileRun:
    model_family: str
    feature_set: str
    quantile: float
    older_history: bool
    predictions: pd.DataFrame
    audits: list[dict[str, Any]]

    @property
    def weighted_inner_pinball(self) -> float:
        return float(np.average(
            [row["inner_raw_pinball"] for row in self.audits],
            weights=[row["calibration_rows"] for row in self.audits],
        ))


def run_phase6b_research(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    research_root: Path = DEFAULT_RESEARCH_ROOT,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Run the predeclared bounded research and freeze the winning distribution."""

    result_path = sample_root / "results.json"
    if result_path.exists() and not force:
        return _read_json(result_path)
    frame = load_phase6b_frame(database_path)
    folds = phase6b_folds(frame)
    full_history = load_phase4b_frame(database_path, primary_only=False)
    base_rates = threshold_base_rates(frame)
    retained = retained_classifier_thresholds(base_rates)
    central = _central_analysis(frame)

    candidate_runs: dict[tuple[str, str], QuantileRun] = {}
    for feature_set in QUANTILE_FEATURE_CANDIDATES:
        run = run_quantile_candidate(
            frame, folds, quantile=0.90, model_family="catboost",
            feature_set=feature_set,
        )
        candidate_runs[("catboost", feature_set)] = run
    selected_feature = min(
        QUANTILE_FEATURE_CANDIDATES,
        key=lambda name: candidate_runs[("catboost", name)].weighted_inner_pinball,
    )
    lightgbm = run_quantile_candidate(
        frame, folds, quantile=0.90, model_family="lightgbm",
        feature_set=selected_feature,
    )
    candidate_runs[("lightgbm", selected_feature)] = lightgbm
    selected_family = min(
        MODEL_FAMILY_CANDIDATES,
        key=lambda name: candidate_runs[(name, selected_feature)].weighted_inner_pinball,
    )

    older = run_quantile_candidate(
        frame, folds, quantile=0.90, model_family=selected_family,
        feature_set=selected_feature, full_history=full_history,
        older_history=True,
    )
    verified = candidate_runs[(selected_family, selected_feature)]
    older_helped = all(
        older.audits[index]["inner_raw_pinball"]
        < verified.audits[index]["inner_raw_pinball"]
        for index in range(len(folds))
    )

    quantile_runs: dict[float, QuantileRun] = {}
    for quantile in QUANTILES:
        if quantile == 0.90 and not older_helped:
            quantile_runs[quantile] = verified
        else:
            quantile_runs[quantile] = run_quantile_candidate(
                frame, folds, quantile=quantile, model_family=selected_family,
                feature_set=selected_feature, full_history=full_history,
                older_history=older_helped,
            )

    probability_runs = {
        threshold: run_threshold_classifier(
            frame, folds, threshold=threshold, feature_set=selected_feature,
            full_history=full_history if older_helped else None,
            older_history=older_helped,
        )
        for threshold in retained
    }
    predictions = _assemble_predictions(
        frame, central, quantile_runs, probability_runs, retained
    )
    quantile_summary = _quantile_summary(predictions, quantile_runs)
    probability_summary = _probability_summary(predictions, probability_runs, base_rates)
    tail = _tail_diagnostics(predictions)
    role_calibration = _role_calibration(predictions)
    absence = _known_absence_diagnostic(predictions)
    cases = _case_studies(predictions, absence.get("rows_frame"))
    absence.pop("rows_frame", None)

    research_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(predictions, research_root / "outer_predictions.parquet")
    candidate_summary = [
        _candidate_summary(run) for run in candidate_runs.values()
    ]
    result = {
        "status": "FROZEN", "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol_payload(),
        "protocol_fingerprint": payload_fingerprint(),
        "dataset": _dataset_summary(frame, folds, base_rates),
        "central_analysis": central["summary"],
        "quantile_candidate_comparison": candidate_summary,
        "selected_quantile_feature_set": selected_feature,
        "selected_quantile_model_family": selected_family,
        "older_standardized_history": {
            "tested": True, "adopted": bool(older_helped),
            "verified_only": _candidate_summary(verified),
            "verified_plus_standardized": _candidate_summary(older),
            "adoption_rule": "strict inner-pinball improvement in every fold",
        },
        "quantile_models": quantile_summary,
        "high_score_probabilities": probability_summary,
        "role_and_player_type_calibration": role_calibration,
        "tail_diagnostics": tail,
        "known_absence_diagnostic": absence,
        "case_studies": cases,
        "final_distribution": {
            "expected_fp_source": central["summary"]["selected_expected_fp_source"],
            "median_fp_source": "calibrated reconciled direct P50",
            "quantile_reconciliation": "row-wise increasing rearrangement",
            "probability_reconciliation": "decreasing PAVA plus quantile bounds",
            "weighted_interval_score": weighted_interval_score(
                predictions.actual_fp.to_numpy(float),
                predictions[[f"p{int(q*100):02d}_fp" for q in QUANTILES]].to_numpy(float),
            ),
            "user_facing_ceiling": "P90",
            "legacy_phase4b_interval_preserved": True,
            "fields": _probabilistic_fields(),
        },
    }
    result["research_fingerprint"] = _json_sha({
        "dataset": result["dataset"]["dataset_fingerprint"],
        "predictions": _prediction_fingerprint(predictions),
        "protocol": result["protocol_fingerprint"],
    })
    bundle = package_phase6b_bundle(
        frame, result, selected_family=selected_family,
        selected_feature=selected_feature, retained_thresholds=retained,
        older_history=older_helped, full_history=full_history,
        output_root=bundle_root,
    )
    result["deployment_bundle"] = bundle
    _write_json(research_root / "results.json", result)
    _write_json(result_path, result)
    return result


def run_quantile_candidate(
    frame: pd.DataFrame,
    folds: Sequence[Mapping[str, Any]],
    *,
    quantile: float,
    model_family: str,
    feature_set: str,
    full_history: pd.DataFrame | None = None,
    older_history: bool = False,
) -> QuantileRun:
    manifest = feature_manifest(feature_set, include_player_id=True)
    rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for fold in folds:
        fold_id = fold["definition"].fold_id
        train = frame.loc[fold["inner_train"]].copy()
        calibration = frame.loc[fold["inner_validation"]].copy()
        test = frame.loc[fold["outer_test"]].copy()
        if older_history:
            if full_history is None:
                raise ValueError("older-history quantile run requires full history")
            train = add_older_history(
                train, full_history, cutoff=calibration.target_game_time.min()
            )
        target_train = _training_fp(train)
        target_calibration = calibration[PRIMARY_TARGET_COLUMN].to_numpy(float)
        model, best_iterations = _fit_quantile_model(
            model_family, manifest, train, target_train, calibration,
            target_calibration, quantile,
        )
        raw_calibration = _predict_model(model_family, model, calibration, manifest)
        offset, role_offsets = _quantile_calibration_offsets(
            calibration, target_calibration - raw_calibration, quantile
        )
        raw_test = _predict_model(model_family, model, test, manifest)
        test_roles = role_band(test)
        applied_offset = test_roles.map(role_offsets).fillna(offset).to_numpy(float)
        predicted = raw_test + applied_offset
        context = test[_context_columns(test)].copy()
        context["outer_fold"] = fold_id
        context["actual_fp"] = test[PRIMARY_TARGET_COLUMN].to_numpy(float)
        context["raw_prediction"] = raw_test
        context["calibrated_prediction"] = predicted
        rows.append(context)
        audits.append({
            "outer_fold": fold_id, "model_family": model_family,
            "feature_set": feature_set, "quantile": quantile,
            "older_history": older_history, "base_train_rows": len(train),
            "calibration_rows": len(calibration), "outer_test_rows": len(test),
            "base_train_max_time": train.target_game_time.max().isoformat(),
            "calibration_min_time": calibration.target_game_time.min().isoformat(),
            "calibration_max_time": calibration.target_game_time.max().isoformat(),
            "outer_test_min_time": test.target_game_time.min().isoformat(),
            "outer_test_rows_seen": 0, "calibration_offset": offset,
            "role_calibration_offsets": role_offsets,
            "role_calibration_minimum_rows": 100,
            "final_iterations": best_iterations,
            "inner_raw_pinball": pinball_loss(
                target_calibration, raw_calibration, quantile
            ),
            "inner_calibrated_pinball": pinball_loss(
                target_calibration, raw_calibration + offset, quantile
            ),
            "inner_raw_coverage": float(np.mean(target_calibration <= raw_calibration)),
            "inner_calibrated_coverage": float(
                np.mean(target_calibration <= raw_calibration + offset)
            ),
        })
    return QuantileRun(
        model_family, feature_set, quantile, older_history,
        pd.concat(rows, ignore_index=True), audits,
    )


def run_threshold_classifier(
    frame: pd.DataFrame,
    folds: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
    feature_set: str,
    full_history: pd.DataFrame | None = None,
    older_history: bool = False,
) -> dict[str, Any]:
    manifest = feature_manifest(feature_set, include_player_id=True)
    rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for fold in folds:
        fold_id = fold["definition"].fold_id
        train = frame.loc[fold["inner_train"]].copy()
        calibration = frame.loc[fold["inner_validation"]].copy()
        test = frame.loc[fold["outer_test"]].copy()
        if older_history:
            if full_history is None:
                raise ValueError("older-history classifier requires full history")
            train = add_older_history(
                train, full_history, cutoff=calibration.target_game_time.min()
            )
        y_train = (_training_fp(train) >= threshold).astype(int)
        y_cal = (calibration[PRIMARY_TARGET_COLUMN].to_numpy(float) >= threshold).astype(int)
        model, iterations = _fit_classifier(
            manifest, train, y_train, calibration, y_cal
        )
        raw_cal = _predict_classifier(model, calibration, manifest)
        calibrator = _fit_platt(raw_cal, y_cal)
        raw_test = _predict_classifier(model, test, manifest)
        calibrated_test = calibrator.predict_proba(_logit(raw_test).reshape(-1, 1))[:, 1]
        context = test[_context_columns(test)].copy()
        context["outer_fold"] = fold_id
        context["actual_fp"] = test[PRIMARY_TARGET_COLUMN].to_numpy(float)
        context["label"] = (
            test[PRIMARY_TARGET_COLUMN].to_numpy(float) >= threshold
        ).astype(int)
        context["raw_probability"] = raw_test
        context["calibrated_probability"] = calibrated_test
        rows.append(context)
        audits.append({
            "outer_fold": fold_id, "threshold": threshold,
            "base_train_rows": len(train), "calibration_rows": len(calibration),
            "outer_test_rows": len(test), "base_positive_examples": int(y_train.sum()),
            "calibration_positive_examples": int(y_cal.sum()),
            "base_train_max_time": train.target_game_time.max().isoformat(),
            "calibration_min_time": calibration.target_game_time.min().isoformat(),
            "calibration_max_time": calibration.target_game_time.max().isoformat(),
            "outer_test_min_time": test.target_game_time.min().isoformat(),
            "outer_test_rows_seen": 0, "final_iterations": iterations,
            "platt_coefficient": float(calibrator.coef_[0, 0]),
            "platt_intercept": float(calibrator.intercept_[0]),
        })
    predictions = pd.concat(rows, ignore_index=True)
    return {
        "threshold": threshold, "feature_set": feature_set,
        "older_history": older_history, "predictions": predictions,
        "audits": audits,
    }


def package_phase6b_bundle(
    frame: pd.DataFrame,
    research: Mapping[str, Any],
    *,
    selected_family: str,
    selected_feature: str,
    retained_thresholds: Sequence[float],
    older_history: bool,
    full_history: pd.DataFrame,
    output_root: Path = DEFAULT_BUNDLE_ROOT,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = feature_manifest(selected_feature, include_player_id=True)
    train = frame[frame.season.isin(("E2022", "E2023", "E2024"))].copy()
    calibration = frame[frame.season.eq("E2025")].copy()
    if older_history:
        train = add_older_history(
            train, full_history, cutoff=calibration.target_game_time.min()
        )
    quantile_entries: dict[str, Any] = {}
    for quantile in QUANTILES:
        model, iterations = _fit_quantile_model(
            selected_family, manifest, train, _training_fp(train), calibration,
            calibration[PRIMARY_TARGET_COLUMN].to_numpy(float), quantile,
        )
        raw_cal = _predict_model(selected_family, model, calibration, manifest)
        offset, role_offsets = _quantile_calibration_offsets(
            calibration,
            calibration[PRIMARY_TARGET_COLUMN].to_numpy(float) - raw_cal,
            quantile,
        )
        extension = "json" if selected_family == "catboost" else "pkl"
        name = f"q{int(quantile * 100):02d}.{extension}"
        path = output_root / name
        if selected_family == "catboost":
            _save_canonical_catboost(model, path)
        else:
            with path.open("wb") as handle:
                pickle.dump(model, handle, protocol=5)
        quantile_entries[f"{quantile:.2f}"] = {
            "model_path": name, "model_sha256": _file_sha(path),
            "model_type": selected_family,
            "model_format": "json" if selected_family == "catboost" else "pickle",
            "iterations": iterations, "calibration_offset": offset,
            "role_calibration_offsets": role_offsets,
        }
    classifier_entries: dict[str, Any] = {}
    for threshold in retained_thresholds:
        y_train = (_training_fp(train) >= threshold).astype(int)
        y_cal = (
            calibration[PRIMARY_TARGET_COLUMN].to_numpy(float) >= threshold
        ).astype(int)
        model, iterations = _fit_classifier(
            manifest, train, y_train, calibration, y_cal
        )
        raw_cal = _predict_classifier(model, calibration, manifest)
        calibrator = _fit_platt(raw_cal, y_cal)
        name = f"ge_{int(threshold)}.json"
        path = output_root / name
        _save_canonical_catboost(model, path)
        classifier_entries[str(int(threshold))] = {
            "model_path": name, "model_sha256": _file_sha(path),
            "model_type": "catboost", "model_format": "json",
            "iterations": iterations,
            "platt_coefficient": float(calibrator.coef_[0, 0]),
            "platt_intercept": float(calibrator.intercept_[0]),
        }
    mean_entry = _package_mean_model(frame, output_root)
    calibration_payload = {
        "version": PHASE6B_CALIBRATION_VERSION,
        "quantiles": {
            key: {
                "global_offset": value["calibration_offset"],
                "role_offsets": value["role_calibration_offsets"],
                "role_definition": "season_minutes_avg_before bands",
                "minimum_role_calibration_rows": 100,
            } for key, value in quantile_entries.items()
        },
        "classifiers": {
            key: {
                "coefficient": value["platt_coefficient"],
                "intercept": value["platt_intercept"],
            } for key, value in classifier_entries.items()
        },
        "quantile_reconciliation": "INCREASING_REARRANGEMENT",
        "probability_reconciliation": "DECREASING_PAVA_QUANTILE_BOUNDS",
        "availability_adjustment": "PHASE5B_LOCATION_SHIFT",
    }
    _write_json(output_root / "calibration.json", calibration_payload)
    _write_json(output_root / "feature_manifest.json", manifest)
    _write_json(output_root / "protocol.json", protocol_payload())
    files = {
        str(path.relative_to(output_root)): _file_sha(path)
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    code_files = {
        relative: _file_sha(PROJECT_ROOT / relative)
        for relative in PHASE6B_CODE_FILES
    }
    code_protocol_fingerprint = _json_sha(code_files)
    training_fingerprint = _frame_fingerprint(frame, manifest)
    payload = {
        "status": "FROZEN", "bundle_identifier": PHASE6B_MODEL_VERSION,
        "probabilistic_model_version": PHASE6B_MODEL_VERSION,
        "calibration_version": PHASE6B_CALIBRATION_VERSION,
        "protocol_version": PHASE6B_PROTOCOL_VERSION,
        "protocol_fingerprint": payload_fingerprint(),
        "created_at": datetime.now(UTC).isoformat(), "random_seed": PHASE6B_RANDOM_SEED,
        "conditional_on_participation": True, "excludes_zero_minute_rows": True,
        "target_rule_version": research["protocol"]["target_rule_version"],
        "target_rule_fingerprint": research["protocol"]["target_rule_fingerprint"],
        "feature_manifest": manifest, "selected_model_family": selected_family,
        "selected_feature_set": selected_feature, "older_standardized_history": older_history,
        "training_data_fingerprint": training_fingerprint,
        "code_files": code_files,
        "code_protocol_fingerprint": code_protocol_fingerprint,
        "mean_model": mean_entry, "quantile_models": quantile_entries,
        "classifier_models": classifier_entries,
        "retained_direct_thresholds": list(retained_thresholds),
        "derived_thresholds": [
            value for value in THRESHOLDS if value not in retained_thresholds
        ],
        "reconciliation": calibration_payload,
        "outer_evaluation_fingerprint": research.get("research_fingerprint"),
        "outer_evaluation_gate": {
            "passed": True,
            "rows": research["dataset"]["outer_evaluation_rows"],
            "final_quantile_crossing_rate": research["quantile_models"][
                "final_crossing_rate"
            ],
            "final_probability_order_violation_rate": research[
                "high_score_probabilities"
            ]["final_probability_order_violation_rate"],
            "quantile_metrics": [
                row["final"] for row in research["quantile_models"]["metrics"]
            ],
        },
        "files": files,
    }
    payload["bundle_fingerprint"] = _json_sha({
        "files": files, "code_files": code_files,
        "protocol_fingerprint": payload["protocol_fingerprint"],
    })
    _write_json(output_root / "manifest.json", payload)
    return {
        "bundle_identifier": PHASE6B_MODEL_VERSION,
        "bundle_fingerprint": payload["bundle_fingerprint"],
        "training_data_fingerprint": training_fingerprint,
        "code_protocol_fingerprint": code_protocol_fingerprint,
        "files": files,
    }


def repackage_phase6b_bundle(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    result_path: Path = DEFAULT_SAMPLE_ROOT / "results.json",
    output_root: Path = DEFAULT_BUNDLE_ROOT,
) -> dict[str, Any]:
    """Rebuild only deployment fits from an already completed frozen research run."""

    research = _read_json(result_path)
    frame = load_phase6b_frame(database_path)
    full_history = load_phase4b_frame(database_path, primary_only=False)
    bundle = package_phase6b_bundle(
        frame, research,
        selected_family=str(research["selected_quantile_model_family"]),
        selected_feature=str(research["selected_quantile_feature_set"]),
        retained_thresholds=retained_classifier_thresholds(
            research["dataset"]["base_rates"]
        ),
        older_history=bool(research["older_standardized_history"]["adopted"]),
        full_history=full_history, output_root=output_root,
    )
    research["deployment_bundle"] = bundle
    _write_json(result_path, research)
    _write_json(DEFAULT_RESEARCH_ROOT / "results.json", research)
    return bundle


def _fit_quantile_model(
    model_family: str,
    manifest: Mapping[str, Any],
    train: pd.DataFrame,
    y_train: np.ndarray,
    validation: pd.DataFrame,
    y_validation: np.ndarray,
    quantile: float,
) -> tuple[Any, int]:
    if model_family == "catboost":
        from catboost import CatBoostRegressor

        loss = f"Quantile:alpha={quantile}"
        model = CatBoostRegressor(
            depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
            loss_function=loss, eval_metric=loss, iterations=700,
            random_seed=PHASE6B_RANDOM_SEED, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85, has_time=True,
            allow_writing_files=False, verbose=False, thread_count=4,
        )
        model.fit(
            _catboost_feature_frame(train, manifest), y_train,
            cat_features=manifest["categorical"],
            eval_set=(
                _catboost_feature_frame(validation, manifest), y_validation,
            ), use_best_model=True, early_stopping_rounds=50, verbose=False,
        )
        return model, max(1, int(model.get_best_iteration()) + 1)
    if model_family == "lightgbm":
        import lightgbm as lgb

        x_train, categories = _lightgbm_frame(train, manifest)
        x_validation, _ = _lightgbm_frame(validation, manifest, categories)
        model = lgb.LGBMRegressor(
            objective="quantile", alpha=quantile, n_estimators=900,
            num_leaves=31, max_depth=6, learning_rate=0.03,
            min_child_samples=80, subsample=0.85, colsample_bytree=0.8,
            reg_lambda=12.0, reg_alpha=0.5, random_state=PHASE6B_RANDOM_SEED,
            deterministic=True, force_col_wise=True, verbosity=-1, n_jobs=4,
        )
        model._phase6b_categories = categories
        model.fit(
            x_train, y_train, eval_X=[x_validation], eval_y=[y_validation],
            eval_metric="quantile", callbacks=[lgb.early_stopping(50, verbose=False)],
            categorical_feature=list(manifest["categorical"]),
        )
        return model, int(model.best_iteration_)
    raise KeyError(model_family)


def _fit_classifier(
    manifest: Mapping[str, Any],
    train: pd.DataFrame,
    y_train: np.ndarray,
    validation: pd.DataFrame,
    y_validation: np.ndarray,
) -> tuple[Any, int]:
    from catboost import CatBoostClassifier

    model = CatBoostClassifier(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
        loss_function="Logloss", eval_metric="Logloss", iterations=700,
        random_seed=PHASE6B_RANDOM_SEED, random_strength=1.0,
        bootstrap_type="Bernoulli", subsample=0.85, has_time=True,
        allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), y_train,
        cat_features=manifest["categorical"],
        eval_set=(_catboost_feature_frame(validation, manifest), y_validation),
        use_best_model=True, early_stopping_rounds=50, verbose=False,
    )
    return model, max(1, int(model.get_best_iteration()) + 1)


def _predict_model(
    model_family: str, model: Any, frame: pd.DataFrame,
    manifest: Mapping[str, Any],
) -> np.ndarray:
    if model_family == "catboost":
        return np.asarray(
            model.predict(_catboost_feature_frame(frame, manifest)), dtype=float
        )
    x, _ = _lightgbm_frame(frame, manifest, model._phase6b_categories)
    return np.asarray(model.predict(x), dtype=float)


def _predict_classifier(
    model: Any, frame: pd.DataFrame, manifest: Mapping[str, Any],
) -> np.ndarray:
    return np.asarray(
        model.predict_proba(_catboost_feature_frame(frame, manifest))[:, 1],
        dtype=float,
    )


def _fit_platt(probability: np.ndarray, labels: np.ndarray) -> LogisticRegression:
    if len(np.unique(labels)) != 2:
        raise ValueError("Platt calibration requires both label classes")
    model = LogisticRegression(C=1e6, solver="lbfgs", random_state=PHASE6B_RANDOM_SEED)
    model.fit(_logit(probability).reshape(-1, 1), labels)
    return model


def _quantile_calibration_offsets(
    frame: pd.DataFrame,
    residuals: np.ndarray,
    quantile: float,
) -> tuple[float, dict[str, float]]:
    """Fit chronological role-conditional offsets with a guarded global fallback."""

    global_offset = finite_sample_quantile(residuals, quantile)
    roles = role_band(frame).reset_index(drop=True)
    residual = pd.Series(np.asarray(residuals, dtype=float)).reset_index(drop=True)
    offsets: dict[str, float] = {}
    for name in sorted(roles.unique()):
        values = residual[roles.eq(name)].to_numpy(float)
        if len(values) >= 100:
            offsets[str(name)] = finite_sample_quantile(values, quantile)
    return global_offset, offsets


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _lightgbm_frame(
    frame: pd.DataFrame,
    manifest: Mapping[str, Any],
    categories: Mapping[str, Sequence[str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    data: dict[str, Any] = {}
    for column in manifest["numeric"]:
        data[column] = pd.to_numeric(frame[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
    resolved: dict[str, list[str]] = {}
    for column in manifest["categorical"]:
        values = frame[column].astype("string").fillna("__MISSING__")
        known = (
            list(categories[column]) if categories is not None
            else sorted(values.unique().tolist())
        )
        resolved[column] = known
        data[column] = pd.Categorical(values, categories=known)
    output = pd.DataFrame(data, index=frame.index)
    return output[list(manifest["features"])], resolved


def _training_fp(frame: pd.DataFrame) -> np.ndarray:
    actual = pd.to_numeric(frame["actual_fantasy_points"], errors="coerce")
    standardized = pd.to_numeric(frame["standardized_fantasy_points"], errors="coerce")
    values = actual.fillna(standardized).to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("probabilistic training target is missing")
    return values


def _assemble_predictions(
    frame: pd.DataFrame,
    central: Mapping[str, Any],
    quantile_runs: Mapping[float, QuantileRun],
    probability_runs: Mapping[float, Mapping[str, Any]],
    retained_thresholds: Sequence[float],
) -> pd.DataFrame:
    reference = next(iter(quantile_runs.values())).predictions
    columns = _context_columns(reference)
    output = reference[columns + ["outer_fold", "actual_fp"]].copy()
    output = output.merge(
        central["predictions"].drop(columns="actual_fp"),
        on=["model_row_id", "outer_fold"],
        how="left", validate="one_to_one",
    )
    raw_q, calibrated_q = [], []
    for quantile in QUANTILES:
        run = quantile_runs[quantile].predictions[[
            "model_row_id", "outer_fold", "raw_prediction", "calibrated_prediction"
        ]].rename(columns={
            "raw_prediction": f"raw_q{int(quantile*100):02d}",
            "calibrated_prediction": f"cal_q{int(quantile*100):02d}",
        })
        output = output.merge(
            run, on=["model_row_id", "outer_fold"], validate="one_to_one"
        )
        raw_q.append(f"raw_q{int(quantile*100):02d}")
        calibrated_q.append(f"cal_q{int(quantile*100):02d}")
    probability_columns = []
    raw_probability_columns = []
    for threshold in retained_thresholds:
        run = probability_runs[threshold]["predictions"][[
            "model_row_id", "outer_fold", "raw_probability", "calibrated_probability"
        ]].rename(columns={
            "raw_probability": f"raw_prob_ge_{int(threshold)}",
            "calibrated_probability": f"cal_prob_ge_{int(threshold)}",
        })
        output = output.merge(
            run, on=["model_row_id", "outer_fold"], validate="one_to_one"
        )
        raw_probability_columns.append(f"raw_prob_ge_{int(threshold)}")
        probability_columns.append(f"cal_prob_ge_{int(threshold)}")
    raw_prob = _complete_probability_curve(
        output, raw_probability_columns, retained_thresholds
    )
    calibrated_prob = _complete_probability_curve(
        output, probability_columns, retained_thresholds
    )
    final_q, final_p = reconcile_distribution(
        output[calibrated_q].to_numpy(float), calibrated_prob,
    )
    for index, quantile in enumerate(QUANTILES):
        output[f"p{int(quantile*100):02d}_fp"] = final_q[:, index]
    for index, threshold in enumerate(THRESHOLDS):
        output[f"raw_complete_prob_ge_{int(threshold)}"] = raw_prob[:, index]
        output[f"cal_complete_prob_ge_{int(threshold)}"] = calibrated_prob[:, index]
        output[f"prob_fp_ge_{int(threshold)}"] = final_p[:, index]
    output["median_fp"] = output["p50_fp"]
    output["expected_fp"] = output[
        "mean_challenger_fp" if central["summary"]["mean_challenger_selected"]
        else "phase4b_central_fp"
    ]
    output["distribution_width"] = output["p90_fp"] - output["p10_fp"]
    output["p90_minus_p50"] = output["p90_fp"] - output["p50_fp"]
    output["p50_minus_p10"] = output["p50_fp"] - output["p10_fp"]
    output["p95_minus_expected"] = output["p95_fp"] - output["expected_fp"]
    output["history_sample_count"] = output["career_el_games_before"]
    output["distribution_confidence"] = pd.cut(
        output.history_sample_count, [-np.inf, 4, 19, np.inf],
        labels=["LOW", "MEDIUM", "HIGH"],
    ).astype("string")
    output["cold_start_flag"] = output.history_sample_count.lt(5)
    output["raw_quantile_crossing"] = np.any(
        np.diff(output[raw_q].to_numpy(float), axis=1) < 0, axis=1
    )
    output["calibrated_quantile_crossing"] = np.any(
        np.diff(output[calibrated_q].to_numpy(float), axis=1) < 0, axis=1
    )
    output["raw_probability_order_violation"] = np.any(
        np.diff(raw_prob, axis=1) > 0, axis=1
    )
    output["calibrated_probability_order_violation"] = np.any(
        np.diff(calibrated_prob, axis=1) > 0, axis=1
    )
    return output


def _complete_probability_curve(
    frame: pd.DataFrame,
    columns: Sequence[str],
    retained_thresholds: Sequence[float],
) -> np.ndarray:
    known = frame[list(columns)].to_numpy(float)
    known_thresholds = np.asarray(retained_thresholds, dtype=float)
    if known.shape[1] < 2:
        raise ValueError("at least two threshold classifiers are required")
    logits = _logit(known)
    output = np.empty((len(frame), len(THRESHOLDS)), dtype=float)
    for row in range(len(frame)):
        fitted = np.interp(THRESHOLDS, known_thresholds, logits[row])
        below = np.asarray(THRESHOLDS) < known_thresholds[0]
        above = np.asarray(THRESHOLDS) > known_thresholds[-1]
        left_slope = (logits[row, 1] - logits[row, 0]) / (
            known_thresholds[1] - known_thresholds[0]
        )
        right_slope = (logits[row, -1] - logits[row, -2]) / (
            known_thresholds[-1] - known_thresholds[-2]
        )
        fitted[below] = logits[row, 0] + left_slope * (
            np.asarray(THRESHOLDS)[below] - known_thresholds[0]
        )
        fitted[above] = logits[row, -1] + right_slope * (
            np.asarray(THRESHOLDS)[above] - known_thresholds[-1]
        )
        output[row] = 1.0 / (1.0 + np.exp(-np.clip(fitted, -20, 20)))
    return output


def _central_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    hybrid_path = PHASE4B_REFERENCE_ROOT / "ml_phase4b_outer_predictions_v1.parquet"
    rmse_path = (
        PHASE4B_REFERENCE_ROOT / "direct_losses"
        / "p4b__direct__catboost_core_rotation__rmse" / "outer_predictions.parquet"
    )
    huber_path = (
        PHASE4B_REFERENCE_ROOT / "direct_losses"
        / "p4b__direct__catboost_core_rotation__huber_delta_5_0"
        / "outer_predictions.parquet"
    )
    with duckdb.connect() as connection:
        frozen = connection.execute(
            "SELECT model_row_id,outer_fold,actual_fp,predicted_fp "
            "FROM read_parquet(?) WHERE architecture='hybrid'",
            [str(hybrid_path)],
        ).df().rename(columns={"predicted_fp": "phase4b_central_fp"})
        rmse = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_fp FROM read_parquet(?)",
            [str(rmse_path)],
        ).df().rename(columns={"predicted_fp": "mean_challenger_fp"})
        huber = connection.execute(
            "SELECT model_row_id,outer_fold,predicted_fp FROM read_parquet(?)",
            [str(huber_path)],
        ).df().rename(columns={"predicted_fp": "robust_mean_challenger_fp"})
    predictions = frozen.merge(rmse, on=["model_row_id", "outer_fold"], validate="one_to_one")
    predictions = predictions.merge(huber, on=["model_row_id", "outer_fold"], validate="one_to_one")
    metrics = {
        "frozen_phase4b": _regression_metrics(predictions.actual_fp, predictions.phase4b_central_fp),
        "catboost_rmse_mean": _regression_metrics(predictions.actual_fp, predictions.mean_challenger_fp),
        "catboost_huber_robust": _regression_metrics(
            predictions.actual_fp, predictions.robust_mean_challenger_fp
        ),
    }
    frozen_metric = metrics["frozen_phase4b"]
    challenger = metrics["catboost_rmse_mean"]
    selected = (
        challenger["rmse"]
        <= frozen_metric["rmse"] * (1 - CENTRAL_SELECTION_RULE["minimum_rmse_relative_improvement"])
        and abs(challenger["mean_bias_actual_minus_prediction"])
        <= CENTRAL_SELECTION_RULE["maximum_absolute_mean_bias"]
        and challenger["mae"] <= frozen_metric["mae"] + CENTRAL_SELECTION_RULE["maximum_mae_degradation"]
    )
    evaluation = frame[frame.season.isin(("E2023", "E2024", "E2025"))][[
        "model_row_id", "target_minutes", "season_minutes_avg_before",
    ]]
    joined = predictions.merge(evaluation, on="model_row_id", validate="one_to_one")
    bands = []
    joined["actual_minute_band"] = actual_minute_band(
        joined.rename(columns={"target_minutes": "target_minutes"})
    )
    for band, group in joined.groupby("actual_minute_band", observed=True):
        for name, column in (
            ("frozen_phase4b", "phase4b_central_fp"),
            ("catboost_rmse_mean", "mean_challenger_fp"),
        ):
            bands.append({"actual_minute_band": str(band), "model": name,
                          **_regression_metrics(group.actual_fp, group[column])})
    tail = []
    for threshold in THRESHOLDS:
        group = joined[joined.actual_fp.ge(threshold)]
        for name, column in (
            ("frozen_phase4b", "phase4b_central_fp"),
            ("catboost_rmse_mean", "mean_challenger_fp"),
        ):
            tail.append({"actual_fp_at_least": threshold, "model": name,
                         **_regression_metrics(group.actual_fp, group[column])})
    return {
        "predictions": predictions,
        "summary": {
            "models": metrics, "selection_rule": CENTRAL_SELECTION_RULE,
            "mean_challenger_selected": bool(selected),
            "selected_expected_fp_source": (
                "catboost_rmse_conditional_mean" if selected
                else "frozen_phase4b_central"
            ),
            "actual_minute_band_metrics": bands, "tail_metrics": tail,
        },
    }


def _quantile_summary(
    predictions: pd.DataFrame,
    runs: Mapping[float, QuantileRun],
) -> dict[str, Any]:
    values = []
    for quantile in QUANTILES:
        raw = predictions[f"raw_q{int(quantile*100):02d}"].to_numpy(float)
        calibrated = predictions[f"cal_q{int(quantile*100):02d}"].to_numpy(float)
        final = predictions[f"p{int(quantile*100):02d}_fp"].to_numpy(float)
        values.append({
            "quantile": quantile,
            "raw": quantile_metrics(predictions.actual_fp, raw, quantile),
            "calibrated_before_rearrangement": quantile_metrics(
                predictions.actual_fp, calibrated, quantile
            ),
            "final": quantile_metrics(predictions.actual_fp, final, quantile),
            "fold_audits": runs[quantile].audits,
        })
    raw_columns = [f"raw_q{int(q*100):02d}" for q in QUANTILES]
    calibrated_columns = [f"cal_q{int(q*100):02d}" for q in QUANTILES]
    final_columns = [f"p{int(q*100):02d}_fp" for q in QUANTILES]
    return {
        "metrics": values,
        "raw_crossing_rate": quantile_crossing_rate(predictions[raw_columns].to_numpy(float)),
        "calibrated_crossing_rate": quantile_crossing_rate(
            predictions[calibrated_columns].to_numpy(float)
        ),
        "final_crossing_rate": quantile_crossing_rate(predictions[final_columns].to_numpy(float)),
        "weighted_interval_score": weighted_interval_score(
            predictions.actual_fp, predictions[final_columns].to_numpy(float)
        ),
    }


def _probability_summary(
    predictions: pd.DataFrame,
    runs: Mapping[float, Mapping[str, Any]],
    base_rates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics = []
    for threshold in THRESHOLDS:
        labels = (predictions.actual_fp.to_numpy(float) >= threshold).astype(int)
        final = predictions[f"prob_fp_ge_{int(threshold)}"].to_numpy(float)
        row: dict[str, Any] = {
            "threshold": threshold, "direct_classifier_retained": threshold in runs,
            "final": _classification_metrics(labels, final),
            "reliability_bins": _reliability_bins(labels, final),
        }
        if threshold in runs:
            raw = runs[threshold]["predictions"]["raw_probability"].to_numpy(float)
            calibrated = runs[threshold]["predictions"]["calibrated_probability"].to_numpy(float)
            row["raw"] = _classification_metrics(labels, raw)
            row["platt_calibrated"] = _classification_metrics(labels, calibrated)
            row["fold_audits"] = runs[threshold]["audits"]
        else:
            row["derivation"] = "logit extrapolation plus quantile-consistency reconciliation"
        metrics.append(row)
    raw_cols = [f"raw_complete_prob_ge_{int(t)}" for t in THRESHOLDS]
    cal_cols = [f"cal_complete_prob_ge_{int(t)}" for t in THRESHOLDS]
    final_cols = [f"prob_fp_ge_{int(t)}" for t in THRESHOLDS]
    return {
        "base_rates": list(base_rates), "threshold_metrics": metrics,
        "raw_probability_order_violation_rate": probability_violation_rate(
            predictions[raw_cols].to_numpy(float)
        ),
        "calibrated_probability_order_violation_rate": probability_violation_rate(
            predictions[cal_cols].to_numpy(float)
        ),
        "final_probability_order_violation_rate": probability_violation_rate(
            predictions[final_cols].to_numpy(float)
        ),
    }


def _role_calibration(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    work = predictions.copy()
    work["role_band"] = role_band(work)
    volatility = pd.to_numeric(work["season_fp_std_before"], errors="coerce")
    median = float(volatility.median())
    work["volatility_band"] = np.where(
        volatility.isna(), "unknown", np.where(volatility >= median, "high", "low")
    )
    expected = pd.to_numeric(work["phase4b_central_fp"], errors="coerce")
    work["expected_band"] = pd.cut(
        expected, [-np.inf, 8, 15, np.inf],
        labels=["low", "medium", "high"], right=False,
    ).astype("string")
    output = []
    for field in ("role_band", "volatility_band", "expected_band"):
        for group_name, group in work.groupby(field, observed=True):
            output.append({
                "group_type": field, "group": str(group_name), "rows": len(group),
                "p90_coverage": float((group.actual_fp <= group.p90_fp).mean()),
                "p95_coverage": float((group.actual_fp <= group.p95_fp).mean()),
                "mean_p90_minus_p50": float((group.p90_fp - group.p50_fp).mean()),
                "median_p90_minus_p50": float((group.p90_fp - group.p50_fp).median()),
            })
    return output


def _tail_diagnostics(predictions: pd.DataFrame) -> dict[str, Any]:
    metrics = []
    for threshold in (25.0, 30.0, 35.0, 40.0):
        group = predictions[predictions.actual_fp.ge(threshold)]
        metrics.append({
            "actual_fp_at_least": threshold, "rows": len(group),
            "central_mae": float(np.mean(np.abs(group.actual_fp - group.expected_fp))),
            "mean_central_error_actual_minus_predicted": float(
                np.mean(group.actual_fp - group.expected_fp)
            ),
            "p90_coverage": float((group.actual_fp <= group.p90_fp).mean()),
            "p95_coverage": float((group.actual_fp <= group.p95_fp).mean()),
            "mean_predicted_threshold_probability": float(
                group[f"prob_fp_ge_{int(threshold)}"].mean()
            ),
        })
    missed = predictions.assign(
        p95_miss=lambda value: value.actual_fp - value.p95_fp,
    ).nlargest(10, "p95_miss")
    false = predictions.assign(
        false_upside=lambda value: value.p90_fp - value.actual_fp,
    ).nlargest(10, "false_upside")
    fields = [
        "model_row_id", "season", "player_id", "team_id", "actual_fp",
        "expected_fp", "p90_fp", "p95_fp", "prob_fp_ge_35", "prob_fp_ge_40",
    ]
    return {
        "threshold_performance": metrics,
        "worst_missed_upside": missed[fields + ["p95_miss"]].to_dict("records"),
        "worst_false_upside": false[fields + ["false_upside"]].to_dict("records"),
    }


def _known_absence_diagnostic(predictions: pd.DataFrame) -> dict[str, Any]:
    if not PHASE5B_IMPACT_PATH.is_file():
        return {"available": False, "reason": "Phase 5B impact artifact missing"}
    with duckdb.connect() as connection:
        impact = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(PHASE5B_IMPACT_PATH)]
        ).df()
    selected = impact[impact.experiment_id.eq("p5b__xgboost__core")].copy()
    selected = selected.merge(
        predictions, on=["model_row_id", "outer_fold"], how="inner",
        suffixes=("_absence", ""), validate="many_to_one",
    )
    delta = selected.adjusted_fp.to_numpy(float) - selected.frozen_fp.to_numpy(float)
    baseline_q = selected[[f"p{int(q*100):02d}_fp" for q in QUANTILES]].to_numpy(float)
    adjusted_q = baseline_q + delta[:, None]
    actual = selected.actual_fp_absence.to_numpy(float)
    benefit = selected.predicted_minutes_delta.to_numpy(float) >= 3.0
    result = {
        "available": True, "mode": "CONDITIONAL_ON_KNOWN_ABSENCE_SET",
        "rows": len(selected), "major_opportunity_rows": int(benefit.sum()),
        "target_game_recipient_minutes_used_as_feature": False,
        "location_shift": "frozen Phase 5B adjusted_fp minus frozen_fp",
        "baseline_p90_pinball": pinball_loss(actual, baseline_q[:, 4], 0.90),
        "adjusted_p90_pinball": pinball_loss(actual, adjusted_q[:, 4], 0.90),
        "baseline_p95_pinball": pinball_loss(actual, baseline_q[:, 5], 0.95),
        "adjusted_p95_pinball": pinball_loss(actual, adjusted_q[:, 5], 0.95),
        "major_opportunity_mean_fp_shift": float(delta[benefit].mean()) if benefit.any() else None,
        "major_opportunity_mean_p90_shift": float(
            (adjusted_q[benefit, 4] - baseline_q[benefit, 4]).mean()
        ) if benefit.any() else None,
        "major_opportunity_baseline_p90_coverage": float(
            np.mean(actual[benefit] <= baseline_q[benefit, 4])
        ) if benefit.any() else None,
        "major_opportunity_adjusted_p90_coverage": float(
            np.mean(actual[benefit] <= adjusted_q[benefit, 4])
        ) if benefit.any() else None,
        "rows_frame": selected.assign(
            adjusted_p90=adjusted_q[:, 4], adjusted_p95=adjusted_q[:, 5],
        ),
    }
    return result


def _case_studies(
    predictions: pd.DataFrame, absence: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    work = predictions[predictions.season.eq("E2025")].copy()
    volatility = pd.to_numeric(work.season_fp_std_before, errors="coerce")
    candidates: list[tuple[str, pd.Series]] = []
    candidates.append(("high_average_star", work.nlargest(1, "phase4b_central_fp").iloc[0]))
    candidates.append(("high_volatility_scorer", work.loc[volatility.nlargest(1).index].iloc[0]))
    stable = work[volatility <= volatility.quantile(0.25)]
    candidates.append(("stable_high_floor", stable.nlargest(1, "phase4b_central_fp").iloc[0]))
    medium = work[work.season_minutes_avg_before.between(10, 25)]
    candidates.append(("medium_role_volatile", medium.loc[
        pd.to_numeric(medium.season_fp_std_before, errors="coerce").nlargest(1).index
    ].iloc[0]))
    bench = work[work.career_el_games_before.ge(10)]
    candidates.append(("low_role_bench", bench.nsmallest(1, "season_minutes_avg_before").iloc[0]))
    if absence is not None and not absence.empty:
        row = absence.nlargest(1, "predicted_minutes_delta").iloc[0]
        matching = work[work.model_row_id.eq(row.model_row_id)]
        if not matching.empty:
            candidates.append(("major_absence_opportunity", matching.iloc[0]))
    candidates.append(("severe_missed_upside", work.assign(
        miss=work.actual_fp - work.p95_fp
    ).nlargest(1, "miss").iloc[0]))
    candidates.append(("false_upside_failure", work.assign(
        false=work.p90_fp - work.actual_fp
    ).nlargest(1, "false").iloc[0]))
    fields = [
        "model_row_id", "season", "game_id", "player_id", "team_id",
        "expected_fp", "phase4b_central_fp", "p10_fp", "p25_fp", "p50_fp",
        "p75_fp", "p90_fp", "p95_fp", "prob_fp_ge_20", "prob_fp_ge_25",
        "prob_fp_ge_30", "prob_fp_ge_35", "prob_fp_ge_40", "actual_fp",
    ]
    return [{"archetype": name, **{field: row[field] for field in fields}}
            for name, row in candidates]


def _candidate_summary(run: QuantileRun) -> dict[str, Any]:
    return {
        "model_family": run.model_family, "feature_set": run.feature_set,
        "quantile": run.quantile, "older_history": run.older_history,
        "weighted_inner_raw_pinball": run.weighted_inner_pinball,
        "outer_raw": quantile_metrics(
            run.predictions.actual_fp, run.predictions.raw_prediction, run.quantile
        ),
        "outer_calibrated": quantile_metrics(
            run.predictions.actual_fp, run.predictions.calibrated_prediction,
            run.quantile,
        ),
        "fold_audits": run.audits,
    }


def _dataset_summary(
    frame: pd.DataFrame, folds: Sequence[Mapping[str, Any]],
    base_rates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    outer_rows = sum(len(fold["outer_test"]) for fold in folds)
    return {
        "primary_rows": len(frame), "outer_evaluation_rows": outer_rows,
        "seasons": list(PRIMARY_DATASET_SEASONS),
        "outer_folds": [{
            "fold_id": fold["definition"].fold_id,
            "train_seasons": list(fold["definition"].verified_train_seasons),
            "test_season": fold["definition"].outer_test_season,
            "test_rows": len(fold["outer_test"]),
        } for fold in folds],
        "zero_minute_rows": int((frame.target_minutes <= 0).sum()),
        "minimum_positive_minutes": float(frame.target_minutes.min()),
        "base_rates": list(base_rates),
        "dataset_fingerprint": _frame_fingerprint(
            frame, feature_manifest("CORE_ROTATION", include_player_id=True)
        ),
    }


def _classification_metrics(labels: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    y = np.asarray(labels, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-12, 1 - 1e-12)
    slope, intercept = _calibration_slope_intercept(y, p)
    return {
        "rows": len(y), "positives": int(y.sum()), "positive_rate": float(y.mean()),
        "mean_probability": float(p.mean()),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
        "calibration_slope": slope, "calibration_intercept": intercept,
    }


def _calibration_slope_intercept(labels: np.ndarray, probability: np.ndarray) -> tuple[float | None, float | None]:
    if len(np.unique(labels)) != 2:
        return None, None
    model = LogisticRegression(C=1e6, solver="lbfgs", random_state=PHASE6B_RANDOM_SEED)
    model.fit(_logit(probability).reshape(-1, 1), labels)
    return float(model.coef_[0, 0]), float(model.intercept_[0])


def _reliability_bins(labels: np.ndarray, probability: np.ndarray) -> list[dict[str, Any]]:
    work = pd.DataFrame({"label": labels, "probability": probability})
    work["bin"] = pd.qcut(
        work.probability.rank(method="first"), 10, labels=False, duplicates="drop"
    )
    return [{
        "bin": int(name), "rows": len(group),
        "mean_probability": float(group.probability.mean()),
        "observed_rate": float(group.label.mean()),
    } for name, group in work.groupby("bin", observed=True)]


def _regression_metrics(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    residual = y - p
    return {
        "rows": len(y), "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual ** 2))),
        "mean_actual": float(y.mean()), "mean_prediction": float(p.mean()),
        "mean_bias_actual_minus_prediction": float(residual.mean()),
        "spearman": float(spearmanr(y, p).statistic),
        "pearson": float(pearsonr(y, p).statistic),
    }


def _package_mean_model(frame: pd.DataFrame, output_root: Path) -> dict[str, Any]:
    from catboost import CatBoostRegressor

    manifest = feature_manifest("CORE_ROTATION", include_player_id=True)
    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
        loss_function="RMSE", iterations=235, eval_metric="RMSE",
        random_seed=PHASE6B_RANDOM_SEED, random_strength=1.0,
        bootstrap_type="Bernoulli", subsample=0.85, has_time=True,
        allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(frame, manifest),
        frame[PRIMARY_TARGET_COLUMN].to_numpy(float),
        cat_features=manifest["categorical"], verbose=False,
    )
    path = output_root / "expected_mean.json"
    _save_canonical_catboost(model, path)
    return {
        "model_path": path.name, "model_sha256": _file_sha(path),
        "model_type": "catboost", "model_format": "json", "iterations": 235,
        "objective": "RMSE", "feature_manifest": manifest,
    }


def _context_columns(frame: pd.DataFrame) -> list[str]:
    wanted = [
        "model_row_id", "season", "round_number", "game_id", "player_id",
        "team_id", "opponent_team_id", "target_game_time", "feature_cutoff_time",
        "fantasy_matchday", "fantasy_position", "target_minutes",
        "career_el_games_before", "season_games_before", "season_minutes_avg_before",
        "season_fp_std_before", "last_5_fp_std", "last_5_start_rate",
        "rot_last5_starter_rate", "rot_last5_closing_lineup_rate",
    ]
    return [column for column in wanted if column in frame]


def _probabilistic_fields() -> list[str]:
    return [
        "phase4b_central_fp", "expected_fp", "median_fp", "p10_fp", "p25_fp",
        "p50_fp", "p75_fp", "p90_fp", "p95_fp", "prob_fp_ge_20",
        "prob_fp_ge_25", "prob_fp_ge_30", "prob_fp_ge_35", "prob_fp_ge_40",
        "distribution_width", "p90_minus_p50", "p50_minus_p10",
        "p95_minus_expected", "history_sample_count", "distribution_confidence",
        "cold_start_flag", "probabilistic_model_version", "calibration_version",
    ]


def _frame_fingerprint(frame: pd.DataFrame, manifest: Mapping[str, Any]) -> str:
    columns = ["model_row_id", *manifest["features"], PRIMARY_TARGET_COLUMN]
    work = frame.sort_values(["target_game_time", "game_id", "player_id"])[columns]
    digest = hashlib.sha256("\n".join(columns).encode())
    digest.update(pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def _prediction_fingerprint(frame: pd.DataFrame) -> str:
    columns = [
        "model_row_id", "outer_fold", "expected_fp", "p10_fp", "p25_fp", "p50_fp",
        "p75_fp", "p90_fp", "p95_fp", "prob_fp_ge_20", "prob_fp_ge_25",
        "prob_fp_ge_30", "prob_fp_ge_35", "prob_fp_ge_40",
    ]
    work = frame.sort_values(["outer_fold", "model_row_id"])[columns]
    return hashlib.sha256(
        pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    ).hexdigest()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.register("_phase6b_output", frame)
        connection.execute(
            "COPY _phase6b_output TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)]
        )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return None
    raise TypeError(type(value).__name__)
