# Phase 3B rich feature dictionary

## Global contract

All features below are derived from already-ingested E2021-E2025 PBP/shots and
are attached as a left extension of the Phase 3A modelling identity. For every
target row, source observations satisfy `source_game_time < feature_cutoff_time`.
Actual datetime, not nominal round, defines history. `last_N` means up to N most
recent valid prior observations. Counts are zero when no history exists; a
statistic is NULL when its numerator/denominator or minimum sample is unavailable.
No target-game starter, minutes, substitutions, lineup, shots, active roster or
on/off result is used.

Common row audit fields are `model_row_id`, `season`, `game_id`, `player_id`,
`target_game_time`, `feature_cutoff_time`, `target_rule_version`,
`feature_pipeline_version`, and `rich_feature_pipeline_version`. The latter is
`phase3b_rich_v1`.

## Rotation reconstruction semantics

Raw source is `play_by_play_events`, seeded by canonical box-score starters and
validated against box-score minutes. Settled same-clock substitution transactions
must leave five distinct players. Regulation quarters are 600 seconds; explicit
OT periods are 300. Same-clock non-substitution events are assigned to the
pre-transaction lineup except at a period start. No impossible sequence is
repaired. Only games marked `rotation_usable` populate rotation, on/off or lineup
observations.

`closing_lineup` means continuously present for the complete final 120 seconds of
regulation (game seconds 2,280-2,400). It does not depend on score margin, remains
regulation-defined in overtime, and naturally returns false for a player who
fouled out or left during the window.

### Rotation/role features (26)

Raw observation: `player_game_rotation_observations_v1`. Career history may cross
seasons; fields containing `season` reset to the target season. Rotation
quarantines produce zero sample counts/NULL statistics without deleting the core
row. Standard deviations require two observations.

| Name | Formula / meaning | Window, minimum and missing behavior |
|---|---|---|
| `rot_games_before` | Count of prior rotation-usable games for player | All prior; 0 if none |
| `rot_last3_games_n` | Valid observations represented in last-3 fields | Up to 3; 0 if none |
| `rot_last5_games_n` | Valid observations represented in last-5 fields | Up to 5; 0 if none |
| `rot_last1_stint_count` | Number of reconstructed stints in most recent valid game | Last 1; NULL if none |
| `rot_last3_stint_count_avg` | Mean per-game stint count | Up to last 3; ≥1 |
| `rot_last5_stint_count_avg` | Mean per-game stint count | Up to last 5; ≥1 |
| `rot_last3_avg_stint_minutes` | Mean of each game's average stint seconds / 60 | Up to last 3; ≥1 |
| `rot_last5_avg_stint_minutes` | Mean of each game's average stint seconds / 60 | Up to last 5; ≥1 |
| `rot_last3_longest_stint_minutes_avg` | Mean of each game's longest stint / 60 | Up to last 3; ≥1 |
| `rot_last5_longest_stint_minutes_avg` | Mean of each game's longest stint / 60 | Up to last 5; ≥1 |
| `rot_last3_first_sub_out_minutes_avg` | Mean elapsed game minute of first exit, starters only | Up to last 3; NULL if no qualifying starter game |
| `rot_last5_first_sub_out_minutes_avg` | Mean elapsed game minute of first exit, starters only | Up to last 5; NULL if no qualifying starter game |
| `rot_last3_first_entry_minutes_avg` | Mean elapsed game minute first on court (0 for starters) | Up to last 3; ≥1 |
| `rot_last5_first_entry_minutes_avg` | Mean elapsed game minute first on court | Up to last 5; ≥1 |
| `rot_last5_first_stint_minutes_avg` | Mean `(first_stint_end-first_entry)/60` | Up to last 5; ≥1 |
| `rot_last5_minutes_avg` | Mean reconstructed player seconds / 60 | Up to last 5; ≥1 |
| `rot_last5_minutes_share` | `sum(player seconds)/sum(game seconds)`; OT included | Up to last 5; positive game-seconds guard |
| `rot_season_minutes_share_before` | Same ratio using target-season prior games only | Current season; NULL before first valid same-season game |
| `rot_last5_closing_lineup_rate` | Mean closing-lineup indicator | Up to last 5; ≥1 |
| `rot_season_closing_lineup_rate_before` | Mean closing indicator in target season | Current season; ≥1 |
| `rot_last5_starter_rate` | Mean historical box-score starter indicator | Up to last 5; ≥1 |
| `rot_last5_minutes_std` | Sample SD of reconstructed game minutes | Up to last 5; ≥2 |
| `rot_last5_stint_count_std` | Sample SD of per-game stint count | Up to last 5; ≥2 |
| `rot_minutes_trend` | Mean minutes recency 1-3 minus mean recency 4-6 | Needs ≥1 game in both groups (at least 4 total) |
| `rot_minutes_ewma` | `sum(minutes × 0.65^(recency-1))/sum(weights)` | All prior valid games; ≥1; fixed, untuned decay |
| `rot_role_stability` | `1/(1 + minutes_SD/10 + stint_count_SD/3)` | Up to last 5; both SDs require ≥2; range `[0,1]` |

