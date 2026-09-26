# Phase 6B central mean analysis

## Protocol

The primary target is player Fantasy performance conditional on participation:
all 29,232 E2022–E2025 verified rows have `actual_minutes > 0`; no DNP is encoded
as zero FP. The 22,399 outer-test predictions use `E2022 → E2023`,
`E2022–E2023 → E2024`, and `E2022–E2024 → E2025`. Inner chronological data,
never an outer season, selected iteration counts. Seed 17 and the standardized
player-scoring target `ELFC_PLAYER_V1_STANDARDIZED_2025` are fixed.

## Central candidates

| Model | MAE | RMSE | Actual − predicted bias | Spearman | Pearson |
|---|---:|---:|---:|---:|---:|
| frozen Phase 4B | 5.808650 | 7.579663 | +1.110517 | 0.506355 | 0.513485 |
| CatBoost RMSE mean | 5.874369 | 7.515070 | +0.083051 | 0.502965 | 0.510503 |
| CatBoost Huber | 5.840336 | 7.591200 | +1.016948 | 0.502529 | 0.509079 |

The predeclared mean gate required at least 0.5% relative RMSE improvement,
absolute mean bias at most 0.20 FP, and MAE degradation at most 0.10 FP. The RMSE
challenger passes: RMSE improves 0.852%, mean bias falls by 1.027 FP, and MAE
worsens by 0.0657 FP. It therefore becomes `expected_fp`, explicitly an
estimated conditional mean. The frozen value remains immutable and exposed as
`phase4b_central_fp`; it remains the better MAE-oriented benchmark.

`median_fp` is the calibrated and reconciled direct P50 model, not an alias for
the conditional mean.

## Positive-minute diagnostics

The primary data retain real downside. The RMSE mean model's MAE / actual-minus-
predicted bias by actual-minute band is: 0–5 (4.435 / −4.293), 5–10
(4.812 / −4.056), 10–15 (4.893 / −2.616), 15–20 (5.334 / −0.722),
20–25 (6.200 / +1.480), 25–30 (7.084 / +3.349), and 30+ minutes
(8.276 / +5.400). Low positive-minute games expose genuine downside and were
not censored or synthetically extrapolated. The remaining high-minute bias is a
material limitation.

The current E2026 scoring state remains `SCORING_RULES_UNVERIFIED`; this does
not affect historical research but still blocks production-ready live labels.
