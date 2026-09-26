"""Chronological Phase 6C family ablations and frozen uplift packaging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.ml_experiments import _catboost_feature_frame
from src.modeling.phase4b_deployment import _save_canonical_catboost
from src.modeling.phase4b_protocol import load_phase4b_frame
from src.modeling.phase6b_protocol import (
    QUANTILES,
    THRESHOLDS,
    phase6b_folds,
    pinball_loss,
    quantile_metrics,
)
from src.modeling.phase6b_runner import _regression_metrics

from .phase6c_features import (
    add_expected_usage_interactions,
    attach_phase6c_features,
    validate_phase6c_cutoffs,
)
from .phase6c_protocol import (
    DOWNSIDE_THRESHOLDS,
    EVALUATION_SEASONS,
    FAMILY_ADDITIONS,
    PHASE6C_CALIBRATION_VERSION,
    PHASE6C_MODEL_VERSION,
    PHASE6C_PROTOCOL_VERSION,
    PHASE6C_RANDOM_SEED,
    PRIMARY_SEASONS,
    expected_usage_feature_manifest,
    phase6c_feature_manifest,
    protocol_fingerprint,
    protocol_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE6C_ROOT = PROJECT_ROOT / "data" / "derived" / "phase6c"
DEFAULT_RESEARCH_ROOT = DEFAULT_PHASE6C_ROOT / "research"
DEFAULT_BUNDLE_ROOT = DEFAULT_PHASE6C_ROOT / "frozen_predictive_uplift"
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase6c"
PHASE6B_PREDICTIONS = (
    PROJECT_ROOT / "data" / "derived" / "phase6b" / "research"
    / "outer_predictions.parquet"
)
PHASE6C_CODE_FILES = (
    "src/modeling/phase6c_features.py",
    "src/modeling/phase6c_protocol.py",
    "src/modeling/phase6c_runner.py",
    "src/live/predictive_uplift_artifacts.py",
)


@dataclass(slots=True)
class CentralRun:
    families: tuple[str, ...]
    manifest: dict[str, Any]
    predictions: pd.DataFrame
    audits: list[dict[str, Any]]

    @property
    def inner_mae(self) -> float:
        return float(np.average(
            [row["inner_mae"] for row in self.audits],
            weights=[row["inner_validation_rows"] for row in self.audits],
        ))

    @property
    def inner_rmse(self) -> float:
        return float(np.average(
            [row["inner_rmse"] for row in self.audits],
            weights=[row["inner_validation_rows"] for row in self.audits],
        ))


def run_phase6c_research(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    research_root: Path = DEFAULT_RESEARCH_ROOT,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Run every independent family, combine inner winners, and freeze once."""

    result_path = sample_root / "results.json"
    if result_path.exists() and not force:
        return _read_json(result_path)
    print("phase6c: building leakage-safe feature frame", flush=True)
    primary, usage_summary = prepare_phase6c_frame(database_path)
    print(f"phase6c: feature frame ready ({len(primary)} rows)", flush=True)
    folds = phase6b_folds(primary)
    phase6b = _load_phase6b_predictions()

    base = run_central_candidate(primary, folds, ())
    print(
        f"phase6c: base reproduction inner MAE={base.inner_mae:.6f}", flush=True
    )
    independent: dict[str, CentralRun] = {}
    for family in FAMILY_ADDITIONS:
        independent[family] = run_central_candidate(primary, folds, (family,))
        print(
            f"phase6c: ablation {family} inner MAE="
            f"{independent[family].inner_mae:.6f}", flush=True,
        )
    inner_winners = tuple(
        family for family, run in independent.items()
        if run.inner_mae < base.inner_mae
        and run.inner_rmse <= base.inner_rmse + 0.01
    )
    combination = (
        run_central_candidate(primary, folds, inner_winners)
        if len(inner_winners) > 1 else None
    )
    if combination is not None:
        print(
            f"phase6c: combined {len(inner_winners)} inner winners; inner MAE="
            f"{combination.inner_mae:.6f}", flush=True,
        )
    eligible = [run for run in independent.values() if run.families[0] in inner_winners]
    if combination is not None:
        eligible.append(combination)
    selected = min(
        eligible or [base], key=lambda run: (run.inner_mae, run.inner_rmse)
    )

    baseline_metrics = _regression_metrics(
        phase6b.actual_fp, phase6b.expected_fp
    )
    selected_joined = _join_candidate(selected, phase6b)
    selected_metrics = _regression_metrics(
        selected_joined.actual_fp, selected_joined.predicted_fp
    )
    accepted = bool(
        selected.families
        and selected_metrics["mae"] < baseline_metrics["mae"]
        and selected_metrics["rmse"] <= baseline_metrics["rmse"] * 1.002
        and abs(selected_metrics["mean_bias_actual_minus_prediction"]) <= 0.25
    )
    retained = selected if accepted else base

    ablations = [
        _ablation_summary("phase6b_frozen", None, phase6b),
        _ablation_summary("phase6c_core_rotation_reproduction", base, phase6b),
    ]
    ablations.extend(
        _ablation_summary(family, run, phase6b)
        for family, run in independent.items()
    )
    if combination is not None:
        ablations.append(_ablation_summary(
            "winning_inner_family_combination", combination, phase6b
        ))

    retained_joined = _join_candidate(retained, phase6b)
    downside = run_downside_models(primary, folds, retained.manifest)
    print("phase6c: downside calibration complete", flush=True)
    final_predictions = _final_prediction_frame(retained_joined, downside)
    central_comparison = _central_comparison(final_predictions)
    distribution_comparison = _distribution_comparison(final_predictions)
    usage_effects = _usage_effects(final_predictions, primary)
    absence = _absence_redistribution_diagnostic(
        final_predictions, primary, database_path
    )
    importance = _fit_importance_model(primary, retained.manifest)
    ranking = {
        "phase6b": _ranking_metrics(final_predictions, "expected_fp"),
        "phase6c": _ranking_metrics(final_predictions, "phase6c_expected_fp"),
    }
    tail = _tail_comparison(final_predictions)

    research_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(primary, research_root / "feature_frame.parquet")
    _write_parquet(final_predictions, research_root / "outer_predictions.parquet")
    result: dict[str, Any] = {
        "status": "FROZEN" if accepted else "PHASE6B_RETAINED",
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol_payload(),
        "protocol_fingerprint": protocol_fingerprint(),
        "dataset": {
            "rows": len(primary), "outer_rows": len(final_predictions),
            "seasons": list(PRIMARY_SEASONS), "zero_minute_rows": int(
                (pd.to_numeric(primary.target_minutes, errors="coerce") <= 0).sum()
            ),
            "feature_frame_fingerprint": _frame_fingerprint(primary),
        },
        "expected_usage_model": usage_summary,
        "family_ablations": ablations,
        "inner_winning_families": list(inner_winners),
        "selected_families": list(selected.families),
        "retained_families": list(retained.families) if accepted else [],
        "phase6c_accepted": accepted,
        "selection_gate": {
            "phase6b_metrics": baseline_metrics,
            "selected_metrics": selected_metrics,
            "rule": (
                "selected on inner MAE with inner RMSE guard; accepted only if "
                "outer pooled MAE improves, RMSE is within 0.2%, and |bias|<=0.25"
            ),
        },
        "central_comparison": central_comparison,
        "ranking_comparison": ranking,
        "distribution_comparison": distribution_comparison,
        "downside_models": downside["summary"],
        "usage_archetype_effects": usage_effects,
        "absence_redistribution": absence,
        "feature_importance": importance,
        "tail_diagnostics": tail,
    }
    result["research_fingerprint"] = _json_sha({
        "protocol": result["protocol_fingerprint"],
        "frame": result["dataset"]["feature_frame_fingerprint"],
        "predictions": _prediction_fingerprint(final_predictions),
    })
    if accepted:
        result["deployment_bundle"] = package_phase6c_bundle(
            primary, retained, downside, result, output_root=bundle_root,
        )
    else:
        result["deployment_bundle"] = None
    _write_json(research_root / "results.json", result)
    _write_json(result_path, result)
    return result


