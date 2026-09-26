# Phase 3A feature dictionary

## Dataset contract

The materialized core is `ml_player_game_core_features_v1`, version
`phase3a_core_boxscore_v1`. Its unit is one player who played in one target
EuroLeague game. The benchmark is explicitly `conditional_on_playing`.

Every row is auditable through `season`, `game_id`, `player_id`,
`target_game_time`, `feature_cutoff_time`, `cutoff_policy`, `target_rule_version`,
and `feature_pipeline_version`. `feature_cutoff_time` equals the trusted target tip
time; every source game must satisfy `source_game_time < feature_cutoff_time`.
Actual game datetime, then stable game ID, orders simultaneous/rescheduled games.
Nominal round is never the history-ordering key.

Unless a row below says otherwise:

- raw inputs are canonical `player_game_stats`, `team_game_stats`, and `games`;
- the target game is excluded;
- `last_N` means up to the N most recent eligible prior appearances/games;
- no history produces `NULL` and its sample count is zero;
- no future/backfilled value is used as a fallback;
- standard deviation needs two observations;
- percentages and rates use ratio-of-sums with a positive denominator;
- per-minute player rates additionally require at least ten aggregate minutes;
- EWMA alpha is fixed at 0.35 and was not test-set tuned.

## Audit, identity, and outcomes — not model inputs

These fields travel with the dataset for reproducibility but must be excluded from
the Phase 4 feature selector.

| Fields | Meaning / source | Safety note |
| --- | --- | --- |
| `player_game_id`, `game_id`, `game_code`, `player_id`, `team_id`, `opponent_team_id` | Stable canonical identities | Join/audit only |
| `target_game_time`, `local_game_date`, `feature_cutoff_time`, `cutoff_policy` | Trusted scheduling and cutoff policy | Cutoff/audit only |
| `target_minutes`, `target_started` | Actual target-game outcomes | **Never a pregame feature**; used only for evaluation bands |
| `standardized_fantasy_points` | Explicit common-rule scored outcome | Target, not input |
| `actual_fantasy_points` | Same outcome only where historical rule semantics are verified (E2022-E2025) | Target, not input |
| `target_rule_version`, `target_rule_status` | Numeric scoring regime and historical confidence | Audit/experiment selection |
| `feature_pipeline_version` | Generator version | Audit only |
| `player_history_max_game_time`, `team_history_max_game_time`, `opp_history_max_game_time` | Maximum contributing source timestamps | Leakage assertions, not model inputs |

## Structural and history-depth features

| Feature | Meaning / formula | Window and minimum | Missing/fallback and limitations |
| --- | --- | --- | --- |
| `season` | Canonical season category E2018-E2025 | Target context | Never treat as continuous without explicit encoding |
| `season_start_year` | Start year associated with `season` | Target context | Complete; structural only |
| `round_number` | Canonical competition round | Target context | Complete; not used to order history |
| `home_away` | Canonical target-team orientation | Target context | Complete categorical value |
| `home_game` | `home_away = 'home'` | Target context | Complete boolean |
| `away_game` | `home_away = 'away'` | Target context | Complete boolean |
| `career_el_games_before` | Count of player's eligible prior EL appearances | Full retained career | Zero at cold start |
| `season_games_before` | Count of player's eligible prior appearances in target season | Expanding current season | Zero at season debut |
| `has_1_prior_game` | `career_el_games_before >= 1` | Full career | Complete boolean |
| `has_3_prior_games` | `career_el_games_before >= 3` | Full career | Complete boolean |
| `has_5_prior_games` | `career_el_games_before >= 5` | Full career | Complete boolean |
| `has_10_prior_games` | `career_el_games_before >= 10` | Full career | Complete boolean |

## Fantasy-form and variability features

All prior Fantasy values in this section are `standardized_fantasy_points`, so a
rolling history never mixes incompatible rule scales.

