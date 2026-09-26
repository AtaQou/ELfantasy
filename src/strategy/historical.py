"""Strict-cutoff E2025 market reconstruction and frozen Phase 6C inference."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.db.ids import stable_id
from src.live.prediction_artifacts import load_and_validate_frozen_artifacts
from src.live.predictive_uplift_artifacts import load_and_validate_predictive_uplift_artifact
from src.live.probabilistic_artifacts import load_and_validate_probabilistic_artifact
from src.modeling.core_features import (
    CUTOFF_POLICY,
    _create_core_table,
    _create_player_features,
    _create_team_features,
)
from src.modeling.phase4b_protocol import role_change_score
from src.modeling.rich_features import _create_rotation_features
from src.strategy.rules import normalize_position


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE7_RESEARCH_ROOT = PROJECT_ROOT / "data" / "derived" / "phase7" / "research"
PRICE_AWARE_REPLAY_SEASONS = ("E2025",)


def load_historical_market(
    season: str,
    matchday: int,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    if str(season) not in PRICE_AWARE_REPLAY_SEASONS:
        raise ValueError(f"{season} is not validated for point-in-time price-aware replay")
    with connect_database(database_path, read_only=True) as connection:
        market = connection.execute(
            """
            WITH mapping AS (
              SELECT * EXCLUDE(rank_value) FROM (
                SELECT crosswalk.*,
                       row_number() OVER (
                         PARTITION BY fantasy_entity_id
                         ORDER BY CASE WHEN mapping_status='MATCHED' THEN 0 ELSE 1 END,
                                  valid_from DESC NULLS LAST, crosswalk_id DESC
                       ) AS rank_value
                FROM fantasy_player_crosswalk AS crosswalk
                WHERE season_code=?
              ) WHERE rank_value=1
            )
            SELECT market.fantasy_entity_id AS entity_id,
                   entity.entity_type,
                   entity.display_name AS name,
                   mapping.canonical_player_id AS player_id,
                   market.canonical_team_id AS team_id,
                   market.position_name AS position,
                   market.credits,
                   market.price_semantics,
                   market.position_semantics,
                   market.status_semantics,
                   market.snapshot_batch_id,
                   market.source_artifact_id
            FROM fantasy_market_snapshots AS market
            JOIN fantasy_entities AS entity USING(fantasy_entity_id)
            LEFT JOIN mapping USING(fantasy_entity_id)
            WHERE market.season_code=? AND market.matchday_number=?
            ORDER BY entity.entity_type, market.fantasy_entity_id
            """,
            [season, season, int(matchday)],
        ).df()
    market["coach_id"] = np.where(
        market["entity_type"].astype(str).str.upper().eq("COACH"),
        market["entity_id"], pd.NA,
    )
    players = market["entity_type"].astype(str).str.upper().eq("PLAYER")
    market.loc[players & market["position"].notna(), "position"] = market.loc[
        players & market["position"].notna(), "position"
    ].map(normalize_position)
    return market


def build_historical_player_predictions(
    season: str,
    matchday: int,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Score the full historical market from information strictly before T1."""

    market = load_historical_market(season, matchday, database_path=database_path)
    player_market = market[
        market["entity_type"].astype(str).str.upper().eq("PLAYER")
        & market["player_id"].notna()
    ].copy()
    with connect_database(database_path) as connection:
        games = connection.execute(
            """
            SELECT * FROM games
            WHERE season_code=? AND round_number=? AND phase_code='RS'
            ORDER BY game_date, canonical_game_id
            """,
            [season, int(matchday)],
        ).df()
        if games.empty:
            raise ValueError(f"no games for {season} Matchday {matchday}")
        cutoff = games["game_date"].min().to_pydatetime() - timedelta(microseconds=1)
        local_dates = sorted(pd.to_datetime(games["local_game_date"]).dt.date.unique())
        turn_by_date = {value: index + 1 for index, value in enumerate(local_dates)}
        game_by_team: dict[str, tuple[Any, str, str]] = {}
        for game in games.itertuples(index=False):
            game_by_team[str(game.home_team_id)] = (game, "home", str(game.away_team_id))
            game_by_team[str(game.away_team_id)] = (game, "away", str(game.home_team_id))
        candidates: list[dict[str, Any]] = []
        for row in player_market.itertuples(index=False):
            assignment = game_by_team.get(str(row.team_id))
            if assignment is None:
                continue
            game, home_away, opponent = assignment
            player_game_id = stable_id(
                "phase7_historical_player_game", season, matchday,
                str(row.player_id), str(game.canonical_game_id),
            )
            local_date = pd.Timestamp(game.local_game_date).date()
            candidates.append({
                "model_row_id": player_game_id,
                "player_game_id": player_game_id,
                "season": season,
                "season_start_year": int(str(season)[1:]),
                "competition_stage": "REGULAR_SEASON",
                "phase_code": "RS",
                "round_number": int(matchday),
                "game_id": str(game.canonical_game_id),
                "game_code": int(game.game_code),
                "target_game_time": game.game_date,
                "local_game_date": game.local_game_date,
                "scheduled_tip_time": game.game_date,
                "game_status": "HISTORICAL_REPLAY_TARGET",
                "player_id": str(row.player_id),
                "team_id": str(row.team_id),
                "opponent_team_id": opponent,
                "home_away": home_away,
                "current_roster_status": "HISTORICAL_MARKET",
                "feature_cutoff_time": cutoff,
                "entity_id": str(row.entity_id),
                "name": str(row.name),
                "position": str(row.position),
                "credits": float(row.credits),
                "turn": int(turn_by_date[local_date]),
            })
        actual = connection.execute(
            """
            SELECT player_id, game_id,
                   max(actual_fantasy_points) FILTER(WHERE target_minutes>0)
                     AS actual_fp_if_played,
                   max(target_minutes) AS actual_minutes
            FROM main.ml_player_game_targets_v1
            WHERE season=? AND round_number=?
            GROUP BY player_id, game_id
            """,
            [season, int(matchday)],
        ).df()
        feature_frame = _generate_strict_historical_features(
            connection, candidates, season, cutoff
        )
    metadata = pd.DataFrame(candidates)[[
        "player_game_id", "entity_id", "name", "position", "credits", "turn",
    ]]
    feature_frame = feature_frame.merge(metadata, on="player_game_id", how="left")
    feature_frame["fantasy_position"] = feature_frame["position"].str.title()
    performance, performance_validation = load_and_validate_frozen_artifacts()
    probabilistic, probabilistic_validation = load_and_validate_probabilistic_artifact()
    predictive, predictive_validation = load_and_validate_predictive_uplift_artifact()
    feature_frame = predictive.prepare_features(feature_frame, database_path=database_path)
    components = performance.predict_components(feature_frame)
    for column in components:
        feature_frame[column] = components[column]
    feature_frame["baseline_expected_minutes"] = feature_frame[
        "baseline_expected_minutes_if_available"
    ]
    feature_frame["direct_fp"] = feature_frame["direct_prediction"]
    feature_frame["predicted_fp_per_min"] = feature_frame["expected_fp_per_min"]
    central = (
        performance.weight_direct * feature_frame["direct_prediction"]
        + performance.weight_decomposed
        * feature_frame["baseline_expected_minutes_if_available"]
        * feature_frame["expected_fp_per_min"]
    )
    feature_frame["expected_fp_before_absence_adjustment"] = central
    feature_frame["expected_fp_after_absence_adjustment"] = central
    phase6b = probabilistic.predict_distribution(feature_frame)
    distribution, enriched = predictive.predict_distribution(
        feature_frame, phase6b, database_path=database_path
    )
    overlapping = [column for column in distribution if column in feature_frame]
    output = pd.concat([
        feature_frame.drop(columns=overlapping).reset_index(drop=True),
        distribution.reset_index(drop=True),
    ], axis=1)
    output["expected_fp"] = output["phase6c_expected_fp"]
    output = output.merge(actual, on=["player_id", "game_id"], how="left")
    output["actual_minutes"] = pd.to_numeric(output["actual_minutes"], errors="coerce").fillna(0.0)
    output["actual_fp"] = pd.to_numeric(
        output["actual_fp_if_played"], errors="coerce"
    ).fillna(0.0)
    output["played"] = output["actual_minutes"].gt(0)
    output["prediction_cutoff"] = cutoff
    output["feature_source_max_time"] = cutoff
    output["predictive_artifact_fingerprint"] = predictive_validation.artifact_fingerprint
    output["probabilistic_artifact_fingerprint"] = (
        probabilistic_validation.artifact_fingerprint
    )
    output["performance_artifact_fingerprint"] = (
        performance_validation.performance_artifact_fingerprint
    )
    audit = {
        "season": season,
        "matchday": int(matchday),
        "cutoff": cutoff.isoformat(),
        "market_player_rows": int((market.entity_type.str.upper() == "PLAYER").sum()),
        "mapped_prediction_rows": len(output),
        "eventual_dnp_rows_retained": int((~output["played"]).sum()),
        "max_feature_time_strictly_before_cutoff": bool(
            pd.to_datetime(output["feature_source_max_time"], utc=True).le(cutoff).all()
        ),
        "conditional_on_playing_predictions": True,
        "actual_dnp_scored_zero_only_at_replay": True,
        "input_fingerprint": _frame_fingerprint(metadata),
    }
    return output, audit


