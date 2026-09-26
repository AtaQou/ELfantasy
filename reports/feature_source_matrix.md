# EuroLeague Fantasy AI — Feature Source Matrix

Audit date: 2026-08-11  
Scope: Phase 1.5 feasibility only. No feature pipeline, model, optimizer, or production schema is defined here.

## Decision summary

The official EuroLeague game feeds are sufficient for the core statistical feature set. Traditional player/team box scores, schedules, game times, opponents, starters, minutes, PIR, plus-minus, play-by-play, and shot coordinates support leakage-safe rolling form, efficiency, workload, pace, ratings, and shot-profile features. The representative audit demonstrated traditional game statistics through E2000; the detailed legacy play-by-play and shot feeds begin at E2007 in the tested boundary cases.

The authenticated Fantasy market adds a current 365-row market and limited
current-season price history: matchdays 1, 19, and 37 returned different prices
and player pools. It does not expose a previous-season archive. More importantly,
injury/probability, bench, and average fields were identical for every shared
player across those old-matchday responses and carry no timestamps, so they are
current overlays rather than safe historical observations. The genuinely new
remaining information is mostly outside completed EuroLeague games: point-in-time
availability/news, domestic-league workload, pregame betting expectations, and
proprietary tactical/tracking labels.

## Classification and leakage rules

The `Class` column uses the requested categories:

1. **Directly available** — a source field exists; normalization may still be required.
2. **Derivable from existing raw data** — calculable from official EuroLeague schedule, game, box-score, play-by-play, or shot data already identified.
3. **Requires an external source** — completed EuroLeague game data cannot supply the information.
4. **Currently unknown** — the relevant structured route or historical/as-of semantics have not been established.

Historical coverage below means *demonstrated representative endpoint availability*, not a complete game-by-game integrity guarantee. `E2000+` refers to official schedule/roster/v3 game-stat probes; `E2007+` refers to the successful detailed legacy box-score, play-by-play, and shot probes.

Leakage risk means the risk of failing this test:

> Could this exact value be reconstructed as it was known immediately before the historical prediction cutoff?

`Low` still requires excluding the target game. `Medium` requires an explicit as-of window. `High` or `Very high` requires immutable snapshots/publication times as well as an as-of join. Full-season aggregate API responses must never be used for an earlier round, even when the metric itself is valid.

## Identity, targets, and Fantasy market

| Feature | Class | Source or required raw inputs | Historical feasibility | Leakage risk | Priority |
| --- | --- | --- | --- | --- | --- |
| Game/round identity | 1. Directly available | competition/season codes, game UUID/code, round, UTC tip time | E2000+ | Low | Core |
| Player identity | 1. Directly available | v2/v3 person code; retain legacy `Player_ID` too | E2000+; cross-season stability still needs a full audit | Medium | Core |
| Team/opponent identity | 1. Directly available | team codes, home/road clubs, season membership | E2000+ | Low | Core |
| Basketball position | 1. Directly available | roster/membership position code/name | E2000+; may reflect retrospective corrections | Medium | Core |
| Fantasy player ID | 1. Directly available, authenticated | Fantasy player-market `id` | Current + tested same-season matchdays 1/19/37; 307→348→365 rows | High | Core |
| Fantasy position assignment | 1/4. Direct, historical semantics partly unknown | position `{id,name}` in matchday market | Returned for matchdays 1/19/37, with 0 changes among shared IDs; old seasons and whether assignments are as-of remain unproven | High | Core |
| Fantasy price/credits | 1. Direct authenticated, limited history | `quotation` plus players-list/matchday IDs and capture time | Distinct values proved for same-season matchdays 1/19/37/38; no previous-season index | Very high | Core |
| Price movement | 2. Derivable for covered rounds | difference between two matchday `quotation` snapshots | Same-season tested; no formula or previous-season archive | Very high | Core |
| Fantasy average (`avg_pts`) | 1. Direct current only | authenticated market | Identical for every shared ID in matchdays 1/19/37 versus 38; not historical as-of | Very high | Core |
| Fantasy bench designation | 1. Direct current only | `started_from_bench` | Identical for every shared ID in old-matchday probes; meaning/timing has no timestamp | Very high | Later |
| Official Fantasy score | 1. Direct authenticated, limited test | one-player `/fantasy-pts` response with round total and component items | Same player succeeded for matchdays 1 and 38; full-player/previous-season coverage untested | High | Core |
| Recomputed Fantasy score | 2. Derivable, subject to validation | official scoring rules plus player box score, game outcome, bonuses/penalties | E2000+ for basketball inputs; exact equivalence not proven | Medium | Core |
| Fantasy value | 2. Derivable | pre-deadline price and lagged/projected Fantasy points; never final score divided by its own pregame price as an input | Only where price snapshots exist | Very high | Core |
| Availability/injury flag | 1. Direct current only | `is_injured`, `probability_of_playing` | Not historical as-of: all shared values stayed identical in old-matchday probes and no timestamps exist | Very high | Core |

