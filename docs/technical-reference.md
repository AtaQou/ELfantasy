# EuroLeague Fantasy AI — Phase 9A Stat-Line & Game-Environment Challenger

This repository provides a reproducible, leakage-aware local data foundation and
point-in-time EuroLeague Fantasy modelling datasets. It preserves
immutable source responses, normalizes them into DuckDB, explicitly scores
player-game Fantasy outcomes, generates box-score-only core features, and runs
transparent chronological baselines, then adds modular PBP rotation/on-off/
lineup context and validated player/opponent shot profiles, then evaluates
leakage-safe Ridge, CatBoost, XGBoost, and LightGBM regressors with nested
chronological folds.

Phase 4B adds separately validated conditional expected-minutes and FP/min
models, leakage-safe decomposed and hybrid Fantasy predictions, controlled
calibration/loss/stage ablations, and chronological split-conformal uncertainty
intervals.

Phase 5A adds an incremental current-season updater, timestamped Fantasy and
availability snapshots, scoped manual overrides, freshness monitoring, and a
train/live-parity feature builder for upcoming games. It consumes the frozen
Phase 4B model schema but never retrains or changes that model. Phase 5A does
not estimate participation probabilities or create availability-adjusted
Fantasy predictions.

Phase 5B adds user-controlled PLAY/OUT/UNKNOWN/LIMITED decisions and a
chronologically evaluated team-level minute-redistribution layer. Historical
zero-minute roster observations are treated as **rotation absences**, not
fabricated injuries. Backtests are conditional on being supplied the correct
OUT set; no participation probability is invented and the Phase 4B performance
models are not retrained.

Phase 6A composes refresh, upcoming-slate and Fantasy-matchday resolution,
current player mapping, availability scenarios, shared point-in-time features,
frozen Phase 4B inference, Phase 5B redistribution, machine-readable output,
and immutable prospective storage behind one command. Phase 6A.1 adds the
deterministically reconstructed, row-level-validated Phase 4B deployment bundle.
The runner verifies model, training-data, code/protocol, feature, preprocessing,
and bundle hashes before every run and never retrains automatically.

Phase 6B adds a conditional-on-participation player outcome distribution. A
chronological CatBoost RMSE challenger supplies `expected_fp`; calibrated direct
CatBoost quantiles supply P10/P25/P50/P75/P90/P95; and calibrated high-score
models supply monotonic probabilities for 20+/25+/30+/35+, with 40+ derived
from the coherent distribution because only 65 verified positives exist. The
frozen Phase 4B central value remains available as `phase4b_central_fp` and is
not overwritten. The frozen bundle is
`phase6b_probabilistic_player_outcome_frozen_v1`.

Phase 6C audits ten pre-game feature families built only from the repository's
existing box scores, PBP/rotations, shots, roster history, opponent context, and
known-absence research. It explicitly models walk-forward next-game USG% and
never supplies actual target-game usage to the Fantasy model. Only historical
usage/offensive-involvement features survived independent chronological
ablation and the untouched outer gate. The separate frozen uplift bundle is
`phase6c_predictive_uplift_frozen_v1`; Phase 4B and Phase 6B artifacts remain
unchanged. The benchmark remains conditional on playing.

Phase 7 adds a versioned Classic rules engine, exact legal expected-score MILP,
a calibrated nonparametric Monte Carlo layer over the frozen Phase 6C player
distributions, dynamic captain/field-bench decisions between day-based Turns,
sequential trade and capital accounting, a small separate coach distribution,
and strict point-in-time E2025 replay. Phase 4B, 5B, 6B, and 6C are neither
modified nor retrained. Current E2026 optimization remains fail-closed until the
market plus both player-scoring and Fantasy-rules compatibility gates pass.
The completed repository validation is 272/272 tests and 73/73 database
integrity checks.

Phase 8A adds a dependency-free local web control center over the existing live
refresh, frozen Phase 6C prediction, Phase 5A override, and frozen Phase 7
strategy layers. It persists the current 4G/4F/2C plus coach roster, bank and
transfer limit; exposes source freshness and distribution diagnostics; supports
manual availability and optimization-pool decisions; and returns Best Overall,
Safer, and Higher-Upside legal strategies with re-optimized player alternatives.
It does not retrain or alter any frozen model. Every recommendation records its
market, prediction, rules, override, roster, constraint, and simulation identity.
Current repository verification is 289/289 tests and 78/78 database integrity
checks.

Phase 8B adds immutable pre-lock shadow records, automatic attachment of completed-Turn
Fantasy outcomes, legal formation-wide after-Turn recourse, paired KEEP/SWITCH and
captain evidence, prospective player/strategy calibration, and read-only behavior
monitoring. A separate E2025-only next-price model is exposed with low confidence after
improving chronological RMSE in all three folds but not aggregate MAE; its output never
enters the frozen Phase 7 score objective. No frozen predictive/strategy artifact is
modified and no multi-Matchday optimization is introduced. Current verification is
308/308 tests and 86/86 database integrity checks.

Phase 8C completes the UI/UX and operational hardening pass. It adds an always-visible
readiness strip, intentional pre-season/empty/blocked/error states, structured refresh
progress and change reporting, compact sortable player-table presets, modal availability
overrides, clearer recommendation and formation-aware KEEP/SWITCH comparisons, grouped
low-sample-safe monitoring, request-race protection, and an isolated in-memory A–T demo
catalog. Demo mode never reads or writes production data. No frozen predictive or strategy
artifact, Monte Carlo logic, optimizer objective, price objective, or Fantasy rule is
changed. Current verification is 333/333 tests and 86/86 database integrity checks.

