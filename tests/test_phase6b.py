from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from src.db.database import connect_database, initialize_database
from src.live.prediction import run_live_prediction
from src.live.prediction_artifacts import ModelArtifactMismatch
from src.live.probabilistic_artifacts import (
    DEFAULT_PHASE6B_ARTIFACT_ROOT,
    load_and_validate_probabilistic_artifact,
)
from src.modeling.phase6b_protocol import (
    PHASE6B_CALIBRATION_VERSION,
    PHASE6B_MODEL_VERSION,
    THRESHOLDS,
    load_phase6b_frame,
    phase6b_folds,
    probability_violation_rate,
    quantile_crossing_rate,
    quantile_metrics,
    reconcile_distribution,
    reconcile_probabilities,
    reconcile_quantiles,
    retained_classifier_thresholds,
    threshold_base_rates,
    weighted_interval_score,
)


RESULTS_PATH = Path("data/derived/phase6b/research/results.json")


class Phase6BProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = load_phase6b_frame()

    def test_primary_dataset_is_verified_and_conditional_on_participation(self) -> None:
        self.assertEqual(len(self.frame), 29232)
        self.assertEqual(set(self.frame.season), {"E2022", "E2023", "E2024", "E2025"})
        self.assertTrue((self.frame.target_minutes > 0).all())
        self.assertTrue((self.frame.feature_cutoff_time <= self.frame.target_game_time).all())

    def test_outer_folds_are_strictly_chronological(self) -> None:
        folds = phase6b_folds(self.frame)
        self.assertEqual([fold["definition"].fold_id for fold in folds], [
            "outer_e2023", "outer_e2024", "outer_e2025",
        ])
        for fold in folds:
            train = self.frame.loc[fold["outer_train"]]
            test = self.frame.loc[fold["outer_test"]]
            self.assertLess(train.target_game_time.max(), test.target_game_time.min())

    def test_threshold_sample_gate_retains_20_to_35_but_not_40(self) -> None:
        rates = threshold_base_rates(self.frame)
        self.assertEqual(retained_classifier_thresholds(rates), (20.0, 25.0, 30.0, 35.0))
        forty = next(row for row in rates if row["threshold"] == 40.0)
        self.assertEqual(forty["positives"], 65)
        self.assertFalse(forty["direct_classifier_retained"])

    def test_quantile_metrics_use_pinball_and_empirical_coverage(self) -> None:
        actual = np.array([0.0, 1.0, 2.0, 3.0])
        metrics = quantile_metrics(actual, np.full(4, 2.0), 0.5)
        self.assertAlmostEqual(metrics["pinball_loss"], 0.5)
        self.assertAlmostEqual(metrics["empirical_coverage"], 0.75)

    def test_quantile_rearrangement_eliminates_crossing(self) -> None:
        raw = np.array([[5, 4, 3, 2, 1, 0], [0, 1, 2, 3, 4, 5]], dtype=float)
        self.assertEqual(quantile_crossing_rate(raw), 0.5)
        final = reconcile_quantiles(raw)
        self.assertEqual(quantile_crossing_rate(final), 0.0)

    def test_probability_projection_eliminates_order_violations(self) -> None:
        raw = np.array([[0.2, 0.4, 0.1, 0.3, 0.0]], dtype=float)
        self.assertEqual(probability_violation_rate(raw), 1.0)
        final = reconcile_probabilities(raw)
        self.assertEqual(probability_violation_rate(final), 0.0)
        self.assertTrue(((0 <= final) & (final <= 1)).all())

    def test_joint_distribution_is_coherent_and_has_finite_wis(self) -> None:
        quantiles = np.array([[2, 5, 10, 15, 20, 25]], dtype=float)
        probabilities = np.array([[0.2, 0.4, 0.3, 0.1, 0.05]], dtype=float)
        final_q, final_p = reconcile_distribution(quantiles, probabilities)
        self.assertEqual(quantile_crossing_rate(final_q), 0.0)
        self.assertEqual(probability_violation_rate(final_p), 0.0)
        self.assertTrue(np.isfinite(weighted_interval_score(np.array([12.0]), final_q)))


