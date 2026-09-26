"""Generate measured Phase 5B reports from frozen-protocol artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.modeling.ml_experiments import ranking_metrics


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DERIVED_ROOT = PROJECT_ROOT / "data" / "derived" / "phase5b"
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase5b"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "reports"


def generate_phase5b_reports(
    *,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> dict[str, Path]:
    summary = json.loads((sample_root / "results.json").read_text(encoding="utf-8"))
    normalized = _read(derived_root / "phase5b_normalized_team_rotations.parquet")
    events = _read(derived_root / "phase5b_absence_recipient_rows.parquet")
    predictions = _read(derived_root / "ml_phase5b_redistribution_predictions_v1.parquet")
    fantasy = _read(derived_root / "ml_phase5b_fantasy_impact_v1.parquet")
    if "before_ranking" not in summary["fantasy_impact"]:
        summary["fantasy_impact"]["before_ranking"] = ranking_metrics(
            fantasy.rename(columns={"frozen_fp": "predicted_fp"})
        )
        summary["fantasy_impact"]["after_ranking"] = ranking_metrics(
            fantasy.rename(columns={"adjusted_fp": "predicted_fp"})
        )
        (sample_root / "results.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    summary["extended_diagnostics"] = _extended_diagnostics(
        summary, normalized, predictions
    )
    (sample_root / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    report_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "absence_dataset": report_root / "phase5b_absence_dataset.md",
        "redistribution_model": report_root / "phase5b_redistribution_model.md",
        "fantasy_impact": report_root / "phase5b_fantasy_impact.md",
        "live_workflow": report_root / "phase5b_live_workflow.md",
    }
    paths["absence_dataset"].write_text(
        _absence_report(summary, normalized, events), encoding="utf-8"
    )
    paths["redistribution_model"].write_text(
        _redistribution_report(summary), encoding="utf-8"
    )
    paths["fantasy_impact"].write_text(
        _fantasy_report(summary, fantasy), encoding="utf-8"
    )
    paths["live_workflow"].write_text(_live_report(summary), encoding="utf-8")
    return paths


def _absence_report(
    summary: dict[str, Any], normalized: pd.DataFrame, events: pd.DataFrame,
) -> str:
    dataset = summary["dataset"]
    absences = normalized[normalized["rotation_absent"]].copy()
    team_games = absences.groupby(["game_id", "team_id"], as_index=False).agg(
        number_absent=("player_id", "size"),
        total_missing=("baseline_expected_minutes", "sum"),
        max_absent_role=("baseline_expected_minutes", "max"),
    )
    count_distribution = {
        "one": int(team_games["number_absent"].eq(1).sum()),
        "two": int(team_games["number_absent"].eq(2).sum()),
        "three_plus": int(team_games["number_absent"].ge(3).sum()),
    }
    missing = team_games["total_missing"].describe(
        percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]
    )
    low_role = absences.assign(low=lambda value: value.baseline_expected_minutes.lt(10)).groupby(
        ["game_id", "team_id"]
    ).agg(
        absent_count=("player_id", "size"),
        all_low=("low", "all"),
        missing=("baseline_expected_minutes", "sum"),
    )
    cumulative_low = int((
        low_role["absent_count"].ge(2) & low_role["all_low"] & low_role["missing"].ge(20)
    ).sum())
    normalized["team_game_key"] = (
        normalized["game_id"].astype(str) + "|" + normalized["team_id"].astype(str)
    )
    coverage = normalized.groupby("season").agg(
        team_games=("team_game_key", "nunique"),
        roster_rows=("player_id", "size"),
        zero_rows=("rotation_absent", "sum"),
    ).reset_index()
    return f"""# Phase 5B historical rotation-absence dataset

Generated at `{datetime.now(UTC).isoformat()}`. Historical zero-minute roster rows are
**rotation absences/non-participation**, not fabricated injury labels. Results are
`CONDITIONAL_ON_KNOWN_ABSENCE_SET`: the true zero-minute set is supplied as the scenario.

## Coverage

- Usable historical team-games: **{dataset['usable_team_games']:,}**.
- Team-games with at least one rotation absence: **{dataset['absence_team_games']:,}**.
- Zero-minute roster observations: **{dataset['zero_minute_observations']:,}**.
- Non-trivial roles (baseline >=1 minute): **{dataset['non_trivial_absences']:,}**.
- Available-recipient target rows: **{dataset['recipient_rows']:,}**.
- Seasons: **{dataset['first_season']}–{dataset['last_season']}**.
- Dataset fingerprint: `{dataset['fingerprint']}`.