Phase 9A evaluates a separate leakage-safe predictive architecture that forecasts the
game environment, samples a minutes-conditioned and jointly dependent player stat line,
and reconstructs the complete Fantasy distribution with the verified scoring formula.
It uses only existing repository box scores, PBP/shots, rotations, rosters, Phase 5B
availability context, and historical features. Validation preserves the chronological
E2022→E2023, E2022–E2023→E2024, and E2022–E2024→E2025 outer folds. The inner-selected
Phase 6C plus environment/stat-line ensemble improved distribution CRPS slightly, but
lost to frozen Phase 6C on pooled MAE/RMSE and in every outer-fold MAE comparison.
Consequently `phase6c_predictive_uplift_frozen_v1` remains the production winner and no
`phase9a_statline_game_environment_frozen_v1` bundle was created. Phase 6C and Phase 7
artifacts remain hash-identical and the Phase 7 interface check passes. Current
repository verification is 357/357 tests and 86/86 database integrity checks.

## Phase 9A challenger research

Reproduce the bounded local-data-only research run (cached unless `--force` is supplied):

```bash
python -m scripts.run_phase9a_experiments --force
```

The complete architecture, game-environment, component, dependency, minutes,
calibration, threshold, explosion, and freeze-gate evidence is in
[`reports/phase9a_statline_game_environment.md`](reports/phase9a_statline_game_environment.md).
The command stops after Phase 9A and never starts the multi-Matchday planner.

## Phase 8C local control center

```bash
python -m scripts.control_center
```

Open `http://127.0.0.1:8765` if the browser does not open automatically. Use
`--no-browser`, `--host`, `--port`, `--database`, `--fantasy-config`, or
`--profile` as needed. The desktop UI contains Dashboard, Current Team, Players,
Availability, Recommendations, Strategy, Value/Growth, and Monitoring sections. The **Update latest data**
action calls the existing `update_live` pipeline, then calls the existing frozen
prediction runner only when both compatibility gates pass. **Optimize Next
Matchday** uses the saved current roster and transfer allowance; it never assumes
a clean-slate roster.

History opens on the active season and lists completed games before future
fixtures. Its game, player, and team results use `current_games`, which combines
stable game identities with the latest official schedule snapshots. This allows
final scores to appear even when existing predictions prevent DuckDB from updating
the original referenced game row.

Between Turns, Fantasy matchday resolution checks the market's team/opponent pairs
against the complete official round, including games already played. Partial
official roster coverage is supplemented from safely mapped current Fantasy
players only for uncovered teams. Conflicting or non-unique matchday mappings
remain blocked. Restart the control center after code updates, then use
**Update latest data** to rebuild the slate and predictions.

Once a Matchday has started, players whose games are complete remain valid current-team
entities but are intentionally absent from a new-roster prediction pool. Standard roster
optimization now reports their names and directs the user to **Update Turn Results /
Re-evaluate Strategy**, which preserves realized scores and permits only legal between-Turn
formation and captain actions. It no longer mislabels these players as missing from the
Fantasy market.

Inspect all synthetic pre-season, blocked, stale, error, between-Turn, and price states
without touching the database or live sources:

```bash
python -m scripts.control_center --demo
```

Start directly in one named state (run `--help` for all available values):

```bash
python -m scripts.control_center --demo --demo-state captain-decision
```

After a Turn is fully complete, **Update Turn Results / Re-evaluate Strategy** refreshes
only the official schedule and required box scores, then locks already-played entities,
re-optimizes the whole legal formation, and displays paired final-team KEEP/SWITCH and
captain evidence. It reuses the immutable pre-lock prediction instead of rebuilding the
entire live market and prediction pipeline. The live control center also keeps one DuckDB
instance warm so its short repository operations do not repeatedly reload the full catalog.
An advisor snapshot is eligible only when its profile, season, Matchday, and complete roster
exactly match the currently saved team. When no matching pre-lock role snapshot exists, the
advisor falls back to the saved roster and the official Turn assignments: it shows verified
actual Fantasy points and minutes for players whose Turn is complete, projections for the
later Turn, and same-position conditional switches. Because that fallback cannot recover
starters, bench, sixth man, or captain, each switch is explicitly conditional on the first
player being on the field and the later player being on the bench. It never borrows those
roles from another roster.
The same saved-roster resolver is used for Team 1, Team 2, and Team 3. Player mappings are
selected from the latest valid season crosswalk record, including a closed record when it is
the newest verified match, so a routine market refresh cannot silently turn a complete saved
team into an incomplete Live Turn Strategy roster.
The Players page also
contains a separate **Value Credit Growth** view and player detail panel. Monitoring is
strictly read-only; price forecasts and live validation never auto-retrain or alter the
optimizer.

History → **Players** is a dense sortable table rather than a card grid. It defaults to the
current season. **All Rounds** shows per-game averages; choosing one Round changes the same
table to actual FP, minutes, points, rebounds, assists, steals, blocks, and PIR from that
round. Both views also include 2P, 3P, and free throws made/attempted, turnovers, personal
fouls, fouls won/drawn, and blocks received, which makes the full Fantasy-point calculation
visible. A selected round shows credits at the start of that round, credits after the round,
and the change; All Rounds shows starting credits, latest known credits, and cumulative
change. Post-round credits remain blank until the next point-in-time market snapshot proves
the change. Team and name filters, a context-aware minimum-minutes filter, and selectable
ordering remain available in both modes. Fantasy points fall back to the verified box-score
formula whenever the materialized target table has not caught up with a newly completed round.
Long tables keep their column headings visible while scrolling down and their first identifying
column visible while scrolling right. This applies to the Players tables and the other tabular
History, Availability, Value Growth, Monitoring, and detail views.

The Players tab builds Last Game and L5 evidence directly from canonical completed box
scores, with the same verified Fantasy-score fallback, then combines those games with stored
preparation appearances. It therefore updates as soon as the latest schedule and box score
refresh succeeds and no longer waits for the derived ML target view to be rebuilt.

