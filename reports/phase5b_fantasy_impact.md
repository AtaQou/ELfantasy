# Phase 5B Fantasy impact under known absence sets

The Phase 4B point models are frozen. The tested adjustment is exactly `25% frozen
direct + 75% (redistributed minutes × frozen FP/min)`. The direct 25% component is
unchanged, and no performance model is retrained.

## Same-row result

- Matched absence-recipient Fantasy rows: **15,019**.
- Frozen MAE: **5.9290**; adjusted MAE:
  **5.8998**.
- MAE change: **-0.0292** points
  (-0.49%).
- Frozen RMSE: **7.7215**; adjusted RMSE:
  **7.6668**.
- Frozen Spearman/Pearson: **0.5070 /
  0.5117**; adjusted: **0.5132 /
  0.5173**.
- Top-10 actual in predicted Top-20: **0.5710**
  frozen versus **0.5726** adjusted.
- Top-20 actual in predicted Top-30: **0.6158**
  frozen versus **0.6162** adjusted.

| season | rows | frozen_mae | adjusted_mae |
|---|---|---|---|
| E2023 | 4859 | 5.7914 | 5.7599 |
| E2024 | 4771 | 5.9843 | 5.9553 |
| E2025 | 5389 | 6.0040 | 5.9767 |

| group | rows | frozen_mae | adjusted_mae | mae_change |
|---|---|---|---|---|
| important_absence_25_plus | 841 | 6.3019 | 6.1871 | -0.1148 |
| multiple_absences | 6337 | 6.1288 | 6.0857 | -0.0431 |
| single_absence | 8682 | 5.7831 | 5.7640 | -0.0191 |

These rows test teammate performance given the correct historical OUT set. They do
not measure availability prediction. Existing P10/P50/P90 intervals are not shifted
or relabelled as calibrated after a minutes scenario; adjusted live intervals remain
null until a dedicated uncertainty recalibration is validated.
