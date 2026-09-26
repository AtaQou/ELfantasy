"""Parity-safe current feature rows and upcoming-player slate generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.modeling.core_features import (
    CUTOFF_POLICY,
    FEATURE_PIPELINE_VERSION,
    _create_core_table,
    _create_player_features,
    _create_team_features,
)
from src.modeling.ml_protocol import feature_manifest
from src.modeling.phase4b_protocol import phase4b_feature_manifest, role_change_score
from src.modeling.rich_features import _create_rotation_features

from .availability import AvailabilityResolver
from .preparation import attach_preparation_context


FROZEN_PERFORMANCE_MODEL_VERSION = "phase4b_hybrid_25_direct_75_decomposed_v1"
LIVE_FEATURE_VERSION = "phase5a_live_parity_adapter_v1"
UNAVAILABLE_STATUSES = frozenset({"OUT", "SUSPENDED", "NOT_REGISTERED"})


@dataclass(frozen=True, slots=True)
class SlateBuildResult:
    slate_run_id: str
    season_code: str
    row_count: int
    game_count: int
    required_feature_count: int
    schema_compatible: bool
    missing_features: tuple[str, ...]
    input_fingerprint: str
    output_fingerprint: str
    warnings: tuple[str, ...]
    elapsed_seconds: float


def frozen_required_features() -> tuple[str, ...]:
    required = {
        *feature_manifest("CORE_ROTATION")["features"],
        *phase4b_feature_manifest("MINUTES_ROLE")["features"],
        *phase4b_feature_manifest("PRODUCTION")["features"],
    }
    return tuple(sorted(required))


def build_current_slate(
    season_code: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    as_of: datetime | None = None,
    persist: bool = True,
) -> SlateBuildResult:
    """Generate only upcoming rows while executing the frozen historical formulas."""

    started = time.monotonic()
    generated_at = as_of or datetime.now(UTC)
    initialize_database(database_path)
    warnings: list[str] = []
    with connect_database(database_path) as connection:
        candidates, candidate_warnings = _candidate_rows(
            connection, season_code, generated_at
        )
        warnings.extend(candidate_warnings)
        input_fingerprint = _fingerprint_records(candidates)
        if candidates:
            frame = _generate_feature_frame(
                connection, candidates, season_code,
                history_cutoff_time=generated_at,
            )
        else:
            frame = _empty_slate_frame()
        frame = _attach_market(connection, frame, season_code)
        frame = attach_preparation_context(connection, frame, season_code, generated_at)

    frame = _attach_availability_and_opportunity(
        frame, database_path, season_code, generated_at
    )
    frame["generated_at"] = generated_at
    frame["feature_cutoff_time"] = generated_at
    frame["model_feature_cutoff_policy"] = CUTOFF_POLICY
    frame["live_feature_version"] = LIVE_FEATURE_VERSION
    frame["frozen_performance_model_version"] = FROZEN_PERFORMANCE_MODEL_VERSION
    required = frozen_required_features()
    missing = tuple(sorted(set(required) - set(frame.columns)))
    incompatible = tuple(
        column for column in required
        if column in frame.columns
        and column not in feature_manifest("CORE_ROTATION")["categorical"]
        and column not in phase4b_feature_manifest("MINUTES_ROLE")["categorical"]
        and column not in phase4b_feature_manifest("PRODUCTION")["categorical"]
        and len(frame) > 0
        and not pd.api.types.is_numeric_dtype(frame[column])
    )
    if incompatible:
        warnings.append(f"numeric dtype mismatch: {', '.join(incompatible)}")
    schema_compatible = not missing and not incompatible
    output_fingerprint = _fingerprint_frame(
        frame.drop(columns=["generated_at", "feature_cutoff_time"], errors="ignore")
    )
    elapsed = time.monotonic() - started
    slate_run_id = stable_id(
        "live_slate_run", season_code, generated_at.isoformat(), input_fingerprint
    )
    if persist:
        with connect_database(database_path) as connection:
            relation = "_phase5a_current_slate"
            connection.register(relation, frame)
            try:
                connection.execute(
                    f"CREATE OR REPLACE TABLE current_upcoming_player_slate_v1 AS "
                    f"SELECT * FROM {relation}"
                )
                connection.execute(
                    "CREATE OR REPLACE VIEW current_upcoming_player_slate AS "
                    "SELECT * FROM current_upcoming_player_slate_v1"
                )
            finally:
                connection.unregister(relation)
            connection.execute(
                """
                INSERT INTO live_slate_runs (
                  slate_run_id, season_code, generated_at, feature_cutoff_time,
                  row_count, required_feature_count, schema_compatible,
                  input_fingerprint, output_fingerprint, source_freshness_json,
                  warnings_json, elapsed_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [slate_run_id, season_code, generated_at, generated_at, len(frame),
                 len(required), schema_compatible, input_fingerprint,
                 output_fingerprint, json.dumps(warnings), elapsed],
            )
    return SlateBuildResult(
        slate_run_id=slate_run_id,
        season_code=season_code,
        row_count=len(frame),
        game_count=int(frame["game_id"].nunique()) if "game_id" in frame else 0,
        required_feature_count=len(required),
        schema_compatible=schema_compatible,
        missing_features=missing,
        input_fingerprint=input_fingerprint,
        output_fingerprint=output_fingerprint,
        warnings=tuple(warnings),
        elapsed_seconds=elapsed,
    )