| Feature | Meaning / formula | Window and minimum | Missing/fallback |
| --- | --- | --- | --- |
| `career_el_fp_avg_before` | Mean prior standardized FP | All prior retained appearances; 1 | `NULL` at cold start |
| `season_fp_avg_before` | Mean prior standardized FP in target season | Expanding season; 1 | `NULL` at season debut |
| `season_fp_std_before` | Sample SD of prior season FP | Expanding season; 2 | `NULL` with <2 |
| `last_1_fp` | FP in most recent prior appearance | Last 1; 1 | `NULL` with none |
| `last_3_fp_avg` | Mean prior FP | Last 3; 1 | `NULL` with none |
| `last_3_fp_n` | Number used by last-three FP statistics | Last 3; 0-3 | Zero with none |
| `last_3_fp_median` | Median prior FP | Last 3; 1 | `NULL` with none |
| `last_5_fp_avg` | Mean prior FP | Last 5; 1 | `NULL` with none |
| `last_5_fp_n` | Number used by last-five statistics | Last 5; 0-5 | Zero with none |
| `last_5_fp_median` | Median prior FP | Last 5; 1 | `NULL` with none |
| `last_5_fp_std` | Sample SD of prior FP | Last 5; 2 | `NULL` with <2 |
| `last_5_fp_min` | Minimum prior FP | Last 5; 1 | `NULL` with none |
| `last_5_fp_max` | Maximum prior FP | Last 5; 1 | `NULL` with none |
| `last_10_fp_avg` | Mean prior FP | Last 10; 1 | `NULL` with none |
| `last_10_fp_n` | Number used by last-ten statistics | Last 10; 0-10 | Zero with none |
| `last_10_fp_std` | Sample SD of prior FP | Last 10; 2 | `NULL` with <2 |
| `fp_ewma` | `sum(FP_i*(1-.35)^lag_i) / sum((1-.35)^lag_i)` | All prior retained appearances; 1 | `NULL` at cold start |
| `career_el_fp_avg_before_3s` | Prior FP mean limited to target season and previous two seasons | Up to 3 seasons; 1 | `NULL` with none |
| `career_el_fp_avg_before_5s` | Prior FP mean limited to target season and previous four seasons | Up to 5 seasons; 1 | `NULL` with none |
| `career_el_fp_avg_before_8s` | Prior FP mean over retained E2018-E2025 window | Up to 8 seasons; 1 | `NULL` with none |

## Minutes and role features

| Feature | Meaning / formula | Window and minimum | Missing/fallback and limitations |
| --- | --- | --- | --- |
| `season_minutes_avg_before` | Mean prior played minutes in target season | Expanding season; 1 | `NULL` at season debut |
| `last_1_minutes` | Most recent prior played minutes | Last 1; 1 | `NULL` at cold start |
| `last_3_minutes_avg` | Mean prior minutes | Last 3; 1 | `NULL` with none |
| `last_5_minutes_avg` | Mean prior minutes | Last 5; 1 | `NULL` with none |
| `last_10_minutes_avg` | Mean prior minutes | Last 10; 1 | `NULL` with none |
| `minutes_ewma` | Normalized minutes EWMA, alpha 0.35 | All prior appearances; 1 | `NULL` at cold start |
| `minutes_trend` | Mean minutes in last 3 minus mean in preceding 3 | Six prior appearances, split 3+3 | `NULL` until both blocks have 3 games |
| `season_start_rate_before` | Prior starts / prior appearances in target season | Expanding season; 1 | `NULL` at season debut; provider starter field only |
| `last_5_start_rate` | Prior starts / appearances | Last 5; 1 | `NULL` at cold start; provider starter field only |

## Player production-rate features

All per-minute values are ratio-of-sums, never averages of game-level rates.

