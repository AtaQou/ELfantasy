# Phase 4B calibration and uncertainty

## Upper-tail compression and bias

| Architecture | MAE | RMSE | Bias actual-pred | Max prediction | Max actual |
|---|---|---|---|---|---|
| Phase4A_direct | 5.8272 | 7.5874 | 0.9907 | 22.0057 | 57.2000 |
| best_decomposed | 5.8130 | 7.5903 | 1.1505 | 23.3348 | 57.2000 |
| best_calibrated_direct | 5.8272 | 7.5874 | 0.9907 | 22.0057 | 57.2000 |
| hybrid | 5.8086 | 7.5797 | 1.1105 | 22.6105 | 57.2000 |
| calibrated_hybrid | 5.8086 | 7.5797 | 1.1105 | 22.6105 | 57.2000 |
| direct_rmse_loss | 5.8744 | 7.5151 | 0.0831 | 23.7019 | 57.2000 |
| direct_huber_loss | 5.8403 | 7.5912 | 1.0169 | 22.7643 | 57.2000 |

Actual-score bands and predicted-score calibration bins for every serious architecture are retained in `data/samples/phase4b/results.json`, including 25-30 and 30+ outcomes.

| Architecture | >=25 rows | >=25 MAE | >=25 mean pred | >=30 rows | >=30 MAE | >=30 mean pred |
|---|---|---|---|---|---|---|
| Phase4A_direct | 1281 | 16.7580 | 13.0761 | 484 | 20.6402 | 13.8127 |
| best_decomposed | 1281 | 16.8093 | 13.0248 | 484 | 20.6311 | 13.8218 |
| hybrid | 1281 | 16.7965 | 13.0376 | 484 | 20.6334 | 13.8195 |

## Alternative direct losses

Inner validation selected **MAE**. Outer-test results were not used for this choice.

| Loss | Inner MAE |
|---|---|
| MAE | 5.7689 |
| RMSE | 5.8140 |
| Huber:delta=5.0 | 5.7761 |

| Architecture | MAE | RMSE | Bias | Max prediction |
|---|---|---|---|---|
| Phase4A_direct | 5.8272 | 7.5874 | 0.9907 | 22.0057 |
| direct_rmse_loss | 5.8744 | 7.5151 | 0.0831 | 23.7019 |
| direct_huber_loss | 5.8403 | 7.5912 | 1.0169 | 22.7643 |

## Calibration

Direct calibration selected `none`; hybrid calibration selected `none`. Method choice uses the later half of chronological inner validation after fitting on its earlier half; fold parameters are then refit on full inner validation. No outer target fits or selects calibration.

| Direct calibration method | Outer MAE | RMSE | Bias | Spearman |
|---|---|---|---|---|
| none | 5.8272 | 7.5874 | 0.9907 | 0.5021 |
| median_shift | 5.8295 | 7.5707 | 0.8223 | 0.5015 |
| affine | 5.8717 | 7.5285 | -0.0887 | 0.5019 |
| isotonic | 5.8944 | 7.5630 | -0.1276 | 0.4998 |

## Prediction intervals

The interval is an 80% split-conformal absolute-residual interval fitted on chronological inner predictions. Radii are conditional on stable, moderate, or large historical role change when at least 100 calibration rows are available; otherwise the fold-global radius is used. Outputs are `expected_fp`, `floor_fp`/`p10_fp`, and `ceiling_fp`/`p90_fp`.

| Base architecture | Coverage | Mean width | Median width |
|---|---|---|---|
| Phase4A_direct | 79.43% | 18.3064 | 18.4965 |
| best_decomposed | 79.47% | 18.2555 | 18.3644 |
| hybrid | 79.52% | 18.2929 | 18.4158 |
| best_calibrated_direct | 79.34% | 18.2567 | 18.4293 |
| calibrated_hybrid | 79.26% | 18.1720 | 18.3761 |
| direct_rmse_loss | 79.28% | 18.1933 | 18.2940 |
| direct_huber_loss | 79.47% | 18.2161 | 18.4116 |

Coverage by season, minutes band, role-change class, and 25+ actual performances is retained in the result artifact. Empirical coverage is reported rather than assumed to equal the nominal level.