{_markdown_table(coverage)}

## Absence magnitude

One absence occurred in **{count_distribution['one']:,}** team-games, two in
**{count_distribution['two']:,}**, and three or more in
**{count_distribution['three_plus']:,}**. Multiple all-low-role absences combined to
at least 20 expected minutes in **{cumulative_low:,}** team-games.

The total missing-minute distribution was: minimum **{missing['min']:.2f}**, P10
**{missing['10%']:.2f}**, P25 **{missing['25%']:.2f}**, median **{missing['50%']:.2f}**,
P75 **{missing['75%']:.2f}**, P90 **{missing['90%']:.2f}**, and maximum
**{missing['max']:.2f}** regulation minutes.

No role threshold was used to define an absence. Near-zero roles remain in the
dataset and naturally contribute near-zero missing minutes.

## Baseline and target

Baseline minutes are fixed-config, walk-forward conditional-minutes predictions
using prior-season model fits, with a weighted pre-game role fallback (35% EWMA,
25% last-five, 15% last-three, 15% season average, 10% last game; career/equal-team
fallback thereafter). Each full candidate vector is normalized to 200 before an
absence set is removed. Target minutes are `actual player minutes / actual team
player-minutes × 200`, so overtime is converted to regulation-equivalent allocation.

The recipient target is `actual_regulation_equivalent_minutes -
baseline_normalized_expected_minutes`. Player history, rotation history, teammate
overlap, and prior absence response use observations strictly before target tip.
"""


def _redistribution_report(summary: dict[str, Any]) -> str:
    comparisons = pd.DataFrame(summary["method_comparison"])
    comparisons = comparisons[[
        "experiment_id", "mae", "rmse", "top1_recipient_accuracy",
        "top2_recipient_recall", "teammate_delta_spearman",
        "team_conservation_mae", "team_sum_absolute_error",
    ]]
    selected = summary["selected_diagnostics"]
    bands = pd.DataFrame(selected["missing_minutes_bands"])
    counts = pd.DataFrame(selected["absence_count_bands"])
    folds = []
    for row in summary["method_comparison"]:
        if row["experiment_id"] == summary["selected_method"]:
            folds = pd.DataFrame(row["fold_metrics"])
            break
    cross = summary["cross_position"]
    final = summary["final_model"]
    return f"""# Phase 5B team-minute redistribution model

All evaluation is chronological and `CONDITIONAL_ON_KNOWN_ABSENCE_SET`. E2023,
E2024, and E2025 are forward outer seasons; tuning uses only earlier seasons.

## Comparison

{_markdown_table(comparisons)}

The selected method is **{summary['selected_method']}**: {summary['freeze_reason']}.
Freeze state: **{final['status']}**. Reconciliation is
`{final.get('reconciliation_method')}` and projects available-player scores onto
the bounded 0–40 minute simplex with an exact 200-minute team total.

## Stability and disruption size

{_markdown_table(folds)}

{_markdown_table(bands)}

{_markdown_table(counts)}

## Recipient ranking and cross-position allocation

For the selected method, Top-1 recipient accuracy is
**{selected['overall']['top1_recipient_accuracy']:.4f}**, Top-2 recipient recall is
**{selected['overall']['top2_recipient_recall']:.4f}**, and mean within-team delta
Spearman is **{selected['overall']['teammate_delta_spearman']:.4f}**.

Using a >= {cross['meaningful_actual_gain_threshold']:.1f}-minute meaningful-gain
definition, **{cross['cross_position_actual_gainers']:,}** of
**{cross['meaningful_actual_gainers']:,}** actual gainers
({cross['cross_position_actual_rate']:.1%}) were outside every absent player's
nominal position group. Position is therefore an input, never a hard allocation rule.

On those cross-position meaningful-gainer rows, selected ML MAE was
**{summary['extended_diagnostics']['cross_position_selected_mae']:.4f}** versus
**{summary['extended_diagnostics']['cross_position_position_baseline_mae']:.4f}**
for the position split.

## Inputs