Current-market discovery also handles a lagging official Fantasy bootstrap. When the bootstrap
still labels the completed Matchday as current, the refresh checks the next published markets
against the complete upcoming canonical opponent slate. It advances only when exactly one
market matches; missing or conflicting future markets remain `MATCHDAY_AMBIGUOUS`.
When that verified market advances, each saved Control Center team follows it automatically while
retaining its roster and clearing any scenario selection from the completed Matchday.
Coach prices, Turns, and team slots still come from the current Fantasy market, but displayed coach
names come from each team's latest completed current-season official box score. This corrects stale
Fantasy coach assignments automatically after a coaching change; the Fantasy name remains the
fallback until the team has a completed game.

Pre-round **Improve My Team** and **Build New Team** recommendations preserve at least one usable
later-Turn bench substitute whenever the slate contains multiple Turns. Candidate generation applies
this requirement before simulation, and the final lineup keeps that player off the field and outside
the fixed sixth-man slot so a poor early score can be replaced legally. Recommendation cards show the
entire projected roster for every mode, its T1/T2 counts, and the concrete later-Turn replacement
options instead of showing only incoming transfers.

The current E2026 UI correctly displays **BLOCKED** because the repository does
not yet have a genuine E2026 Fantasy market or verified E2026 rules/scoring
attestations. That state must not be bypassed. See
[`reports/phase8a_control_center.md`](reports/phase8a_control_center.md).

Current E2026 player-scoring compatibility is deliberately
`SCORING_RULES_UNVERIFIED`: the current official page describes the same formula,
but it does not explicitly establish an effective E2026/2026-27 rules version
and no genuine E2026 market exists yet. A non-empty real run therefore fails
closed unless compatibility is verified. The explicit
`--allow-unverified-scoring-dry-run` option permits only a labelled technical
dry-run, never a production-ready result.

The Phase 3A, Phase 3B, Phase 4A, Phase 4B, and Phase 6B benchmarks are
**conditional on playing**. Zero-minute DNP rows are excluded from player
performance training and evaluation; positive-minute games retain their actual
Fantasy outcomes.
The project still does not invent participation probabilities. Phase 7 consumes
explicit PLAY/OUT availability scenarios and treats all player-performance
distributions as conditional on playing.

## Phase 7 Fantasy optimization

```bash
python -m scripts.optimize_fantasy \
  --season E2026 \
  --matchday N \
  --budget 100
```

The command returns the roster, starting five, sixth man, bench, coach,
captain, Turn allocation, credits, frozen Phase 6C mean/quantiles/tails,
strategic role, simulated final-score distribution, and an immutable snapshot.
After a Turn, pass `--snapshot SNAPSHOT.json --after-turn-results RESULTS.json`
to obtain only legal actions for players who have not played. E2025 research can
be reproduced with `python -m scripts.run_phase7_backtest --reuse-predictions`.
See [`reports/phase7_dynamic_strategy_engine.md`](reports/phase7_dynamic_strategy_engine.md).

## Phase 6A live prediction

```bash
python -m scripts.predict_live \
  --season E2026 \
  --fantasy-config PATH \
  --refresh
```

Use `--no-refresh` for a stored-state rerun and `--decision-file FILE.json
--non-interactive` for reproducible scenarios. Missing markets produce
`NO_CURRENT_FANTASY_SLATE`; ambiguous matchday mappings are never guessed. See
[`reports/phase6a_live_prediction_runner.md`](reports/phase6a_live_prediction_runner.md)
and
[`reports/phase6a_prospective_prediction_protocol.md`](reports/phase6a_prospective_prediction_protocol.md).
The Phase 4B deployment reconstruction and strict reproduction evidence are in
[`reports/phase6a1_phase4b_deployment_bundle.md`](reports/phase6a1_phase4b_deployment_bundle.md).
Phase 6B's central, quantile, probability, tail, and final-distribution evidence
is in
[`reports/phase6b_central_mean_analysis.md`](reports/phase6b_central_mean_analysis.md),
[`reports/phase6b_quantile_models.md`](reports/phase6b_quantile_models.md),
[`reports/phase6b_high_score_probabilities.md`](reports/phase6b_high_score_probabilities.md),
[`reports/phase6b_tail_diagnostics.md`](reports/phase6b_tail_diagnostics.md), and
[`reports/phase6b_final_distribution.md`](reports/phase6b_final_distribution.md).
Phase 6C's feature definitions, all independent ablations, walk-forward usage
accuracy, central/ranking results, upside/downside calibration, and frozen
artifact decision are in
[`reports/phase6c_predictive_uplift.md`](reports/phase6c_predictive_uplift.md).

Reproduce the bounded Phase 6C research and frozen bundle from local data only:

```bash
python -m scripts.run_phase6c_experiments --force
```