## Player-level feature feasibility

| Feature | Class | Source or required raw inputs | Historical feasibility | Leakage risk | Priority |
| --- | --- | --- | --- | --- | --- |
| Minutes | 1. Directly available | legacy `Minutes` or v3 `timePlayed`; normalize `DNP` | E2000+ via v3; E2007+ cross-check via legacy | Low as an outcome; target-game minutes cannot be an input | Core |
| Points, rebounds, assists, steals, blocks, turnovers | 1. Directly available | player game box score | E2000+ | Low as outcomes; use only completed prior games | Core |
| 2PM/2PA, 3PM/3PA, FTM/FTA | 1. Directly available | player game box score | E2000+ | Low as outcomes | Core |
| PIR | 1. Directly available | `Valuation`/v3 equivalent | E2000+ | Low as an outcome | Core |
| Plus-minus | 1. Directly available | player `Plusminus` | E2000+; quality audit still needed at scale | Low as an outcome | Core |
| Starter indicator | 1. Directly available | `IsStarter`/v3 equivalent | E2000+ | Low as an outcome; not a confirmed pregame lineup | Core |
| Raw Fantasy points | 1/2. Official sample + recomputable | `/fantasy-pts` or scoring rules + box score | One current-season player proved at matchdays 1/38; estimated version E2000+ | High until archive/equivalence is proven | Core |
| FP/minute | 2. Derivable | lagged Fantasy points / minutes; define DNP and low-minute policy | Same as Fantasy-point source | Medium | Core |
| Rolling Fantasy points | 2. Derivable | player-game Fantasy points ordered by actual UTC tip time | Same as Fantasy-point source | High | Core |
| Rolling PIR | 2. Derivable | lagged PIR | E2000+ | Medium | Core |
| Rolling minutes and recent workload | 2. Derivable | lagged minutes, actual game times, overtime duration | E2000+ | Medium | Core |
| Minutes trend | 2. Derivable | lagged minutes and a declared window/weighting rule | E2000+ | Medium | Core |
| Points/rebounds/assists trends | 2. Derivable | corresponding lagged player-game totals | E2000+ | Medium | Core |
| Steals/blocks/turnovers trends | 2. Derivable | corresponding lagged player-game totals | E2000+ | Medium | Core |
| Shooting volume | 2. Derivable | 2PA + 3PA; optionally attempts/minute or per possession | E2000+ | Medium | Core |
| 2PA/3PA/FTA volume | 1/2. Direct per game; derived rolling/rate | attempts and lagged minutes/possessions | E2000+ | Medium | Core |
| FG%, 2P%, 3P%, FT% | 2. Derivable | makes and attempts; aggregate as sums, not mean of game percentages | E2000+ | Medium | Core |
| Effective FG% | 2. Derivable | `(2PM + 3PM + 0.5 × 3PM) / (2PA + 3PA)` | E2000+ | Medium | Core |
| True shooting % | 2. Derivable | `PTS / (2 × (FGA + 0.44 × FTA))`; 0.44 must be validated for EuroLeague semantics | E2000+ | Medium | Core |
| Assist rate/proxy | 2. Derivable | basic proxy: AST/team FGM; stronger version needs minutes and reconstructed on-court teammate FGM | Basic E2000+; lineup version E2007+ with caveats | Medium | Core |
| Turnover rate/proxy | 2. Derivable | `TO / (FGA + 0.44 × FTA + TO)` or possessions used | E2000+ | Medium | Core |
| Rebound rate/proxy | 2. Derivable | player rebounds and both teams' rebound opportunities; on-court rate needs lineups | Game-level E2000+; on-court version E2007+ with caveats | Medium | Core |
| Offensive rebound rate | 2. Derivable | player ORB, team ORB, opponent DRB; on-court opportunity denominator preferred | E2000+ basic | Medium | Core |
| Defensive rebound rate | 2. Derivable | player DRB, team DRB, opponent ORB; on-court opportunity denominator preferred | E2000+ basic | Medium | Core |
| Usage rate/best feasible proxy | 2. Derivable | player FGA, FTA, TO, minutes and team totals/minutes; exact possession usage is not directly tagged | E2000+ proxy | Medium | Core |
| Consistency/variance | 2. Derivable | lagged game-level Fantasy points or basketball metric, minimum sample and window | Same as input metric | High for Fantasy score; otherwise Medium | Core |
| Rolling variance | 2. Derivable | strictly lagged observations inside a declared window | Same as input metric | High for Fantasy score; otherwise Medium | Core |
| Starter frequency | 2. Derivable | lagged starter indicators; actual game chronology | E2000+ | Medium | Core |
| Shot-location profile | 2. Derivable | Points coordinates/zone/action + PBP result/player/event link | E2007+; Points omits missed free throws | Medium | Core |
| Rim/mid-range/three tendency | 2. Derivable, partly approximate | field-goal coordinates, action type, court geometry/zone definitions | E2007+ | Medium; coordinate interpretation must be versioned | Core |
| Recent overtime workload | 2. Derivable | game duration/extra periods plus player minutes | E2000+ from game metadata/stats; richer validation E2007+ | Medium | Core |
| Role change with teammate off court | 2. Derivable with caveats | ordered substitutions/PBP, player stats/events, reconstructed lineups | Candidate E2007+; exact in 3 E2025 regulation games, but 4/24 rows differed by ±60 seconds in one four-OT game | High | Later |
| Touches/time of possession | 3. Requires external source | optical/tracking or provider-tagged possession data | Not in official feeds | High | Later |

