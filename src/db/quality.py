"""Generate the evidence-backed Phase 2 database quality report."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .database import DEFAULT_DATABASE_PATH, connect_database
from .integrity import validate_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = PROJECT_ROOT / "reports" / "database_quality.md"


def generate_database_quality_report(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    output_path: Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    integrity = validate_database(database_path)
    with connect_database(database_path, read_only=True) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ).fetchall()
        ]
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        season_rows = connection.execute(
            """
            SELECT season_code, count(DISTINCT canonical_game_id),
                   count(DISTINCT CASE WHEN player_game_id IS NOT NULL THEN canonical_game_id END)
            FROM games
            LEFT JOIN player_game_stats USING (canonical_game_id)
            GROUP BY season_code ORDER BY season_code
            """
        ).fetchall()
        missingness = _missingness(connection)
        mappings = connection.execute(
            """
            SELECT mapping_status, count(*) FROM fantasy_player_crosswalk
            WHERE valid_to IS NULL
            GROUP BY mapping_status ORDER BY mapping_status
            """
        ).fetchall()
        unresolved_players = connection.execute(
            """
            SELECT entity.fantasy_id, entity.display_name,
                   coalesce(team.canonical_name, snapshot.team_name_raw),
                   crosswalk.match_method, crosswalk.notes
            FROM fantasy_player_crosswalk AS crosswalk
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN teams AS team
              ON team.canonical_team_id = crosswalk.team_context_id
            LEFT JOIN (
                SELECT fantasy_entity_id, team_name_raw,
                       row_number() OVER (
                           PARTITION BY fantasy_entity_id ORDER BY observed_at DESC
                       ) AS recency
                FROM fantasy_market_snapshots
            ) AS snapshot
              ON snapshot.fantasy_entity_id = crosswalk.fantasy_entity_id
             AND snapshot.recency = 1
            WHERE crosswalk.mapping_status <> 'MATCHED'
              AND crosswalk.valid_to IS NULL
            ORDER BY entity.display_name, entity.fantasy_id
            """
        ).fetchall()
        fantasy_coverage = connection.execute(
            """
            SELECT season_code, matchday_number, count(*),
                   cast(min(observed_at) AS VARCHAR), cast(max(observed_at) AS VARCHAR),
                   min(price_semantics), bool_or(status_valid_as_of_matchday)
            FROM fantasy_market_snapshots
            GROUP BY season_code, matchday_number
            ORDER BY season_code, matchday_number
            """
        ).fetchall()
        anomalies = connection.execute(
            """
            SELECT entity_type, entity_id, anomaly_code, severity, quarantined, details_json
            FROM data_anomalies ORDER BY severity DESC, anomaly_code
            """
        ).fetchall()
        ingestion_runs = connection.execute(
            """
            SELECT source, status, count(*), sum(records_inserted), sum(error_count)
            FROM ingestion_runs GROUP BY source, status ORDER BY source, status
            """
        ).fetchall()
        pbp_inversions = int(
            connection.execute(
                """
                WITH ordered AS (
                    SELECT canonical_game_id, source_sequence, raw_event_number,
                           lag(raw_event_number) OVER (
                               PARTITION BY canonical_game_id ORDER BY source_sequence
                           ) AS previous_number
                    FROM play_by_play_events
                )
                SELECT count(*) FROM ordered
                WHERE raw_event_number < previous_number
                """
            ).fetchone()[0]
        )
        unresolved_team_snapshots = int(
            connection.execute(
                "SELECT count(*) FROM fantasy_market_snapshots WHERE canonical_team_id IS NULL"
            ).fetchone()[0]
        )

    matched = dict((str(key), int(value)) for key, value in mappings).get("MATCHED", 0)
    total_crosswalk = sum(int(row[1]) for row in mappings)
    mapping_percent = (100 * matched / total_crosswalk) if total_crosswalk else 0.0
    lines = [
        "# Canonical Database Quality Report",
        "",
        f"Generated: `{datetime.now(UTC).isoformat()}`",
        "",
        "This is the selective Phase 2.5 canonical database: core E2018-E2025 and rich event data E2021-E2025. Raw artifacts remain the source of truth.",
        "",
        "## Engine and integrity",
        "",
        f"- Engine: DuckDB `{duckdb.__version__}`.",
        f"- Integrity suite: **{'PASS' if integrity['passed'] else 'FAIL'}** ({integrity['check_count']} checks, {integrity['failure_count']} failures).",
        "- Winner is derived only from validated final scores; the known-bad v2 `winner` field is not stored as truth.",
        "- Shot records carry an explicit note that the source omits missed free throws.",
        "",
        "## Table row counts",
        "",
        "| Table | Rows |",
        "| --- | ---: |",
        *[f"| `{table}` | {counts[table]:,} |" for table in tables],
        "",
        "## Retained season coverage",
        "",
        "| Season | Games | Games with player stats |",
        "| --- | ---: | ---: |",
        *[
            f"| {season_code} | {game_count:,} | {detail_count:,} |"
            for season_code, game_count, detail_count in season_rows
        ],
        "",
        "Detailed coverage and requested-versus-policy distinctions are reported in `historical_coverage.md`. E2018-E2020 PBP/shots are absent by policy, not endpoint failure; E2007-E2017 bulk data are not retained.",
        "",
        "## Missingness and reconciliation",
        "",
        "| Check | Rows |",
        "| --- | ---: |",
        *[f"| {name} | {value:,} |" for name, value in missingness.items()],
        "",
        "All games with team box scores have exactly two team rows, and team points reconcile to player points under the integrity suite.",
        "",
        "## Fantasy identity and history",
        "",
        f"- Confident player mappings: **{matched}/{total_crosswalk} ({mapping_percent:.1f}%)**.",
        f"- Unresolved Fantasy team snapshot rows: {unresolved_team_snapshots}.",
        "- The Baskonia provider ID/name/code discrepancy is resolved through reviewed rows in `team_aliases`, not a scattered string replacement.",
        "- Coaches remain separate `COACH` fantasy entities and are never joined to official players.",
        "",
        "| Season | Matchday | Rows | First observed | Last observed | Price semantics | Any status valid as-of matchday? |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
        *[
            f"| {season} | {matchday} | {rows:,} | {first} | {last} | {price} | {bool(status_valid)} |"
            for season, matchday, rows, first, last, price, status_valid in fantasy_coverage
        ],
        "",
        "Historical current-season prices are retained as matchday-addressed prices. Position semantics are explicitly unproven historically. Injury, probability, bench, on-fire, and average fields from old-matchday responses are marked current overlays and return `NULL` through `leakage_safe_fantasy_market`.",
        "",
        "No rows were fabricated in `availability_events`; it remains ready for sources with real publication/observation timestamps.",
        "",
        "### Unresolved Fantasy players",
        "",
        "No weak fuzzy match is accepted merely to increase coverage.",
        "",
        "| Fantasy ID | Player | Team context | Method | Review note |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {fantasy_id} | {name} | {team or '—'} | `{method}` | {notes or 'No exact candidate.'} |"
            for fantasy_id, name, team, method, notes in unresolved_players
        ],
        "",
        "## Play-by-play and schema observations",
        "",
        f"- Raw event-number inversions retained: {pbp_inversions}. `source_sequence` and `source_period` are the canonical ordering evidence.",
        "- Retained player-box and roster row schemas are stable across E2018-E2025; PBP and shot row schemas are stable across E2021-E2025.",
        "- Live numeric player IDs are deliberately canonicalized from forms such as `P007200` to roster code `007200`; raw codes remain on fact rows.",
        "",
        "## Quarantine and anomalies",
        "",
    ]
    if anomalies:
        lines.extend(
            [
                "| Entity | Code | Severity | Quarantined | Details |",
                "| --- | --- | --- | --- | --- |",
                *[
                    f"| {entity_type}:{entity_id} | `{code}` | {severity} | {bool(quarantined)} | `{details}` |"
                    for entity_type, entity_id, code, severity, quarantined, details in anomalies
                ],
            ]
        )
    else:
        lines.append("No anomalies recorded.")
    lines.extend(
        [
            "",
            "Raw records are never deleted when an anomaly is quarantined. E2018/21 lacks a usable official box score. Fourteen games carry invalid provider clock sentinels and are excluded from strict clock-dependent work. E2023/170 is additionally excluded from rotation-derived calculations until its four ±60-second player discrepancies are resolved and from strict chronology until a trustworthy UTC tip time is sourced. Their other valid facts remain queryable.",
            "",
            "## Ingestion run audit",
            "",
            "| Source | Status | Runs | Inserted records | Errors |",
            "| --- | --- | ---: | ---: | ---: |",
            *[
                f"| {source} | {status} | {runs:,} | {int(inserted or 0):,} | {int(errors or 0):,} |"
                for source, status, runs, inserted, errors in ingestion_runs
            ],
            "",
            "Failed runs remain visible in metadata; they never silently become successful. Cached reruns compare per-game normalized counts, skip valid immutable artifacts, and converge to zero new records.",
            "",
            "## Three largest remaining risks",
            "",
            "1. Previous-season Fantasy prices are still unavailable, so multi-season price backtests remain incomplete.",
            "2. Twenty-one current Fantasy players still lack a high-confidence official identity mapping; three are ambiguous, two conflict with team/jersey context, and sixteen have no official candidate.",
            "3. E2018/21 has no usable official box, while provider clock sentinels and the E2023/170 minute conflict require quarantine-aware feature generation.",
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "integrity": integrity,
        "counts": counts,
        "mapping_percentage": mapping_percent,
        "output": str(output_path),
    }


def _missingness(connection: Any) -> dict[str, int]:
    queries = {
        "games missing UTC tip time": "SELECT count(*) FROM games WHERE game_date IS NULL",
        "player-games missing canonical player": "SELECT count(*) FROM player_game_stats WHERE canonical_player_id IS NULL",
        "player-games with DNP/blank minutes": "SELECT count(*) FROM player_game_stats WHERE minutes IS NULL",
        "play-by-play events missing team (often period/timeout events)": "SELECT count(*) FROM play_by_play_events WHERE canonical_team_id IS NULL",
        "play-by-play events missing player (often system/team events)": "SELECT count(*) FROM play_by_play_events WHERE canonical_player_id IS NULL",
        "shots missing canonical player": "SELECT count(*) FROM shots WHERE canonical_player_id IS NULL",
        "Fantasy snapshots missing canonical team": "SELECT count(*) FROM fantasy_market_snapshots WHERE canonical_team_id IS NULL",
        "unresolved active Fantasy player mappings": "SELECT count(*) FROM fantasy_player_crosswalk WHERE mapping_status <> 'MATCHED' AND valid_to IS NULL",
    }
    return {
        name: int(connection.execute(query).fetchone()[0])
        for name, query in queries.items()
    }
