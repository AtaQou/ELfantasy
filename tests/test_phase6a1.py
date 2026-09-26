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
from src.live.prediction_artifacts import (
    DEFAULT_PHASE4B_ARTIFACT_ROOT,
    ModelArtifactMismatch,
    _validate_phase4b,
    load_and_validate_frozen_artifacts,
)
from src.live.scoring_rules import resolve_scoring_rule_compatibility
from src.modeling.fantasy_scoring import PLAYER_SCORING_RULE_SPECIFICATION
from src.modeling.phase4b_deployment import verify_serialized_reproduction
from src.modeling.phase4b_protocol import load_phase4b_frame


class Phase6A1DeploymentBundleTests(unittest.TestCase):
    def test_real_bundle_loads_and_exposes_frozen_identity(self) -> None:
        bundle, validation = load_and_validate_frozen_artifacts()
        self.assertEqual(
            validation.performance_bundle_identifier,
            "phase4b_conditional_performance_frozen_v1",
        )
        self.assertTrue(validation.reproduction_gate_passed)
        self.assertEqual(len(validation.performance_bundle_fingerprint), 64)
        self.assertEqual(set(bundle.payload["components"]), {
            "direct", "minutes", "production",
        })

    def test_serialized_historical_models_reproduce_every_frozen_row(self) -> None:
        result = verify_serialized_reproduction()
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["rows"], 22399)
        self.assertLessEqual(result["maximum_absolute_difference"], 1e-12)
        self.assertLessEqual(result["mean_absolute_difference"], 1e-12)
        self.assertEqual(result["reproduced_metrics"]["mae"], 5.808649555104744)

    def test_live_deployment_inference_is_deterministic(self) -> None:
        frame = load_phase4b_frame(primary_only=True).tail(8)
        bundle, _ = load_and_validate_frozen_artifacts()
        first = bundle.predict_components(frame)
        second = bundle.predict_components(frame.copy())
        np.testing.assert_array_equal(first.to_numpy(), second.to_numpy())

    def test_corrupted_model_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_PHASE4B_ARTIFACT_ROOT, root)
            direct_path = root / json.loads(
                (root / "manifest.json").read_text()
            )["components"]["direct"]["model_path"]
            with direct_path.open("ab") as handle:
                handle.write(b"corruption")
            with self.assertRaisesRegex(ModelArtifactMismatch, "fingerprint mismatch"):
                _validate_phase4b(root)

    def test_missing_model_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_PHASE4B_ARTIFACT_ROOT, root)
            minutes_path = root / json.loads(
                (root / "manifest.json").read_text()
            )["components"]["minutes"]["model_path"]
            minutes_path.unlink()
            with self.assertRaisesRegex(ModelArtifactMismatch, "missing Phase 4B minutes"):
                _validate_phase4b(root)

    def test_predict_live_service_preflights_real_bundle_even_without_market(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "test.duckdb"
            initialize_database(database)
            with patch(
                "src.live.prediction.load_and_validate_frozen_artifacts",
                wraps=load_and_validate_frozen_artifacts,
            ) as loader:
                result = run_live_prediction(
                    "E2026", database_path=database,
                    cutoff=datetime(2026, 8, 19, 9, tzinfo=UTC),
                )
            loader.assert_called_once()
            self.assertEqual(result.status, "NO_CURRENT_FANTASY_SLATE")
            with connect_database(database, read_only=True) as connection:
                stored = connection.execute(
                    "SELECT performance_model_version FROM live_prediction_runs"
                ).fetchone()[0]
            self.assertEqual(stored, "phase4b_conditional_performance_frozen_v1")


class Phase6A1ScoringCompatibilityTests(unittest.TestCase):
    def _evidence(self, status: str, specification: dict) -> Path:
        path = Path(self.temp.name) / f"{status}.json"
        path.write_text(json.dumps({
            "season_code": "E2026", "verified_at": "2026-08-19T00:00:00+00:00",
            "verification_status": status,
            "player_scoring_rule_specification": specification,
        }))
        return path

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_verified_matching_formula_is_compatible(self) -> None:
        result = resolve_scoring_rule_compatibility(
            "E2026", evidence_path=self._evidence(
                "VERIFIED_CURRENT_SEASON", PLAYER_SCORING_RULE_SPECIFICATION
            )
        )
        self.assertEqual(result.state, "SCORING_RULES_COMPATIBLE")
        self.assertTrue(result.production_ready)

    def test_verified_different_formula_is_mismatch(self) -> None:
        changed = dict(PLAYER_SCORING_RULE_SPECIFICATION)
        changed["team_win_bonus"] = "0.20 * abs(base_score)"
        result = resolve_scoring_rule_compatibility(
            "E2026", evidence_path=self._evidence("VERIFIED_CURRENT_SEASON", changed)
        )
        self.assertEqual(result.state, "SCORING_RULES_MISMATCH")
        self.assertFalse(result.production_ready)

    def test_unverified_evidence_is_unverified(self) -> None:
        result = resolve_scoring_rule_compatibility(
            "E2026", evidence_path=self._evidence(
                "CURRENT_PAGE_ONLY", PLAYER_SCORING_RULE_SPECIFICATION
            )
        )
        self.assertEqual(result.state, "SCORING_RULES_UNVERIFIED")
        self.assertFalse(result.production_ready)

    def test_repository_e2026_state_is_compatible(self) -> None:
        self.assertEqual(
            resolve_scoring_rule_compatibility("E2026").state,
            "SCORING_RULES_COMPATIBLE",
        )


if __name__ == "__main__":
    unittest.main()
