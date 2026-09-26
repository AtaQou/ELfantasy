# Phase 3A baseline backtest

## Benchmark

The transparent **season/recent blend** is the lowest-error Phase 3A baseline:
MAE 5.9655 and RMSE 7.6994 on 54,642 future, active-player observations. This is
the error benchmark a Phase 4 model must beat under the same chronological and
`conditional_on_playing` evaluation.

Season average is the best ranking baseline: it captures 44.21% of actual top-10
performers inside its predicted top 20, and 49.62% of actual top-20 performers
inside its predicted top 30. The blend is close at 43.96% and 49.15%.

This is not a full end-to-end Fantasy forecast. Every evaluated row is known after
the fact to have played; no availability/DNP model was trained.

## Dataset and chronology

- 54,642 rows, 930 players, 2,528 games, E2018-E2025.
- E2018-E2021 use the common standardized/counterfactual rule target.
- E2022-E2025 use the same numeric rule with verified official target semantics.
- Every baseline reads only strictly prior box-score observations.
- `target_game_time` is the prediction cutoff and actual datetime determines order,
  including rescheduled games.
- `ml_walk_forward_splits_v1` provides one ordered split record per game and
  parameterized 3-, 5-, and 8-season history starts.
- No random split, fitted estimator, tuning, PBP, shot, current Fantasy overlay, or
  target-game input is used.

## Baseline definitions

| Baseline | Pregame prediction |
| --- | --- |
| Season average | Player's current-season mean before the game; otherwise same-rule career mean in selected history window; otherwise fixed 10.0 cold-start fallback |
| Last 3 | Mean over up to three most recent prior EL appearances; season-average hierarchy if none |
| Last 5 | Mean over up to five most recent prior EL appearances; season-average hierarchy if none |
| EWMA | Normalized prior-career EWMA with alpha 0.35 (most recent weight 0.35); season-average hierarchy if none |
| FP/min × minutes | Guarded last-five ratio-of-sums FP/min × prior-minutes EWMA; falls back to last-five prediction |
| Season/recent blend | Fixed 50% current-season average + 50% last-five average when both exist; season-average hierarchy otherwise |

The cold-start value 10.0 was selected in advance as a simple fixed contest-scale
fallback and was not tuned. Baseline inputs are updated row by row rather than fit
once on a final-season aggregate.

## Overall results

| Baseline | MAE | RMSE | Spearman | Pearson |
| --- | ---: | ---: | ---: | ---: |
| Season average | 5.9869 | 7.7361 | 0.4607 | 0.4659 |
| Last 3 | 6.4040 | 8.2982 | 0.4119 | 0.4178 |
| Last 5 | 6.1363 | 7.9387 | 0.4418 | 0.4488 |
| EWMA | 6.1438 | 7.9520 | 0.4430 | 0.4484 |
| FP/min × minutes | 6.1243 | 7.9244 | 0.4441 | 0.4510 |
| **Season/recent blend** | **5.9655** | **7.6994** | **0.4655** | **0.4732** |

## Ranking results

Players are grouped by season and competition round. For each group, the test asks
how many actual top performers were present in a larger predicted shortlist.
Feature histories still use actual datetime, not round order.

| Baseline | Actual top 10 in predicted top 20 | Actual top 20 in predicted top 30 |
| --- | ---: | ---: |
| **Season average** | **44.21%** | **49.62%** |
| Last 3 | 39.72% | 45.61% |
| Last 5 | 41.87% | 47.48% |
| EWMA | 41.46% | 47.42% |
| FP/min × minutes | 41.74% | 47.64% |
| Season/recent blend | 43.96% | 49.15% |

## Performance by season

The table shows the primary blend; all-baseline season metrics are retained in
`data/samples/phase3a/baseline_results.json`.

