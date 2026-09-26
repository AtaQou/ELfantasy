# Role compression and cold-start fix

## Result

The frozen minutes model was materially compressed on Matchday 1. Across all outer-fold seasons, the predicted star-to-bench gap was 11.96 minutes versus 14.20 actual. Returning stars were underpredicted by 1.37 minutes on average, while returning bench players were overpredicted by 0.88. Compression was no longer systematic after the opening rounds.

The implemented calibration is a leakage-safe convex blend for returning players with zero current-season appearances:

`corrected minutes = 70% frozen prediction + 30% previous-season MPG`

The previous-season value is read only from the exact prior EuroLeague season and is checked against the prediction cutoff. New EuroLeague players and anyone with at least one current-season appearance remain unchanged. This is not a trained model, does not special-case players, and does not alter the optimizer.

The weight was selected on E2023-E2024 outer-fold predictions. E2025 was held out. The validation grid also tested previous-season last-five minutes and an MD2 weight; neither improved early-season MAE, so the smallest selected schedule is 30% at zero games and 0% after the first appearance.

## Historical validation

| E2025 holdout window | N | MAE before | MAE after | Bias before | Bias after | Predicted SD before → after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MD1 | 151 | 5.382 | **5.216** | +0.292 | +0.404 | 4.079 → 4.402 |
| MD1-3 | 563 | 4.629 | **4.577** | -0.045 | +0.019 | 5.278 → 5.294 |
| MD2-3 | 412 | 4.354 | **4.342** | -0.168 | -0.122 | 5.644 → 5.573 |
| MD4-5 | 426 | 4.545 | 4.560 | -0.721 | -0.714 | 5.731 → 5.729 |
| MD6+ | 7,527 | 4.449 | 4.449 | -0.759 | -0.753 | 6.056 → 6.051 |

Later-season performance is effectively unchanged. The small MD4-5 MAE movement is +0.015 minutes; MD6+ is unchanged at displayed precision.

For E2025 returning players on MD1:

| Previous-season role | Predicted before | Predicted after | Actual | Bias before → after |
| --- | ---: | ---: | ---: | ---: |
| STAR / PRIMARY | 24.93 | 26.38 | 27.97 | -3.04 → -1.59 |
| STARTER | 22.82 | 23.43 | 22.51 | +0.31 → +0.92 |
| ROTATION | 18.03 | 17.96 | 17.60 | +0.43 → +0.36 |
| BENCH / LIMITED | 13.64 | 12.81 | 12.80 | +0.84 → +0.01 |

The MD1 star-to-bench spread expands from 11.29 to 13.57 minutes, closer to the 15.17 actual spread. Across every frozen outer fold it moves from 11.96 to 14.10 versus 14.20 actual.

## Existing role signal exposed

The UI label is a presentation mapping of the final expected-minutes signal already produced by the live history/minutes pipeline:

- `STAR / PRIMARY`: 28+ expected minutes
- `STARTER`: 22 to under 28
- `ROTATION`: 14 to under 22
- `BENCH / LIMITED`: under 14

Because the label is derived from final adjusted expected minutes, it remains dynamic with current/recent history and availability redistribution. The existing minutes-trend feature is exposed as `TRENDING UP`, `STABLE`, or `TRENDING DOWN` when available.

Player detail now prominently displays Expected Role, expected minutes, recent minutes, previous-season MPG, and the existing role trend. Recommendation roster rows show the compact role, expected minutes, and predicted FP without technical model metadata.

## E2026 smoke sample

The exact calibration helper was applied read-only to the latest stored E2026 MD1 slate (243 scored players), with one representative row per exposed role:

| Player | Old minutes | Corrected minutes | Role |
| --- | ---: | ---: | --- |
| Derrick Alston Jr. | 34.73 | 31.04 | STAR / PRIMARY |
| Justin Robinson | 27.54 | 24.97 | STARTER |
| Lamar Stevens | 17.10 | 17.96 | ROTATION |
| Moustapha Fall | 12.89 | 11.04 | BENCH / LIMITED |

The production prediction fingerprint now includes the minutes-calibration version, preventing an older immutable prediction snapshot from being reused after this change.

## Targeted verification

- Calibration boundary and eligibility assertions passed.
- Historical notebook reran successfully with point-in-time prior-season checks.
- E2026 repository API exposed a valid role for all 243 scored players.
- Recommendation generation returned role labels on every player.
- Player-detail and recommendation-card render hooks passed focused source assertions.
- `node --check web/control_center/app.js` passed.
- No model was retrained, no optimizer code was changed, and the full test suite was not run. `pytest` is not installed in the configured project interpreter, so the focused test functions were executed directly.

Reproducible analysis: `notebooks/role_compression_cold_start_fix.ipynb`.
