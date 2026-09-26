"""Chronological Phase 9A game-environment/stat-line challenger research."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm, pearsonr, spearmanr
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.ml_experiments import _catboost_feature_frame
from src.modeling.phase6b_protocol import phase6b_folds
from src.modeling.phase6c_protocol import phase6c_feature_manifest
from src.modeling.phase6c_runner import _fit_central_probe

from .phase9a_features import (
    add_profile_environment_interactions,
    attach_environment_predictions,
    build_game_environment_frame,
    environment_feature_columns,
    environment_trailing_baseline,
    load_phase6c_baseline,
    load_phase9a_player_frame,
    player_archetype,
    style_environment_feature_columns,
)
from .phase9a_protocol import (
    DOWNSIDE_THRESHOLDS,
    EVALUATION_SEASONS,
    GAME_ENVIRONMENT_TARGETS,
    MADE_COMPONENTS,
    OPPORTUNITY_COMPONENTS,
    PBP_FEATURE_FAMILIES,
    PBP_STYLE_TARGETS,
    PHASE9A_MODEL_VERSION,
    PHASE9A_PROTOCOL_VERSION,
    PHASE9A_RANDOM_SEED,
    PHASE9A_SIMULATIONS,
    PRIMARY_SEASONS,
    QUANTILES,
    UNSUPPORTED_PBP_CONCEPTS,
    UPSIDE_THRESHOLDS,
    player_feature_manifest,
    protocol_fingerprint,
    protocol_payload,
)
from .phase9a_simulation import (
    blend_samples,
    build_residual_bank,
    crps_from_samples,
    distribution_metrics,
    efficiency_parameters,
    inverse_quantile_samples,
    minute_band,
    regression_metrics,
    simulate_stat_lines,
    summarize_distribution,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE9A_ROOT = PROJECT_ROOT / "data" / "derived" / "phase9a"
DEFAULT_RESEARCH_ROOT = DEFAULT_PHASE9A_ROOT / "research"
DEFAULT_BUNDLE_ROOT = DEFAULT_PHASE9A_ROOT / "frozen_statline_game_environment"
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase9a"
FROZEN_PHASE6C_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase6c" / "frozen_predictive_uplift"
)
FROZEN_PHASE7_ROOT = (
    PROJECT_ROOT / "data" / "derived" / "phase7" / "frozen_dynamic_strategy_engine"
)

ARCHITECTURES = (
    "A_phase6c_frozen",
    "B_statline",
    "C_environment_statline",
    "D_phase6c_statline_ensemble",
    "E_phase6c_environment_statline_ensemble",
)


def _tree_fingerprint(root: Path) -> str:
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }
    return _json_sha(files)


def _extra_trees(seed: int, *, leaf: int = 7) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        ExtraTreesRegressor(
            n_estimators=144, max_depth=16, min_samples_leaf=leaf,
            max_features=0.75, bootstrap=False, n_jobs=4,
            random_state=seed,
        ),
    )


def _numeric_matrix(frame: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    return frame[list(features)].apply(pd.to_numeric, errors="coerce").to_numpy(float)


def _normalized_mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    scale = np.nanstd(actual, axis=0)
    scale = np.where(scale > 1e-6, scale, 1.0)
    return float(np.nanmean(np.abs(actual - predicted) / scale))


def build_walk_forward_environment_predictions(
    environment: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Predict each target season from strictly earlier team games."""

    prediction_rows: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    family_outer: dict[str, list[dict[str, Any]]] = {
        "box_history_only": [], **{name: [] for name in PBP_FEATURE_FAMILIES},
    }
    core_targets = list(GAME_ENVIRONMENT_TARGETS)
    style_targets = list(PBP_STYLE_TARGETS)
    years = environment["season"].str.removeprefix("E").astype(int)

    for target_year in range(2022, 2026):
        outer_train = environment[years < target_year].copy()
        test = environment[years == target_year].copy()
        if target_year == 2022:
            unique_times = sorted(outer_train["game_time"].unique())
            split = unique_times[int(len(unique_times) * 0.75)]
            inner_train = outer_train[outer_train["game_time"] < split]
            validation = outer_train[outer_train["game_time"] >= split]
        else:
            validation = outer_train[outer_train["season"].eq(f"E{target_year - 1}")]
            inner_train = outer_train[~outer_train["season"].eq(f"E{target_year - 1}")]
        if inner_train.empty or validation.empty or test.empty:
            raise ValueError(f"empty environment split for E{target_year}")

        family_inner: dict[str, float] = {}
        variants: dict[str, Sequence[str]] = {
            "box_history_only": environment_feature_columns(),
            **{
                family: environment_feature_columns(pbp_family=family)
                for family in PBP_FEATURE_FAMILIES
            },
        }
        for index, (family, features) in enumerate(variants.items()):
            model = _extra_trees(PHASE9A_RANDOM_SEED + target_year + index, leaf=5)
            model.fit(
                _numeric_matrix(inner_train, features),
                inner_train[core_targets].to_numpy(float),
            )
            prediction = model.predict(_numeric_matrix(validation, features))
            family_inner[family] = _normalized_mae(
                validation[core_targets].to_numpy(float), prediction
            )
        base_inner = family_inner["box_history_only"]
        eligible = [
            family for family in PBP_FEATURE_FAMILIES
            if family_inner[family] < base_inner * 0.998
        ]
        retained_family = min(
            eligible, key=lambda name: family_inner[name], default="box_history_only"
        )
        retained_features = variants[retained_family]
        model = _extra_trees(PHASE9A_RANDOM_SEED + 100 + target_year, leaf=5)
        model.fit(
            _numeric_matrix(outer_train, retained_features),
            outer_train[core_targets].to_numpy(float),
        )
        core_prediction = model.predict(_numeric_matrix(test, retained_features))
        core_prediction = np.maximum(core_prediction, 0.0)
        for rate_index, target in enumerate(core_targets):
            if target.endswith("_rate") or target.endswith("concentration"):
                core_prediction[:, rate_index] = np.clip(
                    core_prediction[:, rate_index], 0.0, 1.0
                )

        style_train = outer_train.dropna(subset=style_targets)
        style_test = test.dropna(subset=style_targets)
        if len(style_train) < 300 or len(style_test) != len(test):
            raise ValueError("PBP style targets lack reliable Phase 9A coverage")
        style_features = style_environment_feature_columns()
        style_model = _extra_trees(PHASE9A_RANDOM_SEED + 200 + target_year, leaf=7)
        style_model.fit(
            _numeric_matrix(style_train, style_features),
            style_train[style_targets].to_numpy(float),
        )
        style_prediction = np.clip(
            style_model.predict(_numeric_matrix(test, style_features)), 0.0, 1.0
        )
        fold_prediction = test[["game_id", "team_id", "season", "game_time"]].copy()
        for index, target in enumerate(core_targets):
            fold_prediction[f"p9_env_{target}"] = core_prediction[:, index]
        for index, target in enumerate(style_targets):
            fold_prediction[f"p9_env_{target}"] = style_prediction[:, index]
        prediction_rows.append(fold_prediction)

        baseline = environment_trailing_baseline(test)
        audits.append({
            "prediction_season": f"E{target_year}",
            "inner_train_rows": len(inner_train), "inner_validation_rows": len(validation),
            "outer_train_rows": len(outer_train), "outer_test_rows": len(test),
            "outer_test_rows_seen_during_selection": 0,
            "inner_family_normalized_mae": family_inner,
            "retained_pbp_family": retained_family,
            "train_max_time": outer_train["game_time"].max().isoformat(),
            "test_min_time": test["game_time"].min().isoformat(),
            "model_normalized_mae": _normalized_mae(
                test[core_targets].to_numpy(float), core_prediction
            ),
            "trailing_baseline_normalized_mae": _normalized_mae(
                test[core_targets].to_numpy(float), baseline
            ),
        })
        for index, (family, features) in enumerate(variants.items()):
            ablation_model = _extra_trees(
                PHASE9A_RANDOM_SEED + 300 + target_year * 10 + index, leaf=5
            )
            ablation_model.fit(
                _numeric_matrix(outer_train, features),
                outer_train[core_targets].to_numpy(float),
            )
            ablation_prediction = ablation_model.predict(_numeric_matrix(test, features))
            family_outer[family].append({
                "prediction_season": f"E{target_year}", "rows": len(test),
                "normalized_mae": _normalized_mae(
                    test[core_targets].to_numpy(float), ablation_prediction
                ),
            })

    predictions = pd.concat(prediction_rows, ignore_index=True)
    evaluation_predictions = predictions[predictions["season"].isin(EVALUATION_SEASONS)]
    evaluation = environment.merge(
        evaluation_predictions.drop(columns=["season", "game_time"]),
        on=["game_id", "team_id"], how="inner", validate="one_to_one",
    )
    target_metrics: dict[str, Any] = {}
    style_metrics: dict[str, Any] = {}
    for target in GAME_ENVIRONMENT_TARGETS:
        baseline = environment_trailing_baseline(evaluation)[
            :, list(GAME_ENVIRONMENT_TARGETS).index(target)
        ]
        target_metrics[target] = _environment_target_metrics(
            evaluation[target].to_numpy(float),
            evaluation[f"p9_env_{target}"].to_numpy(float), baseline,
        )
    for target in PBP_STYLE_TARGETS:
        baseline = evaluation[f"env_{target}_last5"].fillna(
            evaluation[f"env_{target}_season_before"]
        ).fillna(evaluation[target].mean()).to_numpy(float)
        style_metrics[target] = _environment_target_metrics(
            evaluation[target].to_numpy(float),
            evaluation[f"p9_env_{target}"].to_numpy(float), baseline,
        )
    pbp_ablation_summary = {}
    for family, rows in family_outer.items():
        pbp_ablation_summary[family] = {
            "folds": rows,
            "weighted_normalized_mae": float(np.average(
                [row["normalized_mae"] for row in rows],
                weights=[row["rows"] for row in rows],
            )),
        }
    return predictions, {
        "fold_audits": audits,
        "target_metrics": target_metrics,
        "pbp_style_target_metrics": style_metrics,
        "pbp_feature_ablations": pbp_ablation_summary,
        "unsupported": UNSUPPORTED_PBP_CONCEPTS,
    }


