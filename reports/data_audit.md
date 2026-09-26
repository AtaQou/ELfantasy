# EuroLeague Fantasy AI — Phase 1 and 1.5 Data Audit

Audit date: 2026-08-11  
Competition tested: EuroLeague (`E`)  
Runtime: Python 3.13  
Status: Phase 1.5 complete; no production database, feature pipeline, prediction model, simulation, or optimizer was built.

## Executive conclusion

The basketball side is strong enough to support a future game-level modelling dataset. EuroLeague-hosted endpoints provide schedules, results, roster metadata, per-game player and team box scores, play-by-play, and shot coordinates. The small Phase 1 sample contains the timestamps, round labels, season-scoped game codes, UUIDs, stable-looking person codes, team codes, and raw game outcomes needed for leakage-safe historical reconstruction.

Phase 1.5 established a safe Fantasy access path. A visible Playwright browser lets the user sign in manually, saves browser state only under ignored `.auth/`, and can bootstrap a normal authenticated HTTP client. The clean JSON client successfully retrieved the current 365-record market without Playwright being the normal collection mechanism. No username, password, cookie, bearer value, or user-owned Fantasy roster was logged or included in the retained samples.

The Fantasy result is **B. Limited historical Fantasy prices appear available**. Current-season matchdays 1, 19, 37, and 38 returned addressable market states with materially different `quotation` values and player pools. No previous-season list/index was discovered. Fantasy positions were returned at each tested matchday, but their point-in-time semantics remain unproven. Injury, probability, starting-bench, and average fields were unchanged for every shared player in all historical probes and have no timestamps; they behave as mutable current overlays, not historical observations.

There is enough evidence to design a conservative Phase 2 architecture around immutable observations, provider-scoped IDs, crosswalk evidence, and explicit knowledge timestamps. There is **not** enough Fantasy history for a trustworthy multi-season roster backtest: previous-season price coverage and timestamped availability history remain missing, and current identity matching does not cover the entire market automatically.

