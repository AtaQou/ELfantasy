# Revised selective historical ingestion plan

Prepared 2026-08-11 after the abandoned E2007-E2025 attempt was rolled back. The policy is to retain the most useful modern history, not every technically available season.

## Retention policy

| Layer | Seasons | Contents | Expected completed games |
| --- | --- | --- | ---: |
| Core | E2018-E2025 | schedules, results, rounds, rosters, player/team box scores | 2,530 |
| Rich | E2021-E2025 | complete available play-by-play and shot feed | 1,690 |
| Not requested | E2007-E2017 | no bulk raw or normalized history | — |

The expected counts come from the locally cached official v1 result listings:

| Season | Completed games | Core requested | PBP/shots requested |
| --- | ---: | --- | --- |
| E2018 | 260 | Yes | No — policy |
| E2019 | 252 | Yes | No — policy |
| E2020 | 328 | Yes | No — policy |
| E2021 | 299 | Yes | Yes |
| E2022 | 328 | Yes | Yes |
| E2023 | 331 | Yes | Yes |
| E2024 | 330 | Yes | Yes |
| E2025 | 402 | Yes | Yes |

Projected normalized volume is approximately 60,720 player-games, 5,060 team-games, 992,030 PBP events, 275,893 shots, and roughly 2,524 roster memberships. Details and byte estimates are in [`storage_budget.md`](storage_budget.md).

## Controlled execution

1. **Stage A — core:** download/cache and normalize E2018-E2025 sequentially.
2. Validate completed-game, player-stat, team-stat, and roster coverage by season.
3. **Stage B — rich:** download/cache and normalize PBP/shots only for E2021-E2025.
4. Validate endpoint/game coverage and retain all source anomalies without rewriting them.
5. **Stage C — Fantasy:** retrieve only matchday IDs exposed by the legitimate current Fantasy config; archive sanitized responses and preserve unsafe historical status overlays as non-point-in-time fields.
6. Re-run deterministic identity matching and final integrity/storage reports.

The runner is single-threaded, uses HTTP retries/backoff plus conservative pacing, writes immutable raw files before normalization, and skips valid cached artifacts. A rerun converges on existing raw hashes and uniqueness constraints. `KeyboardInterrupt` is recorded as a failed run, while completed raw files remain valid resume checkpoints.

Storage is checked before the run and after every season. The preferred total footprint is below 15 GB, warning threshold 20 GB, and hard stop 25 GB. The current projection is below 1 GB, far below all boundaries.

Commands:

```bash
python -m scripts.ingest_history --stage core
python -m scripts.ingest_history --stage events --start-season 2021
```

`--stage all` applies the asymmetric policy automatically: core E2018-E2025, then events E2021-E2025. Requests outside these windows fail closed unless `--allow-outside-policy` is explicitly supplied after a future project decision.

## Completeness gate

A retained season is core-complete only when official completed-game expectations match canonical games, all usable games have player statistics, and team statistics have two team rows. PBP and shots are evaluated separately. For E2018-E2020, absent rich data are labelled **NOT REQUESTED BY POLICY**, never “missing.” Requested endpoint failures remain in `ingestion_failures`; structural problems remain raw and are quarantined.

## Training-window policy

Storage depth and model training depth remain independent. Phase 3 must accept configurable last-3, last-5, and last-8-season windows and select among them with chronological backtesting. No stored season is automatically included in every model.

## Future Live Update Strategy

The same idempotent paths support future incremental work without a schema redesign:

- refresh the mutable current schedule by stable season/game key, then ingest only newly completed games;
- treat completed box/PBP/shot responses as immutable artifacts and skip an existing valid hash;
- append every Fantasy market observation with `observed_at`, matchday, and Turn instead of overwriting it;
- append timestamped availability/news observations so queries can reconstruct what was known before time T;
- later attach domestic box-score workload through provider IDs and explicit player/team crosswalks;
- later append timestamped pregame odds rather than replacing closing/current values.

No scheduler, polling service, domestic collector, news collector, or odds integration is implemented in Phase 2.5.

