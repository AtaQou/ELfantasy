from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.data.euroleague_client import APIResponse
from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.live.availability import (
    AvailabilityObservation,
    AvailabilityResolver,
    clear_availability_override,
    ingest_availability_observations,
    normalize_availability_status,
    normalize_reason,
    set_availability_override,
)
from src.live.current_features import (
    SlateBuildResult,
    frozen_required_features,
    train_live_parity_check,
    validate_frozen_schema,
)
from src.live.current_season import (
    _classify_schedule_row,
    current_season_code,
    update_current_season,
)
from src.live.freshness import record_source_refresh, source_freshness
from src.live.official_availability import (
    OfficialReportSource,
    collect_official_report,
    register_discovered_sources,
)
from src.live.pipeline import update_live
from src.modeling.ml_protocol import FORBIDDEN_MODEL_INPUTS


def canonical_has_live_dependencies() -> bool:
    if not DEFAULT_DATABASE_PATH.is_file():
        return False
    with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
        return bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='ml_player_game_core_plus_rich_v1'"
        ).fetchone()[0])


class Phase5AUnitTests(unittest.TestCase):
    def test_normalized_status_vocabulary_is_conservative(self) -> None:
        cases = {
            "ruled out for Thursday": "OUT",
            "questionable to play": "QUESTIONABLE",
            "game-time decision": "GAME_TIME_DECISION",
            "doubtful": "DOUBTFUL",
            "available to play": "AVAILABLE",
            "minutes restriction": "LIMITED",
            "deactivated from the roster": "NOT_REGISTERED",
            "missed Round 12": "UNKNOWN",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_availability_status(None, raw), expected)

    def test_reason_is_separate_from_status(self) -> None:
        self.assertEqual(normalize_reason("out with flu", "OUT"), "ILLNESS")
        self.assertEqual(normalize_reason("out with an ankle injury", "OUT"), "INJURY")
        self.assertEqual(normalize_reason("suspended", "SUSPENDED"), "SUSPENSION")

    def test_schedule_classifies_new_updated_unchanged_and_rescheduled(self) -> None:
        base = {
            "played": False, "local_game_date": datetime(2026, 9, 25, 20),
            "home_score": None, "away_score": None, "game_status": "Confirmed",
        }
        previous = {
            "played": False, "local": base["local_game_date"],
            "home_score": None, "away_score": None, "status": "Confirmed",
        }
        self.assertEqual(_classify_schedule_row(base, None), "NEW_GAME")
        self.assertEqual(_classify_schedule_row(base, previous), "UNCHANGED_GAME")
        self.assertEqual(
            _classify_schedule_row({**base, "played": True}, previous), "UPDATED_GAME"
        )
        self.assertEqual(
            _classify_schedule_row(
                {**base, "local_game_date": datetime(2026, 9, 26, 20)}, previous
            ),
            "POSTPONED_RESCHEDULED_GAME",
        )

    def test_current_season_boundary(self) -> None:
        self.assertEqual(current_season_code(datetime(2026, 6, 30, tzinfo=UTC)), "E2025")
        self.assertEqual(current_season_code(datetime(2026, 7, 1, tzinfo=UTC)), "E2026")

    def test_incremental_schedule_ingestion_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "test.duckdb"
            raw = root / "raw"
            client = FakeEuroLeagueClient()
            first = update_current_season(
                "E2026", database_path=database, raw_root=raw, client=client,
                captured_at=datetime(2026, 8, 18, 12, tzinfo=UTC),
            )
            second = update_current_season(
                "E2026", database_path=database, raw_root=raw, client=client,
                captured_at=datetime(2026, 8, 18, 13, tzinfo=UTC),
            )
            self.assertEqual(first.new_games, 1)
            self.assertEqual(second.unchanged_games, 1)
            self.assertEqual(client.round_calls, 1)
            with connect_database(database, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM games").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM live_schedule_snapshots"
                ).fetchone()[0], 2)
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM current_live_schedule "
                    "WHERE scheduled_tip_time IS NOT NULL"
                ).fetchone()[0], 1)

    def test_frozen_manifest_excludes_outcomes_and_availability(self) -> None:
        required = set(frozen_required_features())
        self.assertFalse(required & FORBIDDEN_MODEL_INPUTS)
        self.assertNotIn("resolved_availability_status", required)
        self.assertNotIn("current_fantasy_credits", required)
        self.assertIn("rot_minutes_ewma", required)

    def test_roster_membership_dates_accept_naive_and_aware_timestamps(self) -> None:
        cases = [
            ("2026-08-19T12:00:00", True, "CURRENT_ROSTER"),
            ("2026-08-17T12:00:00", True, "LEFT_TEAM"),
            ("2026-08-18T12:00:00", True, "LEFT_TEAM"),
            ("2026-08-18T14:00:00+03:00", True, "LEFT_TEAM"),
            ("2026-08-18T16:00:00+03:00", True, "CURRENT_ROSTER"),
            ("2026-08-19T12:00:00Z", True, "CURRENT_ROSTER"),
            (None, True, "CURRENT_ROSTER"),
            (None, False, "LEFT_TEAM"),
            ("2026-08-19T12:00:00", False, "LEFT_TEAM"),
        ]
        payload = {"data": [
            {
                "type": "J", "person": {"code": f"P{index}", "name": f"Player {index}"},
                "club": {"code": "AAA", "name": "Alpha"},
                "season": {"code": "E2026"}, "active": active,
                "startDate": "2026-07-01T00:00:00", "endDate": end,
            }
            for index, (end, active, _) in enumerate(cases)
        ]}
        for captured in (datetime(2026, 8, 18, 12, tzinfo=UTC), datetime(2026, 8, 18, 12)):
            with self.subTest(captured=captured), TemporaryDirectory() as directory:
                root = Path(directory)
                client = FakeEuroLeagueClient()
                with patch.object(client, "season_players", return_value=response(
                    "v2_season_players", json.dumps(payload), payload
                )):
                    result = update_current_season(
                        "E2026", database_path=root / "test.duckdb", raw_root=root / "raw",
                        client=client, captured_at=captured,
                    )
                self.assertEqual(result.roster_rows, len(cases))
                with connect_database(root / "test.duckdb", read_only=True) as connection:
                    statuses = dict(connection.execute(
                        "SELECT canonical_name, roster_status FROM live_roster_snapshots "
                        "JOIN players USING (canonical_player_id)"
                    ).fetchall())
                self.assertEqual(statuses, {
                    f"Player {index}": expected
                    for index, (_, _, expected) in enumerate(cases)
                })


