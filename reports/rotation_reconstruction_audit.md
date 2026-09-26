# Rotation Reconstruction Feasibility Audit

## Verdict

**POSSIBLE WITH CAVEATS**.

The three retained E2025 regulation games remain exact: all 72 player rows reconcile to the official box score to the second. The retained E2023/170 four-overtime game is structurally consistent but not minute-exact: every substitution transaction produces a legal five-player lineup and both team totals reconcile, while four individual players differ from the official box score by exactly 60 seconds.

The discrepancy is a conflict between official feeds. It must not be silently corrected or used as evidence that one source is necessarily right. Historical rotation construction is feasible if every game is reconciled and discrepant games are quarantined or assigned an explicit, documented correction policy.

Machine-readable evidence: [`data/samples/rotation_reconstruction_summary.json`](../data/samples/rotation_reconstruction_summary.json)

## Scope and method

This is an offline audit with zero network calls. It covers:

- E2025 games 1, 2, and 11 from the retained raw and normalized sample
- E2023 game 170, Real Madrid 130–126 Anadolu Efes after four overtimes, from the bounded raw overtime probe

For each game, the audit:

1. Initializes both teams from the five players marked `IsStarter=1`.
2. Carries the ending lineup across period boundaries.
3. Accrues time from the decreasing period clock: 600 seconds in regulation and 300 in overtime.
4. Applies every explicit `IN` and `OUT` in source response order.
5. Validates membership and waits for pending substitution transactions to settle back to five players.
6. Reconciles reconstructed seconds against official player `MM:SS`, treating `DNP` as zero.
7. Checks Header game time and box-score overtime fields against the PBP period count.

## Exact results

| Game | Periods | PBP events | Player rows | Participants | DNP | IN/OUT events | Transactions | Interleaved | Lineup segments | Exact minute rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E2025/1 | 4 | 546 | 24 | 23 | 1 | 124 | 44 | 1 | 37 | 24/24 |
| E2025/2 | 4 | 603 | 24 | 22 | 2 | 144 | 45 | 5 | 38 | 24/24 |
| E2025/11 | 4 | 496 | 24 | 20 | 4 | 96 | 32 | 3 | 28 | 24/24 |
| E2023/170 | 8 | 741 | 24 | 19 | 5 | 140 | 52 | 2 | 42 | 20/24 |
| **Total** | **20** | **2,386** | **96** | **84** | **12** | **504** | **173** | **11** | **145** | **92/96** |

All eight team-games reconcile at team level:

- each regulation team: 12,000 player-seconds, or 200:00
- each four-overtime team: 18,000 player-seconds, or 300:00
- official total across all fixtures: **108,000 player-seconds**
- reconstructed total: **108,000 player-seconds**
- exact individual rows: **92 of 96**
- individual discrepancy rows: **4**
- maximum absolute individual error: **60 seconds**
- total absolute individual error: **240 seconds**

All 12 DNP rows reconstruct to zero, and no DNP player appears in a substitution event.

## Four-overtime result

The OT game is correctly recognized as eight periods and 60:00:

- Header `GameTime`: 3,600 seconds
- reconstructed PBP period time: 3,600 seconds
- Boxscore overtime columns: `Extra1` through `Extra4`
- PBP overtime periods: 4
- begin-period events: 8
- end-period events: 8
- end-game events: 1

Its 140 substitution events form 52 balanced team-clock transactions. There are no invalid IN/OUT memberships, incomplete transactions, non-five settled states, missing substitution fields, non-box-score players, or DNP substitutions.

Four individual minute rows nevertheless disagree:

| Team | Player ID | Official | Reconstructed | Difference |
| --- | --- | ---: | ---: | ---: |
| MAD | P001392 | 42:55 | 43:55 | +60 s |
| MAD | P005928 | 46:32 | 45:32 | −60 s |
| IST | P012788 | 23:58 | 24:58 | +60 s |
| IST | PLHK | 52:56 | 51:56 | −60 s |

The paired offsets preserve both team totals. The raw substitution stream does not provide an unambiguous, source-faithful adjustment that resolves all four rows, so the audit retains the disagreement rather than shifting a clock heuristically.

## Overtime representation

The real legacy PBP stores all four overtimes in one flat `ExtraTime` array of 271 events rather than four nested lists. Its elapsed `MINUTE` values run from 41 through 61.

Timed 05:00 substitutions and `BP` at boundary minutes 46, 51, and 56 belong to the next overtime. Closing `EP`/`EG` markers use the same boundary convention but belong to the period that just ended; the final markers use minute 61. The audit handles this explicit marker exception, preventing a false fifth overtime.

Some period-start substitutions appear before the corresponding `BP` in source response order. Rotation logic must use the elapsed-minute/marker rules and retain source sequence; it cannot split overtime solely when a `BP` row is encountered.

## Raw overtime provenance

The following hashes cover the exact retained public API response bytes:

