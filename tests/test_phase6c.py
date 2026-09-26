from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb
import numpy as np

from src.db.database import connect_database, initialize_database
from src.live.prediction import run_live_prediction
from src.live.prediction_artifacts import ModelArtifactMismatch
from src.live.predictive_uplift_artifacts import (
    DEFAULT_PHASE6C_ARTIFACT_ROOT,
    load_and_validate_predictive_uplift_artifact,
)
from src.live.probabilistic_artifacts import load_and_validate_probabilistic_artifact
from src.modeling.phase6c_features import calculate_usage_percent
from src.modeling.phase6c_protocol import (
    DOWNSIDE_THRESHOLDS,
    FAMILY_ADDITIONS,
    PHASE6C_CALIBRATION_VERSION,
    PHASE6C_FORBIDDEN_INPUTS,
    PHASE6C_MODEL_VERSION,
    expected_usage_feature_manifest,
    phase6c_feature_manifest,
)


RESULTS_PATH = Path("data/samples/phase6c/results.json")
FEATURE_PATH = Path("data/derived/phase6c/research/feature_frame.parquet")
PREDICTION_PATH = Path("data/derived/phase6c/research/outer_predictions.parquet")


class Phase6CProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = json.loads(RESULTS_PATH.read_text())

    def test_usage_formula_matches_the_frozen_definition(self) -> None:
        value = calculate_usage_percent(
            fga=10, fta=4, turnovers=2, minutes=20,
            team_fga=60, team_fta=20, team_turnovers=12,
            team_player_minutes=200,
        )
        self.assertAlmostEqual(value, 34.05940594059406)

    def test_all_required_families_were_independently_ablated(self) -> None:
        names = {row["name"] for row in self.results["family_ablations"]}
        self.assertEqual(len(FAMILY_ADDITIONS), 10)
        self.assertTrue(set(FAMILY_ADDITIONS).issubset(names))
        self.assertEqual(
            self.results["retained_families"], ["usage_offensive_involvement"]
        )

    def test_manifests_exclude_target_game_information(self) -> None:
        manifests = [
            expected_usage_feature_manifest(),
            *[phase6c_feature_manifest(name) for name in FAMILY_ADDITIONS],
        ]
        for manifest in manifests:
            self.assertFalse(set(manifest["features"]) & PHASE6C_FORBIDDEN_INPUTS)
        self.assertNotIn("actual_usage_next_game", phase6c_feature_manifest(
            "expected_next_game_usage"
        )["features"])

    def test_feature_sources_are_strictly_before_each_cutoff(self) -> None:
        with duckdb.connect() as connection:
            row = connection.execute(
                """
                SELECT count(*) AS rows,
                       count(*) FILTER (WHERE target_minutes <= 0) AS zero_minutes,
                       count(*) FILTER (
                         WHERE p6c_player_source_max_game_time >= feature_cutoff_time
                            OR p6c_opponent_source_max_game_time >= feature_cutoff_time
                            OR p6c_role_matchup_source_max_game_time >= feature_cutoff_time
                       ) AS cutoff_violations,
                       count(DISTINCT model_row_id) AS distinct_rows
                FROM read_parquet(?)
                """,
                [str(FEATURE_PATH)],
            ).fetchone()
        self.assertEqual(row, (29232, 0, 0, 29232))

    def test_outer_selection_and_usage_predictions_never_see_test_rows(self) -> None:
        self.assertEqual(self.results["dataset"]["outer_rows"], 22399)
        for ablation in self.results["family_ablations"]:
            for audit in ablation["fold_audits"]:
                self.assertEqual(audit["outer_test_rows_seen_during_selection"], 0)
                self.assertLess(audit["outer_train_max_time"], audit["outer_test_min_time"])
        for audit in self.results["expected_usage_model"]["walk_forward_metrics"]:
            self.assertEqual(audit["outer_rows_seen"], 0)
            self.assertLess(audit["train_max_time"], audit["test_min_time"])

    def test_usage_model_reports_a_finite_leakage_safe_baseline(self) -> None:
        usage = self.results["expected_usage_model"]
        self.assertTrue(np.isfinite(usage["last5_usage_baseline_metrics"]["mae"]))
        self.assertTrue(np.isfinite(usage["pooled_outer_metrics"]["mae"]))
        self.assertLess(
            usage["pooled_outer_metrics"]["mae"],
            usage["last5_usage_baseline_metrics"]["mae"],
        )
        self.assertGreater(usage["oof_rows"], 22000)

    def test_frozen_gate_improves_all_outer_fold_maes(self) -> None:
        self.assertEqual(self.results["status"], "FROZEN")
        self.assertTrue(self.results["phase6c_accepted"])
        for values in self.results["central_comparison"]["by_fold"].values():
            self.assertLess(values["phase6c"]["mae"], values["phase6b"]["mae"])
        self.assertLess(
            self.results["central_comparison"]["phase6c"]["rmse"],
            self.results["central_comparison"]["phase6b"]["rmse"],
        )

    def test_downside_probabilities_are_present_and_ordered(self) -> None:
        with duckdb.connect() as connection:
            frame = connection.execute(
                "SELECT prob_fp_le_5,prob_fp_le_10,prob_fp_le_15 "
                "FROM read_parquet(?)",
                [str(PREDICTION_PATH)],
            ).df()
        self.assertTrue(np.isfinite(frame.to_numpy(float)).all())
        self.assertTrue((frame.prob_fp_le_5 <= frame.prob_fp_le_10).all())
        self.assertTrue((frame.prob_fp_le_10 <= frame.prob_fp_le_15).all())
        self.assertEqual(tuple(DOWNSIDE_THRESHOLDS), (5.0, 10.0, 15.0))


