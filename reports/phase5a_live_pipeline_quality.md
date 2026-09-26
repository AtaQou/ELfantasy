# Phase 5A live pipeline quality

Generated at `2026-08-18T14:12:10.526235+00:00` for `E2026`.

## Current measured state

- Schedule coverage: **380 games**, **60 with authoritative UTC tips**, **0 completed**.
- Latest completed canonical game: **E2025/406 at 2026-05-24 21:00:00+03:00**.
- Current Fantasy coverage: **0 player rows**, latest capture **none**.
- Confident Fantasy mappings: **0/0**.
- Current slate: **0 players**; rows with credits: **0**.
- Availability: **{}**; active overrides: **0**.
- Frozen schema: **compatible**, required features **133**, missing **[]**.
- Latest end-to-end runtime: **15.53 seconds**.

The E2026 schedule is published but, at this snapshot, the official player endpoint returns zero roster rows and the current Fantasy market/config is not supplied. The slate therefore remains intentionally empty instead of carrying the prior season's players forward. Incremental round metadata is rate-limited to two newly needed rounds per run; this snapshot has 60/380 authoritative UTC times, and subsequent bounded runs add missing detail without erasing prior tips.

## Freshness

| Source | Last success | Age minutes | Stale |
| --- | --- | ---: | --- |
| schedule | 2026-08-18 17:05:48.187976+03:00 | 6.319293 | False |
| basketball_stats | 2026-08-18 17:05:48.187976+03:00 | 6.319293 | False |
| fantasy_market | — | — | True |
| availability | — | — | True |
| current_features | 2026-08-18 17:05:48.187976+03:00 | 6.319293 | False |

## Quality gates

- Historical completed-game facts remain immutable; only childless scheduled game metadata can update.
- Upcoming rows execute the historical core/rotation SQL; a historical-cutoff parity test compares frozen features at 1e-9 tolerance.
- Exact duplicate availability and market inputs are idempotent; distinct retrievals remain append-only.
- Missing sources produce UNKNOWN/stale state and warnings, not AVAILABLE or zero-injury assumptions.
- Current slate fingerprints exclude generation timestamps, so unchanged basketball/market/availability inputs are reproducible.
- Prediction-run storage retains prediction timestamp, feature cutoff, model version, feature version, input fingerprint, and prospective-outcome flag. Phase 5A does not generate performance or availability predictions.