def prepare_phase6c_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build features and attach genuinely walk-forward expected usage."""

    full = load_phase4b_frame(database_path, primary_only=False)
    full = full[
        full.season.isin(tuple(f"E{year}" for year in range(2018, 2026)))
        & pd.to_numeric(full.target_minutes, errors="coerce").gt(0)
    ].copy()
    full = attach_phase6c_features(full, database_path)
    full, usage = add_walk_forward_expected_usage(full)

    from .phase6b_protocol import load_phase6b_frame

    primary = load_phase6b_frame(database_path)
    additions = full.drop(columns=[
        column for column in full.columns
        if column != "model_row_id" and column in primary.columns
    ])
    primary = primary.merge(additions, on="model_row_id", how="left", validate="one_to_one")
    if len(primary) != 29232 or primary["expected_usage_next_game"].isna().any():
        raise ValueError("Phase 6C expected-usage merge changed the frozen population")
    primary = add_expected_usage_interactions(primary)
    validate_phase6c_cutoffs(primary)
    return primary, usage


def add_walk_forward_expected_usage(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Predict each season only from earlier seasons; refit after inner selection."""

    output = frame.copy()
    output["expected_usage_next_game"] = np.nan
    manifest = expected_usage_feature_manifest()
    audits: list[dict[str, Any]] = []
    for year in range(2022, 2026):
        train = output[
            output.season.isin(tuple(f"E{x}" for x in range(2018, year - 1)))
            & output.actual_usage_next_game.notna()
        ]
        validation = output[
            output.season.eq(f"E{year - 1}")
            & output.actual_usage_next_game.notna()
        ]
        refit = pd.concat([train, validation], ignore_index=True)
        test = output[
            output.season.eq(f"E{year}")
            & output.actual_usage_next_game.notna()
        ]
        probe, iterations = _fit_usage_model(train, validation, manifest)
        del probe
        model = _fit_usage_model_fixed(refit, manifest, iterations)
        predicted = np.clip(
            model.predict(_catboost_feature_frame(test, manifest)), 0.0, 100.0
        )
        output.loc[test.index, "expected_usage_next_game"] = predicted
        metric = _regression_metrics(test.actual_usage_next_game, predicted)
        metric.update({
            "prediction_season": f"E{year}", "base_train_rows": len(train),
            "iteration_validation_rows": len(validation), "refit_rows": len(refit),
            "outer_rows_seen": 0, "final_iterations": iterations,
            "train_max_time": refit.target_game_time.max().isoformat(),
            "test_min_time": test.target_game_time.min().isoformat(),
        })
        audits.append(metric)
        print(
            f"phase6c: usage {year} MAE={metric['mae']:.6f} "
            f"RMSE={metric['rmse']:.6f}", flush=True,
        )
    evaluation = output[output.season.isin(EVALUATION_SEASONS)].dropna(
        subset=["actual_usage_next_game", "expected_usage_next_game"]
    )
    model_metric = _regression_metrics(
        evaluation.actual_usage_next_game, evaluation.expected_usage_next_game
    )
    baseline_evaluation = evaluation.dropna(subset=["p6c_usage_last5"])
    baseline_metric = _regression_metrics(
        baseline_evaluation.actual_usage_next_game,
        baseline_evaluation.p6c_usage_last5,
    )
    raw = pd.to_numeric(evaluation.actual_usage_next_game_raw, errors="coerce")
    return output, {
        "target": "bounded traditional USG%; raw USG retained for audit",
        "formula_version": output.p6c_usage_formula_version.iloc[0],
        "walk_forward_metrics": audits,
        "pooled_outer_metrics": model_metric,
        "last5_usage_baseline_metrics": baseline_metric,
        "raw_usage_above_100_rows": int((raw > 100).sum()),
        "raw_usage_max": float(raw.max()),
        "oof_rows": len(evaluation),
        "feature_manifest": manifest,
    }


