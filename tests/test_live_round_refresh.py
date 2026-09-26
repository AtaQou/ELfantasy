from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.control_center.repository import ControlCenterRepository
from src.db.database import connect_database, initialize_database
from src.db.ids import stable_id
from src.live.current_features import _candidate_rows
from src.live.current_season import _insert_schedule_snapshot
from src.live.prediction import resolve_fantasy_matchday
from tests.test_phase5a import seed_minimal_database


class LiveRoundRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "test.duckdb"
        initialize_database(self.database)
        self.player, _, self.game = seed_minimal_database(self.database)
        self.now = datetime(2026, 10, 1, 12, tzinfo=UTC)
        self.teams = {code: stable_id("team", "euroleague", code)
                      for code in ("AAA", "BBB", "CCC", "DDD")}
        with connect_database(self.database) as c:
            self.artifact = c.execute("SELECT source_artifact_id FROM games").fetchone()[0]
            for code in ("CCC", "DDD"):
                c.execute("INSERT INTO teams VALUES (?, ?, ?, NULL, 'test')",
                          [self.teams[code], code, code])
            self.snapshot(c, 1, "AAA", "BBB", False)
            self.snapshot(c, 2, "CCC", "DDD", True)
            for index, (team, opponent) in enumerate(
                [("AAA", "BBB"), ("BBB", "AAA"), ("CCC", "DDD"), ("DDD", "CCC")]
            ):
                entity = f"entity-{index}"
                c.execute("INSERT INTO fantasy_entities VALUES (?, 'fantasy', ?, 'PLAYER', ?, ?, ?)",
                          [entity, str(index), entity, self.now, self.now])
                c.execute("""
                    INSERT INTO fantasy_market_snapshots (
                        snapshot_record_id, snapshot_batch_id, observed_at, season_code,
                        matchday_id, matchday_number, fantasy_entity_id, credits,
                        canonical_team_id, opponent_team_id, status_valid_as_of_matchday,
                        status_semantics, position_semantics, price_semantics,
                        source_artifact_id, ingestion_run_id
                    ) VALUES (?, 'batch', ?, 'E2026', 1, 1, ?, 10, ?, ?, false,
                        'OBSERVED_AT_COLLECTION_ONLY', 'CURRENT', 'CURRENT', ?, 'seed-run')
                """, [entity, self.now, entity, self.teams[team], self.teams[opponent], self.artifact])
            c.execute("""
                INSERT INTO fantasy_player_crosswalk (
                    crosswalk_id, fantasy_entity_id, canonical_player_id, mapping_status,
                    confidence, match_method, season_code, source_artifact_id, ingestion_run_id
                ) VALUES ('mapping', 'entity-0', ?, 'MATCHED', 'HIGH', 'TEST',
                          'E2026', ?, 'seed-run')
            """, [self.player, self.artifact])
            c.execute("INSERT INTO players (canonical_player_id,canonical_name,created_from_source) "
                      "VALUES ('official-player','Official Player','test')")
            self.roster(c, "official-player", "CCC", "CURRENT_ROSTER")

    def snapshot(self, c, code, home, away, played, round_number=1):
        _insert_schedule_snapshot(c, {
            "canonical_game_id": stable_id("game", "E", "E2026", code),
            "season_code": "E2026", "game_code": code, "phase_code": "RS",
            "round_number": round_number, "home_team_code": home, "away_team_code": away,
            "scheduled_tip_time": datetime(2026, 10, 1, 18, tzinfo=UTC),
            "played": played, "game_status": "Confirmed",
            "home_score": 88 if played else None, "away_score": 80 if played else None,
        }, "UPDATED_GAME", f"schedule-{code}", self.now, self.artifact)

    def roster(self, c, player, team, status):
        c.execute("""
            INSERT INTO live_roster_snapshots (
                roster_snapshot_id,snapshot_batch_id,captured_at,season_code,
                canonical_player_id,canonical_team_id,roster_status,source_identifier,row_fingerprint
            ) VALUES (?, 'roster', ?, 'E2026', ?, ?, ?, 'test', 'test')
        """, [player, self.now, player, self.teams[team], status])

    def slate(self):
        return pd.DataFrame({"round_number": [1, 1],
                             "team_id": [self.teams["AAA"], self.teams["BBB"]],
                             "opponent_team_id": [self.teams["BBB"], self.teams["AAA"]]})

    def test_remaining_turn_resolves_against_complete_round(self):
        result = resolve_fantasy_matchday("E2026", self.slate(), database_path=self.database)
        self.assertEqual((result.status, result.fantasy_matchday), ("MATCHDAY_RESOLVED", 1))

    def test_conflicting_market_pair_stays_ambiguous(self):
        with connect_database(self.database) as c:
            c.execute("UPDATE fantasy_market_snapshots SET opponent_team_id=? "
                      "WHERE fantasy_entity_id='entity-2'", [self.teams["AAA"]])
        result = resolve_fantasy_matchday("E2026", self.slate(), database_path=self.database)
        self.assertEqual(result.status, "MATCHDAY_AMBIGUOUS")

    def test_multiple_matching_rounds_stay_ambiguous(self):
        with connect_database(self.database) as c:
            self.snapshot(c, 3, "AAA", "BBB", False, round_number=2)
            self.snapshot(c, 4, "CCC", "DDD", False, round_number=2)
        result = resolve_fantasy_matchday("E2026", self.slate(), database_path=self.database)
        self.assertEqual(result.status, "MATCHDAY_AMBIGUOUS")

    def test_partial_official_roster_allows_uncovered_market_team(self):
        with connect_database(self.database) as c:
            rows, warnings = _candidate_rows(c, "E2026", self.now)
        self.assertEqual([row["player_id"] for row in rows], [self.player])
        self.assertTrue(any("uncovered teams" in warning for warning in warnings))

    def test_market_does_not_override_explicit_departure(self):
        with connect_database(self.database) as c:
            self.roster(c, self.player, "AAA", "LEFT_TEAM")
            rows, _ = _candidate_rows(c, "E2026", self.now)
        self.assertEqual(rows, [])

    def test_completed_game_is_not_crowded_out_by_future_schedule(self):
        with connect_database(self.database) as c:
            # Replace the single upcoming snapshot with a later final observation.
            c.execute("UPDATE live_schedule_snapshots SET played=true,home_score=88,away_score=80 "
                      "WHERE game_code=1")
            c.execute("""
                INSERT INTO games (canonical_game_id,season_id,competition_code,season_code,
                    game_code,round_number,game_date,home_team_id,away_team_id,played,
                    source_artifact_id,ingestion_run_id)
                SELECT 'future-' || n, season_id,competition_code,season_code,
                    n+100,2,game_date+INTERVAL '10 days',home_team_id,away_team_id,false,
                    source_artifact_id,ingestion_run_id FROM games,range(300) AS r(n)
            """)
        rows = ControlCenterRepository(self.database).history_games({"season": "E2026"})
        self.assertEqual(len(rows), 250)
        self.assertEqual((rows[0]["game_id"], rows[0]["home_score"]), (self.game, 88))
