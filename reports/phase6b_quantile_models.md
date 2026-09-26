# Phase 6B quantile models

## Selection

Direct FP quantiles were fitted for q=0.10/0.25/0.50/0.75/0.90/0.95 using
chronological CatBoost pinball objectives. The bounded P90 screen compared Core,
Core+Rotation, Core+On/off, Core+Player-shot, and LightGBM Core+Rotation. Weighted
inner raw P90 pinball losses were 1.441510, 1.441373, 1.445392, 1.441455, and
1.580766 respectively. Core+Rotation CatBoost won before outer-test inspection.
On/off was harmful and player-shot was effectively neutral; broad PBP-rich
feature expansion was not justified. Rotation supplied the small winning gain.

Adding standardized E2018–E2021 reduced aggregate inner P90 pinball from
1.441373 to 1.437079, but failed the required every-fold improvement
(the last fold worsened from 1.432382 to 1.440856). It was not adopted.

## Outer-fold quantile results

| Quantile | Pinball loss | Empirical coverage | Coverage error |
|---|---:|---:|---:|
| P10 | 1.087705 | 0.099603 | −0.000397 |
| P25 | 2.142625 | 0.248493 | −0.001507 |
| P50 | 2.929096 | 0.495602 | −0.004398 |
| P75 | 2.507503 | 0.746462 | −0.003538 |
| P90 | 1.468471 | 0.895977 | −0.004023 |
| P95 | 0.891762 | 0.947587 | −0.002413 |

Calibration uses finite-sample residual offsets learned on inner chronological
validation, conditionally by pre-game role band when at least 100 calibration
rows exist. Raw quantile crossing affected 0.5536% of rows; calibrated values
crossed on 0.2455%. Final row-wise increasing rearrangement reduces crossing to
exactly zero. Both pre- and post-reconciliation metrics are retained in the
research artifact.

## Player-type calibration

P90/P95 coverage is 0.8912/0.9414 for 30+ expected-minute stars,
0.8967/0.9525 for 25–30 minute roles, and 0.8961/0.9448 for under-10 roles.
The high-`expected_fp` band remains under-covered at 0.8669/0.9280 and is an
explicit limitation. Mean P90−P50 spread is 12.60 FP for 30+ roles versus 8.36
for under-10 roles. By historical-volatility band it is 11.51 FP for high
volatility versus 9.86 FP for low volatility.

The direct quantiles replace the old generic Phase 4B residual interval for
user-facing upside interpretation. The legacy interval remains stored for
reproducibility only.