def run_central_candidate(
    frame: pd.DataFrame,
    folds: Sequence[Mapping[str, Any]],
    families: Sequence[str],
) -> CentralRun:
    manifest = (
        phase6c_feature_manifest(tuple(families), name="phase6c_core_rotation")
        if families else _base_manifest()
    )
    rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for fold in folds:
        inner = frame.loc[fold["inner_train"]]
        validation = frame.loc[fold["inner_validation"]]
        outer_train = frame.loc[fold["outer_train"]]
        test = frame.loc[fold["outer_test"]]
        probe, iterations = _fit_central_probe(inner, validation, manifest)
        inner_prediction = probe.predict(_catboost_feature_frame(validation, manifest))
        model = _fit_central_fixed(outer_train, manifest, iterations)
        prediction = model.predict(_catboost_feature_frame(test, manifest))
        rows.append(pd.DataFrame({
            "model_row_id": test.model_row_id.to_numpy(),
            "outer_fold": fold["definition"].fold_id,
            "predicted_fp": prediction,
        }))
        inner_error = validation.actual_fantasy_points.to_numpy(float) - inner_prediction
        audits.append({
            "outer_fold": fold["definition"].fold_id,
            "inner_train_rows": len(inner), "inner_validation_rows": len(validation),
            "outer_train_rows": len(outer_train), "outer_test_rows": len(test),
            "outer_test_rows_seen_during_selection": 0,
            "inner_mae": float(np.mean(np.abs(inner_error))),
            "inner_rmse": float(np.sqrt(np.mean(inner_error ** 2))),
            "inner_bias": float(inner_error.mean()), "final_iterations": iterations,
            "inner_train_max_time": inner.target_game_time.max().isoformat(),
            "inner_validation_min_time": validation.target_game_time.min().isoformat(),
            "outer_train_max_time": outer_train.target_game_time.max().isoformat(),
            "outer_test_min_time": test.target_game_time.min().isoformat(),
        })
    return CentralRun(tuple(families), manifest, pd.concat(rows, ignore_index=True), audits)


