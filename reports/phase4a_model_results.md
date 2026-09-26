# Phase 4A chronological model results

## Decision

The Phase 4A winner is **CatBoost 1.2.10 with CORE + ROTATION**, seed 17. Its
genuine outer-fold MAE is **5.8272** on the identical 22,399 E2023-E2025 rows,
versus **6.0130** for the unchanged 50/50 Phase 3A blend on those rows. This is a
3.09% same-population improvement (0.1858 FP per row). A game-clustered paired
bootstrap gives a 95% interval of **0.1583 to 0.2119 FP** for the mean MAE
improvement; 53.37% of rows improve.

The locked Phase 3B number is also reproduced without alteration on its full
29,232-row universe: MAE **5.9952919891** (reported 5.9953). That full-universe
number must not be compared directly to ML outer predictions because E2022 has no
earlier historically verified season from which to make a verified-only outer
forecast. On the exact ML outer universe the correct benchmark is 6.0130.

All results remain conditional on the player actually playing. No DNP or
availability prediction is implied.

## Evaluation design

The protocol is defined in `reports/phase4a_ml_protocol.md`. Actual game UTC time
defines every boundary and games are group-safe.

| Outer fold | Outer train | Inner chronological validation | Outer test rows |
|---|---|---|---:|
| E2023 | E2022 | late 25% of grouped E2022 times | 6,984 |
| E2024 | E2022-E2023 | E2023 | 6,899 |
| E2025 | E2022-E2024 | E2024 | 8,516 |

The inner selection step alone chose hyperparameters and boosting iterations.
Outer labels were loaded only after the fold model was fitted. No outer value
selected a fold's parameters, iteration, seed, calibration, or ensemble weight.
All feature sets share exactly the same 22,399 outer rows.

## Primary same-row results

`Top10/20` is actual top-10 players captured by predicted top-20; `Top20/30` is
actual top-20 captured by predicted top-30. Rankings use 124 explicitly mapped
historical Fantasy slates (19,476 player rows).

| Model / feature set | Features | MAE | RMSE | Spearman | Pearson | Top10/20 | Top20/30 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Phase 3A 50/50 blend | fixed baseline | 6.0130 | 7.7600 | 0.4757 | 0.4808 | 46.13% | 52.02% |
| Ridge CORE | 106 | 5.8935 | **7.5209** | **0.5035** | 0.5091 | 47.02% | **54.41%** |
| Ridge ALL RICH | 222 | 5.9095 | 7.5636 | 0.4953 | 0.5014 | 46.29% | 52.75% |
| CatBoost CORE | 106 | 5.8303 | 7.5881 | 0.5016 | 0.5082 | 48.06% | 54.13% |
| **CatBoost CORE + ROTATION** | **132** | **5.8272** | 7.5874 | 0.5021 | 0.5088 | 48.55% | 53.77% |
| CatBoost ALL RICH | 222 | 5.8368 | 7.6002 | 0.5013 | 0.5076 | 47.98% | 53.48% |
| XGBoost CORE | 106 | 5.8848 | 7.5186 | 0.5021 | **0.5098** | 48.23% | **54.41%** |
| XGBoost ALL RICH | 222 | 5.8859 | 7.5218 | 0.5012 | 0.5092 | 47.58% | 54.09% |
| LightGBM CORE | 106 | 5.8504 | 7.5980 | 0.4982 | 0.5049 | 48.06% | 54.29% |
| LightGBM ALL RICH | 222 | 5.8508 | 7.6008 | 0.4981 | 0.5051 | **48.63%** | 53.64% |

CatBoost CORE + ROTATION wins the predeclared primary MAE. Ridge CORE wins RMSE
and overall Spearman; XGBoost CORE wins Pearson and ties Ridge on Top20/30;
LightGBM ALL RICH narrowly wins Top10/20. The choice therefore reflects the
predeclared MAE-first hierarchy, not dominance on every secondary metric.

## Fold performance

| Outer test | Rows | Blend MAE | Winner MAE | Winner RMSE | Spearman | Pearson |
|---|---:|---:|---:|---:|---:|---:|
| E2023 | 6,984 | 5.9086 | 5.7295 | 7.4902 | 0.5057 | 0.5095 |
| E2024 | 6,899 | 5.9813 | 5.8219 | 7.5513 | 0.5082 | 0.5271 |
| E2025 | 8,516 | 6.1242 | 5.9116 | 7.6951 | 0.4942 | 0.4948 |

