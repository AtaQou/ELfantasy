-- Referenced game identities stay stable. Live consumers see the latest
-- authoritative schedule observation even when DuckDB cannot update the parent.
CREATE VIEW current_games AS
SELECT game.* REPLACE (
    coalesce(schedule.official_game_id, game.official_game_id) AS official_game_id,
    coalesce(schedule.phase_code, game.phase_code) AS phase_code,
    coalesce(schedule.round_number, game.round_number) AS round_number,
    coalesce(schedule.scheduled_tip_time, game.game_date) AS game_date,
    coalesce(schedule.local_game_date, game.local_game_date) AS local_game_date,
    coalesce(schedule.home_team_id, game.home_team_id) AS home_team_id,
    coalesce(schedule.away_team_id, game.away_team_id) AS away_team_id,
    CASE WHEN schedule.canonical_game_id IS NOT NULL THEN schedule.home_score
         ELSE game.home_score END AS home_score,
    CASE WHEN schedule.canonical_game_id IS NOT NULL THEN schedule.away_score
         ELSE game.away_score END AS away_score,
    CASE WHEN schedule.played THEN 'Played'
         ELSE coalesce(schedule.game_status, game.game_status) END AS game_status,
    coalesce(schedule.played, game.played) AS played,
    coalesce(schedule.source_artifact_id, game.source_artifact_id) AS source_artifact_id
)
FROM games AS game
LEFT JOIN current_live_schedule AS schedule
  ON schedule.canonical_game_id=game.canonical_game_id
 AND schedule.season_code=game.season_code;
