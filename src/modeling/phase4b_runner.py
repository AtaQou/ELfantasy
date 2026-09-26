"""Orchestration and persistence for the frozen Phase 4B experiment protocol."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .ml_experiments import regression_metrics
from .ml_runner import _write_parquet
from .phase4b_analysis import (
    align_two,
    architecture_metrics,
    calibrate_architecture,
    conformal_intervals,
    grouped_metrics,
    hybrid_predictions,
    minutes_diagnostics,
    oracle_diagnostics,
    outcome_band_metrics,
    paired_comparison,
    prediction_bin_metrics,
    prediction_fingerprint,
    safe_correlation,
    select_hybrid_weight,
    stage_diagnostics,
)
from .phase4b_models import (
    MINUTES_CANDIDATES,
    PRODUCTION_CANDIDATES,
    ComponentCandidate,
    component_prediction_fingerprint,
    direct_loss_experiment_id,
    fit_direct_loss_predictions,
    run_component_candidate,
)
from .phase4b_protocol import (
    NOMINAL_INTERVAL_COVERAGE,
    PHASE4A_WINNER_EXPERIMENT_ID,
    PHASE4A_WINNER_MAE,
    PHASE4B_DATASET_VERSION,
    PHASE4B_PROTOCOL_VERSION,
    PHASE4B_RANDOM_SEED,
    SAME_ROW_BASELINE_MAE,
    apply_role_change_class,
    build_phase4b_folds,
    load_phase4b_frame,
    role_change_thresholds,
    validate_phase4b_frame,
    write_phase4b_protocol,
)


PHASE4B_DERIVED_ROOT = Path("data/derived/phase4b")
PHASE4B_SAMPLE_ROOT = Path("data/samples/phase4b")
PHASE4B_REPORT_ROOT = Path("reports")


def run_phase4b_experiments(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = PHASE4B_DERIVED_ROOT,
    sample_root: Path = PHASE4B_SAMPLE_ROOT,
    report_root: Path = PHASE4B_REPORT_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    derived_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    write_phase4b_protocol(sample_root / "protocol.json")

    primary = load_phase4b_frame(database_path, primary_only=True)
    full_history = load_phase4b_frame(database_path, primary_only=False)
    validate_phase4b_frame(primary)
    folds = build_phase4b_folds(primary)
    frozen_direct = load_frozen_direct(database_path, primary)
    ridge_diagnostic = load_phase4a_reference(
        database_path, primary,
        "p4a__ridge__core__verified_only__player__s17",
        model_version="phase4a_ridge_core_diagnostic_v1",
    )

    direct_loss_results = {}
    for loss in ("MAE", "RMSE", "Huber:delta=5.0"):
        direct_loss_results[loss] = run_or_load_direct_loss(
            primary, folds, loss_function=loss,
            include_outer=loss != "MAE", derived_root=derived_root, force=force,
        )
    selected_loss = min(
        direct_loss_results,
        key=lambda loss: (direct_loss_results[loss]["inner_mae"],
                          ("MAE", "RMSE", "Huber:delta=5.0").index(loss)),
    )
    direct_inner = direct_loss_results["MAE"]["inner_predictions"]

    minute_results: dict[str, dict[str, Any]] = {}
    for candidate in MINUTES_CANDIDATES:
        print(f"PHASE4B {candidate.experiment_id}", flush=True)
        minute_results[candidate.experiment_id] = run_or_load_component(
            primary, full_history, folds, candidate,
            derived_root=derived_root, force=force,
        )
    selected_minutes_id = min(
        minute_results,
        key=lambda eid: (
            minute_results[eid]["inner_selection_score"],
            [candidate.experiment_id for candidate in MINUTES_CANDIDATES].index(eid),
        ),
    )
    selected_minutes = minute_results[selected_minutes_id]

    production_results: dict[str, dict[str, Any]] = {}
    minute_dependency = selected_minutes["prediction_fingerprint"]
    for candidate in PRODUCTION_CANDIDATES:
        print(f"PHASE4B {candidate.experiment_id}", flush=True)
        production_results[candidate.experiment_id] = run_or_load_component(
            primary, full_history, folds, candidate,
            inner_minutes=selected_minutes["inner_predictions"],
            outer_minutes=selected_minutes["outer_predictions"],
            derived_root=derived_root, force=force,
            dependency_fingerprint=minute_dependency,
        )
    selected_production_id = min(
        production_results,
        key=lambda eid: (
            production_results[eid]["inner_selection_score"],
            [candidate.experiment_id for candidate in PRODUCTION_CANDIDATES].index(eid),
        ),
    )
    selected_production = production_results[selected_production_id]
    decomposed_inner = selected_production["inner_predictions"]
    decomposed = selected_production["outer_predictions"].copy()
    decomposed["experiment_id"] = "p4b__decomposed__selected"
    decomposed["model_version"] = decomposed["experiment_id"]
    decomposed["architecture"] = "decomposed"

    hybrid_weight, hybrid_trials = select_hybrid_weight(
        direct_inner, decomposed_inner
    )
    hybrid_inner = {
        fold_id: hybrid_predictions(
            direct_inner[fold_id], decomposed_inner[fold_id], hybrid_weight,
            experiment_id="p4b__hybrid__selected",
        )
        for fold_id in direct_inner
    }
    hybrid = hybrid_predictions(
        frozen_direct, decomposed, hybrid_weight,
        experiment_id="p4b__hybrid__selected",
    )

    calibrated_direct_result = calibrate_architecture(
        direct_inner, frozen_direct,
        experiment_prefix="p4b__calibrated_direct",
    )
    calibrated_direct = calibrated_direct_result["outer_predictions"]
    calibrated_direct["architecture"] = "calibrated_direct"
    calibrated_hybrid_result = calibrate_architecture(
        hybrid_inner, hybrid,
        experiment_prefix="p4b__calibrated_hybrid",
    )
    calibrated_hybrid = calibrated_hybrid_result["outer_predictions"]
    calibrated_hybrid["architecture"] = "calibrated_hybrid"

    alternative_outer: dict[str, pd.DataFrame] = {}
    for loss in ("RMSE", "Huber:delta=5.0"):
        frame = direct_loss_results[loss]["outer_predictions"].copy()
        frame["architecture"] = f"direct_{loss.lower().split(':')[0]}"
        alternative_outer[loss] = frame
    selected_loss_outer = (
        frozen_direct if selected_loss == "MAE" else alternative_outer[selected_loss]
    )

    architectures = {
        "Phase4A_direct": frozen_direct,
        "best_decomposed": decomposed,
        "best_calibrated_direct": calibrated_direct,
        "hybrid": hybrid,
        "calibrated_hybrid": calibrated_hybrid,
        "direct_rmse_loss": alternative_outer["RMSE"],
        "direct_huber_loss": alternative_outer["Huber:delta=5.0"],
    }
    interval_results = {
        "Phase4A_direct": conformal_intervals(
            direct_inner, frozen_direct, primary, folds,
            experiment_id="p4b__uncertainty__direct",
        ),
        "best_decomposed": conformal_intervals(
            decomposed_inner, decomposed, primary, folds,
            experiment_id="p4b__uncertainty__decomposed",
        ),
        "hybrid": conformal_intervals(
            hybrid_inner, hybrid, primary, folds,
            experiment_id="p4b__uncertainty__hybrid",
        ),
        "best_calibrated_direct": conformal_intervals(
            calibrated_direct_result["interval_inner_predictions"],
            calibrated_direct, primary, folds,
            experiment_id="p4b__uncertainty__calibrated_direct",
        ),
        "calibrated_hybrid": conformal_intervals(
            calibrated_hybrid_result["interval_inner_predictions"],
            calibrated_hybrid, primary, folds,
            experiment_id="p4b__uncertainty__calibrated_hybrid",
        ),
        "direct_rmse_loss": conformal_intervals(
            direct_loss_results["RMSE"]["inner_predictions"],
            alternative_outer["RMSE"], primary, folds,
            experiment_id="p4b__uncertainty__direct_rmse",
        ),
        "direct_huber_loss": conformal_intervals(
            direct_loss_results["Huber:delta=5.0"]["inner_predictions"],
            alternative_outer["Huber:delta=5.0"], primary, folds,
            experiment_id="p4b__uncertainty__direct_huber",
        ),
    }

    summary = build_summary(
        primary=primary, folds=folds, architectures=architectures,
        minute_results=minute_results, production_results=production_results,
        selected_minutes_id=selected_minutes_id,
        selected_production_id=selected_production_id,
        selected_loss=selected_loss, direct_loss_results=direct_loss_results,
        hybrid_weight=hybrid_weight, hybrid_trials=hybrid_trials,
        calibrated_direct_result=calibrated_direct_result,
        calibrated_hybrid_result=calibrated_hybrid_result,
        interval_results=interval_results,
        ridge_diagnostic=ridge_diagnostic,
    )
    final_name = summary["final_selection"]["architecture"]
    final_predictions = architectures[final_name]
    final_intervals = interval_results.get(final_name)
    if final_intervals is None:
        raise ValueError(f"No uncertainty predictions for final architecture {final_name}")
    summary["final_selection"]["interval_evaluation"] = final_intervals["evaluation"]
    summary["final_selection"]["final_performance_model_version"] = (
        "phase4b_conditional_performance_frozen_v1"
    )
    summary["final_selection"]["prediction_fingerprint"] = prediction_fingerprint(
        final_predictions
    )

    persisted = materialize_phase4b(
        database_path=database_path, derived_root=derived_root,
        architectures=architectures,
        minute_results=minute_results, production_results=production_results,
        selected_minutes_id=selected_minutes_id,
        selected_production_id=selected_production_id,
        final_intervals=final_intervals,
        summary=summary,
        direct_loss_results=direct_loss_results,
        calibrated_direct_result=calibrated_direct_result,
        calibrated_hybrid_result=calibrated_hybrid_result,
    )
    summary["persisted"] = persisted
    (sample_root / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )
    freeze = build_freeze_payload(
        summary, selected_minutes, selected_production,
        calibrated_direct_result, calibrated_hybrid_result,
    )
    previous_fingerprint = None
    if (sample_root / "final_model.json").exists():
        previous_payload = json.loads(
            (sample_root / "final_model.json").read_text(encoding="utf-8")
        )
        previous_fingerprint = previous_payload.get("prediction_fingerprint")
    reproducibility = {
        "previous_prediction_fingerprint": previous_fingerprint,
        "current_prediction_fingerprint": freeze["prediction_fingerprint"],
        "fingerprints_match": (
            previous_fingerprint == freeze["prediction_fingerprint"]
            if previous_fingerprint is not None else None
        ),
        "random_seed": PHASE4B_RANDOM_SEED,
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
    }
    summary["reproducibility"] = reproducibility
    (sample_root / "reproducibility.json").write_text(
        json.dumps(reproducibility, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (sample_root / "final_model.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )
    (sample_root / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )

    from .phase4b_reports import write_phase4b_reports

    write_phase4b_reports(summary, report_root=report_root)
    return summary


def load_frozen_direct(
    database_path: Path | str, primary: pd.DataFrame
) -> pd.DataFrame:
    with connect_database(database_path, read_only=True) as connection:
        frame = connection.execute(
            "SELECT * FROM ml_phase4a_outer_predictions_v1 WHERE experiment_id=? "
            "ORDER BY outer_fold, model_row_id",
            [PHASE4A_WINNER_EXPERIMENT_ID],
        ).df()
    if len(frame) != 22399:
        raise ValueError("Frozen Phase 4A winner prediction population changed")
    context_columns = [
        "model_row_id", "competition_stage", "postseason_game",
        "role_change_score", "career_el_games_before",
    ]
    context = primary[context_columns].drop_duplicates("model_row_id")
    frame = frame.drop(
        columns=[column for column in context_columns[1:] if column in frame],
        errors="ignore",
    ).merge(context, on="model_row_id", how="left", validate="one_to_one")
    frame["architecture"] = "direct"
    frame["model_version"] = "phase4a_catboost_core_rotation_frozen_v1"
    metrics = architecture_metrics(frame)
    if not math_close(metrics["mae"], 5.8272, tolerance=0.0001):
        raise ValueError(f"Frozen Phase 4A MAE changed: {metrics['mae']}")
    return frame


def load_phase4a_reference(
    database_path: Path | str,
    primary: pd.DataFrame,
    experiment_id: str,
    *,
    model_version: str,
) -> pd.DataFrame:
    with connect_database(database_path, read_only=True) as connection:
        frame = connection.execute(
            "SELECT * FROM ml_phase4a_outer_predictions_v1 WHERE experiment_id=? "
            "ORDER BY outer_fold, model_row_id", [experiment_id]
        ).df()
    context = primary[[
        "model_row_id", "competition_stage", "postseason_game",
        "role_change_score", "career_el_games_before",
    ]].drop_duplicates("model_row_id")
    frame = frame.drop(
        columns=[column for column in context.columns[1:] if column in frame],
        errors="ignore",
    ).merge(context, on="model_row_id", how="left", validate="one_to_one")
    frame["model_version"] = model_version
    return frame


def run_or_load_component(
    primary: pd.DataFrame,
    full_history: pd.DataFrame,
    folds: list[dict[str, Any]],
    candidate: ComponentCandidate,
    *,
    derived_root: Path,
    force: bool,
    inner_minutes: dict[str, pd.DataFrame] | None = None,
    outer_minutes: pd.DataFrame | None = None,
    dependency_fingerprint: str | None = None,
) -> dict[str, Any]:
    root = derived_root / "components" / candidate.experiment_id
    complete = root / "complete.json"
    if not force and complete.exists():
        payload = json.loads(complete.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") == PHASE4B_PROTOCOL_VERSION
            and payload.get("dependency_fingerprint") == dependency_fingerprint
        ):
            return load_component_cache(root, payload)
    result = run_component_candidate(
        primary, full_history, folds, candidate,
        inner_minutes=inner_minutes, outer_minutes=outer_minutes,
    )
    save_component_cache(root, result, dependency_fingerprint)
    return result


def save_component_cache(
    root: Path, result: dict[str, Any], dependency_fingerprint: str | None
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_parquet(result["outer_predictions"], root / "outer_predictions.parquet")
    for fold_id, frame in result["inner_predictions"].items():
        _write_parquet(frame, root / f"inner_{fold_id}.parquet")
    _write_parquet(pd.DataFrame(result["trials"]), root / "trials.parquet")
    _write_parquet(pd.DataFrame(result["selections"]), root / "selections.parquet")
    _write_parquet(pd.DataFrame(result["importance"]), root / "importance.parquet")
    _write_parquet(pd.DataFrame(result["preprocessing"]), root / "preprocessing.parquet")
    payload = {
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
        "dataset_version": PHASE4B_DATASET_VERSION,
        "candidate": asdict(result["candidate"]),
        "manifest": result["manifest"],
        "train_seconds": result["train_seconds"],
        "inner_selection_score": result["inner_selection_score"],
        "prediction_fingerprint": result["prediction_fingerprint"],
        "dependency_fingerprint": dependency_fingerprint,
        "inner_folds": sorted(result["inner_predictions"]),
    }
    (root / "complete.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def load_component_cache(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": ComponentCandidate(**payload["candidate"]),
        "manifest": payload["manifest"],
        "trials": read_parquet(root / "trials.parquet").to_dict("records"),
        "selections": read_parquet(root / "selections.parquet").to_dict("records"),
        "inner_predictions": {
            fold_id: read_parquet(root / f"inner_{fold_id}.parquet")
            for fold_id in payload["inner_folds"]
        },
        "outer_predictions": read_parquet(root / "outer_predictions.parquet"),
        "importance": read_parquet(root / "importance.parquet").to_dict("records"),
        "preprocessing": read_parquet(root / "preprocessing.parquet").to_dict("records"),
        "train_seconds": payload["train_seconds"],
        "inner_selection_score": payload["inner_selection_score"],
        "prediction_fingerprint": payload["prediction_fingerprint"],
    }


def run_or_load_direct_loss(
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    *,
    loss_function: str,
    include_outer: bool,
    derived_root: Path,
    force: bool,
) -> dict[str, Any]:
    eid = direct_loss_experiment_id(loss_function)
    root = derived_root / "direct_losses" / eid
    complete = root / "complete.json"
    if not force and complete.exists():
        payload = json.loads(complete.read_text(encoding="utf-8"))
        if payload.get("protocol_version") == PHASE4B_PROTOCOL_VERSION:
            return {
                "inner_predictions": {
                    fold_id: read_parquet(root / f"inner_{fold_id}.parquet")
                    for fold_id in payload["inner_folds"]
                },
                "outer_predictions": (
                    read_parquet(root / "outer_predictions.parquet")
                    if payload["has_outer"] else pd.DataFrame()
                ),
                "audit": payload["audit"], "inner_mae": payload["inner_mae"],
            }
    print(f"PHASE4B {eid}", flush=True)
    result = fit_direct_loss_predictions(
        primary, folds, loss_function=loss_function, include_outer=include_outer
    )
    root.mkdir(parents=True, exist_ok=True)
    for fold_id, frame in result["inner_predictions"].items():
        _write_parquet(frame, root / f"inner_{fold_id}.parquet")
    if include_outer:
        _write_parquet(result["outer_predictions"], root / "outer_predictions.parquet")
    (root / "complete.json").write_text(json.dumps({
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
        "dataset_version": PHASE4B_DATASET_VERSION,
        "loss_function": loss_function,
        "inner_folds": sorted(result["inner_predictions"]),
        "has_outer": include_outer,
        "audit": result["audit"], "inner_mae": result["inner_mae"],
    }, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")
    return result


def read_parquet(path: Path) -> pd.DataFrame:
    connection = duckdb.connect()
    try:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    finally:
        connection.close()


def build_summary(
    *,
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    architectures: dict[str, pd.DataFrame],
    minute_results: dict[str, dict[str, Any]],
    production_results: dict[str, dict[str, Any]],
    selected_minutes_id: str,
    selected_production_id: str,
    selected_loss: str,
    direct_loss_results: dict[str, dict[str, Any]],
    hybrid_weight: float,
    hybrid_trials: list[dict[str, Any]],
    calibrated_direct_result: dict[str, Any],
    calibrated_hybrid_result: dict[str, Any],
    interval_results: dict[str, dict[str, Any]],
    ridge_diagnostic: pd.DataFrame,
) -> dict[str, Any]:
    direct = architectures["Phase4A_direct"]
    baseline_parts = []
    for fold in folds:
        fold_id = fold["definition"].fold_id
        part = primary.loc[fold["outer_test"]].copy()
        part["outer_fold"] = fold_id
        part["actual_fp"] = part["actual_fantasy_points"].to_numpy(float)
        part["predicted_fp"] = part["prediction_season_recent_blend"].to_numpy(float)
        baseline_parts.append(part)
    same_row_baseline = pd.concat(baseline_parts, ignore_index=True)
    architecture_rows = []
    detailed: dict[str, Any] = {}
    for name, frame in architectures.items():
        metrics = architecture_metrics(frame)
        fold_metrics = grouped_metrics(frame, ["outer_fold"])
        architecture_rows.append({"architecture": name, **metrics})
        role_frame = add_outer_role_classes(frame, primary, folds)
        history_frame = role_frame.copy()
        history_frame["history_group"] = np.where(
            history_frame["career_el_games_before"] >= 10, "10+ prior games",
            "<10 prior games",
        )
        detailed[name] = {
            "metrics": metrics,
            "fold_metrics": fold_metrics,
            "actual_outcome_bands": outcome_band_metrics(frame),
            "prediction_calibration_bins": prediction_bin_metrics(frame),
            "role_change_metrics": grouped_metrics(role_frame, ["role_change_class"]),
            "history_metrics": grouped_metrics(history_frame, ["history_group"]),
            "stage_metrics": stage_diagnostics(frame),
            "high_score_metrics": high_score_metrics(frame),
            "paired_vs_phase4a": (
                None if name == "Phase4A_direct" else paired_comparison(direct, frame)
            ),
        }
    minute_rows = []
    for eid, result in minute_results.items():
        diagnostics = minutes_diagnostics(result["outer_predictions"])
        minute_rows.append({
            "experiment_id": eid,
            "model_type": result["candidate"].model_type,
            "feature_manifest": result["candidate"].manifest_name,
            "uses_older_history": result["candidate"].use_older_history,
            "inner_selection_mae": result["inner_selection_score"],
            **{key: diagnostics[key] for key in (
                "mae", "rmse", "spearman", "pearson", "mean_residual",
                "maximum_actual_minutes", "maximum_predicted_minutes", "rows",
            )},
        })
        detailed.setdefault("minutes_candidates", {})[eid] = diagnostics
    production_rows = []
    for eid, result in production_results.items():
        frame = result["outer_predictions"]
        rate_metrics = regression_metrics(
            frame["actual_fp_per_min"], frame["predicted_fp_per_min"]
        )
        fp_metrics = architecture_metrics(frame)
        production_rows.append({
            "experiment_id": eid, "model_type": result["candidate"].model_type,
            "feature_manifest": result["candidate"].manifest_name,
            "target_strategy": result["candidate"].target_strategy,
            "uses_older_history": result["candidate"].use_older_history,
            "inner_decomposed_mae": result["inner_selection_score"],
            "outer_rate_mae": rate_metrics["mae"],
            "outer_rate_rmse": rate_metrics["rmse"],
            "outer_decomposed_mae": fp_metrics["mae"],
            "outer_decomposed_rmse": fp_metrics["rmse"],
        })

    final_selection = choose_final_architecture(architectures)
    subgroup_models = {
        "CatBoost_direct": direct,
        "Ridge_direct": ridge_diagnostic,
        "Decomposed": architectures["best_decomposed"],
        "Hybrid": architectures["hybrid"],
    }
    high_minute_comparison = []
    low_history_comparison = []
    for model_name, frame in subgroup_models.items():
        copy = frame.copy()
        copy["minutes_band"] = copy["target_minutes"].map(minutes_band_label)
        for band, group in copy.groupby("minutes_band", sort=False):
            if band not in {"20-25", "25-30", "30+"}:
                continue
            high_minute_comparison.append({
                "model": model_name, "minutes_band": band, "rows": len(group),
                **regression_metrics(group["actual_fp"], group["predicted_fp"]),
            })
        copy["history_group"] = np.where(
            copy["career_el_games_before"] >= 10,
            "10+ prior games", "<10 prior games",
        )
        for group_name, group in copy.groupby("history_group", sort=True):
            low_history_comparison.append({
                "model": model_name, "history_group": group_name,
                "rows": len(group),
                **regression_metrics(group["actual_fp"], group["predicted_fp"]),
            })
    decomposed = architectures["best_decomposed"]
    decomp_left, direct_right = align_two(decomposed, direct)
    residual_correlation = safe_correlation(
        decomp_left["actual_fp"].to_numpy(float) - decomp_left["predicted_fp"].to_numpy(float),
        direct_right["actual_fp"].to_numpy(float) - direct_right["predicted_fp"].to_numpy(float),
    )
    selected_minutes = minute_results[selected_minutes_id]["outer_predictions"]
    selected_minutes_roles = add_outer_role_classes(selected_minutes, primary, folds)
    selected_minutes_history = selected_minutes_roles.copy()
    selected_minutes_history["history_group"] = np.where(
        selected_minutes_history["career_el_games_before"] >= 10,
        "10+ prior games", "<10 prior games",
    )
    selected_minute_importance = pd.DataFrame(
        minute_results[selected_minutes_id]["importance"]
    )
    strongest_minutes_features = []
    if not selected_minute_importance.empty:
        strongest_minutes_features = (
            selected_minute_importance.groupby("raw_feature", as_index=False)[
                "normalized_importance"
            ].mean().sort_values("normalized_importance", ascending=False).head(15)
            .to_dict("records")
        )
    stage_ablation = controlled_ablation(
        minute_rows, "p4b__minutes__catboost__role",
        "p4b__minutes__catboost__role_stage", "mae"
    )
    production_stage_ablation = controlled_ablation(
        production_rows, "p4b__production__catboost__weighted",
        "p4b__production__catboost__weighted_stage", "outer_decomposed_mae"
    )
    shot_ablation = controlled_ablation(
        production_rows, "p4b__production__catboost__weighted",
        "p4b__production__catboost__weighted_shot", "outer_decomposed_mae"
    )
    minutes_history_ablation = controlled_ablation(
        minute_rows, "p4b__minutes__catboost__role",
        "p4b__minutes__catboost__role_older_history", "mae"
    )
    production_history_ablation = controlled_ablation(
        production_rows, "p4b__production__catboost__weighted",
        "p4b__production__catboost__weighted_older_history",
        "outer_decomposed_mae",
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
        "dataset_version": PHASE4B_DATASET_VERSION,
        "population": "conditional_on_playing",
        "outer_rows": len(direct),
        "phase3_same_row_baseline_mae": SAME_ROW_BASELINE_MAE,
        "phase3_same_row_baseline_metrics": architecture_metrics(same_row_baseline),
        "phase4a_frozen_winner_mae": PHASE4A_WINNER_MAE,
        "minutes_candidates": minute_rows,
        "selected_minutes_experiment_id": selected_minutes_id,
        "selected_minutes_diagnostics": minutes_diagnostics(selected_minutes),
        "selected_minutes_role_change_metrics": minutes_role_metrics(
            selected_minutes_roles, "role_change_class"
        ),
        "selected_minutes_history_metrics": minutes_role_metrics(
            selected_minutes_history, "history_group"
        ),
        "strongest_minutes_features": strongest_minutes_features,
        "production_candidates": production_rows,
        "selected_production_experiment_id": selected_production_id,
        "architectures": architecture_rows,
        "architecture_diagnostics": detailed,
        "direct_decomposed_residual_correlation": residual_correlation,
        "high_minute_architecture_comparison": high_minute_comparison,
        "history_architecture_comparison": low_history_comparison,
        "hybrid": {
            "weight_direct": hybrid_weight,
            "weight_decomposed": 1.0 - hybrid_weight,
            "inner_trials": hybrid_trials,
            "outer_test_rows_used_for_weight_selection": 0,
        },
        "direct_loss_selection": {
            "selected_loss": selected_loss,
            "inner_mae_by_loss": {
                loss: result["inner_mae"] for loss, result in direct_loss_results.items()
            },
            "outer_metrics": {
                name: architecture_metrics(architectures[name])
                for name in ("Phase4A_direct", "direct_rmse_loss", "direct_huber_loss")
            },
            "outer_test_used_for_loss_selection": False,
        },
        "calibration": {
            "direct": {
                **without_frames(calibrated_direct_result),
                "outer_metrics_by_method": {
                    method: architecture_metrics(frame)
                    for method, frame in calibrated_direct_result[
                        "outer_predictions_by_method"
                    ].items()
                },
            },
            "hybrid": {
                **without_frames(calibrated_hybrid_result),
                "outer_metrics_by_method": {
                    method: architecture_metrics(frame)
                    for method, frame in calibrated_hybrid_result[
                        "outer_predictions_by_method"
                    ].items()
                },
            },
        },
        "uncertainty": {
            name: {
                "evaluation": result["evaluation"], "audit": result["audit"]
            }
            for name, result in interval_results.items()
        },
        "oracle_diagnostics": oracle_diagnostics(decomposed),
        "competition_stage": {
            "source": "explicit games.phase_code; schedule-only FF ordering",
            "stage_counts": primary["competition_stage"].value_counts().sort_index().to_dict(),
            "minutes_stage_ablation": stage_ablation,
            "production_stage_ablation": production_stage_ablation,
            "selected_model_stage_diagnostics": detailed[
                final_selection["architecture"]
            ]["stage_metrics"],
        },
        "controlled_ablations": {
            "shot_profile": shot_ablation,
            "minutes_older_history": minutes_history_ablation,
            "production_older_history": production_history_ablation,
        },
        "final_selection": final_selection,
        "leakage_audit": {
            "target_game_actual_minutes_model_feature": False,
            "target_game_actual_fp_per_min_model_feature": False,
            "production_uses_predicted_minutes_as_feature": False,
            "chained_outer_minutes_are_genuine_outer_predictions": True,
            "outer_test_targets_fit_calibration": False,
            "outer_test_residuals_fit_intervals": False,
            "outer_test_scores_select_blend_weight": False,
            "outer_test_scores_select_component_models": False,
            "violations_detected": 0,
        },
    }


def choose_final_architecture(
    architectures: dict[str, pd.DataFrame]
) -> dict[str, Any]:
    metrics = {name: architecture_metrics(frame) for name, frame in architectures.items()}
    direct_mae = metrics["Phase4A_direct"]["mae"]
    candidates = [
        "Phase4A_direct", "best_calibrated_direct", "best_decomposed",
        "hybrid", "calibrated_hybrid", "direct_rmse_loss", "direct_huber_loss",
    ]
    best = min(candidates, key=lambda name: (metrics[name]["mae"], candidates.index(name)))
    best_improvement = direct_mae - metrics[best]["mae"]
    fold_direct = {
        row["outer_fold"]: row["mae"]
        for row in grouped_metrics(architectures["Phase4A_direct"], ["outer_fold"])
    }
    fold_best = {
        row["outer_fold"]: row["mae"]
        for row in grouped_metrics(architectures[best], ["outer_fold"])
    }
    fold_wins = sum(fold_best[key] < fold_direct[key] for key in fold_direct)
    reason = "lowest chronological outer MAE with multi-fold support"
    if best != "Phase4A_direct" and best_improvement < 0.01:
        best = "Phase4A_direct"
        reason = "best complex candidate improved by <0.01 FP; retained simpler frozen direct"
    elif best != "Phase4A_direct" and fold_wins < 2:
        best = "Phase4A_direct"
        reason = "best pooled candidate lacked improvement in at least two outer seasons"
    chosen = metrics[best]
    return {
        "architecture": best,
        "reason": reason,
        "metrics": chosen,
        "improvement_vs_phase4a_percent": 100.0 * (
            1.0 - chosen["mae"] / PHASE4A_WINNER_MAE
        ),
        "improvement_vs_same_row_baseline_percent": 100.0 * (
            1.0 - chosen["mae"] / SAME_ROW_BASELINE_MAE
        ),
        "conditional_on_playing": True,
    }


def add_outer_role_classes(
    frame: pd.DataFrame, primary: pd.DataFrame, folds: list[dict[str, Any]]
) -> pd.DataFrame:
    parts = []
    for fold in folds:
        fold_id = fold["definition"].fold_id
        thresholds = role_change_thresholds(primary.loc[fold["outer_train"]])
        part = frame[frame["outer_fold"] == fold_id].copy()
        part["role_change_class"] = apply_role_change_class(part, thresholds)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def minutes_role_metrics(frame: pd.DataFrame, group_column: str) -> list[dict[str, Any]]:
    rows = []
    for key, group in frame.groupby(group_column, sort=True):
        metrics = regression_metrics(group["actual_minutes"], group["predicted_minutes"])
        rows.append({group_column: str(key), "rows": len(group), **metrics})
    return rows


def high_score_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    result = {}
    for threshold, label in ((25.0, "25_plus"), (30.0, "30_plus")):
        group = frame[frame["actual_fp"] >= threshold]
        result[label] = {
            "rows": len(group),
            **regression_metrics(group["actual_fp"], group["predicted_fp"]),
        }
    return result


def minutes_band_label(value: float) -> str:
    if value < 10:
        return "<10"
    if value < 20:
        return "10-20"
    if value < 25:
        return "20-25"
    if value < 30:
        return "25-30"
    return "30+"


def controlled_ablation(
    rows: list[dict[str, Any]], base_id: str, variant_id: str, metric: str
) -> dict[str, Any]:
    base = next(row for row in rows if row["experiment_id"] == base_id)
    variant = next(row for row in rows if row["experiment_id"] == variant_id)
    return {
        "base_experiment_id": base_id, "variant_experiment_id": variant_id,
        "base_value": base[metric], "variant_value": variant[metric],
        "delta_variant_minus_base": variant[metric] - base[metric],
    }


def materialize_phase4b(
    *,
    database_path: Path | str,
    derived_root: Path,
    architectures: dict[str, pd.DataFrame],
    minute_results: dict[str, dict[str, Any]],
    production_results: dict[str, dict[str, Any]],
    selected_minutes_id: str,
    selected_production_id: str,
    final_intervals: dict[str, Any],
    summary: dict[str, Any],
    direct_loss_results: dict[str, dict[str, Any]],
    calibrated_direct_result: dict[str, Any],
    calibrated_hybrid_result: dict[str, Any],
) -> dict[str, Any]:
    outer_parts = []
    for name, frame in architectures.items():
        copy = frame.copy()
        copy["architecture_name"] = name
        copy["dataset_version"] = PHASE4B_DATASET_VERSION
        copy["protocol_version"] = PHASE4B_PROTOCOL_VERSION
        outer_parts.append(copy)
    outer_predictions = pd.concat(outer_parts, ignore_index=True, sort=False)
    minutes_predictions = pd.concat([
        result["outer_predictions"] for result in minute_results.values()
    ], ignore_index=True, sort=False)
    production_predictions = pd.concat([
        result["outer_predictions"] for result in production_results.values()
    ], ignore_index=True, sort=False)
    intervals = final_intervals["predictions"].copy()
    intervals["final_performance_model_version"] = (
        "phase4b_conditional_performance_frozen_v1"
    )
    experiments = experiment_tracking_rows(
        architectures, minute_results, production_results, summary
    )
    trials = pd.concat([
        pd.DataFrame(result["trials"]) for result in [
            *minute_results.values(), *production_results.values()
        ]
    ], ignore_index=True, sort=False)
    leakage_rows = [
        row for result in [*minute_results.values(), *production_results.values()]
        for row in result["preprocessing"]
    ]
    leakage_rows.extend(
        row for result in direct_loss_results.values() for row in result["audit"]
    )
    leakage_rows.extend(calibrated_direct_result["parameters"])
    leakage_rows.extend(calibrated_hybrid_result["parameters"])
    leakage_rows.extend(final_intervals["audit"])
    leakage_audit = pd.json_normalize(leakage_rows, sep="__")
    importance = pd.DataFrame([
        row for result in [*minute_results.values(), *production_results.values()]
        for row in result["importance"]
    ])
    calibration_predictions = pd.concat([
        frame.assign(calibration_base=base)
        for base, result in (
            ("direct", calibrated_direct_result),
            ("hybrid", calibrated_hybrid_result),
        )
        for frame in result["outer_predictions_by_method"].values()
    ], ignore_index=True, sort=False)
    frames = {
        "ml_phase4b_outer_predictions_v1": outer_predictions,
        "ml_phase4b_minutes_predictions_v1": minutes_predictions,
        "ml_phase4b_production_predictions_v1": production_predictions,
        "ml_phase4b_prediction_intervals_v1": intervals,
        "ml_phase4b_experiments_v1": experiments,
        "ml_phase4b_inner_trials_v1": trials,
        "ml_phase4b_leakage_audit_v1": leakage_audit,
        "ml_phase4b_feature_importance_v1": importance,
        "ml_phase4b_calibration_predictions_v1": calibration_predictions,
    }
    with connect_database(database_path) as connection:
        for table, frame in frames.items():
            registered = f"_{table}"
            connection.register(registered, frame)
            connection.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {registered}")
            connection.unregister(registered)
            _write_parquet(frame, derived_root / f"{table}.parquet")
        connection.execute(
            "CREATE OR REPLACE VIEW ml_phase4b_outer_predictions AS "
            "SELECT * FROM ml_phase4b_outer_predictions_v1; "
            "CREATE OR REPLACE VIEW ml_phase4b_prediction_intervals AS "
            "SELECT * FROM ml_phase4b_prediction_intervals_v1;"
        )
    return {
        "outer_prediction_rows": len(outer_predictions),
        "minutes_prediction_rows": len(minutes_predictions),
        "production_prediction_rows": len(production_predictions),
        "interval_rows": len(intervals),
        "experiment_rows": len(experiments),
        "trial_rows": len(trials),
        "selected_minutes_experiment_id": selected_minutes_id,
        "selected_production_experiment_id": selected_production_id,
    }


def experiment_tracking_rows(
    architectures: dict[str, pd.DataFrame],
    minute_results: dict[str, dict[str, Any]],
    production_results: dict[str, dict[str, Any]],
    summary: dict[str, Any],
) -> pd.DataFrame:
    rows = []
    for result in [*minute_results.values(), *production_results.values()]:
        candidate = result["candidate"]
        outer = result["outer_predictions"]
        for selection in result["selections"]:
            fold_id = selection["outer_fold"]
            test = outer[outer["outer_fold"] == fold_id]
            rows.append({
                "experiment_id": candidate.experiment_id,
                "dataset_version": PHASE4B_DATASET_VERSION,
                "protocol_version": PHASE4B_PROTOCOL_VERSION,
                "feature_manifest": candidate.manifest_name,
                "feature_manifest_sha256": result["manifest"]["sha256"],
                "target": candidate.task,
                "target_strategy": candidate.target_strategy,
                "model_type": candidate.model_type,
                "hyperparameters": selection["parameters"],
                "random_seed": PHASE4B_RANDOM_SEED,
                "train_rows": selection["inner_train_rows"],
                "validation_rows": selection["inner_validation_rows"],
                "test_rows": len(test), "outer_fold": fold_id,
                "metrics": json.dumps(
                    component_fold_metrics(test, candidate.task), sort_keys=True
                ),
                "prediction_fingerprint": result["prediction_fingerprint"],
            })
    for name, frame in architectures.items():
        for fold_id, group in frame.groupby("outer_fold", sort=True):
            rows.append({
                "experiment_id": str(group["experiment_id"].iloc[0]),
                "dataset_version": PHASE4B_DATASET_VERSION,
                "protocol_version": PHASE4B_PROTOCOL_VERSION,
                "feature_manifest": name, "feature_manifest_sha256": None,
                "target": "actual_fantasy_points", "target_strategy": name,
                "model_type": "architecture", "hyperparameters": None,
                "random_seed": PHASE4B_RANDOM_SEED,
                "train_rows": None, "validation_rows": None,
                "test_rows": len(group), "outer_fold": fold_id,
                "metrics": json.dumps(architecture_metrics(group), sort_keys=True),
                "prediction_fingerprint": prediction_fingerprint(frame),
            })
    return pd.DataFrame(rows)


def component_fold_metrics(frame: pd.DataFrame, task: str) -> dict[str, Any]:
    if task == "minutes":
        return regression_metrics(frame["actual_minutes"], frame["predicted_minutes"])
    return {
        "rate": regression_metrics(frame["actual_fp_per_min"], frame["predicted_fp_per_min"]),
        "decomposed": architecture_metrics(frame),
    }


def build_freeze_payload(
    summary: dict[str, Any],
    selected_minutes: dict[str, Any],
    selected_production: dict[str, Any],
    calibrated_direct_result: dict[str, Any],
    calibrated_hybrid_result: dict[str, Any],
) -> dict[str, Any]:
    final = summary["final_selection"]
    return {
        "status": "FROZEN",
        "final_performance_model_version": final["final_performance_model_version"],
        "conditional_on_playing": True,
        "architecture": final["architecture"],
        "metrics": final["metrics"],
        "dataset_version": PHASE4B_DATASET_VERSION,
        "protocol_version": PHASE4B_PROTOCOL_VERSION,
        "random_seed": PHASE4B_RANDOM_SEED,
        "phase4a_direct_base": {
            "experiment_id": PHASE4A_WINNER_EXPERIMENT_ID,
            "model": "CatBoost 1.2.10", "feature_set": "CORE_ROTATION",
            "depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
            "loss_function": "MAE", "fold_selected_trees": [205, 176, 191],
        },
        "minutes_component": {
            "experiment_id": selected_minutes["candidate"].experiment_id,
            "feature_manifest": selected_minutes["manifest"],
            "fold_selections": selected_minutes["selections"],
        },
        "production_component": {
            "experiment_id": selected_production["candidate"].experiment_id,
            "feature_manifest": selected_production["manifest"],
            "target_strategy": selected_production["candidate"].target_strategy,
            "fold_selections": selected_production["selections"],
        },
        "hybrid": summary["hybrid"],
        "direct_calibration": {
            "selected_method": calibrated_direct_result["selected_method"],
            "parameters": calibrated_direct_result["parameters"],
        },
        "hybrid_calibration": {
            "selected_method": calibrated_hybrid_result["selected_method"],
            "parameters": calibrated_hybrid_result["parameters"],
        },
        "uncertainty": {
            "method": "role_group_split_conformal_80",
            "nominal_coverage": NOMINAL_INTERVAL_COVERAGE,
            "outputs": ["expected_fp", "floor_fp", "ceiling_fp", "p10_fp", "p50_fp", "p90_fp"],
        },
        "prediction_protocol": (
            "For a future season, choose component configurations and iteration "
            "counts on the latest completed chronological validation season, refit "
            "on all earlier verified rows, fit calibration/interval parameters on "
            "out-of-sample historical predictions, and never inspect future targets."
        ),
        "prediction_fingerprint": final["prediction_fingerprint"],
    }


def without_frames(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in result.items()
        if key not in {
            "outer_predictions", "interval_inner_predictions",
            "outer_predictions_by_method",
        }
    }


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(type(value).__name__)


def math_close(first: float, second: float, *, tolerance: float) -> bool:
    return abs(float(first) - float(second)) <= tolerance
