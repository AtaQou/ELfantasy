# Revised Phase 2.5 cleanup audit

Cleanup was performed on 2026-08-11 after the original E2007-E2025 bulk run was stopped. The cleanup boundary was established from `ingestion_runs`, fact-row `ingestion_run_id` provenance, `raw_artifacts`, checkpoints, and exact raw paths before any deletion.

## Interrupted/abandoned runs

The following 13 runs were created by the abandoned Phase 2.5 attempt. The first 12 completed E2007/E2008 download or offline-normalization work; the final E2009 run was interrupted and has been closed as `FAILED` with an `INTERRUPTED` audit message. Run rows were intentionally retained as an audit ledger even though their data were rolled back.

| Run ID | Source | Season | Result before cleanup |
| --- | --- | --- | --- |
| `c149f9ed-2cfb-49db-b302-b0a42184161b` | bulk_download_core | E2007 | succeeded smoke test |
| `d504a09c-f207-492e-9f29-71fa0ce7d11e` | archived_official_season | E2007 | succeeded smoke normalization |
| `950e07f8-b100-47a7-b1f4-f65930ebc719` | bulk_download_events | E2007 | succeeded two-game smoke test |
| `b9fbde86-ca23-4222-9f32-7de7b14e7638` | archived_official_season | E2007 | succeeded event normalization |
| `fef893af-aebf-48f5-8f15-91daaf926fec` | bulk_download_core | E2007 | idempotency check |
| `c66d5eb1-3c0b-4aba-9fbb-b78949697ec0` | archived_official_season | E2007 | idempotency check |
| `179b0774-cd5e-419d-b1da-0fdb7c534ba4` | bulk_download_events | E2007 | idempotency check |
| `de7ced06-1715-44f1-b60f-585bb750137c` | archived_official_season | E2007 | idempotency check |
| `85aa91f1-5cb9-4cbf-99d0-6f11e73e9bca` | bulk_download_core | E2007 | full season download |
| `d3835b0c-e4c9-4659-9c55-6f4eb6dc58cc` | archived_official_season | E2007 | full season normalization |
| `b4384d8f-9bd1-4819-a9ec-68fef63722a9` | bulk_download_core | E2008 | full season download |
| `0e0bcaa8-19c6-447e-bcee-d01abdf4aa5e` | archived_official_season | E2008 | full season normalization |
| `e8ce4acf-a70b-41af-93ed-34f4a10d144e` | bulk_download_core | E2009 | interrupted after 139 box scores |

## Database rows removed

Only E2007/E2008 records owned by the run set above were normalized. Before deletion, an assertion verified that no E2007/E2008 game or registered raw artifact belonged to a pre-existing run. E2009 had no normalized games.

| Table | Rows removed |
| --- | ---: |
| seasons | 2 |
| games | 419 |
| player_game_stats | 9,678 |
| team_game_stats | 838 |
| play_by_play_events | 1,042 |
| shots | 324 |
| player_team_memberships | 688 |
| raw_artifacts | 477 |
| ingestion_checkpoints | 641 |
| player_aliases | 2,065 |
| team_aliases | 125 |
| orphan players | 980 |
| orphan teams | 16 |

Seven players and 13 teams touched by E2007/E2008 also had retained references. They and their retained aliases were preserved. Parent entities were removed only after checks across retained games, memberships, PBP, shots, Fantasy crosswalks/snapshots, and availability events found no references.

DuckDB foreign-key indexes require dependent and parent deletes in separate committed stages. The first attempted single transaction was rejected and rolled back cleanly; the final cleanup used dependency-ordered commits backed by a temporary safety copy.

## Raw files removed

Exactly 657 files (12,616,815 bytes) under `data/raw/euroleague/E2007` through `E2017` were removed:

- 477 E2007/E2008 registered artifacts;
- 164 E2009 files downloaded before interruption, including 139 box scores;
- 16 planning-only E2010-E2017 schedule/result files.

No `data/samples/` fixture matched E2007, E2008, or E2009. Phase 1/1.5 samples, the E2023/E2024/E2025 retained raw data, authentication state, and Fantasy artifacts were not touched.

## Size change

| Storage | Before | After | Change |
| --- | ---: | ---: | ---: |
| DuckDB file | 55,586,816 B (53.01 MiB) | 53,751,808 B (51.26 MiB) | -1,835,008 B |
| `data/raw/` | 18,257,509 B (17.41 MiB) | 5,640,694 B (5.38 MiB) | -12,616,815 B |

The DuckDB file retains reusable allocated blocks, so physical shrinkage is smaller than the deleted logical row volume. This is harmless and avoids a risky database rewrite merely to reclaim a few megabytes.

## Intentionally retained

- all 23 Phase 2 representative games in E2023/E2024/E2025;
- 120 player-games, 10 team-games, 2,935 PBP events, 653 shots, and 631 memberships;
- all 1,385 Fantasy market rows, 365 Fantasy entities, and 345 crosswalk rows;
- both existing Phase 2 quarantines/anomalies;
- all Phase 1/1.5 sample fixtures and reports;
- all ingestion-run rows, including the abandoned runs, for audit history;
- `.auth/euroleague.json` (ignored and never read during cleanup).

## Validation gate

After cleanup, all **52/52** then-existing tests passed and the retained row counts matched the validated Phase 2 baseline. After the revised ingestion completed, the final gate passed **62/62** tests plus all **35/35** database integrity checks. The exact temporary raw safety archive and database copy under `/private/tmp` were then deleted; they are not retained source artifacts.

There were no ambiguous provenance cases after the ownership assertions. The abandoned `reports/historical_ingestion_plan.md` is being replaced by the revised E2018-E2025/E2021-E2025 policy rather than retained as an active plan.
