"""Orchestration, caching, and persistence for the frozen Phase 4A matrix."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .ml_experiments import (
    FoldSelection,
    experiment_metrics,
    run_experiment,
)
from .ml_protocol import (
    DEFAULT_RANDOM_SEED,
    FEATURE_MANIFEST_ADDITIONS,
    PHASE4A_DATASET_VERSION,
    PHASE4A_PROTOCOL_VERSION,
    STABILITY_SEEDS,
    build_outer_fold_indices,
    feature_manifest,
    load_phase4a_frame,
    protocol_payload,
    write_protocol_json,
)


PHASE4A_DERIVED_ROOT = Path("data/derived/phase4a")
PHASE4A_SAMPLE_ROOT = Path("data/samples/phase4a")


def run_phase4a_experiments(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = PHASE4A_DERIVED_ROOT,
    sample_root: Path = PHASE4A_SAMPLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Run the predeclared experiment matrix; never branch on outer scores."""

    derived_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    write_protocol_json(sample_root / "protocol.json")
    primary = load_phase4a_frame(database_path, primary_only=True)
    full = load_phase4a_frame(database_path, primary_only=False)
    folds = build_outer_fold_indices(primary)
    _write_fold_manifest(primary, folds, sample_root / "outer_folds.json")

    results: dict[str, dict[str, Any]] = {}
    selections: dict[str, dict[str, FoldSelection]] = {}

    # Predeclared first pass. All-Rich is tuned first; its fold-wise settings are
    # reused for Core so the controlled comparison changes only the feature set.
    for model_type in ("ridge", "catboost", "xgboost", "lightgbm"):
        all_rich = _run_or_load(
            primary, folds, model_type=model_type, feature_set="CORE_ALL_RICH",
            derived_root=derived_root, sample_root=sample_root, force=force,
        )
        results[all_rich["summary"]["experiment_id"]] = all_rich
        all_selections = all_rich["selections"]
        selections[model_type] = all_selections
        core = _run_or_load(
            primary, folds, model_type=model_type, feature_set="CORE",
            derived_root=derived_root, sample_root=sample_root, force=force,
            selection_override=all_selections,
        )
        results[core["summary"]["experiment_id"]] = core

    # Detailed CatBoost ablations were fixed in the protocol before outer scores.
    for feature_set in FEATURE_MANIFEST_ADDITIONS:
        if feature_set in {"CORE", "CORE_ALL_RICH"}:
            continue
        result = _run_or_load(
            primary, folds, model_type="catboost", feature_set=feature_set,
            derived_root=derived_root, sample_root=sample_root, force=force,
            selection_override=selections["catboost"],
        )
        results[result["summary"]["experiment_id"]] = result

    no_player = _run_or_load(
        primary, folds, model_type="catboost", feature_set="CORE_ALL_RICH",
        include_player_id=False, derived_root=derived_root,
        sample_root=sample_root, force=force,
        selection_override=selections["catboost"],
    )
    results[no_player["summary"]["experiment_id"]] = no_player

    standardized = _run_or_load(
        primary, folds, model_type="catboost", feature_set="CORE_ALL_RICH",
        training_target_mode="verified_plus_standardized", full_history_frame=full,
        derived_root=derived_root, sample_root=sample_root, force=force,
        selection_override=selections["catboost"],
    )
    results[standardized["summary"]["experiment_id"]] = standardized

    for model_type in ("catboost", "xgboost"):
        for seed in STABILITY_SEEDS:
            if seed == DEFAULT_RANDOM_SEED:
                continue
            result = _run_or_load(
                primary, folds, model_type=model_type,
                feature_set="CORE_ALL_RICH", seed=seed,
                derived_root=derived_root, sample_root=sample_root, force=force,
                selection_override=selections[model_type],
            )
            results[result["summary"]["experiment_id"]] = result

    combined = _materialize_results(database_path, results, derived_root)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol_version": PHASE4A_PROTOCOL_VERSION,
        "dataset_version": PHASE4A_DATASET_VERSION,
        "experiment_count": len(results),
        "prediction_rows": len(combined["predictions"]),
        "trial_rows": len(combined["trials"]),
        "importance_rows": len(combined["importance"]),
        "preprocessing_audit_rows": len(combined["preprocessing"]),
        "experiments": [
            {
                **_enrich_summary(result["summary"]),
                "metrics": experiment_metrics(result["predictions"]),
            }
            for result in results.values()
        ],
        "foundation_model": {
            "status": "NOT_EVALUATED_RESOURCE_CONSTRAINT",
            "candidate": "TabICLv2 2.1.1 / TabPFN 8.1.0",
            "reason": (
                "Local host is Intel CPU-only with 8 GB RAM; current foundation "
                "models recommend GPU for this 7k-21k-row, 106-222-feature fold "
                "scale. Hosted inference was not authorized and no data were uploaded."
            ),
        },
    }
    (sample_root / "experiment_results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _run_or_load(
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    *,
    model_type: str,
    feature_set: str,
    derived_root: Path,
    sample_root: Path,
    force: bool,
    seed: int = DEFAULT_RANDOM_SEED,
    include_player_id: bool = True,
    training_target_mode: str = "verified_only",
    full_history_frame: pd.DataFrame | None = None,
    selection_override: dict[str, FoldSelection] | None = None,
) -> dict[str, Any]:
    from .ml_experiments import experiment_id

    eid = experiment_id(
        model_type, feature_set, seed=seed,
        include_player_id=include_player_id,
        training_target_mode=training_target_mode,
    )
    cache_root = derived_root / "experiments" / eid
    if not force and (cache_root / "complete.json").exists():
        return _load_cached_result(cache_root)
    print(f"RUN {eid}", flush=True)
    result = run_experiment(
        primary, folds, model_type=model_type, feature_set=feature_set,
        seed=seed, include_player_id=include_player_id,
        training_target_mode=training_target_mode,
        full_history_frame=full_history_frame,
        selection_override=selection_override,
    )
    _save_cached_result(cache_root, result)
    print(
        f"DONE {eid}: MAE={experiment_metrics(result['predictions'])['mae']:.5f}",
        flush=True,
    )
    return result


