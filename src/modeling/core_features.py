"""Reproducible box-score-only Phase 3A feature generation in DuckDB."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .fantasy_scoring import TARGET_RULE_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DERIVED_ROOT = PROJECT_ROOT / "data" / "derived" / "phase3a"
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "phase3a"
FEATURE_PIPELINE_VERSION = "phase3a_core_boxscore_v1"
CUTOFF_POLICY = "STRICTLY_BEFORE_TARGET_GAME_TIME"
EWMA_ALPHA = 0.35
MIN_RATE_MINUTES = 10.0


def estimated_possessions(
    field_goal_attempts: float,
    offensive_rebounds: float,
    turnovers: float,
    free_throw_attempts: float,
) -> float:
    """Box-score possession estimate used by the Phase 3A team features."""

    return (
        field_goal_attempts
        - offensive_rebounds
        + turnovers
        + 0.44 * free_throw_attempts
    )


def pace_per_40(possessions: float, game_minutes: float) -> float:
    """Normalize possessions to 40 minutes, including explicit OT duration."""

    if game_minutes < 40:
        raise ValueError("EuroLeague game duration cannot be below 40 minutes")
    return possessions * 40.0 / game_minutes


@dataclass(frozen=True, slots=True)
class FeatureBuildSummary:
    target_rows: int
    valid_target_rows: int
    modelling_rows: int
    fantasy_rows: int
    e2025_price_rows: int
    dataset_fingerprint: str
    generated_at: str


def generate_targets(database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
    """Materialize player-game targets without modifying canonical facts."""

    with connect_database(database_path) as connection:
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE ml_player_game_targets_v1 AS
            WITH scored AS (
                SELECT
                    stat.player_game_id,
                    game.season_code AS season,
                    season.start_year AS season_start_year,
                    game.round_number,
                    game.canonical_game_id AS game_id,
                    game.game_code,
                    game.game_date AS target_game_time,
                    game.local_game_date,
                    stat.canonical_player_id AS player_id,
                    stat.canonical_team_id AS team_id,
                    stat.opponent_team_id,
                    stat.home_away,
                    stat.minutes AS target_minutes,
                    stat.starter AS target_started,
                    stat.did_not_play,
                    stat.source_is_playing,
                    CASE
                        WHEN stat.minutes > 0 AND NOT stat.did_not_play THEN 'PLAYED'
                        WHEN stat.did_not_play THEN 'DNP'
                        WHEN coalesce(stat.minutes, 0) = 0 THEN 'UNKNOWN'
                        ELSE 'UNKNOWN'
                    END AS eligibility_status,
                    CASE
                        WHEN stat.canonical_team_id = game.home_team_id
                            THEN game.home_score > game.away_score
                        WHEN stat.canonical_team_id = game.away_team_id
                            THEN game.away_score > game.home_score
                        ELSE NULL
                    END AS team_won,
                    stat.points + stat.total_rebounds + stat.assists
                        + stat.steals + stat.blocks + stat.fouls_drawn
                        - stat.turnovers - stat.blocks_received
                        - stat.fouls_committed
                        - (stat.two_points_attempted - stat.two_points_made)
                        - (stat.three_points_attempted - stat.three_points_made)
                        - (stat.free_throws_attempted - stat.free_throws_made)
                        AS fantasy_base_score,
                    stat.points,
                    stat.two_points_made,
                    stat.two_points_attempted,
                    stat.three_points_made,
                    stat.three_points_attempted,
                    stat.free_throws_made,
                    stat.free_throws_attempted,
                    stat.offensive_rebounds,
                    stat.defensive_rebounds,
                    stat.total_rebounds,
                    stat.assists,
                    stat.steals,
                    stat.turnovers,
                    stat.blocks,
                    stat.blocks_received,
                    stat.fouls_committed,
                    stat.fouls_drawn,
                    stat.source_artifact_id
                FROM player_game_stats AS stat
                JOIN games AS game USING (canonical_game_id)
                JOIN seasons AS season USING (season_id)
                WHERE game.season_code BETWEEN 'E2018' AND 'E2025'
                  AND game.played
            ), final AS (
                SELECT *,
                    CASE WHEN eligibility_status = 'PLAYED' THEN
                        cast(
                            fantasy_base_score
                            + CASE WHEN team_won
                                THEN abs(fantasy_base_score) * 0.1 ELSE 0 END
                            AS DOUBLE
                        )
                    END AS standardized_fantasy_points,
                    CASE
                        WHEN season IN ('E2022','E2023','E2024')
                            THEN 'OFFICIAL_HISTORICAL_OUTCOME_VALIDATED'
                        WHEN season = 'E2025'
                            THEN 'OFFICIAL_CURRENT_RULE_AND_SAMPLES_VALIDATED'
                        ELSE 'STANDARDIZED_COUNTERFACTUAL_HISTORICAL_RULE_UNVERIFIED'
                    END AS target_rule_status
                FROM scored
            )
            SELECT
                player_game_id,
                season,
                season_start_year,
                round_number,
                game_id,
                game_code,
                target_game_time,
                local_game_date,
                target_game_time AS feature_cutoff_time,
                '{CUTOFF_POLICY}' AS cutoff_policy,
                player_id,
                team_id,
                opponent_team_id,
                home_away,
                target_minutes,
                target_started,
                did_not_play,
                source_is_playing,
                eligibility_status,
                team_won,
                CASE WHEN eligibility_status = 'PLAYED'
                    THEN fantasy_base_score END AS fantasy_base_score,
                CASE WHEN eligibility_status = 'PLAYED' AND team_won
                    THEN abs(fantasy_base_score) * 0.1
                    WHEN eligibility_status = 'PLAYED' THEN 0 END
                    AS fantasy_team_win_bonus,
                standardized_fantasy_points,
                CASE WHEN season IN ('E2022','E2023','E2024','E2025')
                    THEN standardized_fantasy_points END
                    AS actual_fantasy_points,
                '{TARGET_RULE_VERSION}' AS target_rule_version,
                target_rule_status,
                points,
                two_points_made,
                two_points_attempted,
                three_points_made,
                three_points_attempted,
                free_throws_made,
                free_throws_attempted,
                offensive_rebounds,
                defensive_rebounds,
                total_rebounds,
                assists,
                steals,
                turnovers,
                blocks,
                blocks_received,
                fouls_committed,
                fouls_drawn,
                source_artifact_id
            FROM final
            ORDER BY target_game_time NULLS LAST, game_id, player_id
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW ml_player_game_targets AS
            SELECT * FROM ml_player_game_targets_v1
            """
        )