There is also a productization gate: the published [Euroleague Basketball Terms of Use](https://inform.euroleague.net/terms) place restrictions on reuse of statistical data, including fantasy products, archived play-by-play, and comprehensive databases. Before a full-history ingestion or a public/commercial product, the intended use should be reviewed and, where required, written permission obtained. This is a risk flag, not legal advice.

## Audit method

- Inspected the installed [`euroleague-api` package](https://pypi.org/project/euroleague-api/) (`0.1.1`) and its [source repository](https://github.com/giasemidis/euroleague_api).
- Called EuroLeague-hosted v1, v2, v3, and legacy live endpoints directly so original bodies and fields were not lost through DataFrame normalization.
- Downloaded a deliberately small E2025 sample: Rounds 1–2 schedules and three detailed games.
- Fetched full v1 schedule/result/roster listings for selected seasons, then probed only the first and last played game for the large per-game feeds.
- Ran a focused boundary probe for E2006–E2009 after E2005 failed and E2010 succeeded.
- Inspected the official Fantasy application, its public configuration, its route definitions, and the published rules. No authentication controls were bypassed.
- Used a one-time, visible Playwright login in which the user entered credentials only in the official browser page. Reused the saved browser state without printing or copying any secret value.
- Captured one authenticated current market response, sanitized it, and compared only three historical matchdays supplied by the current anonymous league configuration: Rounds 37, 19, and 1. No IDs or endpoints were brute-forced.
- Probed the frontend-supported Fantasy-points route for one market player at current Matchday 38 and config-listed Matchday 1; both returned structured, sanitized round detail.
- Audited current-season Fantasy-to-official player/team identity using conservative exact/normalized matching; fuzzy and name-only candidates were not auto-accepted.
- Reconstructed lineups offline for three regulation fixtures and one four-overtime fixture, then reconciled every team total and all player minutes against the official box scores.
- Classified the requested feature inventory and reviewed candidate external sources and reference-project architecture. See the [feature-source matrix](feature_source_matrix.md), [rotation audit](rotation_reconstruction_audit.md), and [reference-project lessons](reference_project_lessons.md).

The coverage results prove that records existed for the tested calls. They are **not** an exhaustive integrity scan of every game in every season.

## Source overview

| Data | Available? | Source | Demonstrated historical coverage | Key fields | Main issues |
| --- | --- | --- | --- | --- | --- |
| Schedule | Yes | v1 XML; v2 round-games JSON | v1 tested E2000–E2025 | season, round, game code/UUID, UTC/local time, teams, venue, played/status | Schedule and results can differ; fixture data can be revised |
| Results | Yes | v1 XML; v2 round-games JSON; live Header | v1 tested E2000–E2025 | final score, quarter scores, game time, attendance, officials | v2 `winner` was wrong in 19/20 sampled games |
| Player box score | Yes | legacy Boxscore; v3 game stats | v3 tested to E2000; legacy earliest successful test E2007 | full traditional line, starter, minutes, PIR, plus-minus | DNP strings, padded IDs, misspelled raw fields, legacy cutoff |
| Team stats | Yes | legacy Boxscore totals; v3 game stats | v3 tested to E2000; legacy earliest successful test E2007 | equivalent team totals and team rebounds | Team plus-minus is not reliably populated |
| Aggregate advanced stats | Yes, with caveats | v3 player/team statistics; v2 leaders | E2025 field probe only | shooting/rebound/assist/turnover rates; player possessions | Full-season state leaks; no tested team pace/ORtg/DRtg fields |
| Team comparison | Partial | v3 `teamsComparison` | Current three-game sample only | comparative team summary | 1/3 succeeded; two otherwise valid games returned 404 |
| Play-by-play / rotations | Yes / qualified | legacy PlaybyPlay | earliest successful feed test E2007; lineup audit E2023 and E2025 | period, clock, player/team, event type, score, substitutions, retained source order | **POSSIBLE WITH CAVEATS**: 92/96 player-minute rows exact; four OT rows differed by 60 seconds |
| Shots | Partial | legacy Points | earliest successful test E2007 | shooter, team, action, result, coordinates, clock, context flags | Missed free throws are absent; FT coordinates are sentinels |
| Players/rosters | Yes | v1 teams XML; v2 players; nested v3 memberships | v1 tested E2000–E2025 | person code, names, team, position, height, weight, DOB, nationality, dates | “Stable” ID linkage is strongly suggested, not formally guaranteed; roster data may be corrected later |
| Fantasy credits | Yes, authenticated | Fantaking JSON API used by official Fantasy app | Current plus tested current-season matchdays 1/19/37; no old-season archive discovered | `quotation`, Fantasy player/coach ID, list ID, matchday ID | Very high leakage risk; no prior-price field, timestamp, or published formula |
| Fantasy positions | Yes, authenticated; historical semantics qualified | Fantasy config and matchday player market | Returned for current-season matchdays 1/19/37/38; previous seasons untested | nested position ID/name: Guard, Forward, Center, Head Coach | Zero changes among shared players do not prove assignments are historical rather than overlays |
| Fantasy official points | Yes, authenticated; limited probe | Fantaking single-player `/fantasy-pts` route | One player succeeded at current-season matchdays 1 and 38; full pool/old seasons untested | total Fantasy score, component values/points, match, matchday, Turn | Authentication required; postponed-game awards may not be derivable from basketball stats |
| Injuries/status | Current structured fields only | Authenticated Fantasy matchday market | Fields returned on all tested matchdays, but no historical as-of behavior | `is_injured`, `probability_of_playing`, `started_from_bench`, `is_on_fire` | No timestamp/history; tested injury/probability/bench values repeated current values for every shared ID |
| Expected lineups/minutes | No direct source found | Must be modelled or sourced elsewhere | None | — | Requires injuries, news, rotation context, and timestamped snapshots |

## Endpoint map

These are the observed request patterns. They are hosted on EuroLeague domains, but several are not described by a stable public developer contract.

```text
# v1 XML
GET https://api-live.euroleague.net/v1/schedules?seasonCode=E2025
GET https://api-live.euroleague.net/v1/results?seasonCode=E2025
GET https://api-live.euroleague.net/v1/teams?seasonCode=E2025

# v2 JSON
GET https://api-live.euroleague.net/v2/competitions/E/seasons/E2025/games?roundNumber=1
GET https://api-live.euroleague.net/v2/competitions/E/players?seasonCode=E2025&Limit=1000&Offset=0

# v3 JSON
GET https://api-live.euroleague.net/v3/competitions/E/seasons/E2025/games/1/report
GET https://api-live.euroleague.net/v3/competitions/E/seasons/E2025/games/1/stats
GET https://api-live.euroleague.net/v3/competitions/E/seasons/E2025/games/1/teamsComparison

# legacy live JSON
GET https://live.euroleague.net/api/Header?gamecode=1&seasoncode=E2025
GET https://live.euroleague.net/api/Boxscore?gamecode=1&seasoncode=E2025
GET https://live.euroleague.net/api/PlaybyPlay?gamecode=1&seasoncode=E2025
GET https://live.euroleague.net/api/Points?gamecode=1&seasoncode=E2025
```

The [EuroLeague Swagger UI](https://api-live.euroleague.net/swagger/index.html) exists, but it did not provide a useful complete route catalogue during this audit. The package was helpful for discovery, yet it does not expose every useful endpoint—most notably the richer v2 player-membership response used here. The project therefore keeps a small direct HTTP client and preserves raw bodies.

### `euroleague-api` package assessment

The installed `euroleague-api==0.1.1` ran under Python 3.13 and is useful for interactive DataFrame work. It wraps schedules, game metadata, box scores, player/team game stats, play-by-play, shots, and several v3 aggregate player/team-stat views, with helpers for a round, season, or season range.

It is not a complete or raw archival layer. It does not expose all useful v2 membership/game objects, it normalizes responses immediately, and some convenience functions depend on aggregate endpoints that would be unsafe as historical features without an as-of guarantee. Its repository does not currently provide a substantive test suite, and source inspection found code paths that merit validation before bulk use. The direct client in this project is intentionally small and can coexist with the package rather than replacing every convenience method.

The package is published under GPLv3 with a commercial-license option noted by its [PyPI project page](https://pypi.org/project/euroleague-api/). That licensing choice is separate from Euroleague Basketball's rights in the underlying data; both must be considered for later product use.

## Detailed field findings

### Games and schedule

The v1 schedule contains:

```text
gamecode, game, gameday, round, group,
date, startime, endtime, confirmeddate, confirmedtime,
homecode, hometeam, awaycode, awayteam,
hometv, awaytv,
arenacode, arenaname, arenacapacity,
played
```

The v1 result listing adds final home/away scores and a result game number. The v2 round response is much richer:

```text
id (UUID), identifier, gameCode,
season, competition, group, phase, round/round name,
played, gameStatus,
date, localDate, utcDate, localTimeZone,
home and road club objects, scores and period partials,
audience, referees, venue/address/capacity,
neutral-venue flag, winner
```

Overtime is detectable. v2 includes `extraPeriods`; live Header includes total game time and extra-time score fields; play-by-play has `ExtraTime`. In the official [four-overtime E2023 game 170](https://www.euroleaguebasketball.net/en/euroleague/game-center/2023-24/real-madrid-anadolu-efes-istanbul/E2023/170/), Header reported `GameTime=60:00`, while richer period data distinguished the overtime periods. Do not infer the number of overtimes from one endpoint without cross-checking.

Important semantics:

- `gameCode` is season-scoped. Use `(competition_code, season_code, game_code)` as a natural source key, or preserve the v2 UUID.
- A completed sampled game still had `gameStatus="Confirmed"`. Determine completion from `played` plus valid scores, not from the status label alone.
- Derive the winner from final scores. All 20 sampled rows returned Olympiacos in the nested winner object, producing 19 score conflicts.
- Use UTC tip time for chronological ordering. Round labels alone are unsafe when games are postponed.

### Player box scores

The legacy response exposes every requested traditional field:

| Raw field | Normalized meaning |
| --- | --- |
| `Player_ID`, `Player`, `Team`, `Dorsal` | source player ID, name, team code, jersey |
| `IsStarter`, `IsPlaying`, `Minutes` | starter flag, final/current on-court flag, `MM:SS` or `DNP` |
| `Points` | points |
| `FieldGoalsMade2`, `FieldGoalsAttempted2` | 2PT made/attempted |
| `FieldGoalsMade3`, `FieldGoalsAttempted3` | 3PT made/attempted |
| `FreeThrowsMade`, `FreeThrowsAttempted` | FT made/attempted |
| `OffensiveRebounds`, `DefensiveRebounds`, `TotalRebounds` | rebounds |
| `Assistances`, `Steals`, `Turnovers` | assists, steals, turnovers |
| `BlocksFavour`, `BlocksAgainst` | blocks made/received |
| `FoulsCommited`, `FoulsReceived` | fouls committed/drawn |
| `Valuation` | PIR |
| `Plusminus` | player plus-minus |

Opponent, home/away, round, UUID, and UTC game time are joined from the game response by the normalizer. Top-level box-score data also include attendance, referees, quarter summaries, live state, coaches, team rebounds, and team totals.

Source caveats:

- Raw IDs and team codes can contain trailing spaces. The normalized sample strips them, while the raw response remains untouched.
- `Minutes="DNP"` is the reliable non-participation marker. `IsPlaying` is **not** a participation flag: 35 sampled rows had real minutes with `IsPlaying=false` after the game.
- Raw misspellings such as `Assistances` and `FoulsCommited` are stable source names and must remain represented in schema inventories.
- Modern sampled games listed 24 players; older games had 20–23. Never assume exactly 12 listed players per team.

The v3 game-stat response carries the same basketball line plus nested person and membership metadata. Its `timePlayed` value is seconds; a checked value of `1850` corresponded to `30:50` in the legacy feed.

### Team statistics and later derivations

Each Boxscore team object contains a `totr` team-total row, a `tmr` team-rebound row, coach name, and player rows. The totals cover points, 2PT/3PT/FT, offensive/defensive/total rebounds, assists, steals, turnovers, blocks made/received, fouls committed/drawn, and PIR. Team total plus-minus should not be assumed because it was not reliably populated.

The separate v3 `teamsComparison` route was only partially available: it returned a usable local/road comparison for sampled game 11, but `404` for sampled games 1 and 2 even though their v3 game-stat responses existed. It is optional enrichment, not a dependable core source.

Current v3 season-aggregate endpoints were also probed. Team `advanced` and `opponentsAdvanced` responses directly contained effective field-goal percentage, true-shooting percentage, offensive/defensive/total rebound percentages, assist ratio, assist-to-turnover ratio, turnover ratio, free-throw rate, 2PT/3PT rates, and scoring-source percentages. Player `advanced` added minutes and an API field misspelled `possesions`; `misc` included starts, wins/losses, double-doubles, and triple-doubles; `scoring` contained attempt/make shares and scoring-source percentages.

No team pace, offensive-rating, or defensive-rating field appeared in the tested aggregate objects. A v2 player-leader request could return round-bounded possession estimates, but only with the source spelling `Possesions`; the documented/package spelling was rejected. The equivalent team possession request was rejected, and a tested team `maxRound` parameter was ignored. These endpoints are useful for cross-checking, not for reconstructing clean historical features.

The raw fields are sufficient to derive later, without adding those calculations in Phase 1:

- `FGA = 2PA + 3PA`
- approximate possessions: `FGA + 0.44 × FTA - ORB + TO`
- pace normalized to a 40-minute game, including overtime adjustment
- offensive and defensive rating per 100 possessions
- effective field-goal percentage and true-shooting percentage
- offensive/defensive/total rebound rates using both teams' totals
- turnover, free-throw, and assist rates
- player share/usage proxies, points per minute, PIR per minute, and Fantasy points per minute
- home/away, rest days, opponent strength, rolling form, and shot-zone profiles

The `0.44` possession factor is an approximation. It should be validated for EuroLeague event semantics before production feature engineering.

For backtesting, raw derivation is preferable to fetching an aggregate endpoint after the season. The E2025 aggregate response observed during this audit already covered all 38 regular-season games; using it for an earlier-round prediction would leak future results.

### Play-by-play

Raw event arrays are:

```text
FirstQuarter, SecondQuarter, ThirdQuarter, ForthQuarter, ExtraTime
```

`ForthQuarter` is the source's spelling. Each event can contain:

```text
TYPE, NUMBEROFPLAY, CODETEAM, PLAYER_ID, PLAYTYPE,
PLAYER, TEAM, DORSAL, MINUTE, MARKERTIME,
POINTS_A, POINTS_B, COMMENT, PLAYINFO
```

Observed play types include made/missed 2PT and 3PT attempts, made/missed free throws, assists, turnovers, steals, offensive/defensive rebounds, fouls, fouls drawn, blocks, timeouts, jump balls, period start/end, and substitutions. The retained regulation sample contains 182 `IN` and 182 `OUT` events with player, team, and clock context.

The larger [rotation reconstruction audit](rotation_reconstruction_audit.md) classifies five-player reconstruction as **POSSIBLE WITH CAVEATS**. Across three regulation games and one four-overtime game, 252 `IN` and 252 `OUT` events formed 173 balanced team-clock transactions. All settled lineups contained five players, all eight team-game minute totals matched, and 92/96 individual player rows reconciled exactly. In the four-overtime game, two paired players on each team differed from the official box score by exactly 60 seconds even though both official team totals remained exact. Those conflicts must be preserved and the affected game quarantined or handled by an explicit correction policy.

`NUMBEROFPLAY` was unique in the four audited games but had 208 gaps and 17 ordering inversions. The full source clock increased 19 times, while substitution clocks stayed monotonic within audited periods. Multiple substitutions can be interleaved with free throws, opponent substitutions, or timeouts; same-clock player events can belong to the pre- or post-transaction lineup. Reconstruction must retain `source_sequence`, source period, raw event number, and raw clock, publish only settled five-player states, and store boundary ambiguity rather than sorting solely by event number. Player IDs are legitimately absent on timeouts, period events, team rebounds, and similar rows.

### Shot data

The Points endpoint returns:

```text
NUM_ANOT, TEAM, ID_PLAYER, PLAYER, ID_ACTION, ACTION, POINTS,
COORD_X, COORD_Y, ZONE,
FASTBREAK, SECOND_CHANCE, POINTS_OFF_TURNOVER,
MINUTE, CONSOLE, POINTS_A, POINTS_B, UTC
```

This supports shooter/team identity, made/missed field goals, coordinates, zone, quarter/clock reconstruction, fast-break/second-chance/points-off-turnover flags, and linking back to play-by-play by event number.

It is a shot-chart feed, not a complete attempt log. In the sample, made/missed field goals and made free throws matched play-by-play counts, but 19 missed-free-throw (`FTA`) play-by-play events had no Points rows. Made free throws use sentinel coordinates `(-1, -1)`. Future shot analysis must join PBP and Points rather than relying on Points alone.

### Players and rosters

The richer v2 players endpoint and nested v3 memberships expose:

```text
person.code, externalId,
canonical/alias/passport/jersey names,
club code/name, jersey number,
position code/name,
height, weight, birth date,
nationality, birth country,
active, membership start/end dates,
season and image metadata
```

Observed basketball position codes were Guard, Forward, and Center. These must not be confused with Fantasy position IDs.

`person.code` is the best candidate identity key. A checked person code such as `006590` aligned with legacy `P006590`, but the two source namespaces should be preserved separately and linked by a tested canonicalization rule. Do not remove the raw values. Names are attributes, never keys.

Historical v1 roster responses worked in every tested season to E2000. Membership dates make transfers representable, but the endpoint may expose retrospectively corrected membership data rather than a true pregame snapshot. The small current-season sample found no duplicate person IDs with multiple names or clubs; that does not establish full historical identity quality.

## Historical coverage results

### Summary

- Full v1 schedule, result, and roster listings returned usable rows for every tested season from E2000 through E2025.
- v3 per-game stats returned usable player rows for both representative games in every tested season, including E2000, E2005, and E2006.
- Legacy Boxscore, PlaybyPlay, and Points succeeded in both probes for E2007, E2008, E2009, E2010, and every later season selected for the audit.
- Those legacy feeds returned HTTP 200 with empty/invalid JSON bodies for both probes in E2006, E2005, and E2000. The earliest demonstrated legacy season is therefore E2007 (2007–08).
- The successful legacy seasons had identical observed key sets for player stats, team totals, play-by-play, and Points rows. This is encouraging but does not prove value semantics or all untested seasons are identical.
- v2 round-game coverage was not independently scanned across all old seasons.

### Probe table

`Schedule/results/roster` counts are full v1 response counts. The four rightmost columns show successful representative games out of two.

| Season | Schedule | Results | Roster memberships | Legacy box/player/team | PBP | Points/shots | v3 game stats |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E2025 | 402 | 402 | 383 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2024 | 330 | 330 | 326 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2023 | 331 | 331 | 320 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2022 | 328 | 328 | 326 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2021 | 327 | 299 | 328 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2020 | 328 | 328 | 368 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2018 | 260 | 260 | 279 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2015 | 250 | 250 | 391 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2010 | 189 | 189 | 401 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2009 | 188 | 188 | 390 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2008 | 188 | 188 | 407 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2007 | 231 | 231 | 428 | 2/2 | 2/2 | 2/2 | 2/2 |
| E2006 | 230 | 230 | 382 | 0/2 | 0/2 | 0/2 | 2/2 |
| E2005 | 231 | 231 | 397 | 0/2 | 0/2 | 0/2 | 2/2 |
| E2000 | 158 | 158 | 364 | 0/2 | 0/2 | 0/2 | 2/2 |

The E2021 schedule/result mismatch—327 schedule rows versus 299 result rows—is a concrete warning against treating those feeds as interchangeable. This season includes competition disruptions; later ingestion should outer-join, retain status/provenance, and classify missing/annulled games explicitly.

Machine-readable evidence is saved in:

- [`historical_coverage.json`](../data/samples/historical_coverage.json)
- [`historical_coverage_boundary.json`](../data/samples/historical_coverage_boundary.json)
- equivalent CSV files beside them

## Small real sample and quality audit

The retained sample covers all schedule rows from E2025 Rounds 1–2 and detailed responses for game codes 1, 2, and 11. It includes 22 successful raw responses. All core calls succeeded; optional v3 `teamsComparison` succeeded for game 11 and returned 404 for games 1 and 2, both recorded in the manifest.

| Normalized data | Rows |
| --- | ---: |
| Games | 20 |
| Player box-score rows | 72 |
| Team box-score rows | 6 |
| Play-by-play events | 1,645 |
| Shot rows | 497 |
| Roster memberships | 335 |

The sample directory contains exact raw bodies, SHA-256 hashes, fetch timestamps, a schema/type inventory, normalized CSVs, and a generated quality summary:

- [`manifest.json`](../data/samples/e2025_rounds_1_2/manifest.json)
- [`schema_inventory.json`](../data/samples/e2025_rounds_1_2/schema_inventory.json)
- [`data_quality_summary.json`](../data/samples/e2025_rounds_1_2/data_quality_summary.json)

Positive checks:

- no duplicate game keys or player-game keys
- no missing player IDs/names in box scores
- no unparsed non-DNP minute strings
- six team-game player-minute sums all equalled 200 minutes
- no missing player IDs in the 497 Points rows
- substitutions were balanced: 182 `IN`, 182 `OUT`

Problems and edge cases:

1. **Corrupt winner field:** 19/20 v2 games disagreed with score-derived winners. Preserve but never trust this field.
2. **Misleading status:** all 20 played games had `gameStatus="Confirmed"`.
3. **DNP and participation:** seven DNP rows; 35 participating rows had `IsPlaying=false`.
4. **Event order:** each detailed game had four play-number inversions; raw response order must be retained.
5. **Expected missing PBP IDs:** 82 events lacked player IDs because many were game/team events.
6. **Incomplete shot feed:** 19 PBP missed-free-throw events versus zero matching Points rows.
7. **Identifier namespaces:** live IDs have a `P` prefix and padding; v2/v3 person codes do not.
8. **Venue ambiguity:** the v2 venue capacity for sampled game 1 was 5,262, while Header's `Capacity` value was 2,110 and appears to represent attendance. Preserve both source and field name.
9. **Schema naming:** `ForthQuarter`, `FoulsCommited`, and `Assistances` are real source spellings.
10. **Roster history:** the Phase 1 sample did not expose name changes or same-season transfers. Phase 1.5 audited current-season Fantasy joins, but a cross-season identity/transfer audit remains necessary.
11. **Team identity:** team codes/names can change with sponsorship, relocation, or competition history. No cross-season alias table was inferred from this small audit.

## Fantasy-specific audit

### Safe authentication, session reuse, and repository protection

The project now uses a bounded authentication bootstrap:

1. `scripts/login_fantasy.py` opens a visible Chromium browser at the official [EuroLeague Fantasy application](https://euroleaguefantasy.euroleaguebasketball.net/).
2. The user enters account credentials only in the official browser UI; Python never receives a password.
3. After a successful protected Fantasy response, Playwright saves browser storage state to `.auth/euroleague.json` through an atomic writer that enforces mode `0700` on `.auth/` and `0600` on the state file.
4. Expired or unusable state fails closed and instructs the user to repeat the manual login. The code does not automate a password or bypass authentication.

The observed protected request used bearer authentication. Only that high-level mechanism is documented: no header, token, cookie, session ID, callback URL, password, or storage-state value is printed or saved outside `.auth/`. The retained market response is sanitized, and user-specific `fantasy_team` ownership is redacted. Tests use mocked/synthetic state and never load the real file. This follows Playwright's warning that [stored authentication state can impersonate the account](https://playwright.dev/python/docs/auth).

The project directory was not a Git work tree at audit time, so repository-local `git check-ignore` and `git status` could not be executed and there was no Git history into which the state could be committed. The exact project `.gitignore` was copied into an isolated temporary Git repository; `git check-ignore -v .auth/euroleague.json` matched the `.auth/` rule. When this project is initialized or placed in a real repository, both commands must be rerun in that repository before the first commit. The ignore rule is necessary protection, but it does not replace reviewing staged files.

Playwright is **not required for normal market collection after bootstrap**. `FantasyClient` recovered the existing session credential in memory from the private Playwright state and a normal `requests.Session` reproduced the current JSON request with HTTP 200. No credential value was persisted, logged, or placed in an artifact. Playwright remains necessary for manual login/session renewal and can remain a fallback for endpoint observation; the cleaner authenticated HTTP/JSON client is the recommended collector.

### Public configuration and authenticated endpoint

The official Flutter application uses the structured API base:

```text
https://fantaking-api.dunkest.com/api/v1
```

Anonymous `GET /leagues/10/config` returned current EuroLeague configuration: competition, schedule, player-list and matchday IDs; all configured teams and matchdays; roster constraints; formations; position definitions; and captain multiplier. The current classic configuration represented four Guards, four Forwards, two Centers, one Head Coach, five starters, five bench players, one coach, and a captain multiplier of `2`. Position IDs were 28 Guard, 29 Forward, 30 Center, and 31 Head Coach.

The authenticated player-market request was:

```text
GET /players-lists/30/matchdays/1000/players?per_page=-1&page=1
```

It returned HTTP 200 and 365 unique market IDs: 345 players and 20 Head Coaches across 20 teams. The market response contained these exact top-level record fields:

```text
id
first_name
last_name
quotation
is_on_fire
popularity
jersey
avg_pts
position
team
opponent
round
is_injured
probability_of_playing
started_from_bench
fantasy_team
label
face_path
```

Nested fields were:

```text
position: id, name
team: id, name, abbreviation, position
opponent: id, name, abbreviation
round: id, number
```

Here, `team.position` is home/away context, while `position` is the Fantasy roster category. `round.number` split the market into the two Fantasy Turns: 185 records in Turn 1 and 180 in Turn 2. Current quotations were numeric and non-null for all 365 records, ranging from 4.0 to 21.1 credits. Position counts were 150 Guards, 127 Forwards, 68 Centers, and 20 Head Coaches.

Current status values were also complete in this response: `is_injured` was true for 28 records; `probability_of_playing` was 0 for 28, 0.5 for 28, and 1 for 309; `started_from_bench` was true for 217 and false for 148. These counts describe the endpoint, not a validated medical taxonomy or pregame lineup forecast.

The market payload did **not** contain an official EuroLeague player/person ID, previous quotation, price delta, recent score array, explicit market-availability field, injury type/description, season code, competition code, response/snapshot timestamp, status-updated timestamp, or publication timestamp. Route context supplies player-list and matchday identity, and the collection process must supply `observed_at_utc`.

A separate frontend-supported request was tested for one legitimately discovered
Fantasy player ID:

```text
GET /players/{fantasy_player_id}/fantasy-pts?league=10&matchday={matchday_id}&lang=en
```

It returned HTTP 200 at both Matchday 38 and config-listed Matchday 1. The response
contained `fantasy_pts`, `label`, `matchday {id,number}`, `round {id,number}`, a
match object (`id`, status, start time, OT flag, home/away teams and scores), and
`stats_items`. Each non-zero component item contained `id`, `name`, raw `value`,
and its `fantasy_pts` contribution. Observed component names were `pts`, `reb`,
`ast`, `stl`, `tov`, `blka`, `fd`, `pf`, and `fg_missed`; zero-value components
were not demonstrated in this one-player sample. This proves limited same-season
official score detail, not full-pool or previous-season coverage.

Sanitized evidence:

- [current market field/value summary](../data/samples/fantasy_current/market_summary.json)
- [discovery manifest and bounded historical comparisons](../data/samples/fantasy_current/discovery_manifest.json)
- [sanitized current market response](../data/samples/fantasy_current/market_response.sanitized.json)
- [sanitized public league configuration](../data/samples/fantasy_current/league_config.sanitized.json)
- [sanitized current single-player Fantasy-points response](../data/samples/fantasy_current/player_fantasy_points_sample.sanitized.json)
- [sanitized Matchday 1 single-player Fantasy-points response](../data/samples/fantasy_current/player_fantasy_points_matchday_1.sanitized.json)

### Historical Fantasy price and position coverage

Classification: **B. Limited historical Fantasy prices appear available.**

Only matchday IDs supplied by the current public configuration were tested. All three bounded current-season historical requests returned HTTP 200 with the same field schema:

| Fantasy Round | Matchday ID | Records | Shared current IDs | Shared IDs with different `quotation` vs Round 38 | Position differences | Status/average/bench differences |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 963 | 307 | 307 | 295 | 0 | 0 |
| 19 | 981 | 348 | 348 | 283 | 0 | 0 |
| 37 | 999 | 365 | 365 | 237 | 0 | 0 |
| 38, current | 1000 | 365 | 365 | — | — | — |

The price and player-pool differences are strong evidence that same-season market state is retained at matchday granularity. Team IDs also differed for 10 shared Round 1 IDs and five shared Round 19 IDs, consistent with some historical membership state. Opponent and Turn objects changed as expected.

Position assignments were present in every response and did not differ for any shared ID. This proves that a position value is returned for old matchday URLs; it does **not** prove that the value is an immutable historical assignment, because other mutable-looking fields are overlaid. Position history is therefore available only with qualified semantics.

No previous-season competition/player-list index or frontend historical-season request was discovered, and no old IDs were guessed. Earliest tested coverage is the current season's Round 1; latest is Round 38. Historical state remains unverified across seasons, and the market response itself has no snapshot timestamp, previous-price field, or price-change formula. Every permitted pre-lock fetch should therefore be retained prospectively even if current-season backfill remains reachable.

### Player and team identity mapping

The Fantasy payload provides its own numeric `id` but no direct official EuroLeague player ID. Exact string overlap between Fantasy IDs and official player/person/external IDs was zero; nine numeric coincidences were diagnostic only and were deliberately ignored. The current mapping result is therefore useful but not fully reliable:

- 345 Fantasy player records evaluated; 20 Head Coaches excluded
- 279/345 accepted unique player matches: 278 exact name + mapped team + season, and one conservative normalized-name + mapped team + season
- 16 additional unique normalized-name-only candidates retained for human alias review, never auto-accepted
- 50 unmatched records and zero ambiguous exact candidates
- 19/20 Fantasy teams received a unique exact crosswalk to an official team code; one remained unmapped
- Fantasy team IDs and official team IDs had zero native-ID overlap

The safe fallback is a versioned, evidence-bearing crosswalk keyed by Fantasy ID, season, Fantasy team, and official person/team IDs. Exact team-scoped name matching can seed it, while aliases, transliterations, transfers, jersey numbers, and birth dates should be review evidence rather than hidden fuzzy joins. A name-only candidate must not become an automatic identity. See the [machine-readable identity audit](../data/samples/fantasy_current/identity_mapping_audit.json).

### Fantasy injury and availability history

The current structured market exposes `is_injured`, `probability_of_playing`, `is_on_fire`, and `started_from_bench`. It does not expose injury type, explanatory text, update time, status history, or a publication timestamp.

For every player shared with current Round 38, the values of `is_injured`, `probability_of_playing`, `started_from_bench`, and `avg_pts` were identical in Round 1, 19, and 37 responses. The endpoint therefore appears to combine a historical price/player pool with mutable current overlays. These fields must be classified as **current only** for leakage-safe work. They cannot establish what was known before a historical deadline, and no historical injury/status availability with timestamps was found.

The last point is material because the [suspended/changed-game rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/suspended-or-changed-games) can award average scores in circumstances not reconstructable from a normal box score. Official Fantasy points should remain separate from any recomputed basketball score.

### Official scoring rules

The current [player scoring rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/players-scoring.md) award:

- +1 for each point, rebound, assist, steal, block made, and foul drawn
- −1 for each turnover, block suffered, foul committed, missed field goal, and missed free throw
- a team-win bonus of 10% of that round's player Fantasy score
- regular-time and overtime statistics

The [coach scoring rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/coach-scoring.md) depend on win/loss margin and treat overtime separately. The [quotation rules](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/quotations-and-price-variations) say values change after rounds using score and starting value, but do not publish a reproducible formula.

## Recommended future core dataset

One immutable `player_game` outcome row should represent one listed player in one game, including DNPs. This is a recommendation, not a final database schema.

```text
# Source identity and provenance
competition_code
season_code
game_code                  # season-scoped source code
game_id                    # v2 UUID
game_identifier
source_player_id_raw       # e.g. padded legacy P-code
person_code                # stable-looking v2/v3 code
source_url
raw_snapshot_id
fetched_at_utc

# Game context
phase_code
round
scheduled_utc
actual_tip_utc             # if distinguishable later
played
home_team_id
away_team_id
team_id
opponent_id
home_away
venue_id
overtime_periods

# Player context at that game
player_name_raw
jersey_number
starter
did_not_play
minutes_raw
minutes

# Observed outcome
points
two_pointers_made
two_pointers_attempted
three_pointers_made
three_pointers_attempted
free_throws_made
free_throws_attempted
offensive_rebounds
defensive_rebounds
total_rebounds
assists
steals
turnovers
blocks
blocks_received
fouls_committed
fouls_drawn
pir
plus_minus
```

Do not make price a timeless player attribute. A separate point-in-time `fantasy_player_round_snapshot` concept should later hold Fantasy player ID, Fantasy competition/list/matchday, position, eligibility, credits, injury/availability flags, official Fantasy points, fetch time, and the pre-lock cutoff. It can then be joined to `player_game` without contaminating immutable basketball outcomes. This is still a conceptual recommendation, not Phase 2 schema work.

## Data already available reliably enough for the next decision

- game schedules, timestamps, rounds, home/away clubs, scores, venues, and officials
- full traditional player box scores including minutes, starter, PIR, blocks received, fouls drawn, and plus-minus
- equivalent team totals and team rebounds
- event-level play-by-play with substitution events and retained source order
- field-goal shot locations and contextual shot flags
- historical/current roster and biographical metadata with stable-looking person codes
- current official Fantasy rules, public matchday/configuration metadata, and roster constraints
- an authenticated 365-record current Fantasy market with player/coach IDs, positions, prices, teams, opponents, Turns, and current status fields
- distinct same-season Fantasy price/player-pool states for tested Rounds 1, 19, 37, and 38
- limited official Fantasy score/component detail for one player at Matchdays 1 and 38
- a safe manual-login bootstrap and reusable authenticated JSON client
- a conservative identity audit with 279 accepted player mappings and 19/20 team crosswalks
- structurally valid five-player lineup reconstruction in four audited games, subject to explicit minute reconciliation
- raw-response provenance and schema inventories for the retained sample

“Reliably enough” here means suitable for a full-ingestion validation design, not guaranteed production stability.

## Missing or not yet reliable

- previous-season Fantasy market/price coverage and a discoverable season/list index
- historical Fantasy position semantics proven to be point-in-time rather than current overlays
- a reproducible official price-change formula
- full-pool/previous-season official Fantasy-score coverage and systematic reconciliation to scoring rules
- historical injury/availability snapshots with publication or observation timestamps
- expected lineups and expected minutes
- point-in-time roster/role snapshots proven not to be retrospectively corrected
- direct Fantasy-to-official player IDs and reviewed mappings for 66 unresolved/name-only player records
- exact player-level rotation stints for every game; one audited four-OT game had four ±60-second feed conflicts
- complete shot attempts in one feed, because missed free throws are absent from Points
- exhaustive every-game historical integrity and schema-drift results

## Feature-source feasibility conclusion

The full classification, raw inputs, historical feasibility, and leakage risk are recorded in the [feature-source matrix](feature_source_matrix.md). The main conclusions are:

### Directly available

- game, team, opponent, UTC time, nominal round, home/away, final score, game duration/overtime
- player minutes, starter result, points, shot makes/attempts, rebounds, assists, steals, blocks, turnovers, fouls, PIR, and plus-minus
- equivalent team totals, substitutions/PBP, and field-goal coordinates
- current Fantasy ID, price, position, team/opponent/Turn, current injury/probability/bench flags, and market player pool

Target-game minutes and starter status are outcomes, not pregame features. Current Fantasy status fields are direct but are not historically safe.

### Derivable from existing official raw data

- recomputed Fantasy points, subject to validation against official scoring adjustments; FP/minute; strictly lagged rolling form, trends, consistency, and variance
- FG%, 2P%, 3P%, FT%, eFG%, TS%, shooting volume, player usage/assist/turnover/rebound proxies, starter frequency, recent minutes, and overtime workload
- estimated possessions, 40-minute pace, offensive/defensive ratings, team eFG%/TS%, turnover/rebound/free-throw/assist rates, 2PA/3PA rates, and points/rebounds/assists allowed
- shot-location and rim/mid-range/three-point tendencies from PBP plus Points, with documented coordinate and missed-FT caveats
- home/away, EuroLeague-only rest and schedule density, opponent form, and opponent Fantasy performance allowed where a historical Fantasy score/position is valid
- approximate lineup/on-off, teammate-absence, and same-lineup context from substitutions, only after per-game reconciliation and boundary flags

Every rolling or aggregate feature must be recomputed from games known before the cutoff. Full-season aggregate API values are not valid historical inputs.

### Genuinely external or currently unknown

Priority order is based on unique information and likely first-model value:

1. **Timestamped injuries/news and expected roles — highest priority and core.** Official [EuroLeague injury reports](https://www.euroleaguebasketball.net/en/euroleague/news/injury-report-round-26-tae2425/), club releases, and coach reports can add OUT/questionable/return/minutes-restriction context, but publication and revision history must be captured prospectively or validated from an archive.
2. **Domestic-league workload — high-value later input.** EuroLeague-only rest misses intervening domestic/cup minutes. [Sportradar Global Basketball](https://developer.sportradar.com/basketball/reference/global-basketball-overview) and [Genius Sports Basketball Warehouse](https://developer.geniussports.com/warehouse/rest/index_basketball.html) are the broadest structured candidates, with commercial/licensing and limited-history constraints. League-specific official sources should be preferred where feasible.
3. **Pregame betting markets — useful later enrichment.** Spread, total, and moneyline add a timestamped external scoring/strength/closeness estimate. [The Odds API historical service](https://the-odds-api.com/historical-odds-data/) is the clearest self-service candidate; the snapshot chosen must precede the Fantasy deadline.
4. **Tactical/tracking data — defer.** Pick-and-roll, transition, isolation, post-up, touches, time of possession, screens, and defensive assignments are not reliably recoverable from normal PBP. [Sportradar Synergy](https://developer.sportradar.com/basketball/reference/synergy-basketball-overview) is a structured commercial candidate, but is not required for a strong baseline.

Actual travel itineraries, confirmed pregame lineups, and reliable expected minutes also remain external/unknown. Venue/time data can support approximate travel distance, but not actual team movement.

## Leakage-safe backtesting requirements

Raw game-level data have enough timestamps and identifiers to reconstruct historical state, but only if the later pipeline enforces an explicit as-of boundary.

For a Round N roster prediction:

```text
feature cutoff = Round N Fantasy lock time
eligible basketball events = games completed before that cutoff
eligible market/injury data = snapshots fetched before that cutoff
target = Round N outcomes, joined only after prediction
```

Required safeguards:

1. Sort and filter by UTC time, not round number alone. Rescheduled games can violate round order.
2. If the decision is made before a whole Fantasy round, do not use earlier games from that same round even if training is reconstructed later.
3. Never use full-season aggregates, “last five” values fetched after the target game, final standings, or later-corrected leader tables as historical features.
4. Store the retrieval time and source of schedules, rosters, injuries, prices, and eligibility. A historical URL is not proof of an as-of snapshot: the Fantasy old-round responses returned current status/average/bench overlays.
5. Compute rolling statistics from raw prior games with closed-left windows: the target game must be excluded.
6. Treat postponed/cancelled games explicitly. Do not silently inner-join them away.
7. Preserve DNP rows and distinguish “listed but did not play,” “not rostered,” and “missing feed.”
8. Keep official Fantasy points as a separate target because rule-based adjustments may differ from recomputed box-score scoring.

## Phase 2 architecture readiness

There is now enough information to design a **conservative Phase 2 database architecture**, but not to launch full historical training/backtesting. The design can safely account for:

- immutable raw basketball and Fantasy artifacts with content hashes and retrieval times
- normalized game/player/team/PBP/shot observations linked to their source identifiers
- provider-namespaced Fantasy IDs and an evidence/confidence-bearing temporal identity crosswalk
- a separate time-varying Fantasy market observation for price, position, eligibility, opponent/Turn, and status
- distinct `effective_round`, `observed_at_utc`, source publication time, and prediction cutoff semantics
- reconciliation/quarantine results for conflicting source fields and reconstructed rotations
- versioned Fantasy rules and a separate official-versus-recomputed Fantasy outcome

That is architecture readiness, not a final schema decision. Full multi-season roster backtests must wait for either a legitimate historical market archive or prospectively accumulated snapshots, and historical availability cannot be inferred from the current-overlaid fields.

The reference comparison supports a strict lineage of immutable raw input → normalized observation → as-of view → leakage-safe features → projection artifact → legal decision → realized outcome. See [reference-project lessons](reference_project_lessons.md). It does not justify proceeding to prediction or optimization in this phase.

## Biggest remaining data risks

The three largest data risks before implementing Phase 2 are:

| Rank | Risk | Evidence and consequence | Required control |
| ---: | --- | --- | --- |
| 1 | No demonstrated previous-season Fantasy price/eligibility archive | Only current-season Rounds 1/19/37/38 were verified; a multi-season price-aware backtest may be impossible or biased | Seek a legitimate archive, preserve every new pre-lock market snapshot, and represent coverage explicitly rather than imputing today's price backward |
| 2 | No timestamped historical availability/injury state | Old-round market URLs repeated current `is_injured`, probability, bench, and average values; using them would leak future/current knowledge | Add immutable, timestamped Fantasy/news observations and enforce publication/observation-time joins |
| 3 | Incomplete cross-provider identity and mutable source semantics | No direct official player ID; 279/345 automatic matches, 16 review candidates, 50 unmatched, plus one unmapped team; roster/status endpoints may be retrospectively revised | Use provider-scoped IDs, temporal crosswalk evidence, manual alias review, and never silently fuzzy-match or overwrite old observations |

Other material controls remain: resolve data-use/licensing before scaling; preserve and test undocumented schemas; derive the winner from scores; retain postponed/annulled games; audit historical completeness; and quarantine PBP games whose reconstructed individual minutes conflict with box scores.

The recommended next decision is whether Phase 2 should initially support (a) a basketball-only historical feature store plus prospective Fantasy snapshots, or (b) a shorter current-season Fantasy-aware backtest. That choice should be made explicitly; Phase 1.5 does not justify pretending that missing historical prices or status timestamps exist.