The detailed findings are in [`reports/data_audit.md`](reports/data_audit.md),
[`reports/feature_source_matrix.md`](reports/feature_source_matrix.md), and
[`reports/rotation_reconstruction_audit.md`](reports/rotation_reconstruction_audit.md).
Architecture comparisons are in
[`reports/reference_project_lessons.md`](reports/reference_project_lessons.md).
The architecture and measured database state are in
[`reports/database_architecture.md`](reports/database_architecture.md) and
[`reports/database_quality.md`](reports/database_quality.md).
Selective coverage, cleanup, and storage evidence are in
[`reports/historical_coverage.md`](reports/historical_coverage.md),
[`reports/phase_2_5_cleanup.md`](reports/phase_2_5_cleanup.md), and
[`reports/storage_and_performance.md`](reports/storage_and_performance.md).
The official historical Fantasy Stats recovery and its safety decision are in
[`reports/historical_fantasy_market.md`](reports/historical_fantasy_market.md).
Phase 3A scoring, features, dataset quality, and baseline results are in
[`reports/fantasy_scoring_rules.md`](reports/fantasy_scoring_rules.md),
[`reports/phase3_feature_dictionary.md`](reports/phase3_feature_dictionary.md),
[`reports/phase3_dataset_quality.md`](reports/phase3_dataset_quality.md), and
[`reports/baseline_backtest.md`](reports/baseline_backtest.md).
Phase 3B definitions, validation, diagnostics, and the apples-to-apples benchmark
are in
[`reports/phase3b_feature_dictionary.md`](reports/phase3b_feature_dictionary.md),
[`reports/phase3b_dataset_quality.md`](reports/phase3b_dataset_quality.md),
[`reports/phase3b_rich_feature_diagnostics.md`](reports/phase3b_rich_feature_diagnostics.md),
and [`reports/phase3b_core_benchmark.md`](reports/phase3b_core_benchmark.md).
Phase 4A's pre-registered split/tuning design, chronological model results,
controlled rich-feature ablations, and frozen error analysis are in
[`reports/phase4a_ml_protocol.md`](reports/phase4a_ml_protocol.md),
[`reports/phase4a_model_results.md`](reports/phase4a_model_results.md),
[`reports/phase4a_feature_ablations.md`](reports/phase4a_feature_ablations.md),
and [`reports/phase4a_error_analysis.md`](reports/phase4a_error_analysis.md).
Phase 4B results are in
[`reports/phase4b_minutes_model.md`](reports/phase4b_minutes_model.md),
[`reports/phase4b_decomposed_model.md`](reports/phase4b_decomposed_model.md),
[`reports/phase4b_calibration_uncertainty.md`](reports/phase4b_calibration_uncertainty.md),
and [`reports/phase4b_final_model_comparison.md`](reports/phase4b_final_model_comparison.md).
Phase 5A's measured live-data results are in
[`reports/phase5a_historical_availability.md`](reports/phase5a_historical_availability.md),
[`reports/phase5a_live_sources.md`](reports/phase5a_live_sources.md), and
[`reports/phase5a_live_pipeline_quality.md`](reports/phase5a_live_pipeline_quality.md).
Phase 5B's absence data, redistribution comparison, Fantasy impact, and live
decision workflow are in
[`reports/phase5b_absence_dataset.md`](reports/phase5b_absence_dataset.md),
[`reports/phase5b_redistribution_model.md`](reports/phase5b_redistribution_model.md),
[`reports/phase5b_fantasy_impact.md`](reports/phase5b_fantasy_impact.md), and
[`reports/phase5b_live_workflow.md`](reports/phase5b_live_workflow.md).

The frozen Phase 5B redistribution model is XGBoost Core. Conditional on the
known historical absence set, its E2023–E2025 minute MAE is 4.4020 versus
4.4610 for proportional allocation and 4.6339 for no adjustment; it wins all
three outer seasons. On 15,019 same-row Fantasy observations, propagating the
adjusted minutes through only the frozen hybrid's decomposed component improves
MAE from 5.9290 to 5.8998. These are absence-scenario results, not availability
prediction results.

## Python setup

The project was built and tested with Python 3.13.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

In zsh, `.venv/bin/activate` is a shell script that must be sourced. Running it directly produces the `permission denied` message shown in the original terminal output. There is no need to make it executable.

Activation is optional; every command can instead use `.venv/bin/python` explicitly.

## Canonical database

Initialize/migrate the ignored local DuckDB file:

```bash
python -m scripts.init_database
```

Ingest or resume the complete compact/core policy window:

```bash
python -m scripts.ingest_history \
  --stage core --start-season 2018 --end-season 2025 --pace-seconds 0.25
```

Ingest or resume PBP and shots only for the rich policy window:

```bash
python -m scripts.ingest_history \
  --stage events --start-season 2021 --end-season 2025 --pace-seconds 0.25
```

`--stage all --start-season 2018 --end-season 2025` applies the asymmetric policy automatically: core for all eight seasons and events only for E2021-E2025. The same command is the resume/retry command. Valid hashed raw files and complete normalized game components are skipped, so a successful rerun converges to zero new records. The CLI rejects broader windows unless `--allow-outside-policy` is explicitly supplied after a separate decision.

Rebuild a retained season from local immutable raw artifacts without network access:

```bash
python -m scripts.ingest_archived_season \
  --season E2024 --components core,play_by_play,shots
```

Validate the database and regenerate its quality report:

```bash
python -m scripts.check_database
```

## Phase 3A target, features, and baselines

Generate the versioned Fantasy target and refresh its retained validation evidence:

```bash
python -m scripts.generate_fantasy_targets
```

Generate the strict point-in-time core feature tables, optional historical Fantasy
metadata/E2025-price extension, walk-forward split definitions, and Parquet
artifacts:

```bash
python -m scripts.generate_core_features
```

Run all six fixed non-ML baselines and write the chronological metric artifact:

```bash
python -m scripts.backtest_baselines
```

The stable views are `ml_player_game_targets`, `ml_player_game_core_features`, and
`ml_player_game_core_features_with_fantasy`. Versioned materializations and Parquet
exports use the `_v1` suffix. Generated Parquet files under `data/derived/phase3a/`
are ignored because they are reproducible from the canonical database; compact
validation summaries under `data/samples/phase3a/` are retained.

E2022-E2025 populate historically verified `actual_fantasy_points`. E2018-E2021
populate only the explicitly standardized/counterfactual target. E2022-E2024
Fantasy credits remain quarantined; only E2025 can populate
`fantasy_credits_pre_matchday` or price-aware diagnostics.

## Phase 3B rich features and validation

Generate deterministic PBP/shot intermediates, point-in-time rich families,
Core+Rich tables, same-population baseline metrics, Parquet exports, and compact
diagnostic JSON:

```bash
python -m scripts.generate_rich_features
```