Audit-only `rot_source_max_game_time_us` records the latest contributing source;
it is not a predictive field.

## Possession/on-off semantics

Exact event-level possession boundaries cannot be guaranteed by the provider.
`pbp_possessions_v1` is therefore a team-game estimate, not an official
possession table: `FGA - OREB + TO + 0.44×FTA`. Player on/off segments apply the
same components to reconstructed lineup intervals. Team pace uses the average of
the two teams' estimated components and normalizes on-court seconds to 2,400;
this explicitly handles overtime. A minimum of 20 estimated possessions guards
every rating.

On/off is contextual team performance while the player was on/off court. It is
not causal impact and reflects teammates, opponents, score state and sampling.

### On/off features (18)

Raw observations: `player_game_onoff_observations_v1`; rotation quarantines apply.
Season fields use only the target season; last-5/10 can use prior-season rich
warm-up. Ratings are ratio-of-sums, never averages of game ratings.

| Name | Formula / meaning | Window, denominator and missing behavior |
|---|---|---|
| `onoff_games_before` | Prior valid on/off game count | All prior; 0 if none |
| `onoff_last5_games_n` | Valid observations in recent window | Up to 5; 0 if none |
| `onoff_season_on_possessions_n` | Sum estimated possessions while on court | Target season; raw denominator |
| `onoff_season_off_possessions_n` | Sum estimated possessions while off court | Target season; raw denominator |
| `onoff_season_on_ortg` | `100×sum(on points for)/sum(on possessions)` | Target season; ≥20 on possessions |
| `onoff_season_on_drtg` | `100×sum(on points against)/sum(on possessions)` | Target season; ≥20 on possessions |
| `onoff_season_on_net_rating` | `on ORtg - on DRtg` | Target season; ≥20 on possessions |
| `onoff_season_off_ortg` | `100×sum(off points for)/sum(off possessions)` | Target season; ≥20 off possessions |
| `onoff_season_off_drtg` | `100×sum(off points against)/sum(off possessions)` | Target season; ≥20 off possessions |
| `onoff_season_off_net_rating` | `off ORtg - off DRtg` | Target season; ≥20 off possessions |
| `onoff_season_ortg_diff` | `on ORtg - off ORtg` | Target season; both denominators ≥20 |
| `onoff_season_drtg_diff` | `on DRtg - off DRtg` | Target season; both denominators ≥20 |
| `onoff_season_net_rating_diff` | `on net - off net` | Target season; both denominators ≥20 |
| `onoff_season_on_court_pace` | `sum(on game-possession equivalents)×2400/sum(on seconds)` | Target season; ≥20 on possessions and positive seconds |
| `onoff_last5_on_possessions_n` | Sum on-court possession equivalents | Up to last 5; sample/eligibility denominator |
| `onoff_last5_on_court_net_rating` | `100×sum(on point differential)/sum(on possessions)` | Up to last 5; ≥20 possessions |
| `onoff_last10_on_possessions_n` | Sum on-court possession equivalents | Up to last 10 |
| `onoff_last10_on_court_net_rating` | `100×sum(on point differential)/sum(on possessions)` | Up to last 10; ≥20 possessions |