class Phase6BArtifactTests(unittest.TestCase):
    def test_real_frozen_bundle_loads_and_hashes_validate(self) -> None:
        bundle, validation = load_and_validate_probabilistic_artifact()
        self.assertEqual(validation.probabilistic_model_version, PHASE6B_MODEL_VERSION)
        self.assertEqual(validation.calibration_version, PHASE6B_CALIBRATION_VERSION)
        self.assertEqual(len(validation.artifact_fingerprint), 64)
        self.assertEqual(set(bundle.payload["classifier_models"]), {"20", "25", "30", "35"})

    def test_repeated_inference_is_numerically_identical_and_ordered(self) -> None:
        frame = load_phase6b_frame().tail(8)
        bundle, _ = load_and_validate_probabilistic_artifact()
        delta = np.linspace(-1.0, 2.0, len(frame))
        first = bundle.predict_distribution(frame, fp_location_delta=delta)
        second = bundle.predict_distribution(frame.copy(), fp_location_delta=delta.copy())
        np.testing.assert_array_equal(first.to_numpy(), second.to_numpy())
        quantiles = first[[f"p{int(q):02d}_fp" for q in (10, 25, 50, 75, 90, 95)]].to_numpy()
        probabilities = first[[f"prob_fp_ge_{int(t)}" for t in THRESHOLDS]].to_numpy()
        self.assertEqual(quantile_crossing_rate(quantiles), 0.0)
        self.assertEqual(probability_violation_rate(probabilities), 0.0)
        self.assertTrue(((0 <= probabilities) & (probabilities <= 1)).all())

    def test_absence_location_shift_changes_mean_only_by_supplied_delta(self) -> None:
        frame = load_phase6b_frame().tail(4)
        bundle, _ = load_and_validate_probabilistic_artifact()
        baseline = bundle.predict_distribution(frame, fp_location_delta=0.0)
        adjusted = bundle.predict_distribution(frame, fp_location_delta=2.5)
        np.testing.assert_allclose(
            adjusted.phase6b_expected_fp - baseline.phase6b_expected_fp, 2.5,
            rtol=0.0, atol=1e-12,
        )
        np.testing.assert_allclose(
            adjusted[["p10_fp", "p25_fp", "p50_fp", "p75_fp", "p90_fp", "p95_fp"]]
            - baseline[["p10_fp", "p25_fp", "p50_fp", "p75_fp", "p90_fp", "p95_fp"]],
            2.5, rtol=0.0, atol=1e-12,
        )

    def test_corrupted_artifact_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "phase6b"
            shutil.copytree(DEFAULT_PHASE6B_ARTIFACT_ROOT, root)
            model = root / "q90.json"
            with model.open("ab") as handle:
                handle.write(b"corruption")
            with self.assertRaisesRegex(ModelArtifactMismatch, "fingerprint mismatch"):
                load_and_validate_probabilistic_artifact(root)

    def test_missing_artifact_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "phase6b"
            shutil.copytree(DEFAULT_PHASE6B_ARTIFACT_ROOT, root)
            (root / "q95.json").unlink()
            with self.assertRaisesRegex(ModelArtifactMismatch, "missing Phase 6B artifact"):
                load_and_validate_probabilistic_artifact(root)

    def test_frozen_research_gates_are_preserved(self) -> None:
        result = json.loads(RESULTS_PATH.read_text())
        self.assertEqual(result["status"], "FROZEN")
        self.assertEqual(result["dataset"]["primary_rows"], 29232)
        self.assertEqual(result["quantile_models"]["final_crossing_rate"], 0.0)
        self.assertEqual(
            result["high_score_probabilities"][
                "final_probability_order_violation_rate"
            ], 0.0
        )
        self.assertTrue(result["central_analysis"]["mean_challenger_selected"])


class Phase6BLiveIntegrationTests(unittest.TestCase):
    def test_predict_live_preflights_phase6b_even_without_a_market(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            initialize_database(database)
            with patch(
                "src.live.prediction.load_and_validate_probabilistic_artifact",
                wraps=load_and_validate_probabilistic_artifact,
            ) as loader:
                result = run_live_prediction(
                    "E2026", database_path=database,
                    cutoff=datetime(2026, 8, 20, 9, tzinfo=UTC),
                )
            loader.assert_called_once()
            self.assertEqual(result.status, "NO_CURRENT_FANTASY_SLATE")
            self.assertEqual(result.probabilistic_model_version, PHASE6B_MODEL_VERSION)
            with connect_database(database, read_only=True) as connection:
                stored = connection.execute(
                    "SELECT probabilistic_model_version,calibration_version "
                    "FROM live_prediction_runs"
                ).fetchone()
            self.assertEqual(stored, (PHASE6B_MODEL_VERSION, PHASE6B_CALIBRATION_VERSION))

    def test_missing_phase6b_bundle_makes_predict_live_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            initialize_database(database)
            result = run_live_prediction(
                "E2026", database_path=database,
                phase6b_artifact_root=Path(directory) / "missing",
                cutoff=datetime(2026, 8, 20, 9, tzinfo=UTC),
            )
            self.assertEqual(result.status, "MODEL_ARTIFACT_MISMATCH")
            self.assertEqual(result.error_code, "MODEL_ARTIFACT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