### Player-rate cautions

- Use aggregate numerators and denominators over the lagged window; averaging game percentages biases low-volume games.
- `0.44 × FTA` is a conventional possession approximation, not a verified EuroLeague constant. Validate it against play-by-play possessions before production use.
- Game-level rebound and assist rates are feasible now. True on-court opportunity rates depend on a reliable lineup reconstruction.
- A player appearing in the postgame starting five does not prove that the start was known before the Fantasy deadline. Pregame confirmed lineups are a separate, timestamped source.

## Team and opponent feature feasibility

| Feature | Class | Source or required raw inputs | Historical feasibility | Leakage risk | Priority |
| --- | --- | --- | --- | --- | --- |
| Team points and points allowed | 1. Directly available | final team/opponent totals | E2000+ | Low as outcomes; rolling window required | Core |
| Estimated possessions | 2. Derivable | `FGA + 0.44 × FTA - ORB + TO`; compare both teams and validate with PBP | E2000+; PBP validation E2007+ | Medium | Core |
| Pace | 2. Derivable | estimated possessions and actual game minutes, normalized to 40 minutes | E2000+ | Medium | Core |
| Offensive rating | 2. Derivable | points / estimated possessions × 100 | E2000+ | Medium | Core |
| Defensive rating | 2. Derivable | opponent points / opponent or averaged game possessions × 100 | E2000+ | Medium | Core |
| Team eFG% | 2. Derivable | team FGM, 3PM, FGA | E2000+ | Medium | Core |
| Team TS% | 2. Derivable | team points, FGA, FTA | E2000+ | Medium | Core |
| Turnover rate | 2. Derivable | team TO and estimated possessions/plays used | E2000+ | Medium | Core |
| Offensive rebound rate | 2. Derivable | team ORB / (team ORB + opponent DRB) | E2000+ | Medium | Core |
| Defensive rebound rate | 2. Derivable | team DRB / (team DRB + opponent ORB) | E2000+ | Medium | Core |
| Free-throw rate | 2. Derivable | FTA/FGA or FTM/FGA; choose and version one definition | E2000+ | Medium | Core |
| Assist rate | 2. Derivable | assists/team FGM or assists/estimated possessions | E2000+ | Medium | Core |
| Three-point attempt rate | 2. Derivable | 3PA/FGA | E2000+ | Medium | Core |
| Two-point attempt rate | 2. Derivable | 2PA/FGA | E2000+ | Medium | Core |
| Paint/perimeter tendencies | 2. Derivable, partial | shot coordinates/zones, makes/misses, action type | E2007+ | Medium; zone geometry/schema drift | Core |
| Rebounds/assists allowed | 2. Derivable | opponent player/team totals grouped against the defense | E2000+ | Medium | Core |
| Turnovers forced | 2. Derivable | opponent turnovers; steals can be retained separately | E2000+ | Medium | Core |
| Blocks and blocks allowed | 1/2. Direct per game; derived rolling | blocks made/received | E2000+ | Medium | Core |
| Opponent shot profile | 2. Derivable | opposing shot coordinates/actions/results | E2007+ | Medium | Core |
| Opponent Fantasy performance allowed | 2. Derivable | opponent player Fantasy scores, game/team identity | Depends on official/recomputed Fantasy history | High | Core |
| Opponent performance by Fantasy position | 2/4. Derivable if historical assignments exist | player-game outcome + point-in-time Fantasy position | Historical assignment coverage unknown | Very high | Later |
| Opponent performance by basketball archetype | 2. Derivable | lagged shot/box profiles and an archetype definition fitted only on prior data | E2007+ for shot archetypes | High | Later |
| Exact play-type frequencies | 3. Requires external source | tagged possessions (P&R, transition, isolation, post-up, spot-up, handoff) | Not in normal official feeds | High | Later |

