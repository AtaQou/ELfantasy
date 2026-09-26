"""Measured Phase 5A source, historical-availability, and quality reports."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .current_features import validate_frozen_schema
from .freshness import live_status


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def generate_phase5a_reports(
    season_code: str,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    report_root: Path = PROJECT_ROOT / "reports",
) -> dict[str, Path]:
    report_root.mkdir(parents=True, exist_ok=True)
    status = live_status(season_code, database_path)
    schema = validate_frozen_schema(database_path)
    with connect_database(database_path, read_only=True) as connection:
        schedule = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE scheduled_tip_time IS NOT NULL),
                   count(*) FILTER (WHERE played),
                   count(DISTINCT snapshot_batch_id)
            FROM current_live_schedule WHERE season_code=?
            """,
            [season_code],
        ).fetchone()
        historical = connection.execute(
            """
            SELECT season_code, count(DISTINCT source_url),
                   count(*) FILTER (WHERE parse_status IN ('PARSED','PARTIAL')),
                   sum(observation_count),
                   count(*) FILTER (WHERE publication_time_reliable)
            FROM official_availability_reports
            GROUP BY season_code ORDER BY season_code
            """
        ).fetchall()
        availability = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE timing_status='PRE_GAME'),
                   count(*) FILTER (WHERE identity_match_status='MATCHED'),
                   count(DISTINCT matchday_number),
                   count(*) FILTER (WHERE normalized_status='UNKNOWN')
            FROM availability_snapshots
            """
        ).fetchone()
        snapshots = connection.execute(
            """
            SELECT count(*), count(DISTINCT snapshot_batch_id),
                   count(*) FILTER (WHERE entity.entity_type='PLAYER'),
                   max(observed_at)
            FROM fantasy_market_snapshots AS snapshot
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            WHERE season_code=?
            """,
            [season_code],
        ).fetchone()
        latest_game = connection.execute(
            """
            SELECT season_code, game_code, game_date FROM games
            WHERE played AND game_date IS NOT NULL ORDER BY game_date DESC LIMIT 1
            """
        ).fetchone()
        override_count = int(connection.execute(
            "SELECT count(*) FROM active_availability_overrides"
        ).fetchone()[0])
        latest_runtime = connection.execute(
            "SELECT elapsed_seconds FROM live_update_runs "
            "WHERE season_code=? ORDER BY started_at DESC LIMIT 1",
            [season_code],
        ).fetchone()

    historical_path = report_root / "phase5a_historical_availability.md"
    historical_path.write_text(
        _historical_report(historical, availability), encoding="utf-8"
    )
    sources_path = report_root / "phase5a_live_sources.md"
    sources_path.write_text(_sources_report(), encoding="utf-8")
    quality_path = report_root / "phase5a_live_pipeline_quality.md"
    quality_path.write_text(
        _quality_report(
            season_code, schedule, snapshots, latest_game, override_count,
            latest_runtime[0] if latest_runtime else None, status, schema,
        ),
        encoding="utf-8",
    )
    return {
        "historical_availability": historical_path,
        "live_sources": sources_path,
        "live_pipeline_quality": quality_path,
    }


def _historical_report(rows: list[tuple[Any, ...]], availability: tuple[Any, ...]) -> str:
    lines = [
        "# Phase 5A historical availability investigation",
        "",
        f"Generated at `{datetime.now(UTC).isoformat()}`. This is a coverage report, not an availability model.",
        "",
        "## Measured recovery",
        "",
        "| Season | Official URLs found | Successful/partial fetches | Parsed observations | Reliable article timestamps |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    observed_seasons = {str(row[0]) for row in rows}
    for season in ("E2022", "E2023", "E2024", "E2025"):
        row = next((item for item in rows if str(item[0]) == season), None)
        lines.append(
            f"| {season} | {int(row[1]) if row else 0} | {int(row[2]) if row else 0} | "
            f"{int(row[3] or 0) if row else 0} | {int(row[4]) if row else 0} |"
        )
    total = int(availability[0] or 0)
    pregame = int(availability[1] or 0)
    matched = int(availability[2] or 0)
    lines += [
        "",
        f"Canonical observations recovered: **{total}**; reliable pre-game timing: **{pregame}/{total}**; identity-matched: **{matched}/{total}**.",
        "",
        "Official report pages were confirmed for E2022, E2024, and E2025. No reliable E2023 report index was found. Direct automated retrieval returned HTTP 429 for every bounded attempt; the collector stopped and did not bypass the control. Publication timestamps were not inferred from round numbers or article contents.",
        "",
        "Examples of the official source family: [2022-23 Round 26](https://www.euroleaguebasketball.net/news/euroleague-injury-report-round-26/), [2024-25 Round 26](https://www.euroleaguebasketball.net/en/euroleague/news/injury-report-round-26-tae2425/), and [2025-26 Round 21](https://www.euroleaguebasketball.net/euroleague/news/euroleague-injury-report-round-21/).",
        "",
        "## Safety decision",
        "",
        "The locally recovered historical data are **not sufficient for Phase 5B supervised training**. The pages contain useful official prose, but an URL discovery is not a point-in-time player observation. Until access and publication timestamps can be retained legitimately, Phase 5B can train only on any future timestamped live snapshots accumulated by this system; historical availability should initially use deterministic status rules and explicit UNKNOWN values.",
        "",
        "Retrospective phrases such as “missed Round 25” or “last played in Round 22” are not normalized as current pre-game OUT unless the same statement explicitly declares the player unavailable for the target game. Rows captured after tip are marked POST_GAME; missing publication times remain UNKNOWN timing.",
    ]
    return "\n".join(lines) + "\n"


def _sources_report() -> str:
    return """# Phase 5A live sources and architecture

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
"""


def _quality_report(
    season: str,
    schedule: tuple[Any, ...],
    fantasy: tuple[Any, ...],
    latest_game: tuple[Any, ...] | None,
    overrides: int,
    runtime: float | None,
    status: dict[str, Any],
    schema: dict[str, Any],
) -> str:
    freshness_rows = "\n".join(
        f"| {name} | {item['last_success_at'] or '—'} | {item['age_minutes'] if item['age_minutes'] is not None else '—'} | {item['is_stale']} |"
        for name, item in status["freshness"].items()
    )
    latest = f"{latest_game[0]}/{latest_game[1]} at {latest_game[2]}" if latest_game else "none"
    runtime_text = f"{runtime:.2f} seconds" if runtime is not None else "not measured"
    return f"""# Phase 5A live pipeline quality

