# Phase 5A live sources and architecture

## What existed and was reused

- `EuroLeagueClient`, canonical raw archiving, stable IDs, retry behavior, box-score/PBP/shot normalizers, and the resumable ingestion checkpoints.
- Append-only `fantasy_market_snapshots`, conservative Fantasy/official identity crosswalks, authenticated read-only market access, and explicit unsafe historical-overlay semantics.
- The Phase 3 core SQL and Phase 3B rotation reconstruction/feature SQL. The live adapter executes these definitions against synthetic upcoming rows, preserving strict-before-target window semantics.
- Explicit schedule `phase_code` competition-stage metadata and the frozen Phase 4B feature manifests/model version.

## What Phase 5A added

- Append-only live schedule and roster snapshots, source refresh events, official-report registry, availability snapshots, override events, slate run fingerprints, and prospective prediction-run storage.
- Incremental schedule diffing (`NEW_GAME`, `UPDATED_GAME`, `UNCHANGED_GAME`, `POSTPONED_RESCHEDULED_GAME`) and canonical updates only before child box-score facts exist.
- Canonical status/reason normalization, deterministic priority/recency resolution, conflict retention, scoped manual overrides, source freshness, teammate missing-role context, current slate generation, and operational status/orchestration CLIs.

## Source inventory

| Source | Fields | Method/authentication | Freshness policy | Priority/failure handling |
| --- | --- | --- | ---: | --- |
| Official EuroLeague v1/v2 schedule/results | game, round, phase, teams, UTC/local tip, status, scores | Public HTTPS API; no auth | 6 h | Authoritative schedule. Append snapshot; preserve last success on failure. |
| Official EuroLeague roster | player/team membership, active dates, position, jersey | Public HTTPS API; no auth | with schedule | Authoritative roster snapshot. Empty unpublished future roster is reported, never replaced by last season. |
| Official live box/PBP/shot feeds | player/team facts, substitutions, shots | Public HTTPS API; no auth | 24 h | Completed-game facts. Malformed PBP/shots are quarantined by family. |
| Official Fantasy market | Fantasy ID, mapped player, team/opponent, position, credits, average, popularity, injured, probability, bench, on-fire | Legitimate config IDs plus manually obtained session; authenticated read-only | 6 h | Current signal only. Every retrieval is timestamped; old-matchday overlays remain unsafe. |
| Official EuroLeague injury-report articles | raw prose, explicit player status/reason, article time if present | Public official pages; no auth | 12 h | `OFFICIAL_EUROLEAGUE` priority 300. HTTP 429/access failures are honored and marked stale. |
| Official club confirmation | explicit status/reason | Import-ready canonical observation API; per-club collectors not generalized | 12 h | `OFFICIAL_CLUB` priority 400. Source identifier and raw summary required. |
| Manual override | AVAILABLE/OUT/QUESTIONABLE/LIMITED/etc., note, scope/expiry | Local service/CLI | until scoped expiry | Priority 500. Append SET/CLEAR events; never changes historical facts. |

Resolution priority is **manual override (500) → official club (400) → official EuroLeague (300) → official Fantasy (200) → other verified (100) → unknown (0)**. Within the same priority, the latest knowable snapshot wins. Conflicting source rows remain stored and the resolved row carries a conflict flag.

No social-media rumor collector, credential bypass, paywall workaround, or automatic free-form news interpretation was added.