def generate_core_features(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> None:
    """Materialize strictly lagged player, team, opponent, and context features."""

    with connect_database(database_path) as connection:
        _create_player_features(connection)
        _create_team_features(connection)
        _create_core_table(connection)
        _create_fantasy_extension(connection)
        _create_walk_forward_splits(connection)


def build_phase3a_datasets(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> FeatureBuildSummary:
    generate_targets(database_path)
    generate_core_features(database_path)
    derived_root.mkdir(parents=True, exist_ok=True)
    sample_root.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    with connect_database(database_path) as connection:
        for table in (
            "ml_player_game_targets_v1",
            "ml_player_game_core_features_v1",
            "ml_player_game_core_features_with_fantasy_v1",
            "ml_walk_forward_splits_v1",
        ):
            target = derived_root / f"{table}.parquet"
            connection.execute(
                f"COPY (SELECT * FROM {table}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(target)],
            )
        target_rows = int(
            connection.execute("SELECT count(*) FROM ml_player_game_targets_v1").fetchone()[0]
        )
        valid_target_rows = int(
            connection.execute(
                "SELECT count(*) FROM ml_player_game_targets_v1 "
                "WHERE standardized_fantasy_points IS NOT NULL"
            ).fetchone()[0]
        )
        modelling_rows = int(
            connection.execute(
                "SELECT count(*) FROM ml_player_game_core_features_v1"
            ).fetchone()[0]
        )
        fantasy_rows = int(
            connection.execute(
                "SELECT count(*) FROM ml_player_game_core_features_with_fantasy_v1 "
                "WHERE fantasy_id IS NOT NULL"
            ).fetchone()[0]
        )
        e2025_price_rows = int(
            connection.execute(
                "SELECT count(*) FROM ml_player_game_core_features_with_fantasy_v1 "
                "WHERE fantasy_credits_pre_matchday IS NOT NULL"
            ).fetchone()[0]
        )
        fingerprint = dataset_fingerprint(connection)
    summary = FeatureBuildSummary(
        target_rows=target_rows,
        valid_target_rows=valid_target_rows,
        modelling_rows=modelling_rows,
        fantasy_rows=fantasy_rows,
        e2025_price_rows=e2025_price_rows,
        dataset_fingerprint=fingerprint,
        generated_at=generated_at,
    )
    (sample_root / "feature_build_summary.json").write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def dataset_fingerprint(connection: Any) -> str:
    rows = connection.execute(
        """
        SELECT player_game_id, season, game_id, player_id,
               epoch_us(target_game_time),
               standardized_fantasy_points,
               career_el_games_before,
               season_games_before,
               last_5_fp_avg,
               team_season_pace_before,
               opp_season_pace_before
        FROM ml_player_game_core_features_v1
        ORDER BY target_game_time, game_id, player_id
        """
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _create_player_features(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE phase3_player_features AS
        WITH eligible AS (
            SELECT target.*,
                   player.birth_date,
                   target.two_points_attempted + target.three_points_attempted AS fga,
                   target.two_points_made + target.three_points_made AS fgm
            FROM ml_player_game_targets_v1 AS target
            JOIN players AS player
              ON player.canonical_player_id = target.player_id
            WHERE target.eligibility_status = 'PLAYED'
              AND target.target_game_time IS NOT NULL
        ), windowed AS (
            SELECT *,
                count(*) OVER career_w AS career_el_games_before,
                count(*) OVER season_w AS season_games_before,
                avg(standardized_fantasy_points) OVER career_w
                    AS career_el_fp_avg_before,
                avg(standardized_fantasy_points) OVER season_w
                    AS season_fp_avg_before,
                stddev_samp(standardized_fantasy_points) OVER season_w
                    AS season_fp_std_before,
                lag(standardized_fantasy_points) OVER player_order AS last_1_fp,
                avg(standardized_fantasy_points) OVER last3_w AS last_3_fp_avg,
                count(*) OVER last3_w AS last_3_fp_n,
                median(standardized_fantasy_points) OVER last3_w AS last_3_fp_median,
                avg(standardized_fantasy_points) OVER last5_w AS last_5_fp_avg,
                count(*) OVER last5_w AS last_5_fp_n,
                median(standardized_fantasy_points) OVER last5_w AS last_5_fp_median,
                stddev_samp(standardized_fantasy_points) OVER last5_w AS last_5_fp_std,
                min(standardized_fantasy_points) OVER last5_w AS last_5_fp_min,
                max(standardized_fantasy_points) OVER last5_w AS last_5_fp_max,
                avg(standardized_fantasy_points) OVER last10_w AS last_10_fp_avg,
                count(*) OVER last10_w AS last_10_fp_n,
                stddev_samp(standardized_fantasy_points) OVER last10_w AS last_10_fp_std,
                avg(target_minutes) OVER season_w AS season_minutes_avg_before,
                lag(target_minutes) OVER player_order AS last_1_minutes,
                avg(target_minutes) OVER last3_w AS last_3_minutes_avg,
                avg(target_minutes) OVER last5_w AS last_5_minutes_avg,
                avg(target_minutes) OVER last10_w AS last_10_minutes_avg,
                avg(target_minutes) OVER previous3_w AS previous_3_minutes_avg,
                count(*) OVER previous3_w AS previous_3_minutes_n,
                avg(CASE WHEN target_started THEN 1.0 ELSE 0.0 END) OVER season_w
                    AS season_start_rate_before,
                avg(CASE WHEN target_started THEN 1.0 ELSE 0.0 END) OVER last5_w
                    AS last_5_start_rate,
                max(target_game_time) OVER career_w AS player_history_max_game_time,
                sum(standardized_fantasy_points) OVER season_w AS season_fp_sum,
                sum(target_minutes) OVER season_w AS season_minutes_sum,
                sum(standardized_fantasy_points) OVER last3_w AS last3_fp_sum,
                sum(target_minutes) OVER last3_w AS last3_minutes_sum,
                sum(standardized_fantasy_points) OVER last5_w AS last5_fp_sum,
                sum(target_minutes) OVER last5_w AS last5_minutes_sum,
                sum(standardized_fantasy_points) OVER last10_w AS last10_fp_sum,
                sum(target_minutes) OVER last10_w AS last10_minutes_sum,
                sum(points) OVER last5_w AS last5_points_sum,
                sum(total_rebounds) OVER last5_w AS last5_rebounds_sum,
                sum(offensive_rebounds) OVER last5_w AS last5_oreb_sum,
                sum(defensive_rebounds) OVER last5_w AS last5_dreb_sum,
                sum(assists) OVER last5_w AS last5_assists_sum,
                sum(steals) OVER last5_w AS last5_steals_sum,
                sum(blocks) OVER last5_w AS last5_blocks_sum,
                sum(turnovers) OVER last5_w AS last5_turnovers_sum,
                sum(fgm) OVER season_w AS season_fgm_sum,
                sum(fga) OVER season_w AS season_fga_sum,
                sum(two_points_made) OVER season_w AS season_2pm_sum,
                sum(two_points_attempted) OVER season_w AS season_2pa_sum,
                sum(three_points_made) OVER season_w AS season_3pm_sum,
                sum(three_points_attempted) OVER season_w AS season_3pa_sum,
                sum(free_throws_made) OVER season_w AS season_ftm_sum,
                sum(free_throws_attempted) OVER season_w AS season_fta_sum,
                sum(points) OVER season_w AS season_points_sum,
                sum(fgm) OVER last5_w AS last5_fgm_sum,
                sum(fga) OVER last5_w AS last5_fga_sum,
                sum(two_points_made) OVER last5_w AS last5_2pm_sum,
                sum(two_points_attempted) OVER last5_w AS last5_2pa_sum,
                sum(three_points_made) OVER last5_w AS last5_3pm_sum,
                sum(three_points_attempted) OVER last5_w AS last5_3pa_sum,
                sum(free_throws_made) OVER last5_w AS last5_ftm_sum,
                sum(free_throws_attempted) OVER last5_w AS last5_fta_sum,
                sum(points) OVER last5_w AS last5_scoring_points_sum
            FROM eligible
            WINDOW
                player_order AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                ),
                career_w AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ),
                season_w AS (
                    PARTITION BY player_id, season
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ),
                last3_w AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
                ),
                last5_w AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                ),
                last10_w AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
                ),
                previous3_w AS (
                    PARTITION BY player_id
                    ORDER BY target_game_time, game_id
                    ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING
                )
        ), ordinals AS (
            SELECT *, row_number() OVER (
                PARTITION BY player_id ORDER BY target_game_time, game_id
            ) AS player_game_ordinal
            FROM windowed
        ), long_history AS (
            SELECT current.player_game_id,
                avg(history.standardized_fantasy_points) FILTER (
                    WHERE history.season_start_year >= current.season_start_year - 2
                ) AS career_el_fp_avg_before_3s,
                avg(history.standardized_fantasy_points) FILTER (
                    WHERE history.season_start_year >= current.season_start_year - 4
                ) AS career_el_fp_avg_before_5s,
                avg(history.standardized_fantasy_points)
                    AS career_el_fp_avg_before_8s,
                sum(history.standardized_fantasy_points * pow(
                    {1.0 - EWMA_ALPHA},
                    current.player_game_ordinal - history.player_game_ordinal - 1
                )) / nullif(sum(pow(
                    {1.0 - EWMA_ALPHA},
                    current.player_game_ordinal - history.player_game_ordinal - 1
                )), 0) AS fp_ewma,
                sum(history.target_minutes * pow(
                    {1.0 - EWMA_ALPHA},
                    current.player_game_ordinal - history.player_game_ordinal - 1
                )) / nullif(sum(pow(
                    {1.0 - EWMA_ALPHA},
                    current.player_game_ordinal - history.player_game_ordinal - 1
                )), 0) AS minutes_ewma,
                count(*) FILTER (
                    WHERE history.target_game_time >=
                        current.target_game_time - INTERVAL 7 DAY
                ) AS player_games_last_7_calendar_days_el,
                sum(history.target_minutes) FILTER (
                    WHERE history.target_game_time >=
                        current.target_game_time - INTERVAL 7 DAY
                ) AS player_minutes_last_7_calendar_days_el
            FROM ordinals AS current
            LEFT JOIN ordinals AS history
              ON history.player_id = current.player_id
             AND history.target_game_time < current.target_game_time
            GROUP BY current.player_game_id
        )
        SELECT
            row.player_game_id,
            row.season,
            row.season_start_year,
            row.round_number,
            row.game_id,
            row.game_code,
            row.target_game_time,
            row.local_game_date,
            row.feature_cutoff_time,
            row.cutoff_policy,
            row.player_id,
            row.team_id,
            row.opponent_team_id,
            row.home_away,
            row.target_minutes,
            row.target_started,
            row.standardized_fantasy_points,
            row.actual_fantasy_points,
            row.target_rule_version,
            row.target_rule_status,
            '{FEATURE_PIPELINE_VERSION}' AS feature_pipeline_version,
            row.career_el_games_before,
            row.season_games_before,
            row.career_el_games_before >= 1 AS has_1_prior_game,
            row.career_el_games_before >= 3 AS has_3_prior_games,
            row.career_el_games_before >= 5 AS has_5_prior_games,
            row.career_el_games_before >= 10 AS has_10_prior_games,
            row.career_el_fp_avg_before,
            row.season_fp_avg_before,
            row.season_fp_std_before,
            row.last_1_fp,
            row.last_3_fp_avg,
            row.last_3_fp_n,
            row.last_5_fp_avg,
            row.last_5_fp_n,
            row.last_10_fp_avg,
            row.last_10_fp_n,
            row.last_3_fp_median,
            row.last_5_fp_median,
            row.last_5_fp_std,
            row.last_10_fp_std,
            row.last_5_fp_min,
            row.last_5_fp_max,
            history.fp_ewma,
            row.season_minutes_avg_before,
            row.last_1_minutes,
            row.last_3_minutes_avg,
            row.last_5_minutes_avg,
            row.last_10_minutes_avg,
            history.minutes_ewma,
            CASE WHEN row.last_3_fp_n = 3 AND row.previous_3_minutes_n = 3
                THEN row.last_3_minutes_avg - row.previous_3_minutes_avg END
                AS minutes_trend,
            row.season_start_rate_before,
            row.last_5_start_rate,
            CASE WHEN row.season_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.season_fp_sum / row.season_minutes_sum END
                AS season_fp_per_min_before,
            CASE WHEN row.last3_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last3_fp_sum / row.last3_minutes_sum END AS last_3_fp_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_fp_sum / row.last5_minutes_sum END AS last_5_fp_per_min,
            CASE WHEN row.last10_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last10_fp_sum / row.last10_minutes_sum END AS last_10_fp_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_points_sum / row.last5_minutes_sum END AS points_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_rebounds_sum / row.last5_minutes_sum END AS rebounds_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_oreb_sum / row.last5_minutes_sum END AS oreb_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_dreb_sum / row.last5_minutes_sum END AS dreb_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_assists_sum / row.last5_minutes_sum END AS assists_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_steals_sum / row.last5_minutes_sum END AS steals_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_blocks_sum / row.last5_minutes_sum END AS blocks_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_turnovers_sum / row.last5_minutes_sum END AS turnovers_per_min,
            CASE WHEN row.last5_turnovers_sum > 0
                THEN row.last5_assists_sum / row.last5_turnovers_sum END AS ast_to_ratio,
            row.season_2pm_sum / nullif(row.season_2pa_sum, 0) AS season_2p_pct_before,
            row.season_3pm_sum / nullif(row.season_3pa_sum, 0) AS season_3p_pct_before,
            row.season_ftm_sum / nullif(row.season_fta_sum, 0) AS season_ft_pct_before,
            row.season_fgm_sum / nullif(row.season_fga_sum, 0) AS season_fg_pct_before,
            (row.season_fgm_sum + 0.5 * row.season_3pm_sum)
                / nullif(row.season_fga_sum, 0) AS season_efg_pct_before,
            row.season_points_sum /
                nullif(2 * (row.season_fga_sum + 0.44 * row.season_fta_sum), 0)
                AS season_ts_pct_before,
            row.last5_2pm_sum / nullif(row.last5_2pa_sum, 0) AS last_5_2p_pct,
            row.last5_3pm_sum / nullif(row.last5_3pa_sum, 0) AS last_5_3p_pct,
            row.last5_ftm_sum / nullif(row.last5_fta_sum, 0) AS last_5_ft_pct,
            row.last5_fgm_sum / nullif(row.last5_fga_sum, 0) AS last_5_fg_pct,
            (row.last5_fgm_sum + 0.5 * row.last5_3pm_sum)
                / nullif(row.last5_fga_sum, 0) AS last_5_efg_pct,
            row.last5_scoring_points_sum /
                nullif(2 * (row.last5_fga_sum + 0.44 * row.last5_fta_sum), 0)
                AS last_5_ts_pct,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_fga_sum / row.last5_minutes_sum END AS fga_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_2pa_sum / row.last5_minutes_sum END AS two_pa_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_3pa_sum / row.last5_minutes_sum END AS three_pa_per_min,
            CASE WHEN row.last5_minutes_sum >= {MIN_RATE_MINUTES}
                THEN row.last5_fta_sum / row.last5_minutes_sum END AS fta_per_min,
            CASE WHEN row.birth_date IS NOT NULL
                       AND row.birth_date <= cast(row.target_game_time AS DATE)
                THEN date_diff('day', row.birth_date,
                    cast(row.target_game_time AS DATE)) / 365.2425 END
                AS player_age,
            CASE WHEN row.career_el_games_before > 0 THEN
                date_diff('second',
                    lag(row.target_game_time) OVER (
                        PARTITION BY row.player_id
                        ORDER BY row.target_game_time, row.game_id
                    ), row.target_game_time) / 86400.0
                END AS days_since_previous_el_game,
            coalesce(history.player_games_last_7_calendar_days_el, 0)
                AS player_games_last_7_calendar_days_el,
            coalesce(history.player_minutes_last_7_calendar_days_el, 0)
                AS player_minutes_last_7_calendar_days_el,
            row.player_history_max_game_time,
            history.career_el_fp_avg_before_3s,
            history.career_el_fp_avg_before_5s,
            history.career_el_fp_avg_before_8s
        FROM ordinals AS row
        JOIN long_history AS history USING (player_game_id)
        ORDER BY row.target_game_time, row.game_id, row.player_id
        """
    )


def _create_team_features(connection: Any, *, season_end: str = "E2025") -> None:
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE phase3_team_features AS
        WITH paired AS (
            SELECT
                team.team_game_id,
                team.canonical_game_id AS game_id,
                game.season_code AS season,
                game.game_date AS target_game_time,
                game.round_number,
                game.overtime_count,
                team.canonical_team_id AS team_id,
                team.opponent_team_id,
                team.points,
                team.two_points_made + team.three_points_made AS fgm,
                team.two_points_attempted + team.three_points_attempted AS fga,
                team.three_points_made AS three_pm,
                team.three_points_attempted AS three_pa,
                team.free_throws_made AS ftm,
                team.free_throws_attempted AS fta,
                team.offensive_rebounds AS oreb,
                team.defensive_rebounds AS dreb,
                team.total_rebounds AS rebounds,
                team.assists,
                team.turnovers,
                opponent.points AS opp_points,
                opponent.two_points_made + opponent.three_points_made AS opp_fgm,
                opponent.two_points_attempted + opponent.three_points_attempted AS opp_fga,
                opponent.three_points_made AS opp_three_pm,
                opponent.three_points_attempted AS opp_three_pa,
                opponent.free_throws_attempted AS opp_fta,
                opponent.offensive_rebounds AS opp_oreb,
                opponent.defensive_rebounds AS opp_dreb,
                opponent.total_rebounds AS opp_rebounds,
                opponent.assists AS opp_assists,
                opponent.turnovers AS opp_turnovers,
                team.two_points_attempted + team.three_points_attempted
                    + 0.44 * team.free_throws_attempted
                    - team.offensive_rebounds + team.turnovers AS raw_possessions,
                opponent.two_points_attempted + opponent.three_points_attempted
                    + 0.44 * opponent.free_throws_attempted
                    - opponent.offensive_rebounds + opponent.turnovers
                    AS opp_raw_possessions
            FROM team_game_stats AS team
            JOIN team_game_stats AS opponent
              ON opponent.canonical_game_id = team.canonical_game_id
             AND opponent.canonical_team_id = team.opponent_team_id
            JOIN games AS game
              ON game.canonical_game_id = team.canonical_game_id
            WHERE game.played AND game.game_date IS NOT NULL
              AND game.season_code BETWEEN 'E2018' AND '{season_end}'
        ), metrics AS (
            SELECT *,
                (raw_possessions + opp_raw_possessions) / 2.0
                    AS estimated_game_possessions,
                40.0 * ((raw_possessions + opp_raw_possessions) / 2.0)
                    / (40.0 + 5.0 * coalesce(overtime_count, 0)) AS pace_40,
                fgm + 0.5 * three_pm AS efg_made_equivalent,
                fga + 0.44 * fta + turnovers AS tov_play_denominator,
                oreb + opp_dreb AS oreb_opportunity,
                opp_fgm + 0.5 * opp_three_pm AS opp_efg_made_equivalent,
                opp_fga + 0.44 * opp_fta + opp_turnovers
                    AS opp_tov_play_denominator,
                opp_rebounds + rebounds AS all_rebounds
            FROM paired
        ), with_allowance AS (
            SELECT metrics.*,
                allowance.fp_allowed,
                allowance.players_allowed
            FROM metrics
            LEFT JOIN (
                SELECT target.game_id,
                       target.team_id AS scoring_team_id,
                       sum(target.standardized_fantasy_points) AS fp_allowed,
                       count(*) AS players_allowed
                FROM ml_player_game_targets_v1 AS target
                WHERE target.eligibility_status = 'PLAYED'
                GROUP BY target.game_id, target.team_id
            ) AS allowance
              ON allowance.game_id = metrics.game_id
             AND allowance.scoring_team_id = metrics.opponent_team_id
        ), history AS (
            SELECT *,
                count(*) OVER season_w AS team_games_before,
                max(target_game_time) OVER season_w AS team_history_max_game_time,
                lag(target_game_time) OVER team_order AS previous_team_game_time,
                avg(pace_40) OVER season_w AS team_season_pace_before,
                avg(pace_40) OVER last5_w AS team_last5_pace,
                100 * sum(points) OVER season_w /
                    nullif(sum(estimated_game_possessions) OVER season_w, 0)
                    AS team_season_ortg_before,
                100 * sum(points) OVER last5_w /
                    nullif(sum(estimated_game_possessions) OVER last5_w, 0)
                    AS team_last5_ortg,
                100 * sum(opp_points) OVER season_w /
                    nullif(sum(estimated_game_possessions) OVER season_w, 0)
                    AS team_season_drtg_before,
                100 * sum(opp_points) OVER last5_w /
                    nullif(sum(estimated_game_possessions) OVER last5_w, 0)
                    AS team_last5_drtg,
                sum(efg_made_equivalent) OVER season_w /
                    nullif(sum(fga) OVER season_w, 0) AS team_efg_before,
                sum(turnovers) OVER season_w /
                    nullif(sum(tov_play_denominator) OVER season_w, 0)
                    AS team_tov_rate_before,
                sum(oreb) OVER season_w /
                    nullif(sum(oreb_opportunity) OVER season_w, 0)
                    AS team_oreb_rate_before,
                sum(fta) OVER season_w /
                    nullif(sum(fga) OVER season_w, 0) AS team_ft_rate_before,
                sum(assists) OVER season_w /
                    nullif(sum(fgm) OVER season_w, 0) AS team_ast_rate_before,
                sum(opp_efg_made_equivalent) OVER season_w /
                    nullif(sum(opp_fga) OVER season_w, 0) AS efg_allowed_before,
                sum(opp_rebounds) OVER season_w /
                    nullif(sum(all_rebounds) OVER season_w, 0)
                    AS rebound_rate_allowed_before,
                sum(opp_assists) OVER season_w /
                    nullif(sum(opp_fgm) OVER season_w, 0)
                    AS assist_rate_allowed_before,
                sum(opp_turnovers) OVER season_w /
                    nullif(sum(opp_tov_play_denominator) OVER season_w, 0)
                    AS turnover_forced_rate_before,
                sum(opp_three_pa) OVER season_w /
                    nullif(sum(opp_fga) OVER season_w, 0)
                    AS three_pa_rate_allowed_before,
                sum(opp_fta) OVER season_w /
                    nullif(sum(opp_fga) OVER season_w, 0)
                    AS ft_rate_allowed_before,
                sum(fp_allowed) OVER season_w /
                    nullif(sum(players_allowed) OVER season_w, 0)
                    AS fp_allowed_per_player_game,
                sum(fp_allowed) OVER last5_w /
                    nullif(sum(players_allowed) OVER last5_w, 0)
                    AS fp_allowed_last5_games
            FROM with_allowance
            WINDOW
                team_order AS (
                    PARTITION BY team_id ORDER BY target_game_time, game_id
                ),
                season_w AS (
                    PARTITION BY team_id, season ORDER BY target_game_time, game_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ),
                last5_w AS (
                    PARTITION BY team_id ORDER BY target_game_time, game_id
                    ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                )
        )
        SELECT * FROM history
        """
    )