Recipient inputs cover baseline/recent/season minutes, starts, rotation share,
closing/stint stability, FP and box-score rates, team role rank, cumulative missing
minutes/starts/production proxies by broad position, remaining roster depth and
role concentration, prior shared-minute evidence, and prior absence response with
sample counts. Player ID and Fantasy credits are excluded. The context-only ablation
measures whether basketball-derived recipient quality/role features add value.
"""


def _fantasy_report(summary: dict[str, Any], fantasy: pd.DataFrame) -> str:
    impact = summary["fantasy_impact"]
    groups = []
    for name, values in impact["groups"].items():
        groups.append({
            "group": name, "rows": values["rows"],
            "frozen_mae": values["before"]["mae"],
            "adjusted_mae": values["after"]["mae"],
            "mae_change": values["after"]["mae"] - values["before"]["mae"],
        })
    fold_rows = []
    if not fantasy.empty:
        for season, frame in fantasy.groupby("season"):
            fold_rows.append({
                "season": season, "rows": len(frame),
                "frozen_mae": (frame.actual_fp - frame.frozen_fp).abs().mean(),
                "adjusted_mae": (frame.actual_fp - frame.adjusted_fp).abs().mean(),
            })
    return f"""# Phase 5B Fantasy impact under known absence sets

The Phase 4B point models are frozen. The tested adjustment is exactly `25% frozen
direct + 75% (redistributed minutes × frozen FP/min)`. The direct 25% component is
unchanged, and no performance model is retrained.

## Same-row result

- Matched absence-recipient Fantasy rows: **{impact['rows']:,}**.
- Frozen MAE: **{impact['before']['mae']:.4f}**; adjusted MAE:
  **{impact['after']['mae']:.4f}**.
- MAE change: **{impact['mae_change']:+.4f}** points
  ({impact['mae_percentage_change']:+.2f}%).
- Frozen RMSE: **{impact['before']['rmse']:.4f}**; adjusted RMSE:
  **{impact['after']['rmse']:.4f}**.
- Frozen Spearman/Pearson: **{impact['before']['spearman']:.4f} /
  {impact['before']['pearson']:.4f}**; adjusted: **{impact['after']['spearman']:.4f} /
  {impact['after']['pearson']:.4f}**.
- Top-10 actual in predicted Top-20: **{impact['before_ranking']['top10_actual_in_predicted_top20']:.4f}**
  frozen versus **{impact['after_ranking']['top10_actual_in_predicted_top20']:.4f}** adjusted.
- Top-20 actual in predicted Top-30: **{impact['before_ranking']['top20_actual_in_predicted_top30']:.4f}**
  frozen versus **{impact['after_ranking']['top20_actual_in_predicted_top30']:.4f}** adjusted.

{_markdown_table(pd.DataFrame(fold_rows))}

{_markdown_table(pd.DataFrame(groups))}

These rows test teammate performance given the correct historical OUT set. They do
not measure availability prediction. Existing P10/P50/P90 intervals are not shifted
or relabelled as calibrated after a minutes scenario; adjusted live intervals remain
null until a dedicated uncertainty recalibration is validated.
"""


def _live_report(summary: dict[str, Any]) -> str:
    final = summary["final_model"]
    return f"""# Phase 5B live availability and redistribution workflow

## Status semantics

| Source status | Default action |
|---|---|
| AVAILABLE, PROBABLE | PLAY |
| OUT, SUSPENDED, NOT_REGISTERED | OUT |
| LIMITED | PLAY, with warning unless an explicit limit is supplied |
| QUESTIONABLE, DOUBTFUL, GAME_TIME_DECISION, UNKNOWN | User decision |

QUESTIONABLE never reduces `expected_minutes_if_playing`. The review command accepts
PLAY, OUT, UNKNOWN, and LIMITED through the existing append-only, scoped override
backend. Manual overrides retain Phase 5A's highest source priority and do not alter
historical basketball facts.

```bash
python -m scripts.review_availability E2026
python -m scripts.review_availability E2026 \
  --decision PLAYER_ID=OUT --game-id GAME_ID
python -m scripts.set_role_limit PLAYER_ID MAX_MINUTES 20 \
  --season E2026 --game-id GAME_ID
python -m scripts.clear_role_limit PLAYER_ID \
  --season E2026 --game-id GAME_ID
```

One unresolved player creates explicit `PLAYER_PLAYS` and `PLAYER_OUT` scenarios.
Multiple unresolved players require user-supplied scenario sets, avoiding an
uncontrolled exponential expansion. No scenario receives a probability.