class Phase5ADatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.duckdb"
        initialize_database(self.database)
        self.player_id, self.team_id, self.game_id = seed_minimal_database(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_snapshots_are_append_only_idempotent_and_timed(self) -> None:
        tip = datetime(2026, 10, 1, 18, tzinfo=UTC)
        observation = AvailabilityObservation(
            season_code="E2026", captured_at=tip - timedelta(hours=4),
            published_at=tip - timedelta(hours=5), effective_game_time=tip,
            source_type="OFFICIAL_EUROLEAGUE", source_identifier="official:test",
            canonical_player_id=self.player_id, canonical_team_id=self.team_id,
            canonical_game_id=self.game_id, matchday_number=1,
            raw_text="Player is ruled out with an ankle injury.",
        )
        first = ingest_availability_observations([observation], self.database)
        second = ingest_availability_observations([observation], self.database)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        with connect_database(self.database, read_only=True) as connection:
            row = connection.execute(
                "SELECT normalized_status, reason_category, timing_status, hours_before_tip "
                "FROM availability_snapshots"
            ).fetchone()
        self.assertEqual(row[:3], ("OUT", "INJURY", "PRE_GAME"))
        self.assertAlmostEqual(row[3], 5.0)

    def test_postgame_and_unknown_timestamps_never_become_pregame(self) -> None:
        tip = datetime(2026, 10, 1, 18, tzinfo=UTC)
        observations = [
            AvailabilityObservation(
                "E2026", tip + timedelta(hours=1), "OFFICIAL_EUROLEAGUE", "post",
                canonical_player_id=self.player_id, canonical_team_id=self.team_id,
                canonical_game_id=self.game_id, effective_game_time=tip,
                raw_status="OUT",
            ),
            AvailabilityObservation(
                "E2026", tip + timedelta(days=10), "OFFICIAL_EUROLEAGUE", "unknown",
                canonical_player_id=self.player_id, canonical_team_id=self.team_id,
                canonical_game_id=self.game_id, effective_game_time=tip,
                raw_status="OUT", timestamp_reliable=False,
            ),
        ]
        ingest_availability_observations(observations, self.database)
        with connect_database(self.database, read_only=True) as connection:
            values = dict(connection.execute(
                "SELECT source_identifier, timing_status FROM availability_snapshots"
            ).fetchall())
        self.assertEqual(values, {"post": "POST_GAME", "unknown": "UNKNOWN"})

    def test_priority_recency_conflicts_are_retained_and_deterministic(self) -> None:
        tip = datetime(2026, 10, 1, 18, tzinfo=UTC)
        ingest_availability_observations([
            AvailabilityObservation(
                "E2026", tip - timedelta(hours=10), "OFFICIAL_EUROLEAGUE", "league",
                canonical_player_id=self.player_id, canonical_team_id=self.team_id,
                canonical_game_id=self.game_id, matchday_number=1,
                effective_game_time=tip, normalized_status="QUESTIONABLE",
            ),
            AvailabilityObservation(
                "E2026", tip - timedelta(hours=8), "OFFICIAL_CLUB", "club",
                canonical_player_id=self.player_id, canonical_team_id=self.team_id,
                canonical_game_id=self.game_id, matchday_number=1,
                effective_game_time=tip, normalized_status="OUT",
            ),
        ], self.database)
        resolved = AvailabilityResolver(self.database).resolve(
            self.player_id, season_code="E2026", game_id=self.game_id,
            matchday_number=1, tip_time=tip, as_of=tip - timedelta(hours=1),
        )
        self.assertEqual(resolved.status, "OUT")
        self.assertTrue(resolved.conflict)
        with connect_database(self.database, read_only=True) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM availability_snapshots"
            ).fetchone()[0], 2)

    def test_override_scope_expiration_and_clear(self) -> None:
        now = datetime(2026, 10, 1, 12, tzinfo=UTC)
        set_availability_override(
            self.player_id, "LIMITED", database_path=self.database,
            season_code="E2026", game_id=self.game_id,
            expires_at=now + timedelta(hours=8), created_at=now,
        )
        resolver = AvailabilityResolver(self.database)
        active = resolver.resolve(
            self.player_id, season_code="E2026", game_id=self.game_id,
            matchday_number=1, tip_time=now + timedelta(hours=6),
            as_of=now + timedelta(hours=1),
        )
        self.assertEqual(active.status, "LIMITED")
        self.assertTrue(active.manual_override_active)
        expired = resolver.resolve(
            self.player_id, season_code="E2026", game_id=self.game_id,
            matchday_number=1, tip_time=now + timedelta(hours=12),
            as_of=now + timedelta(hours=9),
        )
        self.assertEqual(expired.status, "UNKNOWN")
        clear_availability_override(
            self.player_id, database_path=self.database, season_code="E2026",
            game_id=self.game_id, created_at=now + timedelta(hours=2),
        )
        cleared = resolver.resolve(
            self.player_id, season_code="E2026", game_id=self.game_id,
            matchday_number=1, tip_time=now + timedelta(hours=6),
            as_of=now + timedelta(hours=3),
        )
        self.assertEqual(cleared.status, "UNKNOWN")
        with connect_database(self.database, read_only=True) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM availability_override_events"
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM player_game_stats"
            ).fetchone()[0], 0)

    def test_freshness_uses_source_specific_thresholds_and_last_success(self) -> None:
        now = datetime(2026, 10, 1, 12, tzinfo=UTC)
        record_source_refresh(
            "schedule", "OFFICIAL", True, database_path=self.database,
            captured_at=now - timedelta(hours=2),
        )
        record_source_refresh(
            "schedule", "OFFICIAL", False, database_path=self.database,
            captured_at=now - timedelta(hours=1), message="temporary failure",
        )
        state = source_freshness(self.database, as_of=now)["schedule"]
        self.assertFalse(state.is_stale)
        self.assertFalse(state.last_attempt_succeeded)
        self.assertAlmostEqual(state.age_minutes or 0, 120.0)

    def test_official_access_failure_is_retained_without_bypass_or_false_rows(self) -> None:
        captured = datetime(2026, 9, 1, 12, tzinfo=UTC)
        source = OfficialReportSource(
            "E2026",
            "https://www.euroleaguebasketball.net/euroleague/news/test-report/",
            matchday_number=1,
        )
        register_discovered_sources([source], self.database, captured_at=captured)
        result = collect_official_report(
            source, database_path=self.database, captured_at=captured,
            session=FakeHTTP429Session(),
        )
        self.assertEqual(result.parse_status, "BLOCKED")
        with connect_database(self.database, read_only=True) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM official_availability_reports"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT parse_status FROM official_availability_reports"
            ).fetchone()[0], "BLOCKED")
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM availability_snapshots"
            ).fetchone()[0], 0)

    def test_live_orchestration_preserves_last_state_when_one_source_fails(self) -> None:
        slate = SlateBuildResult(
            "slate-test", "E2026", 0, 0, len(frozen_required_features()),
            True, (), "input", "output", (), 0.01,
        )
        with (
            patch("src.live.pipeline.update_current_season", side_effect=RuntimeError("offline")),
            patch("src.live.pipeline.load_source_manifest", return_value=[]),
            patch("src.live.pipeline.build_current_slate", return_value=slate),
        ):
            result = update_live(
                "E2026", database_path=self.database,
                collect_official_availability=False,
                as_of=datetime(2026, 9, 1, 12, tzinfo=UTC),
            )
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(result.stages["current_season"]["status"], "FAILED")
        self.assertEqual(result.stages["current_slate"]["status"], "SUCCEEDED")
        freshness = source_freshness(
            self.database, as_of=datetime(2026, 9, 1, 12, tzinfo=UTC)
        )
        self.assertTrue(freshness["schedule"].is_stale)


