# Storage and Performance

Generated: `2026-08-12T01:53:15.632097+00:00`

- DuckDB `1.5.5` file: **403.8 MiB** (423,374,848 bytes).
- Raw artifacts: **366.2 MiB** (383,937,953 bytes across 6,307 files).
- Combined normalized + raw footprint: **769.9 MiB** (0.807 GB).
- Policy result: below the 15 GB preferred threshold; the 20 GB warning and 25 GB hard stop were not approached.

## Raw size by retained season

| Season | Raw MiB |
| --- | ---: |
| E2018 | 4.5 |
| E2019 | 4.4 |
| E2020 | 5.7 |
| E2021 | 57.9 |
| E2022 | 65.5 |
| E2023 | 66.0 |
| E2024 | 67.3 |
| E2025 | 84.6 |

## Raw size by source type

| Type | Files | MiB | Share of raw |
| --- | ---: | ---: | ---: |
| `play_by_play` | 1,690 | 231.3 | 63.2% |
| `shots` | 1,690 | 80.5 | 22.0% |
| `boxscores` | 2,530 | 32.7 | 8.9% |
| `fantasy` | 38 | 10.1 | 2.8% |
| `games` | 323 | 6.1 | 1.7% |
| `rosters` | 8 | 2.9 | 0.8% |
| `schedules` | 8 | 1.4 | 0.4% |
| `results` | 8 | 0.9 | 0.3% |
| `v3_stats` | 3 | 0.1 | 0.0% |
| `headers` | 4 | 0.0 | 0.0% |
| `v3_reports` | 3 | 0.0 | 0.0% |
| `v3_team_comparisons` | 1 | 0.0 | 0.0% |
| `.gitkeep` | 1 | 0.0 | 0.0% |

Core official raw (results, schedules, rounds, rosters, box scores) is compact; PBP is the dominant source, followed by shots. This validates the asymmetric retention policy: core E2018-E2025 and rich E2021-E2025.

## Canonical row volumes

| Table | Rows |
| --- | ---: |
| `games` | 2,558 |
| `player_game_stats` | 60,101 |
| `team_game_stats` | 5,058 |
| `play_by_play_events` | 895,360 |
| `shots` | 258,453 |
| `player_team_memberships` | 2,352 |
| `fantasy_market_snapshots` | 13,191 |
| `raw_artifacts` | 6,306 |

## Runtime evidence

Ingestion was intentionally sequential and conservatively paced. Interrupted/retried development runs remain in `ingestion_runs`; summing all run durations would therefore overstate one clean pass.

| Source | Status | Runs | Downloaded | Inserted | Errors | Recorded seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `archived_official_season` | FAILED | 6 | 0 | 0 | 6 | 2,312 |
| `archived_official_season` | SUCCEEDED | 30 | 6,159 | 1,059,850 | 0 | 12,556 |
| `bulk_download_core` | FAILED | 2 | 0 | 0 | 2 | 1,340 |
| `bulk_download_core` | SUCCEEDED | 14 | 3,247 | 0 | 0 | 18,575 |
| `bulk_download_events` | SUCCEEDED | 10 | 3,375 | 0 | 0 | 5,937 |

The slowest recorded retained-window run was `bulk_download_core` for E2024 (SUCCEEDED, 11,775 seconds). Network collection, especially PBP, was the bottleneck; local cached normalization and idempotent reruns are much faster.

Fantasy storage contains 38 matchdays (13,191 rows, Matchdays 1-38). Its append-only raw footprint remains small relative to event feeds.

No compression or lossy pruning was applied to retained artifacts. If footprint later approaches policy thresholds, compression/Parquet should be evaluated separately after reproducibility tests—not during ingestion.