def validate_frozen_schema(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    required = frozen_required_features()
    with connect_database(database_path, read_only=True) as connection:
        exists = bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='current_upcoming_player_slate_v1'"
        ).fetchone()[0])
        if not exists:
            return {"compatible": False, "missing": list(required), "row_count": 0}
        columns = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "DESCRIBE current_upcoming_player_slate_v1"
            ).fetchall()
        }
        row_count = int(connection.execute(
            "SELECT count(*) FROM current_upcoming_player_slate_v1"
        ).fetchone()[0])
    missing = sorted(set(required) - set(columns))
    return {
        "compatible": not missing,
        "missing": missing,
        "row_count": row_count,
        "required_feature_count": len(required),
        "column_count": len(columns),
    }


def train_live_parity_check(
    player_game_ids: Iterable[str],
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Recompute historical target rows through the live adapter and compare."""

    ids = tuple(dict.fromkeys(player_game_ids))
    if not ids:
        return {"passed": True, "rows": 0, "mismatches": {}}
    with connect_database(database_path) as connection:
        placeholders = ",".join("?" for _ in ids)
        historical = connection.execute(
            f"SELECT * FROM ml_player_game_core_plus_rich_v1 "
            f"WHERE player_game_id IN ({placeholders}) ORDER BY player_game_id",
            list(ids),
        ).df()
        candidate_rows = [
            {
                "model_row_id": str(row.player_game_id),
                "player_game_id": str(row.player_game_id),
                "season": str(row.season),
                "season_start_year": int(row.season_start_year),
                "round_number": int(row.round_number),
                "game_id": str(row.game_id),
                "game_code": int(row.game_code),
                "target_game_time": row.target_game_time,
                "local_game_date": row.local_game_date,
                "player_id": str(row.player_id),
                "team_id": str(row.team_id),
                "opponent_team_id": str(row.opponent_team_id),
                "home_away": str(row.home_away),
                "competition_stage": "HISTORICAL_PARITY",
                "phase_code": None,
                "scheduled_tip_time": row.target_game_time,
                "game_status": "Played",
                "current_roster_status": "HISTORICAL_PARITY",
            }
            for row in historical.itertuples()
        ]
        recomputed = _generate_feature_frame(
            connection, candidate_rows, max(str(row["season"]) for row in candidate_rows),
            drop_target_ids=ids,
        )
    compare_columns = [
        column for column in frozen_required_features()
        if column not in {"fantasy_position"} and column in historical and column in recomputed
    ]
    left = historical.set_index("player_game_id")
    right = recomputed.set_index("player_game_id")
    mismatches: dict[str, int] = {}
    max_absolute_error = 0.0
    for column in compare_columns:
        if column in {"season", "home_away", "team_id", "opponent_team_id", "player_id"}:
            count = int((left[column].astype("string") != right[column].astype("string")).sum())
        else:
            a = pd.to_numeric(left[column], errors="coerce")
            b = pd.to_numeric(right[column], errors="coerce")
            count = int((~np.isclose(a, b, equal_nan=True, atol=tolerance, rtol=tolerance)).sum())
            comparable = a.notna() & b.notna()
            if comparable.any():
                max_absolute_error = max(
                    max_absolute_error,
                    float((
                        a[comparable].astype(float) - b[comparable].astype(float)
                    ).abs().max()),
                )
        if count:
            mismatches[column] = count
    return {
        "passed": not mismatches,
        "rows": len(ids),
        "mismatches": mismatches,
        "tolerance": tolerance,
        "max_absolute_error": max_absolute_error,
    }


def _candidate_rows(
    connection: Any, season_code: str, as_of: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    games = connection.execute(
        """
        SELECT schedule.canonical_game_id, schedule.game_code, schedule.phase_code,
               schedule.round_number, schedule.scheduled_tip_time,
               schedule.local_game_date, schedule.home_team_id,
               schedule.away_team_id, schedule.game_status
        FROM current_live_schedule AS schedule
        JOIN games AS game USING (canonical_game_id)
        WHERE schedule.season_code=? AND NOT schedule.played
          AND schedule.scheduled_tip_time IS NOT NULL
          AND schedule.scheduled_tip_time >= ?
        ORDER BY schedule.scheduled_tip_time, schedule.game_code
        """,
        [season_code, as_of],
    ).fetchall()
    if games:
        first_round = games[0][3]
        first_phase = games[0][2]
        if first_round is not None:
            games = [
                game for game in games
                if game[3] == first_round and game[2] == first_phase
            ]
        else:
            first_tip = games[0][4]
            games = [game for game in games if game[4] == first_tip]
            warnings.append(
                "earliest upcoming game has no round; slate is limited to its exact tip time"
            )
    missing_tip = int(connection.execute(
        "SELECT count(*) FROM current_live_schedule WHERE season_code=? "
        "AND NOT played AND scheduled_tip_time IS NULL",
        [season_code],
    ).fetchone()[0])
    if missing_tip:
        warnings.append(f"{missing_tip} upcoming games excluded because UTC tip is missing")
    roster = connection.execute(
        """
        SELECT canonical_player_id, canonical_team_id, roster_status
        FROM current_live_roster
        WHERE season_code=? AND roster_status='CURRENT_ROSTER'
        """,
        [season_code],
    ).fetchall()
    if not roster:
        roster = connection.execute(
            """
            SELECT * EXCLUDE (rank_value) FROM (
              SELECT membership.canonical_player_id, membership.canonical_team_id,
                     CASE WHEN coalesce(membership.active, true)
                               AND membership.valid_to IS NULL
                          THEN 'CURRENT_ROSTER' ELSE 'LEFT_TEAM' END AS roster_status,
                     row_number() OVER (
                       PARTITION BY membership.canonical_player_id
                       ORDER BY membership.valid_from DESC NULLS LAST,
                                membership.membership_id DESC
                     ) AS rank_value
              FROM player_team_memberships AS membership
              JOIN seasons AS season USING (season_id)
              WHERE season.season_code=?
            ) WHERE rank_value=1 AND roster_status='CURRENT_ROSTER'
            """,
            [season_code],
        ).fetchall()
        if roster:
            warnings.append("live roster snapshot absent; using same-season canonical membership")
    # Early-season official memberships can cover only teams that have played.
    # Supplement uncovered teams from the current, safely mapped Fantasy market;
    # never replace an official team roster or override an explicit departure.
    covered_teams = {str(row[1]) for row in roster}
    known_players = {str(row[0]) for row in roster}
    for player_id, team_id in connection.execute(
        "SELECT canonical_player_id, canonical_team_id FROM current_live_roster "
        "WHERE season_code=?", [season_code],
    ).fetchall():
        covered_teams.add(str(team_id))
        known_players.add(str(player_id))
    market_roster = connection.execute(
            """
            WITH latest_batch AS (
              SELECT snapshot_batch_id
              FROM fantasy_market_snapshots
              WHERE season_code=?
              ORDER BY observed_at DESC, snapshot_batch_id DESC LIMIT 1
            ), mapping AS (
              SELECT * EXCLUDE (rank_value) FROM (
                SELECT crosswalk.fantasy_entity_id,
                       crosswalk.canonical_player_id,
                       row_number() OVER (
                         PARTITION BY crosswalk.fantasy_entity_id
                         ORDER BY crosswalk.valid_from DESC NULLS LAST,
                                  crosswalk.crosswalk_id DESC
                       ) AS rank_value
                FROM fantasy_player_crosswalk AS crosswalk
                WHERE crosswalk.season_code=?
                  AND crosswalk.mapping_status='MATCHED'
                  AND crosswalk.valid_to IS NULL
              ) WHERE rank_value=1
            )
            SELECT mapping.canonical_player_id, market.canonical_team_id,
                   'CURRENT_ROSTER' AS roster_status
            FROM fantasy_market_snapshots AS market
            JOIN latest_batch USING (snapshot_batch_id)
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            JOIN mapping USING (fantasy_entity_id)
            WHERE market.season_code=? AND entity.entity_type='PLAYER'
              AND market.canonical_team_id IS NOT NULL
            QUALIFY row_number() OVER (
              PARTITION BY mapping.canonical_player_id
              ORDER BY market.observed_at DESC, market.snapshot_record_id DESC
            )=1
            """,
            [season_code, season_code, season_code],
        ).fetchall()
    added = [
        row for row in market_roster
        if str(row[1]) not in covered_teams and str(row[0]) not in known_players
    ]
    if added:
        roster.extend(added)
        warnings.append(
            "official roster coverage incomplete; using safely mapped current Fantasy "
            f"market for {len({row[1] for row in added})} uncovered teams"
        )
    if not roster:
        warnings.append("no current roster/player pool is available; slate is empty")
    players_by_team: dict[str, list[tuple[str, str]]] = {}
    for player_id, team_id, status in roster:
        players_by_team.setdefault(str(team_id), []).append((str(player_id), str(status)))
    candidates: list[dict[str, Any]] = []
    for game in games:
        game_id, game_code, phase, round_number, tip, local, home, away, game_status = game
        for team_id, opponent_id, home_away in (
            (str(home), str(away), "home"), (str(away), str(home), "away")
        ):
            for player_id, roster_status in players_by_team.get(team_id, []):
                row_id = stable_id("live_player_game", game_id, player_id, team_id)
                candidates.append({
                    "model_row_id": row_id,
                    "player_game_id": row_id,
                    "season": season_code,
                    "season_start_year": int(season_code[1:]),
                    "competition_stage": _competition_stage(str(phase or "")),
                    "phase_code": phase,
                    "round_number": int(round_number) if round_number is not None else None,
                    "game_id": str(game_id),
                    "game_code": int(game_code),
                    "target_game_time": tip,
                    "local_game_date": local,
                    "scheduled_tip_time": tip,
                    "game_status": game_status,
                    "player_id": player_id,
                    "team_id": team_id,
                    "opponent_team_id": opponent_id,
                    "home_away": home_away,
                    "current_roster_status": roster_status,
                })
    return candidates, warnings


def _generate_feature_frame(
    connection: Any,
    candidates: list[dict[str, Any]],
    season_code: str,
    *,
    drop_target_ids: Iterable[str] = (),
    history_cutoff_time: datetime | None = None,
) -> pd.DataFrame:
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE ml_player_game_targets_v1 AS "
        "SELECT * FROM main.ml_player_game_targets_v1"
    )
    drop_ids = tuple(drop_target_ids)
    if drop_ids:
        connection.execute(
            f"DELETE FROM ml_player_game_targets_v1 WHERE player_game_id IN "
            f"({','.join('?' for _ in drop_ids)})",
            list(drop_ids),
        )
    if history_cutoff_time is not None:
        connection.execute(
            "DELETE FROM ml_player_game_targets_v1 WHERE target_game_time>=?",
            [history_cutoff_time],
        )
    target_rows = pd.DataFrame([{
        "player_game_id": row["player_game_id"],
        "season": row["season"],
        "season_start_year": row["season_start_year"],
        "round_number": row["round_number"],
        "game_id": row["game_id"],
        "game_code": row["game_code"],
        "target_game_time": row["target_game_time"],
        "local_game_date": row["local_game_date"],
        "feature_cutoff_time": row.get(
            "feature_cutoff_time", history_cutoff_time or row["target_game_time"]
        ),
        "cutoff_policy": CUTOFF_POLICY,
        "player_id": row["player_id"],
        "team_id": row["team_id"],
        "opponent_team_id": row["opponent_team_id"],
        "home_away": row["home_away"],
        "eligibility_status": "PLAYED",
        "did_not_play": False,
        "target_rule_version": "live_no_target",
        "target_rule_status": "LIVE_UPCOMING_NO_OUTCOME",
    } for row in candidates])
    connection.register("_live_targets", target_rows)
    try:
        connection.execute(
            "INSERT INTO ml_player_game_targets_v1 BY NAME SELECT * FROM _live_targets"
        )
    finally:
        connection.unregister("_live_targets")

    connection.execute("CREATE OR REPLACE TEMP TABLE games AS SELECT * FROM main.current_games")
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE team_game_stats AS SELECT * FROM main.team_game_stats"
    )
    game_ids = sorted({row["game_id"] for row in candidates})
    if history_cutoff_time is not None:
        connection.execute(
            "UPDATE games SET played=false WHERE game_date>=?",
            [history_cutoff_time],
        )
    if game_ids:
        connection.execute(
            f"UPDATE games SET played=true WHERE canonical_game_id IN "
            f"({','.join('?' for _ in game_ids)})",
            game_ids,
        )
    team_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (row["game_id"], row["team_id"])
        team_rows[key] = {
            "team_game_id": stable_id("live_team_game", *key),
            "canonical_game_id": row["game_id"],
            "canonical_team_id": row["team_id"],
            "opponent_team_id": row["opponent_team_id"],
            "home_away": row["home_away"],
        }
    existing_team_keys = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            f"SELECT canonical_game_id, canonical_team_id FROM team_game_stats "
            f"WHERE canonical_game_id IN ({','.join('?' for _ in game_ids)})",
            game_ids,
        ).fetchall()
    }
    missing_team_rows = [
        row for key, row in team_rows.items() if key not in existing_team_keys
    ]
    if missing_team_rows:
        team_frame = pd.DataFrame(missing_team_rows)
        connection.register("_live_team_rows", team_frame)
        try:
            connection.execute(
                "INSERT INTO team_game_stats BY NAME SELECT * FROM _live_team_rows"
            )
        finally:
            connection.unregister("_live_team_rows")

    _create_player_features(connection)
    _create_team_features(connection, season_end=season_code)
    _create_core_table(connection, temporary=True, create_view=False)
    _create_rotation_features(connection, temporary=True)
    ids = [row["player_game_id"] for row in candidates]
    result = connection.execute(
        f"""
        SELECT core.*, rotation.* EXCLUDE (player_game_id)
        FROM ml_player_game_core_features_v1 AS core
        JOIN ml_player_game_rotation_features_v1 AS rotation USING (player_game_id)
        WHERE core.player_game_id IN ({','.join('?' for _ in ids)})
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """,
        ids,
    ).df()
    metadata = pd.DataFrame(candidates).set_index("player_game_id")
    result = result.set_index("player_game_id")
    for column in (
        "competition_stage", "phase_code", "scheduled_tip_time", "game_status",
        "current_roster_status", "model_row_id",
    ):
        if column in metadata:
            result[column] = metadata[column]
    result = result.reset_index()
    result["role_change_score"] = role_change_score(result)
    return result


def _attach_market(connection: Any, frame: pd.DataFrame, season_code: str) -> pd.DataFrame:
    if frame.empty:
        if "fantasy_position" not in frame:
            frame["fantasy_position"] = pd.Series(dtype="string")
        if "current_fantasy_credits" not in frame:
            frame["current_fantasy_credits"] = pd.Series(dtype="float64")
        return frame
    market = connection.execute(
        """
        WITH mapping AS (
          SELECT * EXCLUDE (rank_value) FROM (
            SELECT crosswalk.*,
                   row_number() OVER (
                     PARTITION BY fantasy_entity_id, season_code
                     ORDER BY CASE WHEN mapping_status='MATCHED' THEN 0 ELSE 1 END,
                              valid_from DESC NULLS LAST, crosswalk_id DESC
                   ) AS rank_value
            FROM fantasy_player_crosswalk AS crosswalk WHERE season_code=?
          ) WHERE rank_value=1
        ), latest_batch AS (
          SELECT snapshot_batch_id
          FROM fantasy_market_snapshots
          WHERE season_code=?
          GROUP BY snapshot_batch_id
          ORDER BY max(observed_at) DESC, snapshot_batch_id DESC
          LIMIT 1
        )
        SELECT mapping.canonical_player_id AS player_id,
               market.fantasy_entity_id AS fantasy_id,
               entity.fantasy_id AS fantasy_player_id,
               market.position_name AS fantasy_position,
               market.credits AS current_fantasy_credits,
               market.matchday_id AS fantasy_matchday_id,
               market.matchday_number AS fantasy_matchday,
               market.snapshot_batch_id AS fantasy_snapshot_batch_id,
               market.observed_at AS fantasy_captured_at,
               market.overlay_is_injured AS fantasy_is_injured,
               market.overlay_probability_of_playing AS fantasy_probability_of_playing,
               market.overlay_started_from_bench AS fantasy_bench,
               market.overlay_is_on_fire AS fantasy_on_fire,
               market.overlay_popularity AS fantasy_popularity
        FROM fantasy_market_snapshots AS market
        JOIN latest_batch USING (snapshot_batch_id)
        JOIN fantasy_entities AS entity USING (fantasy_entity_id)
        JOIN mapping USING (fantasy_entity_id, season_code)
        WHERE market.season_code=? AND mapping.mapping_status='MATCHED'
          AND entity.entity_type='PLAYER'
        """,
        [season_code, season_code, season_code],
    ).df()
    if market.empty:
        frame["fantasy_position"] = pd.Series(pd.NA, index=frame.index, dtype="string")
        frame["current_fantasy_credits"] = np.nan
        return frame
    return frame.merge(market, on="player_id", how="left", validate="many_to_one")


def _attach_availability_and_opportunity(
    frame: pd.DataFrame,
    database_path: Path | str,
    season_code: str,
    as_of: datetime,
) -> pd.DataFrame:
    if frame.empty:
        for column, dtype in (
            ("resolved_availability_status", "string"),
            ("availability_source", "string"),
            ("availability_timestamp", "datetime64[ns, UTC]"),
            ("manual_override_active", "boolean"),
            ("availability_conflict", "boolean"),
        ):
            frame[column] = pd.Series(dtype=dtype)
        for column in _opportunity_columns():
            frame[column] = pd.Series(dtype="float64")
        return frame
    resolver = AvailabilityResolver(database_path)
    resolved = []
    for row in frame.itertuples():
        item = resolver.resolve(
            str(row.player_id), season_code=season_code, game_id=str(row.game_id),
            matchday_number=(
                int(row.fantasy_matchday)
                if hasattr(row, "fantasy_matchday") and pd.notna(row.fantasy_matchday)
                else int(row.round_number) if pd.notna(row.round_number) else None
            ),
            tip_time=row.scheduled_tip_time,
            as_of=as_of,
        )
        resolved.append({
            "player_game_id": row.player_game_id,
            "resolved_availability_status": item.status,
            "availability_source": item.source,
            "availability_timestamp": item.captured_at,
            "manual_override_active": item.manual_override_active,
            "availability_conflict": item.conflict,
            "availability_conflicting_statuses": "|".join(item.conflicting_statuses),
        })
    frame = frame.merge(pd.DataFrame(resolved), on="player_game_id", how="left")
    frame["player_historical_role_rank"] = frame.groupby(
        ["game_id", "team_id"], sort=False
    )["last_5_minutes_avg"].rank(method="min", ascending=False)
    frame["player_recent_minutes_share"] = frame["last_5_minutes_avg"] / frame.groupby(
        ["game_id", "team_id"], sort=False
    )["last_5_minutes_avg"].transform("sum").replace(0, np.nan)
    unavailable = frame["resolved_availability_status"].isin(UNAVAILABLE_STATUSES)
    frame["_missing_last5"] = frame["last_5_minutes_avg"].where(unavailable, 0).fillna(0)
    frame["_missing_season"] = frame["season_minutes_avg_before"].where(unavailable, 0).fillna(0)
    frame["_missing_starts"] = frame["last_5_start_rate"].where(unavailable, 0).fillna(0)
    frame["_missing_starter_minutes"] = (
        frame["season_minutes_avg_before"].fillna(0)
        * frame["last_5_start_rate"].fillna(0)
    ).where(unavailable, 0)
    frame["_unavailable"] = unavailable.astype(int)
    frame["_rotation_player"] = frame["player_historical_role_rank"].le(10)
    frame["_available_rotation"] = frame["_rotation_player"] & ~unavailable
    frame["_missing_rotation"] = frame["_rotation_player"] & unavailable
    groups = frame.groupby(["game_id", "team_id"], sort=False)
    frame["number_of_teammates_out"] = (
        groups["_unavailable"].transform("sum") - unavailable.astype(int)
    )
    frame["number_of_rotation_players_out"] = groups["_missing_rotation"].transform("sum")
    frame["available_team_rotation_players"] = groups["_available_rotation"].transform("sum")
    frame["missing_recent_team_minutes"] = groups["_missing_last5"].transform("sum")
    frame["missing_recent_starter_minutes"] = groups[
        "_missing_starter_minutes"
    ].transform("sum")
    team_last5 = groups["last_5_minutes_avg"].transform("sum").replace(0, np.nan)
    team_season = groups["season_minutes_avg_before"].transform("sum").replace(0, np.nan)
    frame["team_missing_last5_minutes_share"] = (
        frame["missing_recent_team_minutes"] / team_last5
    )
    frame["team_missing_season_minutes_share"] = (
        groups["_missing_season"].transform("sum") / team_season
    )
    frame["team_missing_recent_starts"] = groups["_missing_starts"].transform("sum")
    return frame.drop(columns=[
        "_missing_last5", "_missing_season", "_missing_starts",
        "_missing_starter_minutes", "_unavailable", "_rotation_player",
        "_available_rotation", "_missing_rotation",
    ])


def _opportunity_columns() -> tuple[str, ...]:
    return (
        "number_of_teammates_out", "number_of_rotation_players_out",
        "available_team_rotation_players", "missing_recent_team_minutes",
        "missing_recent_starter_minutes", "team_missing_last5_minutes_share",
        "team_missing_season_minutes_share", "team_missing_recent_starts",
        "player_historical_role_rank", "player_recent_minutes_share",
    )


def _empty_slate_frame() -> pd.DataFrame:
    categorical = {
        "season", "home_away", "team_id", "opponent_team_id", "player_id",
        "fantasy_position", "game_id", "player_game_id", "model_row_id",
        "competition_stage", "phase_code", "game_status", "current_roster_status",
    }
    columns = {
        *frozen_required_features(), *categorical,
        "round_number", "game_code", "target_game_time", "scheduled_tip_time",
        "local_game_date", "role_change_score", "fantasy_matchday",
        "fantasy_matchday_id", "fantasy_snapshot_batch_id", "fantasy_player_id",
    }
    return pd.DataFrame({
        column: pd.Series(dtype="string" if column in categorical else "float64")
        for column in sorted(columns)
    })


def _competition_stage(phase_code: str) -> str:
    return {
        "RS": "REGULAR_SEASON", "PI": "PLAY_IN", "PO": "PLAYOFFS",
        "FF": "FINAL_FOUR",
    }.get(phase_code.replace(" ", "").upper(), "UNKNOWN")


def _fingerprint_records(rows: list[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: str(item.get("player_game_id"))):
        digest.update(json.dumps(row, default=str, sort_keys=True).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _fingerprint_frame(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    columns = sorted(frame.columns)
    if frame.empty:
        digest.update("\n".join(columns).encode())
        return digest.hexdigest()
    ordered = frame.sort_values(
        [column for column in ("game_id", "team_id", "player_id") if column in frame]
    )
    for row in ordered[columns].itertuples(index=False, name=None):
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()
