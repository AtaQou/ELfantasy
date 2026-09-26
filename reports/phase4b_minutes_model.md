# Phase 4B conditional expected-minutes model

All results are conditional on the player actually playing. Target-game minutes, starter status, active lineups, substitutions, and post-tip data are labels or diagnostics only and are never model inputs.

## Model definitions and selection

The targeted ROLE manifest contains lagged minutes, starter frequency, rotation/stint/closing-lineup history, role movement, history depth, and team/opponent context. Player identity is excluded. Ridge, CatBoost, and XGBoost use the frozen chronological folds; the selected component is the lowest pooled inner-validation minutes MAE, not the best outer result.

| Candidate | Inner MAE | Outer MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| p4b__minutes__ridge__role | 4.6563 | 4.5859 | 5.8210 | 0.7011 | 0.7013 | -0.0703 |
| p4b__minutes__catboost__role | 4.6384 | 4.5651 | 5.8135 | 0.7037 | 0.7027 | 0.0600 |
| p4b__minutes__xgboost__role | 4.6474 | 4.5717 | 5.7898 | 0.7049 | 0.7038 | -0.0279 |
| p4b__minutes__catboost__role_stage | 4.6317 | 4.5598 | 5.8001 | 0.7047 | 0.7036 | -0.0757 |
| p4b__minutes__catboost__role_older_history | 4.6017 | 4.5679 | 5.8221 | 0.7050 | 0.7038 | 0.4305 |

Selected: `p4b__minutes__catboost__role_older_history` with MAE **4.5679 minutes** and RMSE **5.8221**.

## Actual-minute bands

| minutes_band | Rows | MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| 10-20 | 8353 | 3.7660 | 4.7697 | 0.3198 | 0.3218 | -0.9806 |
| 20-25 | 5042 | 3.8688 | 4.9747 | 0.2111 | 0.2160 | 2.1621 |
| 25-30 | 3587 | 4.6631 | 5.9586 | 0.1789 | 0.1846 | 4.1194 |
| <10 | 3590 | 6.0721 | 7.3055 | 0.3198 | 0.3216 | -5.7392 |
| 30+ | 1827 | 7.0203 | 8.2877 | 0.1165 | 0.1343 | 6.9839 |

Maximum actual minutes were 47.3167; maximum predicted minutes were 33.8030. The predicted-bin table below exposes any compression directly.

| predicted_minutes_bin | Rows | MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| 10-15 | 4368 | 5.0350 | 6.2566 | 0.2043 | 0.2037 | 0.6466 |
| 15-20 | 6280 | 4.6763 | 5.8716 | 0.2420 | 0.2398 | 0.3413 |
| 20-25 | 5938 | 4.3860 | 5.6122 | 0.2248 | 0.2355 | 0.3219 |
| 25-30 | 3104 | 4.0561 | 5.3027 | 0.1836 | 0.1957 | -0.1284 |
| <10 | 2254 | 4.7146 | 6.1753 | 0.2014 | 0.2172 | 1.5608 |
| 30+ | 455 | 3.7251 | 5.0259 | 0.1355 | 0.1217 | -0.7829 |

## Role change and history

Role-change score is the mean minute-equivalent movement across lagged minutes EWMA vs season average, core/rotation trends, recent-vs-season rotation share, starter-rate change, and recent rotation volatility. Stable/moderate/large thresholds are the 60th/85th percentiles fitted on each fold's historical training rows only.

| role_change_class | Rows | MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| large_role_change | 3372 | 5.1842 | 6.4770 | 0.6305 | 0.6280 | 0.5473 |
| moderate_role_change | 5382 | 4.8071 | 6.0665 | 0.6728 | 0.6711 | 0.3473 |
| stable_role | 13645 | 4.3212 | 5.5462 | 0.7335 | 0.7316 | 0.4344 |

| history_group | Rows | MAE | RMSE | Pearson | Spearman | Bias |
|---|---|---|---|---|---|---|
| 10+ prior games | 20531 | 4.5470 | 5.7926 | 0.7020 | 0.7016 | 0.4173 |
| <10 prior games | 1868 | 4.7974 | 6.1368 | 0.6692 | 0.6635 | 0.5760 |

## Strongest minute predictors

| Feature | Mean normalized importance |
|---|---|
| minutes_ewma | 0.2172 |
| season_minutes_avg_before | 0.1496 |
| last_10_minutes_avg | 0.1106 |
| last_5_minutes_avg | 0.1081 |
| last_3_minutes_avg | 0.0681 |
| days_since_previous_el_game | 0.0337 |
| last_1_minutes | 0.0315 |
| team_days_since_previous_el_game | 0.0168 |
| round_number | 0.0145 |
| player_minutes_last_7_calendar_days_el | 0.0142 |
| team_games_before | 0.0137 |
| team_season_ortg_before | 0.0131 |
| player_age | 0.0128 |
| season_fp_avg_before | 0.0128 |
| opp_season_pace_before | 0.0128 |

## Competition-stage ablation

`p4b__minutes__catboost__role_stage` changed the measured metric from 4.5651 to 4.5598 (delta -0.0053).
The authoritative source is explicit `games.phase_code`; Final Four sub-stages use known schedule order. Playoff series state uses only completed earlier games in the same series.