`onoff_source_max_game_time_us` and `onoff_family_version` are audit metadata.

## Lineup stability (8)

For each valid team-game, identical settled five-player combinations are grouped
and assigned minute shares `p`. The per-game top lineup is selected
deterministically by minutes then lineup key. Phase 3B averages these game-level
summaries over up to five prior games; it never uses the realized target lineup.
Rotation quarantines apply.

| Name | Formula / meaning | Window, minimum and missing behavior |
|---|---|---|
| `lineup_games_before` | Prior games with a trustworthy lineup observation for player | All prior; 0 if none |
| `lineup_last5_games_n` | Valid recent lineup games | Up to 5; 0 if none |
| `lineup_last5_unique_lineups_avg` | Mean number of unique team lineups per game | Up to last 5; ≥1 |
| `lineup_last5_top_lineup_share_avg` | Mean largest lineup-seconds / game-seconds | Up to last 5; ≥1 |
| `lineup_last5_top3_lineup_share_avg` | Mean sum of top-three lineup shares | Up to last 5; ≥1 |
| `lineup_last5_entropy_avg` | Mean per-game `-sum(p×ln p)` | Up to last 5; ≥1 |
| `lineup_last5_player_top_lineup_share_avg` | Mean share of player's own lineup seconds spent in team's top lineup | Up to last 5; NULL if no top-lineup overlap |
| `lineup_last5_continuity_avg` | Mean fraction (0-1) of current game's top five also in team's preceding top five | Up to last 5; NULL until a prior team top lineup exists |

`lineup_source_max_game_time_us` and `lineup_family_version` are audit metadata.

## Historical teammate interaction (4)

Teammates are inferred only from prior reconstructed lineups. These fields
describe historical interaction concentration; they make no assertion that any
teammate will be active in the target game. Shared seconds are summed for each
player-teammate pair per source game. A “stable” teammate shared at least 600
seconds in that source game.

| Name | Formula / meaning | Window, minimum and missing behavior |
|---|---|---|
| `teammate_last5_top_shared_minutes_avg` | Mean per-game maximum pair shared seconds / 60 | Up to last 5 valid lineup games; ≥1 |
| `teammate_last5_top3_shared_minutes_avg` | Mean per-game sum of top-three pair shared seconds / 60 | Up to last 5; ≥1 |
| `teammate_last5_minutes_concentration_avg` | Mean `top pair seconds/sum(all pair seconds)` | Up to last 5; positive pair-seconds guard |
| `teammate_last5_stable_count_avg` | Mean number of teammates with ≥10 shared minutes | Up to last 5; ≥1 |

## Shot geometry and zones

Raw source is canonical `shots`. Coordinates are validated as centimetres on a
single offensive half court with basket `(0,0)` and broad bounds `x±750 cm`,
`y=-150..1400 cm`. `distance=sqrt(x²+y²)/100` metres. The `(-1,-1)` sentinel,
non-field-goal rows, out-of-bounds points, 2P locations beyond 8 m, and 3P
locations inside 5.5 m are non-spatial/quarantined.

Zones in `elfc_halfcourt_cm_v1`:

- rim: 2P distance ≤1.25 m;
- paint non-rim: remaining 2P with `|x|≤2.45 m` and `y≤4.23 m`;
- mid-range: remaining valid 2P;
- corner three: 3P with `|x|≥6.60 m` and `y≤1.42 m`;
- above-break three: remaining valid 3P.

### Player shot profile (33)

Raw observations: `player_game_shot_profile_observations_v1`; only valid spatial
FG attempts. `season` fields use the target season. `last5`/`last10` and the
all-prior count may use E2021 warm-up. Percentages and rates are ratios of summed
counts. A zone percentage is NULL with zero attempts in that zone.