def _environment_target_metrics(
    actual: np.ndarray, predicted: np.ndarray, baseline: np.ndarray,
) -> dict[str, Any]:
    finite = np.isfinite(actual) & np.isfinite(predicted) & np.isfinite(baseline)
    y, p, b = actual[finite], predicted[finite], baseline[finite]
    model = regression_metrics(y, p)
    base = regression_metrics(y, b)
    return {
        "rows": len(y), "model": model, "trailing_baseline": base,
        "mae_improvement": base["mae"] - model["mae"],
        "predictable": bool(model["mae"] < base["mae"] and (model["pearson"] or 0) > 0.15),
    }


def _fit_minutes_model(
    train: pd.DataFrame, prediction: pd.DataFrame,
    features: Sequence[str], seed: int,
) -> np.ndarray:
    model = _extra_trees(seed, leaf=8)
    model.fit(_numeric_matrix(train, features), train["target_minutes"].to_numpy(float))
    return np.clip(model.predict(_numeric_matrix(prediction, features)), 0.5, 45.0)


def _fit_opportunity_model(
    train: pd.DataFrame,
    prediction: pd.DataFrame,
    features: Sequence[str],
    predicted_minutes: np.ndarray,
    seed: int,
) -> np.ndarray:
    train_matrix = pd.DataFrame(
        _numeric_matrix(train, features), columns=list(features), index=train.index
    )
    prediction_matrix = pd.DataFrame(
        _numeric_matrix(prediction, features), columns=list(features), index=prediction.index
    )
    # Target-game minutes are an exposure during fit only.  Every validation/live
    # prediction receives the separately predicted value, never actual minutes.
    train_matrix["_p9_minutes_condition"] = train["target_minutes"].to_numpy(float)
    prediction_matrix["_p9_minutes_condition"] = predicted_minutes
    model = _extra_trees(seed, leaf=7)
    model.fit(
        train_matrix.to_numpy(float),
        train[list(OPPORTUNITY_COMPONENTS)].to_numpy(float),
    )
    return np.maximum(model.predict(prediction_matrix.to_numpy(float)), 0.0)


def _deterministic_statline_mean(
    frame: pd.DataFrame,
    opportunities: np.ndarray,
    win_probability: np.ndarray,
) -> np.ndarray:
    alpha, beta = efficiency_parameters(frame)
    conversion = alpha / (alpha + beta)
    two_pa, three_pa, fta = opportunities[:, 0], opportunities[:, 1], opportunities[:, 2]
    two_made = two_pa * conversion[:, 0]
    three_made = three_pa * conversion[:, 1]
    ft_made = fta * conversion[:, 2]
    points = 2.0 * two_made + 3.0 * three_made + ft_made
    base = (
        points
        + opportunities[:, 3] + opportunities[:, 4]
        + opportunities[:, 5] + opportunities[:, 6] + opportunities[:, 7]
        + opportunities[:, 9]
        - opportunities[:, 8] - opportunities[:, 10] - opportunities[:, 11]
        - (two_pa - two_made) - (three_pa - three_made) - (fta - ft_made)
    )
    return base + np.clip(win_probability, 0.0, 1.0) * 0.1 * np.abs(base)


def _win_probability(frame: pd.DataFrame, include_environment: bool) -> np.ndarray:
    if include_environment:
        margin = (
            pd.to_numeric(frame["p9_env_team_points"], errors="coerce")
            - pd.to_numeric(frame["p9_env_opponent_points"], errors="coerce")
        ).fillna(0.0).to_numpy(float)
        return np.clip(norm.cdf(margin / 11.5), 0.05, 0.95)
    team_attack = pd.to_numeric(
        frame["team_last5_ortg"], errors="coerce"
    ).fillna(pd.to_numeric(frame["team_season_ortg_before"], errors="coerce"))
    opponent_defense = pd.to_numeric(
        frame["opp_last5_drtg"], errors="coerce"
    ).fillna(pd.to_numeric(frame["opp_season_drtg_before"], errors="coerce"))
    home = pd.to_numeric(frame["home_game"], errors="coerce").fillna(0.0)
    margin_proxy = (team_attack - opponent_defense).fillna(0.0) / 8.0 + 0.28 * home
    return np.clip(1.0 / (1.0 + np.exp(-margin_proxy.to_numpy(float))), 0.05, 0.95)


def _candidate_prefix(include_environment: bool, include_interactions: bool) -> str:
    if not include_environment:
        return "B_statline"
    return "C_environment_statline" if include_interactions else "C_environment_no_interactions"