def run_downside_models(
    frame: pd.DataFrame,
    folds: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    predictions: dict[float, pd.DataFrame] = {}
    summaries: list[dict[str, Any]] = []
    audits: dict[str, list[dict[str, Any]]] = {}
    for threshold in DOWNSIDE_THRESHOLDS:
        rows: list[pd.DataFrame] = []
        threshold_audits: list[dict[str, Any]] = []
        for fold in folds:
            inner = frame.loc[fold["inner_train"]]
            validation = frame.loc[fold["inner_validation"]]
            outer_train = frame.loc[fold["outer_train"]]
            test = frame.loc[fold["outer_test"]]
            y_inner = inner.actual_fantasy_points.le(threshold).astype(int).to_numpy()
            y_validation = validation.actual_fantasy_points.le(threshold).astype(int).to_numpy()
            probe, iterations = _fit_classifier_probe(
                inner, y_inner, validation, y_validation, manifest
            )
            calibrator = _fit_platt(
                probe.predict_proba(_catboost_feature_frame(validation, manifest))[:, 1],
                y_validation,
            )
            y_outer_train = outer_train.actual_fantasy_points.le(threshold).astype(int).to_numpy()
            model = _fit_classifier_fixed(outer_train, y_outer_train, manifest, iterations)
            raw = model.predict_proba(_catboost_feature_frame(test, manifest))[:, 1]
            probability = calibrator.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
            rows.append(pd.DataFrame({
                "model_row_id": test.model_row_id.to_numpy(),
                "outer_fold": fold["definition"].fold_id,
                "label": test.actual_fantasy_points.le(threshold).astype(int).to_numpy(),
                "probability": probability,
            }))
            threshold_audits.append({
                "outer_fold": fold["definition"].fold_id,
                "inner_train_rows": len(inner), "calibration_rows": len(validation),
                "outer_train_rows": len(outer_train), "outer_test_rows": len(test),
                "outer_test_rows_seen": 0, "final_iterations": iterations,
                "calibration_coefficient": float(calibrator.coef_[0, 0]),
                "calibration_intercept": float(calibrator.intercept_[0]),
            })
        combined = pd.concat(rows, ignore_index=True)
        predictions[threshold] = combined
        summaries.append({
            "threshold": threshold,
            **_classification_metrics(combined.label, combined.probability),
        })
        audits[str(int(threshold))] = threshold_audits
    return {"predictions": predictions, "summary": {
        "threshold_metrics": summaries, "fold_audits": audits,
        "calibration": "inner chronological Platt",
    }}


def package_phase6c_bundle(
    frame: pd.DataFrame,
    retained: CentralRun,
    downside: Mapping[str, Any],
    research: Mapping[str, Any],
    *,
    output_root: Path = DEFAULT_BUNDLE_ROOT,
) -> dict[str, Any]:
    """Fit future-facing usage/uplift/downside models without touching Phase 6B."""

    output_root.mkdir(parents=True, exist_ok=True)
    usage_manifest = expected_usage_feature_manifest()
    train_usage = frame[frame.season.isin(("E2022", "E2023", "E2024"))]
    validation_usage = frame[frame.season.eq("E2025")]
    _, usage_iterations = _fit_usage_model(train_usage, validation_usage, usage_manifest)
    usage_model = _fit_usage_model_fixed(frame, usage_manifest, usage_iterations)
    usage_path = output_root / "expected_usage.json"
    _save_canonical_catboost(usage_model, usage_path)

    iterations = int(np.median([row["final_iterations"] for row in retained.audits]))
    central_model = _fit_central_fixed(frame, retained.manifest, iterations)
    central_path = output_root / "central_uplift.json"
    _save_canonical_catboost(central_model, central_path)

    downside_entries: dict[str, Any] = {}
    train = frame[frame.season.isin(("E2022", "E2023", "E2024"))]
    calibration = frame[frame.season.eq("E2025")]
    for threshold in DOWNSIDE_THRESHOLDS:
        y_train = train.actual_fantasy_points.le(threshold).astype(int).to_numpy()
        y_cal = calibration.actual_fantasy_points.le(threshold).astype(int).to_numpy()
        probe, final_iterations = _fit_classifier_probe(
            train, y_train, calibration, y_cal, retained.manifest
        )
        raw = probe.predict_proba(_catboost_feature_frame(calibration, retained.manifest))[:, 1]
        calibrator = _fit_platt(raw, y_cal)
        path = output_root / f"le_{int(threshold)}.json"
        _save_canonical_catboost(probe, path)
        downside_entries[str(int(threshold))] = {
            "model_path": path.name, "model_sha256": _file_sha(path),
            "iterations": final_iterations,
            "platt_coefficient": float(calibrator.coef_[0, 0]),
            "platt_intercept": float(calibrator.intercept_[0]),
        }
    _write_json(output_root / "feature_manifest.json", retained.manifest)
    _write_json(output_root / "expected_usage_feature_manifest.json", usage_manifest)
    _write_json(output_root / "protocol.json", protocol_payload())
    files = {
        path.name: _file_sha(path) for path in sorted(output_root.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    code_files = {
        relative: _file_sha(PROJECT_ROOT / relative)
        for relative in PHASE6C_CODE_FILES
    }
    payload: dict[str, Any] = {
        "status": "FROZEN", "bundle_identifier": PHASE6C_MODEL_VERSION,
        "predictive_model_version": PHASE6C_MODEL_VERSION,
        "calibration_version": PHASE6C_CALIBRATION_VERSION,
        "protocol_version": PHASE6C_PROTOCOL_VERSION,
        "protocol_fingerprint": protocol_fingerprint(),
        "created_at": datetime.now(UTC).isoformat(),
        "conditional_on_participation": True, "excludes_zero_minute_rows": True,
        "random_seed": PHASE6C_RANDOM_SEED,
        "retained_families": list(retained.families),
        "central_feature_manifest": retained.manifest,
        "expected_usage_feature_manifest": usage_manifest,
        "expected_usage_model": {
            "model_path": usage_path.name, "model_sha256": _file_sha(usage_path),
            "iterations": usage_iterations,
        },
        "central_model": {
            "model_path": central_path.name, "model_sha256": _file_sha(central_path),
            "iterations": iterations,
        },
        "downside_models": downside_entries,
        "downside_thresholds": list(DOWNSIDE_THRESHOLDS),
        "training_data_fingerprint": _frame_fingerprint(frame),
        "outer_evaluation_fingerprint": research["research_fingerprint"],
        "outer_evaluation_gate": {
            "passed": True, "rows": research["dataset"]["outer_rows"],
            "phase6b_mae": research["central_comparison"]["phase6b"]["mae"],
            "phase6c_mae": research["central_comparison"]["phase6c"]["mae"],
        },
        "code_files": code_files,
        "code_protocol_fingerprint": _json_sha(code_files),
        "files": files,
    }
    payload["bundle_fingerprint"] = _json_sha({
        "files": files, "code_files": code_files,
        "protocol_fingerprint": payload["protocol_fingerprint"],
    })
    _write_json(output_root / "manifest.json", payload)
    return {
        "bundle_identifier": PHASE6C_MODEL_VERSION,
        "bundle_fingerprint": payload["bundle_fingerprint"],
        "training_data_fingerprint": payload["training_data_fingerprint"],
        "files": files,
    }


def _fit_usage_model(
    train: pd.DataFrame, validation: pd.DataFrame, manifest: Mapping[str, Any],
) -> tuple[Any, int]:
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=10.0, loss_function="RMSE",
        eval_metric="RMSE", iterations=600, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), train.actual_usage_next_game,
        cat_features=manifest["categorical"],
        eval_set=(_catboost_feature_frame(validation, manifest),
                  validation.actual_usage_next_game),
        use_best_model=True, early_stopping_rounds=50, verbose=False,
    )
    return model, max(1, int(model.get_best_iteration()) + 1)


def _fit_usage_model_fixed(
    train: pd.DataFrame, manifest: Mapping[str, Any], iterations: int,
) -> Any:
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=10.0, loss_function="RMSE",
        iterations=iterations, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), train.actual_usage_next_game,
        cat_features=manifest["categorical"], verbose=False,
    )
    return model