@unittest.skipUnless(canonical_has_live_dependencies(), "canonical Phase 3/4 artifacts absent")
class CanonicalPhase5AParityTests(unittest.TestCase):
    def test_live_adapter_matches_training_features_at_historical_cutoff(self) -> None:
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            player_game_id = str(connection.execute(
                """
                SELECT player_game_id FROM ml_player_game_core_plus_rich_v1
                WHERE season='E2025' AND rot_games_before>=5
                ORDER BY target_game_time, player_game_id LIMIT 1
                """
            ).fetchone()[0])
        result = train_live_parity_check([player_game_id])
        self.assertTrue(result["passed"], result["mismatches"])

    def test_persisted_current_slate_is_frozen_schema_compatible(self) -> None:
        result = validate_frozen_schema()
        if result["row_count"] == 0 and result["missing"]:
            self.skipTest("current slate has not been generated")
        self.assertTrue(result["compatible"], result["missing"])


class FakeEuroLeagueClient:
    def __init__(self) -> None:
        self.round_calls = 0

    def schedule(self, season: int) -> APIResponse:
        body = (
            "<schedule><item><gameday>1</gameday><round>RS</round>"
            "<date>Sep 25, 2026</date><startime>20:45</startime><game>1</game>"
            "<gamecode>E2026_1</gamecode><hometeam>Alpha</hometeam><homecode>AAA</homecode>"
            "<awayteam>Beta</awayteam><awaycode>BBB</awaycode><confirmeddate>true</confirmeddate>"
            "<confirmedtime>true</confirmedtime><played>false</played></item></schedule>"
        )
        return response("v1_schedule", body, body)

    def results(self, season: int) -> APIResponse:
        body = "<results></results>"
        return response("v1_results", body, body)

    def round_games(self, season: int, round_number: int) -> APIResponse:
        self.round_calls += 1
        game = {
            "id": "official-1", "identifier": "E2026_1", "gameCode": 1,
            "season": {"year": 2026, "code": "E2026", "competitionCode": "E"},
            "phaseType": {"code": "RS"}, "round": 1, "played": False,
            "localDate": "2026-09-25T20:45:00", "utcDate": "2026-09-25T18:45:00Z",
            "local": {"club": {"code": "AAA", "name": "Alpha"}, "score": 0, "partials": {}},
            "road": {"club": {"code": "BBB", "name": "Beta"}, "score": 0, "partials": {}},
            "venue": {"code": "ARENA", "name": "Arena", "capacity": 1000},
            "gameStatus": "Confirmed", "isNeutralVenue": False,
        }
        return response("v2_round_games", json.dumps({"data": [game]}), {"data": [game]})

    def season_players(self, season: int) -> APIResponse:
        return response("v2_season_players", '{"data":[]}', {"data": []})

    def close(self) -> None:
        return None