Generated at `{datetime.now(UTC).isoformat()}` for `{season}`.

## Current measured state

- Schedule coverage: **{int(schedule[0])} games**, **{int(schedule[1])} with authoritative UTC tips**, **{int(schedule[2])} completed**.
- Latest completed canonical game: **{latest}**.
- Current Fantasy coverage: **{int(fantasy[2])} player rows**, latest capture **{fantasy[3] or 'none'}**.
- Confident Fantasy mappings: **{status['fantasy_players_mapped']}/{status['fantasy_players']}**.
- Current slate: **{status['slate_rows']} players**; rows with credits: **{status['slate_rows_with_credits']}**.
- Availability: **{status['availability_counts']}**; active overrides: **{overrides}**.
- Frozen schema: **{'compatible' if schema['compatible'] else 'incompatible'}**, required features **{schema.get('required_feature_count', 0)}**, missing **{schema['missing']}**.
- Latest end-to-end runtime: **{runtime_text}**.

The {season} schedule is published but, at this snapshot, the official player endpoint returns zero roster rows and the current Fantasy market/config is not supplied. The slate therefore remains intentionally empty instead of carrying the prior season's players forward. Incremental round metadata is rate-limited to two newly needed rounds per run; this snapshot has {int(schedule[1])}/{int(schedule[0])} authoritative UTC times, and subsequent bounded runs add missing detail without erasing prior tips.

## Freshness

| Source | Last success | Age minutes | Stale |
| --- | --- | ---: | --- |
{freshness_rows}

## Quality gates

- Historical completed-game facts remain immutable; only childless scheduled game metadata can update.
- Upcoming rows execute the historical core/rotation SQL; a historical-cutoff parity test compares frozen features at 1e-9 tolerance.
- Exact duplicate availability and market inputs are idempotent; distinct retrievals remain append-only.
- Missing sources produce UNKNOWN/stale state and warnings, not AVAILABLE or zero-injury assumptions.
- Current slate fingerprints exclude generation timestamps, so unchanged basketball/market/availability inputs are reproducible.
- Prediction-run storage retains prediction timestamp, feature cutoff, model version, feature version, input fingerprint, and prospective-outcome flag. Phase 5A does not generate performance or availability predictions.
"""