All rolling team/opponent metrics should be computed from immutable completed games with `game_utc < prediction_cutoff`. Round number alone is insufficient because postponed games can be played out of nominal order.

## Contextual feature feasibility

| Feature | Class | Source or required raw inputs | Historical feasibility | Leakage risk | Priority |
| --- | --- | --- | --- | --- | --- |
| Home/away | 1. Directly available | home and road club IDs from schedule/game | E2000+ | Low | Core |
| Days of EuroLeague rest | 2. Derivable | actual UTC tip times for a team/player's previous played game | E2000+ | Medium | Core |
| Recent EuroLeague schedule density | 2. Derivable | count/gap of played games before cutoff | E2000+ | Medium | Core |
| EuroLeague-only back-to-back-like workload | 2. Derivable | prior game timestamps and minutes | E2000+ | Medium | Core |
| True all-competition workload | 3. Requires external source | domestic/cup schedules and player minutes joined to EuroLeague identities | Provider-dependent | High | Later, high value |
| Overtime workload | 2. Derivable | extra periods/game duration and minutes | E2000+ | Medium | Core |
| Recent player minutes | 2. Derivable | lagged player minutes over time/window | E2000+ | Medium | Core |
| Recent team minutes/workload | 2. Derivable | player/team minutes and game duration | E2000+ | Medium | Core |
| Teammate availability | 3. External for dependable history; Fantasy is current-only context | timestamped injury/news/registration/lineup snapshots | Fantasy old-round routes returned current overlays, not an as-of archive | Very high | Core |
| Opponent availability | 3. External for dependable history; Fantasy is current-only context | same as teammate availability | Fantasy old-round routes returned current overlays, not an as-of archive | Very high | Core |
| Confirmed/projected starters | 3. Requires external source | timestamped lineup/club/beat report; postgame `IsStarter` is an outcome | No historical source established | Very high | Core |
| Travel distance/time zones | 3. Requires external source, then derivable | venue/city plus stable geocodes, distance and time-zone/calendar rules; actual itinerary is normally unavailable | Venue names E2000+, but complete historical travel context unverified | High | Later |
| Round and game timing | 1. Directly available | round plus UTC/local scheduled tip time | E2000+ | Low/Medium because schedules can be revised | Core |
| Early/late Fantasy Turn | 1. Direct for tested current season | market `round {id,number}`, matchday config, and schedule | Matchdays 1/19/37/38 returned Turn 1/2 IDs; previous seasons untested | High | Core |
| Five-player lineup/on-off | 2. Derivable with caveats | PBP substitutions, starters, clocks, API source order, period boundaries | Candidate E2007+; 92/96 rows exact across 3 regulation + 1 four-OT game; OT team totals exact but 4 individual rows differed by 60 seconds | High | Later |
| Exact possession-event lineup | 2/4. Derivable except ambiguous boundaries; global reliability unknown | same inputs plus transaction grouping and before/after lineup states | Four-game audit found 3 delayed same-clock actor events; older-era reliability remains untested | High | Later |

The reproducible [rotation reconstruction audit](rotation_reconstruction_audit.md)
classifies this as **POSSIBLE WITH CAVEATS**. It matched all 72 regulation-game
player rows exactly. The four-overtime fixture had structurally valid transactions
and exact team totals, but four of 24 player rows differed from the official box
score by exactly 60 seconds; those games must be reconciled or quarantined.

## External-source requirement inventory

### A. Domestic-league workload

