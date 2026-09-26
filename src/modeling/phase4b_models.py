"""Bounded leakage-safe component models used by the Phase 4B runner."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from .ml_experiments import (
    _catboost_feature_frame,
    _raw_feature_frame,
    build_sklearn_pipeline,
    regression_metrics,
)
from .ml_protocol import feature_manifest
from .phase4b_protocol import PHASE4B_RANDOM_SEED, phase4b_feature_manifest


@dataclass(frozen=True, slots=True)
class ComponentCandidate:
    experiment_id: str
    task: str
    model_type: str
    manifest_name: str
    target_strategy: str = "minutes"
    use_older_history: bool = False


MINUTES_CANDIDATES = (
    ComponentCandidate(
        "p4b__minutes__ridge__role", "minutes", "ridge", "MINUTES_ROLE"
    ),
    ComponentCandidate(
        "p4b__minutes__catboost__role", "minutes", "catboost", "MINUTES_ROLE"
    ),
    ComponentCandidate(
        "p4b__minutes__xgboost__role", "minutes", "xgboost", "MINUTES_ROLE"
    ),
    ComponentCandidate(
        "p4b__minutes__catboost__role_stage", "minutes", "catboost",
        "MINUTES_ROLE_STAGE",
    ),
    ComponentCandidate(
        "p4b__minutes__catboost__role_older_history", "minutes", "catboost",
        "MINUTES_ROLE", use_older_history=True,
    ),
)

PRODUCTION_CANDIDATES = (
    ComponentCandidate(
        "p4b__production__ridge__raw", "production", "ridge", "PRODUCTION",
        "raw",
    ),
    ComponentCandidate(
        "p4b__production__xgboost__raw", "production", "xgboost", "PRODUCTION",
        "raw",
    ),
    ComponentCandidate(
        "p4b__production__catboost__raw", "production", "catboost", "PRODUCTION",
        "raw",
    ),
    ComponentCandidate(
        "p4b__production__catboost__weighted", "production", "catboost",
        "PRODUCTION", "weighted",
    ),
    ComponentCandidate(
        "p4b__production__catboost__stabilized", "production", "catboost",
        "PRODUCTION", "stabilized",
    ),
    ComponentCandidate(
        "p4b__production__catboost__weighted_shot", "production", "catboost",
        "PRODUCTION_SHOT", "weighted",
    ),
    ComponentCandidate(
        "p4b__production__catboost__weighted_stage", "production", "catboost",
        "PRODUCTION_STAGE", "weighted",
    ),
    ComponentCandidate(
        "p4b__production__catboost__weighted_older_history", "production",
        "catboost", "PRODUCTION", "weighted", use_older_history=True,
    ),
)


def component_parameter_grid(candidate: ComponentCandidate) -> tuple[dict[str, Any], ...]:
    if candidate.model_type == "ridge":
        return ({"alpha": 10.0}, {"alpha": 100.0})
    if candidate.model_type == "catboost":
        base = (
            {
                "depth": 5, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
                "loss_function": "MAE",
            },
            {
                "depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
                "loss_function": "MAE",
            },
        )
        if candidate.target_strategy == "stabilized":
            return tuple({**row, "stabilization_minutes": k} for row in base for k in (5.0, 10.0))
        return base
    if candidate.model_type == "xgboost":
        return (
            {
                "max_depth": 3, "learning_rate": 0.04,
                "min_child_weight": 8.0, "subsample": 0.85,
                "colsample_bytree": 0.85, "reg_lambda": 10.0,
                "reg_alpha": 0.2,
            },
            {
                "max_depth": 4, "learning_rate": 0.035,
                "min_child_weight": 10.0, "subsample": 0.85,
                "colsample_bytree": 0.80, "reg_lambda": 12.0,
                "reg_alpha": 0.5,
            },
        )
    raise KeyError(candidate.model_type)


def run_component_candidate(
    primary: pd.DataFrame,
    full_history: pd.DataFrame,
    folds: list[dict[str, Any]],
    candidate: ComponentCandidate,
    *,
    inner_minutes: dict[str, pd.DataFrame] | None = None,
    outer_minutes: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Tune a component in inner history and predict every untouched outer fold."""

    manifest = phase4b_feature_manifest(candidate.manifest_name)
    trials: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    inner_predictions: dict[str, pd.DataFrame] = {}
    outer_predictions: list[pd.DataFrame] = []
    importance: list[dict[str, Any]] = []
    preprocessing: list[dict[str, Any]] = []
    total_seconds = 0.0

    for fold in folds:
        fold_id = fold["definition"].fold_id
        inner_train = primary.loc[fold["inner_train"]].copy()
        validation = primary.loc[fold["inner_validation"]].copy()
        outer_train = primary.loc[fold["outer_train"]].copy()
        outer_test = primary.loc[fold["outer_test"]].copy()
        if candidate.use_older_history:
            inner_train = add_older_history(
                inner_train, full_history,
                cutoff=validation["target_game_time"].min(),
            )
            outer_train = add_older_history(
                outer_train, full_history,
                cutoff=outer_test["target_game_time"].min(),
            )
        minute_validation = None
        if candidate.task == "production":
            if inner_minutes is None or outer_minutes is None:
                raise ValueError("Production candidate requires predicted minutes")
            minute_validation = align_prediction(
                validation, inner_minutes[fold_id], "predicted_minutes"
            )

        fold_trials: list[dict[str, Any]] = []
        fitted: list[tuple[float, int, dict[str, Any], dict[str, Any], np.ndarray]] = []
        for trial_index, parameters in enumerate(component_parameter_grid(candidate)):
            target = training_target(
                inner_train, candidate.task, candidate.target_strategy, parameters
            )
            validation_target = validation_target_values(validation, candidate.task)
            model_parameters = {
                key: value for key, value in parameters.items()
                if key != "stabilization_minutes"
            }
            model, details, elapsed = fit_inner_component(
                candidate.model_type, manifest, inner_train, validation,
                target["values"], validation_target,
                model_parameters, target["weights"], seed=PHASE4B_RANDOM_SEED,
            )
            total_seconds += elapsed
            predicted = predict_component(candidate.model_type, model, validation, manifest)
            metrics = regression_metrics(validation_target, predicted)
            if candidate.task == "production":
                decomposed = minute_validation * predicted
                selection_score = float(np.mean(np.abs(
                    validation["actual_fantasy_points"].to_numpy(float) - decomposed
                )))
            else:
                selection_score = float(metrics["mae"])
            record = {
                "experiment_id": candidate.experiment_id,
                "outer_fold": fold_id,
                "trial_index": trial_index,
                "task": candidate.task,
                "model_type": candidate.model_type,
                "manifest_name": candidate.manifest_name,
                "target_strategy": candidate.target_strategy,
                "parameters": json.dumps(parameters, sort_keys=True),
                "inner_train_rows": len(inner_train),
                "inner_validation_rows": len(validation),
                "inner_train_max_time": inner_train["target_game_time"].max().isoformat(),
                "inner_validation_min_time": validation["target_game_time"].min().isoformat(),
                "selection_metric": (
                    "decomposed_fp_mae" if candidate.task == "production" else "minutes_mae"
                ),
                "selection_score": selection_score,
                "target_mae": metrics["mae"],
                "target_rmse": metrics["rmse"],
                "best_iteration": details["best_iteration"],
                "final_iterations": details["final_iterations"],
                "fit_seconds": elapsed,
                "outer_test_rows_seen": 0,
                "stabilization_prior_rate": target["prior_rate"],
            }
            fold_trials.append(record)
            fitted.append((selection_score, trial_index, parameters, details, predicted))
        trials.extend(fold_trials)
        _, selected_index, selected_parameters, selected_details, inner_pred = min(
            fitted, key=lambda row: (row[0], row[1])
        )
        selected_trial = fold_trials[selected_index]
        selections.append({
            **selected_trial,
            "selected": True,
        })
        inner_prediction = component_prediction_frame(
            validation, inner_pred, candidate, fold_id,
            prediction_role="inner_validation",
        )
        if candidate.task == "production":
            inner_prediction["predicted_minutes"] = minute_validation
            inner_prediction["predicted_fp"] = minute_validation * inner_pred
            inner_prediction["actual_fp"] = validation[
                "actual_fantasy_points"
            ].to_numpy(float)
        inner_predictions[fold_id] = inner_prediction

        final_target = training_target(
            outer_train, candidate.task, candidate.target_strategy, selected_parameters
        )
        final_parameters = {
            key: value for key, value in selected_parameters.items()
            if key != "stabilization_minutes"
        }
        model, state, elapsed = fit_final_component(
            candidate.model_type, manifest, outer_train, final_target["values"],
            final_parameters, selected_details["final_iterations"],
            final_target["weights"], seed=PHASE4B_RANDOM_SEED,
        )
        total_seconds += elapsed
        outer_pred = predict_component(candidate.model_type, model, outer_test, manifest)
        prediction = component_prediction_frame(
            outer_test, outer_pred, candidate, fold_id, prediction_role="outer_test"
        )
        if candidate.task == "production":
            predicted_minutes = align_prediction(
                outer_test,
                outer_minutes[outer_minutes["outer_fold"] == fold_id],
                "predicted_minutes",
            )
            prediction["predicted_minutes"] = predicted_minutes
            prediction["predicted_fp"] = predicted_minutes * outer_pred
            prediction["actual_fp"] = outer_test["actual_fantasy_points"].to_numpy(float)
        outer_predictions.append(prediction)
        importance.extend(extract_importance(
            model, candidate.model_type, manifest, candidate.experiment_id, fold_id
        ))
        preprocessing.append({
            "experiment_id": candidate.experiment_id,
            "outer_fold": fold_id,
            "preprocessor_fit_rows": len(outer_train),
            "preprocessor_fit_max_time": outer_train["target_game_time"].max().isoformat(),
            "outer_test_min_time": outer_test["target_game_time"].min().isoformat(),
            "outer_test_rows_seen_during_fit": 0,
            "state_fingerprint": state,
            "uses_actual_minutes_as_feature": False,
            "uses_actual_fp_per_min_as_feature": False,
            "downstream_predicted_minutes_feature": False,
            "older_history_rows": int((outer_train["season_start_year"] <= 2021).sum()),
            "stabilization_prior_rate": final_target["prior_rate"],
        })

    outer = pd.concat(outer_predictions, ignore_index=True)
    return {
        "candidate": candidate,
        "manifest": manifest,
        "trials": trials,
        "selections": selections,
        "inner_predictions": inner_predictions,
        "outer_predictions": outer,
        "importance": importance,
        "preprocessing": preprocessing,
        "train_seconds": total_seconds,
        "inner_selection_score": float(np.average(
            [row["selection_score"] for row in selections],
            weights=[row["inner_validation_rows"] for row in selections],
        )),
        "prediction_fingerprint": component_prediction_fingerprint(outer),
    }