def run_stat_architecture_fold(
    *,
    inner_train: pd.DataFrame,
    validation: pd.DataFrame,
    outer_train: pd.DataFrame,
    test: pd.DataFrame,
    include_environment: bool,
    include_interactions: bool,
    fold_index: int,
) -> dict[str, Any]:
    """Fit chained minutes/opportunity models and simulate one outer fold."""

    manifest = player_feature_manifest(
        include_environment=include_environment,
        include_interactions=include_interactions,
    )
    features = manifest["features"]
    seed = PHASE9A_RANDOM_SEED + 1000 * fold_index + (100 if include_environment else 0)
    if include_environment and not include_interactions:
        seed += 50

    inner_minutes = _fit_minutes_model(inner_train, validation, features, seed + 1)
    inner_opportunities = _fit_opportunity_model(
        inner_train, validation, features, inner_minutes, seed + 2
    )
    bank = build_residual_bank(
        actual_minutes=validation["target_minutes"],
        predicted_minutes=inner_minutes,
        actual_opportunities=validation[list(OPPORTUNITY_COMPONENTS)].to_numpy(float),
        predicted_opportunities=inner_opportunities,
        archetype=player_archetype(validation),
    )
    inner_win_probability = _win_probability(validation, include_environment)
    inner_expected = _deterministic_statline_mean(
        validation, inner_opportunities, inner_win_probability
    )
    fixed_inner_samples, _, fixed_inner_minutes = simulate_stat_lines(
        frame=validation, predicted_minutes=inner_minutes,
        predicted_opportunities=inner_opportunities, residual_bank=bank,
        win_probability=inner_win_probability, archetype=player_archetype(validation),
        simulations=PHASE9A_SIMULATIONS, probabilistic_minutes=False,
        random_seed=seed + 3,
    )
    probabilistic_inner_samples, _, probabilistic_inner_minutes = simulate_stat_lines(
        frame=validation, predicted_minutes=inner_minutes,
        predicted_opportunities=inner_opportunities, residual_bank=bank,
        win_probability=inner_win_probability, archetype=player_archetype(validation),
        simulations=PHASE9A_SIMULATIONS, probabilistic_minutes=True,
        random_seed=seed + 4,
    )
    y_validation = validation["actual_fantasy_points"].to_numpy(float)
    fixed_inner_crps = crps_from_samples(y_validation, fixed_inner_samples)
    probabilistic_inner_crps = crps_from_samples(y_validation, probabilistic_inner_samples)
    use_probabilistic_minutes = probabilistic_inner_crps < fixed_inner_crps

    outer_minutes = _fit_minutes_model(outer_train, test, features, seed + 5)
    outer_opportunities = _fit_opportunity_model(
        outer_train, test, features, outer_minutes, seed + 6
    )
    outer_win_probability = _win_probability(test, include_environment)
    fixed_samples, fixed_components, fixed_minute_samples = simulate_stat_lines(
        frame=test, predicted_minutes=outer_minutes,
        predicted_opportunities=outer_opportunities, residual_bank=bank,
        win_probability=outer_win_probability, archetype=player_archetype(test),
        simulations=PHASE9A_SIMULATIONS, probabilistic_minutes=False,
        random_seed=seed + 7,
    )
    probabilistic_samples, probabilistic_components, probabilistic_minute_samples = simulate_stat_lines(
        frame=test, predicted_minutes=outer_minutes,
        predicted_opportunities=outer_opportunities, residual_bank=bank,
        win_probability=outer_win_probability, archetype=player_archetype(test),
        simulations=PHASE9A_SIMULATIONS, probabilistic_minutes=True,
        random_seed=seed + 8,
    )
    samples = probabilistic_samples if use_probabilistic_minutes else fixed_samples
    component_mean = (
        probabilistic_components if use_probabilistic_minutes else fixed_components
    )
    minute_samples = (
        probabilistic_minute_samples if use_probabilistic_minutes else fixed_minute_samples
    )
    summary = summarize_distribution(samples)
    summary.index = test.index
    actual = test["actual_fantasy_points"].to_numpy(float)
    fixed_summary = summarize_distribution(fixed_samples)
    probabilistic_summary = summarize_distribution(probabilistic_samples)
    return {
        "name": _candidate_prefix(include_environment, include_interactions),
        "manifest": manifest,
        "inner_expected_fp": inner_expected,
        "inner_metrics": regression_metrics(y_validation, inner_expected),
        "inner_fixed_minutes_crps": fixed_inner_crps,
        "inner_probabilistic_minutes_crps": probabilistic_inner_crps,
        "probabilistic_minutes_selected": use_probabilistic_minutes,
        "outer_predicted_minutes": outer_minutes,
        "outer_predicted_opportunities": outer_opportunities,
        "outer_samples": samples,
        "outer_summary": summary,
        "outer_component_mean": component_mean,
        "outer_minute_samples": minute_samples,
        "outer_probabilistic_minute_samples": probabilistic_minute_samples,
        "outer_metrics": regression_metrics(actual, summary["expected_fp"]),
        "fixed_minutes_distribution": distribution_metrics(
            actual, fixed_summary, fixed_samples
        ),
        "probabilistic_minutes_distribution": distribution_metrics(
            actual, probabilistic_summary, probabilistic_samples
        ),
        "minutes_metrics": regression_metrics(test["target_minutes"], outer_minutes),
        "minutes_quantiles": _minutes_distribution_metrics(
            test["target_minutes"].to_numpy(float), probabilistic_minute_samples
        ),
        "inner_audit": {
            "inner_train_rows": len(inner_train),
            "inner_validation_rows": len(validation),
            "outer_train_rows": len(outer_train), "outer_test_rows": len(test),
            "outer_test_rows_seen_during_selection": 0,
            "inner_train_max_time": inner_train["target_game_time"].max().isoformat(),
            "inner_validation_min_time": validation["target_game_time"].min().isoformat(),
            "outer_train_max_time": outer_train["target_game_time"].max().isoformat(),
            "outer_test_min_time": test["target_game_time"].min().isoformat(),
            "target_game_actual_minutes_used_at_inference": False,
        },
    }


def _minutes_distribution_metrics(actual: np.ndarray, samples: np.ndarray) -> dict[str, Any]:
    output: dict[str, Any] = {"crps": crps_from_samples(actual, samples)}
    for quantile in (0.10, 0.50, 0.90):
        prediction = np.quantile(samples, quantile, axis=1)
        residual = actual - prediction
        output[f"p{int(quantile * 100):02d}"] = {
            "pinball_loss": float(np.mean(np.maximum(
                quantile * residual, (quantile - 1.0) * residual
            ))),
            "coverage": float(np.mean(actual <= prediction)),
            "coverage_error": float(np.mean(actual <= prediction) - quantile),
        }
    return output


