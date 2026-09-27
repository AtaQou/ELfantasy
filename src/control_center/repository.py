"""DuckDB persistence and read models for the local control center."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.data.normalizers import display_person_name
from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.live.minutes import expected_role_label, role_trend_label
from src.modeling.fantasy_scoring import score_player_game

from .state import ControlCenterState, json_fingerprint, state_from_payload


def recommendation_roster_entity_ids(
        recommendation: Mapping[str, Any],
) -> frozenset[str]:
    """Return the persisted Fantasy entity IDs owned by a recommendation."""

    players = recommendation.get("players") or []
    coach = recommendation.get("coach") or {}
    return frozenset(
        str(entity_id)
        for entity_id in [
            *(row.get("entity_id") for row in players if isinstance(row, Mapping)),
            coach.get("entity_id") if isinstance(coach, Mapping) else None,
        ]
        if entity_id not in (None, "")
    )


def recommendation_for_entity_roster(
        recommendations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        roster_entity_ids: list[str] | tuple[str, ...] | frozenset[str],
) -> dict[str, Any] | None:
    """Return the recommendation whose complete roster is currently saved."""

    expected = frozenset(map(str, roster_entity_ids))
    return next(
        (
            dict(recommendation)
            for recommendation in recommendations
            if recommendation_roster_entity_ids(recommendation) == expected
        ),
        None,
    )


class ControlCenterRepository:
    def __init__(self, database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        initialize_database(self.database_path)

    def load_state(self, profile_id: str = "default") -> ControlCenterState:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT season_code,
                       fantasy_matchday,
                       roster_entity_ids_json,
                       bank_credits,
                       transfers_available,
                       scenario_id,
                       player_constraints_json
                FROM current_fantasy_control_center_state
                WHERE profile_id = ?
                """,
                [profile_id],
            ).fetchone()
        if row is None:
            return ControlCenterState(profile_id=profile_id)
        return state_from_payload({
            "profile_id": profile_id, "season_code": row[0],
            "fantasy_matchday": row[1], "roster_entity_ids": _json(row[2], []),
            "bank_credits": row[3], "transfers_available": row[4],
            "scenario_id": row[5], "player_constraints": _json(row[6], {}),
        })

    def save_state(
            self, state: ControlCenterState, *, created_at: datetime | None = None,
    ) -> ControlCenterState:
        value = state.validated()
        observed = created_at or datetime.now(UTC)
        event_id = stable_id(
            "phase8a_state", value.profile_id, value.fingerprint, observed.isoformat()
        )
        with connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_control_center_state_events (state_event_id, profile_id, season_code,
                                                                 fantasy_matchday,
                                                                 roster_entity_ids_json, bank_credits,
                                                                 transfers_available,
                                                                 scenario_id, player_constraints_json,
                                                                 state_fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id, value.profile_id, value.season_code,
                    value.fantasy_matchday, json.dumps(value.roster_entity_ids),
                    value.bank_credits, value.transfers_available, value.scenario_id,
                    json.dumps(dict(value.player_constraints), sort_keys=True),
                    value.fingerprint, observed,
                ],
            )
        return value

    def latest_market_matchday(self, season: str) -> int | None:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT matchday_number
                FROM fantasy_market_snapshots
                WHERE season_code=?
                ORDER BY observed_at DESC, snapshot_batch_id DESC
                LIMIT 1
                """,
                [season],
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def market_entities(self, season: str, matchday: int | None) -> list[dict[str, Any]]:
        parameters: list[Any] = [season, season]
        matchday_sql = ""
        if matchday is not None:
            matchday_sql = " AND market.matchday_number=?"
            parameters.append(int(matchday))
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                f"""
                WITH latest_official_coach AS (
                  SELECT canonical_team_id, coach_name FROM (
                    SELECT stat.canonical_team_id, stat.coach_name,
                           row_number() OVER (
                             PARTITION BY stat.canonical_team_id
                             ORDER BY game.game_date DESC NULLS LAST,
                                      game.game_code DESC, stat.team_game_id DESC
                           ) AS priority
                    FROM team_game_stats AS stat
                    JOIN current_games AS game USING(canonical_game_id)
                    WHERE game.season_code=? AND game.played
                      AND nullif(trim(stat.coach_name), '') IS NOT NULL
                  ) WHERE priority=1
                )
                SELECT market.fantasy_entity_id AS entity_id, entity.entity_type,
                       entity.display_name AS name, market.canonical_team_id AS team_id,
                       team.canonical_name AS team_name,
                       team.official_team_code AS team_code,
                       market.opponent_team_id, opponent.canonical_name AS opponent_team_name,
                       market.position_name AS position,
                       market.credits, market.turn_number AS source_turn,
                       market.matchday_number, market.observed_at,
                       crosswalk.canonical_player_id AS player_id,
                       market.overlay_is_injured AS market_injured,
                       market.overlay_probability_of_playing AS market_play_probability,
                       latest_official_coach.coach_name AS official_coach_name
                FROM fantasy_market_snapshots AS market
                JOIN fantasy_entities AS entity USING(fantasy_entity_id)
                LEFT JOIN teams AS team
                  ON team.canonical_team_id=market.canonical_team_id
                LEFT JOIN teams AS opponent
                  ON opponent.canonical_team_id=market.opponent_team_id
                LEFT JOIN latest_official_coach
                  ON latest_official_coach.canonical_team_id=market.canonical_team_id
                LEFT JOIN (
                  SELECT *
                  FROM fantasy_player_crosswalk
                  WHERE mapping_status='MATCHED'
                  QUALIFY row_number() OVER (
                    PARTITION BY fantasy_entity_id, season_code
                    ORDER BY CASE WHEN valid_to IS NULL THEN 0 ELSE 1 END,
                             coalesce(valid_to, valid_from) DESC NULLS LAST,
                             valid_from DESC NULLS LAST,
                             manually_reviewed DESC,
                             crosswalk_id DESC
                  )=1
                ) AS crosswalk
                  ON crosswalk.fantasy_entity_id=market.fantasy_entity_id
                 AND crosswalk.season_code=market.season_code
                WHERE market.season_code=? {matchday_sql}
                QUALIFY row_number() OVER (
                  PARTITION BY market.fantasy_entity_id
                  ORDER BY market.observed_at DESC, market.snapshot_record_id DESC
                )=1
                ORDER BY entity.entity_type, entity.display_name
                """,
                parameters,
            ).df()
        rows = _records(frame)
        for row in rows:
            official = row.pop("official_coach_name", None)
            if row.get("entity_type") == "COACH" and official:
                row["name"] = display_person_name(official)
                row["name_source"] = "official_completed_game_box_score"
            else:
                row["name_source"] = "official_fantasy_market"
        return rows

    def history_overview(self) -> dict[str, Any]:
        """Return compact canonical-history coverage without touching ingestion state."""

        with connect_database(self.database_path, read_only=True) as connection:
            seasons = connection.execute(
                """
                SELECT game.season_code AS season, season.season_label,
                       count(DISTINCT game.canonical_game_id) AS games,
                       count(DISTINCT game.canonical_game_id) FILTER (WHERE game.played) AS completed_games,
                       count(DISTINCT stat.canonical_player_id) AS players,
                       count(stat.player_game_id) AS player_rows
                FROM current_games AS game
                LEFT JOIN seasons AS season USING(season_id)
                LEFT JOIN player_game_stats AS stat USING(canonical_game_id)
                GROUP BY game.season_code, season.season_label
                ORDER BY game.season_code DESC
                """
            ).df()
            totals = connection.execute(
                """
                SELECT count(*) AS games,
                       count(*) FILTER (WHERE played) AS completed_games,
                       (SELECT count(*) FROM player_game_stats) AS player_rows,
                       (SELECT count(DISTINCT canonical_player_id) FROM player_game_stats) AS players
                FROM current_games
                """
            ).fetchone()
        return {
            "seasons": _records(seasons),
            "totals": {
                "games": int(totals[0]), "completed_games": int(totals[1]),
                "player_rows": int(totals[2]), "players": int(totals[3]),
            },
            "preseason_available": True,
        }

    def history_catalog(self) -> dict[str, Any]:
        with connect_database(self.database_path, read_only=True) as connection:
            seasons = connection.execute(
                "SELECT season_code, season_label FROM seasons ORDER BY season_code DESC"
            ).df()
            teams = connection.execute(
                """
                SELECT DISTINCT team.canonical_team_id AS team_id,
                       team.canonical_name AS team_name,
                       team.official_team_code AS team_code
                FROM teams AS team
                JOIN (
                  SELECT home_team_id AS team_id FROM current_games
                  UNION SELECT away_team_id AS team_id FROM current_games
                ) AS used ON used.team_id=team.canonical_team_id
                ORDER BY team.canonical_name
                """
            ).df()
            preseason_teams = connection.execute(
                """
                SELECT DISTINCT team.canonical_team_id AS team_id,
                       team.canonical_name AS team_name,
                       team.official_team_code AS team_code
                FROM fantasy_market_snapshots AS market
                JOIN teams AS team ON team.canonical_team_id=market.canonical_team_id
                WHERE market.season_code='E2026'
                  AND market.snapshot_batch_id=(
                      SELECT snapshot_batch_id
                      FROM fantasy_market_snapshots
                      WHERE season_code='E2026'
                      ORDER BY observed_at DESC, snapshot_record_id DESC
                      LIMIT 1
                  )
                ORDER BY team.canonical_name
                """
            ).df()
            players = connection.execute(
                """
                SELECT DISTINCT player.canonical_player_id AS player_id,
                       player.canonical_name AS player_name
                FROM players AS player
                JOIN (
                  SELECT canonical_player_id FROM player_game_stats
                  UNION SELECT canonical_player_id FROM preparation_player_stats
                        WHERE canonical_player_id IS NOT NULL
                ) AS stat USING(canonical_player_id)
                ORDER BY player.canonical_name
                """
            ).df()
            competitions = connection.execute(
                "SELECT DISTINCT competition_code FROM current_games ORDER BY competition_code"
            ).df()
            rounds = connection.execute(
                """
                SELECT DISTINCT season_code, round_number
                FROM current_games
                WHERE round_number IS NOT NULL
                ORDER BY season_code DESC, round_number
                """
            ).df()
        return {
            "seasons": _records(seasons), "teams": _records(teams),
            "preseason_teams": _records(preseason_teams),
            "players": _records(players),
            "competitions": [str(row[0]) for row in competitions.itertuples(index=False)],
            "rounds": _records(rounds),
            "preparation_game_types": [
                "FRIENDLY", "PRESEASON_TOURNAMENT", "SUPERCUP", "DOMESTIC_OFFICIAL"
            ],
        }

    def history_games(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        clauses: list[str] = []
        parameters: list[Any] = []
        if filters.get("season"):
            clauses.append("game.season_code=?")
            parameters.append(str(filters["season"]))
        if filters.get("team_id"):
            clauses.append("? IN (game.home_team_id, game.away_team_id)")
            parameters.append(str(filters["team_id"]))
        if filters.get("player_id"):
            clauses.append(
                "EXISTS (SELECT 1 FROM player_game_stats ps "
                "WHERE ps.canonical_game_id=game.canonical_game_id AND ps.canonical_player_id=?)"
            )
            parameters.append(str(filters["player_id"]))
        if filters.get("round") not in (None, ""):
            clauses.append("game.round_number=?")
            parameters.append(int(filters["round"]))
        if filters.get("date_from"):
            clauses.append("CAST(game.game_date AS DATE)>=CAST(? AS DATE)")
            parameters.append(str(filters["date_from"]))
        if filters.get("date_to"):
            clauses.append("CAST(game.game_date AS DATE)<=CAST(? AS DATE)")
            parameters.append(str(filters["date_to"]))
        if filters.get("competition"):
            clauses.append("game.competition_code=?")
            parameters.append(str(filters["competition"]))
        query = str(filters.get("query") or "").casefold().strip()
        if query:
            clauses.append(
                "lower(coalesce(home.canonical_name,'') || ' ' || coalesce(away.canonical_name,'') "
                "|| ' ' || cast(game.game_code AS VARCHAR)) LIKE ?"
            )
            parameters.append(f"%{query}%")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                f"""
                SELECT game.canonical_game_id AS game_id, game.season_code AS season,
                       game.competition_code AS competition, 'EUROLEAGUE' AS game_type,
                       game.game_code, game.round_number AS round, game.game_date,
                       game.game_status AS status, game.played,
                       game.home_team_id, home.canonical_name AS home_team,
                       game.away_team_id, away.canonical_name AS away_team,
                       game.home_score, game.away_score,
                       count(stat.player_game_id) AS player_rows
                FROM current_games AS game
                JOIN teams AS home ON home.canonical_team_id=game.home_team_id
                JOIN teams AS away ON away.canonical_team_id=game.away_team_id
                LEFT JOIN player_game_stats AS stat USING(canonical_game_id)
                {where}
                GROUP BY ALL
                ORDER BY game.played DESC NULLS LAST,
                         game.game_date DESC NULLS LAST, game.game_code DESC
                LIMIT 250
                """,
                parameters,
            ).df()
        return _records(frame)

    def history_preseason_games(
            self, filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Expose preparation games without copying them into canonical history."""

        filters = dict(filters or {})
        clauses: list[str] = ["game.season_code='E2026'"]
        parameters: list[Any] = []
        if filters.get("team_id"):
            clauses.append(
                "EXISTS (SELECT 1 FROM preparation_player_stats ps "
                "WHERE ps.preparation_game_id=game.preparation_game_id "
                "AND ps.canonical_team_id=?)"
            )
            parameters.append(str(filters["team_id"]))
        if filters.get("player_id"):
            clauses.append(
                "EXISTS (SELECT 1 FROM preparation_player_stats ps "
                "WHERE ps.preparation_game_id=game.preparation_game_id "
                "AND ps.canonical_player_id=?)"
            )
            parameters.append(str(filters["player_id"]))
        if filters.get("date_from"):
            clauses.append("CAST(game.game_date AS DATE)>=CAST(? AS DATE)")
            parameters.append(str(filters["date_from"]))
        if filters.get("date_to"):
            clauses.append("CAST(game.game_date AS DATE)<=CAST(? AS DATE)")
            parameters.append(str(filters["date_to"]))
        if filters.get("competition"):
            clauses.append("game.game_type=?")
            parameters.append(str(filters["competition"]))
        query = str(filters.get("query") or "").casefold().strip()
        if query:
            clauses.append(
                "lower(game.home_team_name || ' ' || game.away_team_name || ' ' "
                "|| game.source_game_id) LIKE ?"
            )
            parameters.append(f"%{query}%")
        where = "WHERE " + " AND ".join(clauses)
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                f"""
                SELECT game.preparation_game_id AS game_id,
                       '2026 Preseason' AS season, game.game_type AS competition,
                       game.game_type, game.source_game_id AS game_code,
                       CAST(NULL AS INTEGER) AS round, game.game_date,
                       'Final' AS status, true AS played,
                       CAST(NULL AS VARCHAR) AS home_team_id, game.home_team_name AS home_team,
                       CAST(NULL AS VARCHAR) AS away_team_id, game.away_team_name AS away_team,
                       (SELECT sum(points) FROM preparation_player_stats ps
                        WHERE ps.preparation_game_id=game.preparation_game_id
                          AND ps.team_name_raw=game.home_team_name) AS home_score,
                       (SELECT sum(points) FROM preparation_player_stats ps
                        WHERE ps.preparation_game_id=game.preparation_game_id
                          AND ps.team_name_raw=game.away_team_name) AS away_score,
                       count(stat.preparation_player_stat_id) AS player_rows
                FROM preparation_games AS game
                LEFT JOIN preparation_player_stats AS stat USING(preparation_game_id)
                {where}
                GROUP BY ALL
                ORDER BY game.game_date DESC, game.source_game_id DESC
                """,
                parameters,
            ).df()
        return _records(frame)

    def history_game_detail(self, game_id: str) -> dict[str, Any]:
        with connect_database(self.database_path, read_only=True) as connection:
            is_preparation = bool(connection.execute(
                "SELECT count(*) FROM preparation_games WHERE preparation_game_id=?",
                [str(game_id)],
            ).fetchone()[0])
        if is_preparation:
            return self.history_preseason_game_detail(game_id)
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            game = connection.execute(
                """
                SELECT game.canonical_game_id AS game_id, game.season_code AS season,
                       game.competition_code AS competition, game.game_code,
                       game.round_number AS round, game.game_date, game.game_status AS status,
                       game.played, game.home_team_id, home.canonical_name AS home_team,
                       game.away_team_id, away.canonical_name AS away_team,
                       game.home_score, game.away_score, game.venue_name
                FROM current_games AS game
                JOIN teams AS home ON home.canonical_team_id=game.home_team_id
                JOIN teams AS away ON away.canonical_team_id=game.away_team_id
                WHERE game.canonical_game_id=?
                """,
                [str(game_id)],
            ).df()
            if game.empty:
                raise ValueError("Historical game not found")
            player_stats = connection.execute(
                """
                SELECT stat.canonical_player_id AS player_id, player.canonical_name AS player,
                       stat.canonical_team_id AS team_id, team.canonical_name AS team,
                       stat.jersey_number, stat.starter, stat.did_not_play, stat.minutes,
                       stat.points, stat.two_points_made, stat.two_points_attempted,
                       stat.three_points_made, stat.three_points_attempted,
                       stat.free_throws_made, stat.free_throws_attempted,
                       stat.offensive_rebounds, stat.defensive_rebounds, stat.total_rebounds,
                       stat.assists, stat.steals, stat.turnovers, stat.blocks,
                       stat.fouls_committed, stat.fouls_drawn, stat.pir, stat.plus_minus,
                       {fantasy_points} AS fantasy_points
                FROM player_game_stats AS stat
                JOIN players AS player USING(canonical_player_id)
                JOIN teams AS team USING(canonical_team_id)
                {target_join}
                WHERE stat.canonical_game_id=?
                ORDER BY stat.home_away, stat.starter DESC NULLS LAST,
                         stat.minutes DESC NULLS LAST, player.canonical_name
                """.format(target_join=target_join, fantasy_points=fantasy_points),
                [str(game_id)],
            ).df()
            team_stats = connection.execute(
                """
                SELECT stat.canonical_team_id AS team_id, team.canonical_name AS team,
                       stat.home_away, stat.coach_name, stat.points,
                       stat.two_points_made, stat.two_points_attempted,
                       stat.three_points_made, stat.three_points_attempted,
                       stat.free_throws_made, stat.free_throws_attempted,
                       stat.offensive_rebounds, stat.defensive_rebounds, stat.total_rebounds,
                       stat.assists, stat.steals, stat.turnovers, stat.blocks,
                       stat.fouls_committed, stat.fouls_drawn, stat.pir, stat.plus_minus
                FROM team_game_stats AS stat
                JOIN teams AS team USING(canonical_team_id)
                WHERE stat.canonical_game_id=?
                ORDER BY stat.home_away
                """,
                [str(game_id)],
            ).df()
        return {
            "game": _records(game)[0], "player_stats": _records(player_stats),
            "team_stats": _records(team_stats),
        }

    def history_preseason_game_detail(self, game_id: str) -> dict[str, Any]:
        with connect_database(self.database_path, read_only=True) as connection:
            game = connection.execute(
                """
                SELECT game.preparation_game_id AS game_id,
                       '2026 Preseason' AS season, game.game_type AS competition,
                       game.game_type, game.source_game_id AS game_code,
                       CAST(NULL AS INTEGER) AS round, game.game_date,
                       'Final' AS status, true AS played,
                       CAST(NULL AS VARCHAR) AS home_team_id, game.home_team_name AS home_team,
                       CAST(NULL AS VARCHAR) AS away_team_id, game.away_team_name AS away_team,
                       (SELECT sum(points) FROM preparation_player_stats ps
                        WHERE ps.preparation_game_id=game.preparation_game_id
                          AND ps.team_name_raw=game.home_team_name) AS home_score,
                       (SELECT sum(points) FROM preparation_player_stats ps
                        WHERE ps.preparation_game_id=game.preparation_game_id
                          AND ps.team_name_raw=game.away_team_name) AS away_score,
                       CAST(NULL AS VARCHAR) AS venue_name
                FROM preparation_games AS game
                WHERE game.preparation_game_id=?
                """,
                [str(game_id)],
            ).df()
            if game.empty:
                raise ValueError("Historical game not found")
            player_stats = connection.execute(
                """
                SELECT stat.canonical_player_id AS player_id,
                       coalesce(player.canonical_name, stat.player_name_raw) AS player,
                       stat.canonical_team_id AS team_id,
                       coalesce(team.canonical_name, stat.team_name_raw) AS team,
                       stat.team_name_raw,
                       CAST(NULL AS VARCHAR) AS jersey_number, stat.starter,
                       CAST(NULL AS BOOLEAN) AS did_not_play, stat.minutes,
                       stat.points, stat.two_points_made, stat.two_points_attempted,
                       stat.three_points_made, stat.three_points_attempted,
                       stat.free_throws_made, stat.free_throws_attempted,
                       stat.offensive_rebounds, stat.defensive_rebounds, stat.total_rebounds,
                       stat.assists, stat.steals, stat.turnovers, stat.blocks,
                       stat.fouls_committed, stat.fouls_drawn,
                       stat.pir, stat.efficiency,
                       stat.blocks_received, stat.plus_minus,
                       fantasy.position_name AS fantasy_position,
                       fantasy.credits AS fantasy_credits,
                       fantasy.fantasy_entity_id,
                       CAST(NULL AS DOUBLE) AS fantasy_points
                FROM preparation_player_stats AS stat
                LEFT JOIN players AS player USING(canonical_player_id)
                LEFT JOIN teams AS team USING(canonical_team_id)
                LEFT JOIN (
                    SELECT crosswalk.canonical_player_id,
                           market.position_name, market.credits,
                           market.fantasy_entity_id
                    FROM fantasy_market_snapshots AS market
                    JOIN fantasy_player_crosswalk AS crosswalk
                      ON crosswalk.fantasy_entity_id=market.fantasy_entity_id
                     AND crosswalk.season_code=market.season_code
                     AND crosswalk.mapping_status='MATCHED'
                    WHERE market.season_code='E2026'
                      AND market.snapshot_batch_id=(
                          SELECT snapshot_batch_id
                          FROM fantasy_market_snapshots
                          WHERE season_code='E2026'
                          ORDER BY observed_at DESC, snapshot_record_id DESC
                          LIMIT 1
                      )
                    QUALIFY row_number() OVER (
                        PARTITION BY crosswalk.canonical_player_id
                        ORDER BY market.observed_at DESC,
                                 market.snapshot_record_id DESC
                    )=1
                ) AS fantasy USING(canonical_player_id)
                WHERE stat.preparation_game_id=?
                ORDER BY stat.team_name_raw, stat.starter DESC NULLS LAST,
                         stat.minutes DESC NULLS LAST, stat.player_name_raw
                """,
                [str(game_id)],
            ).df()
        game_record = _records(game)[0]
        rows = _records(player_stats)
        _score_preparation_rows(rows, game_record)
        return {"game": game_record, "player_stats": rows, "team_stats": []}

    def history_players(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        clauses, parameters = ["game.played", "NOT stat.did_not_play"], []
        if filters.get("season"):
            clauses.append("game.season_code=?"); parameters.append(str(filters["season"]))
        if filters.get("team_id"):
            clauses.append("stat.canonical_team_id=?"); parameters.append(str(filters["team_id"]))
        if filters.get("round") not in (None, ""):
            clauses.append("game.round_number=?"); parameters.append(int(filters["round"]))
        query = str(filters.get("query") or "").casefold().strip()
        if query:
            clauses.append("lower(player.canonical_name) LIKE ?"); parameters.append(f"%{query}%")
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            frame = connection.execute(
                f"""
                SELECT player.canonical_player_id AS player_id,
                       player.canonical_name AS player,
                       team.canonical_name AS team,
                       game.season_code AS season, game.round_number AS round,
                       game.home_team_id, game.away_team_id,
                       game.home_score, game.away_score,
                       stat.player_game_id, stat.canonical_team_id AS stat_team_id,
                       stat.did_not_play, stat.minutes, stat.points,
                       stat.total_rebounds, stat.assists, stat.steals, stat.blocks,
                       stat.fouls_drawn, stat.turnovers, stat.blocks_received,
                       stat.fouls_committed, stat.two_points_made,
                       stat.two_points_attempted, stat.three_points_made,
                       stat.three_points_attempted, stat.free_throws_made,
                       stat.free_throws_attempted, stat.pir,
                       {fantasy_points} AS target_actual_fantasy_points
                FROM player_game_stats AS stat
                JOIN current_games AS game USING(canonical_game_id)
                JOIN players AS player USING(canonical_player_id)
                JOIN teams AS team USING(canonical_team_id)
                {target_join}
                WHERE {' AND '.join(clauses)}
                """,
                parameters,
            ).df()
            credit_parameters: list[Any] = []
            credit_scope = ""
            if filters.get("season"):
                credit_scope = " AND season_code=?"
                credit_parameters.append(str(filters["season"]))
            credit_frame = connection.execute(
                f"""
                WITH ranked AS (
                  SELECT season_code, player_id,
                         coalesce(competition_round, matchday_number) AS round,
                         fantasy_credits_pre_matchday AS credits,
                         observed_at, snapshot_record_id,
                         row_number() OVER (
                           PARTITION BY season_code, player_id,
                                        coalesce(competition_round, matchday_number)
                           ORDER BY observed_at DESC, snapshot_record_id DESC
                         ) AS priority
                  FROM leakage_safe_fantasy_market
                  WHERE player_id IS NOT NULL
                    AND credits_valid_pre_matchday
                    AND fantasy_credits_pre_matchday IS NOT NULL
                    {credit_scope}
                ), market AS (
                  SELECT season_code, player_id, round, credits
                  FROM ranked WHERE priority=1
                )
                SELECT season_code, player_id, round, credits,
                       lead(round) OVER player_rounds AS next_round,
                       lead(credits) OVER player_rounds AS credits_after_round
                FROM market
                WINDOW player_rounds AS (
                  PARTITION BY season_code, player_id ORDER BY round
                )
                """,
                credit_parameters,
            ).df()
        rows = _records(frame)
        for row in rows:
            actual = _optional_number(row.pop("target_actual_fantasy_points", None))
            if actual is None:
                actual, _ = _canonical_actual_fantasy_points(row)
            row["fantasy_points"] = actual

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["player_id"]), []).append(row)
        credit_lookup = {
            (str(row["season_code"]), str(row["player_id"]), int(row["round"])): row
            for row in _records(credit_frame) if row.get("round") is not None
        }
        result: list[dict[str, Any]] = []
        minimum_minutes = _optional_number(filters.get("min_minutes"))
        single_round = filters.get("round") not in (None, "")
        for player_rows in grouped.values():
            first = player_rows[0]
            minutes = [float(row["minutes"]) for row in player_rows if row.get("minutes") is not None]
            fantasy = [float(row["fantasy_points"]) for row in player_rows
                       if row.get("fantasy_points") is not None]
            record = {
                "player_id": first["player_id"], "player": first["player"],
                "teams": ", ".join(sorted({str(row["team"]) for row in player_rows})),
                "games_played": len(player_rows),
                "fantasy_points_avg": _rounded_mean(fantasy),
                "minutes_avg": _rounded_mean(minutes),
                "points_avg": _rounded_mean([row.get("points") for row in player_rows]),
                "rebounds_avg": _rounded_mean([row.get("total_rebounds") for row in player_rows]),
                "assists_avg": _rounded_mean([row.get("assists") for row in player_rows]),
                "steals_avg": _rounded_mean([row.get("steals") for row in player_rows]),
                "blocks_avg": _rounded_mean([row.get("blocks") for row in player_rows]),
                "blocks_received_avg": _rounded_mean([
                    row.get("blocks_received") for row in player_rows
                ]),
                "turnovers_avg": _rounded_mean([row.get("turnovers") for row in player_rows]),
                "fouls_committed_avg": _rounded_mean([
                    row.get("fouls_committed") for row in player_rows
                ]),
                "fouls_drawn_avg": _rounded_mean([
                    row.get("fouls_drawn") for row in player_rows
                ]),
                "two_points_made_avg": _rounded_mean([
                    row.get("two_points_made") for row in player_rows
                ]),
                "two_points_attempted_avg": _rounded_mean([
                    row.get("two_points_attempted") for row in player_rows
                ]),
                "three_points_made_avg": _rounded_mean([
                    row.get("three_points_made") for row in player_rows
                ]),
                "three_points_attempted_avg": _rounded_mean([
                    row.get("three_points_attempted") for row in player_rows
                ]),
                "free_throws_made_avg": _rounded_mean([
                    row.get("free_throws_made") for row in player_rows
                ]),
                "free_throws_attempted_avg": _rounded_mean([
                    row.get("free_throws_attempted") for row in player_rows
                ]),
                "pir_avg": _rounded_mean([row.get("pir") for row in player_rows]),
            }
            credit_rows = []
            for row in player_rows:
                round_number = _optional_number(row.get("round"))
                if round_number is None:
                    continue
                credit = credit_lookup.get((
                    str(row.get("season")), str(row["player_id"]), int(round_number),
                ))
                if credit is not None:
                    credit_rows.append(credit)
            credit_rows = sorted(
                {(
                    str(row["season_code"]), int(row["round"]),
                ): row for row in credit_rows}.values(),
                key=lambda row: (str(row["season_code"]), int(row["round"])),
            )
            if credit_rows:
                first_credit = _optional_number(credit_rows[0].get("credits"))
                last_credit_row = credit_rows[-1]
                last_start = _optional_number(last_credit_row.get("credits"))
                last_round = int(last_credit_row["round"])
                next_round = _optional_number(last_credit_row.get("next_round"))
                after_last = _optional_number(last_credit_row.get("credits_after_round"))
                has_after_last = next_round == last_round + 1 and after_last is not None
                latest_credit = after_last if has_after_last else last_start
                record.update({
                    "credits_start": first_credit,
                    "credits_latest": latest_credit,
                    "credits_change": (
                        round(latest_credit - first_credit, 1)
                        if (has_after_last or len(credit_rows) > 1)
                        and latest_credit is not None and first_credit is not None else None
                    ),
                    "credits_change_includes_last_round": has_after_last,
                })
            else:
                record.update({
                    "credits_start": None, "credits_latest": None,
                    "credits_change": None, "credits_change_includes_last_round": False,
                })
            if single_round:
                record.update({
                    "fantasy_points": _rounded_sum(fantasy),
                    "minutes": _rounded_sum(minutes),
                    "points": _rounded_sum([row.get("points") for row in player_rows]),
                    "rebounds": _rounded_sum([
                        row.get("total_rebounds") for row in player_rows
                    ]),
                    "assists": _rounded_sum([row.get("assists") for row in player_rows]),
                    "steals": _rounded_sum([row.get("steals") for row in player_rows]),
                    "blocks": _rounded_sum([row.get("blocks") for row in player_rows]),
                    "blocks_received": _rounded_sum([
                        row.get("blocks_received") for row in player_rows
                    ]),
                    "turnovers": _rounded_sum([
                        row.get("turnovers") for row in player_rows
                    ]),
                    "fouls_committed": _rounded_sum([
                        row.get("fouls_committed") for row in player_rows
                    ]),
                    "fouls_drawn": _rounded_sum([
                        row.get("fouls_drawn") for row in player_rows
                    ]),
                    "two_points_made": _rounded_sum([
                        row.get("two_points_made") for row in player_rows
                    ]),
                    "two_points_attempted": _rounded_sum([
                        row.get("two_points_attempted") for row in player_rows
                    ]),
                    "three_points_made": _rounded_sum([
                        row.get("three_points_made") for row in player_rows
                    ]),
                    "three_points_attempted": _rounded_sum([
                        row.get("three_points_attempted") for row in player_rows
                    ]),
                    "free_throws_made": _rounded_sum([
                        row.get("free_throws_made") for row in player_rows
                    ]),
                    "free_throws_attempted": _rounded_sum([
                        row.get("free_throws_attempted") for row in player_rows
                    ]),
                    "pir": _rounded_sum([row.get("pir") for row in player_rows]),
                })
                round_credit = credit_rows[0] if credit_rows else None
                round_number = int(filters["round"])
                credits_start = _optional_number(
                    round_credit.get("credits") if round_credit else None
                )
                next_round = _optional_number(
                    round_credit.get("next_round") if round_credit else None
                )
                credits_after = _optional_number(
                    round_credit.get("credits_after_round") if round_credit else None
                )
                if next_round != round_number + 1:
                    credits_after = None
                record.update({
                    "credits_start": credits_start,
                    "credits_after": credits_after,
                    "credits_change": (
                        round(credits_after - credits_start, 1)
                        if credits_after is not None and credits_start is not None else None
                    ),
                    "credits_change_includes_last_round": credits_after is not None,
                })
            filter_minutes = record.get("minutes") if single_round else record["minutes_avg"]
            if minimum_minutes is not None and (
                    filter_minutes is None or float(filter_minutes) < minimum_minutes
            ):
                continue
            result.append(record)
        ranking_column = "fantasy_points" if single_round else "fantasy_points_avg"
        result.sort(key=lambda row: (
            row.get(ranking_column) is not None,
            row.get(ranking_column) or float("-inf"),
            row["player"],
        ), reverse=True)
        return result[:500]

    def history_player_detail(
            self, player_id: str, filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = dict(filters or {})
        clauses, parameters = ["stat.canonical_player_id=?", "game.played"], [str(player_id)]
        if filters.get("season"):
            clauses.append("game.season_code=?"); parameters.append(str(filters["season"]))
        if filters.get("team_id"):
            clauses.append("stat.canonical_team_id=?"); parameters.append(str(filters["team_id"]))
        if filters.get("round") not in (None, ""):
            clauses.append("game.round_number=?"); parameters.append(int(filters["round"]))
        where = " AND ".join(clauses)
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            player = connection.execute(
                "SELECT canonical_player_id AS player_id, canonical_name AS player "
                "FROM players WHERE canonical_player_id=?", [str(player_id)]
            ).df()
            if player.empty:
                raise ValueError("Historical player not found")
            summary = connection.execute(
                f"""
                SELECT count(*) FILTER (WHERE NOT stat.did_not_play) AS games_played,
                       round(avg(stat.minutes),1) AS minutes_avg,
                       round(avg(stat.points),1) AS points_avg,
                       round(avg(stat.total_rebounds),1) AS rebounds_avg,
                       round(avg(stat.assists),1) AS assists_avg,
                       round(avg(stat.steals),1) AS steals_avg,
                       round(avg(stat.blocks),1) AS blocks_avg,
                       round(avg(stat.pir),1) AS pir_avg,
                       round(avg({fantasy_points}),1) AS fantasy_points_avg
                FROM player_game_stats AS stat JOIN current_games AS game USING(canonical_game_id)
                {target_join}
                WHERE {where}
                """, parameters,
            ).df()
            games = connection.execute(
                f"""
                WITH fantasy_market AS (
                    SELECT season_code, competition_round, player_id,
                           canonical_team_id AS team_id,
                           fantasy_credits_pre_matchday,
                           row_number() OVER (
                               PARTITION BY season_code, competition_round,
                                            player_id, canonical_team_id
                               ORDER BY observed_at DESC, snapshot_record_id DESC
                           ) AS priority
                    FROM leakage_safe_fantasy_market
                    WHERE credits_valid_pre_matchday
                      AND fantasy_credits_pre_matchday IS NOT NULL
                )
                SELECT game.canonical_game_id AS game_id, game.season_code AS season,
                       game.round_number AS round, game.game_date,
                       home.canonical_name AS home_team, away.canonical_name AS away_team,
                       game.home_score, game.away_score, team.canonical_name AS team,
                       stat.starter, stat.minutes, stat.points, stat.total_rebounds,
                       stat.assists, stat.steals, stat.blocks, stat.turnovers, stat.pir,
                       {fantasy_points} AS fantasy_points,
                       market.fantasy_credits_pre_matchday
                FROM player_game_stats AS stat JOIN current_games AS game USING(canonical_game_id)
                JOIN teams AS home ON home.canonical_team_id=game.home_team_id
                JOIN teams AS away ON away.canonical_team_id=game.away_team_id
                JOIN teams AS team ON team.canonical_team_id=stat.canonical_team_id
                {target_join}
                LEFT JOIN fantasy_market AS market
                  ON market.season_code=game.season_code
                 AND market.competition_round=game.round_number
                 AND market.player_id=stat.canonical_player_id
                 AND market.team_id=stat.canonical_team_id
                 AND market.priority=1
                WHERE {where}
                ORDER BY game.game_date DESC
                LIMIT 100
                """, parameters,
            ).df()
        return {"player": _records(player)[0], "summary": _records(summary)[0], "games": _records(games)}

    def history_teams(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        clauses, parameters = ["game.played"], []
        if filters.get("season"):
            clauses.append("game.season_code=?"); parameters.append(str(filters["season"]))
        if filters.get("team_id"):
            clauses.append("stat.canonical_team_id=?"); parameters.append(str(filters["team_id"]))
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            frame = connection.execute(
                f"""
                SELECT team.canonical_team_id AS team_id, team.canonical_name AS team,
                       team.official_team_code AS team_code, count(*) AS games,
                       round(avg(stat.points),1) AS points_avg,
                       round(avg(stat.total_rebounds),1) AS rebounds_avg,
                       round(avg(stat.assists),1) AS assists_avg,
                       round(avg(stat.pir),1) AS pir_avg
                FROM team_game_stats AS stat JOIN current_games AS game USING(canonical_game_id)
                JOIN teams AS team USING(canonical_team_id)
                WHERE {' AND '.join(clauses)}
                GROUP BY team.canonical_team_id, team.canonical_name, team.official_team_code
                ORDER BY team.canonical_name
                """, parameters,
            ).df()
        return _records(frame)

    def history_team_detail(
            self, team_id: str, filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = dict(filters or {})
        game_filters = {**filters, "team_id": str(team_id)}
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            team = connection.execute(
                "SELECT canonical_team_id AS team_id, canonical_name AS team, official_team_code AS team_code "
                "FROM teams WHERE canonical_team_id=?", [str(team_id)]
            ).df()
            if team.empty:
                raise ValueError("Historical team not found")
            clauses, parameters = ["stat.canonical_team_id=?", "game.played"], [str(team_id)]
            if filters.get("season"):
                clauses.append("game.season_code=?"); parameters.append(str(filters["season"]))
            roster = connection.execute(
                f"""
                SELECT player.canonical_player_id AS player_id, player.canonical_name AS player,
                       count(*) FILTER (WHERE NOT stat.did_not_play) AS games_played,
                       round(avg(stat.minutes),1) AS minutes_avg,
                       round(avg(stat.points),1) AS points_avg,
                       round(avg({fantasy_points}),1) AS fantasy_points_avg
                FROM player_game_stats AS stat JOIN current_games AS game USING(canonical_game_id)
                JOIN players AS player USING(canonical_player_id)
                {target_join}
                WHERE {' AND '.join(clauses)}
                GROUP BY player.canonical_player_id, player.canonical_name
                ORDER BY minutes_avg DESC NULLS LAST, player.canonical_name
                """, parameters,
            ).df()
        return {"team": _records(team)[0], "games": self.history_games(game_filters), "roster": _records(roster)}

    def prediction_context(
            self, season: str, matchday: int | None, *, require_usable: bool = False,
            require_scored: bool = False,
    ) -> dict[str, Any] | None:
        parameters: list[Any] = [season]
        matchday_sql = ""
        if matchday is not None:
            matchday_sql = " AND season_matchday=?"
            parameters.append(int(matchday))
        usable_sql = ""
        if require_usable:
            usable_sql = """
                AND production_ready
                AND EXISTS (
                    SELECT 1
                    FROM live_prediction_scenarios AS usable_scenario
                    WHERE usable_scenario.prediction_run_id=live_prediction_runs.prediction_run_id
                      AND usable_scenario.scenario_status IN ('READY','GENERATED')
                )
                AND EXISTS (
                    SELECT 1
                    FROM live_player_predictions AS usable_prediction
                    WHERE usable_prediction.prediction_run_id=live_prediction_runs.prediction_run_id
                      AND usable_prediction.prediction_status='SCORED'
                )
            """
        elif require_scored:
            usable_sql = """
                AND EXISTS (
                    SELECT 1
                    FROM live_prediction_scenarios AS display_scenario
                    WHERE display_scenario.prediction_run_id=live_prediction_runs.prediction_run_id
                      AND display_scenario.scenario_status IN ('READY','GENERATED')
                )
                AND EXISTS (
                    SELECT 1
                    FROM live_player_predictions AS display_prediction
                    WHERE display_prediction.prediction_run_id=live_prediction_runs.prediction_run_id
                      AND display_prediction.prediction_status='SCORED'
                )
            """
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                f"""
                SELECT prediction_run_id, status, prediction_generated_at,
                       season_matchday, market_snapshot_batch_id,
                       market_snapshot_fingerprint, run_fingerprint,
                       predictive_model_version, predictive_artifact_fingerprint,
                       scoring_rules_compatibility, production_ready,
                       source_freshness_json, warnings_json, error_code, error_message
                FROM live_prediction_runs
                WHERE season_code=? {matchday_sql} {usable_sql}
                ORDER BY prediction_generated_at DESC, prediction_run_id DESC LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            scenarios = [str(value[0]) for value in connection.execute(
                "SELECT scenario_id FROM live_prediction_scenarios "
                "WHERE prediction_run_id=? AND scenario_status IN ('READY','GENERATED') "
                "ORDER BY scenario_id",
                [row[0]],
            ).fetchall()]
            last_override = connection.execute(
                "SELECT max(created_at) FROM availability_override_events "
                "WHERE season_code IS NULL OR season_code=?",
                [season],
            ).fetchone()[0]
        generated = row[2]
        return {
            "prediction_run_id": str(row[0]), "status": str(row[1]),
            "generated_at": _value(generated), "matchday": row[3],
            "market_snapshot_batch_id": row[4],
            "market_snapshot_fingerprint": row[5], "prediction_fingerprint": row[6],
            "predictive_model_version": row[7], "predictive_artifact_fingerprint": row[8],
            "scoring_compatibility": row[9], "production_ready": bool(row[10]),
            "source_freshness": _json(row[11], {}), "warnings": _json(row[12], []),
            "error_code": row[13], "error_message": row[14], "scenarios": scenarios,
            "last_override_at": _value(last_override),
            "overrides_newer_than_prediction": bool(
                last_override is not None and generated is not None and last_override > generated
            ),
        }

    def player_predictions(
            self, prediction_run_id: str, scenario_id: str,
    ) -> list[dict[str, Any]]:
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                """
                SELECT prediction.canonical_player_id AS player_id,
                       prediction.fantasy_entity_id   AS entity_id,
                       prediction.player_name         AS name,
                       prediction.canonical_team_id   AS team_id,
                       prediction.opponent_team_id,
                       opponent.canonical_name AS opponent_team_name,
                       prediction.canonical_game_id   AS game_id,
                       prediction.fantasy_position AS position,
                       prediction.credits, prediction.scheduled_tip_time,
                       prediction.source_availability_status,
                       prediction.resolved_availability AS availability,
                       prediction.availability_decision_source AS availability_source,
                       prediction.prediction_generated_at AS availability_timestamp,
                       prediction.adjusted_expected_minutes AS expected_minutes,
                       prediction.expected_fp, prediction.median_fp,
                       prediction.p10_fp, prediction.p25_fp, prediction.p50_fp,
                       prediction.p75_fp, prediction.p90_fp, prediction.p95_fp,
                       prediction.prob_fp_le_5, prediction.prob_fp_le_10,
                       prediction.prob_fp_le_15, prediction.prob_fp_ge_20,
                       prediction.prob_fp_ge_25, prediction.prob_fp_ge_30,
                       prediction.prob_fp_ge_35, prediction.prob_fp_ge_40,
                       prediction.freshness_status, prediction.feature_snapshot_json,
                       prediction.predictive_artifact_fingerprint,
                       games.local_game_date
                FROM live_player_predictions AS prediction
                    JOIN current_games AS games
                ON games.canonical_game_id=prediction.canonical_game_id
                LEFT JOIN teams AS opponent
                  ON opponent.canonical_team_id=prediction.opponent_team_id
                WHERE prediction.prediction_run_id=?
                  AND prediction.scenario_id=?
                  AND prediction.prediction_status='SCORED'
                ORDER BY prediction.scheduled_tip_time, prediction.player_name
                """,
                [prediction_run_id, scenario_id],
            ).df()
            manual = {
                str(row[0]): {
                    "manual_override": True, "manual_override_status": row[1],
                    "manual_override_timestamp": _value(row[2]),
                    "manual_override_note": row[3],
                }
                for row in connection.execute(
                    """
                    SELECT canonical_player_id, normalized_status, created_at, note
                    FROM active_availability_overrides
                    """
                ).fetchall()
            }
        if frame.empty:
            return []
        dates = sorted(pd.to_datetime(frame["local_game_date"]).dt.date.unique())
        turns = {value: index + 1 for index, value in enumerate(dates)}
        frame["turn"] = pd.to_datetime(frame["local_game_date"]).dt.date.map(turns)
        frame["fp_per_credit"] = frame["expected_fp"] / frame["credits"].replace(0, pd.NA)
        rows = _records(frame.drop(columns=["local_game_date"]))
        for row in rows:
            features = _json(row.pop("feature_snapshot_json", {}), {})
            row["recent_form"] = _first_finite(features, (
                "last_5_fp_avg", "fantasy_points_last5_mean", "fp_last5_mean",
                "recent_fantasy_points_mean", "career_fp_mean_before",
            ))
            row["uncalibrated_expected_minutes"] = _first_finite(features, (
                "uncalibrated_expected_minutes",
            ))
            row["previous_season_minutes"] = _first_finite(features, (
                "previous_season_minutes_avg",
            ))
            row["recent_minutes"] = _first_finite(features, (
                "season_minutes_avg_before", "last_5_minutes_avg", "minutes_ewma",
                "previous_season_minutes_avg",
            ))
            row["minutes_trend"] = _first_finite(features, (
                "minutes_trend", "rot_minutes_trend",
            ))
            row["role_trend"] = role_trend_label(row["minutes_trend"])
            row["expected_role"] = expected_role_label(row.get("expected_minutes"))
            expected_minutes = _first_finite(row, ("expected_minutes",))
            expected_fp = _first_finite(row, ("expected_fp",))
            row["fp_per_minute"] = (
                expected_fp / expected_minutes
                if expected_fp is not None and expected_minutes not in (None, 0.0)
                else None
            )
            row.update(manual.get(str(row["player_id"]), {
                "manual_override": False, "manual_override_status": None,
                "manual_override_timestamp": None, "manual_override_note": None,
            }))
        return rows

    def player_recent_form(
            self, player_ids: list[str], *, limit: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """Return the newest combined EuroLeague/preparation appearances per player.

        Missing minutes must not discard a documented appearance. Aggregate each
        stat from available values within the same five games, never as zero.
        """

        ids = sorted({str(value) for value in player_ids if value})
        if not ids or limit < 1:
            return {}
        with connect_database(self.database_path, read_only=True) as connection:
            canonical_tables = connection.execute(
                "SELECT count(DISTINCT table_name) FROM information_schema.tables "
                "WHERE table_name IN ('player_game_stats', 'current_games', 'seasons')"
            ).fetchone()[0]
            if canonical_tables == 3:
                target_join, fantasy_points = _history_fantasy_target(connection)
                historical = connection.execute(
                    f"""
                    SELECT stat.canonical_player_id AS player_id,
                           game.canonical_game_id AS game_id, game.game_date,
                           game.season_code AS season, season.start_year AS season_start_year,
                           'EUROLEAGUE' AS game_type,
                           game.home_team_id, game.away_team_id,
                           game.home_score, game.away_score,
                           stat.player_game_id, stat.canonical_team_id AS stat_team_id,
                           stat.did_not_play, stat.minutes, stat.points,
                           stat.two_points_made, stat.two_points_attempted,
                           stat.three_points_made, stat.three_points_attempted,
                           stat.free_throws_made, stat.free_throws_attempted,
                           stat.total_rebounds, stat.assists, stat.steals,
                           stat.turnovers, stat.blocks, stat.blocks_received,
                           stat.fouls_committed, stat.fouls_drawn, stat.pir,
                           {fantasy_points} AS target_actual_fantasy_points
                    FROM player_game_stats AS stat
                    JOIN current_games AS game USING(canonical_game_id)
                    JOIN seasons AS season USING(season_id)
                    {target_join}
                    WHERE stat.canonical_player_id IN (SELECT unnest(?))
                      AND game.played AND NOT stat.did_not_play
                    """,
                    [ids],
                ).df()
            else:
                historical = connection.execute(
                    """
                    SELECT target.player_id, target.game_id,
                           target.target_game_time AS game_date,
                           target.actual_fantasy_points AS fantasy_points,
                           target.target_minutes AS minutes,
                           target.season, target.season_start_year,
                           'EUROLEAGUE' AS game_type
                    FROM ml_player_game_targets_v1 AS target
                    WHERE target.player_id IN (SELECT unnest(?))
                      AND target.eligibility_status='PLAYED'
                    """,
                    [ids],
                ).df()
            preparation = connection.execute(
                """
                SELECT stat.canonical_player_id AS player_id,
                       game.preparation_game_id AS game_id, game.game_date,
                       game.game_type, game.home_team_name, game.away_team_name,
                       (SELECT sum(home_stat.points)
                        FROM preparation_player_stats AS home_stat
                        WHERE home_stat.preparation_game_id=game.preparation_game_id
                          AND home_stat.team_name_raw=game.home_team_name) AS home_score,
                       (SELECT sum(away_stat.points)
                        FROM preparation_player_stats AS away_stat
                        WHERE away_stat.preparation_game_id=game.preparation_game_id
                          AND away_stat.team_name_raw=game.away_team_name) AS away_score,
                       stat.team_name_raw, stat.minutes, stat.points,
                       stat.two_points_made, stat.two_points_attempted,
                       stat.three_points_made, stat.three_points_attempted,
                       stat.free_throws_made, stat.free_throws_attempted,
                       stat.total_rebounds, stat.assists, stat.steals,
                       stat.turnovers, stat.blocks, stat.blocks_received,
                       stat.fouls_committed, stat.fouls_drawn,
                       stat.pir, stat.efficiency
                FROM preparation_player_stats AS stat
                JOIN preparation_games AS game USING(preparation_game_id)
                WHERE stat.canonical_player_id IN (SELECT unnest(?))
                  AND (
                    stat.minutes>0
                    OR (stat.minutes IS NULL AND (
                      stat.starter IS TRUE
                      OR greatest(
                        abs(stat.points), abs(stat.total_rebounds),
                        abs(stat.assists), abs(stat.steals), abs(stat.turnovers),
                        abs(stat.blocks), abs(stat.blocks_received),
                        abs(stat.fouls_committed), abs(stat.fouls_drawn),
                        abs(stat.two_points_attempted), abs(stat.three_points_attempted),
                        abs(stat.free_throws_attempted), abs(stat.pir), abs(stat.efficiency)
                      )>0
                    ))
                  )
                ORDER BY game.game_date DESC, game.preparation_game_id DESC
                """,
                [ids],
            ).df()

        appearances: list[dict[str, Any]] = _records(historical)
        for row in appearances:
            if "target_actual_fantasy_points" not in row:
                continue
            actual = _optional_number(row.pop("target_actual_fantasy_points", None))
            provenance = "CANONICAL_VERIFIED_TARGET"
            if actual is None:
                actual, provenance = _canonical_actual_fantasy_points(row)
            row["fantasy_points"] = actual
            row["fantasy_points_kind"] = provenance
        if not preparation.empty:
            for _, game_rows in preparation.groupby("game_id", sort=False):
                raw_rows = _records(game_rows)
                home = raw_rows[0]["home_team_name"]
                away = raw_rows[0]["away_team_name"]
                game = {
                    "home_team": home, "away_team": away,
                    "home_score": raw_rows[0].get("home_score"),
                    "away_score": raw_rows[0].get("away_score"),
                }
                for row in raw_rows:
                    row.pop("home_team_name", None)
                    row.pop("away_team_name", None)
                    row.pop("home_score", None)
                    row.pop("away_score", None)
                _score_preparation_rows(raw_rows, game)
                appearances.extend(raw_rows)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in appearances:
            grouped.setdefault(str(row["player_id"]), []).append(row)
        output: dict[str, dict[str, Any]] = {}
        for player_id, rows in grouped.items():
            latest = sorted(
                rows,
                key=lambda row: (str(row.get("game_date") or ""), str(row.get("game_id") or "")),
                reverse=True,
            )[:limit]
            latest_game = latest[0] if latest else {}
            fp_values = [
                value for row in latest
                if (value := _optional_number(row.get("fantasy_points"))) is not None
            ]
            minute_values = [
                value for row in latest
                if (value := _optional_number(row.get("minutes"))) is not None
            ]
            euroleague_rows = [
                row for row in rows if row.get("game_type") == "EUROLEAGUE"
            ]
            latest_season_start = max(
                (
                    int(row["season_start_year"])
                    for row in euroleague_rows
                    if _optional_number(row.get("season_start_year")) is not None
                ),
                default=None,
            )
            season_rows = [
                row for row in euroleague_rows
                if latest_season_start is not None
                and int(row["season_start_year"]) == latest_season_start
            ]
            season_fp_values = [
                value for row in season_rows
                if (value := _optional_number(row.get("fantasy_points"))) is not None
            ]
            season_minute_values = [
                value for row in season_rows
                if (value := _optional_number(row.get("minutes"))) is not None
            ]
            output[player_id] = {
                "last5_fp_average": float(np.mean(fp_values)) if fp_values else None,
                "last5_minutes_median": (
                    float(np.median(minute_values)) if minute_values else None
                ),
                "last_game_fp": _optional_number(latest_game.get("fantasy_points")),
                "last_game_minutes": _optional_number(latest_game.get("minutes")),
                "season_fp_average": (
                    float(np.mean(season_fp_values)) if season_fp_values else None
                ),
                "season_minutes_average": (
                    float(np.mean(season_minute_values)) if season_minute_values else None
                ),
                "season_code": (
                    str(season_rows[0].get("season")) if season_rows else None
                ),
                "last5_games": [
                    {
                        "game_id": str(row.get("game_id") or ""),
                        "game_date": _value(row.get("game_date")),
                        "game_type": str(row.get("game_type") or "EUROLEAGUE"),
                        "fantasy_points": _optional_number(row.get("fantasy_points")),
                        "fantasy_points_kind": row.get("fantasy_points_kind", "EXACT"),
                        "fantasy_points_missing": row.get("fantasy_points_missing", []),
                        "minutes": _optional_number(row.get("minutes")),
                    }
                    for row in latest
                ],
            }
        return output

    def active_overrides(self, season: str, matchday: int | None) -> list[dict[str, Any]]:
        parameters: list[Any] = [season]
        scope_sql = ""
        if matchday is not None:
            scope_sql = " AND (fantasy_matchday IS NULL OR fantasy_matchday=?)"
            parameters.append(int(matchday))
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                f"""
                SELECT canonical_player_id AS player_id, normalized_status AS status,
                       created_at AS timestamp, note, override_event_id,
                       fantasy_matchday, canonical_game_id
                FROM active_availability_overrides
                WHERE (season_code IS NULL OR season_code=?) {scope_sql}
                ORDER BY created_at DESC, canonical_player_id
                """,
                parameters,
            ).df()
        return _records(frame)

    def persist_run(
            self,
            payload: Mapping[str, Any],
            *,
            snapshot_path: Path | str | None,
    ) -> str:
        stable = dict(payload)
        stable.pop("generated_at", None)
        stable.pop("snapshot_path", None)
        run_fingerprint = json_fingerprint(stable)
        run_id = stable_id("phase8a_control_center_run", run_fingerprint)
        context = payload.get("snapshot", {})
        with connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_control_center_runs (control_center_run_id, profile_id, season_code,
                                                         fantasy_matchday,
                                                         status, market_snapshot_fingerprint,
                                                         prediction_snapshot_fingerprint,
                                                         prediction_run_id, ruleset_version, rules_fingerprint,
                                                         manual_override_fingerprint, current_roster_json, bank_credits,
                                                         transfer_limit, optimization_constraints_json,
                                                         recommendations_json, input_fingerprint, run_fingerprint,
                                                         snapshot_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                [
                    run_id, payload["profile_id"], payload["season"], payload["matchday"],
                    payload["status"], context.get("market_fingerprint"),
                    context.get("prediction_fingerprint"), context.get("prediction_run_id"),
                    context["ruleset_version"], context["rules_fingerprint"],
                    context["manual_override_fingerprint"],
                    json.dumps(payload.get("current_roster", []), default=str),
                    float(payload.get("bank_credits", 0.0)),
                    int(payload.get("transfers_available", 0)),
                    json.dumps(payload.get("constraints", {}), default=str, sort_keys=True),
                    json.dumps(payload.get("recommendations", []), default=str),
                    payload["input_fingerprint"], run_fingerprint,
                    str(snapshot_path) if snapshot_path else None,
                ],
            )
        return run_id

    def persist_prelock_snapshot(
            self,
            payload: Mapping[str, Any],
            *,
            scenario_id: str,
            decision_cutoff_at: datetime,
            decision_inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append the complete decision-time state only while the slate is unlocked."""

        created = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
        cutoff = decision_cutoff_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        if created >= cutoff:
            return {"status": "NOT_PRELOCK", "reason": "decision was created at/after first tip"}
        snapshot = dict(payload.get("snapshot", {}))
        required = (
            "market_fingerprint", "prediction_fingerprint", "prediction_run_id",
            "ruleset_version", "rules_fingerprint", "manual_override_fingerprint",
        )
        missing = [name for name in required if not snapshot.get(name)]
        if missing:
            return {"status": "NOT_PRELOCK", "reason": "missing " + ", ".join(missing)}
        knowledge = {
            "version": payload.get("version"),
            "generated_at": payload["generated_at"],
            "current_roster": payload.get("current_roster", []),
            "bank_credits": payload.get("bank_credits", 0.0),
            "transfers_available": payload.get("transfers_available", 0),
            "constraints": payload.get("constraints", {}),
            "manual_overrides": payload.get("manual_overrides", []),
            "strategy": payload.get("strategy", {}),
            "player_alternatives": payload.get("player_alternatives", []),
            "simulation": payload.get("simulation", {}),
            "decision_inputs": dict(decision_inputs),
        }
        stable = {
            "control_center_run_id": payload["control_center_run_id"],
            "profile_id": payload["profile_id"], "season": payload["season"],
            "matchday": payload["matchday"], "scenario_id": scenario_id,
            "decision_created_at": created.isoformat(), "decision_cutoff_at": cutoff.isoformat(),
            "snapshot": snapshot, "input_fingerprint": payload["input_fingerprint"],
            "knowledge": knowledge, "recommendations": payload.get("recommendations", []),
        }
        fingerprint = json_fingerprint(stable)
        shadow_id = stable_id("phase8b_prelock", fingerprint)
        with connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_shadow_prelock_snapshots VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) ON CONFLICT DO NOTHING
                """,
                [shadow_id, payload["control_center_run_id"], payload["profile_id"],
                 payload["season"], int(payload["matchday"]), snapshot["prediction_run_id"],
                 scenario_id, created, cutoff, snapshot["market_fingerprint"],
                 snapshot["prediction_fingerprint"], snapshot["ruleset_version"],
                 snapshot["rules_fingerprint"], snapshot["manual_override_fingerprint"],
                 payload["input_fingerprint"], json.dumps(knowledge, default=str),
                 json.dumps(payload.get("recommendations", []), default=str), fingerprint,
                 payload.get("snapshot_path"), datetime.now(UTC)],
            )
            existing = connection.execute(
                """SELECT shadow_snapshot_id, snapshot_fingerprint
                   FROM fantasy_shadow_prelock_snapshots WHERE control_center_run_id=?""",
                [payload["control_center_run_id"]],
            ).fetchone()
        if existing is None:
            raise ValueError("pre-lock snapshot persistence failed")
        if str(existing[1]) != fingerprint:
            raise ValueError("immutable pre-lock snapshot conflict")
        return {"status": "STORED_IMMUTABLY", "shadow_snapshot_id": str(existing[0]),
                "snapshot_fingerprint": str(existing[1])}

    def shadow_snapshot(self, shadow_snapshot_id: str) -> dict[str, Any] | None:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT shadow_snapshot_id, control_center_run_id, profile_id, season_code,
                       fantasy_matchday, prediction_run_id, scenario_id, decision_created_at,
                       decision_cutoff_at, market_snapshot_fingerprint,
                       prediction_snapshot_fingerprint, ruleset_version, rules_fingerprint,
                       manual_override_fingerprint, input_fingerprint, knowledge_json,
                       recommendations_json, snapshot_fingerprint, snapshot_path, created_at
                FROM fantasy_shadow_prelock_snapshots WHERE shadow_snapshot_id=?
                """, [shadow_snapshot_id],
            ).fetchone()
        if row is None:
            return None
        keys = (
            "shadow_snapshot_id", "control_center_run_id", "profile_id", "season",
            "matchday", "prediction_run_id", "scenario_id", "decision_created_at",
            "decision_cutoff_at", "market_fingerprint", "prediction_fingerprint",
            "ruleset_version", "rules_fingerprint", "manual_override_fingerprint",
            "input_fingerprint", "knowledge", "recommendations", "snapshot_fingerprint",
            "snapshot_path", "created_at",
        )
        value = dict(zip(keys, row, strict=True))
        value["knowledge"] = _json(value["knowledge"], {})
        value["recommendations"] = _json(value["recommendations"], [])
        for key in ("decision_created_at", "decision_cutoff_at", "created_at"):
            value[key] = _value(value[key])
        return value

    def latest_shadow_snapshot(
            self,
            profile_id: str = "default",
            *,
            season: str | None = None,
            matchday: int | None = None,
            roster_entity_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["profile_id=?"]
        parameters: list[Any] = [profile_id]
        if season is not None:
            clauses.append("season_code=?")
            parameters.append(str(season))
        if matchday is not None:
            clauses.append("fantasy_matchday=?")
            parameters.append(int(matchday))
        with connect_database(self.database_path, read_only=True) as connection:
            rows = connection.execute(
                f"""
                SELECT shadow_snapshot_id, recommendations_json
                FROM fantasy_shadow_prelock_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, shadow_snapshot_id DESC
                """,
                parameters,
            ).fetchall()
        expected_roster = (
            sorted(map(str, roster_entity_ids))
            if roster_entity_ids is not None else None
        )
        for shadow_id, recommendations_json in rows:
            matched_recommendation: dict[str, Any] | None = None
            if expected_roster is not None:
                recommendations = _json(recommendations_json, [])
                matched_recommendation = recommendation_for_entity_roster(
                    recommendations, expected_roster,
                )
                if matched_recommendation is None:
                    continue
            snapshot = self.shadow_snapshot(str(shadow_id))
            if snapshot is not None and matched_recommendation is not None:
                snapshot["matched_recommendation"] = matched_recommendation
            return snapshot
        return None

    def shadow_snapshot_ids(self, profile_id: str = "default") -> list[str]:
        with connect_database(self.database_path, read_only=True) as connection:
            return [str(row[0]) for row in connection.execute(
                """SELECT shadow_snapshot_id FROM fantasy_shadow_prelock_snapshots
                   WHERE profile_id=? ORDER BY created_at, shadow_snapshot_id""",
                [profile_id],
            ).fetchall()]

    def current_roster_game_outcomes(
            self,
            season: str,
            matchday: int,
            players: list[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return the current-round game state and realized FP for roster players."""

        output: dict[str, dict[str, Any]] = {}
        with connect_database(self.database_path, read_only=True) as connection:
            target_join, fantasy_points = _history_fantasy_target(connection)
            for player in players:
                player_id = str(player.get("player_id") or "")
                team_id = str(player.get("team_id") or "")
                if not player_id or not team_id:
                    continue
                frame = connection.execute(
                    f"""
                    SELECT game.canonical_game_id AS game_id,
                           game.played, game.game_date,
                           game.home_team_id, game.away_team_id,
                           game.home_score, game.away_score,
                           stat.player_game_id, stat.canonical_team_id AS stat_team_id,
                           stat.did_not_play, stat.minutes,
                           stat.points, stat.total_rebounds, stat.assists,
                           stat.steals, stat.blocks, stat.fouls_drawn,
                           stat.turnovers, stat.blocks_received, stat.fouls_committed,
                           stat.two_points_made, stat.two_points_attempted,
                           stat.three_points_made, stat.three_points_attempted,
                           stat.free_throws_made, stat.free_throws_attempted,
                           stat.pir,
                           {fantasy_points} AS target_actual_fantasy_points
                    FROM current_games AS game
                    LEFT JOIN player_game_stats AS stat
                      ON stat.canonical_game_id=game.canonical_game_id
                     AND stat.canonical_player_id=?
                    {target_join}
                    WHERE game.season_code=? AND game.round_number=?
                      AND (game.home_team_id=? OR game.away_team_id=?)
                    ORDER BY game.game_date, game.game_code
                    LIMIT 1
                    """,
                    [player_id, season, int(matchday), team_id, team_id],
                ).df()
                if frame.empty:
                    continue
                row = _records(frame)[0]
                played = bool(row["played"])
                actual = _optional_number(row["target_actual_fantasy_points"])
                provenance = "CANONICAL_VERIFIED_TARGET" if actual is not None else None
                if actual is None and played:
                    actual, provenance = _canonical_actual_fantasy_points(row)
                output[player_id] = {
                    "game_id": str(row["game_id"]), "played": played,
                    "game_date": row["game_date"],
                    "actual_minutes": _optional_number(row["minutes"]),
                    "actual_fp": actual,
                    "actual_fp_provenance": provenance,
                }
        return output

    def persist_turn_outcome(
            self, shadow_snapshot_id: str, completed_turn: int,
            observed_scores: Mapping[str, Any], completed_games: list[Mapping[str, Any]],
            *, prediction_run_id: str, source_live_run_id: str | None = None,
            attached_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = attached_at or datetime.now(UTC)
        stable = {
            "shadow_snapshot_id": shadow_snapshot_id, "completed_turn": int(completed_turn),
            "prediction_run_id": prediction_run_id, "observed_scores": dict(observed_scores),
            "completed_games": completed_games,
        }
        fingerprint = json_fingerprint(stable)
        outcome_id = stable_id("phase8b_turn_outcome", fingerprint)
        with connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_shadow_turn_outcomes VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) ON CONFLICT DO NOTHING
                """,
                [outcome_id, shadow_snapshot_id, int(completed_turn), prediction_run_id,
                 source_live_run_id, json.dumps(observed_scores, sort_keys=True),
                 json.dumps(completed_games, default=str, sort_keys=True), now, fingerprint, now],
            )
            stored = connection.execute(
                """SELECT turn_outcome_id, outcome_fingerprint, observed_scores_json,
                          completed_games_json, attached_at
                   FROM fantasy_shadow_turn_outcomes
                   WHERE shadow_snapshot_id=? AND completed_turn=?""",
                [shadow_snapshot_id, int(completed_turn)],
            ).fetchone()
        if str(stored[1]) != fingerprint:
            raise ValueError("immutable completed-Turn outcome conflict")
        return {
            "turn_outcome_id": stored[0], "completed_turn": int(completed_turn),
            "outcome_fingerprint": stored[1], "observed_scores": _json(stored[2], {}),
            "completed_games": _json(stored[3], []), "attached_at": _value(stored[4]),
        }

    def latest_turn_outcome(self, shadow_snapshot_id: str) -> dict[str, Any] | None:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT turn_outcome_id, completed_turn, observed_scores_json,
                       completed_games_json, outcome_fingerprint, attached_at
                FROM fantasy_shadow_turn_outcomes WHERE shadow_snapshot_id=?
                ORDER BY completed_turn DESC, attached_at DESC LIMIT 1
                """, [shadow_snapshot_id],
            ).fetchone()
        if row is None:
            return None
        return {"turn_outcome_id": row[0], "completed_turn": int(row[1]),
                "observed_scores": _json(row[2], {}), "completed_games": _json(row[3], []),
                "outcome_fingerprint": row[4], "attached_at": _value(row[5])}

    def persist_advisor_run(self, payload: Mapping[str, Any]) -> str:
        stable = {key: value for key, value in payload.items() if key not in {"created_at"}}
        fingerprint = json_fingerprint(stable)
        advisor_id = stable_id("phase8b_advisor", fingerprint)
        with connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_turn_advisor_runs VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) ON CONFLICT DO NOTHING
                """,
                [advisor_id, payload["shadow_snapshot_id"], payload["turn_outcome_id"],
                 int(payload["completed_turn"]), payload["status"],
                 json.dumps(payload["current_lineup"], sort_keys=True),
                 json.dumps(payload["recommended_lineup"], sort_keys=True),
                 json.dumps(payload.get("actions", []), default=str),
                 json.dumps(payload.get("evidence", {}), default=str),
                 json.dumps(payload.get("simulation", {}), default=str),
                 payload["input_fingerprint"], fingerprint, datetime.now(UTC)],
            )
        return advisor_id

    def latest_advisor_run(self, shadow_snapshot_id: str) -> dict[str, Any] | None:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT advisor_run_id, completed_turn, status, current_lineup_json,
                       recommended_lineup_json, actions_json, evidence_json, simulation_json,
                       advisor_fingerprint, created_at
                FROM fantasy_turn_advisor_runs WHERE shadow_snapshot_id=?
                ORDER BY completed_turn DESC, created_at DESC LIMIT 1
                """, [shadow_snapshot_id],
            ).fetchone()
        if row is None:
            return None
        return {"advisor_run_id": row[0], "completed_turn": int(row[1]), "status": row[2],
                "current_lineup": _json(row[3], {}), "recommended_lineup": _json(row[4], {}),
                "actions": _json(row[5], []), "evidence": _json(row[6], {}),
                "simulation": _json(row[7], {}), "advisor_fingerprint": row[8],
                "created_at": _value(row[9])}

    def persist_shadow_evaluation(
            self, shadow_snapshot_id: str, player_metrics: Mapping[str, Any],
            strategy_metrics: Mapping[str, Any], *, evaluated_at: datetime | None = None,
    ) -> str:
        stable = {"shadow_snapshot_id": shadow_snapshot_id,
                  "player_metrics": player_metrics, "strategy_metrics": strategy_metrics}
        fingerprint = json_fingerprint(stable)
        evaluation_id = stable_id("phase8b_shadow_evaluation", fingerprint)
        with connect_database(self.database_path) as connection:
            connection.execute(
                """INSERT INTO fantasy_shadow_matchday_evaluations VALUES (
                     ?, ?, ?, ?, ?, ?
                   ) ON CONFLICT DO NOTHING""",
                [evaluation_id, shadow_snapshot_id, json.dumps(player_metrics, default=str),
                 json.dumps(strategy_metrics, default=str), fingerprint,
                 evaluated_at or datetime.now(UTC)],
            )
        return evaluation_id

    def price_feature_rows(
            self, season: str, matchday: int, players: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add only pre-Matchday credit and realized-history lags to live predictions."""

        if not players:
            return []
        with connect_database(self.database_path, read_only=True) as connection:
            history = connection.execute(
                """
                WITH market AS (
                  SELECT fantasy_entity_id, matchday_number, credits
                  FROM leakage_safe_fantasy_market
                  WHERE season_code=? AND credits_valid_pre_matchday AND credits IS NOT NULL
                  QUALIFY row_number() OVER (
                    PARTITION BY fantasy_entity_id, matchday_number
                    ORDER BY observed_at DESC, snapshot_record_id DESC
                  )=1
                )
                SELECT fantasy_entity_id, matchday_number, credits,
                       lag(credits, 1) OVER entity_order AS previous_credits,
                       lag(credits, 2) OVER entity_order AS previous2_credits
                FROM market
                WINDOW entity_order AS (PARTITION BY fantasy_entity_id ORDER BY matchday_number)
                QUALIFY matchday_number=?
                """, [season, int(matchday)],
            ).df()
            recent = connection.execute(
                """
                SELECT target.player_id, game.round_number,
                       target.actual_fantasy_points AS actual_fp,
                       target.target_minutes AS actual_minutes
                FROM ml_player_game_targets_v1 AS target
                JOIN current_games AS game ON game.canonical_game_id=target.game_id
                WHERE game.season_code=? AND game.round_number < ? AND game.played
                QUALIFY row_number() OVER (
                  PARTITION BY target.player_id ORDER BY game.game_date DESC, game.game_code DESC
                ) <= 3
                """, [season, int(matchday)],
            ).df()
        credits = {str(row.fantasy_entity_id): row for row in history.itertuples(index=False)}
        recent_groups = {str(key): value.sort_values("round_number", ascending=False)
                         for key, value in recent.groupby("player_id")}
        output = []
        for source in players:
            row = dict(source)
            market = credits.get(str(row.get("entity_id")))
            games = recent_groups.get(str(row.get("player_id")), pd.DataFrame())
            current = float(row.get("credits") or 0.0)
            previous = _optional_number(getattr(market, "previous_credits", None))
            previous2 = _optional_number(getattr(market, "previous2_credits", None))
            row.update({
                "credits": current,
                "credit_delta_1": current - previous if previous is not None else None,
                "credit_delta_2": previous - previous2
                if previous is not None and previous2 is not None else None,
                "previous_actual_fp": _frame_first(games, "actual_fp"),
                "previous_actual_minutes": _frame_first(games, "actual_minutes"),
                "recent_actual_fp3": _frame_mean(games, "actual_fp"),
                "matchday_number": int(matchday),
            })
            output.append(row)
        return output

    def persist_price_predictions(
            self, prediction_run_id: str, scenario_id: str, season: str, matchday: int,
            rows: list[Mapping[str, Any]], artifact_fingerprint: str,
            *, predicted_at: datetime | None = None,
    ) -> None:
        now = predicted_at or datetime.now(UTC)
        with connect_database(self.database_path) as connection:
            for row in rows:
                stable = {"prediction_run_id": prediction_run_id, "scenario_id": scenario_id,
                          "season": season, "matchday": int(matchday),
                          "target_matchday": int(matchday) + 1, "entity_id": row["entity_id"],
                          "input": row["price_input"], "artifact": artifact_fingerprint}
                fingerprint = json_fingerprint(stable)
                prediction_id = stable_id("phase8b_price_prediction", fingerprint)
                connection.execute(
                    """
                    INSERT INTO fantasy_price_prediction_snapshots VALUES (
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    ) ON CONFLICT DO NOTHING
                    """,
                    [prediction_id, row["price_model_version"], prediction_run_id, scenario_id,
                     season, int(matchday), int(matchday) + 1, row["entity_id"],
                     row.get("player_id") or None, float(row["current_credits"]),
                     float(row["expected_next_price"]), float(row["expected_credit_change"]),
                     float(row["probability_increase"]), float(row["probability_decrease"]),
                     json.dumps(row["price_input"], default=str), fingerprint, now],
                )

    def latest_price_predictions(
            self, prediction_run_id: str, scenario_id: str,
    ) -> dict[str, dict[str, Any]]:
        with connect_database(self.database_path, read_only=True) as connection:
            frame = connection.execute(
                """
                SELECT fantasy_entity_id AS entity_id, expected_next_price,
                       expected_credit_change, probability_increase, probability_decrease,
                       price_model_version, predicted_at
                FROM fantasy_price_prediction_snapshots
                WHERE prediction_run_id=? AND scenario_id=?
                """, [prediction_run_id, scenario_id],
            ).df()
        return {str(row["entity_id"]): row for row in _records(frame)}

    def attach_price_outcomes(self, *, attached_at: datetime | None = None) -> int:
        """Attach a later official market price without altering its earlier forecast."""

        now = attached_at or datetime.now(UTC)
        with connect_database(self.database_path) as connection:
            frame = connection.execute(
                """
                SELECT prediction.price_prediction_id, prediction.current_credits,
                       prediction.expected_next_price, market.snapshot_record_id,
                       market.credits
                FROM fantasy_price_prediction_snapshots AS prediction
                JOIN fantasy_market_snapshots AS market
                  ON market.season_code=prediction.season_code
                 AND market.matchday_number=prediction.target_matchday
                 AND market.fantasy_entity_id=prediction.fantasy_entity_id
                 AND market.observed_at > prediction.predicted_at
                LEFT JOIN fantasy_price_prediction_outcomes AS outcome
                  ON outcome.price_prediction_id=prediction.price_prediction_id
                WHERE outcome.price_prediction_id IS NULL AND market.credits IS NOT NULL
                QUALIFY row_number() OVER (
                  PARTITION BY prediction.price_prediction_id
                  ORDER BY market.observed_at, market.snapshot_record_id
                )=1
                """
            ).df()
            for row in frame.itertuples(index=False):
                error = float(row.credits) - float(row.expected_next_price)
                actual_direction = np.sign(float(row.credits) - float(row.current_credits))
                predicted_direction = np.sign(float(row.expected_next_price) - float(row.current_credits))
                stable = {"price_prediction_id": row.price_prediction_id,
                          "market_snapshot_record_id": row.snapshot_record_id,
                          "actual_next_price": float(row.credits)}
                fingerprint = json_fingerprint(stable)
                connection.execute(
                    """INSERT INTO fantasy_price_prediction_outcomes VALUES (
                         ?, ?, ?, ?, ?, ?, ?, ?, ?
                       ) ON CONFLICT DO NOTHING""",
                    [stable_id("phase8b_price_outcome", fingerprint), row.price_prediction_id,
                     row.snapshot_record_id, float(row.credits), abs(error), error * error,
                     bool(actual_direction == predicted_direction), fingerprint, now],
                )
        return len(frame)

    def latest_run(self, profile_id: str = "default") -> dict[str, Any] | None:
        with connect_database(self.database_path, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT control_center_run_id,
                       status,
                       season_code,
                       fantasy_matchday,
                       recommendations_json,
                       optimization_constraints_json,
                       market_snapshot_fingerprint,
                       prediction_snapshot_fingerprint,
                       rules_fingerprint,
                       snapshot_path,
                       created_at,
                       current_roster_json, bank_credits, transfer_limit,
                       prediction_run_id, manual_override_fingerprint,
                       run_fingerprint
                FROM fantasy_control_center_runs
                WHERE profile_id = ?
                ORDER BY created_at DESC, control_center_run_id DESC LIMIT 1
                """,
                [profile_id],
            ).fetchone()
        if row is None:
            return None
        result = {
            "control_center_run_id": row[0], "status": row[1], "season": row[2],
            "matchday": row[3], "recommendations": _json(row[4], []),
            "constraints": _json(row[5], {}), "market_fingerprint": row[6],
            "prediction_fingerprint": row[7], "rules_fingerprint": row[8],
            "snapshot_path": row[9], "created_at": _value(row[10]),
            "profile_id": profile_id, "current_roster": _json(row[11], []),
            "bank_credits": row[12], "transfers_available": row[13],
            "snapshot": {
                "market_fingerprint": row[6], "prediction_fingerprint": row[7],
                "rules_fingerprint": row[8], "prediction_run_id": row[14],
                "manual_override_fingerprint": row[15],
            },
            "restored_without_snapshot": True,
        }
        # Keep mode, strategy, alternatives and input identity across a reload.
        # Fall back to the DB summary if the immutable file is unavailable or
        # changed; a summary alone must not be presented as a current result.
        if row[9]:
            try:
                saved = json.loads(Path(row[9]).read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    stable = {key: value for key, value in saved.items()
                              if key not in {"generated_at", "snapshot_path"}}
                    if json_fingerprint(stable) == row[16]:
                        result = {**saved, **{
                            key: result[key] for key in
                            ("control_center_run_id", "snapshot_path", "created_at")
                        }}
            except (OSError, ValueError):
                pass
        return result


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return default


def _value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{key: _value(value) for key, value in row.items()} for row in frame.to_dict("records")]


_PREPARATION_FP_INPUTS = (
    "points", "total_rebounds", "assists", "steals", "blocks",
    "fouls_drawn", "turnovers", "blocks_received", "fouls_committed",
    "two_points_made", "two_points_attempted", "three_points_made",
    "three_points_attempted", "free_throws_made", "free_throws_attempted",
)

_PREPARATION_FP_POSITIVE_INPUTS = frozenset({
    "points", "total_rebounds", "assists", "steals", "blocks", "fouls_drawn",
})


def _canonical_actual_fantasy_points(
        row: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    """Score a completed canonical box score when the materialized target lags."""

    if row.get("player_game_id") is None or bool(row.get("did_not_play")):
        return 0.0, "DNP_ZERO_AFTER_COMPLETED_GAME"

    home_score = _optional_number(row.get("home_score"))
    away_score = _optional_number(row.get("away_score"))
    team_id = str(row.get("stat_team_id") or "")
    if home_score is None or away_score is None or not team_id:
        return None, "BOX_SCORE_RESULT_INCOMPLETE"
    if team_id == str(row.get("home_team_id") or ""):
        team_won = home_score > away_score
    elif team_id == str(row.get("away_team_id") or ""):
        team_won = away_score > home_score
    else:
        return None, "BOX_SCORE_TEAM_UNRESOLVED"

    missing = [name for name in _PREPARATION_FP_INPUTS if row.get(name) is None]
    if not missing:
        result = score_player_game(row, team_won=team_won)
        return float(result.total), "COMPUTED_FROM_CANONICAL_BOX_SCORE"

    # EuroLeague PIR uses the same player-event base as the Fantasy formula.
    # It is a safe fallback when an otherwise valid provider row omits one of
    # the individual box-score components needed to reconstruct that base.
    pir = _optional_number(row.get("pir"))
    if pir is None:
        return None, "BOX_SCORE_SCORING_INPUTS_INCOMPLETE"
    actual = pir + (abs(pir) * 0.1 if team_won else 0.0)
    return actual, "COMPUTED_FROM_CANONICAL_PIR"


def _score_preparation_rows(
        rows: list[dict[str, Any]], game: Mapping[str, Any],
) -> None:
    """Attach exact or explicitly labelled preparation Fantasy scores in-place."""

    home_score, away_score = game.get("home_score"), game.get("away_score")
    result_known = home_score is not None and away_score is not None
    for row in rows:
        raw_team = row.pop("team_name_raw", None)
        team_won: bool | None = None
        if result_known and raw_team == game.get("home_team"):
            team_won = float(home_score) > float(away_score)
        elif result_known and raw_team == game.get("away_team"):
            team_won = float(away_score) > float(home_score)

        missing = [name for name in _PREPARATION_FP_INPUTS if row.get(name) is None]
        base: float | None = None
        kind = "UNAVAILABLE"
        if not missing:
            # Calculate from the verified Fantasy formula; no missing component
            # is coerced to zero.
            exact = score_player_game(row, team_won=bool(team_won))
            base = float(exact.base_score)
            kind = "EXACT" if team_won is not None else "EXACT_BASE_ONLY"
        elif row.get("pir") is not None:
            base = float(row["pir"])
            kind = "PIR_EQUIVALENT" if team_won is not None else "PIR_BASE_ONLY"
        elif row.get("efficiency") is not None:
            base = float(row["efficiency"])
            kind = "VAL_ESTIMATE" if team_won is not None else "VAL_BASE_ONLY"
        elif missing and set(missing) <= _PREPARATION_FP_POSITIVE_INPUTS:
            # Missing positive events can safely be omitted to produce a
            # conservative lower estimate. Never omit an unknown penalty,
            # missed shot, turnover, foul, or block received.
            available = dict(row)
            for name in missing:
                available[name] = 0
            estimate = score_player_game(available, team_won=False)
            base = float(estimate.base_score)
            kind = (
                "CONSERVATIVE_LOWER_BOUND"
                if team_won is not None else "CONSERVATIVE_LOWER_BOUND_BASE_ONLY"
            )

        row["fantasy_base_score"] = base
        if base is None:
            row["fantasy_points"] = None
        elif team_won is None:
            row["fantasy_points"] = base
        else:
            row["fantasy_points"] = base + (abs(base) * 0.1 if team_won else 0.0)
        row["fantasy_points_kind"] = kind
        row["fantasy_points_missing"] = missing
        # The shared PIR/VAL column remains useful when the source calls the
        # published valuation "efficiency" rather than PIR.
        if row.get("pir") is None:
            row["pir"] = row.get("efficiency")


def _history_fantasy_target(connection: Any) -> tuple[str, str]:
    exists = connection.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='ml_player_game_targets_v1'"
    ).fetchone()[0]
    if exists:
        return (
            "LEFT JOIN ml_player_game_targets_v1 AS target "
            "ON target.player_game_id=stat.player_game_id",
            "target.actual_fantasy_points",
        )
    return "", "CAST(NULL AS DOUBLE)"


def _first_finite(features: Mapping[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        try:
            value = float(features.get(name))
        except (TypeError, ValueError):
            continue
        if value == value:
            return value
    return None


def _optional_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _finite_numbers(values: list[Any]) -> list[float]:
    return [number for value in values if (number := _optional_number(value)) is not None]


def _rounded_mean(values: list[Any]) -> float | None:
    finite = _finite_numbers(values)
    return round(float(np.mean(finite)), 1) if finite else None


def _rounded_sum(values: list[Any]) -> float | None:
    finite = _finite_numbers(values)
    return round(sum(finite), 1) if finite else None


def _frame_first(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.iloc[0]) if not values.empty else None


def _frame_mean(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None