No download occurs. The command reads only the canonical database and preserves
the Phase 3A core tables. Re-running it replaces only reproducible derived Phase
3B materializations.

Run the fail-closed row-identity, source-cutoff, lineup, stint, possession,
minute-reconciliation, target-semantics, and fingerprint gates:

```bash
python -m scripts.validate_rich_features
```

Run the focused Phase 3B tests, including synthetic future-observation failures:

```bash
python -m unittest tests.test_phase3b -v
```

Stable rich views are `pbp_player_stints`, `pbp_lineup_stints`,
`pbp_possessions`, `ml_player_game_rich_features`, and
`ml_player_game_core_plus_rich`. The exact official-rule comparison population is
materialized as `ml_phase3b_primary_evaluation_v1`. Generated Parquet artifacts
are under ignored `data/derived/phase3b/`; retained fingerprints and diagnostics
are under `data/samples/phase3b/`.

The possession layer is explicitly an estimate
(`FGA-OREB+TO+0.44×FTA`), not a claim of exact provider possession boundaries.
Rotation/on-off/lineup failures are quarantined only from those families; valid
box-score and shot features remain available. Phase 3B performs no ML training.

## Phase 4A chronological ML experiments

Run the frozen nested walk-forward matrix. The command reads only existing Phase
3 artifacts; compatible completed folds are reused unless `--force` is supplied:

```bash
python -m scripts.run_phase4a_experiments
```

Generate the same-row baseline reproduction, fold/season/minutes/history/player
diagnostics, paired game-cluster bootstrap, feature importance, seed stability,
identity comparison, and standardized-history comparison:

```bash
python -m scripts.generate_phase4a_diagnostics
```

Run the focused Phase 4A split, train-only preprocessing, provenance, same-row,
credit-exclusion, and reproducibility tests:

```bash
python -m unittest tests.test_phase4a -v
```

The stable DuckDB views are `ml_phase4a_outer_predictions_v1`,
`ml_phase4a_inner_trials_v1`, `ml_phase4a_preprocessing_audit_v1`,
`ml_phase4a_feature_importance_v1`, and `ml_phase4a_experiments_v1`. Generated
model caches and Parquet predictions live under ignored `data/derived/phase4a/`;
compact protocol, fold, result, and diagnostic JSON lives under
`data/samples/phase4a/`.

The Phase 4A winner is CatBoost CORE + ROTATION with outer MAE 5.8272 versus the
same-row blend's 6.0130. The locked full Phase 3B blend remains 5.9953 on 29,232
rows. Those benchmark populations are intentionally labelled separately.
Phase 4A adds no credits to model inputs and does not implement Phase 4B,
availability modelling, model ensembling, or optimization.

## Phase 4B conditional performance experiments

Run the frozen minutes, production-rate, decomposed, direct-loss, hybrid,
calibration, uncertainty, and competition-stage protocol. Compatible completed
component fits are reused unless `--force` is supplied:

```bash
python -m scripts.run_phase4b_experiments
```

Run the focused Phase 4B leakage, stage, target-stabilization, calibration,
interval, persistence, and Phase 4A immutability tests:

```bash
python -m unittest tests.test_phase4b -v
```

The stable DuckDB views are `ml_phase4b_outer_predictions` and
`ml_phase4b_prediction_intervals`. Versioned component predictions, inner
trials, preprocessing/leakage audits, calibration comparisons, experiment
tracking, and feature importance use the `ml_phase4b_*_v1` tables. Generated
Parquet/model caches are under ignored `data/derived/phase4b/`; the retained
protocol, result summary, and exact final freeze are under
`data/samples/phase4b/`.

The frozen primary pipeline is a validation-selected 25% Phase 4A direct / 75%
decomposed hybrid. It has genuine outer MAE 5.8086 on the identical 22,399-row
E2023-E2025 universe. This is a modest but game-cluster-bootstrap-positive
0.32% improvement over the frozen Phase 4A winner and 3.40% over the original
same-row blend. It does not model participation, injury, DNP risk, lineup
optimization, simulation, or captain selection.

## Phase 5A live-data workflow

Run the complete incremental live refresh for the current season:

```bash
python -m scripts.update_live --season E2026
```

The updater automatically discovers the current competition, players-list, and
Matchday identifiers from the official public Fantasy league bootstrap, then
reuses the bearer token in `.auth/euroleague.json` for the structured market
request. `--fantasy-config PATH` remains an optional sanitized/controlled
override. Configuration, authentication, endpoint, and validation failures are
reported separately and always preserve the last valid market snapshot. A normal
pre-prediction workflow is:

```bash
python -m scripts.update_live --season E2026
python -m scripts.live_status --season E2026
python -m scripts.set_availability_override PLAYER_ID OUT \
  --season E2026 --game-id GAME_ID --note "confirmed pre-game"
python -m scripts.build_current_slate --season E2026
```

The updater classifies games as new, updated, unchanged, or rescheduled; only
missing completed-game facts are fetched. Re-running with unchanged source data
is idempotent. Official round-detail calls are deliberately bounded per run with
`--max-round-requests` so the current season can fill incrementally without
hammering the source. No command in this section retrains the frozen model.

Individual diagnostic/update commands remain available:

```bash
python -m scripts.update_current_season --season E2026
python -m scripts.update_fantasy_market --season E2026
python -m scripts.update_availability --season E2026
python -m scripts.build_current_slate --season E2026
python -m scripts.live_status --season E2026
python -m scripts.generate_phase5a_reports --season E2026
```

Availability observations are append-only. Resolution is deterministic:
active scoped manual override, then source priority, then the newest capture.
The source hierarchy is official club confirmation, official EuroLeague report,
official current Fantasy signal, other verified source, and unknown. Equal-time
conflicts remain stored and are flagged. Missing information resolves to
`UNKNOWN`, never implicitly to `AVAILABLE`.

