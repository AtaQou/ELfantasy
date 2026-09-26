# Revised Phase 2.5 storage budget

This estimate was calculated before the revised bulk run from real local raw samples and the current canonical database. It is deliberately conservative and is not a promise of exact compression.

## Source volume

| Quantity | Estimate |
| --- | ---: |
| Core games, E2018-E2025 | 2,530 |
| Rich games, E2021-E2025 | 1,690 |
| Player-game rows | 60,720 |
| Team-game rows | 5,060 |
| PBP events | 992,030 |
| Shot rows | 275,893 |
| Roster memberships | about 2,524 |
| Fantasy market rows if 38 matchdays average the observed pool | about 13,158 |

Observed row averages were 24 player rows and 2 team rows per box-scored game, 587 PBP events per sampled game, 163.25 shots per sampled game, and 315.5 memberships per sampled season.

## Actual raw sample measurements

| Artifact | Sample | Files/rows | Total bytes | Average used |
| --- | --- | ---: | ---: | ---: |
| Box score JSON | retained E2023-E2025 | 5 games | 68,644 | 13,728.8/game |
| PBP JSON | retained E2023-E2025 | 5 games | 796,464 | 159,292.8/game |
| Shot JSON | retained E2024-E2025 | 4 games | 213,119 | 53,279.75/game |
| Roster JSON | retained E2024-E2025 | 2 seasons | 807,569 | 403,784.5/season |
| v2 round JSON | retained E2024-E2025 | 22 encoded games | 55,017 | 2,500.77/game |
| Fantasy market JSON | Matchdays 1, 19, 37, 38 | 4 snapshots | 1,119,605 | 279,901.25/matchday |
| Schedule + result XML | actual E2018-E2025 listings | 16 files | 2,429,794 | measured total |

The PBP average includes the retained four-overtime E2023/170 fixture, making the estimate mildly conservative.

## Projected raw storage

| Layer | Estimated bytes | Estimated MiB |
| --- | ---: | ---: |
| Core schedules/results | 2,429,794 | 2.32 |
| Core rosters | 3,230,276 | 3.08 |
| Core round JSON | 6,326,955 | 6.03 |
| Core box scores | 34,733,864 | 33.12 |
| **Core subtotal** | **46,720,889** | **44.56** |
| Rich PBP | 269,204,832 | 256.73 |
| Rich shots | 90,042,778 | 85.87 |
| **Rich subtotal** | **359,247,610** | **342.60** |
| All 38 Fantasy market raw snapshots | about 10,636,248 | about 10.14 |
| **Projected retained raw total** | **about 416,604,747** | **about 397.30** |

## Projected normalized DuckDB storage

Zstandard Parquet exports of the current logical tables measured approximately 352 bytes/game, 138/player-game, 743/team-game, 39.5/PBP event, 58.4/shot, 50/membership, and 46/Fantasy snapshot row. Those figures imply about 70 MB of compressed logical fact data at the target row counts.

DuckDB also stores indexes, UUID/string dictionaries, dimensions, provenance, checkpoints, ingestion runs, anomalies, and reusable free blocks. Applying a conservative 4x allowance plus fixed database overhead gives an expected normalized database of roughly **300-400 MB**. This range is more honest than extrapolating the physical file directly because the post-cleanup DuckDB intentionally retains reusable allocated blocks.

## Budget decision

| Measure | Projection |
| --- | ---: |
| Additional raw plus normalized data | roughly 0.7-0.9 GB |
| Expected final project data footprint | roughly 0.8-1.1 GB |
| Preferred threshold | 15 GB |
| Warning threshold | 20 GB |
| Hard stop | 25 GB |

**Decision: proceed.** The upper projection uses less than 8% of the 15 GB preferred limit. The ingestion runner checks actual raw plus DuckDB size after every season and stops automatically at 25 GB.

This budget excludes temporary safety copies under `/private/tmp`, the Python environment, Git metadata, and future external sources. No compression or lossy pruning of retained raw artifacts is warranted now.
