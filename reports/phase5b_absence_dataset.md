# Phase 5B historical rotation-absence dataset

Generated at `2026-08-18T15:54:51.798693+00:00`. Historical zero-minute roster rows are
**rotation absences/non-participation**, not fabricated injury labels. Results are
`CONDITIONAL_ON_KNOWN_ABSENCE_SET`: the true zero-minute set is supplied as the scenario.

## Coverage

- Usable historical team-games: **5,056**.
- Team-games with at least one rotation absence: **3,531**.
- Zero-minute roster observations: **5,435**.
- Non-trivial roles (baseline >=1 minute): **5,408**.
- Available-recipient target rows: **36,631**.
- Seasons: **E2018–E2025**.
- Dataset fingerprint: `bcfaaddaa14b4b06d0863693a99fa05a4cd5d1a939cb2a92e4ef20fd0a45bbed`.

| season | team_games | roster_rows | zero_rows |
|---|---|---|---|
| E2018 | 518 | 6177 | 574 |
| E2019 | 504 | 6001 | 492 |
| E2020 | 656 | 7780 | 701 |
| E2021 | 598 | 7072 | 674 |
| E2022 | 656 | 7785 | 725 |
| E2023 | 660 | 7859 | 713 |
| E2024 | 660 | 7863 | 757 |
| E2025 | 804 | 9540 | 799 |

## Absence magnitude

One absence occurred in **1,954** team-games, two in
**1,269**, and three or more in
**308**. Multiple all-low-role absences combined to
at least 20 expected minutes in **47** team-games.

The total missing-minute distribution was: minimum **0.09**, P10
**4.99**, P25 **6.96**, median **11.29**,
P75 **16.93**, P90 **23.10**, and maximum
**66.04** regulation minutes.

No role threshold was used to define an absence. Near-zero roles remain in the
dataset and naturally contribute near-zero missing minutes.

## Baseline and target

Baseline minutes are fixed-config, walk-forward conditional-minutes predictions
using prior-season model fits, with a weighted pre-game role fallback (35% EWMA,
25% last-five, 15% last-three, 15% season average, 10% last game; career/equal-team
fallback thereafter). Each full candidate vector is normalized to 200 before an
absence set is removed. Target minutes are `actual player minutes / actual team
player-minutes × 200`, so overtime is converted to regulation-equivalent allocation.

The recipient target is `actual_regulation_equivalent_minutes -
baseline_normalized_expected_minutes`. Player history, rotation history, teammate
overlap, and prior absence response use observations strictly before target tip.