def _fit_central_probe(
    train: pd.DataFrame, validation: pd.DataFrame, manifest: Mapping[str, Any],
) -> tuple[Any, int]:
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0, loss_function="RMSE",
        eval_metric="RMSE", iterations=600, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), train.actual_fantasy_points,
        cat_features=manifest["categorical"],
        eval_set=(_catboost_feature_frame(validation, manifest),
                  validation.actual_fantasy_points),
        use_best_model=True, early_stopping_rounds=50, verbose=False,
    )
    return model, max(1, int(model.get_best_iteration()) + 1)


def _fit_central_fixed(
    train: pd.DataFrame, manifest: Mapping[str, Any], iterations: int,
) -> Any:
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0, loss_function="RMSE",
        iterations=iterations, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), train.actual_fantasy_points,
        cat_features=manifest["categorical"], verbose=False,
    )
    return model


def _fit_classifier_probe(
    train: pd.DataFrame, y_train: np.ndarray, validation: pd.DataFrame,
    y_validation: np.ndarray, manifest: Mapping[str, Any],
) -> tuple[Any, int]:
    from catboost import CatBoostClassifier
    model = CatBoostClassifier(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0, loss_function="Logloss",
        eval_metric="Logloss", iterations=600, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), y_train,
        cat_features=manifest["categorical"],
        eval_set=(_catboost_feature_frame(validation, manifest), y_validation),
        use_best_model=True, early_stopping_rounds=50, verbose=False,
    )
    return model, max(1, int(model.get_best_iteration()) + 1)


def _fit_classifier_fixed(
    train: pd.DataFrame, y_train: np.ndarray, manifest: Mapping[str, Any],
    iterations: int,
) -> Any:
    from catboost import CatBoostClassifier
    model = CatBoostClassifier(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0, loss_function="Logloss",
        iterations=iterations, random_seed=PHASE6C_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        has_time=True, allow_writing_files=False, verbose=False, thread_count=4,
    )
    model.fit(
        _catboost_feature_frame(train, manifest), y_train,
        cat_features=manifest["categorical"], verbose=False,
    )
    return model


