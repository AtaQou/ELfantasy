# Phase 3B same-population core benchmark

## Evaluation contract

This report does not train or tune a model. It re-scores the six frozen Phase 3A
baselines on the exact rows eligible for controlled Phase 3B comparisons.

- Population: `phase3b_primary_eval_eligible = true`
- Target: historically verified official-rule Fantasy points, E2022-E2025
- Semantics: `conditional_on_playing`
- Rich requirement: prior rotation, lineup, player-shot, opponent-shot history and
  at least 20 estimated on-court possessions in the last five valid PBP games
- Rows: 29,232 (`E2022` 6,833; `E2023` 6,984; `E2024` 6,899; `E2025` 8,516)
- Ordering: actual game datetime; every rich source time is strictly before the row
  cutoff
- Baselines: unchanged `phase3a_transparent_baselines_v1`, eight-season history

## Phase 3B verified-rich-scope metrics

| Baseline | Rows | MAE | RMSE | Spearman | Pearson |
|---|---:|---:|---:|---:|---:|
| Season average | 29,232 | 6.0084 | 7.7601 | 0.4672 | 0.4712 |
| Last 3 | 29,232 | 6.4632 | 8.3650 | 0.4135 | 0.4167 |
| Last 5 | 29,232 | 6.1865 | 7.9994 | 0.4426 | 0.4470 |
| EWMA | 29,232 | 6.1862 | 7.9991 | 0.4456 | 0.4486 |
| FP/min × minutes | 29,232 | 6.1708 | 7.9799 | 0.4456 | 0.4499 |
| 50/50 season/recent blend | 29,232 | **5.9953** | **7.7334** | **0.4704** | **0.4759** |

The 50/50 blend is the Phase 4 scalar-error/correlation benchmark for this exact
population. It was selected by the already-fixed definitions, not retuned in
Phase 3B.

## Ranking metrics

Metrics are pooled across 174 season/round groups on the same 29,232 rows.

| Baseline | Actual top 10 captured by predicted top 20 | Actual top 20 captured by predicted top 30 |
|---|---:|---:|
| Season average | **44.77%** | **51.32%** |
| Last 3 | 39.08% | 46.67% |
| Last 5 | 41.95% | 48.68% |
| EWMA | 41.49% | 48.56% |
| FP/min × minutes | 42.13% | 48.59% |
| 50/50 season/recent blend | 44.37% | 50.92% |

Season average is narrowly best on both ranking diagnostics; the blend remains
best on MAE, RMSE, Spearman and Pearson. Phase 4 should report both conclusions
rather than collapsing them into one notion of “best.”

## Original Phase 3A global benchmark

The original benchmark covers all 54,642 Phase 3A active-player rows across
E2018-E2025. It remains valid for that different question and was not overwritten.

| Baseline | MAE | RMSE | Spearman | Pearson |
|---|---:|---:|---:|---:|
| Season average | 5.9869 | 7.7361 | 0.4607 | 0.4659 |
| Last 3 | 6.4040 | 8.2982 | 0.4119 | 0.4178 |
| Last 5 | 6.1363 | 7.9387 | 0.4418 | 0.4488 |
| EWMA | 6.1438 | 7.9520 | 0.4430 | 0.4484 |
| FP/min × minutes | 6.1243 | 7.9244 | 0.4441 | 0.4510 |
| 50/50 season/recent blend | **5.9655** | **7.6994** | **0.4655** | **0.4732** |

The global 5.9655 MAE must not be compared directly with Phase 4 rich-feature
results unless the row population is restored to exactly those 54,642 rows.