def _phase6c_summary(frame: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    output["expected_fp"] = frame["phase6c_expected_fp"].to_numpy(float)
    for quantile in QUANTILES:
        output[f"p{int(quantile * 100):02d}_fp"] = frame[
            f"p{int(quantile * 100):02d}_fp"
        ].to_numpy(float)
    output["median_fp"] = output["p50_fp"]
    for threshold in UPSIDE_THRESHOLDS:
        output[f"prob_fp_ge_{int(threshold)}"] = frame[
            f"prob_fp_ge_{int(threshold)}"
        ].to_numpy(float)
    for threshold in DOWNSIDE_THRESHOLDS:
        output[f"prob_fp_le_{int(threshold)}"] = frame[
            f"prob_fp_le_{int(threshold)}"
        ].to_numpy(float)
    output["distribution_width"] = output["p90_fp"] - output["p10_fp"]
    output["p90_minus_p50"] = output["p90_fp"] - output["p50_fp"]
    output["p50_minus_p10"] = output["p50_fp"] - output["p10_fp"]
    output["p95_minus_expected"] = output["p95_fp"] - output["expected_fp"]
    return output


def _phase6c_inner_prediction(
    full_frame: pd.DataFrame,
    inner_train: pd.DataFrame,
    validation: pd.DataFrame,
    phase6c: pd.DataFrame,
) -> np.ndarray:
    available = validation[["model_row_id"]].merge(
        phase6c[["model_row_id", "phase6c_expected_fp"]],
        on="model_row_id", how="left", validate="one_to_one",
    )
    if available["phase6c_expected_fp"].notna().all():
        return available["phase6c_expected_fp"].to_numpy(float)
    manifest = phase6c_feature_manifest(("usage_offensive_involvement",))
    model, _ = _fit_central_probe(inner_train, validation, manifest)
    return np.asarray(
        model.predict(_catboost_feature_frame(validation, manifest)), dtype=float
    )


def learn_convex_weight(
    actual: Sequence[float], baseline: Sequence[float], challenger: Sequence[float],
) -> dict[str, Any]:
    """Learn challenger weight from chronological OOF/inner predictions only."""

    y = np.asarray(actual, float)
    base = np.asarray(baseline, float)
    challenge = np.asarray(challenger, float)
    trials = []
    for weight in np.linspace(0.0, 1.0, 41):
        prediction = (1.0 - weight) * base + weight * challenge
        metric = regression_metrics(y, prediction)
        trials.append({"challenger_weight": float(weight), **metric})
    selected = min(trials, key=lambda row: (row["mae"], row["rmse"], row["challenger_weight"]))
    return {
        "challenger_weight": selected["challenger_weight"],
        "phase6c_weight": 1.0 - selected["challenger_weight"],
        "selection_metric": "inner chronological MAE; RMSE tie-break",
        "outer_test_rows_seen": 0,
        "selected_inner_metrics": selected,
        "trials": trials,
    }


def _prefix_summary(
    output: pd.DataFrame, name: str, summary: pd.DataFrame,
) -> pd.DataFrame:
    prefixed = summary.reset_index(drop=True).add_prefix(f"{name}__")
    return pd.concat([output.reset_index(drop=True), prefixed], axis=1)


def _ranking_metrics(frame: pd.DataFrame, expected_column: str) -> dict[str, Any]:
    correlations: list[float] = []
    top1: list[float] = []
    top3: list[float] = []
    for _, group in frame.groupby("game_id"):
        if len(group) < 5:
            continue
        correlation = spearmanr(group["actual_fp"], group[expected_column]).statistic
        if np.isfinite(correlation):
            correlations.append(float(correlation))
        predicted = group.nlargest(3, expected_column)["model_row_id"].tolist()
        actual = group.nlargest(3, "actual_fp")["model_row_id"].tolist()
        top1.append(float(predicted[0] == actual[0]))
        top3.append(len(set(predicted) & set(actual)) / 3.0)
    return {
        "games": len(top1), "mean_game_spearman": float(np.mean(correlations)),
        "top1_hit_rate": float(np.mean(top1)), "top3_recall": float(np.mean(top3)),
    }


def run_phase9a_research(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    research_root: Path = DEFAULT_RESEARCH_ROOT,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    force: bool = False,
) -> dict[str, Any]:
    """Run the bounded challenger and freeze only after the strict outer gate."""

    result_path = sample_root / "results.json"
    if result_path.is_file() and not force:
        return json.loads(result_path.read_text())
    frozen_before = {
        "phase6c": _tree_fingerprint(FROZEN_PHASE6C_ROOT),
        "phase7": _tree_fingerprint(FROZEN_PHASE7_ROOT),
    }
    print("phase9a: building measurable game environments", flush=True)
    environment = build_game_environment_frame(database_path)
    environment_predictions, environment_results = (
        build_walk_forward_environment_predictions(environment)
    )
    print("phase9a: attaching verified stat-line targets and Phase 5B context", flush=True)
    players = load_phase9a_player_frame(database_path)
    players = attach_environment_predictions(players, environment_predictions)
    folds = phase6b_folds(players)
    phase6c = load_phase6c_baseline()

    outer_rows: list[pd.DataFrame] = []
    fold_results: dict[str, Any] = {}
    inner_architecture: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {
        name: [] for name in ARCHITECTURES
    }
    component_frames: dict[str, list[pd.DataFrame]] = {
        "B_statline": [], "C_environment_statline": [],
    }
    minute_frames: list[pd.DataFrame] = []
    interaction_rows: list[dict[str, Any]] = []
    blend_rows: dict[str, list[dict[str, Any]]] = {
        "D_phase6c_statline_ensemble": [],
        "E_phase6c_environment_statline_ensemble": [],
    }

    for fold_index, fold in enumerate(folds):
        definition = fold["definition"]
        fold_id = definition.fold_id
        inner_train = players.loc[fold["inner_train"]].copy()
        validation = players.loc[fold["inner_validation"]].copy()
        outer_train = players.loc[fold["outer_train"]].copy()
        test = players.loc[fold["outer_test"]].copy()
        baseline_test = test[["model_row_id"]].merge(
            phase6c, on="model_row_id", how="left", validate="one_to_one"
        )
        if baseline_test["phase6c_expected_fp"].isna().any():
            raise ValueError(f"missing frozen Phase 6C baseline in {fold_id}")
        baseline_inner = _phase6c_inner_prediction(
            players, inner_train, validation, phase6c
        )
        actual_inner = validation["actual_fantasy_points"].to_numpy(float)

        candidates: dict[str, dict[str, Any]] = {}
        for include_environment, include_interactions in (
            (False, False), (True, False), (True, True),
        ):
            candidate = run_stat_architecture_fold(
                inner_train=inner_train, validation=validation,
                outer_train=outer_train, test=test,
                include_environment=include_environment,
                include_interactions=include_interactions,
                fold_index=fold_index,
            )
            candidates[candidate["name"]] = candidate
            print(
                f"phase9a: {fold_id} {candidate['name']} "
                f"MAE={candidate['outer_metrics']['mae']:.5f}", flush=True,
            )

        b = candidates["B_statline"]
        c = candidates["C_environment_statline"]
        c_no = candidates["C_environment_no_interactions"]
        weights_d = learn_convex_weight(actual_inner, baseline_inner, b["inner_expected_fp"])
        weights_e = learn_convex_weight(actual_inner, baseline_inner, c["inner_expected_fp"])
        blend_rows["D_phase6c_statline_ensemble"].append({
            "outer_fold": fold_id, **{key: value for key, value in weights_d.items() if key != "trials"}
        })
        blend_rows["E_phase6c_environment_statline_ensemble"].append({
            "outer_fold": fold_id, **{key: value for key, value in weights_e.items() if key != "trials"}
        })

        baseline_summary = _phase6c_summary(baseline_test)
        baseline_samples = inverse_quantile_samples(
            baseline_test[[f"p{int(q * 100):02d}_fp" for q in QUANTILES]].to_numpy(float),
            PHASE9A_SIMULATIONS,
        )
        d_samples = blend_samples(
            baseline_samples, b["outer_samples"], weights_d["challenger_weight"]
        )
        e_samples = blend_samples(
            baseline_samples, c["outer_samples"], weights_e["challenger_weight"]
        )
        summaries = {
            "A_phase6c_frozen": baseline_summary,
            "B_statline": b["outer_summary"].reset_index(drop=True),
            "C_environment_statline": c["outer_summary"].reset_index(drop=True),
            "D_phase6c_statline_ensemble": summarize_distribution(d_samples),
            "E_phase6c_environment_statline_ensemble": summarize_distribution(e_samples),
        }
        samples = {
            "A_phase6c_frozen": baseline_samples,
            "B_statline": b["outer_samples"],
            "C_environment_statline": c["outer_samples"],
            "D_phase6c_statline_ensemble": d_samples,
            "E_phase6c_environment_statline_ensemble": e_samples,
        }
        actual = test["actual_fantasy_points"].to_numpy(float)
        fold_architecture: dict[str, Any] = {}
        output = test[[
            "model_row_id", "season", "round_number", "game_id", "player_id",
            "team_id", "opponent_team_id", "target_game_time", "feature_cutoff_time",
            "fantasy_matchday", "fantasy_position", "target_minutes",
            "phase4b_expected_minutes", "p6c_player_archetype",
            "expected_usage_next_game", "rebounds_per_min", "assists_per_min",
            "season_fp_std_before", "number_players_out",
        ]].copy().reset_index(drop=True)
        output["outer_fold"] = fold_id
        output["actual_fp"] = actual
        for name in ARCHITECTURES:
            summary = summaries[name]
            output = _prefix_summary(output, name, summary)
            fold_architecture[name] = {
                "central": regression_metrics(actual, summary["expected_fp"]),
                "distribution": distribution_metrics(actual, summary, samples[name]),
                "ranking": _ranking_metrics(
                    pd.DataFrame({
                        "game_id": output["game_id"],
                        "model_row_id": output["model_row_id"],
                        "actual_fp": actual,
                        "expected": summary["expected_fp"].to_numpy(float),
                    }), "expected",
                ),
            }
            if name == "A_phase6c_frozen":
                inner_prediction = baseline_inner
            elif name == "B_statline":
                inner_prediction = b["inner_expected_fp"]
            elif name == "C_environment_statline":
                inner_prediction = c["inner_expected_fp"]
            elif name == "D_phase6c_statline_ensemble":
                weight = weights_d["challenger_weight"]
                inner_prediction = (1.0 - weight) * baseline_inner + weight * b["inner_expected_fp"]
            else:
                weight = weights_e["challenger_weight"]
                inner_prediction = (1.0 - weight) * baseline_inner + weight * c["inner_expected_fp"]
            inner_architecture[name].append((actual_inner.copy(), inner_prediction.copy()))
        output["B_statline__expected_minutes"] = b["outer_predicted_minutes"]
        output["C_environment_statline__expected_minutes"] = c["outer_predicted_minutes"]
        outer_rows.append(output)

        for candidate_name, candidate in (("B_statline", b), ("C_environment_statline", c)):
            component = test[[
                "model_row_id", "actual_fantasy_points", "target_minutes",
                *MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS, "points",
            ]].copy()
            component = component.reset_index(drop=True)
            predicted_components = candidate["outer_component_mean"].reset_index(drop=True)
            for column in predicted_components:
                component[f"predicted_{column}"] = predicted_components[column]
            component["predicted_fp"] = candidate["outer_summary"]["expected_fp"].to_numpy(float)
            component_frames[candidate_name].append(component)

        minute_frame = test[[
            "model_row_id", "target_minutes", "phase4b_expected_minutes",
        ]].copy().reset_index(drop=True)
        minute_frame["outer_fold"] = fold_id
        minute_frame["challenger_expected_minutes"] = c["outer_predicted_minutes"]
        for quantile in (0.10, 0.50, 0.90):
            minute_frame[f"challenger_minutes_p{int(quantile * 100):02d}"] = np.quantile(
                c["outer_probabilistic_minute_samples"], quantile, axis=1
            )
        minute_frames.append(minute_frame)
        interaction_rows.append({
            "outer_fold": fold_id, "rows": len(test),
            "without_interactions": c_no["outer_metrics"],
            "with_interactions": c["outer_metrics"],
            "mae_improvement": c_no["outer_metrics"]["mae"] - c["outer_metrics"]["mae"],
        })
        fold_results[fold_id] = {
            "rows": len(test), "architectures": fold_architecture,
            "minutes_sampling": {
                "B_statline": _minutes_selection_summary(b),
                "C_environment_statline": _minutes_selection_summary(c),
            },
            "interaction_ablation": interaction_rows[-1],
            "ensemble_weights": {
                "D_phase6c_statline_ensemble": weights_d,
                "E_phase6c_environment_statline_ensemble": weights_e,
            },
            "leakage_audits": {
                name: candidate["inner_audit"] for name, candidate in candidates.items()
            },
        }

    predictions = pd.concat(outer_rows, ignore_index=True)
    architecture_results = _aggregate_architecture_results(
        predictions, fold_results, inner_architecture
    )
    selected_architecture = min(
        ARCHITECTURES,
        key=lambda name: (
            architecture_results[name]["inner_selection_metrics"]["mae"],
            architecture_results[name]["inner_selection_metrics"]["rmse"],
        ),
    )
    gate = _freeze_gate(selected_architecture, architecture_results, fold_results)
    predictive_winner = (
        PHASE9A_MODEL_VERSION if gate["passed"]
        else "phase6c_predictive_uplift_frozen_v1"
    )
    winner_architecture = selected_architecture if gate["passed"] else "A_phase6c_frozen"
    predictions = _add_winner_interface(
        predictions, winner_architecture, predictive_winner
    )

    minutes = pd.concat(minute_frames, ignore_index=True)
    component_results = {
        name: _component_accuracy(pd.concat(frames, ignore_index=True))
        for name, frames in component_frames.items()
    }
    frozen_after = {
        "phase6c": _tree_fingerprint(FROZEN_PHASE6C_ROOT),
        "phase7": _tree_fingerprint(FROZEN_PHASE7_ROOT),
    }
    frozen_untouched = frozen_before == frozen_after
    if not frozen_untouched:
        raise ValueError("Phase 9A modified a frozen Phase 6C/7 artifact")

    research_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(environment, research_root / "game_environment_frame.parquet")
    _write_parquet(environment_predictions, research_root / "game_environment_predictions.parquet")
    _write_parquet(predictions, research_root / "outer_predictions.parquet")
    _write_parquet(minutes, research_root / "minutes_predictions.parquet")
    result: dict[str, Any] = {
        "status": "FROZEN" if gate["passed"] else "PHASE6C_RETAINED",
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol_payload(), "protocol_fingerprint": protocol_fingerprint(),
        "dataset": {
            "player_rows": len(players), "outer_rows": len(predictions),
            "team_game_environment_rows": len(environment),
            "seasons": list(PRIMARY_SEASONS),
            "conditional_on_participation": True,
            "player_frame_fingerprint": _frame_fingerprint(players),
        },
        "game_environment": environment_results,
        "architectures": architecture_results,
        "outer_folds": fold_results,
        "selected_on_inner_validation": selected_architecture,
        "freeze_gate": gate,
        "predictive_winner": predictive_winner,
        "winner_architecture": winner_architecture,
        "ensemble_weights": blend_rows,
        "minutes_distribution": _minutes_results(minutes, fold_results),
        "component_accuracy": component_results,
        "player_environment_interactions": _interaction_summary(interaction_rows),
        "segments": _segment_metrics(predictions, selected_architecture),
        "explosion_analysis": _explosion_analysis(
            predictions, pd.concat(component_frames["C_environment_statline"], ignore_index=True)
        ),
        "phase7_interface": _phase7_interface_check(predictions),
        "frozen_artifacts": {
            "before": frozen_before, "after": frozen_after,
            "phase6c_phase7_untouched": frozen_untouched,
        },
        "deployment_bundle": None,
    }
    result["research_fingerprint"] = _json_sha({
        "protocol": result["protocol_fingerprint"],
        "frame": result["dataset"]["player_frame_fingerprint"],
        "predictions": _prediction_fingerprint(predictions),
        "winner": predictive_winner,
    })
    if gate["passed"]:
        result["deployment_bundle"] = _package_research_winner(
            result, bundle_root=bundle_root
        )
    _write_json(research_root / "results.json", result)
    _write_json(result_path, result)
    _write_report(result, PROJECT_ROOT / "reports" / "phase9a_statline_game_environment.md")
    return result


def _minutes_selection_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "probabilistic_minutes_selected": candidate["probabilistic_minutes_selected"],
        "inner_fixed_minutes_crps": candidate["inner_fixed_minutes_crps"],
        "inner_probabilistic_minutes_crps": candidate["inner_probabilistic_minutes_crps"],
        "outer_point_metrics": candidate["minutes_metrics"],
        "outer_quantile_metrics": candidate["minutes_quantiles"],
        "outer_fixed_minutes_distribution": candidate["fixed_minutes_distribution"],
        "outer_probabilistic_minutes_distribution": candidate[
            "probabilistic_minutes_distribution"
        ],
    }