def _save_cached_result(root: Path, result: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_parquet(result["predictions"], root / "predictions.parquet")
    _write_parquet(pd.DataFrame(result["trials"]), root / "trials.parquet")
    _write_parquet(pd.DataFrame(result["importance"]), root / "importance.parquet")
    _write_parquet(
        pd.DataFrame(result["preprocessing"]), root / "preprocessing.parquet"
    )
    payload = {
        "summary": result["summary"],
        "selections": {
            fold_id: asdict(selection)
            for fold_id, selection in result["selections"].items()
        },
        "protocol_version": PHASE4A_PROTOCOL_VERSION,
        "dataset_version": PHASE4A_DATASET_VERSION,
    }
    (root / "complete.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _load_cached_result(root: Path) -> dict[str, Any]:
    payload = json.loads((root / "complete.json").read_text(encoding="utf-8"))
    if payload["protocol_version"] != PHASE4A_PROTOCOL_VERSION:
        raise ValueError("Cached experiment protocol version mismatch")
    if payload["dataset_version"] != PHASE4A_DATASET_VERSION:
        raise ValueError("Cached experiment dataset version mismatch")
    connection = duckdb.connect()
    try:
        predictions = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(root / "predictions.parquet")]
        ).df()
        trials = _read_optional_parquet(connection, root / "trials.parquet")
        importance = _read_optional_parquet(connection, root / "importance.parquet")
        preprocessing = _read_optional_parquet(
            connection, root / "preprocessing.parquet"
        )
    finally:
        connection.close()
    return {
        "summary": _enrich_summary(payload["summary"]),
        "predictions": predictions,
        "trials": trials.to_dict("records"),
        "importance": importance.to_dict("records"),
        "preprocessing": preprocessing.to_dict("records"),
        "selections": {
            fold_id: FoldSelection(**selection)
            for fold_id, selection in payload["selections"].items()
        },
    }


