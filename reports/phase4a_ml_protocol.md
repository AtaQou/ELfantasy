# Phase 4A pre-registered ML protocol

This protocol was written and frozen before any outer-test ML score was calculated. Phase
3B remains frozen at dataset hash
`c02fd9e781cdc090f6bedd2c16b2ca47d3d47007d79ed25ab9c8cd342f8c64b3`,
core pipeline `phase3a_core_boxscore_v1`, and rich pipeline
`phase3b_rich_v1`.

## Target and evaluation population

- Primary target: historically verified `actual_fantasy_points` only.
- Primary target seasons: E2022-E2025.
- Semantics: performance `conditional_on_playing`.
- Frozen eligible population: 29,232 rows.
- Primary selection metric: MAE.
- Secondary metrics: RMSE, Pearson, Spearman, Fantasy-slate ranking recall,
  within-slate Spearman, fold stability and practical cost.
- Same-row ML outer-test population: E2023-E2025, 22,399 rows. E2022 cannot be an
  outer test in a verified-only experiment because no earlier historically
  verified target season exists; it remains training/inner-validation history.
- The locked Phase 3B baseline is reproduced on all 29,232 rows, and separately
  recalculated on the exact 22,399 ML outer-test rows.

No Fantasy credit, target-game minutes/starter/role, target-game PBP/shot result,
post-game field, source timestamp, game identifier, or target value is a model
input. E2022-E2024 quarantined credits are absent.

## Nested chronological folds

Actual UTC target game time defines ordering. Games are never split across roles.

| Outer fold | Inner train | Inner future validation | Full outer train | Untouched outer test |
|---|---:|---:|---:|---:|
| E2023 | First 75% of grouped E2022 times: 5,184 rows | Late E2022: 1,649 | E2022: 6,833 | E2023: 6,984 |
| E2024 | E2022: 6,833 | E2023: 6,984 | E2022-E2023: 13,817 | E2024: 6,899 |
| E2025 | E2022-E2023: 13,817 | E2024: 6,899 | E2022-E2024: 20,716 | E2025: 8,516 |

For every boundary, `max(earlier target_game_time) < min(later
target_game_time)`. Same-time games remain together. Rescheduled games therefore
follow actual datetime rather than nominal round.

## Selection and outer-test isolation

For each fold/model/manifest:

1. fit every predeclared candidate on inner train;
2. fit preprocessing only on inner train;
3. use chronological inner validation for MAE selection and boosting early
   stopping;
4. choose the lowest inner-validation MAE (configuration order breaks exact
   ties);
5. freeze its best iteration;
6. refit on full outer-train history with that fixed iteration and no access to
   outer-test labels or features during fitting;
7. predict the untouched outer test once and persist predictions.

Outer-test metrics do not select hyperparameters, feature families, iterations,
seeds, calibration or ensemble weights. The winning hierarchy is predeclared:
lowest stable mean chronological MAE first; RMSE, Spearman, Fantasy ranking,
cross-fold stability, history-depth behavior and complexity are secondary.

## Train-only preprocessing

Ridge, XGBoost and LightGBM use a fresh per-fit scikit-learn `ColumnTransformer`:

- numerical: train-only median imputation with empty-column retention;
- categorical: train-only constant missing token then one-hot encoding with
  unknown future categories ignored;
- Ridge alone standardizes numerical values using train-only means/scales.

The pipeline is fitted jointly with the estimator. No category-target encoder is
used. [Scikit-learn documents](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.OneHotEncoder.html)
that `handle_unknown="ignore"` permits categories unseen during fit.

