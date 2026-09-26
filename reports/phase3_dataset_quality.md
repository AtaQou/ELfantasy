# Phase 3A dataset quality

## Outcome

The reproducible `phase3a_core_boxscore_v1` dataset contains 54,642
`conditional_on_playing` player-game rows across eight seasons, 2,528 games,
930 players, and E2018-E2025. All feature inputs are from box scores completed
strictly before `feature_cutoff_time`; PBP, shots, unsafe Fantasy overlays, and
legacy E2022-E2024 credits are absent.

The dataset fingerprint is
`b406af0b506938dafe88bb6f582148a4ee0ec7cf67e0b268276425709ac28977`.
Equivalent regeneration returned the same fingerprint and no duplicate
`player_game_id` values.

## Row eligibility and targets

The target generator considered all 60,101 canonical player-game box-score rows.

| Classification | Rows | Treatment |
| --- | ---: | --- |
| `PLAYED` (`minutes > 0`, not provider DNP) | 54,661 | Eligible for active-player performance |
| `DNP` | 5,437 | Target retained as `NULL`; excluded from active benchmark |
| `UNKNOWN` zero-minute records | 3 | Target retained as `NULL`; excluded |
| `NOT_ROSTERED` | Not materialized | No zero target is inferred |

There are 20,285 roster-membership/game opportunities with no player box-score
row. They are reported as unresolved rostered-but-missing opportunities, not DNPs:
the roster snapshots do not establish reliable historical active-list status for
every game. Availability modelling remains a separate future task.

There are 54,661 valid standardized targets. Of those, 30,072 have historically
verified official rule semantics (E2022-E2025); 24,589 E2018-E2021 targets are
explicit standardized/counterfactual back-scores. The final modelling table has
30,053 official-semantics rows because 19 E2023 player-games lack a trustworthy
target timestamp.

| Season | Box rows | Played targets | DNP | Unknown | Final rows | Players | Cold start |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E2018 | 6,177 | 5,603 | 574 | 0 | 5,603 | 250 | 250 |
| E2019 | 6,001 | 5,509 | 492 | 0 | 5,509 | 298 | 157 |
| E2020 | 7,780 | 7,079 | 701 | 0 | 7,079 | 298 | 69 |
| E2021 | 7,072 | 6,398 | 674 | 0 | 6,398 | 293 | 93 |
| E2022 | 7,785 | 7,060 | 722 | 3 | 7,060 | 295 | 88 |
| E2023 | 7,883 | 7,165 | 718 | 0 | 7,146 | 287 | 76 |
| E2024 | 7,863 | 7,106 | 757 | 0 | 7,106 | 296 | 93 |
| E2025 | 9,540 | 8,741 | 799 | 0 | 8,741 | 335 | 104 |

`career_el_games_before = 0` identifies 930 cold-start rows. There are 2,352
season-first rows. Cold starts remain present with `last_N` statistics `NULL`,
sample sizes zero, and fixed transparent fallback logic only in the baseline
prediction layer.

## Exclusions and quarantines

- E2018/Game 21 has no usable official box score and therefore contributes no
  candidate player-game rows.
- E2023/Game 170 (round 19) has 19 played targets but no trustworthy UTC tip time.
  The targets remain stored; all 19 rows are excluded from point-in-time features.
- The 5,437 DNP and 3 unknown zero-minute records are intentionally excluded from
  the conditional-on-playing benchmark.
- Fourteen `INVALID_PBP_CLOCK` games contribute 301 valid core rows. They are not
  excluded because Phase 3A does not read PBP clocks.
- Rotation-only anomalies do not affect core box-score features. The affected
  E2023/Game 170 is already excluded by the timestamp rule.

No otherwise-valid box-score row was removed because only an unrelated PBP/shot
feature family was quarantined.

## Missingness

Across 101 predictive/structural core columns there are 150,339 `NULL` cells out
of 5,518,842, an overall missing-feature rate of **2.7241%**. This calculation
excludes identifiers, audit timestamps, target outcomes, and optional Fantasy
metadata.

Missingness is primarily expected point-in-time behavior:

| Feature | Missing rows | Reason |
| --- | ---: | --- |
| `season_3p_pct_before` | 8,294 | No prior season 3PA |
| `last_5_3p_pct` | 8,148 | No prior-window 3PA |
| `last_5_ft_pct` | 6,846 | No prior-window FTA |
| `season_ft_pct_before` | 6,292 | No prior season FTA |
| `minutes_trend` | 5,111 | Requires two three-game blocks |
| `season_fp_std_before` | 4,614 | Requires two prior season games |
| `player_age` | 4,186 | Birth date unavailable/unusable |
| `ast_to_ratio` | 3,798 | No prior-window turnover denominator |
| `season_fp_per_min_before` | 3,265 | Fewer than ten prior season minutes |
| `season_2p_pct_before` | 3,171 | No prior season 2PA |
| `season_fg_pct_before`, `season_efg_pct_before` | 2,714 each | No prior FGA |
| `season_ts_pct_before` | 2,659 | No TS denominator |
| Season averages/minutes/start rate | 2,352 each | First appearance of season |

No future values are used to fill these gaps. Standard deviation requires at least
two observations. Per-minute rates require at least ten aggregate minutes. Shooting
percentages are ratio-of-sums and require a positive attempt denominator.

## Fantasy metadata and price coverage

Historical metadata is optional and its absence never removes a basketball row.

| Season | Core rows | Confident Fantasy metadata | Attachment rate | Trusted prices |
| --- | ---: | ---: | ---: | ---: |
| E2022 | 7,060 | 5,184 | 73.43% | 0 |
| E2023 | 7,146 | 5,930 | 82.98% | 0 |
| E2024 | 7,106 | 6,090 | 85.70% | 0 |
| E2025 | 8,741 | 7,921 | 90.62% | 7,921 |

E2022-E2024 attach only season-correct Fantasy ID, position, matchday, team, and
provenance. Their 20,982 retained legacy credit observations are price-quarantined;
zero entered the core table, baseline predictions, value diagnostics, or price-aware
evaluation.

For E2025 matchdays 1-38, 7,921/8,262 active modelling rows have a confident
identity plus validated pre-matchday price (95.87%). The other 341 rows comprise
336 rows/34 players without a confident Fantasy crosswalk and 5 rows/4 confidently
mapped players without an exact team/matchday market join. Thirty-four players
have no usable price on any active matchday; 38 players have at least one missing
row. Another 479 E2025 modelling rows are post-regular-market games beyond Fantasy
matchday 38 and are reported separately, not treated as identity failures.

Historical positions are verified season-level metadata. They are retained for
safe stratification and E2025 evaluation output, but Phase 3A does not treat them
as timestamped intra-season role changes or use them as primary predictive inputs.

## Rescheduled games and chronological safety

Actual trusted game datetime is authoritative for all rolling histories and the
2,528 walk-forward game splits. The following known schedule complications used
the Phase 2.6 explicit competition-round mapping plus source date windows:

| Season / round | Active rows | Attached metadata |
| --- | ---: | ---: |
| E2022 / 24 | 195 | 146 |
| E2023 / 2 | 197 | 157 |
| E2023 / 6 | 193 | 155 |
| E2024 / 17 | 193 | 165 |

Automated canonical audits found zero player-, team-, or opponent-history maximum
times at or after the feature cutoff; zero negative player/team EL-only intervals;
zero duplicate modelling rows; and zero walk-forward ordering inversions.

## Distribution checks

- Target range: -13.0 to 58.3.
- Played minutes: 0.0167 to 48.8833; no negative minutes.
- Guarded last-five FP/min: -0.6977 to 1.7395.
- Season pace: 64.924 to 83.270 possessions per 40 minutes.
- Player age: 15.43 to 40.84 years where known.
- EL-only previous-game interval: 1.79 to 2,540.99 days. The upper extreme is a
  legitimate long EuroLeague absence, not capped.
- Season TS%: 0 to 1.5. Values above 1.0 can legitimately occur in tiny perfect
  three-point samples under the standard TS formula; no arbitrary cap was applied.

No pace near 500, negative interval, unstable near-zero-minute rate, impossible
made/attempt count, duplicate key, or unexplained impossible feature value remains.

## Readiness decision

The core dataset is ready for a Phase 4 CatBoost/XGBoost experiment using the same
walk-forward definitions, subject to selecting target semantics explicitly:
E2022-E2025 for historically verified official targets, or E2018-E2025 for the
common standardized target. No high-severity leakage issue remains. The benchmark
is still conditional on playing and is not an end-to-end availability forecast.

Final verification: all 88 unit/integration tests pass (the previous 67 plus 21
Phase 3A tests), Python compilation passes, and all 35 canonical database integrity
checks pass with zero failures.