| Name | Formula / meaning | Window, denominator and missing behavior |
|---|---|---|
| `shot_games_before` | Prior games with ≥1 valid spatial attempt | All prior; 0 if none |
| `shot_last5_games_n` | Valid shot games represented | Up to 5; 0 if none |
| `shot_last10_games_n` | Valid shot games represented | Up to 10; 0 if none |
| `shot_attempts_before` | Total valid spatial attempts | All prior; NULL if none |
| `shot_last5_attempts_n` | Valid attempts in recent shot games | Up to last 5 |
| `shot_last10_attempts_n` | Valid attempts in recent shot games | Up to last 10 |
| `shot_season_rim_attempts` | Target-season rim attempts before cutoff | Current season; NULL before first valid attempt |
| `shot_season_paint_attempts` | Target-season paint non-rim attempts | Current season |
| `shot_season_midrange_attempts` | Target-season mid-range attempts | Current season |
| `shot_season_corner3_attempts` | Target-season corner-three attempts | Current season |
| `shot_season_above_break3_attempts` | Target-season above-break-three attempts | Current season |
| `shot_season_rim_attempt_rate` | Rim attempts / all target-season spatial attempts | Positive season attempts |
| `shot_season_paint_attempt_rate` | Paint non-rim attempts / all season attempts | Positive season attempts |
| `shot_season_midrange_attempt_rate` | Mid-range attempts / all season attempts | Positive season attempts |
| `shot_season_corner3_attempt_rate` | Corner threes / all season attempts | Positive season attempts |
| `shot_season_above_break3_attempt_rate` | Above-break threes / all season attempts | Positive season attempts |
| `shot_last5_rim_attempt_rate` | Recent rim attempts / recent spatial attempts | Up to last 5; positive attempts |
| `shot_last5_paint_attempt_rate` | Recent paint attempts / recent spatial attempts | Up to last 5; positive attempts |
| `shot_last5_midrange_attempt_rate` | Recent mid-range attempts / recent attempts | Up to last 5; positive attempts |
| `shot_last5_corner3_attempt_rate` | Recent corner threes / recent attempts | Up to last 5; positive attempts |
| `shot_last5_above_break3_attempt_rate` | Recent above-break threes / recent attempts | Up to last 5; positive attempts |
| `shot_avg_distance_m` | `sum(distance)/sum(attempts)` | Target season; positive attempts |
| `shot_rim_fg_pct` | Rim makes / rim attempts | Target season; positive zone attempts |
| `shot_paint_fg_pct` | Paint non-rim makes / attempts | Target season; positive zone attempts |
| `shot_midrange_fg_pct` | Mid-range makes / attempts | Target season; positive zone attempts |
| `shot_corner3_fg_pct` | Corner-three makes / attempts | Target season; positive zone attempts |
| `shot_above_break3_fg_pct` | Above-break-three makes / attempts | Target season; positive zone attempts |
| `shot_3pa_share` | Valid 3PA / all target-season spatial attempts | Positive season attempts |
| `shot_2pa_share` | Valid 2PA / all target-season spatial attempts | Positive season attempts; exact complement of 3PA share |
| `shot_points_per_shot` | Made-FG points / spatial attempts | Target season; positive attempts; excludes FTs |
| `shot_zone_concentration` | Sum of squared five zone shares | Target season; positive attempts; range `[0.2,1]` |
| `shot_profile_entropy` | `-sum(zone_share×ln(zone_share))`, zero terms omitted | Target season; positive attempts |
| `shot_recent_vs_season_mix_change` | Half the L1 distance between last-five and season five-zone shares | Both mixes required; range `[0,1]` |

`shot_source_max_game_time_us` and `shot_family_version` are audit metadata.
Individual invalid shot coordinates are quarantined without suppressing valid
rotation or other shots from that game.

## Opponent shot profile (24)

Raw observations: `team_game_shot_profile_observations_v1`, grouped by the team
that defended the recorded shooting team. All values describe shots allowed by
the target opponent before cutoff. Season fields reset to the target season;
last-five and all-prior depth can use previous-season warm-up. Rates/FG% are
ratio-of-sums.

