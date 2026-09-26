"""Leakage-safe game-environment and player stat-line data for Phase 9A."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH

from .phase9a_protocol import (
    AVAILABILITY_FEATURES,
    BASE_PLAYER_FEATURES,
    GAME_ENVIRONMENT_TARGETS,
    INTERACTION_FEATURES,
    MADE_COMPONENTS,
    OPPORTUNITY_COMPONENTS,
    PBP_FEATURE_FAMILIES,
    PBP_STYLE_TARGETS,
    PRIMARY_SEASONS,
    require_columns,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE6C_FEATURE_FRAME = (
    PROJECT_ROOT / "data" / "derived" / "phase6c" / "research"
    / "feature_frame.parquet"
)
PHASE6C_OUTER_PREDICTIONS = (
    PROJECT_ROOT / "data" / "derived" / "phase6c" / "research"
    / "outer_predictions.parquet"
)
SELECTED_MINUTES_EXPERIMENT = "p4b__minutes__catboost__role_older_history"


def load_phase9a_player_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *, feature_path: Path = PHASE6C_FEATURE_FRAME,
) -> pd.DataFrame:
    """Reuse frozen row identities and attach only permitted local targets/context."""

    with duckdb.connect() as connection:
        frame = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY target_game_time,game_id,player_id",
            [str(feature_path)],
        ).df()
    frame["target_game_time"] = pd.to_datetime(frame["target_game_time"], utc=True)
    frame["feature_cutoff_time"] = pd.to_datetime(frame["feature_cutoff_time"], utc=True)
    frame = frame[frame["season"].isin(PRIMARY_SEASONS)].copy()

    target_columns = [
        "player_game_id", "points", *MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS,
    ]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        stats = connection.execute(
            "SELECT " + ",".join(target_columns) + " FROM player_game_stats"
        ).df()
        games = connection.execute(
            "SELECT canonical_game_id AS game_id,"
            "CASE WHEN home_score>away_score THEN home_team_id "
            "WHEN away_score>home_score THEN away_team_id END AS winner_team_id "
            "FROM games"
        ).df()
        availability = connection.execute(
            "SELECT model_row_id,relationship_history_max_time,"
            + ",".join(AVAILABILITY_FEATURES)
            + " FROM ml_phase5b_absence_recipient_rows_v1"
        ).df()
        minutes = connection.execute(
            "SELECT model_row_id,predicted_minutes AS phase4b_expected_minutes "
            "FROM ml_phase4b_minutes_predictions_v1 "
            "WHERE experiment_id=? AND prediction_role='outer_test'",
            [SELECTED_MINUTES_EXPERIMENT],
        ).df()
    if stats["player_game_id"].duplicated().any():
        raise ValueError("duplicate player stat targets")
    frame = frame.merge(stats, on="player_game_id", how="left", validate="one_to_one")
    frame = frame.merge(games, on="game_id", how="left", validate="many_to_one")
    frame["team_won"] = frame["team_id"].eq(frame["winner_team_id"])
    frame = frame.drop(columns="winner_team_id")
    frame = frame.merge(availability, on="model_row_id", how="left", validate="one_to_one")
    frame = frame.merge(minutes, on="model_row_id", how="left", validate="one_to_one")
    for column in AVAILABILITY_FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    relationship_time = pd.to_datetime(
        frame["relationship_history_max_time"], utc=True, errors="coerce"
    )
    if (relationship_time.notna() & (relationship_time >= frame["feature_cutoff_time"])).any():
        raise ValueError("Phase 5B relationship context reaches target-game cutoff")
    require_columns(
        [*BASE_PLAYER_FEATURES, *MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS],
        frame.columns,
    )
    target_values = frame[[*MADE_COMPONENTS, *OPPORTUNITY_COMPONENTS]].apply(
        pd.to_numeric, errors="coerce"
    )
    if target_values.isna().any().any() or (target_values < 0).any().any():
        raise ValueError("Phase 9A stat-line targets are missing or negative")
    validate_stat_targets(frame)
    if frame["model_row_id"].duplicated().any() or len(frame) != 29232:
        raise ValueError("Phase 9A changed the frozen Phase 6C population")
    if not (frame["feature_cutoff_time"] <= frame["target_game_time"]).all():
        raise ValueError("Phase 9A player cutoff occurs after target game")
    return frame.reset_index(drop=True)


def validate_stat_targets(frame: pd.DataFrame) -> None:
    for made, attempted in (
        ("two_points_made", "two_points_attempted"),
        ("three_points_made", "three_points_attempted"),
        ("free_throws_made", "free_throws_attempted"),
    ):
        if (pd.to_numeric(frame[made]) > pd.to_numeric(frame[attempted])).any():
            raise ValueError(f"invalid stat target: {made}>{attempted}")
    points = (
        2 * pd.to_numeric(frame["two_points_made"])
        + 3 * pd.to_numeric(frame["three_points_made"])
        + pd.to_numeric(frame["free_throws_made"])
    )
    if not np.array_equal(points.to_numpy(), pd.to_numeric(frame["points"]).to_numpy()):
        raise ValueError("box-score points do not reconstruct from made shots")


def load_phase6c_baseline(
    *, prediction_path: Path = PHASE6C_OUTER_PREDICTIONS,
) -> pd.DataFrame:
    with duckdb.connect() as connection:
        return connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY outer_fold,model_row_id",
            [str(prediction_path)],
        ).df()


def build_game_environment_frame(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Build measurable team-game outcomes and strictly shifted histories."""

    query = """
    WITH team AS (
      SELECT g.canonical_game_id AS game_id,g.season_code AS season,
        g.game_date AS game_time,g.round_number,t.canonical_team_id AS team_id,
        t.opponent_team_id,CASE WHEN t.home_away='home' THEN 1.0 ELSE 0.0 END home_game,
        t.points,t.two_points_made,t.two_points_attempted,t.three_points_made,
        t.three_points_attempted,t.free_throws_made,t.free_throws_attempted,
        t.offensive_rebounds,t.defensive_rebounds,t.assists,t.turnovers,t.fouls_drawn
      FROM team_game_stats t JOIN games g ON g.canonical_game_id=t.canonical_game_id
      WHERE g.played AND g.game_date IS NOT NULL AND g.season_code BETWEEN 'E2021' AND 'E2025'
    ), player_concentration AS (
      SELECT p.canonical_game_id AS game_id,p.canonical_team_id AS team_id,
        CASE WHEN sum(p.assists)>0 THEN sum(pow(p.assists,2))/pow(sum(p.assists),2) END assist_hhi,
        CASE WHEN sum(p.two_points_attempted+p.three_points_attempted)>0 THEN
          sum(pow(p.two_points_attempted+p.three_points_attempted,2))
          /pow(sum(p.two_points_attempted+p.three_points_attempted),2) END fga_hhi
      FROM player_game_stats p WHERE p.minutes>0 GROUP BY 1,2
    ), shot_flags AS (
      SELECT canonical_game_id AS game_id,canonical_team_id AS team_id,
        avg(CASE WHEN fast_break THEN 1.0 ELSE 0.0 END) transition_shot_rate,
        avg(CASE WHEN points_off_turnover THEN 1.0 ELSE 0.0 END) turnover_generated_shot_rate,
        avg(CASE WHEN second_chance THEN 1.0 ELSE 0.0 END) second_chance_shot_rate
      FROM shots WHERE points_value IN (2,3) GROUP BY 1,2
    ), profile AS (
      SELECT game_id,shooting_team_id AS team_id,
        rim_attempts/nullif(attempts,0) rim_attempt_rate,
        paint_attempts/nullif(attempts,0) paint_attempt_rate,
        midrange_attempts/nullif(attempts,0) midrange_attempt_rate,
        corner3_attempts/nullif(attempts,0) corner_three_attempt_rate,
        above_break3_attempts/nullif(attempts,0) above_break_three_attempt_rate
      FROM team_game_shot_profile_observations_v1
    )
    SELECT team.game_id,team.season,team.game_time,team.round_number,
      team.team_id,team.opponent_team_id,team.home_game,
      ((team.two_points_attempted+team.three_points_attempted-team.offensive_rebounds
        +team.turnovers+0.44*team.free_throws_attempted)
       +(opp.two_points_attempted+opp.three_points_attempted-opp.offensive_rebounds
        +opp.turnovers+0.44*opp.free_throws_attempted))/2.0 AS game_possessions,
      team.points AS team_points,opp.points AS opponent_points,
      team.two_points_attempted+team.three_points_attempted AS team_fga,
      team.two_points_attempted AS team_two_pa,
      team.three_points_attempted AS team_three_pa,
      team.free_throws_attempted AS team_fta,team.assists AS team_assists,
      team.turnovers AS team_turnovers,team.offensive_rebounds AS team_offensive_rebounds,
      (opp.two_points_attempted+opp.three_points_attempted
       -opp.two_points_made-opp.three_points_made
       +opp.free_throws_attempted-opp.free_throws_made)
        AS team_defensive_rebounds_available,
      team.fouls_drawn AS team_fouls_drawn,
      least(team.assists/nullif(team.two_points_made+team.three_points_made,0),1.0)
        AS assisted_field_goal_rate,
      (coalesce(concentration.assist_hhi,0)+coalesce(concentration.fga_hhi,0))/2.0
        AS creation_concentration,
      team.three_points_attempted/nullif(team.two_points_attempted+team.three_points_attempted,0)
        AS three_point_attempt_rate,
      flags.transition_shot_rate,flags.turnover_generated_shot_rate,
      flags.second_chance_shot_rate,profile.rim_attempt_rate,profile.paint_attempt_rate,
      profile.midrange_attempt_rate,profile.corner_three_attempt_rate,
      profile.above_break_three_attempt_rate
    FROM team JOIN team opp ON opp.game_id=team.game_id AND opp.team_id=team.opponent_team_id
    LEFT JOIN player_concentration concentration
      ON concentration.game_id=team.game_id AND concentration.team_id=team.team_id
    LEFT JOIN shot_flags flags
      ON flags.game_id=team.game_id AND flags.team_id=team.team_id
    LEFT JOIN profile
      ON profile.game_id=team.game_id AND profile.team_id=team.team_id
    ORDER BY team.game_time,team.game_id,team.team_id
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        frame = connection.execute(query).df()
    frame["game_time"] = pd.to_datetime(frame["game_time"], utc=True)
    target_columns = [*GAME_ENVIRONMENT_TARGETS, *PBP_STYLE_TARGETS]
    frame[target_columns] = frame[target_columns].apply(pd.to_numeric, errors="coerce")
    if frame[list(GAME_ENVIRONMENT_TARGETS)].isna().any().any():
        raise ValueError("core game-environment targets are incomplete")
    if frame[["game_id", "team_id"]].duplicated().any():
        raise ValueError("duplicate team-game environment rows")
    frame = add_shifted_environment_history(frame, target_columns)
    validate_environment_cutoffs(frame)
    return frame.reset_index(drop=True)


def add_shifted_environment_history(
    frame: pd.DataFrame, target_columns: Iterable[str],
) -> pd.DataFrame:
    work = frame.sort_values(["season", "team_id", "game_time", "game_id"]).copy()
    group = work.groupby(["season", "team_id"], sort=False)
    history_columns: list[str] = []
    for target in target_columns:
        season_column = f"env_{target}_season_before"
        last5_column = f"env_{target}_last5"
        work[season_column] = group[target].transform(
            lambda values: values.expanding().mean().shift(1)
        )
        work[last5_column] = group[target].transform(
            lambda values: values.shift(1).rolling(5, min_periods=1).mean()
        )
        history_columns.extend((season_column, last5_column))
    work["env_source_max_game_time"] = group["game_time"].shift(1)
    opponent = work[["game_id", "team_id", *history_columns, "env_source_max_game_time"]].rename(
        columns={
            "team_id": "opponent_team_id",
            **{column: f"opp_{column}" for column in history_columns},
            "env_source_max_game_time": "opp_env_source_max_game_time",
        }
    )
    work = work.merge(
        opponent, on=["game_id", "opponent_team_id"], how="left", validate="one_to_one"
    )
    return work.sort_values(["game_time", "game_id", "team_id"]).reset_index(drop=True)


def environment_feature_columns(*, pbp_family: str | None = None) -> list[str]:
    columns = ["round_number", "home_game"]
    for target in GAME_ENVIRONMENT_TARGETS:
        columns.extend((
            f"env_{target}_season_before", f"env_{target}_last5",
            f"opp_env_{target}_season_before", f"opp_env_{target}_last5",
        ))
    if pbp_family is not None:
        if pbp_family not in PBP_FEATURE_FAMILIES:
            raise KeyError(pbp_family)
        for target in PBP_FEATURE_FAMILIES[pbp_family]:
            columns.extend((
                f"env_{target}_season_before", f"env_{target}_last5",
                f"opp_env_{target}_season_before", f"opp_env_{target}_last5",
            ))
    return list(dict.fromkeys(columns))


def style_environment_feature_columns() -> list[str]:
    columns = environment_feature_columns()
    for family in PBP_FEATURE_FAMILIES:
        columns.extend(environment_feature_columns(pbp_family=family))
    return list(dict.fromkeys(columns))


def environment_trailing_baseline(frame: pd.DataFrame) -> np.ndarray:
    predictions = []
    for target in GAME_ENVIRONMENT_TARGETS:
        team = frame[f"env_{target}_last5"].fillna(
            frame[f"env_{target}_season_before"]
        )
        opponent = frame[f"opp_env_{target}_last5"].fillna(
            frame[f"opp_env_{target}_season_before"]
        )
        predictions.append(pd.concat([team, opponent], axis=1).mean(axis=1))
    return np.column_stack(predictions)


def validate_environment_cutoffs(frame: pd.DataFrame) -> None:
    for column in ("env_source_max_game_time", "opp_env_source_max_game_time"):
        observed = pd.to_datetime(frame[column], utc=True, errors="coerce")
        if (observed.notna() & (observed >= frame["game_time"])).any():
            raise ValueError(f"game-environment history leaks through {column}")


def attach_environment_predictions(
    players: pd.DataFrame, environment_predictions: pd.DataFrame,
) -> pd.DataFrame:
    prediction_columns = [
        column for column in environment_predictions if column.startswith("p9_env_")
    ]
    key_columns = ["game_id", "team_id"]
    if environment_predictions[key_columns].duplicated().any():
        raise ValueError("duplicate game-environment predictions")
    output = players.merge(
        environment_predictions[[*key_columns, *prediction_columns]],
        on=key_columns, how="left", validate="many_to_one",
    )
    if output[prediction_columns].isna().any().any():
        raise ValueError("missing player game-environment prediction")
    return add_profile_environment_interactions(output)


def add_profile_environment_interactions(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["p9_three_profile_x_three_pa_env"] = (
        pd.to_numeric(output["three_pa_per_min"], errors="coerce")
        * pd.to_numeric(output["p9_env_team_three_pa"], errors="coerce")
    )
    output["p9_rebound_profile_x_miss_env"] = (
        pd.to_numeric(output["rebounds_per_min"], errors="coerce")
        * pd.to_numeric(
            output["p9_env_team_defensive_rebounds_available"], errors="coerce"
        )
    )
    output["p9_creator_profile_x_possessions"] = (
        pd.to_numeric(output["assists_per_min"], errors="coerce")
        * pd.to_numeric(output["p9_env_game_possessions"], errors="coerce")
    )
    output["p9_creator_profile_x_assists_env"] = (
        pd.to_numeric(output["p6c_assist_share_last5"], errors="coerce")
        * pd.to_numeric(output["p9_env_team_assists"], errors="coerce")
    )
    output["p9_foul_draw_profile_x_fta_env"] = (
        pd.to_numeric(output["fta_per_min"], errors="coerce")
        * pd.to_numeric(output["p9_env_team_fta"], errors="coerce")
    )
    efficiency = pd.to_numeric(output["season_ts_pct_before"], errors="coerce")
    output["p9_efficiency_x_assisted_env"] = (
        efficiency * pd.to_numeric(
            output["p9_env_assisted_field_goal_rate"], errors="coerce"
        )
    )
    require_columns(INTERACTION_FEATURES, output.columns)
    return output


def player_archetype(frame: pd.DataFrame) -> np.ndarray:
    return frame["p6c_player_archetype"].astype("string").fillna("unknown").to_numpy(str)
