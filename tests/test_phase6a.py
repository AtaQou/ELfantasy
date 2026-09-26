from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.db.database import connect_database, initialize_database
from src.db.ids import stable_id
from src.db.integrity import validate_database
from src.live.current_features import frozen_required_features
from src.live.phase5b import generate_availability_scenarios, redistribute_live_team
from src.live.prediction import (
    DecisionConfig,
    LivePredictionResult,
    MatchdayResolution,
    _feature_fingerprint,
    build_prediction_scenarios,
    load_decision_file,
    resolve_fantasy_matchday,
    run_live_prediction,
    store_prediction_run,
)
from src.live.prediction_artifacts import (
    ArtifactValidation,
    ModelArtifactMismatch,
    _validate_phase4b,
    validate_redistribution_artifact,
)
from src.live.probabilistic_artifacts import ProbabilisticArtifactValidation
from src.live.rehearsal import run_historical_rehearsal
from src.modeling.phase5b_protocol import PHASE5B_PROTOCOL_VERSION
from tests.test_phase5b import synthetic_team
from tests.test_phase5a import seed_minimal_database


class Phase6ADecisionTests(unittest.TestCase):
    def test_json_decision_file_supports_all_states_roles_and_scenarios(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps({
                "players": {
                    "P1": {"decision": "OUT", "manual_expected_minutes_if_available": 24},
                    "P2": "PLAY", "P3": "UNKNOWN", "P4": "LIMITED",
                },
                "scenarios": {"late_news": {"P3": "OUT"}},
                "default_unknown_decision": "PLAY",
            }))
            result = load_decision_file(path)
        self.assertEqual(result.player_decisions["P1"], "OUT")
        self.assertEqual(result.manual_role_estimates["P1"], 24)
        self.assertEqual(result.scenarios[0][0], "late_news")
        self.assertEqual(result.default_unknown_decision, "PLAY")

    def test_invalid_decision_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"players":{"P1":"MAYBE"}}')
            with self.assertRaises(ValueError):
                load_decision_file(path)

    def test_invalid_manual_role_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                '{"players":{"P1":{"decision":"OUT",'
                '"manual_expected_minutes_if_available":41}}}'
            )
            with self.assertRaises(ValueError):
                load_decision_file(path)

    def test_explicit_scenario_names_are_preserved(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "AVAILABLE"
        frame.loc[:1, "resolved_availability_status"] = "UNKNOWN"
        config = DecisionConfig(
            {}, (("one", {"P0": "PLAY", "P1": "OUT"}),
                 ("two", {"P0": "OUT", "P1": "OUT"})),
            {}, None, "x",
        )
        scenarios = build_prediction_scenarios(frame, config)
        self.assertEqual([row.scenario_name for row in scenarios], ["one", "two"])
        self.assertTrue(all(not row.unresolved_players for row in scenarios))

    def test_default_unknown_is_explicit_and_avoids_explosion(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "UNKNOWN"
        config = DecisionConfig({}, (), {}, "PLAY", "x")
        scenarios = build_prediction_scenarios(frame, config)
        self.assertEqual(len(scenarios), 1)
        self.assertTrue(all(value == "PLAY" for value in scenarios[0].decisions.values()))

    def test_questionable_play_choice_does_not_reduce_minutes(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "AVAILABLE"
        frame.loc[0, "resolved_availability_status"] = "QUESTIONABLE"
        scenario = generate_availability_scenarios(
            frame, user_decisions={"P0": "PLAY"}
        )[0]
        output = redistribute_live_team(frame, scenario.decisions)
        self.assertAlmostEqual(
            output.loc[output.player_id.eq("P0"), "adjusted_expected_minutes"].item(),
            output.loc[output.player_id.eq("P0"), "baseline_expected_minutes"].item(),
        )


class Phase6AArtifactTests(unittest.TestCase):
    def test_frozen_phase5b_model_and_manifest_fingerprint_validate(self) -> None:
        fingerprint = validate_redistribution_artifact()
        self.assertEqual(len(fingerprint), 64)

    def test_missing_phase4b_deployment_bundle_fails_closed(self) -> None:
        with TemporaryDirectory() as directory, self.assertRaises(ModelArtifactMismatch):
            _validate_phase4b(Path(directory))

    def test_wrong_hybrid_weight_fails_before_inference(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({
                "status": "FROZEN",
                "performance_model_version": "phase4b_conditional_performance_frozen_v1",
                "protocol_version": "phase4b_conditional_performance_v1",
                "hybrid": {"weight_direct": 0.5, "weight_decomposed": 0.5},
            }))
            with self.assertRaisesRegex(ModelArtifactMismatch, "direct weight"):
                _validate_phase4b(root)

    def test_feature_manifest_fingerprint_is_repeatable(self) -> None:
        self.assertEqual(_feature_fingerprint(), _feature_fingerprint())
        self.assertEqual(len(_feature_fingerprint()), 64)

    def test_phase5b_protocol_is_the_frozen_version(self) -> None:
        self.assertEqual(PHASE5B_PROTOCOL_VERSION, "phase5b_known_absence_redistribution_v1")


class Phase6AMatchdayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.duckdb"
        initialize_database(self.database)
        self._seed_market(matchdays=[4])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_market(self, matchdays: list[int]) -> None:
        now = datetime(2026, 9, 1, tzinfo=UTC)
        with connect_database(self.database) as connection:
            connection.execute(
                "INSERT INTO ingestion_runs (run_id,source,started_at,status) "
                "VALUES ('run','test',?,'SUCCEEDED')", [now]
            )
            raw = Path(self.temp.name) / "raw"
            raw.write_bytes(b"x")
            connection.execute(
                """
                INSERT INTO raw_artifacts (
                  artifact_id,source,source_endpoint,stored_at,raw_path,
                  content_sha256,byte_count,is_sanitized,auth_data_included,ingestion_run_id
                ) VALUES ('raw','test','test',?,?,'x',1,false,false,'run')
                """, [now, str(raw)]
            )
            for index, matchday in enumerate(matchdays):
                entity = f"entity-{index}"
                connection.execute(
                    "INSERT INTO fantasy_entities VALUES (?, 'fantasy', ?, 'PLAYER', ?, ?, ?)",
                    [entity, str(index), f"Player {index}", now, now],
                )
                connection.execute(
                    """
                    INSERT INTO fantasy_market_snapshots (
                      snapshot_record_id,snapshot_batch_id,observed_at,season_code,
                      matchday_id,matchday_number,fantasy_entity_id,credits,
                      status_valid_as_of_matchday,status_semantics,position_semantics,
                      price_semantics,source_artifact_id,ingestion_run_id
                    ) VALUES (?, 'batch', ?, 'E2026', ?, ?, ?, 10, false,
                      'OBSERVED_AT_COLLECTION_ONLY','CURRENT','CURRENT','raw','run')
                    """, [f"snapshot-{index}", now, matchday, matchday, entity],
                )

    def test_latest_market_without_mapping_is_ambiguous(self) -> None:
        slate = pd.DataFrame({"round_number": [4], "team_id": ["A"],
                              "opponent_team_id": ["B"]})
        result = resolve_fantasy_matchday("E2026", slate, database_path=self.database)
        self.assertEqual(result.status, "MATCHDAY_AMBIGUOUS")

    def test_validated_direct_mapping_resolves(self) -> None:
        with connect_database(self.database) as connection:
            connection.execute(
                """
                INSERT INTO fantasy_matchday_round_mapping VALUES (
                  'map','E2026',4,4,'VALIDATED_DIRECT',NULL,'raw','run'
                )
                """
            )
        slate = pd.DataFrame({"round_number": [4], "team_id": ["A"],
                              "opponent_team_id": ["B"]})
        result = resolve_fantasy_matchday("E2026", slate, database_path=self.database)
        self.assertEqual(result.status, "MATCHDAY_RESOLVED")
        self.assertEqual(result.fantasy_matchday, 4)

    def test_absent_market_is_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "empty.duckdb"
            initialize_database(database)
            result = resolve_fantasy_matchday(
                "E2026", pd.DataFrame(), database_path=database
            )
        self.assertEqual(result.status, "MARKET_NOT_AVAILABLE")


class Phase6AStorageAndRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.duckdb"
        initialize_database(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_empty_current_market_returns_explicit_non_success_state(self) -> None:
        result = run_live_prediction(
            "E2026", database_path=self.database,
            cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
            output_root=Path(self.temp.name) / "outputs",
        )
        self.assertEqual(result.status, "NO_CURRENT_FANTASY_SLATE")
        self.assertEqual(result.fantasy_matchday_status, "MARKET_NOT_AVAILABLE")
        self.assertEqual(result.coverage["players_in_fantasy_market"], 0)

    def test_naive_cutoff_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            run_live_prediction(
                "E2026", database_path=self.database,
                cutoff=datetime(2026, 8, 19, 9),
            )

    def test_no_refresh_does_not_call_phase5a_updater(self) -> None:
        with patch("src.live.prediction.update_live") as updater:
            run_live_prediction(
                "E2026", database_path=self.database, refresh=False,
                cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
            )
        updater.assert_not_called()

    def test_refresh_calls_phase5a_updater_first(self) -> None:
        refreshed = SimpleNamespace(live_run_id=None, warnings=())
        with patch("src.live.prediction.update_live", return_value=refreshed) as updater:
            run_live_prediction(
                "E2026", database_path=self.database, refresh=True,
                cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
            )
        updater.assert_called_once()

    def test_identical_terminal_rerun_reuses_one_immutable_row(self) -> None:
        cutoff = datetime(2026, 8, 19, 9, tzinfo=UTC)
        first = run_live_prediction(
            "E2026", database_path=self.database, cutoff=cutoff
        )
        second = run_live_prediction(
            "E2026", database_path=self.database, cutoff=cutoff
        )
        self.assertEqual(first.prediction_run_id, second.prediction_run_id)
        self.assertTrue(second.reused_immutable_snapshot)
        with connect_database(self.database, read_only=True) as connection:
            count = connection.execute(
                "SELECT count(*) FROM live_prediction_runs"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_terminal_snapshot_stores_complete_audit_state(self) -> None:
        result = run_live_prediction(
            "E2026", database_path=self.database,
            cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
        )
        with connect_database(self.database, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT input_fingerprint,run_fingerprint,feature_manifest_fingerprint,
                       availability_state_json,scenario_definitions_json,
                       source_freshness_json,created_before_outcomes
                FROM live_prediction_runs WHERE prediction_run_id=?
                """, [result.prediction_run_id],
            ).fetchone()
        self.assertTrue(all(row[:6]))
        self.assertTrue(row[6])

    def test_phase6a_integrity_checks_exist_and_pass_on_empty_snapshot(self) -> None:
        run_live_prediction(
            "E2026", database_path=self.database,
            cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
        )
        integrity = validate_database(self.database)
        phase6 = [row for row in integrity["checks"] if row["name"].startswith("live_prediction")]
        self.assertGreaterEqual(len(phase6), 9)
        self.assertTrue(all(row["passed"] for row in phase6), phase6)

    def test_migrations_include_phase6a_schema(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            self.assertGreaterEqual(connection.execute(
                "SELECT max(version) FROM schema_migrations"
            ).fetchone()[0], 6)
            tables = {row[0] for row in connection.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()}
        self.assertIn("live_prediction_runs", tables)
        self.assertIn("live_player_predictions", tables)
        self.assertIn("live_prediction_outcomes", tables)

    def test_phase6a_output_schema_uses_intervals_not_ceiling(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            columns = {row[0] for row in connection.execute(
                "DESCRIBE live_player_predictions"
            ).fetchall()}
        self.assertIn("lower_prediction_interval", columns)
        self.assertIn("upper_prediction_interval", columns)
        self.assertNotIn("ceiling", columns)
        self.assertNotIn("ceiling_fp", columns)

    def test_generated_player_snapshot_is_append_only_and_schema_complete(self) -> None:
        player_id, team_id, game_id = seed_minimal_database(self.database)
        with connect_database(self.database) as connection:
            opponent_id = connection.execute(
                "SELECT away_team_id FROM games WHERE canonical_game_id=?", [game_id]
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO fantasy_entities VALUES "
                "('fantasy-entity','fantasy','99','PLAYER','Test Player',?,?)",
                [datetime(2026, 9, 1, tzinfo=UTC)] * 2,
            )
        generated = datetime(2026, 9, 1, tzinfo=UTC)
        result = LivePredictionResult(
            "prediction-run", "SUCCEEDED", "E2026", generated,
            "MATCHDAY_RESOLVED", 1, 1, {"players_scored": 1},
            {"total_seconds": 1.0}, (), "predictions.json", "predictions.csv",
        )
        resolution = MatchdayResolution(
            "MATCHDAY_RESOLVED", 1, 1, "VALIDATED_DIRECT_MAPPING",
            None, 1, "market-fingerprint", "resolved",
        )
        validation = ArtifactValidation(
            "phase4b_conditional_performance_frozen_v1",
            "phase5b_known_absence_redistribution_frozen_v1",
            "p" * 64, "r" * 64, "f" * 64,
        )
        probabilistic_validation = ProbabilisticArtifactValidation(
            "phase6b_probabilistic_player_outcome_frozen_v1",
            "phase6b_chronological_calibration_v1",
            "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64,
        )
        output = pd.DataFrame([{
            "scenario_id": "resolved", "fantasy_matchday": 1,
            "game_id": game_id, "game_date": datetime(2026, 10, 1).date(),
            "scheduled_tip_time": datetime(2026, 10, 1, 18, tzinfo=UTC),
            "player_id": player_id, "fantasy_id": "fantasy-entity",
            "fantasy_player_id": "99", "player_name": "Test Player",
            "team_id": team_id, "opponent_team_id": opponent_id,
            "home_away": "home", "fantasy_position": "Guard", "credits": 10.0,
            "source_availability_status": "AVAILABLE",
            "resolved_availability": "PLAY",
            "availability_decision_source": "SOURCE_DEFAULT",
            "baseline_expected_minutes": 20.0, "adjusted_expected_minutes": 20.0,
            "minutes_delta_due_to_absences": 0.0, "direct_prediction": 15.0,
            "expected_fp_per_min": 0.75, "expected_fp_before_adjustment": 15.0,
            "expected_fp": 15.0, "fp_delta_due_to_absences": 0.0,
            "phase4b_central_fp_before_adjustment": 14.5,
            "phase4b_central_fp": 14.5, "median_fp": 14.0,
            "p10_fp": 5.0, "p25_fp": 9.0, "p50_fp": 14.0,
            "p75_fp": 19.0, "p90_fp": 24.0, "p95_fp": 28.0,
            "prob_fp_ge_20": 0.22, "prob_fp_ge_25": 0.10,
            "prob_fp_ge_30": 0.04, "prob_fp_ge_35": 0.015,
            "prob_fp_ge_40": 0.005, "distribution_width": 19.0,
            "p90_minus_p50": 10.0, "p50_minus_p10": 9.0,
            "p95_minus_expected": 13.0, "history_sample_count": 0,
            "distribution_confidence": "LOW",
            "lower_prediction_interval": np.nan, "upper_prediction_interval": np.nan,
            "interval_calibration_status":
                "NOT_RECALIBRATED_AFTER_AVAILABILITY_ADJUSTMENT",
            "career_el_games_before": 0, "season_games_before": 0,
            "cold_start_flag": True, "feature_completeness": 0.1,
            "freshness_status": "FRESH", "prediction_status": "SCORED",
            "missing_role_status": "NOT_APPLICABLE",
            "performance_model_version": validation.performance_model_version,
            "redistribution_model_version": validation.redistribution_model_version,
            "probabilistic_model_version": (
                probabilistic_validation.probabilistic_model_version
            ),
            "calibration_version": probabilistic_validation.calibration_version,
            "probabilistic_artifact_fingerprint": (
                probabilistic_validation.artifact_fingerprint
            ),
            "feature_snapshot_fingerprint": "s" * 64,
        }])
        definitions = [{
            "scenario_id": "resolved", "decisions": {player_id: "PLAY"},
            "manual_role_estimates": {},
        }]
        summaries = {"resolved": {
            f"{game_id}:{team_id}": {"adjusted_team_minutes": 200.0}
        }}
        from src.live.prediction import _insert_prediction_row

        def interrupted_insert(*args):
            _insert_prediction_row(*args)
            raise RuntimeError("interrupted after a player write")

        with patch("src.live.prediction._insert_prediction_row", side_effect=interrupted_insert):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                store_prediction_run(
                    result, database_path=self.database, resolution=resolution,
                    input_fingerprint="i" * 64, run_fingerprint="u" * 64,
                    slate_run_id=None, live_run_id=None, refresh_requested=False,
                    validation=validation, availability_state={"players": []},
                    probabilistic_validation=probabilistic_validation,
                    scenario_definitions=definitions, freshness={}, output=output,
                    scenario_summaries=summaries,
                )
        with connect_database(self.database, read_only=True) as connection:
            self.assertEqual(connection.execute(
                "SELECT (SELECT count(*) FROM live_prediction_runs), "
                "(SELECT count(*) FROM live_prediction_scenarios), "
                "(SELECT count(*) FROM live_player_predictions)"
            ).fetchone(), (0, 0, 0))
        for _ in range(2):
            store_prediction_run(
                result, database_path=self.database, resolution=resolution,
                input_fingerprint="i" * 64, run_fingerprint="u" * 64,
                slate_run_id=None, live_run_id=None, refresh_requested=False,
                validation=validation, availability_state={"players": []},
                probabilistic_validation=probabilistic_validation,
                scenario_definitions=definitions, freshness={}, output=output,
                scenario_summaries=summaries,
            )
        with connect_database(self.database, read_only=True) as connection:
            counts = connection.execute(
                "SELECT (SELECT count(*) FROM live_prediction_runs), "
                "(SELECT count(*) FROM live_prediction_scenarios), "
                "(SELECT count(*) FROM live_player_predictions)"
            ).fetchone()
        self.assertEqual(counts, (1, 1, 1))


class Phase6AHistoricalRehearsalTests(unittest.TestCase):
    def test_e2025_rehearsal_is_reproducible_and_leakage_free(self) -> None:
        database = Path("data/db/euroleague.duckdb")
        if not database.is_file():
            self.skipTest("canonical database unavailable")
        first = run_historical_rehearsal(
            database_path=database, matchdays=(1, 2), output_path=None
        )
        second = run_historical_rehearsal(
            database_path=database, matchdays=(1, 2), output_path=None
        )
        self.assertTrue(first["successful"], first)
        self.assertFalse(first["target_game_information_used_as_features"])
        self.assertEqual(first["prediction_fingerprint"], second["prediction_fingerprint"])
        self.assertEqual(first["matchdays"], [1, 2])
        self.assertTrue(first["serialized_phase4b_bundle_used"])
        self.assertLessEqual(first["maximum_direct_component_difference"], 1e-12)
        self.assertLess(first["maximum_team_minute_reconciliation_error"], 1e-9)


if __name__ == "__main__":
    unittest.main()