def _aggregate_architecture_results(
    predictions: pd.DataFrame,
    fold_results: Mapping[str, Any],
    inner_architecture: Mapping[str, list[tuple[np.ndarray, np.ndarray]]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    actual = predictions["actual_fp"].to_numpy(float)
    for name in ARCHITECTURES:
        expected_column = f"{name}__expected_fp"
        summary_columns = {
            column.split("__", 1)[1]: predictions[column].to_numpy()
            for column in predictions.columns if column.startswith(f"{name}__")
            and column.split("__", 1)[1] != "expected_minutes"
        }
        summary = pd.DataFrame(summary_columns)
        distribution = distribution_metrics(actual, summary, None)
        fold_distribution = [
            fold["architectures"][name]["distribution"]
            for fold in fold_results.values()
        ]
        weights = [fold["rows"] for fold in fold_results.values()]
        distribution["crps"] = float(np.average(
            [row["crps"] for row in fold_distribution], weights=weights
        ))
        inner_actual = np.concatenate([pair[0] for pair in inner_architecture[name]])
        inner_prediction = np.concatenate([pair[1] for pair in inner_architecture[name]])
        output[name] = {
            "inner_selection_metrics": regression_metrics(inner_actual, inner_prediction),
            "outer_central": regression_metrics(actual, predictions[expected_column]),
            "outer_distribution": distribution,
            "outer_ranking": _ranking_metrics(predictions, expected_column),
            "by_fold": {
                fold_id: values["architectures"][name]
                for fold_id, values in fold_results.items()
            },
        }
    return output


def _freeze_gate(
    selected: str,
    architecture: Mapping[str, Any],
    folds: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = architecture["A_phase6c_frozen"]
    challenger = architecture[selected]
    fold_wins = {
        fold_id: (
            values["architectures"][selected]["central"]["mae"]
            < values["architectures"]["A_phase6c_frozen"]["central"]["mae"]
        )
        for fold_id, values in folds.items()
    }
    checks = {
        "challenger_selected": selected != "A_phase6c_frozen",
        "pooled_mae_improved": (
            challenger["outer_central"]["mae"] < baseline["outer_central"]["mae"]
        ),
        "pooled_rmse_not_degraded": (
            challenger["outer_central"]["rmse"] <= baseline["outer_central"]["rmse"]
        ),
        "pooled_crps_not_degraded": (
            challenger["outer_distribution"]["crps"]
            <= baseline["outer_distribution"]["crps"]
        ),
        "bias_guard": abs(challenger["outer_central"]["bias_actual_minus_prediction"]) <= 0.25,
        "all_outer_fold_mae_wins": all(fold_wins.values()),
    }
    return {
        "passed": bool(all(checks.values())), "selected_architecture": selected,
        "checks": checks, "fold_mae_wins": fold_wins,
        "rule": (
            "architecture selected only on chronological inner predictions; "
            "freeze requires pooled MAE improvement, no RMSE/CRPS degradation, "
            "|bias|<=0.25, and an MAE win in every outer season"
        ),
    }


def _add_winner_interface(
    predictions: pd.DataFrame, architecture: str, predictive_winner: str,
) -> pd.DataFrame:
    interface_columns = (
        "expected_fp", "median_fp", "p10_fp", "p25_fp", "p50_fp", "p75_fp",
        "p90_fp", "p95_fp", "distribution_width", "p90_minus_p50",
        "p50_minus_p10", "p95_minus_expected",
        *[f"prob_fp_ge_{int(value)}" for value in UPSIDE_THRESHOLDS],
        *[f"prob_fp_le_{int(value)}" for value in DOWNSIDE_THRESHOLDS],
    )
    interface = pd.DataFrame({
        column: predictions[f"{architecture}__{column}"].to_numpy()
        for column in interface_columns
    })
    if architecture in ("B_statline", "C_environment_statline"):
        interface["expected_minutes"] = predictions[
            f"{architecture}__expected_minutes"
        ].to_numpy()
    else:
        interface["expected_minutes"] = predictions["phase4b_expected_minutes"].to_numpy()
    interface["predictive_model_version"] = predictive_winner
    interface["statline_architecture"] = architecture
    return pd.concat([predictions.reset_index(drop=True), interface], axis=1)


def _minutes_results(
    minutes: pd.DataFrame, fold_results: Mapping[str, Any],
) -> dict[str, Any]:
    actual = minutes["target_minutes"].to_numpy(float)
    existing = minutes["phase4b_expected_minutes"].to_numpy(float)
    challenger = minutes["challenger_expected_minutes"].to_numpy(float)
    quantiles = {}
    for quantile in (0.10, 0.50, 0.90):
        prediction = minutes[f"challenger_minutes_p{int(quantile * 100):02d}"].to_numpy(float)
        residual = actual - prediction
        quantiles[f"p{int(quantile * 100):02d}"] = {
            "pinball_loss": float(np.mean(np.maximum(
                quantile * residual, (quantile - 1.0) * residual
            ))),
            "coverage": float(np.mean(actual <= prediction)),
            "coverage_error": float(np.mean(actual <= prediction) - quantile),
        }
    weights = [values["rows"] for values in fold_results.values()]
    fixed_crps = np.average([
        values["minutes_sampling"]["C_environment_statline"]
        ["outer_fixed_minutes_distribution"]["crps"]
        for values in fold_results.values()
    ], weights=weights)
    probabilistic_crps = np.average([
        values["minutes_sampling"]["C_environment_statline"]
        ["outer_probabilistic_minutes_distribution"]["crps"]
        for values in fold_results.values()
    ], weights=weights)
    by_fold = {}
    if "outer_fold" in minutes:
        for fold_id, group in minutes.groupby("outer_fold"):
            by_fold[str(fold_id)] = {
                "existing_phase4b_point": regression_metrics(
                    group["target_minutes"], group["phase4b_expected_minutes"]
                ),
                "challenger_point": regression_metrics(
                    group["target_minutes"], group["challenger_expected_minutes"]
                ),
            }
    return {
        "existing_phase4b_point": regression_metrics(actual, existing),
        "challenger_point": regression_metrics(actual, challenger),
        "by_fold": by_fold,
        "probabilistic_quantiles": quantiles,
        "fixed_minutes_fp_distribution_crps": float(fixed_crps),
        "probabilistic_minutes_fp_distribution_crps": float(probabilistic_crps),
        "probabilistic_minutes_helped_fp_crps": bool(probabilistic_crps < fixed_crps),
        "selected_in_outer_folds": int(sum(
            values["minutes_sampling"]["C_environment_statline"]
            ["probabilistic_minutes_selected"]
            for values in fold_results.values()
        )),
    }


def refresh_saved_phase9a_diagnostics(
    *,
    research_root: Path = DEFAULT_RESEARCH_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> dict[str, Any]:
    """Refresh reporting-only diagnostics without fitting or changing predictions."""

    research_result_path = research_root / "results.json"
    sample_result_path = sample_root / "results.json"
    result = json.loads(research_result_path.read_text(encoding="utf-8"))
    with duckdb.connect() as connection:
        predictions = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(research_root / "outer_predictions.parquet")]
        ).df()
        minutes = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(research_root / "minutes_predictions.parquet")]
        ).df()
    if "outer_fold" not in minutes:
        minutes = minutes.merge(
            predictions[["model_row_id", "outer_fold"]],
            on="model_row_id", how="left", validate="one_to_one",
        )
    if minutes["outer_fold"].isna().any():
        raise ValueError("minute diagnostics lack outer-fold identity")
    selected = str(result["selected_on_inner_validation"])
    result["segments"] = _segment_metrics(predictions, selected)
    result["minutes_distribution"] = _minutes_results(
        minutes, result["outer_folds"]
    )
    _write_parquet(minutes, research_root / "minutes_predictions.parquet")
    _write_json(research_result_path, result)
    _write_json(sample_result_path, result)
    _write_report(result, PROJECT_ROOT / "reports" / "phase9a_statline_game_environment.md")
    return result