def _ablation_summary(
    name: str, run: CentralRun | None, phase6b: pd.DataFrame,
) -> dict[str, Any]:
    if run is None:
        joined = phase6b.copy()
        joined["predicted_fp"] = joined["expected_fp"]
        inner_mae = inner_rmse = None
        families: list[str] = []
        audits: list[dict[str, Any]] = []
    else:
        joined = _join_candidate(run, phase6b)
        inner_mae, inner_rmse = run.inner_mae, run.inner_rmse
        families, audits = list(run.families), run.audits
    return {
        "name": name, "families": families, "inner_mae": inner_mae,
        "inner_rmse": inner_rmse,
        "central_metrics": _regression_metrics(joined.actual_fp, joined.predicted_fp),
        "fold_metrics": {
            fold: _regression_metrics(group.actual_fp, group.predicted_fp)
            for fold, group in joined.groupby("outer_fold")
        },
        "probabilistic_location_metrics": _location_metrics(joined),
        "fold_audits": audits,
    }


def _load_phase6b_predictions() -> pd.DataFrame:
    with duckdb.connect() as connection:
        frame = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY outer_fold,model_row_id",
            [str(PHASE6B_PREDICTIONS)],
        ).df()
    return frame


def _join_candidate(run: CentralRun, phase6b: pd.DataFrame) -> pd.DataFrame:
    return phase6b.merge(
        run.predictions, on=["model_row_id", "outer_fold"], validate="one_to_one"
    )


def _location_metrics(joined: pd.DataFrame) -> dict[str, Any]:
    delta = joined.predicted_fp.to_numpy(float) - joined.expected_fp.to_numpy(float)
    values: dict[str, Any] = {"mean_absolute_location_delta": float(np.mean(np.abs(delta)))}
    for quantile in QUANTILES:
        base = joined[f"p{int(quantile*100):02d}_fp"].to_numpy(float)
        values[f"p{int(quantile*100):02d}"] = quantile_metrics(
            joined.actual_fp.to_numpy(float), base + delta, quantile
        )
    base_probability = joined[
        [f"prob_fp_ge_{int(value)}" for value in THRESHOLDS]
    ].to_numpy(float)
    shifted = _shift_survival(base_probability, np.asarray(THRESHOLDS), delta)
    values["upside"] = {
        str(int(threshold)): _classification_metrics(
            joined.actual_fp.ge(threshold).astype(int), shifted[:, index]
        ) for index, threshold in enumerate(THRESHOLDS)
    }
    return values


def _final_prediction_frame(
    joined: pd.DataFrame, downside: Mapping[str, Any],
) -> pd.DataFrame:
    output = joined.copy()
    delta = output.predicted_fp.to_numpy(float) - output.expected_fp.to_numpy(float)
    output["phase6c_expected_fp"] = output.predicted_fp
    for quantile in QUANTILES:
        column = f"p{int(quantile*100):02d}_fp"
        output[f"phase6b_{column}"] = output[column]
        output[column] = output[column].to_numpy(float) + delta
    base = output[[f"prob_fp_ge_{int(t)}" for t in THRESHOLDS]].to_numpy(float)
    shifted = _shift_survival(base, np.asarray(THRESHOLDS), delta)
    for index, threshold in enumerate(THRESHOLDS):
        column = f"prob_fp_ge_{int(threshold)}"
        output[f"phase6b_{column}"] = output[column]
        output[column] = shifted[:, index]
    for threshold, prediction in downside["predictions"].items():
        column = f"prob_fp_le_{int(threshold)}"
        output = output.merge(
            prediction[["model_row_id", "outer_fold", "probability"]].rename(
                columns={"probability": column}
            ), on=["model_row_id", "outer_fold"], validate="one_to_one",
        )
    downside_columns = [f"prob_fp_le_{int(t)}" for t in DOWNSIDE_THRESHOLDS]
    output[downside_columns] = np.maximum.accumulate(
        output[downside_columns].to_numpy(float), axis=1
    )
    return output


def _central_comparison(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "phase6b": _regression_metrics(frame.actual_fp, frame.expected_fp),
        "phase6c": _regression_metrics(frame.actual_fp, frame.phase6c_expected_fp),
        "by_fold": {
            fold: {
                "phase6b": _regression_metrics(group.actual_fp, group.expected_fp),
                "phase6c": _regression_metrics(group.actual_fp, group.phase6c_expected_fp),
            } for fold, group in frame.groupby("outer_fold")
        },
    }


