# Phase 4B final conditional performance model comparison

All rows use the identical 22,399-player-game E2023-E2025 chronological outer universe and remain conditional on playing.

| Model | MAE | RMSE | Spearman | Pearson | Bias | Top10/20 | Top20/30 |
|---|---|---|---|---|---|---|---|
| Phase 3 same-row blend | 6.0130 | 7.7600 | 0.4757 | 0.4808 | 0.0264 | 46.13% | 52.02% |
| Phase 4A direct winner | 5.8272 | 7.5874 | 0.5021 | 0.5088 | 0.9907 | 48.55% | 53.44% |
| Best decomposed | 5.8130 | 7.5903 | 0.5057 | 0.5126 | 1.1505 | 49.03% | 54.45% |
| Best calibrated direct | 5.8272 | 7.5874 | 0.5021 | 0.5088 | 0.9907 | 48.55% | 53.44% |
| Best hybrid | 5.8086 | 7.5797 | 0.5064 | 0.5135 | 1.1105 | 49.68% | 54.29% |

## Frozen decision

**hybrid** is frozen as `phase4b_conditional_performance_frozen_v1`. lowest chronological outer MAE with multi-fold support. Its MAE is **5.8086**, an improvement of **0.32%** versus Phase 4A and **3.40%** versus the original same-row blend.

The exact component manifests, fold hyperparameters, calibration parameters, interval method, prediction protocol, and fingerprint are in `data/samples/phase4b/final_model.json`.

## Fold stability

| outer_fold | Rows | MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| outer_e2023 | 6984 | 5.7056 | 7.4809 | 0.5156 | 0.5111 | 1.3162 |
| outer_e2024 | 6899 | 5.8017 | 7.5172 | 0.5310 | 0.5122 | 0.9875 |
| outer_e2025 | 8516 | 5.8988 | 7.7094 | 0.4977 | 0.4972 | 1.0415 |

## Competition stage

| Stage | Rows | Mean FP | Mean minutes | Role-change score | MAE | Bias |
|---|---|---|---|---|---|---|
| FINAL_FOUR_FINAL | 63 | 8.6952 | 19.0476 | 3.1647 | 6.0335 | 0.2283 |
| FINAL_FOUR_SEMIFINAL | 131 | 8.0260 | 18.3182 | 3.2969 | 6.0287 | -0.0679 |
| FINAL_FOUR_THIRD_PLACE | 43 | 10.4186 | 18.6047 | 3.4283 | 6.4912 | 3.3002 |
| PLAYOFFS | 1080 | 8.8203 | 19.1253 | 2.8723 | 5.7119 | 0.5336 |
| PLAY_IN | 190 | 8.8232 | 19.2105 | 2.9727 | 6.0430 | 0.2187 |
| REGULAR_SEASON | 20892 | 8.9655 | 18.8039 | 2.8866 | 5.8081 | 1.1540 |

## High-minute architecture comparison

| Model | Actual minutes | Rows | MAE | RMSE | Bias |
|---|---|---|---|---|---|
| CatBoost_direct | 25-30 | 3587 | 7.3757 | 9.3455 | 4.1220 |
| CatBoost_direct | 20-25 | 5042 | 6.3623 | 8.0812 | 2.2418 |
| CatBoost_direct | 30+ | 1827 | 8.6525 | 10.8921 | 6.2091 |
| Ridge_direct | 25-30 | 3587 | 7.0201 | 8.9025 | 3.2437 |
| Ridge_direct | 20-25 | 5042 | 6.1052 | 7.7653 | 1.3163 |
| Ridge_direct | 30+ | 1827 | 8.2087 | 10.3739 | 5.3244 |
| Decomposed | 20-25 | 5042 | 6.4098 | 8.1587 | 2.4435 |
| Decomposed | 25-30 | 3587 | 7.4259 | 9.4199 | 4.2505 |
| Decomposed | 30+ | 1827 | 8.5849 | 10.8025 | 6.0570 |
| Hybrid | 25-30 | 3587 | 7.4060 | 9.3915 | 4.2183 |
| Hybrid | 20-25 | 5042 | 6.3885 | 8.1294 | 2.3931 |
| Hybrid | 30+ | 1827 | 8.5933 | 10.8154 | 6.0951 |

No Phase 4A result was altered retroactively. Phase 4B stops here; availability/participation, optimization, simulation, and frontend work remain out of scope.