## Prediction flow

Each resolved scenario normalizes the full pre-game baseline to 200, removes OUT
roles, derives cumulative missing-role and remaining-roster context, applies the
{final['status'].lower()} method `{summary['selected_method']}`, reconciles available
players to exactly 200 in [0,40], and propagates adjusted minutes only through the
frozen 75% decomposed component. Outputs retain scenario decisions, missing minutes,
minutes delta, before/after FP, model version, cutoff, fingerprints, and context—not
causal claims.

LIMITED alone supplies no numeric restriction. Supported explicit scopes are maximum
minutes, expected minutes, and percentage role reduction. Adjusted uncertainty
intervals are deliberately null/flagged, not fabricated.
"""


def _read(path: Path) -> pd.DataFrame:
    connection = duckdb.connect()
    try:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    finally:
        connection.close()


def _extended_diagnostics(
    summary: dict[str, Any], normalized: pd.DataFrame, predictions: pd.DataFrame,
) -> dict[str, Any]:
    selected = predictions[predictions.experiment_id.eq(summary["selected_method"])].copy()
    position = predictions[
        predictions.experiment_id.eq("p5b__baseline__position_split")
    ][["model_row_id", "predicted_adjusted_minutes"]].rename(
        columns={"predicted_adjusted_minutes": "position_prediction"}
    )
    cross = selected.merge(position, on="model_row_id", how="inner")
    cross = cross[
        cross["minutes_delta"].ge(3) & ~cross["recipient_matches_missing_position"]
    ]
    comparison = {row["experiment_id"]: row for row in summary["method_comparison"]}
    absences = normalized[normalized.rotation_absent].copy()
    grouped = absences.groupby(["game_id", "team_id"]).agg(
        count=("player_id", "size"),
        total_missing=("baseline_expected_minutes", "sum"),
        min_role=("baseline_expected_minutes", "min"),
        max_role=("baseline_expected_minutes", "max"),
    ).reset_index()

    def scenario_metric(mask: pd.Series) -> dict[str, Any]:
        keys = grouped.loc[mask, ["game_id", "team_id"]]
        selected_rows = selected.merge(keys, on=["game_id", "team_id"], how="inner")
        no_adjustment = predictions[
            predictions.experiment_id.eq("p5b__baseline__no_adjustment")
        ].merge(keys, on=["game_id", "team_id"], how="inner")
        return {
            "team_games": len(keys), "recipient_rows": len(selected_rows),
            "selected_mae": float(selected_rows.absolute_error.mean()) if len(selected_rows) else None,
            "no_adjustment_mae": float(no_adjustment.absolute_error.mean()) if len(no_adjustment) else None,
            "mean_absolute_adjustment": float(
                selected_rows.predicted_minutes_delta.abs().mean()
            ) if len(selected_rows) else None,
        }

    return {
        "cross_position_rows": len(cross),
        "cross_position_selected_mae": float((
            cross.actual_regulation_minutes - cross.predicted_adjusted_minutes
        ).abs().mean()),
        "cross_position_position_baseline_mae": float((
            cross.actual_regulation_minutes - cross.position_prediction
        ).abs().mean()),
        "recipient_quality_core_mae": comparison["p5b__catboost__core"]["mae"],
        "recipient_quality_context_only_mae": comparison[
            "p5b__catboost__context_only_ablation"
        ]["mae"],
        "near_zero_absence_scenarios": scenario_metric(grouped.total_missing.lt(1)),
        "absent_25_to_30_minute_player_scenarios": scenario_metric(
            grouped.max_role.ge(25) & grouped.max_role.lt(30)
        ),
        "exactly_three_5_to_10_minute_absences": scenario_metric(
            grouped["count"].eq(3) & grouped.min_role.ge(5) & grouped.max_role.lt(10)
        ),
        "multiple_low_roles_combined_20_plus_team_games": int((
            grouped["count"].ge(2) & grouped.max_role.lt(10)
            & grouped.total_missing.ge(20)
        ).sum()),
    }


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    display = frame.copy()
    for column in display.select_dtypes(include="number"):
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}" if isinstance(value, float) else str(value)
        )
    header = "| " + " | ".join(map(str, display.columns)) + " |"
    separator = "|" + "|".join("---" for _ in display.columns) + "|"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])