def _create_core_table(
    connection: Any, *, temporary: bool = False, create_view: bool = True
) -> None:
    table_kind = "TEMP TABLE" if temporary else "TABLE"
    connection.execute(
        f"""
        CREATE OR REPLACE {table_kind} ml_player_game_core_features_v1 AS
        SELECT
            player.*,
            player.home_away = 'home' AS home_game,
            player.home_away = 'away' AS away_game,
            team.team_games_before,
            team.team_season_pace_before,
            team.team_last5_pace,
            team.team_season_ortg_before,
            team.team_last5_ortg,
            team.team_season_drtg_before,
            team.team_last5_drtg,
            team.team_efg_before,
            team.team_tov_rate_before,
            team.team_oreb_rate_before,
            team.team_ft_rate_before,
            team.team_ast_rate_before,
            opponent.team_games_before AS opp_games_before,
            opponent.team_season_pace_before AS opp_season_pace_before,
            opponent.team_last5_pace AS opp_last5_pace,
            opponent.team_season_ortg_before AS opp_season_ortg_before,
            opponent.team_last5_ortg AS opp_last5_ortg,
            opponent.team_season_drtg_before AS opp_season_drtg_before,
            opponent.team_last5_drtg AS opp_last5_drtg,
            opponent.efg_allowed_before AS opp_efg_allowed,
            opponent.rebound_rate_allowed_before AS opp_rebound_rate_allowed,
            opponent.assist_rate_allowed_before AS opp_assist_rate_allowed,
            opponent.turnover_forced_rate_before AS opp_turnover_forced_rate,
            opponent.three_pa_rate_allowed_before AS opp_3pa_rate_allowed,
            opponent.ft_rate_allowed_before AS opp_ft_rate_allowed,
            opponent.fp_allowed_per_player_game AS opp_fp_allowed_per_player_game,
            opponent.fp_allowed_last5_games AS opp_fp_allowed_last5_games,
            date_diff('second', team.previous_team_game_time,
                player.target_game_time) / 86400.0
                AS team_days_since_previous_el_game,
            team.team_history_max_game_time,
            opponent.team_history_max_game_time AS opp_history_max_game_time
        FROM phase3_player_features AS player
        JOIN phase3_team_features AS team
          ON team.game_id = player.game_id AND team.team_id = player.team_id
        JOIN phase3_team_features AS opponent
          ON opponent.game_id = player.game_id
         AND opponent.team_id = player.opponent_team_id
        ORDER BY player.target_game_time, player.game_id, player.player_id
        """
    )
    if create_view:
        connection.execute(
            """
            CREATE OR REPLACE VIEW ml_player_game_core_features AS
            SELECT * FROM ml_player_game_core_features_v1
            """
        )