def _component_accuracy(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.copy()
    work["total_rebounds"] = work["offensive_rebounds"] + work["defensive_rebounds"]
    components = [
        "minutes", "points", "two_points_made", "two_points_attempted",
        "three_points_made", "three_points_attempted", "free_throws_made",
        "free_throws_attempted", "offensive_rebounds", "defensive_rebounds",
        "total_rebounds", "assists", "steals", "blocks", "turnovers",
        "fouls_drawn", "fouls_committed", "blocks_received",
    ]
    actual_name = {"minutes": "target_minutes"}
    metrics = {}
    for component in components:
        actual_column = actual_name.get(component, component)
        predicted_column = f"predicted_{component}"
        metrics[component] = regression_metrics(
            work[actual_column], work[predicted_column]
        )
    coefficients = {
        "two_points_made": 3.0, "two_points_attempted": -1.0,
        "three_points_made": 4.0, "three_points_attempted": -1.0,
        "free_throws_made": 2.0, "free_throws_attempted": -1.0,
        "offensive_rebounds": 1.0, "defensive_rebounds": 1.0,
        "assists": 1.0, "steals": 1.0, "blocks": 1.0,
        "turnovers": -1.0, "fouls_drawn": 1.0,
        "fouls_committed": -1.0, "blocks_received": -1.0,
    }
    final_error = work["actual_fantasy_points"] - work["predicted_fp"]
    contributions = []
    for component, coefficient in coefficients.items():
        contribution = coefficient * (
            work[component] - work[f"predicted_{component}"]
        )
        correlation = pearsonr(contribution, final_error).statistic
        contributions.append({
            "component": component, "scoring_coefficient": coefficient,
            "mean_absolute_fp_error_contribution": float(np.mean(np.abs(contribution))),
            "mean_signed_fp_error_contribution": float(np.mean(contribution)),
            "correlation_with_final_fp_error": float(correlation),
        })
    contributions.sort(
        key=lambda row: row["mean_absolute_fp_error_contribution"], reverse=True
    )
    minute_error = work["target_minutes"] - work["predicted_minutes"]
    return {
        "metrics": metrics, "fp_error_contributions": contributions,
        "minutes_error_correlation_with_final_fp_error": float(
            pearsonr(minute_error, final_error).statistic
        ),
        "largest_bottlenecks": [row["component"] for row in contributions[:5]],
    }


def _interaction_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "folds": list(rows),
        "weighted_mae_improvement": float(np.average(
            [row["mae_improvement"] for row in rows],
            weights=[row["rows"] for row in rows],
        )),
        "folds_helped": int(sum(row["mae_improvement"] > 0 for row in rows)),
        "interpretation": "learned production-profile interactions; no position rules",
    }