class Phase6CArtifactTests(unittest.TestCase):
    def test_real_bundle_loads_with_strict_hash_validation(self) -> None:
        bundle, validation = load_and_validate_predictive_uplift_artifact()
        self.assertEqual(validation.predictive_model_version, PHASE6C_MODEL_VERSION)
        self.assertEqual(validation.calibration_version, PHASE6C_CALIBRATION_VERSION)
        self.assertEqual(bundle.payload["retained_families"], [
            "usage_offensive_involvement"
        ])
        self.assertEqual(len(validation.artifact_fingerprint), 64)

    def test_repeated_inference_is_identical_and_probabilities_are_valid(self) -> None:
        with duckdb.connect() as connection:
            frame = connection.execute(
                "SELECT * FROM read_parquet(?) ORDER BY model_row_id LIMIT 8",
                [str(FEATURE_PATH)],
            ).df()
        probabilistic, _ = load_and_validate_probabilistic_artifact()
        phase6b = probabilistic.predict_distribution(frame)
        bundle, _ = load_and_validate_predictive_uplift_artifact()
        first, _ = bundle.predict_distribution(frame, phase6b)
        second, _ = bundle.predict_distribution(frame.copy(), phase6b.copy())
        columns = [
            "phase6c_expected_fp", "expected_usage_next_game",
            "prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15",
        ]
        np.testing.assert_array_equal(first[columns].to_numpy(), second[columns].to_numpy())
        self.assertTrue(((first[["prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15"]]
                          .to_numpy(float) >= 0) &
                         (first[["prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15"]]
                          .to_numpy(float) <= 1)).all())

    def test_missing_and_corrupted_bundles_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaisesRegex(ModelArtifactMismatch, "Phase 6C"):
                load_and_validate_predictive_uplift_artifact(missing)
            copied = Path(directory) / "phase6c"
            shutil.copytree(DEFAULT_PHASE6C_ARTIFACT_ROOT, copied)
            with (copied / "expected_usage.json").open("ab") as handle:
                handle.write(b"corruption")
            with self.assertRaisesRegex(ModelArtifactMismatch, "fingerprint mismatch"):
                load_and_validate_predictive_uplift_artifact(copied)


class Phase6CLiveIntegrationTests(unittest.TestCase):
    def test_predict_live_preflights_phase6c_even_without_a_market(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            initialize_database(database)
            with patch(
                "src.live.prediction.load_and_validate_predictive_uplift_artifact",
                wraps=load_and_validate_predictive_uplift_artifact,
            ) as loader:
                result = run_live_prediction(
                    "E2026", database_path=database,
                    cutoff=datetime(2026, 8, 20, 9, tzinfo=UTC),
                )
            loader.assert_called_once()
            self.assertEqual(result.status, "NO_CURRENT_FANTASY_SLATE")
            self.assertEqual(result.predictive_model_version, PHASE6C_MODEL_VERSION)
            with connect_database(database, read_only=True) as connection:
                stored = connection.execute(
                    "SELECT predictive_model_version,predictive_calibration_version,"
                    "predictive_feature_manifest_fingerprint "
                    "FROM live_prediction_runs"
                ).fetchone()
            self.assertEqual(stored[:2], (
                PHASE6C_MODEL_VERSION, PHASE6C_CALIBRATION_VERSION,
            ))
            self.assertEqual(len(stored[2]), 64)

    def test_missing_phase6c_bundle_makes_live_prediction_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            initialize_database(database)
            result = run_live_prediction(
                "E2026", database_path=database,
                phase6c_artifact_root=Path(directory) / "missing",
                cutoff=datetime(2026, 8, 20, 9, tzinfo=UTC),
            )
            self.assertEqual(result.status, "MODEL_ARTIFACT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
