from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src.control_center.repository import ControlCenterRepository
from src.control_center.service import ControlCenterService
from src.control_center.state import ControlCenterState
from src.db.database import connect_database, initialize_database
from scripts.optimize_fantasy import _load_live_market
from tests.test_phase5a import seed_minimal_database


def test_market_players_remain_visible_without_predictions_and_keep_official_status() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(Path(directory) / "focused.duckdb", fantasy_config=None)
        service.repository.prediction_context = lambda season, matchday, **kwargs: {
            "prediction_run_id": "run", "scenarios": ["base"],
        }
        service.repository.player_predictions = lambda run, scenario: [{
            "entity_id": "OTHER", "player_id": "P-OTHER", "name": "Predicted Player",
            "expected_fp": 20.0, "availability": "AVAILABLE",
        }]
        service.repository.market_entities = lambda season, matchday: [
            {"entity_id": "OTHER", "entity_type": "PLAYER", "player_id": "P-OTHER",
             "name": "Predicted Player", "team_name": "Other", "position": "Guard",
             "credits": 10.0, "source_turn": 1, "market_injured": False,
             "market_play_probability": None},
            {"entity_id": "NUNN", "entity_type": "PLAYER", "player_id": "P-NUNN",
             "name": "Kendrick Nunn", "team_name": "Panathinaikos AKTOR Athens",
             "position": "Guard", "credits": 14.6, "source_turn": 1,
             "market_injured": True, "market_play_probability": 0.0,
             "observed_at": datetime(2026, 9, 3, tzinfo=UTC)},
        ]
        service._enrich_price_intelligence = lambda rows, *args: rows
        rows = service.players(state=ControlCenterState(
            season_code="E2026", fantasy_matchday=1, scenario_id="base",
        ))
    assert {row["name"] for row in rows} == {"Predicted Player", "Kendrick Nunn"}
    nunn = next(row for row in rows if row["name"] == "Kendrick Nunn")
    assert nunn["availability"] == "INJURED"
    assert nunn["availability_group"] == "RED"
    assert nunn["availability_source"] == "official_fantasy_market"