def cache_historical_predictions(
    season: str = "E2025",
    *,
    matchdays: range | list[int] = range(1, 39),
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    output_root: Path | str = DEFAULT_PHASE7_RESEARCH_ROOT,
) -> tuple[Path, Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    prediction_path = root / f"{season.lower()}_full_market_predictions.parquet"
    audit_path = root / f"{season.lower()}_replay_cutoff_audit.json"
    frames: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    for matchday in matchdays:
        frame, audit = build_historical_player_predictions(
            season, int(matchday), database_path=database_path
        )
        frames.append(frame); audits.append(audit)
    combined = pd.concat(frames, ignore_index=True)
    temporary = duckdb.connect()
    temporary.register("phase7_predictions", combined)
    try:
        temporary.execute(
            "COPY phase7_predictions TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(prediction_path)],
        )
    finally:
        temporary.close()
    payload = {
        "season": season,
        "matchdays": audits,
        "rows": len(combined),
        "prediction_fingerprint": _frame_fingerprint(combined[[
            "player_game_id", "expected_fp", "p10_fp", "p50_fp", "p90_fp",
            "prob_fp_le_10", "prob_fp_ge_30", "prediction_cutoff",
        ]]),
    }
    audit_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return prediction_path, audit_path


def read_cached_predictions(
    season: str,
    *,
    path: Path | str | None = None,
) -> pd.DataFrame:
    source = Path(path) if path is not None else (
        DEFAULT_PHASE7_RESEARCH_ROOT / f"{season.lower()}_full_market_predictions.parquet"
    )
    if not source.is_file():
        raise FileNotFoundError(f"Phase 7 historical predictions are not cached: {source}")
    connection = duckdb.connect()
    try:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(source)]).df()
    finally:
        connection.close()


