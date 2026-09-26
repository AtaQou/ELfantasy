# Phase 4A out-of-fold error analysis

This report is descriptive and was produced only after the predeclared outer
predictions were frozen. Findings are inputs to future work, not retroactive
reasons to retune Phase 4A.

## Seasonal generalization

| Outer season | Rows | Blend MAE | Winner MAE | Difference | Winner RMSE |
|---|---:|---:|---:|---:|---:|
| E2023 | 6,984 | 5.9086 | 5.7295 | -0.1790 | 7.4902 |
| E2024 | 6,899 | 5.9813 | 5.8219 | -0.1594 | 7.5513 |
| E2025 | 8,516 | 6.1242 | 5.9116 | -0.2126 | 7.6951 |

The model wins every future season, but absolute error rises over time and E2025
has the weakest correlations (Spearman 0.4942, Pearson 0.4948). This indicates
seasonal drift and/or a harder E2025 population, not a single-fold failure.

## Actual-minute diagnostics

Actual target-game minutes are post-game diagnostic labels only.

| Minutes | Rows | Blend MAE | Winner MAE | Best serious model |
|---|---:|---:|---:|---|
| <10 | 3,590 | 4.3840 | **3.8445** | CatBoost + Rotation |
| 10-20 | 8,353 | 5.4353 | 5.0734 | LightGBM CORE (5.0589) |
| 20-25 | 5,042 | 6.4687 | 6.3623 | Ridge CORE (6.1052) |
| 25-30 | 3,587 | 7.2178 | 7.3757 | Ridge CORE (7.0201) |
| 30+ | 1,827 | 8.2314 | 8.6525 | Ridge CORE (8.2087) |

The pooled CatBoost win is driven largely by lower-minute rows. It loses to the
blend in the two highest-minute bands and Ridge is materially better from 20+
minutes. This is the clearest evidence that expected-minutes/production
decomposition should be the first Phase 4B experiment.

## Prior-history depth

| Prior EL games | Rows | Blend MAE | Winner MAE | Best serious model |
|---|---:|---:|---:|---|
| 1-2 | 335 | 6.8130 | 5.7859 | Ridge CORE (5.7827) |
| 3-4 | 433 | 5.4477 | **5.2819** | CatBoost + Rotation |
| 5-9 | 1,100 | 5.2272 | **4.9771** | CatBoost + Rotation |
| 10+ | 20,531 | 6.0539 | **5.8849** | CatBoost + Rotation |

The rich eligible outer population has no true zero-history rows; PBP/shot
warm-up and all-major-family eligibility necessarily remove that diagnostic.
Results therefore support near-cold-start handling, not a true new-player claim.

## Calibration and range

The winner's mean prediction is 7.9632 versus a mean actual of 8.9538, a mean
residual of +0.9907 FP. Its prediction range is -0.456 to 22.006, while actuals
range from -11.0 to 57.2 (99th percentile 34.0). It therefore strongly compresses
the target distribution and cannot express observed ceiling games.

By predicted bin, mean residual remains positive: +1.184 (`<5`), +0.923
(`5-10`), +0.870 (`10-15`), +0.919 (`15-20`), and +0.619 (`20-25`). There are no
winner predictions at 25+. No clipping or post-hoc calibration was applied.
XGBoost CORE is better calibrated overall (mean residual +0.0444) while retaining
a worse MAE, which is a useful Phase 4B trade-off.

## Player diagnostics

Only players with at least 15 genuine outer observations are included. The
hardest by MAE include Guerschon Yabusele (9.84 over 26 rows), Richaun Holmes
(9.65/18), and McKinley Wright IV (9.35/37). The largest systematic
underpredictions include Sasha Vezenkov (mean residual +5.60 over 77 rows) and
Jean Montero (+5.10/36). Kevin Pangos is among the largest systematic
overpredictions (-2.66/17).

These cases share combinations of elite/high-ceiling production, changing role,
or limited/transitioning history. The names are diagnostic examples, not a basis
for refitting Phase 4A.

## Model-error diversity

Genuine OOF residuals are extremely similar. Pearson residual correlations are
0.9929 winner-vs-XGBoost, 0.9932 winner-vs-LightGBM, and 0.9851 winner-vs-Ridge;
all tree-pair correlations exceed 0.993. A simple ensemble is therefore unlikely
to add meaningful value without a genuinely different minutes/production or
uncertainty model. Ensemble weights were not fitted.

## Remaining limitations

1. This is conditional-on-playing; actual availability and DNP risk are absent.
2. EuroLeague-only history misses domestic workload, injuries, and other
   point-in-time role context.
3. The direct regression compresses high-end outcomes, and the richest feature
   families add almost no stable MAE value at current sample depths.

E2025 has been inspected historically and is not presented as an ultimate virgin
holdout. A frozen future pipeline should receive prospective E2026 evaluation
under a protocol defined before observing those results.