Manual overrides support `OUT`, `AVAILABLE`, `QUESTIONABLE`, `LIMITED`, and the
other canonical statuses. Scope an override to a game, Fantasy matchday, or
expiry time so it cannot silently persist forever:

```bash
python -m scripts.set_availability_override PLAYER_ID LIMITED \
  --season E2026 --matchday 1 --expires-at 2026-10-02T18:00:00Z
python -m scripts.clear_availability_override PLAYER_ID \
  --season E2026 --matchday 1 --note "restriction cleared"
```

The current slate is built by calling the same Phase 3/4 core and rotation
feature functions used for training at an explicit pre-tip cutoff. It validates
all 133 inputs required by the frozen direct, minutes, and production components
before prediction time. If a current official roster is unavailable, the system
does not carry an old-season membership forward: it emits a schema-valid empty
slate and an explicit warning.

## Phase 5B availability review and known-absence scenarios

Run the frozen-protocol historical experiment and generate its reports:

```bash
python -m scripts.run_phase5b_experiments
python -m scripts.generate_phase5b_reports
```

Review the current slate before a prediction run. QUESTIONABLE, DOUBTFUL,
GAME_TIME_DECISION, and UNKNOWN require a user decision; they never reduce
conditional minutes by themselves:

```bash
python -m scripts.review_availability E2026
python -m scripts.review_availability E2026 \
  --decision PLAYER_ID=PLAY --game-id GAME_ID
python -m scripts.review_availability E2026 \
  --decision PLAYER_ID=OUT --game-id GAME_ID
python -m scripts.review_availability E2026 \
  --decision PLAYER_ID=UNKNOWN --game-id GAME_ID
python -m scripts.build_absence_scenarios E2026 \
  --decision PLAYER_ID=OUT --game-id GAME_ID
```

`UNKNOWN` produces separate play/out scenarios when one unresolved player
remains. With multiple unresolved players, supply explicit scenario sets rather
than generating every combination or assigning unsupported probabilities.

LIMITED is participation-with-role-context, not participation uncertainty. A
bare LIMITED status is only a warning. Store a numeric restriction only when one
is explicitly known:

```bash
python -m scripts.review_availability E2026 \
  --decision PLAYER_ID=LIMITED --game-id GAME_ID
python -m scripts.set_role_limit PLAYER_ID MAX_MINUTES 20 \
  --season E2026 --game-id GAME_ID
python -m scripts.clear_role_limit PLAYER_ID \
  --season E2026 --game-id GAME_ID
```

Role limits and availability choices are append-only and game/matchday/expiry
scoped. For each fully resolved team scenario, OUT players contribute their
normalized pre-game role to the missing-minute context, the redistribution
layer allocates the regulation-equivalent 200 minutes among remaining players,
and only the frozen hybrid's 75% decomposed component receives adjusted minutes.
The 25% direct component remains unchanged. Scenario-adjusted uncertainty
intervals remain null until separately recalibrated.

The generated database is `data/db/euroleague.duckdb` and is ignored by Git. Raw source artifacts under `data/raw/` remain authoritative and are also local/ignored because they can grow substantially. Normalized fact rows retain raw-artifact hashes, paths, endpoints, fetch times, and ingestion-run IDs.

## Safe one-time Fantasy login

Fantasy market data require a normal EuroLeague account session. The login script
opens a visible Chrome window and lets you sign in on the official site yourself:

```bash
python -m scripts.login_fantasy
```

The script never accepts or reads your email/password. It does not create a trace,
HAR, request log, or callback-URL log. After the official frontend confirms login,
Playwright state is saved to `.auth/euroleague.json` with owner-only permissions.
That file can contain a reusable session credential: do not inspect, share, attach,
or copy it into a fixture. The complete `.auth/` directory is ignored by Git.

The default uses an installed Google Chrome. If Chrome is unavailable, install
Playwright's Chromium and select it explicitly:

```bash
python -m playwright install chromium
python -m scripts.login_fantasy --channel bundled-chromium
```

The project is now a real Git repository. Verify the secret exclusions at any time:

```bash
git check-ignore -v .auth/euroleague.json
git check-ignore -v .env
git status --short --untracked-files=all
```

The verified local modes are `0700` for `.auth/` and `0600` for its state file. Neither `.auth/euroleague.json`, `.env`, raw collection files, nor the generated DuckDB file appears in Git status.

Capture a small sanitized market sample after login:

```bash
python -m scripts.explore_fantasy_api --direct-http
```

Discover the current official scope and append one validated market observation
to the canonical raw/database layers:

```bash
python -m scripts.update_fantasy_market --season E2026
```

For controlled historical research, append one explicitly addressed observation:

```bash
python -m scripts.collect_fantasy_snapshot \
  --season E2025 \
  --competition-id 30 \
  --players-list-id 30 \
  --matchday-id 1000 \
  --matchday-number 38
```

Use only IDs exposed by the legitimate current Fantasy configuration/UI. For an old matchday supplied by that configuration, add `--historical-request`; its price is retained as matchday-addressed, while injury/probability/bench/on-fire/average fields are automatically labelled unsafe current overlays.

Collect/resume every matchday explicitly listed by the legitimate current configuration:

```bash
python -m scripts.collect_fantasy_history
```

Recover and validate every historical season exposed by the official Stats UI:

```bash
python -m scripts.collect_historical_fantasy_market
```

This retains exact E2022-E2024 responses but quarantines their non-historical
credit overlay. It independently validates E2025 Stats quotations against the
existing matchday market and never persists Stats outcomes/current overlays into
the leakage-safe pre-matchday market view.

The current archive has all 38 listed E2025 matchdays. A completed rerun makes no authenticated requests. Previous-season IDs are not enumerated or guessed.

