from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.modeling.ml_experiments import (
    build_sklearn_pipeline,
    prediction_fingerprint,
)
from src.modeling.ml_protocol import (
    FEATURE_MANIFEST_ADDITIONS,
    FORBIDDEN_MODEL_INPUTS,
    PHASE4A_DATASET_VERSION,
    PHASE4A_PROTOCOL_VERSION,
    OuterFold,
    build_outer_fold_indices,
    feature_manifest,
    load_phase4a_frame,
    protocol_payload,
    validate_fold,
)


SAMPLE_ROOT = Path("data/samples/phase4a")


def _canonical_has_phase4a() -> bool:
    if not DEFAULT_DATABASE_PATH.exists():
        return False
    with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
        return bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='ml_phase4a_outer_predictions_v1'"
        ).fetchone()[0])


class Phase4AProtocolUnitTests(unittest.TestCase):
    def test_frozen_manifest_counts_and_hashes_are_deterministic(self) -> None:
        expected = {
            "CORE": 106,
            "CORE_ROTATION": 132,
            "CORE_ONOFF": 124,
            "CORE_LINEUP_TEAMMATE": 118,
            "CORE_PLAYER_SHOT": 139,
            "CORE_OPPONENT_SHOT": 130,
            "CORE_ROTATION_ONOFF": 150,
            "CORE_SHOT_ALL": 166,
            "CORE_ALL_RICH": 222,
        }
        for name, count in expected.items():
            first = feature_manifest(name)
            second = feature_manifest(name)
            self.assertEqual(first["feature_count"], count)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(len(first["sha256"]), 64)

    def test_no_manifest_contains_forbidden_or_target_game_outcomes(self) -> None:
        target_game_outcomes = {
            "target_minutes", "target_started", "target_stint_count",
            "target_lineup", "target_shots", "target_onoff",
        }
        for name in FEATURE_MANIFEST_ADDITIONS:
            columns = set(feature_manifest(name)["features"])
            self.assertFalse(columns & FORBIDDEN_MODEL_INPUTS)
            self.assertFalse(columns & target_game_outcomes)
            self.assertFalse(any("credit" in column.lower() for column in columns))

    def test_player_identity_ablation_removes_only_player_id(self) -> None:
        with_id = feature_manifest("CORE_ALL_RICH")
        without_id = feature_manifest("CORE_ALL_RICH", include_player_id=False)
        self.assertEqual(
            set(with_id["features"]) - set(without_id["features"]), {"player_id"}
        )
        self.assertEqual(with_id["feature_count"] - without_id["feature_count"], 1)

    def test_protocol_versions_and_primary_metric_are_frozen(self) -> None:
        payload = protocol_payload()
        self.assertEqual(payload["protocol_version"], PHASE4A_PROTOCOL_VERSION)
        self.assertEqual(payload["dataset_version"], PHASE4A_DATASET_VERSION)
        self.assertEqual(payload["primary_target"], "actual_fantasy_points")
        self.assertEqual(payload["primary_metric"], "mae")

    def test_train_only_ridge_preprocessing_ignores_future_state(self) -> None:
        manifest = {
            "numeric": ["number"],
            "categorical": ["category"],
            "features": ["number", "category"],
        }
        train = pd.DataFrame({
            "number": [1.0, np.nan, 3.0],
            "category": ["old_a", "old_b", None],
        })
        future = pd.DataFrame({"number": [10_000.0], "category": ["future_only"]})
        model = build_sklearn_pipeline("ridge", manifest, {"alpha": 10.0}, 17)
        model.fit(train, np.array([1.0, 2.0, 3.0]))
        numeric = model.named_steps["preprocessor"].named_transformers_["numeric"]
        categorical = model.named_steps["preprocessor"].named_transformers_[
            "categorical"
        ]
        self.assertEqual(float(numeric.named_steps["imputer"].statistics_[0]), 2.0)
        learned = set(categorical.named_steps["onehot"].categories_[0])
        self.assertNotIn("future_only", learned)
        self.assertEqual(len(model.predict(future)), 1)

    def test_prediction_fingerprint_is_repeatable_and_sensitive(self) -> None:
        frame = pd.DataFrame({
            "model_row_id": ["a", "b"],
            "outer_fold": ["one", "two"],
            "predicted_fp": [1.25, 2.5],
        })
        first = prediction_fingerprint(frame)
        self.assertEqual(first, prediction_fingerprint(frame.copy()))
        changed = frame.copy()
        changed.loc[0, "predicted_fp"] += 0.01
        self.assertNotEqual(first, prediction_fingerprint(changed))

    def test_fold_validation_rejects_future_training_and_game_overlap(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        frame = pd.DataFrame({
            "target_game_time": [start, start + timedelta(days=1)],
            "game_id": ["same", "same"],
        })
        fold = {
            "definition": OuterFold("bad", "E2025", ("E2024",), "synthetic"),
            "inner_train": pd.Index([0]),
            "inner_validation": pd.Index([1]),
            "outer_train": pd.Index([0]),
            "outer_test": pd.Index([1]),
        }
        with self.assertRaisesRegex(ValueError, "Game crosses"):
            validate_fold(frame, fold)
        frame.loc[0, "target_game_time"] = start + timedelta(days=2)
        with self.assertRaisesRegex(ValueError, "Non-chronological"):
            validate_fold(frame, fold)


@unittest.skipUnless(_canonical_has_phase4a(), "canonical Phase 4A artifacts absent")
class CanonicalPhase4ATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frame = load_phase4a_frame(DEFAULT_DATABASE_PATH, primary_only=True)
        cls.folds = build_outer_fold_indices(cls.frame)

    def scalar(self, query: str):
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            return connection.execute(query).fetchone()[0]

    def test_outer_fold_counts_and_strict_chronology(self) -> None:
        expected = {
            "outer_e2023": (6833, 6984),
            "outer_e2024": (13817, 6899),
            "outer_e2025": (20716, 8516),
        }
        for fold in self.folds:
            fold_id = fold["definition"].fold_id
            self.assertEqual(
                (len(fold["outer_train"]), len(fold["outer_test"])),
                expected[fold_id],
            )
            train = self.frame.loc[fold["outer_train"]]
            test = self.frame.loc[fold["outer_test"]]
            self.assertLess(
                train["target_game_time"].max(), test["target_game_time"].min()
            )
            self.assertFalse(set(train["game_id"]) & set(test["game_id"]))

    def test_every_experiment_has_the_identical_outer_row_universe(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM (SELECT experiment_id, count(*) n, "
                "count(DISTINCT model_row_id) d "
                "FROM ml_phase4a_outer_predictions_v1 GROUP BY experiment_id "
                "HAVING n<>22399 OR d<>22399)"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM (SELECT model_row_id, count(*) n "
                "FROM ml_phase4a_outer_predictions_v1 GROUP BY model_row_id "
                "HAVING n<>(SELECT count(DISTINCT experiment_id) "
                "FROM ml_phase4a_outer_predictions_v1))"
            ),
            0,
        )

    def test_predictions_are_genuine_future_verified_targets(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_outer_predictions_v1 WHERE "
                "season NOT IN ('E2023','E2024','E2025') OR actual_fp IS NULL OR "
                "target_rule_status NOT LIKE 'OFFICIAL_%VALIDATED' OR "
                "feature_cutoff_time<>target_game_time"
            ),
            0,
        )

    def test_tuning_and_preprocessing_never_see_outer_test_rows(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_inner_trials_v1 WHERE "
                "outer_test_rows_seen<>0 OR "
                "inner_train_max_time>=inner_validation_min_time"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_preprocessing_audit_v1 WHERE "
                "outer_test_rows_seen_during_fit<>0 OR "
                "preprocessor_fit_max_time>=outer_test_min_time"
            ),
            0,
        )

    def test_prediction_provenance_is_complete_and_unique(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_outer_predictions_v1 WHERE "
                "model_row_id IS NULL OR experiment_id IS NULL OR outer_fold IS NULL "
                "OR dataset_version IS NULL OR protocol_version IS NULL OR "
                "predicted_fp IS NULL OR actual_fp IS NULL"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*)-count(DISTINCT (experiment_id, model_row_id)) "
                "FROM ml_phase4a_outer_predictions_v1"
            ),
            0,
        )

    def test_experiment_tracking_records_every_required_version(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_experiments_v1 WHERE "
                "dataset_version IS NULL OR feature_pipeline_version IS NULL OR "
                "rich_feature_pipeline_version IS NULL OR "
                "evaluation_target_status IS NULL OR feature_manifest_sha256 IS NULL OR "
                "model_library_version IS NULL OR random_seed IS NULL"
            ),
            0,
        )

    def test_no_quarantined_credit_or_target_outcome_is_an_input(self) -> None:
        protocol = json.loads((SAMPLE_ROOT / "protocol.json").read_text())
        for manifest in protocol["feature_manifests"].values():
            columns = set(manifest["features"])
            self.assertFalse(any("credit" in column.lower() for column in columns))
            self.assertNotIn("target_minutes", columns)
            self.assertNotIn("actual_fantasy_points", columns)

    def test_fixed_seed_outer_prediction_fingerprints_are_retained(self) -> None:
        results = json.loads((SAMPLE_ROOT / "experiment_results.json").read_text())
        experiments = results["experiments"]
        self.assertEqual(len(experiments), 21)
        self.assertEqual(results["prediction_rows"], 470379)
        self.assertTrue(all(len(row["prediction_fingerprint"]) == 64 for row in experiments))

    def test_standardized_history_never_changes_outer_target_semantics(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase4a_outer_predictions_v1 WHERE "
                "training_target_mode='verified_plus_standardized' AND "
                "(season NOT IN ('E2023','E2024','E2025') OR "
                "target_rule_status NOT LIKE 'OFFICIAL_%VALIDATED')"
            ),
            0,
        )

    def test_locked_phase3b_baseline_is_reproduced(self) -> None:
        diagnostics = json.loads((SAMPLE_ROOT / "diagnostics.json").read_text())
        reproduction = diagnostics["global_phase3b_baseline_reproduction"]
        self.assertEqual(reproduction["rows"], 29232)
        self.assertAlmostEqual(reproduction["mae"], 5.9952919891, places=9)
        self.assertAlmostEqual(reproduction["mae_reproduction_difference"], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