| Feature | Meaning / formula | Window and minimum | Missing/fallback |
| --- | --- | --- | --- |
| `season_fp_per_min_before` | `sum(FP) / sum(minutes)` | Expanding season; 10 aggregate min | `NULL` below guard |
| `last_3_fp_per_min` | `sum(FP) / sum(minutes)` | Last 3; 10 aggregate min | `NULL` below guard |
| `last_5_fp_per_min` | `sum(FP) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `last_10_fp_per_min` | `sum(FP) / sum(minutes)` | Last 10; 10 aggregate min | `NULL` below guard |
| `points_per_min` | `sum(points) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `rebounds_per_min` | `sum(total_rebounds) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `oreb_per_min` | `sum(offensive_rebounds) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `dreb_per_min` | `sum(defensive_rebounds) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `assists_per_min` | `sum(assists) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `steals_per_min` | `sum(steals) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `blocks_per_min` | `sum(blocks) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `turnovers_per_min` | `sum(turnovers) / sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `ast_to_ratio` | `sum(assists) / sum(turnovers)` | Last 5; positive TO denominator | `NULL` with zero TO; proxy, not official AST% |

## Shooting and volume features

`FGM = 2PM + 3PM`, `FGA = 2PA + 3PA`,
`eFG% = (FGM + 0.5*3PM)/FGA`, and
`TS% = points/[2*(FGA + 0.44*FTA)]`.

| Feature | Meaning / formula | Window and minimum | Missing/fallback |
| --- | --- | --- | --- |
| `season_2p_pct_before` | `sum(2PM)/sum(2PA)` | Expanding season; 1 attempt | `NULL` with no 2PA |
| `season_3p_pct_before` | `sum(3PM)/sum(3PA)` | Expanding season; 1 attempt | `NULL` with no 3PA |
| `season_ft_pct_before` | `sum(FTM)/sum(FTA)` | Expanding season; 1 attempt | `NULL` with no FTA |
| `season_fg_pct_before` | `sum(FGM)/sum(FGA)` | Expanding season; 1 attempt | `NULL` with no FGA |
| `season_efg_pct_before` | Ratio-of-sums eFG% | Expanding season; 1 FGA | `NULL` with no FGA |
| `season_ts_pct_before` | Ratio-of-sums TS% | Expanding season; positive denominator | `NULL` with none |
| `last_5_2p_pct` | `sum(2PM)/sum(2PA)` | Last 5; 1 attempt | `NULL` with no 2PA |
| `last_5_3p_pct` | `sum(3PM)/sum(3PA)` | Last 5; 1 attempt | `NULL` with no 3PA |
| `last_5_ft_pct` | `sum(FTM)/sum(FTA)` | Last 5; 1 attempt | `NULL` with no FTA |
| `last_5_fg_pct` | `sum(FGM)/sum(FGA)` | Last 5; 1 attempt | `NULL` with no FGA |
| `last_5_efg_pct` | Ratio-of-sums eFG% | Last 5; 1 FGA | `NULL` with no FGA |
| `last_5_ts_pct` | Ratio-of-sums TS% | Last 5; positive denominator | `NULL` with none |
| `fga_per_min` | `sum(FGA)/sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `two_pa_per_min` | `sum(2PA)/sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `three_pa_per_min` | `sum(3PA)/sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |
| `fta_per_min` | `sum(FTA)/sum(minutes)` | Last 5; 10 aggregate min | `NULL` below guard |

## Biographical and EuroLeague-only workload features

| Feature | Meaning / formula | Window and minimum | Missing/fallback and limitations |
| --- | --- | --- | --- |
| `player_age` | Days from birth date to target date / 365.2425 | Point-in-time biography | `NULL` if DOB missing/invalid |
| `days_since_previous_el_game` | Actual target tip minus previous EL appearance tip, days | Last prior appearance; 1 | `NULL` at cold start; not true all-competition rest |
| `player_games_last_7_calendar_days_el` | Prior EL appearances in `[cutoff-7d, cutoff)` | Seven calendar days | Zero with none; EL only |
| `player_minutes_last_7_calendar_days_el` | Sum of prior EL minutes in `[cutoff-7d, cutoff)` | Seven calendar days | Zero with none; EL only |

## Team environment features

For each prior team game, raw possessions are
`FGA - OREB + TO + 0.44*FTA`. The game estimate is the mean of both teams' raw
estimates. Pace is `estimated_possessions * 40 / (40 + 5*OT_periods)`, explicitly
normalizing overtime. ORtg/DRtg are points per 100 estimated possessions.

| Feature | Meaning / formula | Window and minimum | Missing/fallback and limitations |
| --- | --- | --- | --- |
| `team_games_before` | Prior target-season team games | Expanding season | Zero at team season opener |
| `team_season_pace_before` | Mean pace per 40 | Expanding season; 1 game | `NULL` at opener; estimated possessions |
| `team_last5_pace` | Mean pace per 40 | Last 5 team games; 1 | `NULL` with none |
| `team_season_ortg_before` | `100*sum(points)/sum(est_possessions)` | Expanding season; positive possessions | `NULL` at opener |
| `team_last5_ortg` | Same ORtg formula | Last 5 team games | `NULL` with none |
| `team_season_drtg_before` | `100*sum(opp_points)/sum(est_possessions)` | Expanding season | `NULL` at opener |
| `team_last5_drtg` | Same DRtg formula | Last 5 team games | `NULL` with none |
| `team_efg_before` | `(sum(FGM)+.5*sum(3PM))/sum(FGA)` | Expanding season | `NULL` without FGA |
| `team_tov_rate_before` | `sum(TO)/sum(FGA+.44*FTA+TO)` | Expanding season | Box-score proxy |
| `team_oreb_rate_before` | `sum(OREB)/sum(OREB+opp_DREB)` | Expanding season | `NULL` without opportunities |
| `team_ft_rate_before` | `sum(FTA)/sum(FGA)` | Expanding season | `NULL` without FGA |
| `team_ast_rate_before` | `sum(AST)/sum(FGM)` | Expanding season | Assist-to-made-FG proxy, not official AST% |
| `team_days_since_previous_el_game` | Target tip minus team's previous EL tip, days | Last prior team game | `NULL` at opener; EL only |