def _segment_metrics(predictions: pd.DataFrame, selected: str) -> dict[str, Any]:
    work = predictions.copy()
    work["expected_minutes_band"] = pd.cut(
        work["phase4b_expected_minutes"],
        [-np.inf, 10, 20, 25, 30, np.inf],
        labels=["under_10", "10_20", "20_25", "25_30", "30_plus"],
        right=False,
    ).astype("string").fillna("unknown")
    work["expected_fp_band"] = pd.cut(
        work["A_phase6c_frozen__expected_fp"],
        [-np.inf, 5, 10, 15, 20, 25, np.inf],
        labels=["under_5", "5_10", "10_15", "15_20", "20_25", "25_plus"],
        right=False,
    ).astype("string").fillna("unknown")
    thresholds = {
        "high_usage": work["expected_usage_next_game"].quantile(0.75),
        "rebound_heavy": work["rebounds_per_min"].quantile(0.75),
        "creator_heavy": work["assists_per_min"].quantile(0.75),
        "high_volatility": work["season_fp_std_before"].quantile(0.75),
    }
    work["profile_segment"] = "other"
    for name, threshold in thresholds.items():
        source = {
            "high_usage": "expected_usage_next_game",
            "rebound_heavy": "rebounds_per_min",
            "creator_heavy": "assists_per_min",
            "high_volatility": "season_fp_std_before",
        }[name]
        work.loc[pd.to_numeric(work[source], errors="coerce") >= threshold, "profile_segment"] = name
    work.loc[work["number_players_out"] > 0, "absence_segment"] = "known_absence"
    work.loc[work["number_players_out"] <= 0, "absence_segment"] = "no_known_absence"
    compared = list(dict.fromkeys((
        "A_phase6c_frozen", "B_statline", "C_environment_statline", selected,
    )))

    def comparison(group: pd.DataFrame) -> dict[str, Any]:
        rows = {
            name: regression_metrics(group["actual_fp"], group[f"{name}__expected_fp"])
            for name in compared
        }
        baseline_mae = rows["A_phase6c_frozen"]["mae"]
        return {
            "architectures": rows,
            "mae_improvement_vs_phase6c": {
                name: baseline_mae - metric["mae"] for name, metric in rows.items()
            },
        }

    output = {}
    for grouping in (
        "expected_minutes_band", "expected_fp_band", "p6c_player_archetype",
        "profile_segment", "absence_segment",
    ):
        rows = []
        for value, group in work.groupby(grouping, dropna=False):
            if len(group) < 30:
                continue
            rows.append({
                "segment": str(value), "rows": len(group),
                **comparison(group),
            })
        output[grouping] = rows
    profile_rows = []
    for name, threshold in thresholds.items():
        source = {
            "high_usage": "expected_usage_next_game",
            "rebound_heavy": "rebounds_per_min",
            "creator_heavy": "assists_per_min",
            "high_volatility": "season_fp_std_before",
        }[name]
        group = work[pd.to_numeric(work[source], errors="coerce") >= threshold]
        profile_rows.append({
            "segment": name, "rows": len(group), "threshold": float(threshold),
            **comparison(group),
        })
    output["production_profiles"] = profile_rows
    return output


def _explosion_analysis(
    predictions: pd.DataFrame, components: pd.DataFrame,
) -> dict[str, Any]:
    joined = predictions.merge(
        components.drop(columns=[
            "actual_fantasy_points", "predicted_fp", "target_minutes",
        ]),
        on="model_row_id", how="left", validate="one_to_one",
    )
    coefficients = {
        "two_points_made": 3.0, "two_points_attempted": -1.0,
        "three_points_made": 4.0, "three_points_attempted": -1.0,
        "free_throws_made": 2.0, "free_throws_attempted": -1.0,
        "offensive_rebounds": 1.0, "defensive_rebounds": 1.0,
        "assists": 1.0, "steals": 1.0, "blocks": 1.0,
        "turnovers": -1.0, "fouls_drawn": 1.0,
        "fouls_committed": -1.0, "blocks_received": -1.0,
    }
    output: dict[str, Any] = {}
    for threshold in (30, 35, 40):
        group = joined[joined["actual_fp"] >= threshold].copy()
        missed_components = []
        for component, coefficient in coefficients.items():
            underprediction = coefficient * (
                group[component] - group[f"predicted_{component}"]
            )
            missed_components.append({
                "component": component,
                "mean_signed_fp_shortfall": float(underprediction.mean()),
                "mean_positive_fp_shortfall": float(underprediction.clip(lower=0).mean()),
            })
        minute_gap = group["target_minutes"] - group["predicted_minutes"]
        missed_components.append({
            "component": "minutes",
            "mean_signed_fp_shortfall": None,
            "mean_positive_fp_shortfall": None,
            "mean_minutes_underprediction": float(minute_gap.mean()),
        })
        missed_components.sort(
            key=lambda row: row.get("mean_positive_fp_shortfall") or -np.inf,
            reverse=True,
        )
        output[str(threshold)] = {
            "rows": len(group),
            "phase6c_mean_central_shortfall": float((
                group["actual_fp"] - group["A_phase6c_frozen__expected_fp"]
            ).mean()),
            "statline_mean_central_shortfall": float((
                group["actual_fp"] - group["C_environment_statline__expected_fp"]
            ).mean()),
            "phase6c_mean_p90_shortfall": float((
                group["actual_fp"] - group["A_phase6c_frozen__p90_fp"]
            ).clip(lower=0).mean()),
            "statline_mean_p90_shortfall": float((
                group["actual_fp"] - group["C_environment_statline__p90_fp"]
            ).clip(lower=0).mean()),
            "phase6c_mean_p95_shortfall": float((
                group["actual_fp"] - group["A_phase6c_frozen__p95_fp"]
            ).clip(lower=0).mean()),
            "statline_mean_p95_shortfall": float((
                group["actual_fp"] - group["C_environment_statline__p95_fp"]
            ).clip(lower=0).mean()),
            "phase6c_mean_predicted_probability": float(
                group[f"A_phase6c_frozen__prob_fp_ge_{threshold}"].mean()
            ),
            "statline_mean_predicted_probability": float(
                group[f"C_environment_statline__prob_fp_ge_{threshold}"].mean()
            ),
            "most_common_missed_components": missed_components[:6],
            "mean_minutes_underprediction": float(minute_gap.mean()),
        }
    return output


