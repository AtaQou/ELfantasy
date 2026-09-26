# Phase 6B high-score probabilities

## Base rates and retention gate

| Threshold | Primary positives | Primary rate | E2022/E2023/E2024/E2025 positives | Direct model? |
|---|---:|---:|---|---|
| 20+ | 3,344 | 11.4395% | 710 / 758 / 851 / 1,025 | yes |
| 25+ | 1,607 | 5.4974% | 326 / 365 / 416 / 500 | yes |
| 30+ | 606 | 2.0731% | 122 / 140 / 150 / 194 | yes |
| 35+ | 236 | 0.8073% | 56 / 46 / 55 / 79 | yes |
| 40+ | 65 | 0.2224% | 14 / 14 / 18 / 19 | no |

The fixed gate is at least 150 total positives and 30 in every outer-evaluation
season. Thus 20–35 use direct CatBoost classifiers. The requested 40+ field is
derived from the calibrated survival curve constrained by the quantiles; it is
not presented as a directly validated classifier.

## Chronological outer evaluation

| Threshold | Brier | Log loss | ROC-AUC | PR-AUC | Calibration slope | Intercept |
|---|---:|---:|---:|---:|---:|---:|
| 20+ | 0.090932 | 0.304186 | 0.789354 | 0.312742 | 0.9706 | −0.0695 |
| 25+ | 0.049389 | 0.183778 | 0.808861 | 0.191330 | 0.9766 | −0.0304 |
| 30+ | 0.020230 | 0.088342 | 0.824272 | 0.094489 | 0.9874 | +0.0325 |
| 35+ | 0.007828 | 0.040474 | 0.829759 | 0.039022 | 0.9656 | −0.1645 |
| 40+ derived diagnostic | 0.002289 | 0.014785 | 0.846363 | 0.011294 | 0.8446 | −1.3370 |

Platt/logistic calibration parameters were learned on inner chronological
validation only. Outer folds were never used to fit calibrators. Raw independent
classifiers violated probability order on 2.4064% of rows; calibrated values did
so on 5.7458%. Decreasing PAVA followed by quantile-implied survival bounds
reduces final violations to zero and guarantees values in [0,1]. The 40+
diagnostic's poor slope/intercept reflects its sparse derived status and must not
be overstated.
