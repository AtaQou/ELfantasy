# Phase 8A — Interactive EuroLeague Fantasy Control Center

## Scope and architecture

Phase 8A is a local desktop-first web UI. Its backend uses Python's standard
library `ThreadingHTTPServer`; the browser layer is plain HTML, CSS and JavaScript,
so no new runtime framework or external data dependency was added. The backend is
an application adapter over existing repository services:

- `src.live.pipeline.update_live` for schedule, results, box scores, PBP/shots,
  rotations, current rosters, Fantasy market, availability and current features;
- `src.live.prediction.run_live_prediction` for frozen Phase 6C inference;
- Phase 5A/5B append-only availability overrides and resolved decisions;
- frozen Phase 7 exact optimization, marginal and coach simulation, dynamic legal
  recourse, captain decisions, bench scoring and Turn transitions.

No Phase 4B, 5B, 6B, 6C or 7 model/code artifact is modified or retrained.

## Launch

```bash
python -m scripts.control_center
```

The default URL is `http://127.0.0.1:8765`. The command accepts `--no-browser`,
`--host`, `--port`, `--database`, `--fantasy-config`, and `--profile`.

## Operational behavior

The dashboard always shows the active season/Matchday, latest successful source
refreshes, market and availability timestamps, Phase 6C currency, and both
rules/scoring gates. A failed or stale source is shown explicitly. The update
button runs the existing live pipeline and never enables the unverified-scoring
dry-run flag. Prediction is attempted only after both production gates pass.

Current Team persists an append-only local profile with roster entity IDs, bank
credits, transfers, scenario and NORMAL/EXCLUDE/FORCE_INCLUDE constraints. The
optimizer derives the available budget as current market roster value plus bank.
Its retained-entity constraint counts all 10 players and the coach, so changing a
coach consumes a transfer. Partial teams may be saved while editing; optimization
requires exactly 4 Guards, 4 Forwards, 2 Centers and 1 Coach.

Availability is shown as GREEN (AVAILABLE/PROBABLE), YELLOW (uncertain statuses
and LIMITED), or RED (OUT/SUSPENDED/NOT_REGISTERED), always with an icon and exact
text. PLAY, OUT, UNKNOWN and explicitly selected LIMITED decisions use the Phase
5A append-only override store. Clearing adds a CLEAR event rather than deleting
history. An override newer than the latest prediction makes predictions stale and
blocks optimization until refreshed.

The player explorer exposes expected minutes, mean/median, P10/P90/P95,
downside/upside probabilities, credits, FP/credit and recent form, with Best
Expected FP, Best Value, Most Expected Minutes, Highest Upside and Safest views.
FP/credit remains diagnostic only.

## Recommendation semantics

Candidate rosters are exact legal MILP solutions under current-roster, budget,
position, club, coach, transfer and manual-pool constraints. Phase 7 outcome and
coach samples evaluate legal lineups with between-Turn recourse. Best Overall
maximizes simulated mean final score. Safer and Higher-Upside select P10 and P90
among strategies inside the Monte Carlo 95% mean-error band; no fixed risk bonus
is added. Important player alternatives use different same-position tail profiles
and re-optimize the complete roster around each alternative.

Strategy thresholds are found by evaluating the frozen Phase 7 next-Turn action
as a completed T1 score varies. They are decision boundaries from expected final
value, not hand-written score cutoffs. The Strategy view also shows initial and
later captain candidates, useful early upside, later protection, and players with
no remaining safety net.

## Reproducibility and current production state

Migration 011 stores append-only UI state and recommendation runs. Every run
records market and prediction fingerprints, prediction run, rules version/hash,
manual-override fingerprint, current roster, bank, transfer limit, constraints,
simulation settings, recommendations, input fingerprint and immutable JSON path.

At implementation time E2026 remains correctly blocked: no validated live market
or E2026 rules/scoring compatibility attestations are present. The UI is ready for
daily operation once those existing gates pass; it does not implement future-round
forecasting or multi-Matchday transfer planning.

Final verification: **289/289 tests** and **78/78 database integrity checks** pass.
The unchanged `phase7_dynamic_strategy_engine_frozen_v1` artifact and its
`phase6c_predictive_uplift_frozen_v1` predictive dependency both validate.