The winner beats the blend in every fold. XGBoost CORE has the smallest
unweighted fold-MAE standard deviation (0.0740); CatBoost CORE + ROTATION is
almost identical (0.0743) and has the lowest mean/pooled MAE. E2025 is harder for
all methods, so the evidence is multi-fold rather than a single-season win.

## Hyperparameters and stability

The winning fold configuration was consistently CatBoost's predeclared
depth-6, learning-rate 0.04, L2 8, MAE-loss candidate. Inner validation selected
205 trees for E2023, 176 for E2024, and 191 for E2025 (zero-based best iterations
204, 175, 190). Respective inner MAEs were 5.7093, 5.7374, and 5.8237.

All-Rich seed stability was small and seeds were not selected:

| Model | Seeds | Mean MAE | SD | Min-max |
|---|---|---:|---:|---:|
| CatBoost | 17, 29, 43 | 5.8372 | 0.0023 | 5.8346-5.8402 |
| XGBoost | 17, 29, 43 | 5.8882 | 0.0019 | 5.8859-5.8907 |

## Usage and history-depth diagnostics

Winner MAE by actual post-game minutes is 3.8445 (`<10`, 3,590 rows), 5.0734
(`10-20`, 8,353), 6.3623 (`20-25`, 5,042), 7.3757 (`25-30`, 3,587), and
8.6525 (`30+`, 1,827). CatBoost wins below 10 minutes, LightGBM CORE wins 10-20,
and Ridge CORE wins every 20+ band. Minutes are used only for after-game
stratification, never as target-game inputs.

Winner MAE by prior EL games is 5.7859 (1-2; 335 rows), 5.2819 (3-4; 433),
4.9771 (5-9; 1,100), and 5.8849 (10+; 20,531). No zero-history row occurs in
this rich outer universe. Ridge is best for 1-2 games (5.7827); CatBoost wins all
other history groups and beats the blend in every group.

## Identity and standardized-history experiments

Removing only `player_id` from CatBoost ALL RICH changes MAE from 5.8368 to
5.8378 (+0.0010). For established players it slightly improves MAE
(5.8966 to 5.8952), while for fewer-than-10-history rows it worsens MAE
(5.1800 to 5.2071). Player identity is therefore not driving the result and does
not show material memorization benefit.

Adding standardized/counterfactual E2018-E2021 training history to CatBoost ALL
RICH changes verified-future MAE from 5.8368 to 5.8245 (-0.0123), improves
Spearman from 0.5013 to 0.5045, but worsens RMSE from 7.6002 to 7.6083 and
increases mean underprediction. It improves MAE in all three folds, but the
effect is only 0.21%; this is labelled negligible/mixed and does not replace the
verified-only winner.

## Cost and implementation

Approximate three-fold final-fit costs (excluding bounded inner search) were:

| Candidate | Train seconds | Inference seconds | Mean serialized size |
|---|---:|---:|---:|
| Ridge CORE | 4.1 | 0.8 | 28 KB |
| CatBoost CORE + ROTATION | 29.4 | 0.6 | 299 KB |
| XGBoost CORE | 13.8 | 1.0 | 190 KB |
| LightGBM CORE | 13.1 | 1.0 | 242 KB |

Installed libraries: scikit-learn 1.9.0, CatBoost 1.2.10, XGBoost 3.3.0,
LightGBM 4.7.0, DuckDB 1.5.5, pandas 3.0.5, and NumPy 2.5.2.

A pretrained foundation model is **not part of the primary benchmark**. The
local host is an Intel CPU-only Mac with 8 GB RAM. Current TabPFN documentation
limits practical CPU execution to about 1,000 rows and requires separate weight
licensing/authentication; TabICLv2 supports regression but recommends a GPU at
larger scale. This experiment has 7k-21k training rows and 106-222 features per
fold. No project data were uploaded to a hosted service. See the source links in
the protocol report.

## Reproducibility and conclusion

Twenty-one experiments produced 470,379 genuine outer predictions. Every
prediction retains row, experiment, fold, target, version, and model provenance.
Model artifacts retain fixed feature hashes, selected configurations,
iterations, preprocessing-state fingerprints, prediction fingerprints, timings,
and library versions. A fixed-seed winner refit reproduced its prediction hash.

The direct conditional-on-playing model is a real but modest improvement. Freeze
CatBoost CORE + ROTATION as the Phase 4A candidate and proceed to targeted Phase
4B only; do not treat it as a complete Fantasy decision system.
