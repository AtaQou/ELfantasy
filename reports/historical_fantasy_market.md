# Phase 2.6 historical Fantasy market expansion

Generated from the official EuroLeague Fantasy Stats surfaces on 2026-08-12. Phase 3A has not been started.

## Result

The Stats UI exposes all four requested seasons, but it does **not** provide a safe multi-season historical price archive. E2025 is fully validated. E2022-E2024 were recovered for audit, identity, team, and position coverage, but their returned `cr` values are a season-level overlay: they do not change when the selected matchday changes. Those credits are quarantined and return `NULL` as `fantasy_credits_pre_matchday`.

| Season | Official season ID | Matchdays | Stored observations | Unique players | Raw credits > 0 | Safe pre-MD credits | Confident identity matches | Backtesting price status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| E2022 / 2022-23 | 11 | 1-41 | 6,987 | 278 | 1,746 (24.99%) | 0 | 264/278 (94.96%) | Unsafe |
| E2023 / 2023-24 | 15 | 1-43 | 7,073 | 275 | 1,735 (24.53%) | 0 | 265/275 (96.36%) | Unsafe |
| E2024 / 2024-25 | 17 | 1-43 | 6,922 | 274 | 1,682 (24.30%) | 0 | 267/274 (97.45%) | Unsafe |
| E2025 / 2025-26 | 23 | 1-38 | 13,191 | 345 players | 13,191 (100%) | 13,191 | 324/345 (93.91%) | Validated |

E2025's 13,191 market observations include 12,431 player and 760 coach observations. The older Stats observations are players who played in the selected date window, not a complete selectable market. Therefore the leakage-safe dataset has 12,431 validated **player-matchday** prices (13,191 market observations including coaches), while 20,982 legacy audit observations are retained but excluded as historical prices.

## Official source and request structure

The EuroLeague Stats route embeds Dunkest's official EuroLeague Fantasy provider page:

```text
https://www.dunkest.com/en/euroleague/stats/players/table
```

The season page contains the authoritative season, week, team, and position selectors. Changing a season or matchday drives structured JSON requests rather than rendered-table scraping.

Public legacy Stats table:

```text
GET https://www.dunkest.com/api/stats/table

season_id=<11|15|17|23>
mode=dunkest
stats_type=tot
weeks[]=<Fantasy matchday>
rounds[]=<subround ID>             # repeated when necessary
teams[]=<team ID>                  # repeated
positions[]=<position ID>          # repeated
min_cr=0&max_cr=100
sort_by=pdk&sort_order=desc
```

Subround discovery:

```text
GET https://www.dunkest.com/api/stats/dunkest/week/rounds
    ?season_id=<season ID>&week_number=<Fantasy matchday>
```

The legacy “active players” request only exposes a small end-of-season active subset. The UI's “all players” mode replaces `weeks[]`/`rounds[]` with `mode=nba&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`. It returns all players who played in that date window, including the smaller playoff/Final Four populations, but still is not a full market because DNP/non-playing selectable players are absent.

The current Flutter app also uses an authenticated, columnar Stats endpoint:

```text
GET https://fantaking-api.dunkest.com/api/v1/competitions/30/stats/players/table

stats_type=tot
matchdays=<opaque matchday ID>      # E2025 IDs 963-1000
quotations=0,100
page=<N>&per_page=100
sort_by=fpt&sort_order=desc
```

Pagination metadata is returned in top-level `meta`; records are `data.players`, with values defined by `data.columns`. Normal account authentication was reused from the project's existing manual login. No access controls or anti-bot protections were bypassed.

The public legacy schema is the same for E2022-E2025. The current app endpoint is a separate columnar schema. E2025 market snapshots continue to come from the already archived matchday-addressed players-list endpoint; 165 Stats pages were downloaded only for independent validation, avoiding a redundant market redownload.

## Credit semantics

### E2025

For each matchday, Stats `quotation` was joined by the official Fantasy player ID to the existing matchday-addressed market snapshot. The selected-matchday values are exactly the price available for that Fantasy matchday, already established as the reliable E2025 historical market series.

```text
exact matches:               12,431
mismatches:                       0
missing on new Stats source:      0
missing on existing market:   2,310
exact rate on shared rows:     100%
```

The 2,310 one-sided Stats rows are later/current Stats-table players absent from the relevant historical market pool. They are not inserted into E2025's canonical market. The independently selected values change across matchdays for 9,899 consecutive shared-player pairs, ruling out replication of one current quotation across the E2025 history.

### E2022-E2024

Changing matchday changes game statistics, player participation, and response hashes, but not player `cr`:

| Season | Consecutive shared-player pairs | Pairs whose `cr` changed | Non-zero historical `plus` pairs | `cr + plus = next cr` | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| E2022 | 1,523 | 0 | 0 | 1,523 (degenerate) | Constant overlay; no observed movement |
| E2023 | 1,436 | 0 | 1,230 | 311 (21.66%) | Constant overlay despite price movement |
| E2024 | 1,427 | 0 | 1,251 | 229 (16.05%) | Constant overlay despite price movement |

For E2023 and E2024, 100% of non-zero `plus` pairs retain the same next-matchday `cr`. E2022 only returns non-zero `plus` in its final selected week, so the apparently perfect equation before then is vacuous. The full date-filter route also fills `cr=0` for roughly 75% of observations. Consequently no safe off-by-one shift or `post_round_(N-1)` transformation exists for these seasons. The exact missing historical prices remain missing; none were inferred from performance.

## Matchday and competition-round mapping

The database season mapping was verified from `seasons`, not assumed: E2022=2022-23, E2023=2023-24, E2024=2024-25, and E2025=2025-26.

