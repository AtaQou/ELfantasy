"""Modular point-in-time Phase 3B rich feature generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .core_features import DEFAULT_DERIVED_ROOT, DEFAULT_SAMPLE_ROOT
from .rich_reconstruction import (
    ROTATION_RECONSTRUCTION_VERSION,
    generate_rotation_intermediates,
    reconstruction_fingerprint,
)


RICH_FEATURE_PIPELINE_VERSION = "phase3b_rich_v1"
SHOT_GEOMETRY_VERSION = "elfc_halfcourt_cm_v1"
POSSESSION_VERSION = "pbp_boxscore_possession_proxy_v1"
LINEUP_VERSION = "pbp_settled_lineups_v1"
MIN_ONOFF_POSSESSIONS = 20.0
RICH_DERIVED_ROOT = DEFAULT_DERIVED_ROOT.parent / "phase3b"
RICH_SAMPLE_ROOT = DEFAULT_SAMPLE_ROOT.parent / "phase3b"


@dataclass(frozen=True, slots=True)
class RichBuildSummary:
    core_rows: int
    rich_any_rows: int
    rich_all_major_rows: int
    primary_eval_rows: int
    rotation_rows: int
    onoff_rows: int
    lineup_rows: int
    shot_rows: int
    opponent_shot_rows: int
    rich_feature_count: int
    dataset_fingerprint: str
    reconstruction_fingerprint: str
    generated_at: str
    rich_feature_pipeline_version: str


def generate_rich_features(database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
    """Create rich observations, strictly lagged families, and the left-joined model."""

    generate_rotation_intermediates(database_path)
    with connect_database(database_path) as connection:
        _create_rotation_observations(connection)
        _create_onoff_observations(connection)
        _create_lineup_observations(connection)
        _create_shot_observations(connection)
        _create_rotation_features(connection)
        _create_onoff_features(connection)
        _create_lineup_features(connection)
        _create_shot_features(connection)
        _create_opponent_shot_features(connection)
        _create_core_plus_rich(connection)


def build_phase3b_datasets(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = RICH_DERIVED_ROOT,
    sample_root: Path = RICH_SAMPLE_ROOT,
) -> RichBuildSummary:
    generate_rich_features(database_path)
    derived_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    with connect_database(database_path) as connection:
        for table in (
            "pbp_rotation_game_audit_v1",
            "pbp_rotation_player_minute_audit_v1",
            "pbp_player_stints_v1",
            "pbp_lineup_stints_v1",
            "pbp_lineup_possession_components_v1",
            "pbp_possessions_v1",
            "ml_player_game_rich_features_v1",
            "ml_player_game_core_plus_rich_v1",
            "ml_phase3b_primary_evaluation_v1",
        ):
            connection.execute(
                f"COPY (SELECT * FROM {table}) TO ? "
                "(FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(derived_root / f"{table}.parquet")],
            )
        counts = connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE rich_any_available),
                   count(*) FILTER (WHERE rich_all_major_families_available),
                   count(*) FILTER (WHERE phase3b_primary_eval_eligible),
                   count(*) FILTER (WHERE has_rotation_features),
                   count(*) FILTER (WHERE has_onoff_features),
                   count(*) FILTER (WHERE has_lineup_features),
                   count(*) FILTER (WHERE has_shot_profile_features),
                   count(*) FILTER (WHERE has_opp_shot_features)
            FROM ml_player_game_rich_features_v1
            """
        ).fetchone()
        rich_columns = [
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info('ml_player_game_rich_features_v1')"
            ).fetchall()
            if str(row[1]).startswith(
                ("rot_", "onoff_", "lineup_", "teammate_", "shot_", "opp_shot_", "interaction_")
            )
            and not str(row[1]).endswith(("_version", "_source_max_game_time_us"))
        ]
        fingerprint = rich_dataset_fingerprint(connection)
        reconstruction_hash = reconstruction_fingerprint(connection)
    summary = RichBuildSummary(
        core_rows=int(counts[0]), rich_any_rows=int(counts[1]),
        rich_all_major_rows=int(counts[2]), primary_eval_rows=int(counts[3]),
        rotation_rows=int(counts[4]), onoff_rows=int(counts[5]),
        lineup_rows=int(counts[6]), shot_rows=int(counts[7]),
        opponent_shot_rows=int(counts[8]), rich_feature_count=len(rich_columns),
        dataset_fingerprint=fingerprint,
        reconstruction_fingerprint=reconstruction_hash,
        generated_at=datetime.now(UTC).isoformat(),
        rich_feature_pipeline_version=RICH_FEATURE_PIPELINE_VERSION,
    )
    (sample_root / "rich_feature_build_summary.json").write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def rich_dataset_fingerprint(connection: Any) -> str:
    # DuckDB may reduce parallel floating aggregates in a different order;
    # canonicalize well below modelling precision so equivalent 1e-14 noise does
    # not masquerade as a changed dataset.
    rows = connection.execute(
        """
        SELECT model_row_id, epoch_us(target_game_time),
               rot_games_before, round(rot_last5_minutes_avg, 12),
               onoff_games_before, round(onoff_season_on_net_rating, 12),
               lineup_games_before, round(lineup_last5_entropy_avg, 12),
               shot_games_before, round(shot_season_rim_attempt_rate, 12),
               opp_shot_games_before,
               round(opp_shot_season_rim_attempt_rate_allowed, 12),
               phase3b_primary_eval_eligible
        FROM ml_player_game_core_plus_rich_v1
        ORDER BY target_game_time, game_id, player_id
        """
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def validate_rich_cutoffs(connection: Any, *, fail: bool = True) -> dict[str, int]:
    """Return source-at/after-cutoff counts and optionally fail closed."""

    result = dict(
        connection.execute(
            """
            SELECT * FROM (VALUES
              ('rotation', (SELECT count(*) FROM ml_player_game_rich_features_v1
                 WHERE rot_source_max_game_time_us >= epoch_us(feature_cutoff_time))),
              ('onoff', (SELECT count(*) FROM ml_player_game_rich_features_v1
                 WHERE onoff_source_max_game_time_us >= epoch_us(feature_cutoff_time))),
              ('lineup', (SELECT count(*) FROM ml_player_game_rich_features_v1
                 WHERE lineup_source_max_game_time_us >= epoch_us(feature_cutoff_time))),
              ('shot', (SELECT count(*) FROM ml_player_game_rich_features_v1
                 WHERE shot_source_max_game_time_us >= epoch_us(feature_cutoff_time))),
              ('opponent_shot', (SELECT count(*) FROM ml_player_game_rich_features_v1
                 WHERE opp_shot_source_max_game_time_us >= epoch_us(feature_cutoff_time)))
            ) AS checks(family, violations)
            """
        ).fetchall()
    )
    result = {str(key): int(value) for key, value in result.items()}
    if fail and any(result.values()):
        raise ValueError(f"Rich feature cutoff violation: {result}")
    return result


def assert_source_precedes_cutoff(source_time_us: int, cutoff_time_us: int) -> None:
    """Fail-closed primitive used by synthetic future-observation tests."""

    if source_time_us >= cutoff_time_us:
        raise ValueError("Rich source observation is at or after feature cutoff")


def assign_shot_zone(points_value: int, x_cm: float, y_cm: float) -> str:
    """Classify a validated provider coordinate under the documented FIBA geometry."""

    if not (-750 <= x_cm <= 750 and -150 <= y_cm <= 1400):
        raise ValueError("Shot coordinate lies outside validated half-court bounds")
    if x_cm == -1 and y_cm == -1:
        raise ValueError("Free-throw coordinate sentinel is not spatial")
    distance = (x_cm * x_cm + y_cm * y_cm) ** 0.5
    if points_value == 2:
        if distance > 800:
            raise ValueError("Two-point action conflicts with coordinate geometry")
        if distance <= 125:
            return "RIM"
        if abs(x_cm) <= 245 and y_cm <= 423:
            return "PAINT_NONRIM"
        return "MIDRANGE"
    if points_value == 3:
        if distance < 550:
            raise ValueError("Three-point action conflicts with coordinate geometry")
        if abs(x_cm) >= 660 and y_cm <= 142:
            return "CORNER3"
        return "ABOVE_BREAK3"
    raise ValueError("Only field-goal attempts have spatial zones")


def _create_rotation_observations(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE player_game_rotation_observations_v1 AS
        WITH stint AS (
            SELECT game_id, season, game_code, game_time_us, team_id,
                   opponent_team_id, player_id, count(*) AS stint_count,
                   sum(duration_seconds) AS rotation_seconds,
                   avg(duration_seconds) AS avg_stint_seconds,
                   max(duration_seconds) AS longest_stint_seconds,
                   min(start_second) AS first_entry_second,
                   min(end_second) FILTER (WHERE stint_number = 1)
                       AS first_stint_end_second,
                   max(rich_source_game_time_us) AS source_game_time_us
            FROM pbp_player_stints_v1 GROUP BY ALL
        ), closing AS (
            SELECT game_id, team_id, player_id,
                   sum(greatest(0, least(end_second, 2400) -
                       greatest(start_second, 2280))) >= 120 AS closing_lineup
            FROM pbp_player_stints_v1 GROUP BY ALL
        )
        SELECT stint.*,
               stat.starter,
               cast(round(stat.minutes * 60) AS INTEGER) AS official_seconds,
               stint.rotation_seconds - cast(round(stat.minutes * 60) AS INTEGER)
                   AS minute_difference_seconds,
               2400 + 300 * coalesce(game.overtime_count, 0) AS game_seconds,
               CASE WHEN stat.starter THEN stint.first_stint_end_second END
                   AS first_sub_out_second,
               stint.first_entry_second,
               stint.first_stint_end_second - stint.first_entry_second
                   AS first_stint_seconds,
               closing.closing_lineup,
               'phase3b_rotation_v1' AS observation_version
        FROM stint
        JOIN player_game_stats AS stat
          ON stat.canonical_game_id = stint.game_id
         AND stat.canonical_team_id = stint.team_id
         AND stat.canonical_player_id = stint.player_id
        JOIN games AS game ON game.canonical_game_id = stint.game_id
        JOIN closing USING (game_id, team_id, player_id)
        ORDER BY game_time_us, game_id, team_id, player_id
        """
    )


def _create_onoff_observations(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE player_game_onoff_observations_v1 AS
        WITH component AS (
            SELECT lineup.*, possession.fga, possession.offensive_rebounds,
                   possession.turnovers, possession.fta,
                   possession.estimated_possession_component,
                   possession.opponent_estimated_possession_component,
                   possession.estimated_game_possession_component
            FROM pbp_lineup_stints_v1 AS lineup
            JOIN pbp_lineup_possession_components_v1 AS possession
              USING (lineup_stint_id)
        ), member AS (
            SELECT component.*, unnest([
                player_1_id, player_2_id, player_3_id, player_4_id, player_5_id
            ]) AS player_id
            FROM component
        ), on_agg AS (
            SELECT game_id, season, game_time_us, team_id, opponent_team_id,
                   player_id, sum(duration_seconds) AS on_seconds,
                   sum(points_for) AS on_points_for,
                   sum(points_against) AS on_points_against,
                   sum(fga) AS on_fga, sum(offensive_rebounds) AS on_oreb,
                   sum(turnovers) AS on_turnovers, sum(fta) AS on_fta,
                   sum(estimated_game_possession_component) AS on_game_possessions,
                   max(rich_source_game_time_us) AS source_game_time_us
            FROM member GROUP BY ALL
        ), team_total AS (
            SELECT game_id, team_id, sum(duration_seconds) AS game_seconds,
                   sum(points_for) AS points_for,
                   sum(points_against) AS points_against,
                   sum(fga) AS fga, sum(offensive_rebounds) AS oreb,
                   sum(turnovers) AS turnovers, sum(fta) AS fta,
                   sum(estimated_game_possession_component) AS game_possessions
            FROM component GROUP BY ALL
        )
        SELECT on_agg.*,
               on_fga - on_oreb + on_turnovers + 0.44 * on_fta
                   AS on_possessions,
               total.fga - on_fga - (total.oreb - on_oreb)
                   + (total.turnovers - on_turnovers)
                   + 0.44 * (total.fta - on_fta) AS off_possessions,
               total.game_seconds - on_seconds AS off_seconds,
               total.points_for - on_points_for AS off_points_for,
               total.points_against - on_points_against AS off_points_against,
               total.game_possessions - on_game_possessions AS off_game_possessions,
               'pbp_boxscore_possession_proxy_v1' AS possession_version
        FROM on_agg
        JOIN team_total AS total USING (game_id, team_id)
        ORDER BY game_time_us, game_id, team_id, player_id
        """
    )


def _create_lineup_observations(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE player_game_lineup_observations_v1 AS
        WITH lineup_minutes AS (
            SELECT game_id, season, game_time_us, team_id, opponent_team_id,
                   lineup_key, player_1_id, player_2_id, player_3_id,
                   player_4_id, player_5_id,
                   sum(duration_seconds) AS lineup_seconds,
                   max(rich_source_game_time_us) AS source_game_time_us
            FROM pbp_lineup_stints_v1 GROUP BY ALL
        ), ranked AS (
            SELECT *, row_number() OVER (
                     PARTITION BY game_id, team_id
                     ORDER BY lineup_seconds DESC, lineup_key
                   ) AS lineup_rank,
                   sum(lineup_seconds) OVER (PARTITION BY game_id, team_id)
                       AS game_seconds
            FROM lineup_minutes
        ), team_obs AS (
            SELECT game_id, season, game_time_us, team_id, opponent_team_id,
                   count(*) AS unique_lineups,
                   max(lineup_seconds) / max(game_seconds) AS top_lineup_share,
                   sum(lineup_seconds) FILTER (WHERE lineup_rank <= 3)
                       / max(game_seconds) AS top3_lineup_share,
                   -sum((lineup_seconds / game_seconds) *
                        ln(lineup_seconds / game_seconds)) AS lineup_entropy,
                   max(source_game_time_us) AS source_game_time_us
            FROM ranked GROUP BY ALL
        ), top_lineup AS (
            SELECT *, lag([player_1_id, player_2_id, player_3_id,
                           player_4_id, player_5_id]) OVER (
                       PARTITION BY team_id ORDER BY game_time_us, game_id
                   ) AS previous_top_players
            FROM ranked WHERE lineup_rank = 1
        ), continuity AS (
            SELECT game_id, team_id,
                   CASE WHEN previous_top_players IS NOT NULL THEN
                     (list_contains(previous_top_players, player_1_id)::INTEGER
                      + list_contains(previous_top_players, player_2_id)::INTEGER
                      + list_contains(previous_top_players, player_3_id)::INTEGER
                      + list_contains(previous_top_players, player_4_id)::INTEGER
                      + list_contains(previous_top_players, player_5_id)::INTEGER) / 5.0
                   END AS top_lineup_continuity
            FROM top_lineup
        ), member AS (
            SELECT ranked.*, unnest([
                player_1_id, player_2_id, player_3_id, player_4_id, player_5_id
            ]) AS player_id
            FROM ranked
        ), player_top AS (
            SELECT game_id, team_id, player_id,
                   sum(lineup_seconds) FILTER (WHERE lineup_rank = 1)
                       / nullif(sum(lineup_seconds), 0) AS player_top_lineup_share
            FROM member GROUP BY ALL
        ), pairs AS (
            SELECT game_id, team_id, player_id, teammate_id,
                   sum(lineup_seconds) AS shared_seconds
            FROM (
                SELECT member.game_id, member.team_id, member.lineup_seconds,
                       member.player_id,
                       unnest([player_1_id, player_2_id, player_3_id,
                               player_4_id, player_5_id]) AS teammate_id
                FROM member
            ) WHERE player_id <> teammate_id GROUP BY ALL
        ), pair_rank AS (
            SELECT *, row_number() OVER (
                PARTITION BY game_id, team_id, player_id
                ORDER BY shared_seconds DESC, teammate_id
            ) AS teammate_rank,
            sum(shared_seconds) OVER (
                PARTITION BY game_id, team_id, player_id
            ) AS total_shared_seconds
            FROM pairs
        ), teammate AS (
            SELECT game_id, team_id, player_id,
                   max(shared_seconds) AS top_teammate_shared_seconds,
                   sum(shared_seconds) FILTER (WHERE teammate_rank <= 3)
                       AS top3_teammates_shared_seconds,
                   max(shared_seconds) / nullif(max(total_shared_seconds), 0)
                       AS teammate_minutes_concentration,
                   count(*) FILTER (WHERE shared_seconds >= 600)
                       AS stable_teammate_count
            FROM pair_rank GROUP BY ALL
        )
        SELECT member.game_id, min(member.season) AS season,
               min(member.game_time_us) AS game_time_us, member.team_id,
               min(member.opponent_team_id) AS opponent_team_id,
               member.player_id, min(team_obs.unique_lineups) AS unique_lineups,
               min(team_obs.top_lineup_share) AS top_lineup_share,
               min(team_obs.top3_lineup_share) AS top3_lineup_share,
               min(team_obs.lineup_entropy) AS lineup_entropy,
               min(player_top.player_top_lineup_share) AS player_top_lineup_share,
               min(continuity.top_lineup_continuity) AS top_lineup_continuity,
               min(teammate.top_teammate_shared_seconds)
                   AS top_teammate_shared_seconds,
               min(teammate.top3_teammates_shared_seconds)
                   AS top3_teammates_shared_seconds,
               min(teammate.teammate_minutes_concentration)
                   AS teammate_minutes_concentration,
               min(teammate.stable_teammate_count) AS stable_teammate_count,
               max(team_obs.source_game_time_us) AS source_game_time_us,
               'pbp_settled_lineups_v1' AS lineup_version
        FROM member
        JOIN team_obs USING (game_id, team_id)
        JOIN player_top USING (game_id, team_id, player_id)
        JOIN continuity USING (game_id, team_id)
        JOIN teammate USING (game_id, team_id, player_id)
        GROUP BY member.game_id, member.team_id, member.player_id
        ORDER BY game_time_us, game_id, team_id, player_id
        """
    )


def _create_shot_observations(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE shot_spatial_attempts_v1 AS
        SELECT shot.shot_id, shot.canonical_game_id AS game_id,
               game.season_code AS season, epoch_us(game.game_date) AS game_time_us,
               shot.canonical_team_id AS team_id,
               CASE WHEN shot.canonical_team_id = game.home_team_id
                    THEN game.away_team_id ELSE game.home_team_id END
                    AS opponent_team_id,
               shot.canonical_player_id AS player_id, shot.points_value,
               shot.made, shot.coordinate_x AS x_cm, shot.coordinate_y AS y_cm,
               sqrt(shot.coordinate_x * shot.coordinate_x +
                    shot.coordinate_y * shot.coordinate_y) / 100.0
                    AS shot_distance_m,
               CASE
                 WHEN shot.points_value NOT IN (2,3) THEN NULL
                 WHEN shot.coordinate_x = -1 AND shot.coordinate_y = -1 THEN NULL
                 WHEN shot.coordinate_x NOT BETWEEN -750 AND 750
                   OR shot.coordinate_y NOT BETWEEN -150 AND 1400 THEN NULL
                 WHEN shot.points_value = 2 AND sqrt(
                   shot.coordinate_x * shot.coordinate_x +
                   shot.coordinate_y * shot.coordinate_y) > 800 THEN NULL
                 WHEN shot.points_value = 3 AND sqrt(
                   shot.coordinate_x * shot.coordinate_x +
                   shot.coordinate_y * shot.coordinate_y) < 550 THEN NULL
                 WHEN shot.points_value = 2 AND sqrt(
                   shot.coordinate_x * shot.coordinate_x +
                   shot.coordinate_y * shot.coordinate_y) <= 125 THEN 'RIM'
                 WHEN shot.points_value = 2 AND abs(shot.coordinate_x) <= 245
                   AND shot.coordinate_y <= 423 THEN 'PAINT_NONRIM'
                 WHEN shot.points_value = 2 THEN 'MIDRANGE'
                 WHEN shot.points_value = 3 AND abs(shot.coordinate_x) >= 660
                   AND shot.coordinate_y <= 142 THEN 'CORNER3'
                 WHEN shot.points_value = 3 THEN 'ABOVE_BREAK3'
               END AS shot_zone,
               CASE
                 WHEN shot.points_value NOT IN (2,3) THEN 'NON_FIELD_GOAL'
                 WHEN shot.coordinate_x = -1 AND shot.coordinate_y = -1
                   THEN 'FREE_THROW_SENTINEL'
                 WHEN shot.coordinate_x NOT BETWEEN -750 AND 750
                   OR shot.coordinate_y NOT BETWEEN -150 AND 1400
                   THEN 'OUTSIDE_VALIDATED_COURT'
                 WHEN shot.points_value = 2 AND sqrt(
                   shot.coordinate_x * shot.coordinate_x +
                   shot.coordinate_y * shot.coordinate_y) > 800
                   THEN 'TWO_POINT_GEOMETRY_CONFLICT'
                 WHEN shot.points_value = 3 AND sqrt(
                   shot.coordinate_x * shot.coordinate_x +
                   shot.coordinate_y * shot.coordinate_y) < 550
                   THEN 'THREE_POINT_GEOMETRY_CONFLICT'
                 ELSE NULL
               END AS spatial_quarantine_reason,
               '{SHOT_GEOMETRY_VERSION}' AS shot_geometry_version
        FROM shots AS shot JOIN games AS game USING (canonical_game_id)
        WHERE game.season_code BETWEEN 'E2021' AND 'E2025'
        ORDER BY game_time_us, game_id, shot.source_sequence
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TABLE player_game_shot_profile_observations_v1 AS
        SELECT game_id, min(season) AS season, min(game_time_us) AS game_time_us,
               team_id, min(opponent_team_id) AS opponent_team_id, player_id,
               count(*) AS attempts, sum(made::INTEGER) AS makes,
               sum(points_value * made::INTEGER) AS points,
               sum((points_value = 2)::INTEGER) AS two_pa,
               sum((points_value = 3)::INTEGER) AS three_pa,
               sum((shot_zone = 'RIM')::INTEGER) AS rim_attempts,
               sum((shot_zone = 'RIM' AND made)::INTEGER) AS rim_makes,
               sum((shot_zone = 'PAINT_NONRIM')::INTEGER) AS paint_attempts,
               sum((shot_zone = 'PAINT_NONRIM' AND made)::INTEGER) AS paint_makes,
               sum((shot_zone = 'MIDRANGE')::INTEGER) AS midrange_attempts,
               sum((shot_zone = 'MIDRANGE' AND made)::INTEGER) AS midrange_makes,
               sum((shot_zone = 'CORNER3')::INTEGER) AS corner3_attempts,
               sum((shot_zone = 'CORNER3' AND made)::INTEGER) AS corner3_makes,
               sum((shot_zone = 'ABOVE_BREAK3')::INTEGER) AS above_break3_attempts,
               sum((shot_zone = 'ABOVE_BREAK3' AND made)::INTEGER)
                   AS above_break3_makes,
               sum(shot_distance_m) AS distance_sum_m,
               max(game_time_us) AS source_game_time_us,
               min(shot_geometry_version) AS shot_geometry_version
        FROM shot_spatial_attempts_v1 WHERE shot_zone IS NOT NULL
        GROUP BY game_id, team_id, player_id
        ORDER BY game_time_us, game_id, player_id;

        CREATE OR REPLACE TABLE team_game_shot_profile_observations_v1 AS
        SELECT game_id, min(season) AS season, min(game_time_us) AS game_time_us,
               team_id AS shooting_team_id,
               min(opponent_team_id) AS defending_team_id, count(*) AS attempts,
               sum(made::INTEGER) AS makes,
               sum((shot_zone = 'RIM')::INTEGER) AS rim_attempts,
               sum((shot_zone = 'RIM' AND made)::INTEGER) AS rim_makes,
               sum((shot_zone = 'PAINT_NONRIM')::INTEGER) AS paint_attempts,
               sum((shot_zone = 'PAINT_NONRIM' AND made)::INTEGER) AS paint_makes,
               sum((shot_zone = 'MIDRANGE')::INTEGER) AS midrange_attempts,
               sum((shot_zone = 'MIDRANGE' AND made)::INTEGER) AS midrange_makes,
               sum((shot_zone = 'CORNER3')::INTEGER) AS corner3_attempts,
               sum((shot_zone = 'CORNER3' AND made)::INTEGER) AS corner3_makes,
               sum((shot_zone = 'ABOVE_BREAK3')::INTEGER) AS above_break3_attempts,
               sum((shot_zone = 'ABOVE_BREAK3' AND made)::INTEGER)
                   AS above_break3_makes,
               sum(shot_distance_m) AS distance_sum_m,
               max(game_time_us) AS source_game_time_us
        FROM shot_spatial_attempts_v1 WHERE shot_zone IS NOT NULL
        GROUP BY game_id, team_id
        ORDER BY game_time_us, game_id, defending_team_id
        """
    )


def _create_rotation_features(connection: Any, *, temporary: bool = False) -> None:
    table_kind = "TEMP TABLE" if temporary else "TABLE"
    connection.execute(
        f"""
        CREATE OR REPLACE {table_kind} ml_player_game_rotation_features_v1 AS
        WITH history AS (
          SELECT core.player_game_id,
                 row_number() OVER (PARTITION BY core.player_game_id
                   ORDER BY obs.game_time_us DESC, obs.game_id DESC) AS recency,
                 obs.*
          FROM ml_player_game_core_features_v1 AS core
          JOIN player_game_rotation_observations_v1 AS obs
            ON obs.player_id = core.player_id
           AND obs.game_time_us < epoch_us(core.feature_cutoff_time)
        ), agg AS (
          SELECT player_game_id, count(*) AS rot_games_before,
            count(*) FILTER (WHERE recency <= 3) AS rot_last3_games_n,
            count(*) FILTER (WHERE recency <= 5) AS rot_last5_games_n,
            max(stint_count) FILTER (WHERE recency = 1) AS rot_last1_stint_count,
            avg(stint_count) FILTER (WHERE recency <= 3) AS rot_last3_stint_count_avg,
            avg(stint_count) FILTER (WHERE recency <= 5) AS rot_last5_stint_count_avg,
            avg(avg_stint_seconds) FILTER (WHERE recency <= 3) /60.0
                AS rot_last3_avg_stint_minutes,
            avg(avg_stint_seconds) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_avg_stint_minutes,
            avg(longest_stint_seconds) FILTER (WHERE recency <= 3) /60.0
                AS rot_last3_longest_stint_minutes_avg,
            avg(longest_stint_seconds) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_longest_stint_minutes_avg,
            avg(first_sub_out_second) FILTER (WHERE recency <= 3) /60.0
                AS rot_last3_first_sub_out_minutes_avg,
            avg(first_sub_out_second) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_first_sub_out_minutes_avg,
            avg(first_entry_second) FILTER (WHERE recency <= 3) /60.0
                AS rot_last3_first_entry_minutes_avg,
            avg(first_entry_second) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_first_entry_minutes_avg,
            avg(first_stint_seconds) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_first_stint_minutes_avg,
            avg(rotation_seconds) FILTER (WHERE recency <= 5) /60.0
                AS rot_last5_minutes_avg,
            sum(rotation_seconds) FILTER (WHERE recency <= 5)
                / nullif(sum(game_seconds) FILTER (WHERE recency <= 5), 0)
                AS rot_last5_minutes_share,
            sum(rotation_seconds) FILTER (WHERE season = core_season)
                / nullif(sum(game_seconds) FILTER (WHERE season = core_season), 0)
                AS rot_season_minutes_share_before,
            avg(closing_lineup::INTEGER) FILTER (WHERE recency <= 5)
                AS rot_last5_closing_lineup_rate,
            avg(closing_lineup::INTEGER) FILTER (WHERE season = core_season)
                AS rot_season_closing_lineup_rate_before,
            avg(starter::INTEGER) FILTER (WHERE recency <= 5)
                AS rot_last5_starter_rate,
            stddev_samp(rotation_seconds/60.0) FILTER (WHERE recency <= 5)
                AS rot_last5_minutes_std,
            stddev_samp(stint_count) FILTER (WHERE recency <= 5)
                AS rot_last5_stint_count_std,
            avg(rotation_seconds/60.0) FILTER (WHERE recency <= 3)
              - avg(rotation_seconds/60.0) FILTER (WHERE recency BETWEEN 4 AND 6)
                AS rot_minutes_trend,
            sum((rotation_seconds/60.0) * pow(0.65, recency-1))
              / nullif(sum(pow(0.65, recency-1)),0) AS rot_minutes_ewma,
            max(source_game_time_us) AS rot_source_max_game_time_us
          FROM (
            SELECT history.*, core.season AS core_season
            FROM history JOIN ml_player_game_core_features_v1 AS core
              USING (player_game_id)
          ) GROUP BY player_game_id
        )
        SELECT core.player_game_id,
               coalesce(agg.rot_games_before,0) AS rot_games_before,
               coalesce(agg.rot_last3_games_n,0) AS rot_last3_games_n,
               coalesce(agg.rot_last5_games_n,0) AS rot_last5_games_n,
               agg.* EXCLUDE (player_game_id, rot_games_before,
                              rot_last3_games_n, rot_last5_games_n),
               CASE WHEN agg.rot_last5_minutes_std IS NOT NULL
                          AND agg.rot_last5_stint_count_std IS NOT NULL
                    THEN 1.0 / (1.0 + agg.rot_last5_minutes_std/10.0
                                      + agg.rot_last5_stint_count_std/3.0)
               END AS rot_role_stability
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN agg USING (player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )


def _create_onoff_features(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE ml_player_game_onoff_features_v1 AS
        WITH history AS (
          SELECT core.player_game_id, core.season AS core_season,
                 row_number() OVER (PARTITION BY core.player_game_id
                   ORDER BY obs.game_time_us DESC, obs.game_id DESC) AS recency,
                 obs.*
          FROM ml_player_game_core_features_v1 AS core
          JOIN player_game_onoff_observations_v1 AS obs
            ON obs.player_id = core.player_id
           AND obs.game_time_us < epoch_us(core.feature_cutoff_time)
        ), agg AS (
          SELECT player_game_id, count(*) AS onoff_games_before,
            count(*) FILTER (WHERE recency<=5) AS onoff_last5_games_n,
            sum(on_possessions) FILTER (WHERE season=core_season)
                AS onoff_season_on_possessions_n,
            sum(off_possessions) FILTER (WHERE season=core_season)
                AS onoff_season_off_possessions_n,
            sum(on_points_for) FILTER (WHERE season=core_season) AS season_on_pf,
            sum(on_points_against) FILTER (WHERE season=core_season) AS season_on_pa,
            sum(off_points_for) FILTER (WHERE season=core_season) AS season_off_pf,
            sum(off_points_against) FILTER (WHERE season=core_season) AS season_off_pa,
            sum(on_game_possessions) FILTER (WHERE season=core_season) AS season_on_game_poss,
            sum(on_seconds) FILTER (WHERE season=core_season) AS season_on_seconds,
            sum(on_possessions) FILTER (WHERE recency<=5) AS last5_on_poss,
            sum(on_points_for) FILTER (WHERE recency<=5) AS last5_on_pf,
            sum(on_points_against) FILTER (WHERE recency<=5) AS last5_on_pa,
            sum(on_possessions) FILTER (WHERE recency<=10) AS last10_on_poss,
            sum(on_points_for) FILTER (WHERE recency<=10) AS last10_on_pf,
            sum(on_points_against) FILTER (WHERE recency<=10) AS last10_on_pa,
            max(source_game_time_us) AS onoff_source_max_game_time_us
          FROM history GROUP BY player_game_id
        )
        SELECT core.player_game_id,
          coalesce(agg.onoff_games_before,0) AS onoff_games_before,
          coalesce(agg.onoff_last5_games_n,0) AS onoff_last5_games_n,
          agg.onoff_season_on_possessions_n,
          agg.onoff_season_off_possessions_n,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_on_pf/agg.onoff_season_on_possessions_n END
            AS onoff_season_on_ortg,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_on_pa/agg.onoff_season_on_possessions_n END
            AS onoff_season_on_drtg,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*(agg.season_on_pf-agg.season_on_pa)
              /agg.onoff_season_on_possessions_n END AS onoff_season_on_net_rating,
          CASE WHEN agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_off_pf/agg.onoff_season_off_possessions_n END
            AS onoff_season_off_ortg,
          CASE WHEN agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_off_pa/agg.onoff_season_off_possessions_n END
            AS onoff_season_off_drtg,
          CASE WHEN agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*(agg.season_off_pf-agg.season_off_pa)
              /agg.onoff_season_off_possessions_n END AS onoff_season_off_net_rating,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
             AND agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_on_pf/agg.onoff_season_on_possessions_n
               - 100*agg.season_off_pf/agg.onoff_season_off_possessions_n END
            AS onoff_season_ortg_diff,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
             AND agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*agg.season_on_pa/agg.onoff_season_on_possessions_n
               - 100*agg.season_off_pa/agg.onoff_season_off_possessions_n END
            AS onoff_season_drtg_diff,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
             AND agg.onoff_season_off_possessions_n >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*(agg.season_on_pf-agg.season_on_pa)
                       /agg.onoff_season_on_possessions_n
               - 100*(agg.season_off_pf-agg.season_off_pa)
                       /agg.onoff_season_off_possessions_n END
            AS onoff_season_net_rating_diff,
          CASE WHEN agg.onoff_season_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
             AND agg.season_on_seconds>0
            THEN agg.season_on_game_poss*2400.0/agg.season_on_seconds END
            AS onoff_season_on_court_pace,
          agg.last5_on_poss AS onoff_last5_on_possessions_n,
          CASE WHEN agg.last5_on_poss >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*(agg.last5_on_pf-agg.last5_on_pa)/agg.last5_on_poss END
            AS onoff_last5_on_court_net_rating,
          agg.last10_on_poss AS onoff_last10_on_possessions_n,
          CASE WHEN agg.last10_on_poss >= {MIN_ONOFF_POSSESSIONS}
            THEN 100*(agg.last10_on_pf-agg.last10_on_pa)/agg.last10_on_poss END
            AS onoff_last10_on_court_net_rating,
          agg.onoff_source_max_game_time_us
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN agg USING (player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )


def _create_lineup_features(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE ml_player_game_lineup_features_v1 AS
        WITH history AS (
          SELECT core.player_game_id,
                 row_number() OVER (PARTITION BY core.player_game_id
                   ORDER BY obs.game_time_us DESC, obs.game_id DESC) AS recency,
                 obs.*
          FROM ml_player_game_core_features_v1 AS core
          JOIN player_game_lineup_observations_v1 AS obs
            ON obs.player_id=core.player_id
           AND obs.game_time_us < epoch_us(core.feature_cutoff_time)
        ), agg AS (
          SELECT player_game_id, count(*) AS lineup_games_before,
            count(*) FILTER(WHERE recency<=5) AS lineup_last5_games_n,
            avg(unique_lineups) FILTER(WHERE recency<=5)
                AS lineup_last5_unique_lineups_avg,
            avg(top_lineup_share) FILTER(WHERE recency<=5)
                AS lineup_last5_top_lineup_share_avg,
            avg(top3_lineup_share) FILTER(WHERE recency<=5)
                AS lineup_last5_top3_lineup_share_avg,
            avg(lineup_entropy) FILTER(WHERE recency<=5)
                AS lineup_last5_entropy_avg,
            avg(player_top_lineup_share) FILTER(WHERE recency<=5)
                AS lineup_last5_player_top_lineup_share_avg,
            avg(top_lineup_continuity) FILTER(WHERE recency<=5)
                AS lineup_last5_continuity_avg,
            avg(top_teammate_shared_seconds) FILTER(WHERE recency<=5) /60.0
                AS teammate_last5_top_shared_minutes_avg,
            avg(top3_teammates_shared_seconds) FILTER(WHERE recency<=5) /60.0
                AS teammate_last5_top3_shared_minutes_avg,
            avg(teammate_minutes_concentration) FILTER(WHERE recency<=5)
                AS teammate_last5_minutes_concentration_avg,
            avg(stable_teammate_count) FILTER(WHERE recency<=5)
                AS teammate_last5_stable_count_avg,
            max(source_game_time_us) AS lineup_source_max_game_time_us
          FROM history GROUP BY player_game_id
        )
        SELECT core.player_game_id,
          coalesce(agg.lineup_games_before,0) AS lineup_games_before,
          coalesce(agg.lineup_last5_games_n,0) AS lineup_last5_games_n,
          agg.* EXCLUDE(player_game_id,lineup_games_before,lineup_last5_games_n)
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN agg USING(player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )


def _create_shot_features(connection: Any) -> None:
    zones = ("rim", "paint", "midrange", "corner3", "above_break3")
    sums = ",\n".join(
        f"sum({zone}_attempts) FILTER(WHERE season=core_season) AS {zone}_attempts, "
        f"sum({zone}_makes) FILTER(WHERE season=core_season) AS {zone}_makes, "
        f"sum({zone}_attempts) FILTER(WHERE recency<=5) AS last5_{zone}_attempts"
        for zone in zones
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE ml_player_game_shot_features_v1 AS
        WITH history AS (
          SELECT core.player_game_id, core.season AS core_season,
                 row_number() OVER(PARTITION BY core.player_game_id
                   ORDER BY obs.game_time_us DESC, obs.game_id DESC) AS recency,
                 obs.*
          FROM ml_player_game_core_features_v1 AS core
          JOIN player_game_shot_profile_observations_v1 AS obs
            ON obs.player_id=core.player_id
           AND obs.game_time_us < epoch_us(core.feature_cutoff_time)
        ), agg AS (
          SELECT player_game_id, count(*) AS shot_games_before,
            count(*) FILTER(WHERE recency<=5) AS shot_last5_games_n,
            count(*) FILTER(WHERE recency<=10) AS shot_last10_games_n,
            sum(attempts) AS all_attempts,
            sum(attempts) FILTER(WHERE season=core_season) AS attempts,
            sum(makes) FILTER(WHERE season=core_season) AS makes,
            sum(points) FILTER(WHERE season=core_season) AS points,
            sum(two_pa) FILTER(WHERE season=core_season) AS two_pa,
            sum(three_pa) FILTER(WHERE season=core_season) AS three_pa,
            sum(distance_sum_m) FILTER(WHERE season=core_season) AS distance_sum_m,
            sum(attempts) FILTER(WHERE recency<=5) AS last5_attempts,
            sum(attempts) FILTER(WHERE recency<=10) AS last10_attempts,
            {sums},
            max(source_game_time_us) AS shot_source_max_game_time_us
          FROM history GROUP BY player_game_id
        ), rate AS (
          SELECT *,
            rim_attempts/nullif(attempts,0) AS rim_rate,
            paint_attempts/nullif(attempts,0) AS paint_rate,
            midrange_attempts/nullif(attempts,0) AS midrange_rate,
            corner3_attempts/nullif(attempts,0) AS corner3_rate,
            above_break3_attempts/nullif(attempts,0) AS above_break3_rate,
            last5_rim_attempts/nullif(last5_attempts,0) AS last5_rim_rate,
            last5_paint_attempts/nullif(last5_attempts,0) AS last5_paint_rate,
            last5_midrange_attempts/nullif(last5_attempts,0) AS last5_midrange_rate,
            last5_corner3_attempts/nullif(last5_attempts,0) AS last5_corner3_rate,
            last5_above_break3_attempts/nullif(last5_attempts,0) AS last5_above_break3_rate
          FROM agg
        )
        SELECT core.player_game_id,
          coalesce(rate.shot_games_before,0) AS shot_games_before,
          coalesce(rate.shot_last5_games_n,0) AS shot_last5_games_n,
          coalesce(rate.shot_last10_games_n,0) AS shot_last10_games_n,
          rate.all_attempts AS shot_attempts_before,
          rate.last5_attempts AS shot_last5_attempts_n,
          rate.last10_attempts AS shot_last10_attempts_n,
          rate.rim_attempts AS shot_season_rim_attempts,
          rate.paint_attempts AS shot_season_paint_attempts,
          rate.midrange_attempts AS shot_season_midrange_attempts,
          rate.corner3_attempts AS shot_season_corner3_attempts,
          rate.above_break3_attempts AS shot_season_above_break3_attempts,
          rate.rim_rate AS shot_season_rim_attempt_rate,
          rate.paint_rate AS shot_season_paint_attempt_rate,
          rate.midrange_rate AS shot_season_midrange_attempt_rate,
          rate.corner3_rate AS shot_season_corner3_attempt_rate,
          rate.above_break3_rate AS shot_season_above_break3_attempt_rate,
          rate.last5_rim_rate AS shot_last5_rim_attempt_rate,
          rate.last5_paint_rate AS shot_last5_paint_attempt_rate,
          rate.last5_midrange_rate AS shot_last5_midrange_attempt_rate,
          rate.last5_corner3_rate AS shot_last5_corner3_attempt_rate,
          rate.last5_above_break3_rate AS shot_last5_above_break3_attempt_rate,
          rate.distance_sum_m/nullif(rate.attempts,0) AS shot_avg_distance_m,
          rate.rim_makes/nullif(rate.rim_attempts,0) AS shot_rim_fg_pct,
          rate.paint_makes/nullif(rate.paint_attempts,0) AS shot_paint_fg_pct,
          rate.midrange_makes/nullif(rate.midrange_attempts,0) AS shot_midrange_fg_pct,
          rate.corner3_makes/nullif(rate.corner3_attempts,0) AS shot_corner3_fg_pct,
          rate.above_break3_makes/nullif(rate.above_break3_attempts,0)
              AS shot_above_break3_fg_pct,
          rate.three_pa/nullif(rate.attempts,0) AS shot_3pa_share,
          rate.two_pa/nullif(rate.attempts,0) AS shot_2pa_share,
          rate.points/nullif(rate.attempts,0) AS shot_points_per_shot,
          pow(rate.rim_rate,2)+pow(rate.paint_rate,2)+pow(rate.midrange_rate,2)
            +pow(rate.corner3_rate,2)+pow(rate.above_break3_rate,2)
              AS shot_zone_concentration,
          -(CASE WHEN rate.rim_rate>0 THEN rate.rim_rate*ln(rate.rim_rate) ELSE 0 END
            +CASE WHEN rate.paint_rate>0 THEN rate.paint_rate*ln(rate.paint_rate) ELSE 0 END
            +CASE WHEN rate.midrange_rate>0 THEN rate.midrange_rate*ln(rate.midrange_rate) ELSE 0 END
            +CASE WHEN rate.corner3_rate>0 THEN rate.corner3_rate*ln(rate.corner3_rate) ELSE 0 END
            +CASE WHEN rate.above_break3_rate>0 THEN rate.above_break3_rate*ln(rate.above_break3_rate) ELSE 0 END)
              AS shot_profile_entropy,
          (abs(rate.last5_rim_rate-rate.rim_rate)
            +abs(rate.last5_paint_rate-rate.paint_rate)
            +abs(rate.last5_midrange_rate-rate.midrange_rate)
            +abs(rate.last5_corner3_rate-rate.corner3_rate)
            +abs(rate.last5_above_break3_rate-rate.above_break3_rate))/2.0
              AS shot_recent_vs_season_mix_change,
          rate.shot_source_max_game_time_us
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN rate USING(player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )


def _create_opponent_shot_features(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE ml_player_game_opponent_shot_features_v1 AS
        WITH history AS (
          SELECT core.player_game_id, core.season AS core_season,
                 row_number() OVER(PARTITION BY core.player_game_id
                   ORDER BY obs.game_time_us DESC, obs.game_id DESC) AS recency,
                 obs.*
          FROM ml_player_game_core_features_v1 AS core
          JOIN team_game_shot_profile_observations_v1 AS obs
            ON obs.defending_team_id=core.opponent_team_id
           AND obs.game_time_us < epoch_us(core.feature_cutoff_time)
        ), agg AS (
          SELECT player_game_id, count(*) AS games_before,
            count(*) FILTER(WHERE recency<=5) AS last5_games_n,
            sum(attempts) AS all_attempts,
            sum(attempts) FILTER(WHERE season=core_season) AS attempts,
            sum(distance_sum_m) FILTER(WHERE season=core_season) AS distance_sum,
            sum(rim_attempts) FILTER(WHERE season=core_season) AS rim_att,
            sum(rim_makes) FILTER(WHERE season=core_season) AS rim_make,
            sum(paint_attempts) FILTER(WHERE season=core_season) AS paint_att,
            sum(paint_makes) FILTER(WHERE season=core_season) AS paint_make,
            sum(midrange_attempts) FILTER(WHERE season=core_season) AS mid_att,
            sum(midrange_makes) FILTER(WHERE season=core_season) AS mid_make,
            sum(corner3_attempts) FILTER(WHERE season=core_season) AS corner_att,
            sum(corner3_makes) FILTER(WHERE season=core_season) AS corner_make,
            sum(above_break3_attempts) FILTER(WHERE season=core_season) AS above_att,
            sum(above_break3_makes) FILTER(WHERE season=core_season) AS above_make,
            sum(attempts) FILTER(WHERE recency<=5) AS last5_att,
            sum(rim_attempts) FILTER(WHERE recency<=5) AS last5_rim,
            sum(paint_attempts) FILTER(WHERE recency<=5) AS last5_paint,
            sum(midrange_attempts) FILTER(WHERE recency<=5) AS last5_mid,
            sum(corner3_attempts) FILTER(WHERE recency<=5) AS last5_corner,
            sum(above_break3_attempts) FILTER(WHERE recency<=5) AS last5_above,
            max(source_game_time_us) AS source_max
          FROM history GROUP BY player_game_id
        )
        SELECT core.player_game_id,
          coalesce(agg.games_before,0) AS opp_shot_games_before,
          coalesce(agg.last5_games_n,0) AS opp_shot_last5_games_n,
          agg.all_attempts AS opp_shot_attempts_before,
          agg.rim_att AS opp_shot_season_rim_attempts_allowed,
          agg.paint_att AS opp_shot_season_paint_attempts_allowed,
          agg.mid_att AS opp_shot_season_midrange_attempts_allowed,
          agg.corner_att AS opp_shot_season_corner3_attempts_allowed,
          agg.above_att AS opp_shot_season_above_break3_attempts_allowed,
          agg.rim_att/nullif(agg.attempts,0) AS opp_shot_season_rim_attempt_rate_allowed,
          agg.paint_att/nullif(agg.attempts,0) AS opp_shot_season_paint_attempt_rate_allowed,
          agg.mid_att/nullif(agg.attempts,0) AS opp_shot_season_midrange_attempt_rate_allowed,
          agg.corner_att/nullif(agg.attempts,0) AS opp_shot_season_corner3_attempt_rate_allowed,
          agg.above_att/nullif(agg.attempts,0) AS opp_shot_season_above_break3_attempt_rate_allowed,
          agg.rim_make/nullif(agg.rim_att,0) AS opp_shot_rim_fg_allowed,
          agg.paint_make/nullif(agg.paint_att,0) AS opp_shot_paint_fg_allowed,
          agg.mid_make/nullif(agg.mid_att,0) AS opp_shot_midrange_fg_allowed,
          agg.corner_make/nullif(agg.corner_att,0) AS opp_shot_corner3_fg_allowed,
          agg.above_make/nullif(agg.above_att,0) AS opp_shot_above_break3_fg_allowed,
          agg.distance_sum/nullif(agg.attempts,0) AS opp_shot_avg_distance_allowed_m,
          agg.last5_rim/nullif(agg.last5_att,0) AS opp_shot_last5_rim_attempt_rate_allowed,
          agg.last5_paint/nullif(agg.last5_att,0) AS opp_shot_last5_paint_attempt_rate_allowed,
          agg.last5_mid/nullif(agg.last5_att,0) AS opp_shot_last5_midrange_attempt_rate_allowed,
          agg.last5_corner/nullif(agg.last5_att,0) AS opp_shot_last5_corner3_attempt_rate_allowed,
          agg.last5_above/nullif(agg.last5_att,0) AS opp_shot_last5_above_break3_attempt_rate_allowed,
          agg.source_max AS opp_shot_source_max_game_time_us
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN agg USING(player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )


def _create_core_plus_rich(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE ml_player_game_rich_features_v1 AS
        SELECT core.player_game_id AS model_row_id,
               core.player_game_id, core.season, core.game_id, core.player_id,
               core.target_game_time, core.feature_cutoff_time,
               core.target_rule_version, core.feature_pipeline_version,
               '{RICH_FEATURE_PIPELINE_VERSION}' AS rich_feature_pipeline_version,
               '{ROTATION_RECONSTRUCTION_VERSION}' AS rotation_family_version,
               '{POSSESSION_VERSION}' AS onoff_family_version,
               '{LINEUP_VERSION}' AS lineup_family_version,
               '{SHOT_GEOMETRY_VERSION}' AS shot_family_version,
               rotation.* EXCLUDE(player_game_id),
               onoff.* EXCLUDE(player_game_id),
               lineup.* EXCLUDE(player_game_id),
               shot.* EXCLUDE(player_game_id),
               opponent.* EXCLUDE(player_game_id),
               shot.shot_season_rim_attempt_rate
                 * opponent.opp_shot_season_rim_attempt_rate_allowed
                   AS interaction_rim_style,
               shot.shot_3pa_share
                 * (opponent.opp_shot_season_corner3_attempt_rate_allowed
                    + opponent.opp_shot_season_above_break3_attempt_rate_allowed)
                   AS interaction_3pa_style,
               shot.shot_season_corner3_attempt_rate
                 * opponent.opp_shot_season_corner3_attempt_rate_allowed
                   AS interaction_corner3_style,
               rotation.rot_games_before > 0 AS has_rotation_features,
               onoff.onoff_games_before > 0
                 AND onoff.onoff_last5_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
                   AS has_onoff_features,
               lineup.lineup_games_before > 0 AS has_lineup_features,
               shot.shot_games_before > 0 AS has_shot_profile_features,
               opponent.opp_shot_games_before > 0 AS has_opp_shot_features,
               (rotation.rot_games_before > 0 OR onoff.onoff_games_before > 0
                 OR lineup.lineup_games_before > 0 OR shot.shot_games_before > 0
                 OR opponent.opp_shot_games_before > 0) AS rich_any_available,
               (rotation.rot_games_before > 0
                 AND onoff.onoff_last5_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
                 AND lineup.lineup_games_before > 0 AND shot.shot_games_before > 0
                 AND opponent.opp_shot_games_before > 0)
                   AS rich_all_major_families_available,
               (core.season IN ('E2022','E2023','E2024','E2025')
                 AND core.actual_fantasy_points IS NOT NULL
                 AND rotation.rot_games_before > 0
                 AND onoff.onoff_last5_on_possessions_n >= {MIN_ONOFF_POSSESSIONS}
                 AND lineup.lineup_games_before > 0 AND shot.shot_games_before > 0
                 AND opponent.opp_shot_games_before > 0)
                   AS phase3b_primary_eval_eligible
        FROM ml_player_game_core_features_v1 AS core
        JOIN ml_player_game_rotation_features_v1 AS rotation USING(player_game_id)
        JOIN ml_player_game_onoff_features_v1 AS onoff USING(player_game_id)
        JOIN ml_player_game_lineup_features_v1 AS lineup USING(player_game_id)
        JOIN ml_player_game_shot_features_v1 AS shot USING(player_game_id)
        JOIN ml_player_game_opponent_shot_features_v1 AS opponent USING(player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id;

        CREATE OR REPLACE VIEW ml_player_game_rich_features AS
        SELECT * FROM ml_player_game_rich_features_v1;

        CREATE OR REPLACE TABLE ml_player_game_core_plus_rich_v1 AS
        SELECT core.player_game_id AS model_row_id, core.*,
               rich.* EXCLUDE(model_row_id, player_game_id, season, game_id,
                              player_id, target_game_time, feature_cutoff_time,
                              target_rule_version, feature_pipeline_version)
        FROM ml_player_game_core_features_with_fantasy_v1 AS core
        JOIN ml_player_game_rich_features_v1 AS rich USING(player_game_id)
        ORDER BY core.target_game_time, core.game_id, core.player_id;

        CREATE OR REPLACE VIEW ml_player_game_core_plus_rich AS
        SELECT * FROM ml_player_game_core_plus_rich_v1;

        CREATE OR REPLACE TABLE ml_phase3b_primary_evaluation_v1 AS
        SELECT combined.*, prediction.prediction_season_average,
               prediction.prediction_last_3, prediction.prediction_last_5,
               prediction.prediction_ewma,
               prediction.prediction_fp_per_min_x_minutes,
               prediction.prediction_season_recent_blend,
               prediction.baseline_version
        FROM ml_player_game_core_plus_rich_v1 AS combined
        JOIN ml_baseline_predictions_v1 AS prediction
          ON prediction.player_game_id=combined.player_game_id
         AND prediction.history_window_seasons=8
        WHERE combined.phase3b_primary_eval_eligible
        ORDER BY combined.target_game_time, combined.game_id, combined.player_id
        """
    )
    violations = validate_rich_cutoffs(connection, fail=False)
    if any(violations.values()):
        raise ValueError(f"Rich feature leakage detected: {violations}")