def _materialize_results(
    database_path: Path | str,
    results: dict[str, dict[str, Any]],
    derived_root: Path,
) -> dict[str, pd.DataFrame]:
    frames = {
        "predictions": pd.concat(
            [result["predictions"] for result in results.values()], ignore_index=True
        ),
        "trials": _concat_records(results, "trials"),
        "importance": _concat_records(results, "importance"),
        "preprocessing": _concat_records(results, "preprocessing"),
        "experiments": pd.DataFrame(
            [_enrich_summary(result["summary"]) for result in results.values()]
        ),
    }
    table_names = {
        "predictions": "ml_phase4a_outer_predictions_v1",
        "trials": "ml_phase4a_inner_trials_v1",
        "importance": "ml_phase4a_feature_importance_v1",
        "preprocessing": "ml_phase4a_preprocessing_audit_v1",
        "experiments": "ml_phase4a_experiments_v1",
    }
    with connect_database(database_path) as connection:
        for key, frame in frames.items():
            if frame.empty:
                continue
            registered = f"_{key}_phase4a"
            connection.register(registered, frame)
            connection.execute(
                f"CREATE OR REPLACE TABLE {table_names[key]} AS "
                f"SELECT * FROM {registered}"
            )
            connection.unregister(registered)
            _write_parquet(frame, derived_root / f"{table_names[key]}.parquet")
        connection.execute(
            "CREATE OR REPLACE VIEW ml_phase4a_outer_predictions AS "
            "SELECT * FROM ml_phase4a_outer_predictions_v1; "
            "CREATE OR REPLACE VIEW ml_phase4a_experiments AS "
            "SELECT * FROM ml_phase4a_experiments_v1;"
        )
    return frames


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        # DuckDB cannot infer a useful schema from an entirely columnless frame.
        frame = pd.DataFrame({"empty": pd.Series(dtype="int64")})
    connection = duckdb.connect()
    try:
        connection.register("_phase4a_frame", frame)
        connection.execute(
            "COPY _phase4a_frame TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(path)],
        )
        connection.unregister("_phase4a_frame")
    finally:
        connection.close()


def _read_optional_parquet(connection: Any, path: Path) -> pd.DataFrame:
    frame = connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    return frame.drop(columns=["empty"], errors="ignore")


def _concat_records(results: dict[str, dict[str, Any]], key: str) -> pd.DataFrame:
    records = [record for result in results.values() for record in result[key]]
    return pd.DataFrame(records)


def _write_fold_manifest(
    frame: pd.DataFrame, folds: list[dict[str, Any]], path: Path
) -> None:
    rows = []
    for fold in folds:
        definition = fold["definition"]
        row: dict[str, Any] = {
            "fold_id": definition.fold_id,
            "outer_test_season": definition.outer_test_season,
            "inner_validation_policy": definition.inner_validation_policy,
        }
        for role in ("inner_train", "inner_validation", "outer_train", "outer_test"):
            subset = frame.loc[fold[role]]
            row[f"{role}_rows"] = len(subset)
            row[f"{role}_games"] = int(subset["game_id"].nunique())
            row[f"{role}_min_time"] = subset["target_game_time"].min().isoformat()
            row[f"{role}_max_time"] = subset["target_game_time"].max().isoformat()
        rows.append(row)
    path.write_text(
        json.dumps({
            "protocol": protocol_payload(),
            "folds": rows,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _enrich_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result.update({
        "dataset_version": PHASE4A_DATASET_VERSION,
        "protocol_version": PHASE4A_PROTOCOL_VERSION,
        "feature_manifest_sha256": feature_manifest(
            result["feature_set"],
            include_player_id=bool(result["includes_player_id"]),
        )["sha256"],
        "feature_pipeline_version": "phase3a_core_boxscore_v1",
        "rich_feature_pipeline_version": "phase3b_rich_v1",
        "target_rule_version": "ELFC_PLAYER_V1_STANDARDIZED_2025",
        "evaluation_target_status": "HISTORICALLY_VERIFIED_E2023_E2025",
    })
    return result
