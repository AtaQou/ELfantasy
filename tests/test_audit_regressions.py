"""Small isolated regressions for the 2026-09-23 application audit."""

from datetime import UTC, datetime, timedelta
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from src.control_center.demo import DemoControlCenterService
from src.control_center.http import create_server
from src.control_center.service import (
    ControlCenterService, _automatic_availability_decision_payload,
    _prediction_failure_message,
)
from src.control_center.state import ControlCenterState
from src.control_center.repository import (
    ControlCenterRepository,
    recommendation_for_entity_roster,
)
from src.control_center.shadow import recommendation_for_lineup
from src.live.freshness import record_source_refresh, source_freshness
from src.db.database import initialize_database, connect_database
from src.live.current_season import (
    _upsert_mutable_game, _candidate_from_normalized, _insert_schedule_snapshot,
)
from tests.test_phase5a import seed_minimal_database
from scripts.optimize_fantasy import _load_live_pools


class AuditRegressions(unittest.TestCase):
    def test_partial_scored_prediction_can_be_current_without_global_production_flag(self):
        service = ControlCenterService.__new__(ControlCenterService)
        current, reasons = service._prediction_current(
            {
                "status": "PARTIAL", "production_ready": False,
                "predictive_model_version": "phase6c_predictive_uplift_frozen_v1",
                "overrides_newer_than_prediction": False,
                "source_freshness": {}, "scenarios": ["RESOLVED"],
                "market_snapshot_fingerprint": "market",
                "market_snapshot_batch_id": "batch",
            },
            ControlCenterState(
                season_code="E2026", fantasy_matchday=1, scenario_id="RESOLVED",
            ),
            {"fantasy_snapshot_batch_id": "batch"},
        )
        self.assertTrue(current, reasons)
        self.assertEqual(reasons, [])

    def test_unresolved_prediction_message_does_not_expose_player_ids(self):
        message = _prediction_failure_message({
            "status": "AVAILABILITY_UNRESOLVED",
            "error_message": "player-one,player-two",
        })
        self.assertEqual(message, "Availability remains unresolved for 2 players")

    def test_shadow_uses_the_recommendation_matching_the_applied_lineup(self):
        def recommendation(label, players, coach, sixth):
            return {
                "label": label,
                "players": [{"player_id": player} for player in players],
                "coach": {"coach_id": coach},
                "starting_five": players[:5],
                "sixth_man": sixth,
                "captain": players[0],
            }

        best = recommendation("BEST_OVERALL", [f"B{i}" for i in range(10)], "CB", "B5")
        safer = recommendation("SAFER", [f"S{i}" for i in range(10)], "CS", "S5")
        snapshot = {"recommendations": [best, safer]}
        selected = recommendation_for_lineup(snapshot, {
            "player_ids": [f"S{i}" for i in reversed(range(10))],
            "coach_id": "CS", "sixth_man": "S5",
        })
        self.assertEqual(selected["label"], "SAFER")
        self.assertEqual(recommendation_for_lineup(snapshot, None)["label"], "BEST_OVERALL")

    def test_live_reevaluation_uses_the_recommendation_owned_by_saved_team(self):
        def recommendation(label, prefix):
            players = [f"{prefix}{index}" for index in range(10)]
            return {
                "label": label,
                "players": [
                    {"player_id": player, "entity_id": f"E-{player}"}
                    for player in players
                ],
                "coach": {"coach_id": f"C-{prefix}", "entity_id": f"EC-{prefix}"},
                "starting_five": players[:5],
                "sixth_man": players[5],
                "captain": players[0],
            }

        best = recommendation("BEST_OVERALL", "B")
        owned = recommendation("SAFER_ALTERNATIVE", "S")
        saved_entities = tuple(
            [row["entity_id"] for row in owned["players"]]
            + [owned["coach"]["entity_id"]]
        )
        state = ControlCenterState(
            season_code="E2026", fantasy_matchday=1,
            roster_entity_ids=saved_entities,
        )

        class Repository:
            @staticmethod
            def load_state(profile_id):
                return state

            @staticmethod
            def latest_shadow_snapshot(profile_id, **filters):
                return {
                    "shadow_snapshot_id": "shadow",
                    "recommendations": [best, owned],
                }

        service = ControlCenterService.__new__(ControlCenterService)
        service.profile_id = "default"
        service.repository = Repository()
        service._state_with_matchday = lambda value: value
        captured = {}

        def attach(repository, shadow_snapshot_id, *, current_lineup=None):
            captured.update(current_lineup or {})
            return {"status": "NO_COMPLETED_TURN"}

        with patch("src.control_center.service.attach_completed_turn_results", attach):
            result = service.reevaluate_strategy(refresh_first=False)

        self.assertEqual(result["status"], "NO_COMPLETED_TURN")
        self.assertEqual(set(captured["player_ids"]), {f"S{index}" for index in range(10)})
        self.assertEqual(captured["coach_id"], "C-S")

    def test_shadow_roster_identity_uses_recommended_entities_not_optimizer_input(self):
        recommendation = {
            "players": [
                {"player_id": f"P{index}", "entity_id": f"OUTPUT-{index}"}
                for index in range(10)
            ],
            "coach": {"coach_id": "COACH", "entity_id": "OUTPUT-COACH"},
        }
        output_roster = tuple(
            [f"OUTPUT-{index}" for index in range(10)] + ["OUTPUT-COACH"]
        )
        optimizer_input = tuple(
            [f"INPUT-{index}" for index in range(10)] + ["INPUT-COACH"]
        )

        self.assertEqual(
            recommendation_for_entity_roster([recommendation], output_roster),
            recommendation,
        )
        self.assertIsNone(
            recommendation_for_entity_roster([recommendation], optimizer_input)
        )

    def test_automatic_availability_policy_is_team_independent(self):
        self.assertEqual(_automatic_availability_decision_payload(), {
            "players": {}, "default_unknown_decision": "PLAY",
        })

    def test_live_pool_uses_selected_prediction_run(self):
        class Connection:
            def __init__(self):
                self.sql = ""
                self.calls = []

            def execute(self, sql, parameters=None):
                self.sql = sql
                self.calls.append((sql, list(parameters or [])))
                return self

            def fetchone(self):
                return ("selected-run",)

            def fetchall(self):
                return [("base",)]

            def df(self):
                if "FROM live_player_predictions" in self.sql:
                    return pd.DataFrame([{
                        "player_id": "P1", "entity_id": "E1", "name": "Player",
                        "team_id": "T1", "game_id": "G1", "position": "Guard",
                        "credits": 8.0, "expected_fp": 20.0, "p10_fp": 10.0,
                        "p25_fp": 14.0, "p50_fp": 20.0, "p75_fp": 25.0,
                        "p90_fp": 30.0, "p95_fp": 34.0, "prob_fp_ge_20": .5,
                        "prob_fp_ge_25": .3, "prob_fp_ge_30": .15,
                        "prob_fp_ge_35": .08, "prob_fp_ge_40": .03,
                        "prob_fp_le_5": .03, "prob_fp_le_10": .1,
                        "prob_fp_le_15": .25, "predictive_artifact_fingerprint": "a",
                        "local_game_date": "2026-10-01T20:00:00",
                    }])
                return pd.DataFrame([{
                    "entity_id": "E1", "entity_type": "PLAYER", "name": "Player",
                    "team_id": "T1", "position": "Guard", "credits": 8.0,
                    "coach_id": "E1",
                }])

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        connection = Connection()
        with patch("scripts.optimize_fantasy.connect_database", return_value=connection):
            players, _ = _load_live_pools(
                "E2026", 1, Path("unused.duckdb"), "base", "selected-run"
            )
        run_sql, run_parameters = connection.calls[0]
        self.assertIn("AND prediction_run_id=?", run_sql)
        self.assertIn("scoring_rules_compatibility='SCORING_RULES_COMPATIBLE'", run_sql)
        self.assertIn("scored.prediction_status='SCORED'", run_sql)
        self.assertEqual(run_parameters, ["E2026", 1, "selected-run"])
        self.assertEqual(players.iloc[0]["turn"], 1)

    def test_http_serializes_overlapping_database_requests(self):
        class Service(DemoControlCenterService):
            def __init__(self):
                self.active = 0
                self.maximum_active = 0
                self.guard = threading.Lock()
                super().__init__()

            def _result(self):
                with self.guard:
                    self.active += 1
                    self.maximum_active = max(self.maximum_active, self.active)
                time.sleep(0.08)
                with self.guard:
                    self.active -= 1
                return {"ok": True}

            def bootstrap(self):
                return self._result()

            def save_team(self, payload):
                return self._result()

        service = Service()
        server = create_server(service, port=0)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        barrier = threading.Barrier(3)
        statuses = []

        def request(method, path, body=None):
            barrier.wait()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request(method, path, body=body)
                response = connection.getresponse()
                response.read()
                statuses.append(response.status)
            finally:
                connection.close()

        readers = [
            threading.Thread(target=request, args=("GET", "/api/bootstrap")),
            threading.Thread(target=request, args=("POST", "/api/team", b"{}")),
        ]
        try:
            for thread in readers:
                thread.start()
            barrier.wait()
            for thread in readers:
                thread.join(timeout=3)
            self.assertEqual(statuses, [200, 200])
            self.assertEqual(service.maximum_active, 1)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)

    def test_player_slate_exposes_recent_five_evidence(self):
        context_options = []

        def prediction_context(*args, **kwargs):
            context_options.append(kwargs)
            return {"prediction_run_id": "run", "scenarios": ["base"]}

        service = ControlCenterService.__new__(ControlCenterService)
        service.profile_id = "audit"
        service.repository = SimpleNamespace(
            prediction_context=prediction_context,
            player_predictions=lambda *args: [{
                "entity_id": "E1", "player_id": "P1", "expected_fp": 20.0,
                "expected_minutes": 25.0, "availability": "AVAILABLE",
            }],
            market_entities=lambda *args: [{
                "entity_id": "E1", "entity_type": "PLAYER", "player_id": "P1",
                "name": "Recent Player", "team_name": "Alpha", "position": "Guard",
                "credits": 8.0, "source_turn": 1,
            }],
            player_recent_form=lambda ids: {"P1": {
                "last5_fp_average": 18.5, "last5_minutes_median": 24.0,
                "last_game_fp": 22.0, "last_game_minutes": 27.0,
                "season_fp_average": 16.0, "season_minutes_average": 23.0,
                "season_code": "E2025",
                "last5_games": [{"game_id": "G1", "fantasy_points": 18, "minutes": 24}],
            }},
        )
        service._enrich_price_intelligence = lambda rows, *args: rows
        rows = service.players(state=ControlCenterState(
            season_code="E2026", fantasy_matchday=1, scenario_id="base",
        ))
        self.assertEqual(rows[0]["last5_fp_average"], 18.5)
        self.assertEqual(rows[0]["last5_minutes_median"], 24.0)
        self.assertEqual(rows[0]["last_game_fp"], 22.0)
        self.assertEqual(rows[0]["last_game_minutes"], 27.0)
        self.assertEqual(rows[0]["season_fp_average"], 16.0)
        self.assertEqual(rows[0]["season_minutes_average"], 23.0)
        self.assertEqual(rows[0]["stats_season_code"], "E2025")
        self.assertEqual(rows[0]["last5_games"][0]["game_id"], "G1")
        self.assertEqual(context_options, [{"require_scored": True}])

    def test_saved_recommendation_restores_full_contract_and_rejects_tampering(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ControlCenterRepository(root / "recommendation.duckdb")
            payload = {
                "profile_id": "audit:team-2", "season": "E2026", "matchday": 1,
                "status": "SUCCEEDED", "optimization_mode": "COMPLETE_ROSTER",
                "snapshot": {"ruleset_version": "test", "rules_fingerprint": "r",
                             "manual_override_fingerprint": "o",
                             "market_fingerprint": "m", "prediction_fingerprint": "p"},
                "current_roster": ["owned"], "bank_credits": 25., "transfers_available": 3,
                "constraints": {"owned": "FORCE_INCLUDE"}, "recommendations": [],
                "strategy": {"turns": [1, 2]}, "input_fingerprint": "input",
            }
            path = root / "snapshot.json"
            path.write_text(json.dumps(payload))
            repository.persist_run(payload, snapshot_path=path)
            restored = repository.latest_run("audit:team-2")
            self.assertEqual(restored["strategy"], payload["strategy"])
            self.assertEqual(restored["optimization_mode"], "COMPLETE_ROSTER")
            self.assertEqual(restored["snapshot"]["prediction_fingerprint"], "p")
            self.assertNotIn("restored_without_snapshot", restored)
            path.write_text(json.dumps({**payload, "bank_credits": 99}))
            restored = repository.latest_run("audit:team-2")
            self.assertTrue(restored["restored_without_snapshot"])
            self.assertEqual(restored["bank_credits"], 25.)
            self.assertIsNone(repository.latest_run("audit:team-3"))

    def test_referenced_game_accepts_final_score_without_replacing_parent(self):
        """Final observations reach History without replacing referenced identities."""
        with TemporaryDirectory() as directory:
            database = Path(directory) / "game.duckdb"
            initialize_database(database)
            _, team_id, game_id = seed_minimal_database(database)
            with connect_database(database) as connection:
                season_id, artifact, away_id = connection.execute(
                    "SELECT season_id,source_artifact_id,away_team_id FROM games"
                ).fetchone()
                # A child outside the former three-table check must also be protected.
                connection.execute(
                    "INSERT INTO absence_scenarios (absence_scenario_id,season_code,"
                    "canonical_game_id,scenario_name,scenario_status,created_at,"
                    "feature_cutoff_time,decisions_json,unresolved_players_json,"
                    "reconciliation_method,input_fingerprint) "
                    "VALUES ('audit','E2026',?,'audit','READY',current_timestamp,"
                    "current_timestamp,'{}','[]','audit','audit')", [game_id],
                )
                ingestor = SimpleNamespace(
                    connection=connection, run_id="seed-run", updated=0,
                    ensure_team=lambda code, *args, **kwargs: team_id if code == "AAA" else away_id,
                )
                value = {
                    "season_code": "E2026", "game_code": 1,
                    "home_team_id": "AAA", "home_team_name": "Alpha",
                    "away_team_id": "BBB", "away_team_name": "Beta",
                    "round": 1, "utc_date": "2026-10-02T18:00:00Z",
                    "local_date": "2026-10-02T20:00:00", "played": True,
                    "game_status": "Played", "home_score": 88, "away_score": 80,
                }
                self.assertEqual(_upsert_mutable_game(ingestor, value, season_id, artifact), game_id)
                candidate = _candidate_from_normalized(
                    {"game_code": 1, "home_team_code": "AAA", "away_team_code": "BBB"},
                    value, game_id,
                )
                _insert_schedule_snapshot(
                    connection, candidate, "UPDATED_GAME", "final-score",
                    datetime(2026, 10, 2, 22, tzinfo=UTC), artifact,
                )
                self.assertEqual(connection.execute(
                    "SELECT played,home_score,away_score FROM current_games WHERE canonical_game_id=?",
                    [game_id],
                ).fetchone(), (True, 88, 80))
                self.assertEqual(connection.execute("SELECT count(*) FROM absence_scenarios").fetchone()[0], 1)
                count = ingestor.updated
                _upsert_mutable_game(ingestor, value, season_id, artifact)
                self.assertEqual(ingestor.updated, count)
            rows = ControlCenterRepository(database).history_games({"season": "E2026"})
            self.assertEqual((rows[0]["played"], rows[0]["home_score"]), (True, 88))

    def test_refresh_reports_returned_prediction_status(self):
        state = ControlCenterState(fantasy_matchday=1)
        service = ControlCenterService.__new__(ControlCenterService)
        service._refresh_lock = threading.Lock()
        service.profile_id = "audit"
        service.database_path = Path("unused.duckdb")
        service.fantasy_config = None
        service.repository = SimpleNamespace(
            load_state=lambda _: state, attach_price_outcomes=lambda: 0,
        )
        service._state_with_matchday = lambda state: state
        service.update_runner = lambda *args, **kwargs: {"status": "SUCCEEDED", "stages": {}}
        service.dashboard = lambda *args: {
            "rules_gate": {"passed": True}, "scoring_gate": {"passed": True},
        }
        service.players = lambda **kwargs: []
        service._attach_all_shadows = lambda: []
        for status in ("SUCCEEDED", "PARTIAL", "AVAILABILITY_UNRESOLVED", "MODEL_ARTIFACT_MISMATCH", "FAILED"):
            with self.subTest(status=status):
                service.prediction_runner = lambda *args, **kwargs: {"status": status}
                result = service.refresh()
                self.assertEqual(result["status"], "SUCCEEDED" if status == "SUCCEEDED" else "PARTIAL")
                self.assertEqual(result["steps"][0]["status"], status)
                self.assertEqual(result["retryable"], status != "SUCCEEDED")
                self.assertFalse(service._refresh_lock.locked())

    def test_freshness_ignores_observations_after_cutoff(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "freshness.duckdb"
            cutoff = datetime(2026, 9, 23, 12, tzinfo=UTC)
            for instant, succeeded in ((cutoff - timedelta(hours=2), True),
                                       (cutoff + timedelta(hours=1), False)):
                record_source_refresh("schedule", "TEST", succeeded,
                                      database_path=database, captured_at=instant)
            actual = source_freshness(database, as_of=cutoff)["schedule"]
            self.assertEqual(actual.age_minutes, 120)
            self.assertTrue(actual.last_attempt_succeeded)
            self.assertLessEqual(actual.last_attempt_at, cutoff)

    def test_nonfinite_bank_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ControlCenterState(bank_credits=value).validated()
        self.assertEqual(ControlCenterState(bank_credits=0).validated().bank_credits, 0)

    def test_http_rejects_bad_lengths_nonfinite_json_and_unknown_api(self):
        server = create_server(DemoControlCenterService(), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method, path, body, headers, expected in (
                ("GET", "/api/does-not-exist", None, {}, 404),
                ("POST", "/api/team", b"", {"Content-Length": "-1"}, 400),
                ("POST", "/api/team", b'{"bank_credits": NaN}', {}, 400),
                ("POST", "/api/team", b'{"bank_credits": 1e999}', {}, 400),
                ("POST", "/api/team", b'[]', {}, 400),
                ("GET", "/api/bootstrap", None, {}, 200),
            ):
                with self.subTest(path=path, body=body):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    try:
                        connection.request(method, path, body=body, headers=headers)
                        response = connection.getresponse()
                        payload = json.loads(response.read())
                        self.assertEqual(response.status, expected, payload)
                        if expected != 200:
                            self.assertIn("error_code", payload)
                    finally:
                        connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
