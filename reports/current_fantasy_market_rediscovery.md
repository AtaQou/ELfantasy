# Current-season Fantasy market rediscovery (E2026)

## Outcome

The current official EuroLeague Fantasy Challenge source was rediscovered and
the E2026 market was appended to the existing canonical snapshot schema on
2026-09-02. No model, frozen feature definition, prediction, or optimizer code
was changed.

## Official source and authentication

The current Flutter web application still declares
`https://fantaking-api.dunkest.com/api/v1` as its API base and constructs the
market route as `/players-lists/{players_list_id}/matchdays/{matchday_id}/players`.

The current scope is available from the public official bootstrap:

```text
GET https://fantaking-api.dunkest.com/api/v1/leagues/10/config
```

It currently returns:

```text
current_competition_id = 49
current_schedule_id = 49
current_players_list_id = 49
current_matchday.id = 1528
current_matchday.number = 1
configured teams = 20
configured matchdays = 38
```

The structured current market is retrieved from:

```text
GET https://fantaking-api.dunkest.com/api/v1/players-lists/49/matchdays/1528/players?page=1&per_page=-1
```

The market request requires the existing bearer token recovered in memory from
the official frontend origin's Playwright local-storage state in
`.auth/euroleague.json`. The request uses `Accept: application/json`, the
official Fantasy `Origin` and `Referer`, and a bearer `Authorization` header.
Cookies and CSRF headers were not required by the verified direct request. No
credential value was logged, persisted outside `.auth`, or added to a fixture.

## Root cause

The endpoint and authentication mechanism had not disappeared. The live
pipeline only attempted Fantasy ingestion when a caller supplied a local config
file and that file existed. The control center's default
`data/reference/fantasy_live_sources.json` did not exist, so the pipeline
skipped the source and emitted `current Fantasy config not supplied/available`.
Meanwhile, the public bootstrap had advanced from the retained E2025 IDs
(`30`, Matchday `1000`) to the E2026 IDs above.

The live pipeline now discovers and validates the official current bootstrap by
default. An explicit `--fantasy-config` remains available only as a controlled
override for sanitized fixtures or offline reproduction.

## Validation and fail-closed behavior

Before raw archival or database writes, the complete live response is checked
for:

- a reasonable player and team population;
- unique, non-null Fantasy entity IDs;
- non-empty names;
- recognized Guard, Forward, Center, and Head Coach positions;
- finite positive credit values;
- valid team objects whose IDs belong to the official bootstrap catalog;
- complete configured-team coverage;
- valid Turn identifiers/numbers;
- duplicate entities; and
- complete coach coverage when coaches are present.

Empty, malformed, non-JSON, login-page, API-error, duplicate, and obviously
partial responses fail before market mutation. Source freshness distinguishes
configuration discovery, configuration validation, authentication, market API,
and market validation failures by their safe exception categories. The prior
valid snapshot remains available after a failed attempt.

## Verified live E2026 result

```text
entities = 345
players = 325
coaches = 20
valid credits = 345
teams = 20
Matchday = 1
Turns = 1, 2

Guard = 145
Forward = 117
Center = 63
Head Coach = 20
```

| Team | Players | Coaches |
|---|---:|---:|
| Anadolu Efes Istanbul | 18 | 1 |
| Armani Olimpia Milan | 15 | 1 |
| Besiktas Istanbul | 14 | 1 |
| Crvena Zvezda Meridianbet Belgrade | 18 | 1 |
| Dubai Basketball | 18 | 1 |
| FC Barcelona | 14 | 1 |
| FC Bayern Munich | 16 | 1 |
| Fenerbahce Tarfin Istanbul | 16 | 1 |
| Hapoel IBI Tel Aviv | 20 | 1 |
| Kosner Baskonia Vitoria-Gasteiz | 13 | 1 |
| LDLC ASVEL Villeurbanne | 15 | 1 |
| Maccabi Rapyd Tel Aviv | 16 | 1 |
| Olympiacos Piraeus | 17 | 1 |
| Panathinaikos AKTOR Athens | 17 | 1 |
| Paris Basketball | 19 | 1 |
| Partizan Mozzart Bet Belgrade | 16 | 1 |
| Real Madrid | 18 | 1 |
| Valencia Basket | 15 | 1 |
| Virtus Bologna | 15 | 1 |
| Zalgiris Kaunas | 15 | 1 |

All 20 market team IDs resolved to canonical team references. The conservative
crosswalk produced:

```text
MATCHED = 248
AMBIGUOUS = 0
NAME_TEAM_CONFLICT = 0
NO_OFFICIAL_CANDIDATE = 77
```

The 77 no-candidate players remain excluded/quarantined; no edit-distance or
unsafe fuzzy matching was added. This is expected before the official E2026
season-player roster/history is complete.

The response exposes the already-supported `is_injured`,
`probability_of_playing`, and `started_from_bench` fields, plus `face_path`.
The current values are uniform pre-season defaults (`false`, `1`, and `true`
respectively), so they are retained as official current-market observations but
are not treated as proof of a separate injury report. Team jersey/logo metadata
is exposed by the bootstrap. The independent official availability-report
source remains fail-closed because no current-season report URL was discovered.

The canonical live run was `40328da3-8a25-5b2b-a6c0-c98fd7c34705`; it inserted
345 append-only E2026 market snapshot rows. Freshness now reports:

```text
Fantasy Market FRESH
345 entities (325 players, 20 coaches); Matchday 1; official config auto-discovered

Availability STALE
no current-season report URL discovered
```

Sanitized public examples from the inserted snapshot:

| Fantasy ID | Name | Team | Position | Credits | Turn |
|---:|---|---|---|---:|---:|
| 3766 | Sasha Vezenkov | Olympiacos Piraeus | Forward | 17.0 | 1 |
| 3765 | Mike James | Anadolu Efes Istanbul | Guard | 16.0 | 1 |
| 3770 | Chima Moneke | Crvena Zvezda Meridianbet Belgrade | Forward | 15.5 | 1 |
| 11549 | Jonas Valanciunas | Zalgiris Kaunas | Center | 15.5 | 1 |
| 3795 | TJ Shorts | Valencia Basket | Guard | 14.9 | 2 |

## Verification

```text
python -m unittest discover -s tests -p 'test_*.py'
Ran 357 tests in 652.543s — OK

python -m scripts.check_database --no-report
PASS: 86 checks, 0 failures
```

The test total includes seven new current-market tests covering automatic
configuration discovery, endpoint/config error separation, response validation,
authenticated scoped retrieval, append-only/idempotent database insertion,
unmatched-player quarantine, fail-closed preservation, and freshness updates.
