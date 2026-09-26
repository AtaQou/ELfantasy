"""Leakage-safe nested chronological Phase 4A model experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import pickle
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .ml_protocol import (
    CATBOOST_PARAMETER_GRID,
    DEFAULT_RANDOM_SEED,
    LIGHTGBM_PARAMETER_GRID,
    PHASE4A_DATASET_VERSION,
    PHASE4A_PROTOCOL_VERSION,
    PRIMARY_TARGET_COLUMN,
    RIDGE_PARAMETER_GRID,
    STANDARDIZED_TARGET_COLUMN,
    XGBOOST_PARAMETER_GRID,
    feature_family,
    feature_manifest,
)


MODEL_LIBRARY_VERSIONS = {
    "ridge": "scikit-learn==1.9.0",
    "catboost": "catboost==1.2.10",
    "xgboost": "xgboost==3.3.0",
    "lightgbm": "lightgbm==4.7.0",
}


@dataclass(frozen=True, slots=True)
class FoldSelection:
    fold_id: str
    parameters: dict[str, Any]
    final_iterations: int | None
    best_inner_mae: float | None
    best_inner_rmse: float | None
    best_iteration: int | None
    training_loss: float | None
    validation_loss: float | None


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    experiment_id: str
    model_type: str
    model_library_version: str
    feature_set: str
    feature_count: int
    includes_player_id: bool
    training_target_mode: str
    random_seed: int
    outer_prediction_rows: int
    train_seconds: float
    inference_seconds: float
    mean_model_size_bytes: int
    prediction_fingerprint: str


def experiment_id(
    model_type: str,
    feature_set: str,
    *,
    seed: int,
    include_player_id: bool,
    training_target_mode: str,
) -> str:
    identity = "player" if include_player_id else "no_player"
    return (
        f"p4a__{model_type}__{feature_set.lower()}__{training_target_mode}__"
        f"{identity}__s{seed}"
    )


def parameter_grid(model_type: str) -> tuple[dict[str, Any], ...]:
    return {
        "ridge": RIDGE_PARAMETER_GRID,
        "catboost": CATBOOST_PARAMETER_GRID,
        "xgboost": XGBOOST_PARAMETER_GRID,
        "lightgbm": LIGHTGBM_PARAMETER_GRID,
    }[model_type]


def run_experiment(
    primary_frame: pd.DataFrame,
    folds: list[dict[str, Any]],
    *,
    model_type: str,
    feature_set: str,
    seed: int = DEFAULT_RANDOM_SEED,
    include_player_id: bool = True,
    training_target_mode: str = "verified_only",
    full_history_frame: pd.DataFrame | None = None,
    selection_override: dict[str, FoldSelection] | None = None,
) -> dict[str, Any]:
    """Tune on inner history, then predict each untouched outer fold once."""

    manifest = feature_manifest(feature_set, include_player_id=include_player_id)
    eid = experiment_id(
        model_type, feature_set, seed=seed,
        include_player_id=include_player_id,
        training_target_mode=training_target_mode,
    )
    predictions: list[pd.DataFrame] = []
    trials: list[dict[str, Any]] = []
    selections: dict[str, FoldSelection] = {}
    importances: list[dict[str, Any]] = []
    preprocessing: list[dict[str, Any]] = []
    total_train_seconds = total_inference_seconds = 0.0
    model_sizes: list[int] = []

    for fold in folds:
        definition = fold["definition"]
        fold_id = definition.fold_id
        inner_train = primary_frame.loc[fold["inner_train"]].copy()
        inner_validation = primary_frame.loc[fold["inner_validation"]].copy()
        outer_train = primary_frame.loc[fold["outer_train"]].copy()
        outer_test = primary_frame.loc[fold["outer_test"]].copy()

        if training_target_mode == "verified_plus_standardized":
            if full_history_frame is None:
                raise ValueError("Standardized-history experiment requires full frame")
            inner_train = _with_standardized_history(
                inner_train, full_history_frame,
                cutoff=inner_validation["target_game_time"].min(),
            )
            outer_train = _with_standardized_history(
                outer_train, full_history_frame,
                cutoff=outer_test["target_game_time"].min(),
            )

        train_target = (
            STANDARDIZED_TARGET_COLUMN
            if training_target_mode == "verified_plus_standardized"
            else PRIMARY_TARGET_COLUMN
        )
        if selection_override is None:
            selection, fold_trials = select_inner_configuration(
                model_type=model_type,
                manifest=manifest,
                train=inner_train,
                validation=inner_validation,
                train_target=train_target,
                seed=seed,
                fold_id=fold_id,
                experiment_id_value=eid,
            )
            trials.extend(fold_trials)
        else:
            selection = selection_override[fold_id]
        selections[fold_id] = selection

        model, state, train_seconds = fit_final_model(
            model_type=model_type,
            manifest=manifest,
            train=outer_train,
            train_target=train_target,
            parameters=selection.parameters,
            final_iterations=selection.final_iterations,
            seed=seed,
        )
        total_train_seconds += train_seconds
        start = perf_counter()
        predicted = predict_model(model_type, model, outer_test, manifest)
        inference_seconds = perf_counter() - start
        total_inference_seconds += inference_seconds
        if len(predicted) != len(outer_test):
            raise ValueError("Final prediction count differs from outer test")
        model_sizes.append(approximate_model_size(model_type, model))
        importances.extend(
            extract_feature_importance(
                model_type, model, manifest, fold_id=fold_id, experiment_id_value=eid
            )
        )
        preprocessing.append({
            "experiment_id": eid,
            "outer_fold": fold_id,
            "preprocessor_fit_rows": len(outer_train),
            "preprocessor_fit_max_time": outer_train["target_game_time"].max().isoformat(),
            "outer_test_min_time": outer_test["target_game_time"].min().isoformat(),
            "state_fingerprint": state,
            "outer_test_rows_seen_during_fit": 0,
        })
        fold_prediction = outer_test[_prediction_context_columns(outer_test)].copy()
        fold_prediction["experiment_id"] = eid
        fold_prediction["model_type"] = model_type
        fold_prediction["feature_set"] = feature_set
        fold_prediction["training_target_mode"] = training_target_mode
        fold_prediction["random_seed"] = seed
        fold_prediction["outer_fold"] = fold_id
        fold_prediction["predicted_fp"] = np.asarray(predicted, dtype=float)
        fold_prediction["actual_fp"] = outer_test[PRIMARY_TARGET_COLUMN].to_numpy(float)
        fold_prediction["absolute_error"] = np.abs(
            fold_prediction["actual_fp"] - fold_prediction["predicted_fp"]
        )
        fold_prediction["residual"] = (
            fold_prediction["actual_fp"] - fold_prediction["predicted_fp"]
        )
        fold_prediction["dataset_version"] = PHASE4A_DATASET_VERSION
        fold_prediction["protocol_version"] = PHASE4A_PROTOCOL_VERSION
        predictions.append(fold_prediction)

    prediction_frame = pd.concat(predictions, ignore_index=True)
    if prediction_frame["model_row_id"].duplicated().any():
        raise ValueError("Duplicate outer prediction row")
    fingerprint = prediction_fingerprint(prediction_frame)
    summary = ExperimentSummary(
        experiment_id=eid,
        model_type=model_type,
        model_library_version=MODEL_LIBRARY_VERSIONS[model_type],
        feature_set=feature_set,
        feature_count=manifest["feature_count"],
        includes_player_id=include_player_id,
        training_target_mode=training_target_mode,
        random_seed=seed,
        outer_prediction_rows=len(prediction_frame),
        train_seconds=total_train_seconds,
        inference_seconds=total_inference_seconds,
        mean_model_size_bytes=int(np.mean(model_sizes)),
        prediction_fingerprint=fingerprint,
    )
    return {
        "summary": asdict(summary),
        "predictions": prediction_frame,
        "trials": trials,
        "selections": selections,
        "importance": importances,
        "preprocessing": preprocessing,
    }


def select_inner_configuration(
    *,
    model_type: str,
    manifest: dict[str, Any],
    train: pd.DataFrame,
    validation: pd.DataFrame,
    train_target: str,
    seed: int,
    fold_id: str,
    experiment_id_value: str,
) -> tuple[FoldSelection, list[dict[str, Any]]]:
    if train["target_game_time"].max() >= validation["target_game_time"].min():
        raise ValueError("Inner tuning boundary is not chronological")
    records: list[dict[str, Any]] = []
    candidates: list[tuple[float, int, dict[str, Any], dict[str, Any]]] = []
    for index, parameters in enumerate(parameter_grid(model_type)):
        model, details, elapsed = fit_inner_model(
            model_type=model_type,
            manifest=manifest,
            train=train,
            validation=validation,
            train_target=train_target,
            parameters=parameters,
            seed=seed,
        )
        predicted = predict_model(model_type, model, validation, manifest)
        metrics = regression_metrics(
            validation[PRIMARY_TARGET_COLUMN].to_numpy(float), predicted
        )
        record = {
            "experiment_id": experiment_id_value,
            "outer_fold": fold_id,
            "trial_index": index,
            "model_type": model_type,
            "parameters": json.dumps(parameters, sort_keys=True),
            "inner_train_rows": len(train),
            "inner_validation_rows": len(validation),
            "inner_train_max_time": train["target_game_time"].max().isoformat(),
            "inner_validation_min_time": (
                validation["target_game_time"].min().isoformat()
            ),
            "inner_mae": metrics["mae"],
            "inner_rmse": metrics["rmse"],
            "best_iteration": details["best_iteration"],
            "training_loss": details["training_loss"],
            "validation_loss": details["validation_loss"],
            "fit_seconds": elapsed,
            "outer_test_rows_seen": 0,
        }
        records.append(record)
        candidates.append((metrics["mae"], index, parameters, details))
    _, selected_index, parameters, details = min(
        candidates, key=lambda row: (row[0], row[1])
    )
    selected_metrics = records[selected_index]
    selection = FoldSelection(
        fold_id=fold_id,
        parameters=parameters,
        final_iterations=details["final_iterations"],
        best_inner_mae=selected_metrics["inner_mae"],
        best_inner_rmse=selected_metrics["inner_rmse"],
        best_iteration=details["best_iteration"],
        training_loss=details["training_loss"],
        validation_loss=details["validation_loss"],
    )
    return selection, records


def fit_inner_model(
    *, model_type: str, manifest: dict[str, Any], train: pd.DataFrame,
    validation: pd.DataFrame, train_target: str,
    parameters: dict[str, Any], seed: int,
) -> tuple[Any, dict[str, Any], float]:
    start = perf_counter()
    y_train = train[train_target].to_numpy(float)
    y_validation = validation[PRIMARY_TARGET_COLUMN].to_numpy(float)
    if model_type == "ridge":
        model = build_sklearn_pipeline(model_type, manifest, parameters, seed)
        model.fit(_raw_feature_frame(train, manifest), y_train)
        train_pred = model.predict(_raw_feature_frame(train, manifest))
        val_pred = model.predict(_raw_feature_frame(validation, manifest))
        details = {
            "best_iteration": None, "final_iterations": None,
            "training_loss": float(np.mean(np.abs(y_train - train_pred))),
            "validation_loss": float(np.mean(np.abs(y_validation - val_pred))),
        }
    elif model_type == "catboost":
        from catboost import CatBoostRegressor

        x_train = _catboost_feature_frame(train, manifest)
        x_validation = _catboost_feature_frame(validation, manifest)
        model = CatBoostRegressor(
            **parameters, iterations=1000, eval_metric="MAE",
            random_seed=seed, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4,
            has_time=True,
        )
        model.fit(
            x_train, y_train, cat_features=manifest["categorical"],
            eval_set=(x_validation, y_validation), use_best_model=True,
            early_stopping_rounds=60, verbose=False,
        )
        best = max(0, int(model.get_best_iteration()))
        scores = model.get_evals_result()
        details = {
            "best_iteration": best,
            "final_iterations": best + 1,
            "training_loss": _eval_result_value(scores, "learn", "MAE", best),
            "validation_loss": _eval_result_value(scores, "validation", "MAE", best),
        }
    else:
        model = build_sklearn_pipeline(
            model_type, manifest, parameters, seed, early_stopping=True
        )
        preprocessor = model.named_steps["preprocessor"]
        estimator = model.named_steps["model"]
        x_train = preprocessor.fit_transform(_raw_feature_frame(train, manifest))
        x_validation = preprocessor.transform(
            _raw_feature_frame(validation, manifest)
        )
        if model_type == "xgboost":
            estimator.fit(
                x_train, y_train,
                eval_set=[(x_train, y_train), (x_validation, y_validation)],
                verbose=False,
            )
            best = int(estimator.best_iteration)
            history = estimator.evals_result()
            details = {
                "best_iteration": best,
                "final_iterations": best + 1,
                "training_loss": float(history["validation_0"]["mae"][best]),
                "validation_loss": float(history["validation_1"]["mae"][best]),
            }
        else:
            import lightgbm as lgb

            estimator.fit(
                x_train, y_train,
                eval_X=(x_train, x_validation), eval_y=(y_train, y_validation),
                eval_metric="mae",
                callbacks=[lgb.early_stopping(60, verbose=False)],
            )
            best = int(estimator.best_iteration_) - 1
            history = estimator.evals_result_
            details = {
                "best_iteration": best,
                "final_iterations": best + 1,
                "training_loss": float(history["training"]["l1"][best]),
                "validation_loss": float(history["valid_1"]["l1"][best]),
            }
    return model, details, perf_counter() - start


def fit_final_model(
    *, model_type: str, manifest: dict[str, Any], train: pd.DataFrame,
    train_target: str, parameters: dict[str, Any], final_iterations: int | None,
    seed: int,
) -> tuple[Any, str, float]:
    start = perf_counter()
    y_train = train[train_target].to_numpy(float)
    if model_type == "catboost":
        from catboost import CatBoostRegressor

        model = CatBoostRegressor(
            **parameters, iterations=int(final_iterations or 300),
            eval_metric="MAE", random_seed=seed, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4,
            has_time=True,
        )
        x_train = _catboost_feature_frame(train, manifest)
        model.fit(
            x_train, y_train, cat_features=manifest["categorical"], verbose=False
        )
        state = _catboost_training_state(train, manifest)
    else:
        fixed = dict(parameters)
        if model_type in {"xgboost", "lightgbm"}:
            fixed["n_estimators"] = int(final_iterations or 300)
        model = build_sklearn_pipeline(model_type, manifest, fixed, seed)
        raw = _raw_feature_frame(train, manifest)
        model.fit(raw, y_train)
        state = preprocessing_state_fingerprint(
            model.named_steps["preprocessor"], raw
        )
    return model, state, perf_counter() - start


def build_sklearn_pipeline(
    model_type: str,
    manifest: dict[str, Any],
    parameters: dict[str, Any],
    seed: int,
    *,
    early_stopping: bool = False,
) -> Pipeline:
    numeric_steps: list[tuple[str, Any]] = [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True))
    ]
    if model_type == "ridge":
        numeric_steps.append(("scaler", StandardScaler()))
    categorical = Pipeline([
        ("imputer", SimpleImputer(
            strategy="constant", fill_value="__MISSING__", keep_empty_features=True
        )),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=True)),
    ])
    preprocessor = ColumnTransformer(
        [
            ("numeric", Pipeline(numeric_steps), manifest["numeric"]),
            ("categorical", categorical, manifest["categorical"]),
        ],
        remainder="drop",
        sparse_threshold=0.3,
    )
    if model_type == "ridge":
        estimator = Ridge(**parameters)
    elif model_type == "xgboost":
        from xgboost import XGBRegressor

        model_parameters = dict(parameters)
        n_estimators = int(model_parameters.pop("n_estimators", 1200))
        estimator = XGBRegressor(
            **model_parameters, n_estimators=n_estimators,
            objective="reg:squarederror", eval_metric="mae",
            tree_method="hist", random_state=seed, n_jobs=4,
            early_stopping_rounds=60 if early_stopping else None,
        )
    elif model_type == "lightgbm":
        from lightgbm import LGBMRegressor

        model_parameters = dict(parameters)
        n_estimators = int(model_parameters.pop("n_estimators", 1200))
        estimator = LGBMRegressor(
            **model_parameters, n_estimators=n_estimators,
            objective="regression_l1", random_state=seed, n_jobs=4,
            deterministic=True, force_col_wise=True, verbosity=-1,
        )
    else:
        raise KeyError(model_type)
    return Pipeline([("preprocessor", preprocessor), ("model", estimator)])


def predict_model(
    model_type: str, model: Any, frame: pd.DataFrame,
    manifest: dict[str, Any],
) -> np.ndarray:
    if model_type == "catboost":
        return np.asarray(model.predict(_catboost_feature_frame(frame, manifest)))
    return np.asarray(model.predict(_raw_feature_frame(frame, manifest)))


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | None]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = actual - predicted

    def _correlation(method: str) -> float | None:
        if len(actual) < 2 or np.std(actual) == 0.0 or np.std(predicted) == 0.0:
            return None
        value = pd.Series(actual).corr(pd.Series(predicted), method=method)
        return float(value) if pd.notna(value) and np.isfinite(value) else None

    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error * error))),
        "spearman": _correlation("spearman"),
        "pearson": _correlation("pearson"),
        "mean_actual": float(np.mean(actual)),
        "mean_prediction": float(np.mean(predicted)),
        "mean_residual": float(np.mean(error)),
    }


def ranking_metrics(predictions: pd.DataFrame) -> dict[str, Any]:
    mapped = predictions.dropna(subset=["fantasy_matchday"]).copy()
    captured_10 = available_10 = captured_20 = available_20 = 0
    slate_spearman: list[float] = []
    groups = 0
    for _, group in mapped.groupby(["season", "fantasy_matchday"], sort=True):
        if len(group) < 10:
            continue
        groups += 1
        actual10 = set(group.nlargest(min(10, len(group)), "actual_fp")["model_row_id"])
        predicted20 = set(group.nlargest(min(20, len(group)), "predicted_fp")["model_row_id"])
        actual20 = set(group.nlargest(min(20, len(group)), "actual_fp")["model_row_id"])
        predicted30 = set(group.nlargest(min(30, len(group)), "predicted_fp")["model_row_id"])
        captured_10 += len(actual10 & predicted20)
        available_10 += len(actual10)
        captured_20 += len(actual20 & predicted30)
        available_20 += len(actual20)
        value = group["actual_fp"].corr(group["predicted_fp"], method="spearman")
        if pd.notna(value):
            slate_spearman.append(float(value))
    return {
        "mapped_rows": len(mapped),
        "slates": groups,
        "top10_actual_in_predicted_top20": captured_10 / available_10,
        "top20_actual_in_predicted_top30": captured_20 / available_20,
        "mean_within_slate_spearman": float(np.mean(slate_spearman)),
    }


def experiment_metrics(predictions: pd.DataFrame) -> dict[str, Any]:
    overall = regression_metrics(
        predictions["actual_fp"].to_numpy(), predictions["predicted_fp"].to_numpy()
    )
    overall.update(ranking_metrics(predictions))
    overall["rows"] = len(predictions)
    return overall


def prediction_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    ordered = frame.sort_values(["outer_fold", "model_row_id"])
    for row in ordered[["outer_fold", "model_row_id", "predicted_fp"]].itertuples(
        index=False, name=None
    ):
        digest.update(
            f"{row[0]}|{row[1]}|{float(row[2]):.10f}\n".encode()
        )
    return digest.hexdigest()


def preprocessing_state_fingerprint(
    preprocessor: ColumnTransformer, raw_train: pd.DataFrame
) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(raw_train.columns).encode())
    numeric = preprocessor.named_transformers_["numeric"]
    imputer = numeric.named_steps["imputer"]
    digest.update(np.asarray(imputer.statistics_, dtype=float).tobytes())
    if "scaler" in numeric.named_steps:
        scaler = numeric.named_steps["scaler"]
        digest.update(np.asarray(scaler.mean_, dtype=float).tobytes())
        digest.update(np.asarray(scaler.scale_, dtype=float).tobytes())
    encoder = preprocessor.named_transformers_["categorical"].named_steps["onehot"]
    for categories in encoder.categories_:
        digest.update("\x1f".join(map(str, categories)).encode())
    return digest.hexdigest()


def approximate_model_size(model_type: str, model: Any) -> int:
    if model_type == "xgboost":
        return len(model.named_steps["model"].get_booster().save_raw())
    if model_type == "lightgbm":
        return len(
            model.named_steps["model"].booster_.model_to_string().encode()
        )
    return len(pickle.dumps(model, protocol=5))


def extract_feature_importance(
    model_type: str, model: Any, manifest: dict[str, Any], *,
    fold_id: str, experiment_id_value: str,
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
        raw_name = _raw_importance_name(name, manifest)
        rows.append({
            "experiment_id": experiment_id_value,
            "outer_fold": fold_id,
            "transformed_feature": name,
            "raw_feature": raw_name,
            "feature_family": feature_family(raw_name),
            "importance": float(value),
            "normalized_importance": float(value / total) if total else 0.0,
            "importance_type": (
                "catboost_prediction_values_change"
                if model_type == "catboost" else
                "absolute_standardized_coefficient"
                if model_type == "ridge" else "split_importance"
            ),
        })
    return rows


def _raw_importance_name(name: str, manifest: dict[str, Any]) -> str:
    candidate = name.split("__", maxsplit=1)[-1]
    for feature in sorted(manifest["features"], key=len, reverse=True):
        if candidate == feature or candidate.startswith(feature + "_"):
            return feature
    return candidate


def _raw_feature_frame(frame: pd.DataFrame, manifest: dict[str, Any]) -> pd.DataFrame:
    values: dict[str, pd.Series] = {}
    for column in manifest["numeric"]:
        values[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    for column in manifest["categorical"]:
        category = frame[column].astype("string").astype(object)
        values[column] = category.where(pd.notna(category), np.nan)
    result = pd.DataFrame(values, index=frame.index)
    return result[manifest["features"]]


def _catboost_feature_frame(
    frame: pd.DataFrame, manifest: dict[str, Any]
) -> pd.DataFrame:
    result = _raw_feature_frame(frame, manifest)
    for column in manifest["categorical"]:
        result[column] = result[column].where(
            pd.notna(result[column]), "__MISSING__"
        ).astype(str)
    return result


def _catboost_training_state(
    train: pd.DataFrame, manifest: dict[str, Any]
) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(manifest["features"]).encode())
    for column in manifest["categorical"]:
        values = sorted(
            _catboost_feature_frame(train, manifest)[column].unique().tolist()
        )
        digest.update((column + "\x1f" + "\x1f".join(values)).encode())
    return digest.hexdigest()


def _with_standardized_history(
    verified: pd.DataFrame, full: pd.DataFrame, *, cutoff: pd.Timestamp
) -> pd.DataFrame:
    older = full[
        (full["season_start_year"] <= 2021)
        & (full["target_game_time"] < cutoff)
        & full[STANDARDIZED_TARGET_COLUMN].notna()
    ].copy()
    combined = pd.concat([older, verified], ignore_index=True)
    combined = combined.drop_duplicates("model_row_id", keep="last")
    combined = combined.sort_values(
        ["target_game_time", "game_id", "player_id"]
    ).reset_index(drop=True)
    if combined["target_game_time"].max() >= cutoff:
        raise ValueError("Standardized history crosses validation/test cutoff")
    if combined[STANDARDIZED_TARGET_COLUMN].isna().any():
        raise ValueError("Missing standardized training target")
    return combined


def _prediction_context_columns(frame: pd.DataFrame) -> list[str]:
    desired = [
        "model_row_id", "season", "round_number", "game_id", "player_id",
        "team_id", "opponent_team_id", "home_away", "target_game_time",
        "feature_cutoff_time", "target_rule_version", "target_rule_status",
        "feature_pipeline_version", "rich_feature_pipeline_version",
        "fantasy_matchday", "fantasy_position", "target_minutes",
        "career_el_games_before", "season_games_before", "last_5_minutes_avg",
        "rot_role_stability", "onoff_last5_on_possessions_n",
        "opp_season_drtg_before", "prediction_season_recent_blend",
    ]
    return [column for column in desired if column in frame]


def _eval_result_value(
    scores: dict[str, dict[str, list[float]]], group: str,
    metric: str, index: int,
) -> float | None:
    values = scores.get(group, {}).get(metric)
    if not values:
        return None
    return float(values[min(index, len(values) - 1)])
