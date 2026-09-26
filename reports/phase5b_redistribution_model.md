# Phase 5B team-minute redistribution model

All evaluation is chronological and `CONDITIONAL_ON_KNOWN_ABSENCE_SET`. E2023,
E2024, and E2025 are forward outer seasons; tuning uses only earlier seasons.

## Comparison

| experiment_id | mae | rmse | top1_recipient_accuracy | top2_recipient_recall | teammate_delta_spearman | team_conservation_mae | team_sum_absolute_error |
|---|---|---|---|---|---|---|---|
| p5b__xgboost__core | 4.4020 | 5.5826 | 0.0989 | 0.2148 | 0.1341 | 0.0000 | 45.7259 |
| p5b__catboost__core | 4.4021 | 5.6123 | 0.0806 | 0.1897 | 0.1136 | 0.0000 | 45.7266 |
| p5b__catboost__core_rotation_recent | 4.4067 | 5.6194 | 0.0752 | 0.1887 | 0.1031 | 0.0000 | 45.7746 |
| p5b__catboost__context_only_ablation | 4.4093 | 5.6141 | 0.0813 | 0.1894 | 0.1088 | 0.0000 | 45.8020 |
| p5b__ridge__core | 4.4277 | 5.6045 | 0.1023 | 0.2087 | 0.1033 | 0.0000 | 45.9925 |
| p5b__baseline__proportional | 4.4610 | 5.6586 | 0.0474 | 0.1551 | 0.0730 | 0.0000 | 46.3386 |
| p5b__baseline__equal_split | 4.4698 | 5.6400 | 0.1037 | 0.1873 | -0.0059 | 0.0000 | 46.4302 |
| p5b__baseline__position_split | 4.6025 | 5.8369 | 0.0827 | 0.2022 | 0.0509 | 0.0000 | 47.8084 |
| p5b__baseline__no_adjustment | 4.6339 | 5.8202 | 0.0928 | 0.1951 |  | 12.4288 | 48.1347 |

The selected method is **p5b__xgboost__core**: ML improved 1.32% over the best naive method and won 3/3 outer folds.
Freeze state: **FROZEN**. Reconciliation is
`bounded_additive_simplex_200_v1` and projects available-player scores onto
the bounded 0–40 minute simplex with an exact 200-minute team total.

## Stability and disruption size

| mae | mean_actual | mean_prediction | mean_residual | pearson | rmse | season | spearman | team_conservation_mae | team_sum_absolute_error | teammate_delta_spearman | top1_recipient_accuracy | top2_recipient_recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4.5352 | 19.1842 | 19.1842 | 0.0000 | 0.7467 | 5.7245 | E2023 | 0.7463 | 0.0000 | 47.2806 | 0.1077 | 0.0800 | 0.2021 |
| 4.3827 | 19.3575 | 19.3575 | 0.0000 | 0.7452 | 5.5827 | E2024 | 0.7431 | 0.0000 | 45.2816 | 0.1366 | 0.0951 | 0.2209 |
| 4.2991 | 19.2245 | 19.2245 | 0.0000 | 0.7418 | 5.4515 | E2025 | 0.7375 | 0.0000 | 44.7252 | 0.1556 | 0.1193 | 0.2206 |

| mae | mean_actual | mean_prediction | mean_residual | missing_minutes_band | pearson | rmse | rows | spearman | team_conservation_mae | team_sum_absolute_error | teammate_delta_spearman | top1_recipient_accuracy | top2_recipient_recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4.3937 | 18.5637 | 18.5637 | 0.0000 | 0-10 | 0.7543 | 5.5721 | 7380 | 0.7531 | 0.0000 | 47.3361 | 0.1403 | 0.0964 | 0.1964 |
| 4.4345 | 19.5568 | 19.5568 | 0.0000 | 10-20 | 0.7342 | 5.6062 | 5911 | 0.7325 | 0.0000 | 45.3496 | 0.1218 | 0.0969 | 0.2223 |
| 4.3112 | 20.7558 | 20.7558 | 0.0000 | 20-30 | 0.7209 | 5.5259 | 1773 | 0.7196 | 0.0000 | 41.5420 | 0.1543 | 0.1087 | 0.2527 |
| 4.4218 | 21.1640 | 21.1640 | 0.0000 | 30-40 | 0.6539 | 5.7315 | 189 | 0.6355 | 0.0000 | 41.7865 | 0.0435 | 0.0500 | 0.2250 |
| 4.7426 | 22.7848 | 22.7848 | 0.0000 | 40+ | 0.6763 | 5.7011 | 79 | 0.6545 | 0.0000 | 41.6293 | 0.2360 | 0.3333 | 0.3333 |

| absence_count_band | mae | mean_actual | mean_prediction | mean_residual | pearson | rmse | rows | spearman | team_conservation_mae | team_sum_absolute_error | teammate_delta_spearman | top1_recipient_accuracy | top2_recipient_recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4.4012 | 18.3596 | 18.3596 | 0.0000 | 0.7433 | 5.5804 | 8900 | 0.7412 | 0.0000 | 47.9442 | 0.1427 | 0.0894 | 0.2013 |
| 2 | 4.4263 | 20.0829 | 20.0829 | 0.0000 | 0.7288 | 5.6107 | 5308 | 0.7268 | 0.0000 | 44.0808 | 0.1287 | 0.1032 | 0.2270 |
| 3+ | 4.2935 | 22.4199 | 22.4199 | 0.0000 | 0.7415 | 5.4665 | 1124 | 0.7360 | 0.0000 | 38.3007 | 0.1015 | 0.1429 | 0.2500 |

## Recipient ranking and cross-position allocation

For the selected method, Top-1 recipient accuracy is
**0.0989**, Top-2 recipient recall is
**0.2148**, and mean within-team delta
Spearman is **0.1341**.

Using a >= 3.0-minute meaningful-gain
definition, **3,165** of
**5,833** actual gainers
(54.3%) were outside every absent player's
nominal position group. Position is therefore an input, never a hard allocation rule.

On those cross-position meaningful-gainer rows, selected ML MAE was
**5.6353** versus
**6.6706**
for the position split.

## Inputs

Recipient inputs cover baseline/recent/season minutes, starts, rotation share,
closing/stint stability, FP and box-score rates, team role rank, cumulative missing
minutes/starts/production proxies by broad position, remaining roster depth and
role concentration, prior shared-minute evidence, and prior absence response with
sample counts. Player ID and Fantasy credits are excluded. The context-only ablation
measures whether basketball-derived recipient quality/role features add value.