| Name | Formula / meaning | Window, denominator and missing behavior |
|---|---|---|
| `opp_shot_games_before` | Opponent prior defensive shot-profile games | All prior; 0 if none |
| `opp_shot_last5_games_n` | Recent opponent games represented | Up to 5; 0 if none |
| `opp_shot_attempts_before` | Valid spatial attempts allowed | All prior; NULL if none |
| `opp_shot_season_rim_attempts_allowed` | Rim attempts allowed | Target season |
| `opp_shot_season_paint_attempts_allowed` | Paint non-rim attempts allowed | Target season |
| `opp_shot_season_midrange_attempts_allowed` | Mid-range attempts allowed | Target season |
| `opp_shot_season_corner3_attempts_allowed` | Corner threes allowed | Target season |
| `opp_shot_season_above_break3_attempts_allowed` | Above-break threes allowed | Target season |
| `opp_shot_season_rim_attempt_rate_allowed` | Rim attempts / all spatial attempts allowed | Positive season attempts |
| `opp_shot_season_paint_attempt_rate_allowed` | Paint attempts / all allowed | Positive season attempts |
| `opp_shot_season_midrange_attempt_rate_allowed` | Mid-range attempts / all allowed | Positive season attempts |
| `opp_shot_season_corner3_attempt_rate_allowed` | Corner threes / all allowed | Positive season attempts |
| `opp_shot_season_above_break3_attempt_rate_allowed` | Above-break threes / all allowed | Positive season attempts |
| `opp_shot_rim_fg_allowed` | Rim makes allowed / rim attempts allowed | Positive zone attempts |
| `opp_shot_paint_fg_allowed` | Paint makes allowed / attempts allowed | Positive zone attempts |
| `opp_shot_midrange_fg_allowed` | Mid-range makes allowed / attempts allowed | Positive zone attempts |
| `opp_shot_corner3_fg_allowed` | Corner-three makes allowed / attempts allowed | Positive zone attempts |
| `opp_shot_above_break3_fg_allowed` | Above-break makes allowed / attempts allowed | Positive zone attempts |
| `opp_shot_avg_distance_allowed_m` | `sum(distance allowed)/sum(attempts allowed)` | Target season; positive attempts |
| `opp_shot_last5_rim_attempt_rate_allowed` | Recent rim attempts / recent allowed attempts | Up to last 5; positive attempts |
| `opp_shot_last5_paint_attempt_rate_allowed` | Recent paint attempts / recent allowed attempts | Up to last 5; positive attempts |
| `opp_shot_last5_midrange_attempt_rate_allowed` | Recent mid-range attempts / recent allowed attempts | Up to last 5; positive attempts |
| `opp_shot_last5_corner3_attempt_rate_allowed` | Recent corner threes / recent allowed attempts | Up to last 5; positive attempts |
| `opp_shot_last5_above_break3_attempt_rate_allowed` | Recent above-break threes / recent allowed attempts | Up to last 5; positive attempts |

`opp_shot_source_max_game_time_us` is audit-only.

## Player × opponent interactions (3)

These are transparent products of two already point-in-time-safe inputs; no
target-game shots or automated polynomial expansion is used.

| Name | Formula | Missing behavior |
|---|---|---|
| `interaction_rim_style` | `shot_season_rim_attempt_rate × opp_shot_season_rim_attempt_rate_allowed` | NULL if either input unavailable |
| `interaction_3pa_style` | `shot_3pa_share × (opp corner3 rate + opp above-break3 rate)` | NULL if either side unavailable |
| `interaction_corner3_style` | `shot_season_corner3_attempt_rate × opp corner3 rate allowed` | NULL if either input unavailable |

## Availability, audit and eligibility fields

| Name | Meaning |
|---|---|
| `has_rotation_features` | At least one prior rotation-usable player game |
| `has_onoff_features` | Prior on/off history and at least 20 last-five on-court possession equivalents |
| `has_lineup_features` | At least one prior valid lineup game |
| `has_shot_profile_features` | At least one prior player game with a valid spatial attempt |
| `has_opp_shot_features` | At least one prior opponent defensive shot-profile game |
| `rich_any_available` | At least one major family has prior history |
| `rich_all_major_families_available` | All five major family conditions are true |
| `phase3b_primary_eval_eligible` | E2022-E2025 verified target, conditional-on-playing row, all major family conditions true |

Family source-max timestamps are retained specifically for automated leakage
proofs but should not be passed as predictors. Version fields make family
ablation reproducible. E2022-E2024 quarantined Fantasy credits, unsafe current
Fantasy overlays, and every target-game rich outcome are absent from all formulas.