def test_history_queries_return_existing_game_player_and_team_box_data() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "history.duckdb"
        initialize_database(database)
        player_id, team_id, game_id = seed_minimal_database(database)
        with connect_database(database) as connection:
            away_id = connection.execute(
                "SELECT away_team_id FROM games WHERE canonical_game_id=?", [game_id]
            ).fetchone()[0]
            artifact_id = connection.execute("SELECT artifact_id FROM raw_artifacts").fetchone()[0]
            connection.execute(
                "UPDATE games SET played=true, game_status='Played', home_score=88, away_score=80 "
                "WHERE canonical_game_id=?", [game_id],
            )
            connection.execute(
                """
                INSERT INTO player_game_stats (
                  player_game_id, canonical_game_id, canonical_player_id,
                  canonical_team_id, opponent_team_id, home_away, starter,
                  source_is_playing, minutes_raw, minutes, did_not_play,
                  points, total_rebounds, assists, steals, turnovers, blocks, pir,
                  source_artifact_id, ingestion_run_id
                ) VALUES ('PG1', ?, ?, ?, ?, 'home', true, true, '28:30', 28.5,
                          false, 17, 5, 6, 2, 3, 1, 22, ?, 'seed-run')
                """, [game_id, player_id, team_id, away_id, artifact_id],
            )
            connection.execute(
                """
                INSERT INTO team_game_stats (
                  team_game_id, canonical_game_id, canonical_team_id, opponent_team_id,
                  home_away, coach_name, points, total_rebounds, assists, pir,
                  source_artifact_id, ingestion_run_id
                ) VALUES ('TG1', ?, ?, ?, 'home', 'MARTINEZ, PEDRO',
                          88, 34, 21, 101, ?, 'seed-run')
                """, [game_id, team_id, away_id, artifact_id],
            )
            observed = datetime(2026, 10, 2, tzinfo=UTC)
            connection.execute(
                """
                INSERT INTO fantasy_entities VALUES (
                  'COACH1', 'fantasy', 'coach-1', 'COACH', 'Old Coach', ?, ?
                )
                """, [observed, observed],
            )
            connection.execute(
                """
                INSERT INTO fantasy_market_snapshots (
                  snapshot_record_id, snapshot_batch_id, observed_at, season_code,
                  matchday_id, matchday_number, turn_number, fantasy_entity_id,
                  canonical_team_id, opponent_team_id, team_name_raw,
                  opponent_name_raw, position_name, credits,
                  status_valid_as_of_matchday, status_semantics,
                  position_semantics, price_semantics,
                  source_artifact_id, ingestion_run_id
                ) VALUES (
                  'COACH-SNAPSHOT', 'COACH-BATCH', ?, 'E2026', 1, 1, 1,
                  'COACH1', ?, ?, 'Alpha', 'Beta', 'Head Coach', 8.0,
                  false, 'OBSERVED_AT_COLLECTION_ONLY', 'CURRENT', 'CURRENT',
                  ?, 'seed-run'
                )
                """, [observed, team_id, away_id, artifact_id],
            )
        repository = ControlCenterRepository(database)
        games = repository.history_games({"season": "E2026", "query": "Alpha"})
        detail = repository.history_game_detail(game_id)
        players = repository.history_players({"season": "E2026", "team_id": team_id})
        round_players = repository.history_players({"season": "E2026", "round": 1})
        later_round_players = repository.history_players({"season": "E2026", "round": 2})
        high_minutes_players = repository.history_players({
            "season": "E2026", "min_minutes": 29,
        })
        catalog = repository.history_catalog()
        teams = repository.history_teams({"season": "E2026", "team_id": team_id})
        player_detail = repository.history_player_detail(player_id, {"season": "E2026"})
        team_detail = repository.history_team_detail(team_id, {"season": "E2026"})
        outcomes = repository.current_roster_game_outcomes(
            "E2026", 1, [{"player_id": player_id, "team_id": team_id}],
        )
        coach = repository.market_entities("E2026", 1)[0]
        with connect_database(database, read_only=True) as connection:
            optimizer_coach = _load_live_market(connection, "E2026", 1).iloc[0]
        recent = repository.player_recent_form([player_id])[player_id]
    assert len(games) == 1 and games[0]["player_rows"] == 1
    assert detail["game"]["home_score"] == 88
    assert detail["player_stats"][0]["player"] == "Test Player"
    assert detail["player_stats"][0]["minutes"] == 28.5
    assert players[0]["games_played"] == 1 and teams[0]["games"] == 1
    assert players[0]["fantasy_points_avg"] == 24.2
    assert "fantasy_points" not in players[0]
    assert round_players[0]["player_id"] == player_id
    assert round_players[0]["fantasy_points"] == 24.2
    assert round_players[0]["minutes"] == 28.5
    assert round_players[0]["points"] == 17.0
    assert round_players[0]["turnovers"] == 3.0
    assert "free_throws_made" in round_players[0]
    assert "fouls_committed_avg" in players[0]
    assert round_players[0]["credits_start"] is None
    assert "credits_change" in players[0]
    assert recent["last_game_fp"] == 24.2
    assert recent["last_game_minutes"] == 28.5
    assert recent["last5_games"][0]["game_id"] == game_id
    assert later_round_players == [] and high_minutes_players == []
    assert {row["round_number"] for row in catalog["rounds"]} == {1}
    assert player_detail["games"][0]["game_id"] == game_id
    assert "fantasy_credits_pre_matchday" in player_detail["games"][0]
    assert team_detail["roster"][0]["player"] == "Test Player"
    assert outcomes[player_id]["actual_fp"] == 24.2
    assert outcomes[player_id]["actual_fp_provenance"] == "COMPUTED_FROM_CANONICAL_PIR"
    assert coach["name"] == "Pedro Martinez"
    assert coach["name_source"] == "official_completed_game_box_score"
    assert optimizer_coach["name"] == "Pedro Martinez"


