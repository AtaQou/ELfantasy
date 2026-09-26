# Phase 2.6 Canonical Database Architecture

## Decision

The normalized analytical layer uses **DuckDB 1.5.5** at `data/db/euroleague.duckdb`. DuckDB is an embedded analytical database with a Python 3.13 wheel, persistent single-file storage, transactions, constraints, indexes, and `ON CONFLICT` support. It fits a local, reproducible research project without introducing a server or production infrastructure. The code always opens an explicit connection rather than DuckDB's shared module-level connection, following the [official Python guidance](https://duckdb.org/docs/stable/clients/python/overview).

The `.duckdb` file is generated and ignored by Git. It is not the source of truth. Immutable JSON/XML artifacts under `data/raw/` are the source layer from which canonical rows can be rebuilt.

## Layer boundary

```text
Official EuroLeague JSON/XML       Authenticated Fantaking JSON
              |                                  |
              v                                  v
   data/raw/euroleague/...            data/raw/fantasy/...
              |                                  |
              +------------ ingestion -----------+
                                 |
                                 v
                    data/db/euroleague.duckdb
                                 |
                    canonical observed facts only
                                 |
                      Phase 3 (not implemented)
                   leakage-safe feature datasets
```

Raw writes are content-hashed and immutable. If the provider later changes an already occupied payload, the new bytes receive a hash-suffixed file instead of overwriting the original. Every fact/snapshot row has `source_artifact_id` and `ingestion_run_id`; dimensions can be traced through the roster membership, game, box-score, or snapshot fact that first established them.

## Textual ER diagram

```text
competitions 1 ──< seasons 1 ──< games >── 2 teams
                         |          |
                         |          +──< player_game_stats >── players
                         |          +──< team_game_stats >──── teams
                         |          +──< play_by_play_events > players/teams
                         |          +──< shots >────────────── players/teams
                         |
players >──< player_team_memberships >── teams
players 1 ──< player_aliases
teams   1 ──< team_aliases

fantasy_entities 1 ──< fantasy_market_snapshots >── teams (when resolved)
fantasy_entities 1 ──< fantasy_player_crosswalk >── players (only confirmed)
players          1 ──< availability_events

ingestion_runs 1 ──< raw_artifacts 1 ──< normalized fact rows
ingestion_runs 1 ──< data_anomalies >── quarantined entity/game
```

## Canonical identifiers

- Canonical IDs are deterministic UUIDv5 strings derived from provider namespace plus stable natural keys.
- Games use `(competition, season_code, game_code)` because game code is season-scoped.
- Teams use the official EuroLeague team code. Mutable display names are attributes/aliases, never keys.
- Players use official EuroLeague person codes. Only the demonstrated numeric live-feed variant (`P007200` versus roster `007200`) is canonicalized; alphanumeric legacy codes are not altered. Raw player codes remain on fact rows.
- Fantasy IDs remain in the separate `fantaking_euroleague` namespace. Numerical overlap is never identity evidence.

## Canonical tables

| Area | Tables | Purpose |
| --- | --- | --- |
| Metadata | `schema_migrations`, `ingestion_runs`, `raw_artifacts` | Reproducible schema and source/run provenance |
| Competition | `competitions`, `seasons` | Stable competition and season scope |
| Teams | `teams`, `team_aliases` | Canonical identity plus provider/season aliases |
| Players | `players`, `player_aliases`, `player_team_memberships` | Stable people, spelling variants, and transfer-aware roster history |
| Games | `games`, `games_with_derived_winner` | One season-scoped game and a score-derived winner view |
| Basketball facts | `player_game_stats`, `team_game_stats`, `play_by_play_events`, `shots` | Raw observed game statistics; no rolling/advanced features |
| Fantasy | `fantasy_entities`, `fantasy_player_crosswalk`, `fantasy_market_snapshots` | Separate player/coach namespace, versioned mappings, append-only markets |
| Availability | `availability_events` | Future point-in-time injury/news observations; deliberately empty now |
| Quality | `data_anomalies`, `quarantined_entities` | Non-destructive review/exclusion mechanism |