class FakeHTTP429Session:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float):
        del url, timeout
        return type("Response", (), {"status_code": 429})()

    def close(self) -> None:
        return None


def response(endpoint: str, text: str, payload) -> APIResponse:
    return APIResponse(
        endpoint=endpoint, url=f"https://official.test/{endpoint}", status_code=200,
        content_type="application/json", fetched_at_utc=datetime.now(UTC).isoformat(),
        text=text, payload=payload,
    )


def seed_minimal_database(database: Path) -> tuple[str, str, str]:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    run_id = "seed-run"
    competition_id = stable_id("competition", "E")
    season_id = stable_id("season", "E", "E2026")
    team_id = stable_id("team", "euroleague", "AAA")
    away_id = stable_id("team", "euroleague", "BBB")
    player_id = stable_id("player", "euroleague", "P1")
    game_id = stable_id("game", "E", "E2026", 1)
    with connect_database(database) as connection:
        connection.execute(
            "INSERT INTO ingestion_runs (run_id, source, command, started_at, completed_at, status) "
            "VALUES (?, 'test', 'test', ?, ?, 'SUCCEEDED')", [run_id, now, now]
        )
        connection.execute(
            "INSERT INTO competitions VALUES (?, 'E', 'EuroLeague')",
            [competition_id],
        )
        connection.execute(
            "INSERT INTO seasons VALUES (?, ?, 'E2026', '2026-27', 2026, NULL, NULL)",
            [season_id, competition_id],
        )
        connection.execute(
            "INSERT INTO teams VALUES (?, 'AAA', 'Alpha', NULL, 'test')", [team_id]
        )
        connection.execute(
            "INSERT INTO teams VALUES (?, 'BBB', 'Beta', NULL, 'test')", [away_id]
        )
        connection.execute(
            "INSERT INTO players VALUES (?, 'P1', 'Test Player', NULL, NULL, NULL, NULL, NULL, 'test')",
            [player_id],
        )
        connection.execute(
            """
            INSERT INTO player_aliases VALUES (
              ?, ?, 'euroleague', 'Test Player', 'test player', 'E2026',
              NULL, NULL, 'TEST', true
            )
            """,
            [stable_id("alias", player_id), player_id],
        )
        # Games require source provenance, so seed one zero-byte retained artifact.
        raw = Path(database).with_suffix(".raw")
        raw.write_bytes(b"seed")
        digest = __import__("hashlib").sha256(b"seed").hexdigest()
        artifact_id = stable_id("artifact", database)
        connection.execute(
            """
            INSERT INTO raw_artifacts VALUES (
              ?, 'test', 'test', NULL, 'E', 'E2026', 1, NULL, ?, ?, ?, ?, 4,
              'application/octet-stream', false, false, ?
            )
            """,
            [artifact_id, now, now, str(raw), digest, run_id],
        )
        connection.execute(
            """
            INSERT INTO games (
              canonical_game_id, season_id, competition_code, season_code, game_code,
              round_number, game_date, home_team_id, away_team_id, game_status,
              played, source_artifact_id, ingestion_run_id
            ) VALUES (?, ?, 'E', 'E2026', 1, 1, ?, ?, ?, 'Confirmed', false, ?, ?)
            """,
            [game_id, season_id, datetime(2026, 10, 1, 18, tzinfo=UTC),
             team_id, away_id, artifact_id, run_id],
        )
    return player_id, team_id, game_id


if __name__ == "__main__":
    unittest.main()