def _distribution_comparison(frame: pd.DataFrame) -> dict[str, Any]:
    quantiles: dict[str, Any] = {}
    for quantile in QUANTILES:
        suffix = f"p{int(quantile*100):02d}_fp"
        quantiles[f"p{int(quantile*100):02d}"] = {
            "phase6b": quantile_metrics(frame.actual_fp, frame[f"phase6b_{suffix}"], quantile),
            "phase6c": quantile_metrics(frame.actual_fp, frame[suffix], quantile),
        }
    upside: dict[str, Any] = {}
    for threshold in THRESHOLDS:
        labels = frame.actual_fp.ge(threshold).astype(int)
        column = f"prob_fp_ge_{int(threshold)}"
        upside[str(int(threshold))] = {
            "phase6b": _classification_metrics(labels, frame[f"phase6b_{column}"]),
            "phase6c": _classification_metrics(labels, frame[column]),
        }
    downside: dict[str, Any] = {}
    q = frame[[f"phase6b_p{int(value*100):02d}_fp" for value in QUANTILES]].to_numpy(float)
    for threshold in DOWNSIDE_THRESHOLDS:
        labels = frame.actual_fp.le(threshold).astype(int)
        baseline = _cdf_from_quantiles(q, threshold)
        downside[str(int(threshold))] = {
            "phase6b_quantile_derived": _classification_metrics(labels, baseline),
            "phase6c_direct": _classification_metrics(
                labels, frame[f"prob_fp_le_{int(threshold)}"]
            ),
        }
    return {"quantiles": quantiles, "upside": upside, "downside": downside}


def _ranking_metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    correlations: list[float] = []
    top1: list[float] = []
    top3: list[float] = []
    for _, group in frame.groupby("game_id"):
        if len(group) < 5:
            continue
        correlation = spearmanr(group.actual_fp, group[column]).statistic
        if np.isfinite(correlation):
            correlations.append(float(correlation))
        predicted = group.nlargest(3, column).model_row_id.tolist()
        actual = group.nlargest(3, "actual_fp").model_row_id.tolist()
        top1.append(float(predicted[0] == actual[0]))
        top3.append(len(set(predicted) & set(actual)) / 3.0)
    return {
        "games": len(top1), "mean_game_spearman": float(np.mean(correlations)),
        "top1_hit_rate": float(np.mean(top1)), "top3_recall": float(np.mean(top3)),
    }


def _usage_effects(predictions: pd.DataFrame, primary: pd.DataFrame) -> list[dict[str, Any]]:
    context = primary[[
        "model_row_id", "p6c_player_archetype", "expected_usage_next_game",
        "p6c_usage_last5", "last_5_fp_per_min",
    ]]
    joined = predictions.merge(context, on="model_row_id", validate="one_to_one")
    joined["expected_usage_change"] = (
        joined.expected_usage_next_game - joined.p6c_usage_last5
    )
    joined["absolute_error_improvement"] = (
        (joined.actual_fp - joined.expected_fp).abs()
        - (joined.actual_fp - joined.phase6c_expected_fp).abs()
    )
    rows = []
    for archetype, group in joined.groupby("p6c_player_archetype"):
        rows.append({
            "archetype": str(archetype), "rows": len(group),
            "mean_expected_usage_change": float(group.expected_usage_change.mean()),
            "mae_improvement": float(group.absolute_error_improvement.mean()),
            "usage_change_improvement_correlation": _safe_corr(
                group.expected_usage_change, group.absolute_error_improvement
            ),
        })
    return rows


def _absence_redistribution_diagnostic(
    predictions: pd.DataFrame, primary: pd.DataFrame, database_path: Path | str,
) -> dict[str, Any]:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        absence = connection.execute(
            "SELECT model_row_id,number_players_out,missing_expected_minutes,"
            "missing_assist_rate,missing_ballhandling_proxy "
            "FROM ml_phase5b_absence_recipient_rows_v1"
        ).df()
    context = primary[[
        "model_row_id", "actual_usage_next_game", "expected_usage_next_game",
        "p6c_assist_share_last5",
    ]]
    joined = predictions[["model_row_id", "actual_fp"]].merge(
        context, on="model_row_id", validate="one_to_one"
    ).merge(absence, on="model_row_id", how="inner", validate="one_to_one")
    joined["usage_surprise"] = (
        joined.actual_usage_next_game - joined.expected_usage_next_game
    )
    return {
        "semantics": "conditional on the Phase 5B known absence set; not a main-model input",
        "rows": len(joined),
        "missing_minutes_usage_surprise_correlation": _safe_corr(
            joined.missing_expected_minutes, joined.usage_surprise
        ),
        "missing_creation_usage_surprise_correlation": _safe_corr(
            joined.missing_assist_rate, joined.usage_surprise
        ),
        "mean_usage_surprise_by_out_count": {
            str(int(key)): float(group.usage_surprise.mean())
            for key, group in joined.groupby("number_players_out")
        },
    }


