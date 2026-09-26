# Phase 3A Fantasy scoring rules

> Phase 6A.1 production gate: the frozen target remains
> `ELFC_PLAYER_V1_STANDARDIZED_2025`. E2026 is currently
> `SCORING_RULES_UNVERIFIED`, because the current official formula matches but an
> explicit effective E2026/2026-27 player-rule version has not been established.
> Captain, bench, substitutions, transfers, and credits remain separate.

## Decision

Phase 3A uses a versioned, explicit player scoring engine. It does not use PIR as
the target definition, although the calculated base score happens to equal the
canonical PIR in all 60,101 retained player-game records.

The primary historically verified target is available for E2022-E2025. E2018-E2021
remain useful only through a clearly labelled standardized/counterfactual target;
the applicable official Fantasy rules for those seasons could not be established
from an authoritative historical rule page.

| Seasons | `target_rule_status` | `actual_fantasy_points` | `standardized_fantasy_points` |
| --- | --- | --- | --- |
| E2018-E2021 | `STANDARDIZED_COUNTERFACTUAL_HISTORICAL_RULE_UNVERIFIED` | `NULL` | Common rule below |
| E2022-E2024 | `OFFICIAL_HISTORICAL_OUTCOME_VALIDATED` | Populated | Same value |
| E2025 | `OFFICIAL_CURRENT_RULE_AND_SAMPLES_VALIDATED` | Populated | Same value |

All rows carry `target_rule_version = ELFC_PLAYER_V1_STANDARDIZED_2025`. The common
standardized column makes cross-season rolling history numerically compatible;
the status column prevents a standardized back-score from being mistaken for a
verified historical Fantasy outcome.

## Formula

For a player's complete regulation-plus-overtime box score:

```text
base = PTS + REB + AST + STL + BLK + fouls_drawn
       - turnovers - blocks_received - fouls_committed
       - (2PA - 2PM) - (3PA - 3PM) - (FTA - FTM)

team_win_bonus = 0.10 * abs(base) if the player's team won, else 0

fantasy_points = base + team_win_bonus
```

The absolute value in the win bonus is material for negative scores: a winning
player with `base = -2` receives `-1.8`, not `-2.2`. A player's team result is
derived from the final game score. Overtime has no separate bonus or multiplier;
all statistics accumulated in overtime are scored normally. Coaches, captains,
bench multipliers, round-level team totals, trades, and credits are outside this
player-game performance target.

There are no double-double or triple-double bonuses in the verified player
formula. Missed two-point, three-point, and free-throw attempts are each penalized
once. The scorer rejects made-attempt counts that are arithmetically impossible.

## Official sources and rule history

The current official EuroLeague Fantasy Challenge rules document the component
values, the 10% team-win bonus, and regulation/overtime coverage:

- [Official player-scoring rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/players-scoring)
- [Official EuroLeague Fantasy Challenge rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/)

Euroleague Basketball's official 2021-22 launch article describes that edition as
a new and improved format, but does not publish enough player-scoring detail to
prove the E2018-E2021 regimes:

- [Play the new EuroLeague Fantasy Challenge now](https://www.euroleaguebasketball.net/euroleague/news/play-the-new-euroleague-fantasy-challenge-now/)

No archived official scoring page sufficiently established E2018-E2021. Phase 3A
therefore does not claim that the current rule applied then. No external
basketball, market, injury, PBP, shot, or other third-party dataset was collected
as part of this rule research.

No player-scoring change was found within the verified E2022-E2025 evidence. This
does not prove that no unpublished presentation or contest rule changed; it proves
that the retained official outcomes and current formula share one calculable
player-game regime. Captain multipliers and price-market mechanics are separate
from player-game scoring and are excluded.

## Independent validation

### Canonical component check

The engine recomputed the base score component by component. It matched canonical
PIR for 60,101/60,101 records (100%). This is a cross-check only; the implementation
does not set Fantasy points from the `pir` column.

### Retained official E2022-E2024 outcomes

Every retained official Stats `pdk` outcome exactly satisfies the explicit formula
when calculated from that same response's component fields:

| Season | Official outcomes | Exact component matches | Rate |
| --- | ---: | ---: | ---: |
| E2022 | 6,987 | 6,987 | 100% |
| E2023 | 7,073 | 7,073 | 100% |
| E2024 | 6,922 | 6,922 | 100% |

For confidently mapped, unique canonical player-round comparisons, exact rates
were 6,654/6,654 (100%) in E2022, 6,689/6,796 (98.43%) in E2023, and
6,722/6,772 (99.26%) in E2024. The non-exact cases are mostly paired ±1/±1.1
component differences, plus a few larger corrections. The official Fantasy
response always remains internally exact. These cross-feed differences are kept
as evidence of provider snapshot/box-score corrections and were not forced away.

### Retained E2025 point probes

The retained detailed official player endpoint was compared with the calculated
canonical target for one confidently mapped player at two distant matchdays:

| Matchday | Official | Calculated | Result |
| ---: | ---: | ---: | --- |
| 1 | 21.0 | 21.0 | Exact |
| 38 | 30.8 | 30.8 | Exact |

The complete machine-readable evidence is retained in
`data/samples/phase3a/scoring_validation.json`.

## Modelling policy

`actual_fantasy_points` is permitted only for E2022-E2025. The eight-season
baseline benchmark uses `standardized_fantasy_points`, with every prior game
re-scored under the same rule version; it never crosses an incompatible numeric
rule boundary. Any Phase 4 experiment that wants historically verified official
targets only must select `actual_fantasy_points IS NOT NULL`. Reports must continue
to distinguish these two interpretations.