| File | Bytes | SHA-256 | Source |
| --- | ---: | --- | --- |
| `game_170_boxscore.json` | 13,862 | `0bd2d930e045c35f3c035972a9230950cb976a1796d65081a2f7c14c90d70a79` | [Boxscore](https://live.euroleague.net/api/Boxscore?gamecode=170&seasoncode=E2023) |
| `game_170_header.json` | 913 | `a34261ef3170c8ef65edbd48ccd67b7758d935079db19081483ac3b8de77d879` | [Header](https://live.euroleague.net/api/Header?gamecode=170&seasoncode=E2023) |
| `game_170_play_by_play.json` | 198,765 | `7aa375371ea628c5ce7392eda98fcddcdf756becf99d2bd6317523706814d2ff` | [PlaybyPlay](https://live.euroleague.net/api/PlaybyPlay?gamecode=170&seasoncode=E2023) |

The generated JSON summary repeats the paths, byte counts, hashes, and source URLs so provenance is machine-verifiable.

## Substitution transaction integrity

Across all four games there are **252 `IN` and 252 `OUT` events**, forming 173 period-clock-team transactions:

| Players changed in one transaction | Transactions |
| ---: | ---: |
| 1 | 112 |
| 2 | 46 |
| 3 | 12 |
| 4 | 3 |

The combined audit found:

- **173/173** transactions with equal IN and OUT counts
- **0** invalid OUT events
- **0** invalid IN events
- **0** settled transactions ending with other than five players
- **0** unsettled period endings
- **0** substitutions with missing team, player, or clock
- **0** substitution players absent from the box score
- **0** DNP substitutions

Sixty-one transactions change multiple players. Of 173 transactions, 162 are contiguous and **11 are interleaved** with free throws, opponent substitutions, or timeouts. Applying individual records temporarily creates a pending non-five state after 269 substitution rows, but there are **zero non-substitution game events while a team remains unsettled**.

## Ordering findings

| Check | Observed |
| --- | ---: |
| Duplicate `NUMBEROFPLAY` values | 0 |
| Gaps inside event-number ranges | 208 |
| Event-number inversions | 17 |
| Fully duplicated raw events | 0 |
| Remaining-clock increases in full source order | 19 |
| Remaining-clock increases among substitution events | 0 |

`NUMBEROFPLAY` is unique in these fixtures but is not chronological. The overall event clock also occasionally increases when linked statistical records are appended. Substitution clocks remain monotonic within every audited period.

The normalized regulation sample retains `source_sequence` and `source_period`. Equivalent fields are required when the OT fixture is eventually normalized.

## Timeouts and player-event attribution

The combined PBP contains 46 timeout events. Twenty-seven share a clock with substitutions, 25 are directly adjacent, and only 19 share the clock with a substitution by the timeout-taking team. A timeout is contextual evidence, not a lineup boundary.

There are 1,744 non-substitution events tagged to a real box-score player and four bench-foul rows using pseudo IDs `CO_A` and `CO_B`. Every real event belongs to either the pre- or post-substitution lineup at its clock. Three regulation-game events are listed after the credited player has already received a same-clock OUT in raw order; the OT fixture adds no such mismatches.

Store `lineup_before` and `lineup_after` for substitution-clock events and flag boundary ambiguity instead of forcing attribution.

## Recommended reconstruction algorithm

1. Read regulation containers explicitly: `FirstQuarter`, `SecondQuarter`, `ThirdQuarter`, and `ForthQuarter`.
2. For a flat `ExtraTime` array, derive OT number from elapsed `MINUTE`; assign boundary `EP`/`EG` markers to the preceding OT while timed 05:00 rows and `BP` belong to the next.
3. Preserve `source_sequence`, `source_period`, raw `NUMBEROFPLAY`, and raw clock. Never sort solely by event number.
4. Initialize Quarter 1 from the five official starters and carry the ending five into every later period.
5. Use 600 seconds for regulation and 300 seconds for overtime.
6. Accrue a stable lineup only when moving to a lower substitution clock.
7. Process equal-clock IN/OUT rows in source order and publish a state only when the pending team returns to five.
8. Do not infer lineup changes from timeouts.
9. Retain pre- and post-transaction lineups for same-clock event attribution.
10. Quarantine games with structural failures or official-minute discrepancies. Preserve both raw and reconstructed values; never auto-shift a substitution clock merely to force agreement.

## Why the verdict remains qualified

The real multi-overtime probe resolves the structural OT question: four OT periods and all their substitutions can be parsed into valid lineups. It also demonstrates that valid lineups and exact team totals do not guarantee exact player-level agreement across official feeds.

The audit still covers only four games across E2023 and E2025. Older seasons need a small stratified reconciliation sample before reconstructed rotations are treated as a production historical dataset. Suitable cases include E2010, DNP-heavy games, rescheduled games, and additional single- and multi-overtime games.

## Reproduce

Regenerate the machine-readable summary:

```bash
python -m scripts.audit_rotation_reconstruction
```

The command writes the JSON and currently exits with status 1 because strict player-minute reconciliation correctly fails on the four OT rows. Structural invariants and the overall `POSSIBLE WITH CAVEATS` verdict are recorded separately.

Run the focused tests:

```bash
python -m unittest tests.test_rotation_audit -v
```