def _fit_importance_model(
    frame: pd.DataFrame, manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    model = _fit_central_fixed(frame, manifest, 220)
    values = model.get_feature_importance()
    rows = sorted(
        ({"feature": feature, "importance": float(value)}
         for feature, value in zip(manifest["features"], values)),
        key=lambda row: row["importance"], reverse=True,
    )
    return rows[:30]


def _tail_comparison(frame: pd.DataFrame) -> dict[str, Any]:
    explosions = frame[frame.actual_fp.ge(35)].copy()
    misses = frame[frame.actual_fp.ge(40)].copy()
    return {
        "explosion_rows": len(explosions),
        "phase6b_p95_pinball_on_explosions": pinball_loss(
            explosions.actual_fp.to_numpy(float),
            explosions.phase6b_p95_fp.to_numpy(float), 0.95,
        ),
        "phase6c_p95_pinball_on_explosions": pinball_loss(
            explosions.actual_fp.to_numpy(float), explosions.p95_fp.to_numpy(float), 0.95,
        ),
        "rare_40_plus_rows": len(misses),
        "phase6b_mean_40_plus_p95_shortfall": float(
            (misses.actual_fp - misses.phase6b_p95_fp).clip(lower=0).mean()
        ),
        "phase6c_mean_40_plus_p95_shortfall": float(
            (misses.actual_fp - misses.p95_fp).clip(lower=0).mean()
        ),
    }


def _base_manifest() -> dict[str, Any]:
    from .ml_protocol import feature_manifest
    base = feature_manifest("CORE_ROTATION", include_player_id=True)
    return {**base, "families": []}


def _fit_platt(probability: np.ndarray, labels: np.ndarray) -> LogisticRegression:
    model = LogisticRegression(C=1e6, solver="lbfgs", random_state=PHASE6C_RANDOM_SEED)
    model.fit(_logit(probability).reshape(-1, 1), labels)
    return model


def _classification_metrics(labels: Sequence[int], probability: Sequence[float]) -> dict[str, Any]:
    y = np.asarray(labels, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-9, 1 - 1e-9)
    return {
        "rows": len(y), "positives": int(y.sum()), "positive_rate": float(y.mean()),
        "mean_probability": float(p.mean()),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
    }


def _shift_survival(probability: np.ndarray, thresholds: np.ndarray, delta: np.ndarray) -> np.ndarray:
    logits = _logit(probability)
    output = np.empty_like(logits)
    for row in range(len(logits)):
        query = thresholds - delta[row]
        values = np.interp(query, thresholds, logits[row])
        left = query < thresholds[0]
        right = query > thresholds[-1]
        left_slope = (logits[row, 1] - logits[row, 0]) / (thresholds[1] - thresholds[0])
        right_slope = (logits[row, -1] - logits[row, -2]) / (thresholds[-1] - thresholds[-2])
        values[left] = logits[row, 0] + left_slope * (query[left] - thresholds[0])
        values[right] = logits[row, -1] + right_slope * (query[right] - thresholds[-1])
        output[row] = _sigmoid(values)
    return np.minimum.accumulate(np.clip(output, 0.0, 1.0), axis=1)


def _cdf_from_quantiles(quantiles: np.ndarray, threshold: float) -> np.ndarray:
    levels = np.asarray(QUANTILES, dtype=float)
    output = np.empty(len(quantiles), dtype=float)
    for index, row in enumerate(np.sort(quantiles, axis=1)):
        x = np.r_[-20.0, row, 70.0]
        y = np.r_[0.0, levels, 1.0]
        output[index] = np.interp(threshold, x, y)
    return np.clip(output, 0.0, 1.0)


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(values, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -20, 20)))


def _safe_corr(left: Sequence[float], right: Sequence[float]) -> float | None:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 3 or frame.left.nunique() < 2 or frame.right.nunique() < 2:
        return None
    return float(pearsonr(frame.left, frame.right).statistic)


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    columns = [
        "model_row_id", "actual_fantasy_points", "actual_usage_next_game",
        "expected_usage_next_game",
    ]
    work = frame.sort_values("model_row_id")[columns]
    return hashlib.sha256(
        pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    ).hexdigest()


def _prediction_fingerprint(frame: pd.DataFrame) -> str:
    columns = [
        "model_row_id", "outer_fold", "phase6c_expected_fp", "p90_fp", "p95_fp",
        *[f"prob_fp_ge_{int(t)}" for t in THRESHOLDS],
        *[f"prob_fp_le_{int(t)}" for t in DOWNSIDE_THRESHOLDS],
    ]
    work = frame.sort_values(["outer_fold", "model_row_id"])[columns]
    return hashlib.sha256(
        pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    ).hexdigest()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.register("_output", frame)
        connection.execute("COPY _output TO ? (FORMAT PARQUET,COMPRESSION ZSTD)", [str(path)])


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    raise TypeError(type(value).__name__)