CatBoost receives sorted training rows, native missing numerical values and string
categoricals, with `has_time=true`. Its native categorical processing is fitted
inside each training call; no full-data target statistic is materialized.
[CatBoost's official fit API](https://catboost.ai/docs/en/concepts/python-reference_catboostregressor_fit)
supports explicit categorical columns and validation-based early stopping.

## Frozen feature manifests

The complete ordered column lists and SHA-256 hashes are retained in
`data/samples/phase4a/protocol.json`.

| Manifest | Features | Question |
|---|---:|---|
| CORE | 106 | Phase 3A information only |
| CORE + Rotation | 132 | Rotation/role increment |
| CORE + On/off | 124 | Contextual on/off increment |
| CORE + Lineup/teammate | 118 | Stability/interaction increment |
| CORE + Player shot | 139 | Player spatial profile increment |
| CORE + Opponent shot | 130 | Opponent spatial defense increment |
| CORE + Rotation + On/off | 150 | Related PBP families jointly |
| CORE + Shot all | 166 | Player/opponent/style shot context jointly |
| CORE + All rich | 222 | Complete Phase 3B increment |

Default categoricals are season, home/away, team, opponent, historical Fantasy
position, and player identity. A predeclared CatBoost All-Rich experiment removes
only player ID.

## Models and bounded search

- Ridge: `alpha ∈ {1,10,100}`.
- CatBoost: three depth 6-7, learning-rate 0.035-0.04, L2 8-10 candidates;
  RMSE and MAE objectives are represented; maximum 1,000 iterations, patience 60.
- XGBoost: three depth 3-5 candidates with learning rate 0.03-0.04, explicit
  child-weight/subsample/column/L1/L2 regularization; maximum 1,200 estimators,
  patience 60.
- LightGBM: three 15/31-leaf depth 4-6 candidates with explicit child-size,
  subsample/column/L1/L2 regularization; maximum 1,200 estimators, patience 60.

The installed versions are scikit-learn 1.9.0, CatBoost 1.2.10, XGBoost 3.3.0,
and LightGBM 4.7.0. Current PyPI releases advertise Python 3.13 support for
[scikit-learn](https://pypi.org/project/scikit-learn/),
[CatBoost](https://pypi.org/project/catboost/),
[XGBoost](https://pypi.org/project/xgboost/), and
[LightGBM](https://pypi.org/project/lightgbm/). XGBoost's
[official API](https://xgboost.readthedocs.io/en/stable/python/python_api.html)
and LightGBM's
[official API](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRegressor.html)
define the recorded best-iteration/evaluation behavior.

First pass evaluates CORE and All-Rich for all four models. Detailed family
ablations are predeclared for CatBoost only, avoiding model-by-combination search.
CatBoost and XGBoost All-Rich receive fixed-seed stability refits at seeds 17, 29
and 43; seeds are summarized, never selected by outer performance.

## Standardized-history experiment

After primary verified-only runs, the same CatBoost All-Rich protocol separately
adds earlier E2018-E2021 standardized/counterfactual targets to training only.
Outer evaluation remains the exact verified E2023-E2025 rows. The result is kept
out of the primary model table and labelled V+S.

## Foundation-model decision rule

A local current foundation model is evaluated only if it is reliable on this
machine and exact fold protocol. The available host is an Intel Mac with 8 GB RAM
and no supported CUDA GPU. Current local TabPFN documentation says CPU execution
is practical only for roughly ≤1,000 rows and its default weights require a
separate non-commercial licence/authentication; hosted inference is prohibited
because project data may not be uploaded. The open BSD TabICLv2 alternative
supports regression and this row scale but recommends a GPU for larger datasets.
An attempted local feasibility check may be made; failure or unsafe resource
behavior excludes it rather than changing rows or using a hosted API.

## Diagnostics fixed before evaluation

- Metrics by fold/season, actual-minute band and prior-game depth.
- Player residual summaries only for at least 15 outer-test observations.
- Prediction bias overall and in `<5`, `5-10`, `10-15`, `15-20`, `20-25`, `25+`
  predicted bins.
- Fantasy ranking on rows with explicit historical Fantasy matchday mappings.
- Paired improvement against the unchanged blend on identical rows: mean and
  median absolute-error difference, row win rate, fold differences, and a
  fixed-seed grouped bootstrap by Fantasy slate.
- Training-derived model importance grouped by feature family; no causal claim.
- Training/inference time, approximate serialized size and feature count.
- Genuine outer-fold prediction persistence and deterministic fingerprints.

No clipping, post-hoc calibration or ensemble is part of primary selection. An
ensemble recommendation is based only on individual strength and genuine
outer-fold residual correlation; any actual weight fitting is deferred.