Rerun conservative Fantasy-to-official identity resolution after roster ingestion:

```bash
python -m scripts.resolve_fantasy_identity
```

Direct mode is the recommended normal read path: it reuses the Playwright state
in memory through the narrow authenticated HTTP client and does not launch a
browser. Omit `--direct-http` only when intentionally using a visible browser to
rediscover or verify frontend requests.

Run the conservative offline identity comparison after a sample is present:

```bash
python -m scripts.audit_fantasy_identity
```

Only the allowlisted player-market JSON body and non-secret discovery metadata are
saved under `data/samples/fantasy_current/`. Cookies, bearer values, callback URLs,
authorization headers, and user-specific Fantasy-team ownership are excluded. If
the saved session expires, the explorer fails safely and asks you to rerun the
manual login command; it never automates password entry.

## Run the explorer

Download a small sample from E2025 Rounds 1 and 2, with detailed data for three games:

```bash
python -m scripts.explore_euroleague_api sample
```

Probe representative games across selected historical seasons:

```bash
python -m scripts.explore_euroleague_api coverage
```

Run both:

```bash
python -m scripts.explore_euroleague_api all
```

Useful explicit examples:

```bash
python -m scripts.explore_euroleague_api \
  --timeout 45 sample --season 2025 --rounds 1 2 --max-games 3

python -m scripts.explore_euroleague_api \
  --timeout 30 coverage --seasons 2025 2024 2023 --probe-games 2

python main.py --help
```

Global options such as `--timeout` and `--competition` go before the subcommand.

## Test

```bash
python -m unittest discover -v
python -m compileall -q src scripts tests main.py
python -m scripts.check_database --no-report
```

## Project layout

```text
.
├── src/data/
│   ├── euroleague_client.py   # retrying raw-response HTTP client
│   ├── fantasy_client.py      # narrow authenticated read-only Fantasy client
│   ├── fantasy_identity.py    # conservative offline player/team matching audit
│   ├── fantasy_market.py      # sanitized market-response analysis
│   ├── fantasy_security.py    # state permissions, allowlists, and redaction
│   └── normalizers.py         # pure normalization and quality helpers
├── src/modeling/
│   ├── fantasy_scoring.py      # versioned target rules
│   ├── core_features.py        # Phase 3A box-score features
│   ├── rich_reconstruction.py  # PBP stints, lineups, possession components
│   ├── rich_features.py        # modular Phase 3B materialization
│   └── rich_diagnostics.py     # same-subset baseline/feature diagnostics
├── src/db/
│   ├── migrations/            # forward-only canonical DuckDB schema
│   ├── database.py            # explicit connections and migration runner
│   ├── ingestion.py           # retained raw-to-canonical ingestion
│   ├── bulk_ingestion.py      # selective, cached, resumable history runner
│   ├── remote_ingestion.py    # raw-to-canonical official normalization
│   ├── fantasy_ingestion.py   # append-only, leakage-aware Fantasy markets
│   ├── identity_resolution.py # conservative Fantasy player crosswalk updates
│   ├── integrity.py           # canonical invariants
│   └── quality.py             # database quality report
├── scripts/
│   ├── explore_euroleague_api.py
│   ├── login_fantasy.py
│   ├── explore_fantasy_api.py
│   ├── audit_fantasy_identity.py
│   ├── audit_rotation_reconstruction.py
│   ├── init_database.py
│   ├── ingest_samples.py
│   ├── ingest_season.py
│   ├── ingest_archived_season.py
│   ├── ingest_history.py
│   ├── collect_fantasy_history.py
│   ├── resolve_fantasy_identity.py
│   ├── generate_phase25_reports.py
│   ├── collect_fantasy_snapshot.py
│   ├── ingest_fantasy_snapshot.py
│   ├── generate_rich_features.py
│   ├── validate_rich_features.py
│   └── check_database.py
├── data/
│   ├── db/                    # generated DuckDB file; ignored
│   ├── raw/                   # immutable local source artifacts; ignored
│   ├── reference/             # reviewed team aliases
│   └── samples/
│       ├── e2025_rounds_1_2/  # retained raw + normalized three-game sample
│       ├── fantasy_current/    # sanitized authenticated market/audit artifacts
│       ├── rotation_overtime_probe/
│       ├── historical_coverage.json
│       └── historical_coverage_boundary.json
├── reports/
│   ├── data_audit.md
│   ├── feature_source_matrix.md
│   ├── reference_project_lessons.md
│   ├── rotation_reconstruction_audit.md
│   ├── database_architecture.md
│   ├── database_quality.md
│   ├── historical_coverage.md
│   ├── schema_drift.md
│   └── storage_and_performance.md
├── tests/
├── requirements.txt
└── main.py
```

## What was tested

- EuroLeague v1 schedule, result, and roster XML
- v2 round games and player memberships
- v3 game reports, player/team stats, and the partially available team-comparison route
- v3 season aggregate traditional/advanced/misc/scoring field sets
- legacy live Header, Boxscore, PlaybyPlay, and Points feeds
- recent seasons and selected older seasons back to E2000
- the legacy-feed boundary around E2006/E2007
- current public Fantasy configuration, endpoint access behavior, and official scoring rules
- manual Playwright login, protected-state reuse, sanitized authenticated market JSON,
  and direct authenticated HTTP reads
- all 38 current-season Fantasy matchdays exposed by the legitimate configuration
- one player-level official Fantasy-points response at Matchdays 1 and 38
- duplicate IDs, missing fields, minute totals, DNPs, substitution events, event ordering, and cross-feed shot completeness
- lineup reconstruction in three regulation games and one four-overtime game
- full E2021-E2025 PBP rotation reconstruction with player-minute reconciliation,
  overtime, malformed-sequence, lineup-state, and family-specific quarantines
- empirically validated centimetre shot geometry, five spatial zones, player and
  opponent profiles, and boundary/conflict tests
