# Phase 3B dataset quality

## Scope and row policy

Phase 3B is a modular left extension of the unchanged 54,642-row Phase 3A
`conditional_on_playing` dataset. No row is dropped because a rich family is
missing. `ml_player_game_rich_features_v1` preserves the Phase 3A row identity;
`ml_player_game_core_plus_rich_v1` attaches it to the core and safe Fantasy
metadata layers.

The primary official-rule comparison set contains 29,232 E2022-E2025 rows, 542
players, 1,390 games and four seasons. It requires all five major families and at
least 20 last-five on-court possession equivalents. E2021 is retained only as
rich warm-up history/standardized diagnostic data. E2018-E2020 have no rich raw
coverage and therefore keep NULL rich fields.

## Raw rich coverage

| Season | PBP games | PBP events | Shot games | Shots | Rotation-usable games |
|---|---:|---:|---:|---:|---:|
| E2021 | 299 | 151,766 | 299 | 43,555 | 278 |
| E2022 | 328 | 171,870 | 328 | 49,409 | 302 |
| E2023 | 331 | 172,265 | 331 | 50,159 | 307 |
| E2024 | 330 | 176,483 | 330 | 51,193 | 315 |
| E2025 | 402 | 222,976 | 402 | 64,137 | 378 |
| **Total** | **1,690** | **895,360** | **1,690** | **258,453** | **1,580** |

The existing requested PBP/shot ingestion remains complete; Phase 3B made no
network requests.

## Rotation reconstruction and minute reconciliation

Starters initialize each team at five players. PBP `OUT`/`IN` records are grouped
as atomic same-clock transactions; settled states must have exactly five unique
players. A provider record containing the same player as both OUT and IN at the
same dead-ball clock is treated as a net no-op. Periods 1-4 are 600 seconds and
each explicit overtime period is 300 seconds. Impossible sequences are not
repaired.

- PBP games audited: 1,690
- Structurally reconstructable: 1,643
- Rotation usable after minute reconciliation: 1,580 (93.49% of all PBP games)
- Games exact to official minutes: 1,580
- Games near-exact at the documented ±5-second tolerance: 1,580
- Structurally valid player-minute comparisons: 35,452
- Exact player-minute rows: 35,204 (99.30%)
- Outside tolerance before game quarantine: 248 (0.70%)
- Non-zero absolute differences: 9 seconds (2), 14 (1), 60 (230), 120 (13),
  180 (2)
- Maximum structural-game difference: 180 seconds
- Usable-game player-minute differences outside tolerance: 0
- Reusable player stints: 106,597
- Reusable team lineup stints: 106,090

### Rotation/on-off/lineup quarantine reasons

| Reason | Games |
|---|---:|
| Player minutes outside tolerance | 63 |
| Unbalanced substitution transaction | 17 |
| Known invalid PBP clock sentinel | 14 |
| OUT not on court and/or IN already on court | 14 |
| Settled lineup not five (with invalid substitution state) | 6 |
| Known E2023/Game 170 rotation discrepancy | 1 |
| Clock exceeds period length | 1 |

Reasons can overlap inside one row; the mutually exclusive stored reason strings
sum to 110 quarantined games. E2023/Game 170 is also barred as a strict
chronological source because its UTC tip time is untrusted.

## Possession and lineup coverage

Exact possession boundaries cannot be proven from the retained provider event
schema, particularly around technical/free-throw sequences. Phase 3B therefore
does not claim 226,704 exact possessions. It materializes 3,160 team-game rows
(two for each rotation-usable game) totaling **226,704.36 estimated possession
equivalents** under:

`FGA - OREB + TO + 0.44 × FTA`

Lineup-stint components retain FGA, OREB, TO, FTA, points and the same clearly
named proxy. The on/off features use those components and are descriptive team
context, not causal impact estimates. There are 34,102 player-game rotation,
on/off and lineup observation rows. Every usable team lineup covers exactly the
official regulation-plus-overtime duration, and every settled lineup has five
distinct players.

## Shot-coordinate validation

The provider coordinates behave consistently as centimetres on one offensive
half court, with basket at `(0, 0)`, observed ranges `x=-740..746` and
`y=-138..1304`, and validation bounds `x=-750..750`, `y=-150..1400`. Home and
away distributions have the same orientation in every season; no mirror transform
is required. Season/home-away mean coordinates remain small and stable on x, with
mean y roughly 2.94-3.11 m.