The public Fantasy week lists exactly match canonical competition rounds 1-41 for E2022 and 1-43 for E2023/E2024, including postseason weeks. E2025's regular-season Fantasy competition exposes matchdays 1-38, which map directly to canonical rounds 1-38; later canonical postseason rounds belong to a different Fantasy competition and were not silently appended.

Most mappings are `VALIDATED_DIRECT`. Rescheduled-game rounds retain one Fantasy matchday/competition round but require player game-date context:

- E2022 round 24;
- E2023 rounds 2 and 6;
- E2024 round 17.

These are marked `PLAYER_DATE_CONTEXT_REQUIRED`, with separate actual-date request windows. The future join must use the player's game date in those rounds. No round number was shifted.

## Position reliability

Fantasy position is safe as a historical **season-level** classification for all four seasons. The date-filter and matchday-filter legacy routes agree on position for every shared row (5,139/5,139), while changing season changes the returned position for 24 same-name players. Examples include Elijah Bryant (G/F/G across E2022/E2023/E2024) and Filip Petrusev (C/C/F). This rules out a single current-position overlay. E2025 Stats positions agree with the existing market in all 12,431 shared player rows.

Position is not assumed to vary within a season. It is exposed as `fantasy_position` only because its season selection behavior was verified independently from credits.

## Identity matching

Legacy Fantasy IDs use a separate provider namespace and are never treated as canonical EuroLeague player IDs. Resolution uses normalized official names plus exact team membership in the relevant season; strong deterministic name/team evidence is allowed, but edit-distance matching is not.

| Season | MATCHED | AMBIGUOUS | NAME_TEAM_CONFLICT | NO_OFFICIAL_CANDIDATE | UNKNOWN |
| --- | ---: | ---: | ---: | ---: | ---: |
| E2022 | 264 | 14 | 0 | 0 | 0 |
| E2023 | 265 | 10 | 0 | 0 | 0 |
| E2024 | 267 | 7 | 0 | 0 | 0 |
| E2025 | 324 | 3 | 2 | 16 | 0 |

Unresolved identities remain null in the canonical player column. Existing E2025 coverage was not weakened.

## Returned-field safety classification

Legacy fields are classified as follows:

| Classification | Fields | Handling |
| --- | --- | --- |
| Historical identity/team | `id`, `first_name`, `last_name`, `slug`, `team_id`, `team_code`, `team_name` | Retained with raw and canonical identity namespaces separate |
| Historical season position | `position_id`, `position` | Exposed after cross-season validation |
| Unsafe historical credit overlay | `cr` for E2022-E2024 | Retained raw/canonical for audit; null in safe pre-matchday view |
| Historical post-game outcome | `gp`, `pdk`, `plus`, `min`, `starter`, `pts`, `ast`, `reb`, `stl`, `blk`, `blka`, `fgm`, `fgm_tot`, `fga`, `fga_tot`, `tpm`, `tpm_tot`, `tpa`, `tpa_tot`, `ftm`, `ftm_tot`, `fta`, `fta_tot`, `oreb`, `dreb`, `tov`, `pf`, `fouls_received`, `plus_minus`, `fgp`, `tpp`, `ftp` | Preserved only in raw Stats responses; excluded from pre-matchday market view |

Modern E2025 fields `id`, `name`, `team`, `position`, and `quotation` are safe for the validated matchday intersection. `rank`, `fpt`, `plus`, `pts`, `reb`, `ast`, `stl`, `tov`, `blk`, `blka`, `fd`, `pf`, `fg_missed`, `ft_missed`, and the win/loss bonus columns are selected-matchday outcomes or table presentation fields and are not persisted into the pre-matchday market view.

The existing market endpoint's `is_injured`, `probability_of_playing`, `started_from_bench`, `is_on_fire`, and Fantasy average are mutable current overlays and always return null in `leakage_safe_fantasy_market`. Popularity is labelled collection-time-only and is not historical as-of evidence. The Stats endpoints did not add timestamped injury/availability data.

## Coverage anomalies and provenance

No official matchday is absent from the date-filter recovery. The legacy active-only mode returns zero rows for E2023 matchdays 35-36 and E2024 matchdays 35-36, while the date-filter route returns 44/22 and 42/22 played players respectively. These are UI population-mode gaps, not missing game observations. Postseason rows naturally fall to 22-90 players, and Final Four weeks to 39-46.

The raw archive contains 554 exact responses: 126 E2022, 132 E2023, 131 E2024, and 165 E2025 validation pages. Each `raw_artifacts` row has endpoint, full source URL/request parameters, season, matchday where applicable, UTC retrieval time, byte count, SHA-256, and ingestion run. `raw_artifact_request_provenance` adds request-parameter JSON and parser version `historical_fantasy_stats_v1`. No downloaded response was manually edited.

Duplicate validation found zero duplicate `(season, fantasy_matchday, fantasy_entity)` observations. Re-running the collector produces zero new canonical observations and equivalent source bytes map to the same content-addressed artifact/canonical IDs.

## Recommendation for Phase 3A and later backtesting

- **E2022:** not safe for Fantasy-price backtesting.
- **E2023:** not safe for Fantasy-price backtesting.
- **E2024:** not safe for Fantasy-price backtesting.
- **E2025:** safe for regular-season Fantasy-price backtesting, matchdays 1-38.

Do **not** change Phase 3A's optional Fantasy-price extension from E2025-only to E2022-E2025. The older identity, team, position, and round mappings may be retained as supporting historical metadata, but their credit values must not enter a budget-aware backtest.

Remaining limitations are the missing full-market/DNP population for the legacy Stats route, unusable E2022-E2024 point-in-time credits, unresolved identities listed above, rescheduled rounds requiring player-date context, no timestamped historical availability/injuries, and E2025 coverage stopping at the 38-round regular-season Fantasy competition.