def _phase7_interface_check(predictions: pd.DataFrame) -> dict[str, Any]:
    required = [
        "expected_fp", "median_fp", "p10_fp", "p25_fp", "p50_fp",
        "p75_fp", "p90_fp", "p95_fp", "expected_minutes",
        *[f"prob_fp_ge_{int(value)}" for value in UPSIDE_THRESHOLDS],
        *[f"prob_fp_le_{int(value)}" for value in DOWNSIDE_THRESHOLDS],
        "predictive_model_version",
    ]
    missing = sorted(set(required) - set(predictions.columns))
    finite_columns = [column for column in required if column != "predictive_model_version"]
    finite = bool(
        not missing
        and np.isfinite(predictions[finite_columns].to_numpy(float)).all()
    )
    ordered = bool(
        finite
        and (predictions[[f"p{int(q * 100):02d}_fp" for q in QUANTILES]]
             .to_numpy(float)[:, 1:] >=
             predictions[[f"p{int(q * 100):02d}_fp" for q in QUANTILES]]
             .to_numpy(float)[:, :-1]).all()
    )
    return {
        "passed": bool(not missing and finite and ordered),
        "missing_columns": missing, "finite": finite,
        "quantiles_ordered": ordered, "required_columns": required,
        "phase7_retrained": False,
    }


def _package_research_winner(
    result: Mapping[str, Any], *, bundle_root: Path,
) -> dict[str, Any]:
    """Create an immutable gate record; executable integration is added only if reached."""

    bundle_root.mkdir(parents=True, exist_ok=True)
    protocol_path = bundle_root / "protocol.json"
    _write_json(protocol_path, protocol_payload())
    payload = {
        "status": "FROZEN", "bundle_identifier": PHASE9A_MODEL_VERSION,
        "protocol_version": PHASE9A_PROTOCOL_VERSION,
        "protocol_fingerprint": protocol_fingerprint(),
        "selected_architecture": result["selected_on_inner_validation"],
        "research_fingerprint": result["research_fingerprint"],
        "phase7_interface_passed": result["phase7_interface"]["passed"],
        "phase7_retrained": False,
        "phase6c_phase7_untouched": result["frozen_artifacts"]["phase6c_phase7_untouched"],
        "files": {"protocol.json": _file_sha(protocol_path)},
    }
    payload["bundle_fingerprint"] = _json_sha(payload)
    manifest = bundle_root / "manifest.json"
    _write_json(manifest, payload)
    return {
        "bundle_identifier": PHASE9A_MODEL_VERSION,
        "bundle_fingerprint": payload["bundle_fingerprint"],
        "manifest": str(manifest.relative_to(PROJECT_ROOT)),
    }


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    columns = [
        "model_row_id", "actual_fantasy_points", "target_minutes",
        *MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS,
    ]
    work = frame.sort_values("model_row_id")[columns]
    return hashlib.sha256(
        pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    ).hexdigest()


def _prediction_fingerprint(frame: pd.DataFrame) -> str:
    columns = [
        "model_row_id", "outer_fold", "expected_fp", "p90_fp", "p95_fp",
        *[f"prob_fp_ge_{int(value)}" for value in UPSIDE_THRESHOLDS],
        *[f"prob_fp_le_{int(value)}" for value in DOWNSIDE_THRESHOLDS],
        "predictive_model_version",
    ]
    work = frame.sort_values(["outer_fold", "model_row_id"])[columns]
    return hashlib.sha256(
        pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    ).hexdigest()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.register("_phase9a_output", frame)
        connection.execute(
            "COPY _phase9a_output TO ? (FORMAT PARQUET,COMPRESSION ZSTD)", [str(path)]
        )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    raise TypeError(type(value).__name__)


def _write_report(result: Mapping[str, Any], path: Path) -> None:
    architecture = result["architectures"]
    baseline = architecture["A_phase6c_frozen"]
    selected = architecture[result["selected_on_inner_validation"]]
    environment = result["game_environment"]
    predictable = [
        name for name, values in environment["target_metrics"].items()
        if values["predictable"]
    ]
    failed = [
        name for name, values in environment["target_metrics"].items()
        if not values["predictable"]
    ]
    component = result["component_accuracy"]["C_environment_statline"]
    lines = [
        "# Phase 9A — Stat-Line & Game-Environment Challenger",
        "",
        f"Status: **{result['status']}**. Predictive winner: "
        f"`{result['predictive_winner']}`. Phase 7 was not retrained.",
        "",
        "## Decision",
        "",
        f"The architecture selected exclusively on chronological inner validation was "
        f"`{result['selected_on_inner_validation']}`. The strict outer freeze gate "
        f"{'passed' if result['freeze_gate']['passed'] else 'did not pass'}.",
        "",
        "| Architecture | MAE | RMSE | Pearson | Spearman | CRPS |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ARCHITECTURES:
        central = architecture[name]["outer_central"]
        crps = architecture[name]["outer_distribution"]["crps"]
        lines.append(
            f"| {name} | {central['mae']:.6f} | {central['rmse']:.6f} | "
            f"{central['pearson']:.6f} | {central['spearman']:.6f} | {crps:.6f} |"
        )
    lines.extend([
        "",
        "## Game environment",
        "",
        "Predictable versus the strictly trailing team/opponent baseline: "
        + (", ".join(predictable) if predictable else "none") + ".",
        "",
        "Not predictably improved: " + (", ".join(failed) if failed else "none") + ".",
        "",
        "The evaluated PBP families were transition/after-turnover flags, "
        "second-chance flags, and validated shot-zone mix. True early-clock timing, "
        "possession length, named tactics, and defender assignments were not invented.",
        "",
        "## Minutes and stat components",
        "",
        f"Existing Phase 4B minutes MAE: "
        f"{result['minutes_distribution']['existing_phase4b_point']['mae']:.6f}. "
        f"Challenger minutes MAE: "
        f"{result['minutes_distribution']['challenger_point']['mae']:.6f}.",
        "",
        "Largest Fantasy-error bottlenecks: "
        + ", ".join(component["largest_bottlenecks"]) + ".",
        "",
        "## Safety and compatibility",
        "",
        f"Phase 7 interface compatibility: {result['phase7_interface']['passed']}. "
        f"Frozen Phase 6C/7 artifacts untouched: "
        f"{result['frozen_artifacts']['phase6c_phase7_untouched']}. "
        "No planner work was started.",
        "",
        "## Gate detail",
        "",
        "```json",
        json.dumps(result["freeze_gate"], indent=2, sort_keys=True),
        "```",
        "",
        "Selected-versus-baseline pooled central metrics:",
        "",
        "```json",
        json.dumps({
            "phase6c": baseline["outer_central"],
            "inner_selected": selected["outer_central"],
        }, indent=2, sort_keys=True),
        "```",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
