# Phase 3B rich-feature diagnostics

## Interpretation boundary

These are descriptive diagnostics on the 29,232-row, historically verified,
`conditional_on_playing` E2022-E2025 population. No predictive model was trained.
Correlation with the target or with a Phase 3A residual does not prove incremental
out-of-sample value; Phase 4 must establish that with identical chronological
rows and splits.

## Feature inventory and coverage

| Family | Predictive fields | Full-row missingness | Primary-row missingness |
|---|---:|---:|---:|
| Rotation/role | 26 | 32.765% | 2.945% |
| On/off context | 18 | 32.502% | 2.197% |
| Lineup stability | 8 | 27.775% | 2.458% |
| Historical teammate interaction | 4 | 34.644% | 0.000% |
| Player shot profile | 33 | 32.883% | 3.563% |
| Opponent shot profile | 24 | 31.889% | 1.280% |
| Player × opponent style | 3 | 36.474% | 2.788% |
| **Total** | **116** | — | — |

Full-dataset missingness mainly expresses the deliberate absence of PBP/shots in
E2018-E2020 and genuine first-rich-history cases. Remaining primary-set NULLs are
denominator-specific, such as a player with no prior attempts in one zone or no
same-season off-court sample. Availability/sample columns remain populated.

## Strongest descriptive relationships

### With the official target

Recent role volume is most directly related to the next-game target:

- `rot_minutes_ewma`: Spearman 0.430
- `rot_season_minutes_share_before`: 0.428
- `rot_last5_minutes_share`: 0.423
- `rot_last5_minutes_avg`: 0.423
- `onoff_last5_on_possessions_n`: 0.417
- `teammate_last5_top3_shared_minutes_avg`: 0.417
- `shot_last5_attempts_n`: 0.395

These relationships partly restate opportunity/minutes and must not be described
as independent player quality.

### With the frozen 50/50-blend residual

Residual is `actual_fantasy_points - prediction_season_recent_blend`. The largest
absolute Spearman relationships were:

| Feature | Family | Pearson residual corr. | Spearman residual corr. |
|---|---|---:|---:|
| `shot_points_per_shot` | Player shot | -0.135 | -0.144 |
| `shot_above_break3_fg_pct` | Player shot | -0.083 | -0.096 |
| `onoff_season_on_ortg` | On/off | -0.087 | -0.086 |
| `onoff_last5_on_court_net_rating` | On/off | -0.081 | -0.083 |
| `onoff_season_ortg_diff` | On/off | -0.078 | -0.078 |
| `shot_paint_fg_pct` | Player shot | -0.066 | -0.077 |
| `onoff_last10_on_court_net_rating` | On/off | -0.070 | -0.072 |
| `rot_last5_minutes_share` | Rotation | -0.067 | -0.072 |
| `rot_last5_minutes_avg` | Rotation | -0.067 | -0.071 |
| `teammate_last5_top_shared_minutes_avg` | Teammate | -0.066 | -0.071 |

The negative signs are not errors: on this population the Phase 3A blend tends to
overpredict the next result at the high end of several historical efficiency/
opportunity signals, consistent with regression-to-the-mean effects. That is a
hypothesis for chronological Phase 4 testing, not a conclusion.

Family-level screening suggests the first controlled ablations should be:

1. player shot profile (largest residual association);
2. on/off context (several non-redundant residual associations);
3. rotation/role (strongest target/opportunity relationship and moderate residual
   association).

Lineup-only and opponent-shot-only fields have weaker univariate residual
relationships. They should still be tested because interactions and nonlinear
models can extract information absent from a univariate correlation.

## Redundancy

Exact duplicate columns on the primary population are:

- `rot_games_before`, `onoff_games_before`, and `lineup_games_before`;
- `rot_last5_games_n`, `onoff_last5_games_n`, and `lineup_last5_games_n`.

They intentionally remain family-local sample indicators so ablation datasets are
self-describing. Phase 4 can retain one shared copy after constructing family
availability.

Near-perfect relationships include:

- `shot_3pa_share` vs `shot_2pa_share`: -1.000 by construction;
- `rot_last5_minutes_avg` vs `rot_last5_minutes_share`: 0.9997 in mostly
  regulation games;
- `opp_shot_games_before` vs `opp_shot_attempts_before`: 0.9980;
- rotation/on-off/lineup prior-game counts vs `shot_games_before`: about 0.9948;
- top-one vs top-three teammate shared minutes: 0.9930;
- `interaction_3pa_style` vs the player 2PA/3PA shares: about ±0.9845.

No predictive field has at least 50% missingness on the primary population, and
no field is effectively constant there. The clean Phase 4 candidate set should
drop one of the complementary 2PA/3PA shares, avoid using both minutes average and
minutes share without regularization/diagnosis, and retain family sample columns
only as needed for controlled ablation and missingness.

## Seasonal stability

| Season | Rows | Rot last-5 min avg | On-court ORtg | Lineup entropy | Player points/shot | Opp rim attempt rate |
|---|---:|---:|---:|---:|---:|---:|
| E2022 | 6,833 | 18.969 | 111.389 | 2.457 | 1.074 | 0.1699 |
| E2023 | 6,984 | 18.798 | 112.521 | 2.421 | 1.082 | 0.1796 |
| E2024 | 6,899 | 18.881 | 114.489 | 2.442 | 1.097 | 0.1556 |
| E2025 | 8,516 | 18.653 | 115.157 | 2.496 | 1.097 | 0.1709 |

Rotation and lineup distributions are stable. Offensive rating rises gradually,
which Phase 4 should allow for through chronology/season context. E2024 has a
lower observed rim share, but coordinate ranges/orientation are stable and no
season-specific geometry shift was detected; this remains a monitored seasonal
distribution difference rather than a quarantine.

## Cold start and limitations

The full Core+Rich table retains cold/absent rich histories with NULLs and flags.
The primary all-major subset necessarily has at least one prior valid rich game,
so it contains no true rich cold-start rows. This selection is appropriate for
an apples-to-apples ablation benchmark but is not an end-to-end availability or
new-player evaluation.

Largest limitations are: possession counts are explicit box-score/PBP proxies,
on/off is contextual and non-causal, and all role/workload history is EuroLeague-
only with no target-game availability knowledge.