def training_target(
    frame: pd.DataFrame,
    task: str,
    strategy: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    minutes = pd.to_numeric(frame["target_minutes"], errors="coerce").to_numpy(float)
    if not np.all(np.isfinite(minutes)) or np.any(minutes <= 0.0):
        raise ValueError("FP/min targets require finite positive played minutes")
    if task == "minutes":
        return {"values": minutes, "weights": None, "prior_rate": None}
    fp = pd.to_numeric(frame["actual_fantasy_points"], errors="coerce")
    fallback = pd.to_numeric(frame["standardized_fantasy_points"], errors="coerce")
    values = fp.fillna(fallback).to_numpy(float)
    if not np.all(np.isfinite(values)):
        raise ValueError("Production training target is missing")
    raw_rate = values / minutes
    prior = float(np.sum(values) / np.sum(minutes))
    weights = None
    if strategy == "weighted":
        weights = minutes / float(np.mean(minutes))
        target = raw_rate
    elif strategy == "stabilized":
        k = float(parameters["stabilization_minutes"])
        target = (values + k * prior) / (minutes + k)
    elif strategy == "raw":
        target = raw_rate
    else:
        raise KeyError(strategy)
    return {"values": target, "weights": weights, "prior_rate": prior}


def validation_target_values(frame: pd.DataFrame, task: str) -> np.ndarray:
    if task == "minutes":
        return frame["target_minutes"].to_numpy(float)
    minutes = frame["target_minutes"].to_numpy(float)
    if np.any(minutes <= 0.0):
        raise ValueError("Validation FP/min denominator is non-positive")
    return frame["actual_fantasy_points"].to_numpy(float) / minutes


def add_older_history(
    verified: pd.DataFrame,
    full_history: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    older = full_history[
        (full_history["season_start_year"] <= 2021)
        & (full_history["target_game_time"] < cutoff)
        & full_history["standardized_fantasy_points"].notna()
        & (pd.to_numeric(full_history["target_minutes"], errors="coerce") > 0)
    ].copy()
    combined = pd.concat([older, verified], ignore_index=True)
    combined = combined.drop_duplicates("model_row_id", keep="last")
    combined = combined.sort_values(
        ["target_game_time", "game_id", "player_id"]
    ).reset_index(drop=True)
    if combined["target_game_time"].max() >= cutoff:
        raise ValueError("Older-history extension crosses future boundary")
    return combined


def fit_inner_component(
    model_type: str,
    manifest: dict[str, Any],
    train: pd.DataFrame,
    validation: pd.DataFrame,
    y_train: np.ndarray,
    y_validation: np.ndarray,
    parameters: dict[str, Any],
    weights: np.ndarray | None,
    *,
    seed: int,
) -> tuple[Any, dict[str, Any], float]:
    start = perf_counter()
    if model_type == "ridge":
        model = build_sklearn_pipeline("ridge", manifest, parameters, seed)
        fit_kwargs = {"model__sample_weight": weights} if weights is not None else {}
        model.fit(_raw_feature_frame(train, manifest), y_train, **fit_kwargs)
        predicted = model.predict(_raw_feature_frame(validation, manifest))
        details = {
            "best_iteration": None, "final_iterations": None,
            "validation_mae": float(np.mean(np.abs(y_validation - predicted))),
        }
    elif model_type == "catboost":
        from catboost import CatBoostRegressor, Pool

        model = CatBoostRegressor(
            **parameters, iterations=700, eval_metric="MAE", random_seed=seed,
            random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
        )
        train_pool = Pool(
            _catboost_feature_frame(train, manifest), y_train,
            cat_features=manifest["categorical"], weight=weights,
        )
        validation_pool = Pool(
            _catboost_feature_frame(validation, manifest), y_validation,
            cat_features=manifest["categorical"],
        )
        model.fit(
            train_pool, eval_set=validation_pool, use_best_model=True,
            early_stopping_rounds=50, verbose=False,
        )
        best = max(0, int(model.get_best_iteration()))
        details = {
            "best_iteration": best, "final_iterations": best + 1,
            "validation_mae": float(model.get_best_score()["validation"]["MAE"]),
        }
    elif model_type == "xgboost":
        model = build_sklearn_pipeline(
            "xgboost", manifest, parameters, seed, early_stopping=True
        )
        preprocessor = model.named_steps["preprocessor"]
        estimator = model.named_steps["model"]
        x_train = preprocessor.fit_transform(_raw_feature_frame(train, manifest))
        x_validation = preprocessor.transform(_raw_feature_frame(validation, manifest))
        estimator.fit(
            x_train, y_train,
            eval_set=[(x_train, y_train), (x_validation, y_validation)],
            sample_weight=weights, verbose=False,
        )
        best = int(estimator.best_iteration)
        details = {
            "best_iteration": best, "final_iterations": best + 1,
            "validation_mae": float(estimator.evals_result()["validation_1"]["mae"][best]),
        }
    else:
        raise KeyError(model_type)
    return model, details, perf_counter() - start


def fit_final_component(
    model_type: str,
    manifest: dict[str, Any],
    train: pd.DataFrame,
    y_train: np.ndarray,
    parameters: dict[str, Any],
    final_iterations: int | None,
    weights: np.ndarray | None,
    *,
    seed: int,
) -> tuple[Any, str, float]:
    start = perf_counter()
    if model_type == "catboost":
        from catboost import CatBoostRegressor, Pool

        model = CatBoostRegressor(
            **parameters, iterations=int(final_iterations or 250),
            eval_metric="MAE", random_seed=seed, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
        )
        pool = Pool(
            _catboost_feature_frame(train, manifest), y_train,
            cat_features=manifest["categorical"], weight=weights,
        )
        model.fit(pool, verbose=False)
        state = training_state_fingerprint(train, manifest)
    else:
        fixed = dict(parameters)
        if model_type == "xgboost":
            fixed["n_estimators"] = int(final_iterations or 250)
        model = build_sklearn_pipeline(model_type, manifest, fixed, seed)
        fit_kwargs = {"model__sample_weight": weights} if weights is not None else {}
        raw = _raw_feature_frame(train, manifest)
        model.fit(raw, y_train, **fit_kwargs)
        state = training_state_fingerprint(train, manifest)
    return model, state, perf_counter() - start


def predict_component(
    model_type: str,
    model: Any,
    frame: pd.DataFrame,
    manifest: dict[str, Any],
) -> np.ndarray:
    if model_type == "catboost":
        return np.asarray(
            model.predict(_catboost_feature_frame(frame, manifest)), dtype=float
        )
    return np.asarray(model.predict(_raw_feature_frame(frame, manifest)), dtype=float)


def component_prediction_frame(
    frame: pd.DataFrame,
    predicted: np.ndarray,
    candidate: ComponentCandidate,
    fold_id: str,
    *,
    prediction_role: str,
) -> pd.DataFrame:
    columns = [
        "model_row_id", "season", "round_number", "game_id", "player_id",
        "team_id", "opponent_team_id", "target_game_time", "fantasy_matchday",
        "fantasy_position", "target_minutes", "actual_fantasy_points",
        "career_el_games_before", "competition_stage", "postseason_game",
        "role_change_score",
    ]
    result = frame[[column for column in columns if column in frame]].copy()
    result["experiment_id"] = candidate.experiment_id
    result["model_version"] = candidate.experiment_id
    result["outer_fold"] = fold_id
    result["prediction_role"] = prediction_role
    result["model_type"] = candidate.model_type
    result["feature_manifest"] = candidate.manifest_name
    result["target_strategy"] = candidate.target_strategy
    if candidate.task == "minutes":
        result["predicted_minutes"] = np.asarray(predicted, dtype=float)
        result["actual_minutes"] = frame["target_minutes"].to_numpy(float)
    else:
        result["predicted_fp_per_min"] = np.asarray(predicted, dtype=float)
        result["actual_fp_per_min"] = (
            frame["actual_fantasy_points"].to_numpy(float)
            / frame["target_minutes"].to_numpy(float)
        )
    return result


def align_prediction(
    frame: pd.DataFrame, predictions: pd.DataFrame, column: str
) -> np.ndarray:
    lookup = predictions.set_index("model_row_id")[column]
    values = frame["model_row_id"].map(lookup)
    if values.isna().any():
        raise ValueError(f"Missing chained {column} predictions")
    return values.to_numpy(float)


def extract_importance(
    model: Any,
    model_type: str,
    manifest: dict[str, Any],
    experiment_id: str,
    fold_id: str,
) -> list[dict[str, Any]]:
    if model_type == "catboost":
        names = list(manifest["features"])
        values = np.asarray(model.get_feature_importance(), dtype=float)
    else:
        preprocessor = model.named_steps["preprocessor"]
        names = [str(value) for value in preprocessor.get_feature_names_out()]
        estimator = model.named_steps["model"]
        if model_type == "ridge":
            values = np.abs(np.asarray(estimator.coef_, dtype=float))
        else:
            values = np.asarray(estimator.feature_importances_, dtype=float)
    total = float(np.sum(values))
    rows = []
    for name, value in zip(names, values, strict=True):
        raw = raw_importance_name(name, manifest)
        rows.append({
            "experiment_id": experiment_id, "outer_fold": fold_id,
            "transformed_feature": name, "raw_feature": raw,
            "importance": float(value),
            "normalized_importance": float(value / total) if total else 0.0,
        })
    return rows


def raw_importance_name(name: str, manifest: dict[str, Any]) -> str:
    candidate = name.split("__", maxsplit=1)[-1]
    for feature in sorted(manifest["features"], key=len, reverse=True):
        if candidate == feature or candidate.startswith(feature + "_"):
            return feature
    return candidate


def training_state_fingerprint(frame: pd.DataFrame, manifest: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(manifest["features"]).encode())
    digest.update("\n".join(frame["model_row_id"].astype(str)).encode())
    return digest.hexdigest()


def component_prediction_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    value_column = (
        "predicted_minutes" if "predicted_minutes" in frame
        and "predicted_fp_per_min" not in frame else "predicted_fp_per_min"
    )
    for row in frame.sort_values(["outer_fold", "model_row_id"])[
        ["outer_fold", "model_row_id", value_column]
    ].itertuples(index=False, name=None):
        digest.update(f"{row[0]}|{row[1]}|{float(row[2]):.10f}\n".encode())
    return digest.hexdigest()


def fit_direct_loss_predictions(
    primary: pd.DataFrame,
    folds: list[dict[str, Any]],
    *,
    loss_function: str,
    include_outer: bool,
) -> dict[str, Any]:
    """Fit the strongest direct architecture with one controlled objective."""

    from catboost import CatBoostRegressor

    manifest = feature_manifest("CORE_ROTATION", include_player_id=True)
    inner: dict[str, pd.DataFrame] = {}
    outer_rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for fold in folds:
        fold_id = fold["definition"].fold_id
        train = primary.loc[fold["inner_train"]].copy()
        validation = primary.loc[fold["inner_validation"]].copy()
        model = CatBoostRegressor(
            depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
            loss_function=loss_function, iterations=1000, eval_metric="MAE",
            random_seed=PHASE4B_RANDOM_SEED, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
        )
        model.fit(
            _catboost_feature_frame(train, manifest),
            train["actual_fantasy_points"].to_numpy(float),
            cat_features=manifest["categorical"],
            eval_set=(
                _catboost_feature_frame(validation, manifest),
                validation["actual_fantasy_points"].to_numpy(float),
            ),
            use_best_model=True, early_stopping_rounds=60, verbose=False,
        )
        best = max(0, int(model.get_best_iteration()))
        predicted = np.asarray(
            model.predict(_catboost_feature_frame(validation, manifest)), dtype=float
        )
        inner[fold_id] = direct_prediction_frame(
            validation, predicted, fold_id, loss_function, "inner_validation"
        )
        audit = {
            "experiment_id": direct_loss_experiment_id(loss_function),
            "outer_fold": fold_id, "loss_function": loss_function,
            "inner_train_rows": len(train), "inner_validation_rows": len(validation),
            "inner_mae": float(np.mean(np.abs(
                validation["actual_fantasy_points"].to_numpy(float) - predicted
            ))),
            "final_iterations": best + 1, "outer_test_rows_seen": 0,
            "inner_train_max_time": train["target_game_time"].max().isoformat(),
            "inner_validation_min_time": validation["target_game_time"].min().isoformat(),
        }
        audits.append(audit)
        if include_outer:
            outer_train = primary.loc[fold["outer_train"]].copy()
            outer_test = primary.loc[fold["outer_test"]].copy()
            final_model = CatBoostRegressor(
                depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
                loss_function=loss_function, iterations=best + 1,
                eval_metric="MAE", random_seed=PHASE4B_RANDOM_SEED,
                random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
                allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
            )
            final_model.fit(
                _catboost_feature_frame(outer_train, manifest),
                outer_train["actual_fantasy_points"].to_numpy(float),
                cat_features=manifest["categorical"], verbose=False,
            )
            outer_pred = np.asarray(
                final_model.predict(_catboost_feature_frame(outer_test, manifest)),
                dtype=float,
            )
            outer_rows.append(direct_prediction_frame(
                outer_test, outer_pred, fold_id, loss_function, "outer_test"
            ))
    return {
        "inner_predictions": inner,
        "outer_predictions": (
            pd.concat(outer_rows, ignore_index=True) if outer_rows else pd.DataFrame()
        ),
        "audit": audits,
        "inner_mae": float(np.average(
            [row["inner_mae"] for row in audits],
            weights=[row["inner_validation_rows"] for row in audits],
        )),
    }


def direct_loss_experiment_id(loss_function: str) -> str:
    label = loss_function.lower().replace(":", "_").replace("=", "_").replace(".", "_")
    return f"p4b__direct__catboost_core_rotation__{label}"


def direct_prediction_frame(
    frame: pd.DataFrame,
    predicted: np.ndarray,
    fold_id: str,
    loss_function: str,
    prediction_role: str,
) -> pd.DataFrame:
    columns = [
        "model_row_id", "season", "round_number", "game_id", "player_id",
        "team_id", "opponent_team_id", "target_game_time", "fantasy_matchday",
        "fantasy_position", "target_minutes", "actual_fantasy_points",
        "career_el_games_before", "competition_stage", "postseason_game",
        "role_change_score",
    ]
    result = frame[[column for column in columns if column in frame]].copy()
    result["experiment_id"] = direct_loss_experiment_id(loss_function)
    result["model_version"] = result["experiment_id"]
    result["outer_fold"] = fold_id
    result["prediction_role"] = prediction_role
    result["predicted_fp"] = np.asarray(predicted, dtype=float)
    result["actual_fp"] = frame["actual_fantasy_points"].to_numpy(float)
    result["loss_function"] = loss_function
    return result
