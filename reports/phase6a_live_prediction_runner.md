# Phase 6A live prediction runner

## Status and command

```bash
python -m scripts.predict_live \
  --season E2026 \
  --fantasy-config config/fantasy.json \
  --refresh
```

Use `--no-refresh` and a timezone-aware `--cutoff` for a deterministic stored-state
rerun. `--decision-file decisions.json --non-interactive` is the automation/UI path.

The current canonical E2026 state on 2026-08-19 has 380 scheduled games, 60
future games with UTC tip times, no current roster snapshot, and no Fantasy
market. The measured result is `NO_CURRENT_FANTASY_SLATE`: 0 market players, 0
mapped, 0 scored, 0 cold starts, and 0 unscorable market entries.

Phase 6A.1 removed the Phase 4B serialization blocker. The runner now preflights
the deterministic `phase4b_conditional_performance_frozen_v1` bundle even when
the current market is empty. Model files, fixed iterations/hyperparameters,
feature order, preprocessing, training-data state, code/protocol state, and the
row-level reproduction gate are hash-validated. Any mismatch still returns
`MODEL_ARTIFACT_MISMATCH`.

E2026 player-scoring compatibility is currently `SCORING_RULES_UNVERIFIED`.
The official current formula matches the frozen target, but its E2026 effective
version is not authoritatively established. Real non-empty predictions fail
closed; `--allow-unverified-scoring-dry-run` is explicitly non-production.

## Architecture and data flow

The CLI is presentation only. `src.live.prediction` exposes reusable matchday,
player-pool, scenario, prediction, and storage services. Phase 5A remains the
owner of refresh and shared feature construction.

```text
optional Phase 5A refresh
  -> earliest upcoming canonical phase/round
  -> latest whole Fantasy market batch and validated matchday mapping
  -> conservative current-roster/crosswalk join
  -> timestamped availability resolution and explicit scenarios
  -> shared Phase 3/4 features at prediction_generated_at
  -> frozen conditional minutes/direct/FP-per-minute artifact gate
  -> frozen Phase 5B known-absence redistribution
  -> bounded 0-40 / exact 200-minute reconciliation
  -> 25% direct + 75% (adjusted minutes x FP/min)
  -> ranking, JSON, CSV, immutable DuckDB snapshot
```

The slate is the earliest upcoming `phase_code + round_number`. Games without
tip times are excluded and reported. UTC is canonical; schedule-local datetime
is retained.

## Matchday, player pool, and availability

The latest whole market batch must contain one matchday. Resolution requires a
`VALIDATED_DIRECT` mapping or exact non-empty team/opponent slate context;
otherwise it is `MATCHDAY_AMBIGUOUS`. A missing market is
`MARKET_NOT_AVAILABLE`, producing `NO_CURRENT_FANTASY_SLATE`.

The player join retains mapping classification, method, manual-review flag, and
crosswalk ID. A mapped player must occur on the current slate with the same
team. Ambiguous, unmatched, stale-roster, and team-mismatch rows remain in
coverage and are never force-matched.

Source priority is unchanged. `AVAILABLE`/`PROBABLE` play;
`OUT`/`SUSPENDED`/`NOT_REGISTERED` are out; uncertain states require a user
decision; `LIMITED` plays normally absent an explicit restriction.
`QUESTIONABLE` never reduces conditional minutes automatically.

Interactive choices use the Phase 5A append-only override/role-limit tables.
One unknown can branch play/out. Multiple unknowns require named combinations
or explicit defaults; no probabilities or exponential expansion are invented.
Decision files can contain up to 64 named scenarios.

An OUT cold-start player with no scoreable baseline role is marked
`missing_role_status=UNKNOWN`. A 0-40 manual expected-role estimate is optional
and audited.

## Models and output

Expected versions are `phase4b_conditional_performance_frozen_v1` and
`phase5b_known_absence_redistribution_frozen_v1`. Artifact gates verify versions,
protocols, exact feature manifests, 25/75 weights, and model file hashes. There
is no training fallback. With no OUT player and no explicit role limit,
adjusted minutes equal baseline minutes.

Output retains run/cutoff, game and identity, team/opponent, position/credits,
availability and scenario, direct/rate components, baseline/adjusted minutes and
FP, deltas, interval fields/status, history/cold-start/completeness/freshness,
prediction/missing-role status, model and feature fingerprints, per-row feature
snapshot/provenance, and context-not-causality explanations. FP/credit is only a
diagnostic.

Phase 6A exposes `lower_prediction_interval` and `upper_prediction_interval`,
never a `ceiling` field. Adjusted rows are marked
`NOT_RECALIBRATED_AFTER_AVAILABILITY_ADJUSTMENT`; no interval heuristic is added.

## Performance and limitations

The final measured no-refresh empty-slate run took 2.05 seconds: 0.00 update,
0.99 feature/slate construction, and 0.00 redistribution/prediction. This is not
a full-slate inference benchmark.

The five largest limitations are: no E2026 Fantasy market; no E2026 current
roster; E2026 player-scoring rules are not season-version verified; no P(play);
and no post-redistribution interval calibration/true ceiling. No optimizer is
included.