def _create_fantasy_extension(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE ml_player_game_core_features_with_fantasy_v1 AS
        WITH fantasy AS (
            SELECT
                snapshot.season_code AS season,
                semantics.competition_round,
                crosswalk.canonical_player_id AS player_id,
                snapshot.canonical_team_id AS team_id,
                snapshot.matchday_number AS fantasy_matchday,
                entity.fantasy_id,
                snapshot.position_name AS fantasy_position,
                CASE WHEN snapshot.season_code = 'E2025'
                          AND semantics.credits_valid_pre_matchday
                    THEN snapshot.credits END AS fantasy_credits_pre_matchday,
                semantics.credits_valid_pre_matchday,
                semantics.position_valid_as_of_matchday,
                cast(json_extract_string(
                    semantics.source_request_parameters_json,
                    '$.date_from[0]') AS DATE) AS source_date_from,
                cast(json_extract_string(
                    semantics.source_request_parameters_json,
                    '$.date_to[0]') AS DATE) AS source_date_to,
                snapshot.source_artifact_id,
                row_number() OVER (
                    PARTITION BY snapshot.season_code,
                                 semantics.competition_round,
                                 crosswalk.canonical_player_id,
                                 snapshot.canonical_team_id
                    ORDER BY snapshot.observed_at DESC, snapshot.snapshot_record_id
                ) AS priority
            FROM fantasy_market_snapshots AS snapshot
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            JOIN fantasy_market_snapshot_semantics AS semantics
              USING (snapshot_record_id)
            JOIN fantasy_player_crosswalk AS crosswalk
              ON crosswalk.fantasy_entity_id = snapshot.fantasy_entity_id
             AND crosswalk.season_code = snapshot.season_code
             AND crosswalk.valid_to IS NULL
             AND crosswalk.mapping_status = 'MATCHED'
            WHERE entity.entity_type = 'PLAYER'
              AND semantics.position_valid_as_of_matchday
        )
        SELECT
            core.*,
            fantasy.fantasy_id,
            fantasy.fantasy_position,
            fantasy.fantasy_matchday,
            fantasy.fantasy_credits_pre_matchday,
            CASE WHEN fantasy.fantasy_credits_pre_matchday IS NOT NULL
                THEN 'E2025_VALIDATED_PRE_MATCHDAY' END AS fantasy_price_status,
            fantasy.source_artifact_id AS fantasy_source_artifact_id
        FROM ml_player_game_core_features_v1 AS core
        LEFT JOIN fantasy
          ON fantasy.season = core.season
         AND fantasy.competition_round = core.round_number
         AND fantasy.player_id = core.player_id
         AND fantasy.team_id = core.team_id
         AND fantasy.priority = 1
         AND (
              core.season = 'E2025'
              OR cast(core.local_game_date AS DATE)
                    BETWEEN fantasy.source_date_from AND fantasy.source_date_to
         )
        ORDER BY core.target_game_time, core.game_id, core.player_id
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW ml_player_game_core_features_with_fantasy AS
        SELECT * FROM ml_player_game_core_features_with_fantasy_v1
        """
    )


def _create_walk_forward_splits(connection: Any) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TABLE ml_walk_forward_splits_v1 AS
        SELECT
            game_id,
            min(target_game_time) AS prediction_time,
            min(feature_cutoff_time) AS feature_cutoff_time,
            min(season) AS season,
            min(season_start_year) AS season_start_year,
            min(round_number) AS round_number,
            dense_rank() OVER (ORDER BY min(target_game_time), game_id)
                AS chronological_game_index,
            greatest(2018, min(season_start_year) - 2)
                AS history_3_season_start_year,
            greatest(2018, min(season_start_year) - 4)
                AS history_5_season_start_year,
            2018 AS history_8_season_start_year,
            'EXPANDING_STRICTLY_BEFORE_GAME_TIME' AS split_policy,
            '{version}' AS feature_pipeline_version
        FROM ml_player_game_core_features_v1
        GROUP BY game_id
        ORDER BY prediction_time, game_id
        """.format(version=FEATURE_PIPELINE_VERSION)
    )