| Season | Rows | MAE | RMSE | Spearman | Pearson | Target status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| E2018 | 5,603 | 6.0284 | 7.7737 | 0.4196 | 0.4316 | Standardized/counterfactual |
| E2019 | 5,509 | 6.1186 | 7.8383 | 0.4188 | 0.4521 | Standardized/counterfactual |
| E2020 | 7,079 | 5.8050 | 7.5727 | 0.4935 | 0.4897 | Standardized/counterfactual |
| E2021 | 6,398 | 5.8666 | 7.5601 | 0.4874 | 0.4863 | Standardized/counterfactual |
| E2022 | 7,060 | 5.9348 | 7.6404 | 0.4493 | 0.4585 | Official validated |
| E2023 | 7,146 | 5.9013 | 7.6360 | 0.4774 | 0.4789 | Official validated |
| E2024 | 7,106 | 5.9582 | 7.6604 | 0.4833 | 0.5043 | Official validated |
| E2025 | 8,741 | 6.1142 | 7.8940 | 0.4653 | 0.4648 | Official validated |

The old retained era (E2018-E2021) has blend MAE 5.9422 versus 5.9846 in
E2022-E2025. That 0.7% difference is small and does not show broad degradation of
the older box-score histories. E2025 is individually harder, so there is mild
season-level drift/variance worth monitoring rather than evidence that old seasons
should be discarded wholesale.

## Performance by played minutes

| Actual minutes | Rows | Blend MAE | Blend RMSE | Blend Spearman | Best MAE in band |
| --- | ---: | ---: | ---: | ---: | --- |
| <10 | 9,423 | 4.3859 | 5.6302 | 0.0659 | EWMA, 4.3632 |
| 10-20 | 20,044 | 5.4209 | 6.8615 | 0.1770 | Blend, 5.4209 |
| 20-25 | 11,936 | 6.3540 | 8.0497 | 0.1958 | Season average, 6.3528 |
| 25-30 | 8,625 | 7.1446 | 9.0503 | 0.2274 | Blend, 7.1446 |
| 30+ | 4,614 | 8.3482 | 10.5844 | 0.2301 | Blend, 8.3482 |

Absolute error grows with minutes and scoring opportunity. The low-minute group's
smaller MAE is not stronger ranking performance—its correlations are very low.
This confirms why overall MAE cannot stand alone.

## Performance by prior-history depth

| Prior EL games | Rows | Blend MAE | Blend RMSE | Blend Spearman | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 930 | 7.5406 | 8.6247 | N/A | Fixed 10.0 prediction; correlation undefined |
| 1-2 | 1,749 | 5.8115 | 7.8293 | 0.3609 | Very sparse player history |
| 3-4 | 1,641 | 5.2889 | 7.0282 | 0.4150 | First stable recent window |
| 5-9 | 3,802 | 5.4293 | 7.2465 | 0.4350 | Recent and season form available |
| 10+ | 46,520 | 6.0075 | 7.7333 | 0.4707 | Higher-role/more variable population |

Cold start is the clearest weakness. The non-monotonic MAE after five games is
consistent with player/role selection and higher scoring scale; correlation keeps
improving with history.

## History-window infrastructure

The 3-, 5-, and 8-season window paths all generated 54,642 predictions and use
explicit season-start bounds. Most baselines are recent or current-season and are
therefore intentionally identical. Only the career fallback changes:

| History | Season-average MAE | Blend MAE | Blend RMSE |
| ---: | ---: | ---: | ---: |
| 3 seasons | 5.9877 | 5.9663 | 7.7001 |
| 5 seasons | 5.9874 | 5.9660 | 7.6995 |
| 8 seasons | 5.9869 | 5.9655 | 7.6994 |

The infrastructure works and the Phase 3A difference is negligible. It is retained
so future trained models can compare history length without redefining splits.

## E2025 Fantasy evaluation layer

`ml_e2025_fantasy_evaluation_v1` contains 7,921 active player-game rows with
confident identity, historical position, explicit matchday/game mapping, actual
standardized Fantasy points, all six predictions, and validated pre-matchday
credits. It also carries each `prediction / credit` diagnostic.

The diagnostic is descriptive only: credits are not part of the target, the ratio
does not solve roster constraints, and no optimizer/captain/transfer simulation is
implemented. E2022-E2024 prices never enter this table.

## Limitations

1. Evaluation is conditional on playing; DNP and active-list uncertainty are not
   predicted.
2. E2018-E2021 are standardized counterfactual targets, not proven historical
   official Fantasy outcomes.
3. Workload contains EuroLeague games only; domestic games, injuries, and true
   expected minutes are unavailable. The most promising next controlled additions
   are rotation/minutes features, possession/on-off context, and shot-profile
   features—but those remain Phase 3B work and were not implemented here.