The known unreliable v2 `winner` value is absent from canonical truth. `games_with_derived_winner` derives the winner only from final scores. Play-by-play keeps both `source_sequence`/`source_period` and the non-monotonic `raw_event_number`. Shots explicitly document that the source omits missed free throws.

## Team aliases

Team aliases are versioned by provider and season. Exact official names generate safe provider aliases. Reviewed exceptions live in one auditable reference file, `data/reference/team_aliases.csv`.

The Baskonia mapping is represented as three reviewed aliases for Fantaking provider ID `143`, code `KBA`, and name `Baskonia Vitoria-Gasteiz`, all targeting official code `BAS`. No ingestion code contains an ad-hoc Baskonia string replacement.

## Player mapping policy

`fantasy_player_crosswalk` is versioned by season and validity interval. Automatic confirmation requires:

1. an explicit cross-provider official ID, if one ever becomes available; or
2. a unique exact/conservatively normalized name within an exact versioned team mapping and season.

Fuzzy name matches are never silently accepted. An unresolved or ambiguous row has a null canonical player ID and an explicit status. Coaches are `fantasy_entities.entity_type = 'COACH'` and never enter the player crosswalk.

The reviewed Baskonia team alias supplies the missing team context for 16 exact-name players. Complete E2025 roster/game context plus conservative provider-format variants improve current confident coverage to 324/345 (93.9%). The remaining 21 stay null: three ambiguous, two name/team conflicts, and sixteen without an official candidate.

## Fantasy snapshot semantics

`fantasy_market_snapshots` is append-only at one provider entity per observed response. Its natural uniqueness boundary is `(snapshot_batch_id, fantasy_entity_id)`. A batch derives from the immutable raw artifact and observation timestamp, making retrying the same artifact idempotent while allowing a later identical observation to remain a distinct snapshot.

- `credits` from a historical current-season matchday response is labelled `HISTORICAL_CURRENT_SEASON_MATCHDAY_PRICE`.
- historical Fantasy positions are exposed only after season-selection validation;
  old-season credits remain independently quarantined.
- injury, playing probability, bench, on-fire, popularity, and average fields are retained only as collection-time `overlay_*` evidence.
- old-matchday responses are marked `CURRENT_OVERLAY_AT_COLLECTION_NOT_HISTORICAL` and `status_valid_as_of_matchday = false`.
- `leakage_safe_fantasy_market` returns null for unsafe injury/probability/bench/on-fire/average values.

The official Stats UI exposes E2022-E2024, but selected-matchday credits are a
constant season-level overlay and are not historical prices. Their 20,982 played-player
observations are retained for audit with `credits_valid_pre_matchday = false`.
All 38 E2025 matchdays remain validated, with 13,191 market snapshots, real
collection timestamps, and provenance. See `reports/historical_fantasy_market.md`.

## Availability semantics

`availability_events` is bitemporal enough to distinguish when a claim was published, when this project observed it, and its asserted validity interval. It supports a strict query such as:

```sql
SELECT *
FROM availability_events
WHERE canonical_player_id = ?
  AND observed_at < ?
  AND (published_at IS NULL OR published_at < ?)
  AND (valid_from IS NULL OR valid_from < ?)
  AND (valid_to IS NULL OR valid_to >= ?);
```

No historical availability row is fabricated from the demonstrated unsafe Fantasy overlays. Current observations may be added later only with explicit observation time and source provenance.

## Idempotency and failure behavior

Primary/unique keys reject duplicates, and batch inserts use DuckDB's documented [`ON CONFLICT DO NOTHING`](https://duckdb.org/docs/current/sql/statements/insert). Raw content is checksummed. Every invocation creates an `ingestion_runs` record; failures remain `FAILED` with a sanitized error summary rather than being swallowed.

Safe commands enforce the selective retention window:

```bash
python -m scripts.ingest_history --stage core --start-season 2018 --end-season 2025
python -m scripts.ingest_history --stage events --start-season 2021 --end-season 2025
python -m scripts.collect_fantasy_history
```

The CLI refuses core seasons outside E2018-E2025 and rich seasons outside E2021-E2025 unless `--allow-outside-policy` is explicitly supplied after a separate project decision. E2007-E2017 capability remains available in code but is not part of retained canonical history.