Domestic minutes are likely to add meaningful information that EuroLeague-only rest cannot recover. They distinguish, for example, five calendar days without a EuroLeague game from a player who logged heavy domestic minutes two days before the next EuroLeague game. Minimum useful granularity is `competition, game_id, UTC tip, player_id, team_id, minutes, starter/DNP`, with source IDs and mapping evidence preserved.

| Candidate | Likely data and granularity | Historical depth / IDs | Access and licensing | Assessment |
| --- | --- | --- | --- | --- |
| [Sportradar Global Basketball v2](https://developer.sportradar.com/basketball/reference/global-basketball-overview) | Schedules, results, lineups/rosters, game-level team/player stats and timelines where coverage supports them; over 200 competitions advertised | Stable `sr:*` entity IDs and mapping feeds; standard competition endpoints expose at most current plus two prior seasons | Authenticated commercial product; competition/field coverage must be checked in its coverage matrix | Broadest single integration candidate, but three-season standard depth is a serious backtest limit |
| [Genius Sports Basketball Warehouse](https://developer.geniussports.com/warehouse/rest/index_basketball.html) | Matches, people, teams, match statistics/actions depending on entitlement | Integer IDs and optional external IDs; historical entitlement not public | Contract/entitlement dependent | Strong candidate because Genius supplies several European leagues; verify exact leagues, minutes, history, and reuse rights |
| [FIBA GDAP](https://gdap-portal.fiba.basketball/documentation) | Competition, pre/postgame and validated accumulated data; live data where subscribed | FIBA competition-system identifiers; depth is product/competition specific | Authentication and a product subscription are required | Useful for FIBA-run competitions, not a universal domestic-league solution |
| [easyCredit BBL developer API](https://developers.easycredit-bbl.de/) | Official German league schedule/statistics/PBP surface | IDs and oldest retrievable season need a direct feasibility test | API-key/terms review required | Promising league-specific official source; narrower but potentially higher provenance |
| [ACB Live / Genius Sports partnership](https://acb.com/es/liga/noticias/acuerdo-acb-genius-sports-para-la-explotacion-de-los-datos-estadisticos-110152) | ACB Live exposes rich game/PBP/shot/lineup views | Public self-service historical API not established | Access appears partner/club/provider mediated | High-value Spanish coverage, but not yet a dependable programmatic source |
| [ABA League / Genius Sports partnership](https://www.aba-liga.com/news/38440/adriatic-basketball-association-forms-exclusive-data-partnership-with-genius-sports/) | Official statistics infrastructure is provider-backed | Public endpoint and historical depth unverified | Likely requires provider or league permission | Relevant to several EuroLeague clubs; feasibility remains unknown |

Recommendation: treat domestic workload as a high-value Phase 2+ enhancement, not a blocker for the first EuroLeague-only baseline. Pilot one season and a few clubs before buying or normalizing multiple leagues.

### B. Injury, availability, and role news

The official [EuroLeague round injury reports](https://www.euroleaguebasketball.net/en/euroleague/news/injury-report-round-26-tae2425/) consolidate club-reported absences and contextual notes, so they are the best first-party textual source discovered. The remaining problem is temporal integrity: an archived article is not automatically an immutable history of every edit or the exact state before each game deadline.

| Candidate | Unique value | Historical/timestamp assessment | Main limitation |
| --- | --- | --- | --- |
| EuroLeague round injury reports | Club-reported OUT/uncertain/return context and previous absences | Archived round pages exist; publication/revision history must be captured and tested | Free text, incomplete club submissions, and no proven immutable edit history |
| Official club releases and coach press conferences | Minutes restrictions, return to practice, rotation intent | Often publication-timestamped, but fragmented by language/platform | Entity resolution, revisions/deletions, and archive completeness |
| [RotoWire EuroLeague injury pages](https://www.rotowire.com/euro/injury-report.php?pos=G) | Structured current EuroLeague injury view and editorial context | EuroLeague history/API coverage is not documented in the public [API guide](https://rotowire.readme.io/docs/quick-start), which lists NBA/WNBA/CBB basketball APIs | Commercial; historical snapshots and EuroLeague API entitlement require confirmation |
| Timestamped news provider/archive | Searchable publication records across clubs and local media | Can retain `published_at`, but publication time is not necessarily first-known time and edits may overwrite content | Noisy, multilingual, licensing limits, and expensive historical depth |

Store every future availability observation with `observed_at`, source publication time, effective game/round, raw status text, normalized status, and retrieval provenance. Never backfill a later confirmed OUT status into an earlier snapshot.

### C. Betting-market information

Pregame spread, total, and moneyline provide a genuinely external consensus estimate of scoring environment, strength, closeness, and possible blowout risk. The Odds API explicitly lists EuroLeague as `basketball_euroleague`; its [historical API](https://the-odds-api.com/historical-odds-data/) lists EuroLeague snapshots from 24 September 2020 (10-minute intervals, then 5-minute intervals from September 2022) on paid plans. That makes it the cleanest documented pilot candidate.

| Candidate | Historical availability | Fields / IDs | Assessment |
| --- | --- | --- | --- |
| [The Odds API](https://the-odds-api.com/sports-odds-data/sports-apis.html) | EuroLeague snapshots listed from 24 September 2020; paid history | event ID, commence time, bookmaker update/snapshot times, moneyline, spread, total | Best documented self-service option; verify EuroLeague bookmaker continuity and team-name mapping |
| [Betfair Historical Data](https://support.developer.betfair.com/hc/en-us/articles/360002407732-What-data-is-provided-by-the-Historical-Data-service) | Exchange stream archives are available for historical periods; exact EuroLeague market continuity must be audited | market/selection IDs and timestamped exchange changes | Rich market microhistory but heavier normalization and uncertain competition continuity |
| [Sportradar Odds](https://developer.sportradar.com/odds/reference/intro) | Contract/product dependent | provider event IDs, pre-match markets and updates | Enterprise option that may simplify cross-provider IDs if Global Basketball is also licensed |

For leakage safety, select the last snapshot at or before a declared prediction cutoff, retain bookmaker update times, and never use closing odds recorded after the Fantasy deadline. Betting data should remain a later comparison/enrichment feature, not a dependency of the baseline.

### D. Tactical and tracking information

Normal box scores, PBP, and coordinates cannot reliably identify pick-and-roll handlers/rollers, isolations, post-ups, spot-ups, handoffs, transition possession types, touches, time of possession, screens, or primary defensive matchups.

The clearest structured candidate is [Sportradar Synergy Basketball](https://developer.sportradar.com/basketball/reference/synergy-basketball-overview): its provider-tagged possession data include play types, lineups, shot coordinates, and offensive/defensive player roles. It is proprietary, typically postgame rather than pregame, and should be evaluated only after baseline results justify the cost. Public-facing products such as [Hudl Instat](https://instat.hudl.com/products/instat) may offer scouting/video analytics, but no suitable public programmatic contract was established in this audit.

Classification: **3. Requires an external source; Later priority.** These data may improve archetype and matchup models, but they are not required to build a strong first version from official EuroLeague data.

## Leakage-safe construction requirements

Every future feature row should have both an event key and a knowledge-time boundary:

```text
prediction_cutoff_utc
source_observed_at_utc
source_published_at_utc (when applicable)
source_game_utc / effective_round
```

The construction rule is:

```text
include source record only if its knowable timestamp <= prediction_cutoff_utc
and exclude all target-game outcomes
```

Specific controls:

1. Order by actual UTC tip/completion time, not nominal round number.
2. Build rolling features with an explicit one-game lag or `game_utc < cutoff` predicate.
3. Store raw snapshots of mutable prices, statuses, schedules, rosters, news, and odds; do not overwrite them with the latest state.
4. Do not use full-season advanced-stat endpoints for historical early-round rows. Recompute aggregates from prior games.
5. Treat postgame starter/minutes/availability outcomes as labels or past observations, never as pregame confirmations for that same game.
6. Fit archetype clusters, scalers, imputation values, and any opponent-position groupings only on the training window.
7. Record postponed/annulled games and changed deadlines explicitly; `round=N` does not establish what was known at the Round N deadline.

## Phase recommendation

There is now enough source evidence to design a conservative Phase 2 architecture
around immutable games, player/team outcomes, PBP, shots, roster memberships,
provider-namespaced identity, Fantasy market observations, and explicit
`observed_at`/effective-round timestamps. Price and player-pool history are genuine
for tested current-season matchdays; position history is only partially verified,
and Fantasy availability is demonstrably unsafe as historical as-of input.

This is architectural readiness, not backtest readiness. A multi-season Fantasy
price archive and timestamped availability history are still missing. Domestic
workload and injury/news are the highest-value external additions; betting
snapshots are later enrichment, and tactical/tracking feeds should be deferred.