Of 210,751 field-goal-labelled rows, 210,639 (99.947%) have usable geometry.
Quarantines are 53 three-point/geometry conflicts, 19 two-point/geometry conflicts,
and 40 field-goal-labelled rows carrying the `(-1,-1)` free-throw sentinel.
The other 47,702 rows are non-field-goal/free-throw records and are intentionally
non-spatial. No shot lies outside the validated broad court bounds, and no exact
duplicate exists on the canonical game/event/player/action key.

Valid attempts by zone are: rim 35,069; paint non-rim 65,479; mid-range 23,921;
corner three 9,580; above-the-break three 76,590.

## Rich availability and missingness

| Family | Rows with family | Row coverage | Predictive fields | Full missingness | Primary missingness |
|---|---:|---:|---:|---:|---:|
| Rotation | 35,712 | 65.36% | 26 | 32.765% | 2.945% |
| On/off | 35,206 | 64.43% | 18 | 32.502% | 2.197% |
| Lineup | 35,712 | 65.36% | 8 | 27.775% | 2.458% |
| Teammate interaction history | 35,712 via lineup | 65.36% | 4 | 34.644% | 0.000% |
| Player shot | 35,610 | 65.17% | 33 | 32.883% | 3.563% |
| Opponent shot | 36,189 | 66.23% | 24 | 31.889% | 1.280% |
| Style interactions | derived when inputs exist | 63.53% non-null | 3 | 36.474% | 2.788% |

Any rich family: 36,243 rows. All five major families: 35,145 rows. The primary
set is smaller because it also requires verified E2022-E2025 targets and the
on/off sample guard.

Primary history-depth ranges are 1-177 valid rotation games (median 46), 20.00-
349.60 last-five on-court possession equivalents (median 169.18), 1-2,539 prior
player shot attempts (median 282), and 59-12,914 prior opponent attempts (median
5,990). Up-to-N semantics are used throughout; no future values fill short
history. Family-specific denominators remain NULL when their statistic cannot be
formed.

## Sanity, leakage and chronology results

- Source-at/after-cutoff violations: 0 for rotation, on/off, lineup, player shot
  and opponent shot.
- Duplicate modelling row IDs: 0.
- Negative/zero-duration stints: 0.
- Player on-court time beyond regulation plus overtime: 0.
- Team lineup duration disagreements: 0.
- Settled lineups with other than five distinct players: 0.
- Negative estimated team-game possessions: 0.
- Negative rich sample sizes: 0.
- Rates outside `[0,1]`: 0.
- Target-game starter, minutes, lineups, substitutions, shots and on/off outcomes
  in the rich-only table: 0 fields.
- Known rescheduled contexts are ordered with actual datetimes rather than nominal
  round numbers.
- E2022-E2024 quarantined Fantasy credits are absent because no Fantasy credit is
  an input to any Phase 3B module.

Observed but plausible extremes were retained: rotation last-five minutes averages
0.017-43.2 (the high end is overtime), on-court pace 58.84-89.59, average player
shot distance 0.13-13.76 m, and lineup entropy 1.69-3.31. No unexplained impossible
value was found, so no capping or winsorization was applied.

## Feature-family-specific quarantine behavior

The 110 rotation failures are absent only from rotation, on/off and lineup-derived
source observations. Their box scores and valid shots remain usable; all 54,642
core modelling rows remain present. Conversely, the 112 individual spatial shot
anomalies are excluded from shot zones without invalidating their games' rotation
features. This is the intended feature-specific quarantine policy.

## Reproducibility

- Rich pipeline: `phase3b_rich_v1`
- Rotation reconstruction: `phase3b_rotation_v1`
- Shot geometry: `elfc_halfcourt_cm_v1`
- Dataset SHA-256: `c02fd9e781cdc090f6bedd2c16b2ca47d3d47007d79ed25ab9c8cd342f8c64b3`
- Reconstruction SHA-256: `6884c06dba8fe3ce907210af0cb80c3acac53b85a1624d71908b4476e6ceb722`

Generated timestamps are audit metadata and are not included in equivalence. A
second generation against unchanged canonical inputs reproduced both normalized
hashes. DuckDB's parallel floating reductions produced harmless differences up to
`1.42e-14` in six aggregate fields, so the dataset hash canonicalizes floats to 12
decimal places—well below modelling precision. The validation CLI fails if the
live normalized dataset differs from the retained build summary.