def _generate_strict_historical_features(
    connection: Any,
    candidates: list[dict[str, Any]],
    season: str,
    cutoff: Any,
) -> pd.DataFrame:
    """Historical equivalent of live feature generation with target stats removed."""

    connection.execute(
        "CREATE OR REPLACE TEMP TABLE ml_player_game_targets_v1 AS "
        "SELECT * FROM main.ml_player_game_targets_v1"
    )
    connection.execute("DELETE FROM ml_player_game_targets_v1 WHERE target_game_time>=?", [cutoff])
    target_rows = pd.DataFrame([{
        "player_game_id": row["player_game_id"], "season": row["season"],
        "season_start_year": row["season_start_year"], "round_number": row["round_number"],
        "game_id": row["game_id"], "game_code": row["game_code"],
        "target_game_time": row["target_game_time"], "local_game_date": row["local_game_date"],
        "feature_cutoff_time": cutoff, "cutoff_policy": CUTOFF_POLICY,
        "player_id": row["player_id"], "team_id": row["team_id"],
        "opponent_team_id": row["opponent_team_id"], "home_away": row["home_away"],
        "eligibility_status": "PLAYED", "did_not_play": False,
        "target_rule_version": "phase7_historical_no_target",
        "target_rule_status": "HISTORICAL_REPLAY_NO_OUTCOME",
    } for row in candidates])
    connection.register("_phase7_targets", target_rows)
    try:
        connection.execute(
            "INSERT INTO ml_player_game_targets_v1 BY NAME SELECT * FROM _phase7_targets"
        )
    finally:
        connection.unregister("_phase7_targets")
    connection.execute("CREATE OR REPLACE TEMP TABLE games AS SELECT * FROM main.games")
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE team_game_stats AS SELECT * FROM main.team_game_stats"
    )
    connection.execute("UPDATE games SET played=false WHERE game_date>=?", [cutoff])
    game_ids = sorted({row["game_id"] for row in candidates})
    placeholders = ",".join("?" for _ in game_ids)
    connection.execute(
        f"DELETE FROM team_game_stats WHERE canonical_game_id IN ({placeholders})", game_ids
    )
    connection.execute(
        f"UPDATE games SET played=true WHERE canonical_game_id IN ({placeholders})", game_ids
    )
    team_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (row["game_id"], row["team_id"])
        team_rows[key] = {
            "team_game_id": stable_id("phase7_team_game", *key),
            "canonical_game_id": row["game_id"],
            "canonical_team_id": row["team_id"],
            "opponent_team_id": row["opponent_team_id"],
            "home_away": row["home_away"],
        }
    team_frame = pd.DataFrame(team_rows.values())
    connection.register("_phase7_team_rows", team_frame)
    try:
        connection.execute("INSERT INTO team_game_stats BY NAME SELECT * FROM _phase7_team_rows")
    finally:
        connection.unregister("_phase7_team_rows")
    _create_player_features(connection)
    _create_team_features(connection, season_end=season)
    _create_core_table(connection, temporary=True, create_view=False)
    _create_rotation_features(connection, temporary=True)
    ids = [row["player_game_id"] for row in candidates]
    id_placeholders = ",".join("?" for _ in ids)
    output = connection.execute(
        f"""
        SELECT core.*, rotation.* EXCLUDE(player_game_id)
        FROM ml_player_game_core_features_v1 AS core
        JOIN ml_player_game_rotation_features_v1 AS rotation USING(player_game_id)
        WHERE core.player_game_id IN ({id_placeholders})
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """,
        ids,
    ).df()
    output["competition_stage"] = "REGULAR_SEASON"
    output["phase_code"] = "RS"
    output["model_row_id"] = output["player_game_id"]
    output["scheduled_tip_time"] = output["target_game_time"]
    output["game_status"] = "HISTORICAL_REPLAY_TARGET"
    output["current_roster_status"] = "HISTORICAL_MARKET"
    output["role_change_score"] = role_change_score(output)
    return output


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    columns = sorted(frame.columns)
    ordered = frame[columns].astype(str).sort_values(columns).reset_index(drop=True)
    for row in ordered.itertuples(index=False, name=None):
        digest.update(json.dumps(row, separators=(",", ":"), default=str).encode())
        digest.update(b"\n")
    return digest.hexdigest()
