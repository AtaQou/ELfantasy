"""Chronological Phase 5B baseline-role, redistribution, and Fantasy impact runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import pickle
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.modeling.ml_experiments import (
    _catboost_feature_frame,
    _raw_feature_frame,
    build_sklearn_pipeline,
    ranking_metrics,
    regression_metrics,
)
from src.modeling.ml_runner import _write_parquet
from src.modeling.phase4b_runner import read_parquet

from .phase5b_dataset import (
    DEFAULT_DERIVED_ROOT,
    AbsenceDatasetSummary,
    build_candidate_features,
    construct_absence_dataset,
    fallback_baseline_minutes,
    persist_absence_dataset,
)
from .phase5b_protocol import (
    DEFAULT_RECONCILIATION,
    FINAL_PHASE4B_MODEL_VERSION,
    MEANINGFUL_GAIN_MINUTES,
    PHASE5B_PROTOCOL_VERSION,
    PHASE5B_RANDOM_SEED,
    REGULATION_TEAM_MINUTES,
    baseline_manifest,
    capped_simplex_projection,
    redistribution_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase5b"


@dataclass(frozen=True, slots=True)
class RedistributionCandidate:
    experiment_id: str
    model_type: str
    include_rotation: bool = False
    remove_quality: bool = False


REDISTRIBUTION_CANDIDATES = (
    RedistributionCandidate("p5b__ridge__core", "ridge"),
    RedistributionCandidate("p5b__catboost__core", "catboost"),
    RedistributionCandidate("p5b__xgboost__core", "xgboost"),
    RedistributionCandidate(
        "p5b__catboost__core_rotation_recent", "catboost", include_rotation=True
    ),
    RedistributionCandidate(
        "p5b__catboost__context_only_ablation", "catboost", remove_quality=True
    ),
)


def redistribution_parameter_grid(model_type: str) -> tuple[dict[str, Any], ...]:
    if model_type == "ridge":
        return ({"alpha": 10.0}, {"alpha": 100.0})
    if model_type == "catboost":
        return (
            {"depth": 5, "learning_rate": 0.04, "l2_leaf_reg": 8.0},
            {"depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0},
        )
    if model_type == "xgboost":
        return (
            {
                "max_depth": 3, "learning_rate": 0.04, "n_estimators": 260,
                "min_child_weight": 8.0, "subsample": 0.85,
                "colsample_bytree": 0.85, "reg_lambda": 10.0,
                "reg_alpha": 0.2,
            },
            {
                "max_depth": 4, "learning_rate": 0.035, "n_estimators": 300,
                "min_child_weight": 10.0, "subsample": 0.85,
                "colsample_bytree": 0.80, "reg_lambda": 12.0,
                "reg_alpha": 0.5,
            },
        )
    raise KeyError(model_type)


def run_phase5b(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Build all Phase 5B artifacts without changing frozen Phase 4B records."""

    initialize_database(database_path)
    derived_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    result_path = sample_root / "results.json"
    required = (
        derived_root / "ml_phase5b_redistribution_predictions_v1.parquet",
        derived_root / "ml_phase5b_fantasy_impact_v1.parquet",
        sample_root / "final_redistribution.json",
    )
    if not force and result_path.is_file() and all(path.is_file() for path in required):
        return json.loads(result_path.read_text(encoding="utf-8"))

    started = perf_counter()
    candidate_cache = derived_root / "phase5b_candidate_features.parquet"
    if not force and candidate_cache.is_file():
        candidates = read_parquet(candidate_cache)
        for column in (
            "target_game_time", "feature_cutoff_time", "player_history_max_game_time",
            "rotation_history_max_time",
        ):
            if column in candidates:
                candidates[column] = pd.to_datetime(candidates[column], utc=True)
    else:
        candidates = build_candidate_features(database_path)
        _write_parquet(candidates, candidate_cache)
    candidates, baseline_audit = generate_walk_forward_baseline_predictions(candidates)
    normalized, events, dataset_summary = construct_absence_dataset(candidates)
    persist_absence_dataset(
        candidates, normalized, events, dataset_summary,
        database_path=database_path, derived_root=derived_root,
    )
    model_results, model_predictions, trials = run_redistribution_models(events)
    baseline_predictions = run_naive_baselines(events)
    predictions = pd.concat([baseline_predictions, model_predictions], ignore_index=True)
    comparisons = summarize_prediction_methods(predictions)
    selected_method, freeze_reason = select_redistribution_method(comparisons)
    selected_predictions = predictions[
        predictions["experiment_id"].eq(selected_method)
    ].copy()
    fantasy, fantasy_summary = evaluate_fantasy_impact(
        selected_predictions, database_path
    )
    final_model = freeze_final_redistribution_model(
        events, model_results, selected_method, derived_root,
        dataset_summary, freeze_reason,
    )
    cross_position = cross_position_analysis(selected_predictions)
    summary = {
        "protocol_version": PHASE5B_PROTOCOL_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "conditional_interpretation": "CONDITIONAL_ON_KNOWN_ABSENCE_SET",
        "historical_rows_are_injuries": False,
        "dataset": asdict(dataset_summary),
        "baseline_role": baseline_audit,
        "method_comparison": comparisons,
        "model_trials": trials,
        "selected_method": selected_method,
        "freeze_reason": freeze_reason,
        "final_model": final_model,
        "selected_diagnostics": method_diagnostics(selected_predictions),
        "cross_position": cross_position,
        "fantasy_impact": fantasy_summary,
        "runtime_seconds": perf_counter() - started,
        "frozen_phase4b_model_unchanged": True,
    }
    persist_experiment_artifacts(
        predictions, fantasy, pd.DataFrame(trials), baseline_audit, summary,
        database_path=database_path, derived_root=derived_root,
        sample_root=sample_root,
    )
    return summary