## Opponent environment and allowance features

Each opponent value is calculated from games the opponent completed before the
target cutoff. “Allowed” fields describe what prior opponents produced against
the target opponent.

| Feature | Meaning / formula | Window and minimum | Missing/fallback and limitations |
| --- | --- | --- | --- |
| `opp_games_before` | Opponent's prior target-season games | Expanding season | Zero at opener |
| `opp_season_pace_before` | Opponent mean pace per 40 | Expanding season; 1 | `NULL` at opener |
| `opp_last5_pace` | Opponent mean pace per 40 | Last 5 games; 1 | `NULL` with none |
| `opp_season_ortg_before` | Opponent points per 100 estimated possessions | Expanding season | `NULL` at opener |
| `opp_last5_ortg` | Opponent ORtg | Last 5 games | `NULL` with none |
| `opp_season_drtg_before` | Points allowed per 100 estimated possessions | Expanding season | `NULL` at opener |
| `opp_last5_drtg` | Opponent DRtg | Last 5 games | `NULL` with none |
| `opp_efg_allowed` | Prior opponents' ratio-of-sums eFG% | Expanding season | `NULL` without FGA |
| `opp_rebound_rate_allowed` | Prior opponents' rebounds / all rebounds | Expanding season | Simple box-score share proxy |
| `opp_assist_rate_allowed` | Prior opponents' assists / made FGs | Expanding season | Proxy, not official AST% |
| `opp_turnover_forced_rate` | Prior opponents' TO / `(FGA+.44*FTA+TO)` | Expanding season | Box-score proxy |
| `opp_3pa_rate_allowed` | Prior opponents' 3PA / FGA | Expanding season | `NULL` without FGA |
| `opp_ft_rate_allowed` | Prior opponents' FTA / FGA | Expanding season | `NULL` without FGA |
| `opp_fp_allowed_per_player_game` | Sum standardized FP allowed / played opponent-player games | Expanding season; 1 player-game | Conditional-on-playing allowance |
| `opp_fp_allowed_last5_games` | Same allowance ratio | Last 5 opponent games | Conditional-on-playing allowance |

Position-specific allowances are deferred. Safe season-level positions exist only
for confidently matched E2022-E2025 rows, and using them would create a changing
coverage regime across the core eight-season benchmark.

## Optional Fantasy extension

`ml_player_game_core_features_with_fantasy_v1` adds the following fields without
making them mandatory core model features:

| Field | Meaning | Historical safety and coverage |
| --- | --- | --- |
| `fantasy_id` | Raw official Fantasy identity | Safe E2022-E2025 only when season-aware crosswalk is `MATCHED` |
| `fantasy_position` | Official season-level historical classification | Safe E2022-E2025; not evidence of intra-season role timing |
| `fantasy_matchday` | Explicit mapped Fantasy matchday | Safe E2022-E2025; legacy joins also require actual game date inside source window |
| `fantasy_credits_pre_matchday` | Price available before target Fantasy matchday | **E2025 only**; forced `NULL` for E2022-E2024 |
| `fantasy_price_status` | `E2025_VALIDATED_PRE_MATCHDAY` when price is present | No other season is eligible |
| `fantasy_source_artifact_id` | Exact retained source provenance | Audit only |

Historical position is not used as a Phase 3A player-performance predictor. It is
available for safe E2025 evaluation/stratification and future controlled fallback
experiments. Unsafe injury, probability, bench, on-fire, Fantasy average,
popularity, post-matchday outcomes, and E2022-E2024 `cr` fields do not appear.

## Baseline output fields

`ml_baseline_predictions_v1` adds six outcome estimates:
`prediction_season_average`, `prediction_last_3`, `prediction_last_5`,
`prediction_ewma`, `prediction_fp_per_min_x_minutes`, and
`prediction_season_recent_blend`. It also records `history_window_seasons`,
`baseline_version`, and `evaluation_semantics = CONDITIONAL_ON_PLAYING`.

`ml_e2025_fantasy_evaluation_v1` adds each prediction divided by validated credits
as a descriptive `*_per_credit` diagnostic. These ratios are neither targets nor
optimizer results.
