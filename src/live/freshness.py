"""Per-source freshness policies and read-only live status reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id


FRESHNESS_POLICIES_MINUTES = {
    "schedule": 360,
    "basketball_stats": 1_440,
    "fantasy_market": 360,
    "availability": 720,
    "current_features": 1_440,
}


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    source_name: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    age_minutes: float | None
    stale_after_minutes: int
    is_stale: bool
    last_attempt_succeeded: bool | None
    message: str | None


def record_source_refresh(
    source_name: str,
    source_type: str,
    succeeded: bool,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    live_run_id: str | None = None,
    captured_at: datetime | None = None,
    row_count: int | None = None,
    source_identifier: str | None = None,
    snapshot_batch_id: str | None = None,
    message: str | None = None,
    content_fingerprint: str | None = None,
) -> str:
    if source_name not in FRESHNESS_POLICIES_MINUTES:
        raise ValueError(f"No freshness policy for source: {source_name}")
    initialize_database(database_path)
    observed = captured_at or datetime.now(UTC)
    event_id = stable_id(
        "live_source_refresh", source_name, observed.isoformat(), live_run_id,
        source_identifier, succeeded,
    )
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO live_source_refresh_events (
              refresh_event_id, live_run_id, source_name, source_type,
              captured_at, succeeded, row_count, source_identifier,
              snapshot_batch_id, message, content_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [event_id, live_run_id, source_name, source_type, observed, succeeded,
             row_count, source_identifier, snapshot_batch_id,
             message[:2000] if message else None, content_fingerprint],
        )
    return event_id


def source_freshness(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    as_of: datetime | None = None,
) -> dict[str, SourceFreshness]:
    now = as_of or datetime.now(UTC)
    with connect_database(database_path, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT source_name,
                   max(captured_at) AS last_attempt,
                   max(captured_at) FILTER (WHERE succeeded) AS last_success,
                   arg_max(succeeded, captured_at) AS last_attempt_succeeded,
                   arg_max(message, captured_at) AS last_message
            FROM live_source_refresh_events
            WHERE captured_at <= ?
            GROUP BY source_name
            """, [now],
        ).fetchall()
    observed = {str(row[0]): row[1:] for row in rows}
    result: dict[str, SourceFreshness] = {}
    for source_name, threshold in FRESHNESS_POLICIES_MINUTES.items():
        row = observed.get(source_name)
        last_attempt = row[0] if row else None
        last_success = row[1] if row else None
        age = (
            (now - last_success).total_seconds() / 60.0
            if last_success is not None else None
        )
        result[source_name] = SourceFreshness(
            source_name=source_name,
            last_attempt_at=last_attempt,
            last_success_at=last_success,
            age_minutes=age,
            stale_after_minutes=threshold,
            is_stale=age is None or age > threshold,
            last_attempt_succeeded=bool(row[2]) if row and row[2] is not None else None,
            message=str(row[3]) if row and row[3] is not None else None,
        )
    return result


def live_status(
    season_code: str,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only operational view; it never refreshes or mutates data."""

    now = as_of or datetime.now(UTC)
    freshness = source_freshness(database_path, as_of=now)
    with connect_database(database_path, read_only=True) as connection:
        next_games = connection.execute(
            """
            SELECT canonical_game_id, game_code, round_number, scheduled_tip_time,
                   home_team_id, away_team_id, game_status
            FROM current_live_schedule
            WHERE season_code=? AND NOT played
              AND (scheduled_tip_time IS NULL OR scheduled_tip_time>=?)
            ORDER BY scheduled_tip_time NULLS LAST, game_code LIMIT 20
            """,
            [season_code, now],
        ).fetchall()
        latest_completed = connection.execute(
            """
            SELECT season_code, game_code, game_date
            FROM current_games WHERE played AND game_date IS NOT NULL
            ORDER BY game_date DESC, game_code DESC LIMIT 1
            """
        ).fetchone()
        fantasy = connection.execute(
            """
            WITH batch AS (
              SELECT snapshot_batch_id FROM fantasy_market_snapshots
              WHERE season_code=? ORDER BY observed_at DESC LIMIT 1
            )
            SELECT count(*) FILTER (WHERE entity.entity_type='PLAYER'),
                   count(*) FILTER (
                     WHERE entity.entity_type='PLAYER' AND crosswalk.mapping_status='MATCHED'
                   ), max(market.observed_at), max(market.snapshot_batch_id)
            FROM fantasy_market_snapshots AS market
            JOIN batch USING (snapshot_batch_id)
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN fantasy_player_crosswalk AS crosswalk
              ON crosswalk.fantasy_entity_id=market.fantasy_entity_id
             AND crosswalk.season_code=market.season_code
             AND crosswalk.valid_to IS NULL
            """,
            [season_code],
        ).fetchone()
        slate_exists = bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='current_upcoming_player_slate_v1'"
        ).fetchone()[0])
        if slate_exists:
            availability_counts = {
                str(status): int(count)
                for status, count in connection.execute(
                    "SELECT resolved_availability_status, count(*) "
                    "FROM current_upcoming_player_slate_v1 GROUP BY 1"
                ).fetchall()
            }
            slate = connection.execute(
                """
                SELECT count(*), max(generated_at),
                       count(*) FILTER (WHERE current_fantasy_credits IS NOT NULL)
                FROM current_upcoming_player_slate_v1
                """
            ).fetchone()
        else:
            availability_counts = {}
            slate = (0, None, 0)
        overrides = int(connection.execute(
            "SELECT count(*) FROM active_availability_overrides"
        ).fetchone()[0])
        latest_run = connection.execute(
            """
            SELECT live_run_id, status, completed_at, warning_count, elapsed_seconds
            FROM live_update_runs WHERE season_code=?
            ORDER BY started_at DESC LIMIT 1
            """,
            [season_code],
        ).fetchone()
    warnings = [
        f"{name} is stale or has never succeeded"
        for name, item in freshness.items() if item.is_stale
    ]
    return {
        "current_season": season_code,
        "as_of": now.isoformat(),
        "next_games": [
            {
                "game_id": row[0], "game_code": row[1], "round": row[2],
                "tip_time": row[3].isoformat() if row[3] else None,
                "home_team_id": row[4], "away_team_id": row[5], "status": row[6],
            }
            for row in next_games
        ],
        "latest_completed_game": (
            {"season": latest_completed[0], "game_code": latest_completed[1],
             "tip_time": latest_completed[2].isoformat()}
            if latest_completed else None
        ),
        "fantasy_players": int(fantasy[0] or 0),
        "fantasy_players_mapped": int(fantasy[1] or 0),
        "fantasy_captured_at": fantasy[2].isoformat() if fantasy[2] else None,
        "fantasy_snapshot_batch_id": str(fantasy[3]) if fantasy[3] else None,
        "availability_counts": availability_counts,
        "active_manual_overrides": overrides,
        "slate_rows": int(slate[0]),
        "slate_generated_at": slate[1].isoformat() if slate[1] else None,
        "slate_rows_with_credits": int(slate[2]),
        "freshness": {name: asdict(item) for name, item in freshness.items()},
        "latest_live_run": (
            {"run_id": latest_run[0], "status": latest_run[1],
             "completed_at": latest_run[2].isoformat() if latest_run[2] else None,
             "warnings": latest_run[3], "elapsed_seconds": latest_run[4]}
            if latest_run else None
        ),
        "warnings": warnings,
    }