def test_current_team_strategy_uses_saved_roster_and_respects_locks() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(Path(directory) / "strategy.duckdb", fantasy_config=None)
        state = ControlCenterState(
            season_code="E2026", fantasy_matchday=1,
            roster_entity_ids=("OWNED", "COACH"), bank_credits=2.0,
            player_constraints={"OWNED": "FORCE_INCLUDE"},
        )
        market = [
            {"entity_id": "OWNED", "entity_type": "PLAYER", "player_id": "P1",
             "name": "Owned Guard", "team_name": "Alpha", "position": "Guard", "credits": 8.0},
            {"entity_id": "COACH", "entity_type": "COACH", "name": "Owned Coach",
             "team_name": "Alpha", "position": "Coach", "credits": 6.0},
            {"entity_id": "OPTION", "entity_type": "PLAYER", "player_id": "P2",
             "name": "Option Guard", "team_name": "Beta", "position": "Guard", "credits": 9.0},
        ]
        players = [
            {**market[0], "expected_fp": 12.0, "availability": "AVAILABLE", "availability_group": "GREEN"},
            {**market[2], "expected_fp": 18.0, "availability": "AVAILABLE", "availability_group": "GREEN"},
        ]
        service.repository.load_state = lambda profile="default": state
        service.repository.market_entities = lambda season, matchday: market
        service.players = lambda **kwargs: players
        result = service.team_strategy()
    assert result["source"] == "CURRENT_TEAM"
    assert result["state_fingerprint"] == state.fingerprint
    assert {row["name"] for row in result["actions"]} == {"Owned Guard", "Owned Coach"}
    owned = next(row for row in result["actions"] if row["name"] == "Owned Guard")
    assert owned["locked"] is True and owned["recommendation"] == "KEEP"


def test_ui_connects_current_team_strategy_history_and_apply_workflow() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    styles = Path("web/control_center/styles.css").read_text(encoding="utf-8")
    assert 'data-tab="team" aria-current="page"' in html
    assert 'data-tab="history"' in html and "2026 Preseason" in html
    assert 'id="team-analyze"' in html and 'id="current-team-strategy"' in html
    assert 'id="team-lock-selected"' in html
    assert 'api("/api/strategy/current")' in script
    assert 'api("/api/team"' in script and "useRecommendationAsCurrentTeam" in script
    assert "/api/history/game/" in script and "openHistoryGame" in script
    assert "history-player-table" in script and "Average FP" in script
    assert ".table-wrap thead th { position: sticky; top: 0;" in styles
    assert ".table-wrap tr > :first-child:not([colspan]) { position: sticky; left: 0; }" in styles
    assert "max-height: min(72vh,760px)" in styles
    assert "Minimum average minutes" in script and "All rounds" in script
    assert "Avg FT" in script and "Average turnovers" in script
    assert "Personal fouls" in script and "Fouls won" in script
    assert "Start Cr" in script and "Total Δ Cr" in script
    assert "historyGameLog(value.games || [], true)" in script
    assert 'class="numeric">CR</th>' in script
    assert "row.fantasy_credits_pre_matchday" in script
    assert "Expected Role" in script and "expectedRole(perf)" in script
    assert "const compactExpectedRole" in script
    assert "expected-role-callout" in styles
    assert "FP/min" in script and "fp_per_minute" in script
    assert "renderQuickAlternatives" in script and "credit_distance" in script
    assert 'const toggle = `<button class="tiny toggle-team-lock"' in script
    assert 'const alternatives = !manualLock && !coach' in script
    assert "FIXED FOR COMPLETION" in script
    assert "Coach locked by default" not in script
    assert "quick-alternatives" in styles
    assert "lockSelectedTeamEntities" in script
    assert 'constraints[entity] = "FORCE_INCLUDE"' in script