- point-in-time rich source cutoffs plus deliberately injected target/future PBP,
  on/off, shot, and opponent-shot leakage failures
- forward-only DuckDB migrations, deterministic canonical IDs, foreign keys,
  uniqueness constraints, raw provenance, and leakage-safe Fantasy views
- complete core ingestion for E2018-E2025 and rich PBP/shot ingestion for E2021-E2025
- interrupted-run provenance cleanup, cached resume, and zero-new-record reruns
- 47 canonical database integrity checks, including on-disk raw hash verification

The `euroleague-api==0.1.1` package remains installed as the requested reference/convenience layer. The project uses direct requests for its audit because the package does not expose every useful endpoint and normalized DataFrames cannot preserve exact original responses.

## Main discoveries

- Schedules, results, v1 rosters, and v3 game stats returned usable data as far back as E2000 in the representative probes.
- Legacy box score, team/player stats, play-by-play, and shot feeds worked in tested games from E2007 onward; E2006, E2005, and E2000 returned empty bodies for those legacy calls.
- Player box scores include minutes, starter, full shooting/rebounding/playmaking/defensive stats, fouls, PIR, and plus-minus.
- Play-by-play contains substitutions, but source event numbers are not fully chronological. The sample adds a `source_sequence` field. Rotation reconstruction is possible with caveats: 92/96 audited player rows matched official minutes exactly; four rows in a four-OT game had paired ±60-second feed conflicts.
- Shot rows include coordinates and context flags, but the endpoint omits missed free throws.
- Aggregate endpoints expose useful percentages and player possessions, but the tested team payload did not expose pace or offensive/defensive rating; raw derivation is safer for backtesting.
- The current v2 `winner` field is unsafe: it disagreed with the final score in 19 of 20 sampled games.
- v3 `teamsComparison` was optional/partial: it succeeded for one sampled game and returned 404 for two games whose normal v3 stats existed.
- The authenticated current market returned 365 rows: 345 players and 20 coaches. It contains Fantasy IDs, price, position, team/opponent, Turn, average points, popularity, and availability flags.
- All config-listed E2025 Matchdays 1-38 are archived as distinct price/player-pool snapshots. The official Stats UI also exposes E2022-E2024, but its `cr` is constant across selected matchdays and therefore is quarantined rather than presented as historical price coverage.
- A separate single-player route returned official Fantasy total/component detail at Matchdays 1 and 38; full-pool and previous-season score coverage remain untested.
- Injury/probability, bench, and average values were identical for every shared player across all three old-matchday probes and have no timestamps. Treat them as current overlays, not historical as-of status.
- Fantasy and official IDs are separate namespaces. The versioned/reviewed Baskonia team alias and complete modern official context resolve 324/345 current players (93.9%). The remaining 21—3 ambiguous, 2 name/team conflicts, and 16 without an official candidate—remain unmapped rather than being fuzzy-matched.

## Phase 2.6 retained database

The database contains 2,558 game records (2,530 completed), 60,101 player-games, 5,058 team-games, 895,360 play-by-play events, 258,453 shots, and 2,352 roster memberships. It contains 13,191 validated E2025 Fantasy market rows plus 20,982 quarantined E2022-E2024 Stats observations. E2018-E2020 have core facts only; their absent PBP/shots are intentional. Exact season coverage is generated in [`reports/historical_coverage.md`](reports/historical_coverage.md).

E2018 game 21 is the sole requested core box-score miss (259/260, 99.62%); every other retained season has 100% player/team box coverage. Every requested rich season has 100% game-level PBP and shot coverage. Fourteen rich games are quarantined for literal invalid provider clock sentinels, without discarding their events.

E2023/170 remains quarantined for future rotation/on-off calculations because four official player-minute totals differ by ±60 seconds from source-order substitution reconstruction. It is also quarantined from strict chronological features because the retained header lacks a trustworthy UTC tip time. The raw and canonical observations are retained. `availability_events` intentionally has zero rows because no trustworthy timestamped historical injury source has yet been ingested.

## Generated sample

[`data/samples/e2025_rounds_1_2`](data/samples/e2025_rounds_1_2) contains:

- exact raw response bodies
- a response manifest with source URLs, UTC fetch times, byte counts, and SHA-256 hashes
- normalized games, player/team box scores, play-by-play, shots, and roster CSVs
- a recursive field/type inventory
- a generated data-quality summary

The retained sample has 20 schedule rows, 72 player rows, 6 team rows, 1,645 events, 497 shot rows, and 335 roster memberships. It is intentionally not a full-history download.

[`data/samples/fantasy_current`](data/samples/fantasy_current) contains the
sanitized 365-row current market, schema/value summary, public league config,
bounded historical comparisons, two single-player Fantasy-score samples, and
the identity audit. The reusable credential is
stored only under ignored `.auth/`, never in these artifacts.

[`data/samples/rotation_reconstruction_summary.json`](data/samples/rotation_reconstruction_summary.json)
records the four-game rotation invariants and the exact OT feed discrepancy.

## Important limitations

Coverage files use full v1 listings but only two representative detailed games per season. They demonstrate endpoint availability, not complete historical integrity. Do not start model training from these samples.

Several feeds are undocumented, schemas contain source misspellings, API state may be revised retrospectively, and Fantasy routes are authenticated and undocumented. Review the [Euroleague Basketball Terms of Use](https://inform.euroleague.net/terms) and applicable Fantasy/Fantaking terms before scaling collection or building a product.

## Recommended next step

Phase 8C is the stopping point. Do not start multi-Matchday planning automatically. Live
Fantasy use requires a genuine E2026 market and verified E2026 player-scoring
and game-management rules. A future participation model still requires enough
genuinely timestamped historical availability data; untimestamped old-matchday
status overlays remain quarantined.
