"""Leakage-safe Phase 6C features derived from retained repository data.

The builder is deliberately target-frame driven.  Every historical aggregate is
evaluated at the row's feature cutoff and the current game is excluded.  The
same entry point therefore works for historical research rows and for an
upcoming live slate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database


USAGE_FORMULA_VERSION = "traditional_usage_team_minutes_044_bounded100_v1"
PHASE6C_FEATURE_VERSION = "phase6c_existing_data_features_v1"

USAGE_OFFENSIVE_FEATURES = (
    "p6c_usage_season_before", "p6c_usage_last1", "p6c_usage_last3",
    "p6c_usage_last5", "p6c_usage_last10", "p6c_usage_last5_std",
    "p6c_usage_recent_change", "p6c_fga_share_season_before",
    "p6c_fga_share_last5", "p6c_2pa_share_last5", "p6c_3pa_share_last5",
    "p6c_fta_share_last5", "p6c_turnover_share_last5",
    "p6c_fouls_drawn_share_last5", "p6c_points_share_last5",
    "p6c_oreb_share_last5", "p6c_dreb_share_last5",
    "p6c_rebound_share_last5", "p6c_two_point_scoring_mix_last5",
    "p6c_three_point_scoring_mix_last5", "p6c_ft_scoring_mix_last5",
)

CREATION_FEATURES = (
    "p6c_assist_share_season_before", "p6c_assist_share_last1",
    "p6c_assist_share_last3", "p6c_assist_share_last5",
    "p6c_assist_share_last10", "p6c_assist_share_last5_std",
    "p6c_assist_share_recent_change", "p6c_creation_involvement_last5",
    "p6c_assist_share_per_usage_last5",
)

ABSENCE_HISTORY_FEATURES = (
    "p6c_last_game_teammate_dnp_count", "p6c_last5_teammate_dnp_avg",
    "p6c_prior_high_absence_games", "p6c_usage_in_high_absence_games",
    "p6c_usage_without_high_absence", "p6c_historical_absence_usage_lift",
    "p6c_creation_in_high_absence_games",
    "p6c_creation_without_high_absence",
    "p6c_historical_absence_creation_lift",
)

PACE_FEATURES = (
    "p6c_projected_pace_season", "p6c_projected_pace_recent",
    "p6c_pace_disagreement", "p6c_usage_x_projected_pace",
    "p6c_fp_per_min_x_projected_pace",
)

GENERAL_OPPONENT_FEATURES = (
    "p6c_opp_points_allowed_season", "p6c_opp_points_allowed_last5",
    "p6c_opp_assists_allowed_season", "p6c_opp_assists_allowed_last5",
    "p6c_opp_rebounds_allowed_season", "p6c_opp_rebounds_allowed_last5",
    "p6c_opp_fga_allowed_season", "p6c_opp_fga_allowed_last5",
    "p6c_opp_fta_allowed_season", "p6c_opp_fta_allowed_last5",
    "p6c_opp_turnovers_forced_season", "p6c_opp_turnovers_forced_last5",
    "p6c_opp_fp_allowed_team_season", "p6c_opp_fp_allowed_team_last5",
)

ROLE_MATCHUP_FEATURES = (
    "p6c_opp_archetype_games_before", "p6c_opp_archetype_fp_allowed_season",
    "p6c_opp_archetype_fp_allowed_last5",
    "p6c_opp_archetype_points_allowed_season",
    "p6c_opp_archetype_assists_allowed_season",
    "p6c_opp_archetype_rebounds_allowed_season",
    "p6c_opp_archetype_fga_allowed_season",
    "p6c_opp_archetype_fta_allowed_season",
)

RECENT_ROLE_FEATURES = (
    "role_change_score", "p6c_usage_recent_change",
    "p6c_assist_share_recent_change", "p6c_minutes_recent_vs_season",
    "p6c_starter_recent_vs_season", "p6c_closing_recent_vs_season",
    "p6c_first_sub_out_recent_vs_longer", "p6c_rotation_role_momentum",
)

COACH_ROTATION_FEATURES = (
    "p6c_coach_tenure_games_before", "p6c_coach_changes_last10",
    "p6c_expected_coach_is_new", "p6c_rotation_depth_last5",
    "p6c_rotation_depth_change",
)

INTERACTION_FEATURES = (
    "p6c_expected_usage_x_fp_per_min", "p6c_expected_usage_x_assist_profile",
    "p6c_expected_usage_x_rebound_profile",
    "p6c_usage_x_opponent_fp_allowance",
)

AUDIT_COLUMNS = (
    "p6c_player_source_max_game_time", "p6c_opponent_source_max_game_time",
    "p6c_role_matchup_source_max_game_time", "p6c_feature_cutoff_time",
)


def calculate_usage_percent(
    *,
    fga: float,
    fta: float,
    turnovers: float,
    minutes: float,
    team_fga: float,
    team_fta: float,
    team_turnovers: float,
    team_player_minutes: float,
) -> float:
    """Traditional USG%, using the retained box score and a 0.44 FT weight."""

    denominator = float(minutes) * (
        float(team_fga) + 0.44 * float(team_fta) + float(team_turnovers)
    )
    if denominator <= 0 or team_player_minutes <= 0:
        return float("nan")
    return 100.0 * (
        (float(fga) + 0.44 * float(fta) + float(turnovers))
        * (float(team_player_minutes) / 5.0)
        / denominator
    )


def attach_phase6c_features(
    frame: pd.DataFrame,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Attach pre-game involvement, matchup, absence-history, and role features."""

    if frame.empty:
        return _attach_empty_columns(frame.copy())
    required = {
        "model_row_id", "player_game_id", "season", "game_id", "player_id",
        "team_id", "opponent_team_id", "target_game_time", "feature_cutoff_time",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Phase 6C target frame is missing columns: {missing}")
    if frame["model_row_id"].duplicated().any():
        raise ValueError("Phase 6C target frame contains duplicate model rows")

    output = frame.copy().reset_index(drop=True)
    output["target_game_time"] = pd.to_datetime(output["target_game_time"], utc=True)
    output["feature_cutoff_time"] = pd.to_datetime(
        output["feature_cutoff_time"], utc=True
    )
    target = output[[
        "model_row_id", "player_game_id", "season", "game_id", "player_id",
        "team_id", "opponent_team_id", "target_game_time", "feature_cutoff_time",
    ]].copy()
    target["_target_row"] = np.arange(len(target), dtype=np.int64)
    target["fantasy_position"] = output.get(
        "fantasy_position", pd.Series(pd.NA, index=output.index, dtype="string")
    ).astype("string")

    with connect_database(database_path, read_only=True) as connection:
        connection.register("_phase6c_targets", target)
        try:
            player = connection.execute(_PLAYER_FEATURE_SQL).df()
        finally:
            connection.unregister("_phase6c_targets")

    output = _merge_by_target_row(output, player)
    output["p6c_player_archetype"] = _player_archetype(output)
    target["p6c_player_archetype"] = output["p6c_player_archetype"].to_numpy()

    with connect_database(database_path, read_only=True) as connection:
        connection.register("_phase6c_targets", target)
        try:
            opponent = connection.execute(_OPPONENT_FEATURE_SQL).df()
        finally:
            connection.unregister("_phase6c_targets")
    output = _merge_by_target_row(output, opponent)
    output = _derived_context_features(output)
    output["p6c_feature_version"] = PHASE6C_FEATURE_VERSION
    output["p6c_usage_formula_version"] = USAGE_FORMULA_VERSION
    output["p6c_feature_cutoff_time"] = output["feature_cutoff_time"]
    validate_phase6c_cutoffs(output)
    return output


def validate_phase6c_cutoffs(frame: pd.DataFrame) -> None:
    """Fail if any retained source reaches or crosses its row's feature cutoff."""

    cutoff = pd.to_datetime(frame["feature_cutoff_time"], utc=True)
    for column in (
        "p6c_player_source_max_game_time",
        "p6c_opponent_source_max_game_time",
        "p6c_role_matchup_source_max_game_time",
    ):
        if column not in frame:
            continue
        source = pd.to_datetime(frame[column], utc=True, errors="coerce")
        if (source.notna() & (source >= cutoff)).any():
            raise ValueError(f"Phase 6C source cutoff violation: {column}")


def _merge_by_target_row(frame: pd.DataFrame, additions: pd.DataFrame) -> pd.DataFrame:
    if len(additions) != len(frame) or additions["_target_row"].duplicated().any():
        raise ValueError("Phase 6C feature query changed the target-row universe")
    ordered = additions.sort_values("_target_row").reset_index(drop=True)
    if ordered["_target_row"].tolist() != list(range(len(frame))):
        raise ValueError("Phase 6C feature query lost or reordered target rows")
    return pd.concat(
        [frame.reset_index(drop=True), ordered.drop(columns="_target_row")], axis=1
    )


def _player_archetype(frame: pd.DataFrame) -> pd.Series:
    games = pd.to_numeric(frame["career_el_games_before"], errors="coerce").fillna(0)
    usage = pd.to_numeric(frame["p6c_usage_last5"], errors="coerce")
    creation = pd.to_numeric(frame["p6c_assist_share_last5"], errors="coerce")
    rebound = pd.to_numeric(frame["p6c_rebound_share_last5"], errors="coerce")
    values = np.select(
        [
            games < 3,
            creation >= 0.30,
            rebound >= 0.24,
            usage >= 24.0,
        ],
        ["COLD", "CREATOR", "REBOUNDER", "SCORER"],
        default="BALANCED",
    )
    return pd.Series(values, index=frame.index, dtype="string")


def _derived_context_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    numeric = lambda name: pd.to_numeric(output.get(name), errors="coerce")
    output["p6c_projected_pace_season"] = (
        numeric("team_season_pace_before") + numeric("opp_season_pace_before")
    ) / 2.0
    output["p6c_projected_pace_recent"] = (
        numeric("team_last5_pace") + numeric("opp_last5_pace")
    ) / 2.0
    output["p6c_pace_disagreement"] = (
        numeric("team_last5_pace") - numeric("opp_last5_pace")
    ).abs()
    output["p6c_usage_x_projected_pace"] = (
        numeric("p6c_usage_last5") * numeric("p6c_projected_pace_recent") / 100.0
    )
    output["p6c_fp_per_min_x_projected_pace"] = (
        numeric("last_5_fp_per_min") * numeric("p6c_projected_pace_recent")
    )
    output["p6c_minutes_recent_vs_season"] = (
        numeric("last_5_minutes_avg") - numeric("season_minutes_avg_before")
    )
    output["p6c_starter_recent_vs_season"] = (
        numeric("rot_last5_starter_rate") - numeric("season_start_rate_before")
    )
    output["p6c_closing_recent_vs_season"] = (
        numeric("rot_last5_closing_lineup_rate")
        - numeric("rot_season_closing_lineup_rate_before")
    )
    output["p6c_first_sub_out_recent_vs_longer"] = (
        numeric("rot_last3_first_sub_out_minutes_avg")
        - numeric("rot_last5_first_sub_out_minutes_avg")
    )
    output["p6c_rotation_role_momentum"] = (
        numeric("p6c_minutes_recent_vs_season")
        + 8.0 * numeric("p6c_starter_recent_vs_season")
        + 5.0 * numeric("p6c_closing_recent_vs_season")
    )
    output["p6c_usage_x_opponent_fp_allowance"] = (
        numeric("p6c_usage_last5") * numeric("p6c_opp_fp_allowed_team_last5") / 100.0
    )
    return output


def add_expected_usage_interactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Create nonlinear profile interactions after OOF/live usage prediction."""

    output = frame.copy()
    usage = pd.to_numeric(output["expected_usage_next_game"], errors="coerce")
    output["p6c_expected_usage_x_fp_per_min"] = usage * pd.to_numeric(
        output["last_5_fp_per_min"], errors="coerce"
    )
    output["p6c_expected_usage_x_assist_profile"] = usage * pd.to_numeric(
        output["p6c_assist_share_last5"], errors="coerce"
    )
    output["p6c_expected_usage_x_rebound_profile"] = usage * pd.to_numeric(
        output["p6c_rebound_share_last5"], errors="coerce"
    )
    return output


def _attach_empty_columns(frame: pd.DataFrame) -> pd.DataFrame:
    numeric = {
        *USAGE_OFFENSIVE_FEATURES, *CREATION_FEATURES, *ABSENCE_HISTORY_FEATURES,
        *PACE_FEATURES, *GENERAL_OPPONENT_FEATURES, *ROLE_MATCHUP_FEATURES,
        *RECENT_ROLE_FEATURES, *COACH_ROTATION_FEATURES, *INTERACTION_FEATURES,
        "actual_usage_next_game", "actual_usage_next_game_raw",
    }
    for column in sorted(numeric):
        if column not in frame:
            frame[column] = pd.Series(dtype="float64")
    frame["p6c_player_archetype"] = pd.Series(dtype="string")
    for column in AUDIT_COLUMNS:
        frame[column] = pd.Series(dtype="datetime64[ns, UTC]")
    return frame


_PLAYER_FEATURE_SQL = r"""
WITH team_minutes AS (
  SELECT game_id, team_id, sum(coalesce(target_minutes, 0)) AS team_player_minutes,
         count(*) FILTER (WHERE eligibility_status='DNP' OR coalesce(target_minutes,0)=0)
           AS team_dnp_count
  FROM ml_player_game_targets_v1 GROUP BY game_id, team_id
), observations AS (
  SELECT target.player_game_id, target.season, target.game_id,
         target.target_game_time AS event_time, target.player_id, target.team_id,
         target.opponent_team_id, core.fantasy_position,
         target.target_minutes AS minutes,
         coalesce(target.two_points_attempted,0)+coalesce(target.three_points_attempted,0)
           AS fga,
         coalesce(target.two_points_attempted,0) AS two_pa,
         coalesce(target.three_points_attempted,0) AS three_pa,
         coalesce(target.free_throws_attempted,0) AS fta,
         coalesce(target.turnovers,0) AS turnovers,
         coalesce(target.fouls_drawn,0) AS fouls_drawn,
         coalesce(target.points,0) AS points,
         2*coalesce(target.two_points_made,0) AS two_point_points,
         3*coalesce(target.three_points_made,0) AS three_point_points,
         coalesce(target.free_throws_made,0) AS ft_points,
         coalesce(target.assists,0) AS assists,
         coalesce(target.offensive_rebounds,0) AS oreb,
         coalesce(target.defensive_rebounds,0) AS dreb,
         coalesce(target.total_rebounds,0) AS rebounds,
         coalesce(team.two_points_attempted,0)+coalesce(team.three_points_attempted,0)
           AS team_fga,
         coalesce(team.two_points_attempted,0) AS team_two_pa,
         coalesce(team.three_points_attempted,0) AS team_three_pa,
         coalesce(team.free_throws_attempted,0) AS team_fta,
         coalesce(team.turnovers,0) AS team_turnovers,
         coalesce(team.fouls_drawn,0) AS team_fouls_drawn,
         coalesce(team.points,0) AS team_points,
         coalesce(team.assists,0) AS team_assists,
         coalesce(team.offensive_rebounds,0) AS team_oreb,
         coalesce(team.defensive_rebounds,0) AS team_dreb,
         coalesce(team.total_rebounds,0) AS team_rebounds,
         tm.team_player_minutes, tm.team_dnp_count,
         100.0 * ((coalesce(target.two_points_attempted,0)
                   +coalesce(target.three_points_attempted,0)
                   +0.44*coalesce(target.free_throws_attempted,0)
                   +coalesce(target.turnovers,0)) * (tm.team_player_minutes/5.0))
           / nullif(target.target_minutes *
             (coalesce(team.two_points_attempted,0)
              +coalesce(team.three_points_attempted,0)
              +0.44*coalesce(team.free_throws_attempted,0)
              +coalesce(team.turnovers,0)), 0) AS actual_usage_raw,
         target.assists/nullif(team.assists,0) AS actual_assist_share,
         target.total_rebounds/nullif(team.total_rebounds,0) AS actual_rebound_share
  FROM ml_player_game_targets_v1 AS target
  JOIN team_game_stats AS team
    ON team.canonical_game_id=target.game_id AND team.canonical_team_id=target.team_id
  JOIN team_minutes AS tm USING (game_id, team_id)
  LEFT JOIN ml_player_game_core_features_with_fantasy_v1 AS core
    USING (player_game_id)
  WHERE target.target_minutes>0 AND target.target_game_time IS NOT NULL
), events AS (
  SELECT obs.*, target._target_row, 1 AS event_order
  FROM observations AS obs
  LEFT JOIN _phase6c_targets AS target USING (player_game_id)
  UNION ALL
  SELECT target.player_game_id, target.season, target.game_id,
         target.feature_cutoff_time, target.player_id, target.team_id,
         target.opponent_team_id, target.fantasy_position,
         NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
         NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
         target._target_row, 0
  FROM _phase6c_targets AS target
  WHERE NOT EXISTS (
    SELECT 1 FROM observations AS obs WHERE obs.player_game_id=target.player_game_id
  )
), actual AS (
  SELECT *,least(greatest(actual_usage_raw,0.0),100.0) AS actual_usage,
    fga/nullif(team_fga,0) AS fga_share,
    two_pa/nullif(team_two_pa,0) AS two_pa_share,
    three_pa/nullif(team_three_pa,0) AS three_pa_share,
    fta/nullif(team_fta,0) AS fta_share,
    turnovers/nullif(team_turnovers,0) AS turnover_share,
    fouls_drawn/nullif(team_fouls_drawn,0) AS fouls_drawn_share,
    points/nullif(team_points,0) AS points_share,
    oreb/nullif(team_oreb,0) AS oreb_share,
    dreb/nullif(team_dreb,0) AS dreb_share,
    (assists+turnovers)/nullif(team_assists+team_turnovers,0)
      AS creation_involvement,
    two_point_points/nullif(points,0) AS two_point_scoring_mix,
    three_point_points/nullif(points,0) AS three_point_scoring_mix,
    ft_points/nullif(points,0) AS ft_scoring_mix
  FROM events
), windowed AS (
 SELECT *,
   lag(actual_usage) OVER p AS p6c_usage_last1,
   avg(actual_usage) OVER ps AS p6c_usage_season_before,
   avg(actual_usage) OVER p3 AS p6c_usage_last3,
   avg(actual_usage) OVER p5 AS p6c_usage_last5,
   avg(actual_usage) OVER p10 AS p6c_usage_last10,
   stddev_samp(actual_usage) OVER p5 AS p6c_usage_last5_std,
   avg(actual_usage) OVER p3 - avg(actual_usage) OVER pprev3
     AS p6c_usage_recent_change,
   avg(fga_share) OVER ps AS p6c_fga_share_season_before,
   avg(fga_share) OVER p5 AS p6c_fga_share_last5,
   avg(two_pa_share) OVER p5 AS p6c_2pa_share_last5,
   avg(three_pa_share) OVER p5 AS p6c_3pa_share_last5,
   avg(fta_share) OVER p5 AS p6c_fta_share_last5,
   avg(turnover_share) OVER p5 AS p6c_turnover_share_last5,
   avg(fouls_drawn_share) OVER p5 AS p6c_fouls_drawn_share_last5,
   avg(points_share) OVER p5 AS p6c_points_share_last5,
   avg(oreb_share) OVER p5 AS p6c_oreb_share_last5,
   avg(dreb_share) OVER p5 AS p6c_dreb_share_last5,
   avg(actual_rebound_share) OVER p5 AS p6c_rebound_share_last5,
   avg(two_point_scoring_mix) OVER p5 AS p6c_two_point_scoring_mix_last5,
   avg(three_point_scoring_mix) OVER p5 AS p6c_three_point_scoring_mix_last5,
   avg(ft_scoring_mix) OVER p5 AS p6c_ft_scoring_mix_last5,
   avg(actual_assist_share) OVER ps AS p6c_assist_share_season_before,
   lag(actual_assist_share) OVER p AS p6c_assist_share_last1,
   avg(actual_assist_share) OVER p3 AS p6c_assist_share_last3,
   avg(actual_assist_share) OVER p5 AS p6c_assist_share_last5,
   avg(actual_assist_share) OVER p10 AS p6c_assist_share_last10,
   stddev_samp(actual_assist_share) OVER p5 AS p6c_assist_share_last5_std,
   avg(actual_assist_share) OVER p3 - avg(actual_assist_share) OVER pprev3
     AS p6c_assist_share_recent_change,
   avg(creation_involvement) OVER p5 AS p6c_creation_involvement_last5,
   avg(actual_assist_share/nullif(actual_usage/100.0,0)) OVER p5
     AS p6c_assist_share_per_usage_last5,
   lag(team_dnp_count) OVER p AS p6c_last_game_teammate_dnp_count,
   avg(team_dnp_count) OVER p5 AS p6c_last5_teammate_dnp_avg,
   count(*) FILTER (WHERE team_dnp_count>=2) OVER ps AS p6c_prior_high_absence_games,
   avg(actual_usage) FILTER (WHERE team_dnp_count>=2) OVER ps
     AS p6c_usage_in_high_absence_games,
   avg(actual_usage) FILTER (WHERE team_dnp_count<2) OVER ps
     AS p6c_usage_without_high_absence,
   avg(actual_assist_share) FILTER (WHERE team_dnp_count>=2) OVER ps
     AS p6c_creation_in_high_absence_games,
   avg(actual_assist_share) FILTER (WHERE team_dnp_count<2) OVER ps
     AS p6c_creation_without_high_absence,
   max(event_time) OVER p AS p6c_player_source_max_game_time
 FROM actual
 WINDOW
   p AS (PARTITION BY player_id ORDER BY event_time,event_order,game_id
         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
   ps AS (PARTITION BY player_id,season ORDER BY event_time,event_order,game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
   p3 AS (PARTITION BY player_id ORDER BY event_time,event_order,game_id
          ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING),
   p5 AS (PARTITION BY player_id ORDER BY event_time,event_order,game_id
          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
   p10 AS (PARTITION BY player_id ORDER BY event_time,event_order,game_id
           ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING),
   pprev3 AS (PARTITION BY player_id ORDER BY event_time,event_order,game_id
              ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING)
)
SELECT _target_row, actual_usage AS actual_usage_next_game,
       actual_usage_raw AS actual_usage_next_game_raw,
       p6c_usage_season_before,p6c_usage_last1,p6c_usage_last3,p6c_usage_last5,
       p6c_usage_last10,p6c_usage_last5_std,p6c_usage_recent_change,
       p6c_fga_share_season_before,p6c_fga_share_last5,p6c_2pa_share_last5,
       p6c_3pa_share_last5,p6c_fta_share_last5,p6c_turnover_share_last5,
       p6c_fouls_drawn_share_last5,p6c_points_share_last5,p6c_oreb_share_last5,
       p6c_dreb_share_last5,p6c_rebound_share_last5,
       p6c_two_point_scoring_mix_last5,p6c_three_point_scoring_mix_last5,
       p6c_ft_scoring_mix_last5,p6c_assist_share_season_before,
       p6c_assist_share_last1,p6c_assist_share_last3,p6c_assist_share_last5,
       p6c_assist_share_last10,p6c_assist_share_last5_std,
       p6c_assist_share_recent_change,p6c_creation_involvement_last5,
       p6c_assist_share_per_usage_last5,p6c_last_game_teammate_dnp_count,
       p6c_last5_teammate_dnp_avg,p6c_prior_high_absence_games,
       p6c_usage_in_high_absence_games,p6c_usage_without_high_absence,
       p6c_usage_in_high_absence_games-p6c_usage_without_high_absence
         AS p6c_historical_absence_usage_lift,
       p6c_creation_in_high_absence_games,p6c_creation_without_high_absence,
       p6c_creation_in_high_absence_games-p6c_creation_without_high_absence
         AS p6c_historical_absence_creation_lift,
       p6c_player_source_max_game_time
FROM windowed WHERE _target_row IS NOT NULL ORDER BY _target_row
"""


_OPPONENT_FEATURE_SQL = r"""
WITH team_fp AS (
  SELECT game_id,team_id,sum(actual_fantasy_points) AS team_fp
  FROM ml_player_game_targets_v1
  WHERE target_minutes>0 AND actual_fantasy_points IS NOT NULL
  GROUP BY game_id,team_id
), rotation_depth AS (
  SELECT game_id,team_id,count(*) FILTER (WHERE target_minutes>0) AS rotation_players
  FROM ml_player_game_targets_v1 GROUP BY game_id,team_id
), team_history AS (
  SELECT game.season_code AS season, game.canonical_game_id AS game_id,
         game.game_date AS event_time, defense.canonical_team_id AS defense_team_id,
         offense.points AS allowed_points, offense.assists AS allowed_assists,
         offense.total_rebounds AS allowed_rebounds,
         coalesce(offense.two_points_attempted,0)+coalesce(offense.three_points_attempted,0)
           AS allowed_fga,
         offense.free_throws_attempted AS allowed_fta,
         offense.turnovers AS forced_turnovers, fp.team_fp AS allowed_fp,
         defense.coach_name, depth.rotation_players
  FROM team_game_stats AS defense
  JOIN team_game_stats AS offense
    ON offense.canonical_game_id=defense.canonical_game_id
   AND offense.canonical_team_id=defense.opponent_team_id
  JOIN games AS game ON game.canonical_game_id=defense.canonical_game_id
  LEFT JOIN team_fp AS fp
    ON fp.game_id=defense.canonical_game_id AND fp.team_id=defense.opponent_team_id
  LEFT JOIN rotation_depth AS depth
    ON depth.game_id=defense.canonical_game_id AND depth.team_id=defense.canonical_team_id
  WHERE game.game_date IS NOT NULL
), team_matches AS (
 SELECT target._target_row,target.season AS target_season,history.*,
        row_number() OVER (PARTITION BY target._target_row
          ORDER BY history.event_time DESC,history.game_id DESC) AS recency
 FROM _phase6c_targets AS target
 LEFT JOIN team_history AS history
   ON history.defense_team_id=target.opponent_team_id
  AND history.event_time<target.feature_cutoff_time
), team_agg AS (
 SELECT _target_row,
   avg(allowed_points) FILTER(WHERE season=target_season)
     AS p6c_opp_points_allowed_season,
   avg(allowed_points) FILTER(WHERE recency<=5) AS p6c_opp_points_allowed_last5,
   avg(allowed_assists) FILTER(WHERE season=target_season)
     AS p6c_opp_assists_allowed_season,
   avg(allowed_assists) FILTER(WHERE recency<=5) AS p6c_opp_assists_allowed_last5,
   avg(allowed_rebounds) FILTER(WHERE season=target_season)
     AS p6c_opp_rebounds_allowed_season,
   avg(allowed_rebounds) FILTER(WHERE recency<=5) AS p6c_opp_rebounds_allowed_last5,
   avg(allowed_fga) FILTER(WHERE season=target_season) AS p6c_opp_fga_allowed_season,
   avg(allowed_fga) FILTER(WHERE recency<=5) AS p6c_opp_fga_allowed_last5,
   avg(allowed_fta) FILTER(WHERE season=target_season) AS p6c_opp_fta_allowed_season,
   avg(allowed_fta) FILTER(WHERE recency<=5) AS p6c_opp_fta_allowed_last5,
   avg(forced_turnovers) FILTER(WHERE season=target_season)
     AS p6c_opp_turnovers_forced_season,
   avg(forced_turnovers) FILTER(WHERE recency<=5)
     AS p6c_opp_turnovers_forced_last5,
   avg(allowed_fp) FILTER(WHERE season=target_season)
     AS p6c_opp_fp_allowed_team_season,
   avg(allowed_fp) FILTER(WHERE recency<=5) AS p6c_opp_fp_allowed_team_last5,
   max(event_time) AS p6c_opponent_source_max_game_time
 FROM team_matches GROUP BY _target_row
), player_actual AS (
 SELECT target.game_id,target.season,target.target_game_time AS event_time,
        target.opponent_team_id AS defense_team_id,
        CASE
          WHEN target.assists/nullif(team.assists,0)>=0.30 THEN 'CREATOR'
          WHEN target.total_rebounds/nullif(team.total_rebounds,0)>=0.24 THEN 'REBOUNDER'
          WHEN 100.0*((coalesce(target.two_points_attempted,0)
                    +coalesce(target.three_points_attempted,0)
                    +0.44*coalesce(target.free_throws_attempted,0)
                    +coalesce(target.turnovers,0))*
                    (tm.team_player_minutes/5.0))
               /nullif(target.target_minutes*(coalesce(team.two_points_attempted,0)
                    +coalesce(team.three_points_attempted,0)
                    +0.44*coalesce(team.free_throws_attempted,0)
                    +coalesce(team.turnovers,0)),0)>=24.0 THEN 'SCORER'
          ELSE 'BALANCED' END AS archetype,
        target.actual_fantasy_points,target.points,target.assists,
        target.total_rebounds,
        coalesce(target.two_points_attempted,0)+coalesce(target.three_points_attempted,0)
          AS fga,
        target.free_throws_attempted AS fta
 FROM ml_player_game_targets_v1 AS target
 JOIN team_game_stats AS team
   ON team.canonical_game_id=target.game_id AND team.canonical_team_id=target.team_id
 JOIN (
   SELECT game_id,team_id,sum(coalesce(target_minutes,0)) AS team_player_minutes
   FROM ml_player_game_targets_v1 GROUP BY game_id,team_id
 ) AS tm USING(game_id,team_id)
 WHERE target.target_minutes>0 AND target.actual_fantasy_points IS NOT NULL
), role_history AS (
 SELECT game_id,season,event_time,defense_team_id,archetype,
        count(*) AS players,sum(actual_fantasy_points) AS fp,sum(points) AS points,
        sum(assists) AS assists,sum(total_rebounds) AS rebounds,sum(fga) AS fga,
        sum(fta) AS fta
 FROM player_actual GROUP BY ALL
), role_matches AS (
 SELECT target._target_row,target.season AS target_season,history.*,
        row_number() OVER (PARTITION BY target._target_row
          ORDER BY history.event_time DESC,history.game_id DESC) AS recency
 FROM _phase6c_targets AS target
 LEFT JOIN role_history AS history
   ON history.defense_team_id=target.opponent_team_id
  AND history.archetype=target.p6c_player_archetype
  AND history.event_time<target.feature_cutoff_time
), role_agg AS (
 SELECT _target_row,count(game_id) AS p6c_opp_archetype_games_before,
   avg(fp/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_fp_allowed_season,
   avg(fp/nullif(players,0)) FILTER(WHERE recency<=5)
     AS p6c_opp_archetype_fp_allowed_last5,
   avg(points/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_points_allowed_season,
   avg(assists/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_assists_allowed_season,
   avg(rebounds/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_rebounds_allowed_season,
   avg(fga/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_fga_allowed_season,
   avg(fta/nullif(players,0)) FILTER(WHERE season=target_season)
     AS p6c_opp_archetype_fta_allowed_season,
   max(event_time) AS p6c_role_matchup_source_max_game_time
 FROM role_matches GROUP BY _target_row
), coach_matches AS (
 SELECT target._target_row,history.*,
        row_number() OVER (PARTITION BY target._target_row
          ORDER BY history.event_time DESC,history.game_id DESC) AS recency
 FROM _phase6c_targets AS target
 LEFT JOIN team_history AS history
   ON history.defense_team_id=target.team_id
  AND history.event_time<target.feature_cutoff_time
), coach_latest AS (
 SELECT _target_row,arg_max(coach_name,event_time) AS expected_coach
 FROM coach_matches GROUP BY _target_row
), target_team_coach AS (
 SELECT match._target_row,
        count(*) FILTER(WHERE match.coach_name=latest.expected_coach)
          AS p6c_coach_tenure_games_before,
        greatest(count(DISTINCT match.coach_name) FILTER(WHERE recency<=10)-1,0)
          AS p6c_coach_changes_last10,
        CASE WHEN max(match.coach_name) FILTER(WHERE recency=1)
                    IS DISTINCT FROM max(match.coach_name) FILTER(WHERE recency=2)
             THEN 1.0 ELSE 0.0 END AS p6c_expected_coach_is_new,
        avg(rotation_players) FILTER(WHERE recency<=5) AS p6c_rotation_depth_last5,
        avg(rotation_players) FILTER(WHERE recency<=3)
          -avg(rotation_players) FILTER(WHERE recency BETWEEN 4 AND 6)
            AS p6c_rotation_depth_change
 FROM coach_matches AS match JOIN coach_latest AS latest USING(_target_row)
 GROUP BY match._target_row
)
SELECT team._target_row,team.* EXCLUDE(_target_row),role.* EXCLUDE(_target_row),
       coach.* EXCLUDE(_target_row)
FROM team_agg AS team
JOIN role_agg AS role USING(_target_row)
JOIN target_team_coach AS coach USING(_target_row)
ORDER BY team._target_row
"""