DuckDB restricts updates to parent rows after foreign-key-referenced facts exist. The pipeline therefore does not force source corrections in place: a changed payload is preserved as a new immutable raw artifact and flagged for a deterministic rebuild. This keeps normalized output explainable and prevents a partial correction from silently mixing source versions.

## Quarantine policy

Raw data and canonical observations are retained even when unsuitable for a downstream calculation. `data_anomalies` records severity, details, detection time, and a quarantine flag. Future feature builders must anti-join `quarantined_entities` for the affected calculation family.

E2023/170 is quarantined for rotation/on-off use because four players differ from official minutes by ±60 seconds. It is separately quarantined from strict chronological features because the retained live header has no trustworthy UTC offset. Its substitution structure, team totals, box score, and raw events remain available. Identity ambiguity follows the same conservative rule: unresolved mappings are retained but cannot masquerade as confirmed.

## Phase 3 compatibility

The schema supports strict point-in-time construction:

- previous basketball observations join through `games.game_date < prediction_cutoff`;
- Fantasy observations have `observed_at`, matchday, price semantics, and source timestamps;
- memberships have season and validity boundaries;
- future availability events have published/observed/valid times;
- every fact is traceable to an immutable raw hash and ingestion run.

Two known limitations must remain explicit: E2023/170 currently lacks a trustworthy UTC tip timestamp in the retained header-only fixture, and historical Fantasy matchday payloads were collected after those matchdays. Phase 3 must never reinterpret collection-time overlays as historical pre-deadline knowledge.

## Selective historical retention

The canonical layer stores compact core observations for E2018-E2025 and rich PBP/shot observations only for E2021-E2025. This is a data-retention decision, not a training-window decision. Future experiments can choose the last three, five, or eight seasons through query parameters without changing canonical storage.

Immutable core artifacts are retained for all eight seasons. Immutable PBP/shot artifacts are retained only for the five-season rich window. PBP/shots for E2018-E2020 are `NOT REQUESTED BY POLICY`; they must not be reported as failed coverage. The storage guard warns at 20 GB and hard-stops at 25 GB pending explicit approval.

## Future domestic and external-player integration

The permanent identity boundary is the canonical player, not a EuroLeague-only roster row. A later provider-neutral extension can add `external_competitions`, `external_games`, and compact `external_player_game_stats` keyed to the same canonical player through explicit provider crosswalks. It should store source competition, team/opponent, tip time, starts, minutes, and basic box-score workload.

Domestic collection should begin prospectively/current-season and does not initially need PBP. This supports point-in-time `days_since_any_game`, recent-game counts, and minutes over three/five/seven-day windows without pretending EuroLeague rest equals real rest.

For a player entering EuroLeague without recent local history, ingestion should be player-driven: resolve the person, identify the prior competition, and retrieve only the recent one or two relevant seasons. Source league and season must remain attributes so later models can learn, rather than assume, EuroCup/NBA/G League/domestic-to-EuroLeague translation.

## Ephemeral observation policy

Completed official schedules, boxes, PBP, and shots can normally be reconstructed later. Fantasy prices/eligibility, injuries, playing probabilities, coach statements, projected lineups, and pregame odds cannot. Those future sources must be append-only with `observed_at`, publication time where available, provider ID, raw artifact/hash, and validity semantics; current-state rows must never overwrite an older observation.

## Future Live Update Strategy

No scheduler is implemented, but the existing paths support incremental work without redesign:

- refresh mutable current schedules using the stable season-scoped game key, while completed-game facts remain immutable and cached;
- request/normalize only newly completed games; conflict keys and per-game counts make safe reruns converge without duplicates;
- append every legitimately observed Fantasy market batch instead of updating an earlier snapshot;
- append future availability/news and odds observations with publication/observation timestamps;
- attach compact domestic results through provider crosswalks to the same canonical players;
- preserve every fetch in `raw_artifacts` and every operation in `ingestion_runs`, with classified failures/checkpoints available for retry.

Mutable pregame state and immutable completed-game facts therefore share provenance and canonical identities without sharing overwrite semantics.
