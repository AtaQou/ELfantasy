"""Markdown report rendering for measured Phase 4B artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_phase4b_reports(summary: dict[str, Any], *, report_root: Path) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / "phase4b_minutes_model.md").write_text(
        minutes_report(summary), encoding="utf-8"
    )
    (report_root / "phase4b_decomposed_model.md").write_text(
        decomposed_report(summary), encoding="utf-8"
    )
    (report_root / "phase4b_calibration_uncertainty.md").write_text(
        calibration_report(summary), encoding="utf-8"
    )
    (report_root / "phase4b_final_model_comparison.md").write_text(
        final_report(summary), encoding="utf-8"
    )


def minutes_report(summary: dict[str, Any]) -> str:
    selected = summary["selected_minutes_diagnostics"]
    candidates = summary["minutes_candidates"]
    role = summary["selected_minutes_role_change_metrics"]
    history = summary["selected_minutes_history_metrics"]
    features = summary["strongest_minutes_features"]
    lines = [
        "# Phase 4B conditional expected-minutes model",
        "",
        "All results are conditional on the player actually playing. Target-game "
        "minutes, starter status, active lineups, substitutions, and post-tip data "
        "are labels or diagnostics only and are never model inputs.",
        "",
        "## Model definitions and selection",
        "",
        "The targeted ROLE manifest contains lagged minutes, starter frequency, "
        "rotation/stint/closing-lineup history, role movement, history depth, and "
        "team/opponent context. Player identity is excluded. Ridge, CatBoost, and "
        "XGBoost use the frozen chronological folds; the selected component is the "
        "lowest pooled inner-validation minutes MAE, not the best outer result.",
        "",
        table(
            ["Candidate", "Inner MAE", "Outer MAE", "RMSE", "Pearson", "Spearman", "Bias"],
            [[
                row["experiment_id"], f4(row["inner_selection_mae"]), f4(row["mae"]),
                f4(row["rmse"]), f4(row["pearson"]), f4(row["spearman"]),
                f4(row["mean_residual"]),
            ] for row in candidates],
        ),
        "",
        f"Selected: `{summary['selected_minutes_experiment_id']}` with MAE "
        f"**{f4(selected['mae'])} minutes** and RMSE **{f4(selected['rmse'])}**.",
        "",
        "## Actual-minute bands",
        "",
        metric_table(selected["actual_minutes_bands"], "minutes_band"),
        "",
        "Maximum actual minutes were " + f4(selected["maximum_actual_minutes"]) +
        "; maximum predicted minutes were " + f4(selected["maximum_predicted_minutes"]) +
        ". The predicted-bin table below exposes any compression directly.",
        "",
        metric_table(selected["predicted_minutes_bins"], "predicted_minutes_bin"),
        "",
        "## Role change and history",
        "",
        "Role-change score is the mean minute-equivalent movement across lagged "
        "minutes EWMA vs season average, core/rotation trends, recent-vs-season "
        "rotation share, starter-rate change, and recent rotation volatility. "
        "Stable/moderate/large thresholds are the 60th/85th percentiles fitted on "
        "each fold's historical training rows only.",
        "",
        metric_table(role, "role_change_class"),
        "",
        metric_table(history, "history_group"),
        "",
        "## Strongest minute predictors",
        "",
        table(
            ["Feature", "Mean normalized importance"],
            [[row["raw_feature"], f4(row["normalized_importance"])] for row in features],
        ),
        "",
        "## Competition-stage ablation",
        "",
        ablation_sentence(summary["competition_stage"]["minutes_stage_ablation"]),
        "The authoritative source is explicit `games.phase_code`; Final Four "
        "sub-stages use known schedule order. Playoff series state uses only "
        "completed earlier games in the same series.",
    ]
    return "\n".join(lines) + "\n"


def decomposed_report(summary: dict[str, Any]) -> str:
    architecture = architecture_lookup(summary)
    decomposed = architecture["best_decomposed"]
    direct = architecture["Phase4A_direct"]
    oracle = summary["oracle_diagnostics"]
    lines = [
        "# Phase 4B decomposed Fantasy model",
        "",
        "## FP/min target and stabilization",
        "",
        "Raw production is `actual_fantasy_points / actual_minutes`; the denominator "
        "must be finite and strictly positive. Minute-weighted modelling uses "
        "`actual_minutes / training_mean_minutes` as the training weight. The "
        "stabilized alternative is `(FP + k × prior_rate) / (minutes + k)`, where "
        "`prior_rate = sum(training FP) / sum(training minutes)` and `k ∈ {5,10}` "
        "is selected on inner validation. Every prior is refitted inside the "
        "historical fold. No FP/min value is capped.",
        "",
        "Actual target-game minutes define the historical production label only. "
        "They are not inference features. The production model does not consume "
        "minutes, so there is no actual-minutes/predicted-minutes train/inference "
        "mismatch; decomposition multiplies two independently out-of-sample outputs.",
        "",
        "## Production candidates",
        "",
        table(
            ["Candidate", "Strategy", "Inner decomp MAE", "Rate MAE", "Decomp MAE", "Decomp RMSE"],
            [[
                row["experiment_id"], row["target_strategy"],
                f4(row["inner_decomposed_mae"]), f4(row["outer_rate_mae"]),
                f4(row["outer_decomposed_mae"]), f4(row["outer_decomposed_rmse"]),
            ] for row in summary["production_candidates"]],
        ),
        "",
        f"Selected from inner validation: `{summary['selected_production_experiment_id']}`.",
        "",
        "## Direct versus decomposed",
        "",
        table(
            ["Architecture", "MAE", "RMSE", "Spearman", "Pearson", "Bias", "Max prediction"],
            [["Frozen direct", *common_metric_cells(direct)],
             ["Selected decomposed", *common_metric_cells(decomposed)]],
        ),
        "",
        "Direct/decomposed genuine outer residual Pearson correlation: "
        f"**{f4(summary['direct_decomposed_residual_correlation'])}**.",
        "",
        "## ORACLE / DIAGNOSTIC ONLY",
        "",
        "These rows are not predictive models and are excluded from leaderboards.",
        "",
        table(
            ["Diagnostic", "MAE", "RMSE", "Meaning"],
            [[
                "Actual minutes × predicted rate",
                f4(oracle["oracle_minutes_diagnostic_only"]["mae"]),
                f4(oracle["oracle_minutes_diagnostic_only"]["rmse"]),
                "error remaining with perfect minutes",
            ], [
                "Predicted minutes × actual rate",
                f4(oracle["oracle_production_diagnostic_only"]["mae"]),
                f4(oracle["oracle_production_diagnostic_only"]["rmse"]),
                "error remaining with perfect production",
            ]],
        ),
        "",
        f"Minutes-error/FP-error Pearson: {f4(oracle['minutes_error_fp_error_pearson'])}; "
        f"production-rate-error/FP-error Pearson: "
        f"{f4(oracle['production_rate_error_fp_error_pearson'])}.",
        "",
        "## Targeted ablations",
        "",
        "Shot profile: " + ablation_sentence(summary["controlled_ablations"]["shot_profile"]),
        "Older standardized history for production: " + ablation_sentence(
            summary["controlled_ablations"]["production_older_history"]
        ),
    ]
    return "\n".join(lines) + "\n"


def calibration_report(summary: dict[str, Any]) -> str:
    architecture = architecture_lookup(summary)
    losses = summary["direct_loss_selection"]
    calibration = summary["calibration"]
    uncertainty = summary["uncertainty"]
    lines = [
        "# Phase 4B calibration and uncertainty",
        "",
        "## Upper-tail compression and bias",
        "",
        table(
            ["Architecture", "MAE", "RMSE", "Bias actual-pred", "Max prediction", "Max actual"],
            [[
                name, f4(row["mae"]), f4(row["rmse"]), f4(row["mean_residual"]),
                f4(row["maximum_prediction"]), f4(row["maximum_actual"]),
            ] for name, row in architecture.items()],
        ),
        "",
        "Actual-score bands and predicted-score calibration bins for every serious "
        "architecture are retained in `data/samples/phase4b/results.json`, including "
        "25-30 and 30+ outcomes.",
        "",
        table(
            ["Architecture", ">=25 rows", ">=25 MAE", ">=25 mean pred", ">=30 rows", ">=30 MAE", ">=30 mean pred"],
            [[
                name,
                summary["architecture_diagnostics"][name]["high_score_metrics"]["25_plus"]["rows"],
                f4(summary["architecture_diagnostics"][name]["high_score_metrics"]["25_plus"]["mae"]),
                f4(summary["architecture_diagnostics"][name]["high_score_metrics"]["25_plus"]["mean_prediction"]),
                summary["architecture_diagnostics"][name]["high_score_metrics"]["30_plus"]["rows"],
                f4(summary["architecture_diagnostics"][name]["high_score_metrics"]["30_plus"]["mae"]),
                f4(summary["architecture_diagnostics"][name]["high_score_metrics"]["30_plus"]["mean_prediction"]),
            ] for name in ("Phase4A_direct", "best_decomposed", "hybrid")],
        ),
        "",
        "## Alternative direct losses",
        "",
        f"Inner validation selected **{losses['selected_loss']}**. Outer-test results "
        "were not used for this choice.",
        "",
        table(
            ["Loss", "Inner MAE"],
            [[loss, f4(value)] for loss, value in losses["inner_mae_by_loss"].items()],
        ),
        "",
        table(
            ["Architecture", "MAE", "RMSE", "Bias", "Max prediction"],
            [[
                name, f4(row["mae"]), f4(row["rmse"]), f4(row["mean_residual"]),
                f4(row["maximum_prediction"]),
            ] for name, row in losses["outer_metrics"].items()],
        ),
        "",
        "## Calibration",
        "",
        f"Direct calibration selected `{calibration['direct']['selected_method']}`; "
        f"hybrid calibration selected `{calibration['hybrid']['selected_method']}`. "
        "Method choice uses the later half of chronological inner validation after "
        "fitting on its earlier half; fold parameters are then refit on full inner "
        "validation. No outer target fits or selects calibration.",
        "",
        table(
            ["Direct calibration method", "Outer MAE", "RMSE", "Bias", "Spearman"],
            [[
                method, f4(row["mae"]), f4(row["rmse"]),
                f4(row["mean_residual"]), f4(row["spearman"]),
            ] for method, row in calibration["direct"][
                "outer_metrics_by_method"
            ].items()],
        ),
        "",
        "## Prediction intervals",
        "",
        "The interval is an 80% split-conformal absolute-residual interval fitted "
        "on chronological inner predictions. Radii are conditional on stable, "
        "moderate, or large historical role change when at least 100 calibration "
        "rows are available; otherwise the fold-global radius is used. Outputs are "
        "`expected_fp`, `floor_fp`/`p10_fp`, and `ceiling_fp`/`p90_fp`.",
        "",
        table(
            ["Base architecture", "Coverage", "Mean width", "Median width"],
            [[
                name, pct(result["evaluation"]["overall"]["coverage"]),
                f4(result["evaluation"]["overall"]["mean_width"]),
                f4(result["evaluation"]["overall"]["median_width"]),
            ] for name, result in uncertainty.items()],
        ),
        "",
        "Coverage by season, minutes band, role-change class, and 25+ actual "
        "performances is retained in the result artifact. Empirical coverage is "
        "reported rather than assumed to equal the nominal level.",
    ]
    return "\n".join(lines) + "\n"


def final_report(summary: dict[str, Any]) -> str:
    architecture = architecture_lookup(summary)
    baseline = summary["phase3_same_row_baseline_metrics"]
    final = summary["final_selection"]
    requested = [
        ("Phase 3 same-row blend", baseline),
        ("Phase 4A direct winner", architecture["Phase4A_direct"]),
        ("Best decomposed", architecture["best_decomposed"]),
        ("Best calibrated direct", architecture["best_calibrated_direct"]),
        ("Best hybrid", architecture["hybrid"]),
    ]
    lines = [
        "# Phase 4B final conditional performance model comparison",
        "",
        "All rows use the identical 22,399-player-game E2023-E2025 chronological "
        "outer universe and remain conditional on playing.",
        "",
        table(
            ["Model", "MAE", "RMSE", "Spearman", "Pearson", "Bias", "Top10/20", "Top20/30"],
            [[
                name, f4(row["mae"]), f4(row["rmse"]), f4(row["spearman"]),
                f4(row["pearson"]), f4(row["mean_residual"]),
                pct(row["top10_actual_in_predicted_top20"]),
                pct(row["top20_actual_in_predicted_top30"]),
            ] for name, row in requested],
        ),
        "",
        "## Frozen decision",
        "",
        f"**{final['architecture']}** is frozen as "
        f"`{final['final_performance_model_version']}`. {final['reason']}. Its MAE "
        f"is **{f4(final['metrics']['mae'])}**, an improvement of "
        f"**{pct(final['improvement_vs_phase4a_percent'] / 100.0)}** versus Phase "
        f"4A and **{pct(final['improvement_vs_same_row_baseline_percent'] / 100.0)}** "
        "versus the original same-row blend.",
        "",
        "The exact component manifests, fold hyperparameters, calibration "
        "parameters, interval method, prediction protocol, and fingerprint are in "
        "`data/samples/phase4b/final_model.json`.",
        "",
        "## Fold stability",
        "",
        metric_table(
            summary["architecture_diagnostics"][final["architecture"]]["fold_metrics"],
            "outer_fold",
        ),
        "",
        "## Competition stage",
        "",
        table(
            ["Stage", "Rows", "Mean FP", "Mean minutes", "Role-change score", "MAE", "Bias"],
            [[
                row["competition_stage"], row["rows"], f4(row["mean_actual_fp"]),
                f4(row["mean_minutes"]), f4(row["mean_role_change_score"]),
                f4(row["mae"]), f4(row["mean_residual"]),
            ] for row in summary["competition_stage"][
                "selected_model_stage_diagnostics"
            ]],
        ),
        "",
        "## High-minute architecture comparison",
        "",
        table(
            ["Model", "Actual minutes", "Rows", "MAE", "RMSE", "Bias"],
            [[
                row["model"], row["minutes_band"], row["rows"],
                f4(row["mae"]), f4(row["rmse"]), f4(row["mean_residual"]),
            ] for row in summary["high_minute_architecture_comparison"]],
        ),
        "",
        "No Phase 4A result was altered retroactively. Phase 4B stops here; "
        "availability/participation, optimization, simulation, and frontend work "
        "remain out of scope.",
    ]
    return "\n".join(lines) + "\n"


def architecture_lookup(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        row["architecture"]: row for row in summary["architectures"]
    }


def common_metric_cells(row: dict[str, Any]) -> list[str]:
    return [
        f4(row["mae"]), f4(row["rmse"]), f4(row["spearman"]),
        f4(row["pearson"]), f4(row["mean_residual"]),
        f4(row["maximum_prediction"]),
    ]


def metric_table(rows: list[dict[str, Any]], label: str) -> str:
    return table(
        [label, "Rows", "MAE", "RMSE", "Pearson", "Spearman", "Bias"],
        [[
            row[label], str(row["rows"]), f4(row["mae"]), f4(row["rmse"]),
            f4(row["pearson"]), f4(row["spearman"]), f4(row["mean_residual"]),
        ] for row in rows],
    )


def ablation_sentence(result: dict[str, Any]) -> str:
    return (
        f"`{result['variant_experiment_id']}` changed the measured metric from "
        f"{f4(result['base_value'])} to {f4(result['variant_value'])} "
        f"(delta {result['delta_variant_minus_base']:+.4f})."
    )


def table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(map(str, headers)) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    return "\n".join([head, separator, *body])


def f4(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}"


def pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.2f}%"
