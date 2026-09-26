# Phase 6A prospective prediction protocol

`prediction_generated_at` is the UTC cutoff and precedes every game tip. The
deterministic run fingerprint covers cutoff, slate, whole-market snapshot,
protocol, decisions/scenarios, model artifacts, and feature manifest. Identical
effective inputs resolve to the same run ID and byte-identical artifacts.

Successful runs append hierarchical `predictions.json`, one-row-per-scenario/
player `predictions.csv`, run/scenario/player DuckDB rows, complete feature and
provenance snapshots, availability decisions, model hashes, coverage, timing,
and warnings. Existing files must be byte-identical; database prediction rows
are never updated. Empty/failed attempts retain diagnostic metadata without
claiming successful predictions.

Each run also records the frozen player-scoring target version, current-season
compatibility state, evidence fingerprint, and `production_ready`. Only
`SCORING_RULES_COMPATIBLE` can produce a production-ready run; mismatch or
unverified states may be exercised only through an explicit technical dry-run.

Actuals are attached separately in `live_prediction_outcomes`.

```bash
python -m scripts.evaluate_live_predictions --run-id ID
```

Only completed later outcomes are attached. Evaluation reports MAE, RMSE,
Spearman, Pearson, ranking capture, and adjusted versus unadjusted results. With
no completed outcomes it returns `NO_COMPLETED_PROSPECTIVE_OUTCOMES`.

This preserves central FP, eventual actual FP, pre-game feature snapshot/hash,
baseline/adjusted minutes, scenario, and timestamp for Phase 6B. Phase 6A does
not implement quantiles, upside/downside, high-score probability, risk, or a
true ceiling.