def generate_walk_forward_baseline_predictions(
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fixed-config conditional minutes fits; selection sees prior seasons only."""

    frame = candidates.copy()
    frame["fallback_baseline_minutes"] = fallback_baseline_minutes(frame)
    frame["baseline_minutes_core"] = np.nan
    frame["baseline_minutes_rotation"] = np.nan
    frame["baseline_minutes_raw"] = np.nan
    frame["baseline_method"] = "FALLBACK"
    frame["baseline_training_max_time"] = pd.Series(
        pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]"
    )
    seasons = sorted(frame["season"].unique())
    audits: list[dict[str, Any]] = []
    season_scores: dict[str, dict[str, float]] = {}
    for season in seasons:
        target = frame["season"].eq(season)
        train = frame[(frame["season"] < season) & frame["eligibility_status"].eq("PLAYED")]
        if train.empty:
            frame.loc[target, "baseline_minutes_raw"] = frame.loc[
                target, "fallback_baseline_minutes"
            ]
            audits.append({
                "season": season, "selected": "FALLBACK", "train_rows": 0,
                "training_max_time": None, "selection_season": None,
                "outer_target_rows_seen": 0,
            })
            continue
        core = _fit_baseline_model(train, baseline_manifest(False))
        core_prediction = _predict_model(
            "catboost", core, frame.loc[target], baseline_manifest(False)
        )
        frame.loc[target, "baseline_minutes_core"] = core_prediction
        rich_prediction: np.ndarray | None = None
        rich_train = train[train["season"].ge("E2021") & train["rot_games_before"].gt(0)]
        if len(rich_train) >= 1000:
            rich = _fit_baseline_model(rich_train, baseline_manifest(True))
            rich_prediction = _predict_model(
                "catboost", rich, frame.loc[target], baseline_manifest(True)
            )
            frame.loc[target, "baseline_minutes_rotation"] = rich_prediction
        selection_season = f"E{int(season[1:]) - 1}"
        previous = season_scores.get(selection_season, {})
        selected = "CORE"
        if rich_prediction is not None and previous.get("rotation_mae", np.inf) < previous.get(
            "core_mae", np.inf
        ):
            selected = "CORE_ROTATION"
        prediction = rich_prediction if selected == "CORE_ROTATION" else core_prediction
        frame.loc[target, "baseline_minutes_raw"] = np.clip(prediction, 0.0, 40.0)
        frame.loc[target, "baseline_method"] = selected
        training_max = train["target_game_time"].max()
        frame.loc[target, "baseline_training_max_time"] = training_max
        played_target = target & frame["eligibility_status"].eq("PLAYED")
        actual = frame.loc[played_target, "actual_minutes"].to_numpy(float)
        core_mae = float(np.mean(np.abs(
            actual - frame.loc[played_target, "baseline_minutes_core"].to_numpy(float)
        )))
        rotation_mae = (
            float(np.mean(np.abs(
                actual - frame.loc[played_target, "baseline_minutes_rotation"].to_numpy(float)
            ))) if rich_prediction is not None else np.nan
        )
        season_scores[season] = {"core_mae": core_mae, "rotation_mae": rotation_mae}
        audits.append({
            "season": season, "selected": selected, "train_rows": len(train),
            "rich_train_rows": len(rich_train),
            "training_max_time": training_max.isoformat(),
            "selection_season": selection_season,
            "selection_core_mae": previous.get("core_mae"),
            "selection_rotation_mae": previous.get("rotation_mae"),
            "played_core_mae_diagnostic": core_mae,
            "played_rotation_mae_diagnostic": rotation_mae,
            "outer_target_rows_seen": 0,
        })
    frame["baseline_minutes_raw"] = pd.to_numeric(
        frame["baseline_minutes_raw"], errors="coerce"
    ).fillna(frame["fallback_baseline_minutes"])
    team_size = frame.groupby(["game_id", "team_id"])["player_id"].transform("count")
    frame["baseline_minutes_raw"] = frame["baseline_minutes_raw"].fillna(200 / team_size)
    crossing = frame["baseline_training_max_time"].notna() & (
        pd.to_datetime(frame["baseline_training_max_time"], utc=True)
        >= frame["target_game_time"]
    )
    if crossing.any():
        raise ValueError("Baseline role model used target/future rows")
    return frame, {
        "strategy": (
            "fixed Phase4B-style CatBoost conditional minutes; longer Core versus "
            "E2021+ Core+Rotation selected only from the immediately prior season; "
            "weighted lagged-role fallback for the first season/missing score"
        ),
        "fixed_parameters": {
            "depth": 6, "learning_rate": 0.04, "l2_leaf_reg": 8.0,
            "iterations": 220, "loss_function": "MAE", "seed": PHASE5B_RANDOM_SEED,
        },
        "season_audit": audits,
    }


def _fit_baseline_model(train: pd.DataFrame, manifest: dict[str, Any]) -> Any:
    from catboost import CatBoostRegressor, Pool

    model = CatBoostRegressor(
        depth=6, learning_rate=0.04, l2_leaf_reg=8.0,
        loss_function="MAE", iterations=220, random_seed=PHASE5B_RANDOM_SEED,
        random_strength=1.0, bootstrap_type="Bernoulli", subsample=0.85,
        allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
    )
    model.fit(Pool(
        _catboost_feature_frame(train, manifest),
        train["actual_minutes"].to_numpy(float),
        cat_features=manifest["categorical"],
    ), verbose=False)
    return model


def run_naive_baselines(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for experiment in (
        "p5b__baseline__no_adjustment",
        "p5b__baseline__equal_split",
        "p5b__baseline__proportional",
        "p5b__baseline__position_split",
    ):
        predicted = naive_adjusted_minutes(events, experiment)
        output = _prediction_frame(events, predicted, experiment, "baseline", "OUTER_TEST")
        rows.append(output[output["season"].isin(["E2023", "E2024", "E2025"])])
    return pd.concat(rows, ignore_index=True)


def naive_adjusted_minutes(events: pd.DataFrame, experiment_id: str) -> np.ndarray:
    output = pd.Series(index=events.index, dtype=float)
    for _, group in events.groupby(["game_id", "team_id"], sort=False):
        baseline = group["baseline_expected_minutes"].to_numpy(float)
        missing = float(group["missing_expected_minutes"].iloc[0])
        if experiment_id.endswith("no_adjustment"):
            adjusted = baseline
        elif experiment_id.endswith("equal_split"):
            adjusted = capped_simplex_projection(baseline + missing / len(group))
        elif experiment_id.endswith("proportional"):
            adjusted = capped_simplex_projection(
                baseline * (REGULATION_TEAM_MINUTES / baseline.sum())
            )
        elif experiment_id.endswith("position_split"):
            raw = baseline.copy()
            for position, column in (
                ("GUARD", "missing_guard_minutes"),
                ("FORWARD", "missing_forward_minutes"),
                ("CENTER", "missing_center_minutes"),
            ):
                amount = float(group[column].iloc[0])
                mask = group["broad_position"].eq(position).to_numpy()
                if amount <= 0:
                    continue
                if mask.any() and baseline[mask].sum() > 0:
                    raw[mask] += amount * baseline[mask] / baseline[mask].sum()
                else:
                    raw += amount * baseline / baseline.sum()
            allocated = float(raw.sum() - baseline.sum())
            if allocated < missing:
                raw += (missing - allocated) * baseline / baseline.sum()
            adjusted = capped_simplex_projection(raw)
        else:
            raise KeyError(experiment_id)
        output.loc[group.index] = adjusted
    return output.to_numpy(float)


def run_redistribution_models(
    events: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    predictions = []
    trials: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for candidate in REDISTRIBUTION_CANDIDATES:
        manifest = redistribution_manifest(candidate.include_rotation)
        if candidate.remove_quality:
            manifest = remove_recipient_quality(manifest)
        candidate_predictions: list[pd.DataFrame] = []
        selections = []
        for target_season in ("E2023", "E2024", "E2025"):
            validation_season = f"E{int(target_season[1:]) - 1}"
            outer_train = events[events["season"] < target_season].copy()
            outer_test = events[events["season"] == target_season].copy()
            inner_train = outer_train[outer_train["season"] < validation_season].copy()
            validation = outer_train[outer_train["season"] == validation_season].copy()
            if candidate.include_rotation:
                outer_train = outer_train[outer_train["season"].ge("E2021")]
                inner_train = inner_train[inner_train["season"].ge("E2021")]
            fitted = []
            for trial_index, parameters in enumerate(redistribution_parameter_grid(candidate.model_type)):
                model = _fit_redistribution(
                    candidate.model_type, manifest, inner_train, parameters
                )
                raw = _predict_model(candidate.model_type, model, validation, manifest)
                adjusted = reconcile_model_deltas(validation, raw)
                score = float(np.mean(np.abs(
                    validation["actual_regulation_minutes"].to_numpy(float) - adjusted
                )))
                record = {
                    "experiment_id": candidate.experiment_id,
                    "outer_fold": f"outer_{target_season.lower()}",
                    "trial_index": trial_index,
                    "parameters": json.dumps(parameters, sort_keys=True),
                    "inner_train_rows": len(inner_train),
                    "inner_validation_rows": len(validation),
                    "inner_train_max_time": inner_train["target_game_time"].max().isoformat(),
                    "inner_validation_min_time": validation["target_game_time"].min().isoformat(),
                    "selection_metric": "reconciled_minutes_mae",
                    "selection_score": score,
                    "outer_test_rows_seen": 0,
                }
                trials.append(record)
                fitted.append((score, trial_index, parameters))
            _, selected_index, selected_parameters = min(fitted, key=lambda item: (item[0], item[1]))
            final_model = _fit_redistribution(
                candidate.model_type, manifest, outer_train, selected_parameters
            )
            raw_outer = _predict_model(
                candidate.model_type, final_model, outer_test, manifest
            )
            adjusted_outer = reconcile_model_deltas(outer_test, raw_outer)
            prediction = _prediction_frame(
                outer_test, adjusted_outer, candidate.experiment_id,
                candidate.model_type, f"outer_{target_season.lower()}",
            )
            prediction["raw_predicted_delta"] = raw_outer
            candidate_predictions.append(prediction)
            selections.append({
                **trials[-len(fitted) + selected_index], "selected": True,
                "outer_train_rows": len(outer_train), "outer_test_rows": len(outer_test),
            })
        combined = pd.concat(candidate_predictions, ignore_index=True)
        predictions.append(combined)
        results[candidate.experiment_id] = {
            "candidate": asdict(candidate), "manifest": manifest,
            "selections": selections, "metrics": prediction_metrics(combined),
        }
    return results, pd.concat(predictions, ignore_index=True), trials


def _fit_redistribution(
    model_type: str,
    manifest: dict[str, Any],
    train: pd.DataFrame,
    parameters: dict[str, Any],
) -> Any:
    target = train["minutes_delta"].to_numpy(float)
    weights = 1.0 / train.groupby(["game_id", "team_id"])["player_id"].transform("count")
    if model_type == "catboost":
        from catboost import CatBoostRegressor, Pool

        model = CatBoostRegressor(
            **parameters, loss_function="MAE", iterations=320,
            random_seed=PHASE5B_RANDOM_SEED, random_strength=1.0,
            bootstrap_type="Bernoulli", subsample=0.85,
            allow_writing_files=False, verbose=False, thread_count=4, has_time=True,
        )
        model.fit(Pool(
            _catboost_feature_frame(train, manifest), target,
            cat_features=manifest["categorical"], weight=weights.to_numpy(float),
        ), verbose=False)
        return model
    model = build_sklearn_pipeline(model_type, manifest, parameters, PHASE5B_RANDOM_SEED)
    model.fit(
        _raw_feature_frame(train, manifest), target,
        model__sample_weight=weights.to_numpy(float),
    )
    return model


def _predict_model(
    model_type: str,
    model: Any,
    frame: pd.DataFrame,
    manifest: dict[str, Any],
) -> np.ndarray:
    if model_type == "catboost":
        return np.asarray(model.predict(_catboost_feature_frame(frame, manifest)), dtype=float)
    return np.asarray(model.predict(_raw_feature_frame(frame, manifest)), dtype=float)


def reconcile_model_deltas(events: pd.DataFrame, predicted_delta: np.ndarray) -> np.ndarray:
    if len(events) != len(predicted_delta):
        raise ValueError("Predicted delta length does not match recipient rows")
    raw = events["baseline_expected_minutes"].to_numpy(float) + np.asarray(
        predicted_delta, dtype=float
    )
    output = pd.Series(index=events.index, dtype=float)
    raw_series = pd.Series(raw, index=events.index)
    for _, group in events.groupby(["game_id", "team_id"], sort=False):
        output.loc[group.index] = capped_simplex_projection(
            raw_series.loc[group.index].to_numpy(float)
        )
    return output.to_numpy(float)


def remove_recipient_quality(manifest: dict[str, Any]) -> dict[str, Any]:
    prefixes = (
        "last_", "season_fp", "fp_ewma", "points_per_min", "assists_per_min",
        "rebounds_per_min", "turnovers_per_min", "rot_", "historical_role_rank",
        "historical_shared", "prior_absence_response",
    )
    numeric = [
        feature for feature in manifest["numeric"]
        if not feature.startswith(prefixes)
    ]
    categorical = list(manifest["categorical"])
    features = [*numeric, *categorical]
    return {
        "name": "REDISTRIBUTION_CONTEXT_ONLY_ABLATION",
        "numeric": numeric, "categorical": categorical, "features": features,
        "feature_count": len(features),
        "sha256": hashlib.sha256("\n".join(features).encode()).hexdigest(),
        "includes_player_id": False, "includes_fantasy_credits": False,
    }


def _prediction_frame(
    events: pd.DataFrame,
    adjusted: np.ndarray,
    experiment_id: str,
    model_type: str,
    outer_fold: str,
) -> pd.DataFrame:
    columns = [
        "model_row_id", "season", "round_number", "game_id", "player_id",
        "team_id", "opponent_team_id", "home_away", "target_game_time",
        "actual_minutes", "actual_regulation_minutes", "baseline_expected_minutes",
        "minutes_delta", "number_players_out", "missing_expected_minutes",
        "number_rotation_players_out", "dominant_missing_position",
        "broad_position", "recipient_matches_missing_position",
        "historical_role_rank", "actual_started", "actual_fantasy_points",
        "absent_player_ids", "absent_positions",
    ]
    result = events[columns].copy()
    result["experiment_id"] = experiment_id
    result["model_type"] = model_type
    result["outer_fold"] = outer_fold
    result["predicted_adjusted_minutes"] = np.asarray(adjusted, dtype=float)
    result["predicted_minutes_delta"] = (
        result["predicted_adjusted_minutes"] - result["baseline_expected_minutes"]
    )
    result["absolute_error"] = (
        result["actual_regulation_minutes"] - result["predicted_adjusted_minutes"]
    ).abs()
    return result


def prediction_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    actual = frame["actual_regulation_minutes"].to_numpy(float)
    predicted = frame["predicted_adjusted_minutes"].to_numpy(float)
    base = regression_metrics(actual, predicted)
    base.update(recipient_ranking_metrics(frame))
    conservation = frame.groupby(["game_id", "team_id"])[
        "predicted_adjusted_minutes"
    ].sum()
    base["team_conservation_mae"] = float(np.mean(np.abs(
        conservation.to_numpy(float) - REGULATION_TEAM_MINUTES
    )))
    base["team_sum_absolute_error"] = float(
        frame.groupby(["game_id", "team_id"])["absolute_error"].sum().mean()
    )
    return base


def recipient_ranking_metrics(frame: pd.DataFrame) -> dict[str, float]:
    top1: list[float] = []
    top2: list[float] = []
    correlations: list[float] = []
    for _, group in frame.groupby(["game_id", "team_id"], sort=False):
        actual = group.sort_values(
            ["minutes_delta", "player_id"], ascending=[False, True]
        )
        predicted = group.sort_values(
            ["predicted_minutes_delta", "player_id"], ascending=[False, True]
        )
        top1.append(float(actual.iloc[0]["player_id"] == predicted.iloc[0]["player_id"]))
        n = min(2, len(group))
        top2.append(len(set(actual.head(n)["player_id"]) &
                        set(predicted.head(n)["player_id"])) / n)
        if len(group) >= 3 and group["minutes_delta"].nunique() > 1 and group[
            "predicted_minutes_delta"
        ].nunique() > 1:
            correlations.append(float(spearmanr(
                group["minutes_delta"], group["predicted_minutes_delta"]
            ).statistic))
    return {
        "top1_recipient_accuracy": float(np.mean(top1)),
        "top2_recipient_recall": float(np.mean(top2)),
        "teammate_delta_spearman": float(np.mean(correlations)) if correlations else np.nan,
    }


def summarize_prediction_methods(predictions: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for experiment, group in predictions.groupby("experiment_id", sort=True):
        metrics = prediction_metrics(group)
        folds = []
        for season, fold in group.groupby("season", sort=True):
            fold_metrics = prediction_metrics(fold)
            folds.append({"season": season, **fold_metrics})
        rows.append({"experiment_id": experiment, **metrics, "fold_metrics": folds})
    return sorted(rows, key=lambda row: (row["mae"], row["experiment_id"]))


def select_redistribution_method(
    comparisons: list[dict[str, Any]],
) -> tuple[str, str]:
    best_naive = min(
        (row for row in comparisons if "baseline" in row["experiment_id"]),
        key=lambda row: row["mae"],
    )
    best_ml = min(
        (row for row in comparisons if "baseline" not in row["experiment_id"]
         and "ablation" not in row["experiment_id"]),
        key=lambda row: row["mae"],
    )
    improvement = (best_naive["mae"] - best_ml["mae"]) / best_naive["mae"]
    naive_fold = {row["season"]: row["mae"] for row in best_naive["fold_metrics"]}
    fold_wins = sum(
        row["mae"] < naive_fold[row["season"]] for row in best_ml["fold_metrics"]
    )
    if improvement >= 0.005 and fold_wins >= 2:
        return best_ml["experiment_id"], (
            f"ML improved {improvement:.2%} over the best naive method and won "
            f"{fold_wins}/3 outer folds"
        )
    return best_naive["experiment_id"], (
        f"No ML candidate cleared the pre-registered 0.5%/2-fold adoption rule; "
        f"best ML change was {improvement:.2%} with {fold_wins}/3 fold wins"
    )


def method_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.copy()
    work["missing_minutes_band"] = pd.cut(
        work["missing_expected_minutes"],
        [-np.inf, 10, 20, 30, 40, np.inf],
        labels=["0-10", "10-20", "20-30", "30-40", "40+"], right=False,
    )
    work["absence_count_band"] = np.select(
        [work["number_players_out"].eq(1), work["number_players_out"].eq(2)],
        ["1", "2"], default="3+",
    )
    work["recipient_role"] = pd.cut(
        work["baseline_expected_minutes"],
        [-np.inf, 10, 20, 25, np.inf],
        labels=["low_bench", "medium_rotation", "high_rotation", "starter_level"],
        right=False,
    )
    return {
        "overall": prediction_metrics(work),
        "missing_minutes_bands": _group_metrics(work, "missing_minutes_band"),
        "absence_count_bands": _group_metrics(work, "absence_count_band"),
        "recipient_roles": _group_metrics(work, "recipient_role"),
    }


def _group_metrics(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows = []
    for value, group in frame.groupby(column, observed=True, sort=True):
        rows.append({column: str(value), "rows": len(group), **prediction_metrics(group)})
    return rows


def cross_position_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    meaningful = frame[frame["minutes_delta"].ge(MEANINGFUL_GAIN_MINUTES)]
    cross = meaningful[~meaningful["recipient_matches_missing_position"]]
    predicted_meaningful = frame[frame["predicted_minutes_delta"].ge(MEANINGFUL_GAIN_MINUTES)]
    return {
        "meaningful_actual_gain_threshold": MEANINGFUL_GAIN_MINUTES,
        "meaningful_actual_gainers": len(meaningful),
        "cross_position_actual_gainers": len(cross),
        "cross_position_actual_rate": len(cross) / len(meaningful) if len(meaningful) else None,
        "predicted_meaningful_gainers": len(predicted_meaningful),
        "examples": cross.sort_values("minutes_delta", ascending=False)[[
            "season", "game_id", "player_id", "broad_position",
            "dominant_missing_position", "minutes_delta", "predicted_minutes_delta",
        ]].head(10).to_dict("records"),
    }


def evaluate_fantasy_impact(
    selected: pd.DataFrame,
    database_path: Path | str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    with connect_database(database_path, read_only=True) as connection:
        phase4b = connection.execute(
            """
            WITH direct AS (
              SELECT model_row_id, predicted_fp AS direct_fp
              FROM ml_phase4b_outer_predictions_v1
              WHERE experiment_id='p4a__catboost__core_rotation__verified_only__player__s17'
            ), hybrid AS (
              SELECT model_row_id, predicted_fp AS frozen_fp, actual_fp,
                     fantasy_matchday
              FROM ml_phase4b_outer_predictions_v1
              WHERE experiment_id='p4b__hybrid__selected'
            ), production AS (
              SELECT model_row_id, predicted_fp_per_min
              FROM ml_phase4b_production_predictions_v1
              WHERE experiment_id='p4b__production__catboost__weighted_older_history'
            )
            SELECT hybrid.*, direct.direct_fp, production.predicted_fp_per_min
            FROM hybrid JOIN direct USING (model_row_id)
            JOIN production USING (model_row_id)
            """
        ).df()
    merged = selected.merge(phase4b, on="model_row_id", how="inner", validate="one_to_one")
    merged["adjusted_fp"] = 0.25 * merged["direct_fp"] + 0.75 * (
        merged["predicted_adjusted_minutes"] * merged["predicted_fp_per_min"]
    )
    merged["direct_component_changed"] = False
    before = regression_metrics(merged["actual_fp"], merged["frozen_fp"])
    after = regression_metrics(merged["actual_fp"], merged["adjusted_fp"])
    before_ranking = ranking_metrics(
        merged.rename(columns={"frozen_fp": "predicted_fp"})
    )
    after_ranking = ranking_metrics(
        merged.rename(columns={"adjusted_fp": "predicted_fp"})
    )
    groups = {}
    for name, mask in {
        "important_absence_25_plus": merged["missing_expected_minutes"].ge(25),
        "multiple_absences": merged["number_players_out"].ge(2),
        "single_absence": merged["number_players_out"].eq(1),
    }.items():
        group = merged[mask]
        groups[name] = {
            "rows": len(group),
            "before": regression_metrics(group["actual_fp"], group["frozen_fp"]),
            "after": regression_metrics(group["actual_fp"], group["adjusted_fp"]),
        }
    return merged, {
        "rows": len(merged), "before": before, "after": after,
        "before_ranking": before_ranking, "after_ranking": after_ranking,
        "mae_change": after["mae"] - before["mae"],
        "mae_percentage_change": (after["mae"] / before["mae"] - 1) * 100,
        "groups": groups, "direct_weight": 0.25, "decomposed_weight": 0.75,
        "direct_component_changed": False,
        "uncertainty_handling": (
            "existing P10/P50/P90 are not recalibrated after a minutes scenario; "
            "live adjusted intervals remain null until separately calibrated"
        ),
        "frozen_model_version": FINAL_PHASE4B_MODEL_VERSION,
    }


def freeze_final_redistribution_model(
    events: pd.DataFrame,
    model_results: dict[str, Any],
    selected_method: str,
    derived_root: Path,
    dataset_summary: AbsenceDatasetSummary,
    freeze_reason: str,
) -> dict[str, Any]:
    if selected_method not in model_results:
        return {
            "status": "NO_ML_MODEL_FROZEN", "selected_method": selected_method,
            "reason": freeze_reason, "reconciliation_method": DEFAULT_RECONCILIATION,
        }
    result = model_results[selected_method]
    candidate = RedistributionCandidate(**result["candidate"])
    manifest = result["manifest"]
    selected_parameters = json.loads(min(
        result["selections"], key=lambda row: row["selection_score"]
    )["parameters"])
    training = events.copy()
    if candidate.include_rotation:
        training = training[training["season"].ge("E2021")]
    model = _fit_redistribution(
        candidate.model_type, manifest, training, selected_parameters
    )
    model_root = derived_root / "frozen_redistribution"
    model_root.mkdir(parents=True, exist_ok=True)
    if candidate.model_type == "catboost":
        model_path = model_root / "model.cbm"
        model.save_model(str(model_path))
    else:
        model_path = model_root / "model.pkl"
        with model_path.open("wb") as handle:
            pickle.dump(model, handle, protocol=5)
    version = "phase5b_known_absence_redistribution_frozen_v1"
    payload = {
        "status": "FROZEN", "redistribution_model_version": version,
        "selected_method": selected_method, "model_type": candidate.model_type,
        "feature_manifest": manifest, "hyperparameters": selected_parameters,
        "training_coverage": {
            "seasons": sorted(training["season"].unique()), "rows": len(training),
            "absence_team_games": int(training[["game_id", "team_id"]].drop_duplicates().shape[0]),
        },
        "dataset_fingerprint": dataset_summary.fingerprint,
        "evaluation_protocol": "E2023/E2024/E2025 chronological outer folds",
        "conditional_interpretation": "CONDITIONAL_ON_KNOWN_ABSENCE_SET",
        "reconciliation_method": DEFAULT_RECONCILIATION,
        "model_path": str(model_path.relative_to(PROJECT_ROOT)),
        "reason": freeze_reason,
    }
    (model_root / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def persist_experiment_artifacts(
    predictions: pd.DataFrame,
    fantasy: pd.DataFrame,
    trials: pd.DataFrame,
    baseline_audit: dict[str, Any],
    summary: dict[str, Any],
    *,
    database_path: Path | str,
    derived_root: Path,
    sample_root: Path,
) -> None:
    _write_parquet(
        predictions, derived_root / "ml_phase5b_redistribution_predictions_v1.parquet"
    )
    _write_parquet(fantasy, derived_root / "ml_phase5b_fantasy_impact_v1.parquet")
    _write_parquet(trials, derived_root / "ml_phase5b_inner_trials_v1.parquet")
    (sample_root / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (sample_root / "protocol.json").write_text(
        json.dumps({
            "protocol_version": PHASE5B_PROTOCOL_VERSION,
            "conditional_interpretation": "CONDITIONAL_ON_KNOWN_ABSENCE_SET",
            "baseline_role": baseline_audit,
            "redistribution_candidates": [asdict(value) for value in REDISTRIBUTION_CANDIDATES],
            "adoption_rule": "ML must improve >=0.5% over best naive and win >=2/3 folds",
            "reconciliation": DEFAULT_RECONCILIATION,
            "random_seed": PHASE5B_RANDOM_SEED,
        }, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (sample_root / "final_redistribution.json").write_text(
        json.dumps(summary["final_model"], indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    with connect_database(database_path) as connection:
        for name, frame in (
            ("ml_phase5b_redistribution_predictions_v1", predictions),
            ("ml_phase5b_fantasy_impact_v1", fantasy),
            ("ml_phase5b_inner_trials_v1", trials),
        ):
            relation = f"_{name}_source"
            connection.register(relation, frame)
            try:
                connection.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {relation}")
            finally:
                connection.unregister(relation)
        connection.execute(
            "CREATE OR REPLACE VIEW ml_phase5b_redistribution_predictions AS "
            "SELECT * FROM ml_phase5b_redistribution_predictions_v1"
        )
