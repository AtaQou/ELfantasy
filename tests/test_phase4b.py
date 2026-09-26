from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.modeling.phase4b_analysis import (
    chronological_calibration_split,
    finite_sample_radius,
    fit_calibrator,
    select_hybrid_weight,
)
from src.modeling.phase4b_models import training_target
from src.modeling.phase4b_protocol import (
    PHASE4B_FORBIDDEN_MODEL_INPUTS,
    apply_role_change_class,
    competition_context,
    phase4b_feature_manifest,
    phase4b_protocol_payload,
    role_change_score,
    role_change_thresholds,
)


SAMPLE_ROOT = Path("data/samples/phase4b")


def canonical_has_phase4b() -> bool:
    if not DEFAULT_DATABASE_PATH.exists():
        return False
    with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
        return bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='ml_phase4b_outer_predictions_v1'"
        ).fetchone()[0])


class Phase4BProtocolUnitTests(unittest.TestCase):
    def test_manifests_are_targeted_deterministic_and_forbid_identity_outcomes(self) -> None:
        names = (
            "MINUTES_ROLE", "MINUTES_ROLE_STAGE", "PRODUCTION",
            "PRODUCTION_SHOT", "PRODUCTION_STAGE",
        )
        for name in names:
            first = phase4b_feature_manifest(name)
            second = phase4b_feature_manifest(name)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertFalse(set(first["features"]) & PHASE4B_FORBIDDEN_MODEL_INPUTS)
            self.assertNotIn("player_id", first["features"])
            self.assertNotIn("target_minutes", first["features"])
            self.assertNotIn("actual_fp_per_min", first["features"])
        self.assertLess(
            phase4b_feature_manifest("MINUTES_ROLE")["feature_count"], 132
        )

    def test_protocol_preserves_locked_outer_folds_and_conditional_population(self) -> None:
        payload = phase4b_protocol_payload()
        self.assertEqual(payload["population"], "conditional_on_playing")
        self.assertEqual(
            [row["outer_test_season"] for row in payload["outer_folds"]],
            ["E2023", "E2024", "E2025"],
        )
        self.assertEqual(payload["primary_metric"], "mae")

    def test_role_change_score_uses_only_lagged_inputs(self) -> None:
        frame = pd.DataFrame({
            "minutes_ewma": [20.0, 30.0],
            "season_minutes_avg_before": [20.0, 20.0],
            "minutes_trend": [0.0, 5.0],
            "rot_minutes_trend": [0.0, 4.0],
            "rot_last5_minutes_share": [0.5, 0.8],
            "rot_season_minutes_share_before": [0.5, 0.5],
            "rot_last5_starter_rate": [0.5, 1.0],
            "season_start_rate_before": [0.5, 0.5],
            "rot_last5_minutes_std": [1.0, 8.0],
            "target_minutes": [1.0, 99.0],
        })
        first = role_change_score(frame)
        changed = frame.copy()
        changed["target_minutes"] = [99.0, 1.0]
        np.testing.assert_allclose(first, role_change_score(changed))
        self.assertLess(first.iloc[0], first.iloc[1])

    def test_role_change_thresholds_are_fit_on_supplied_history(self) -> None:
        train = pd.DataFrame({"role_change_score": np.arange(100, dtype=float)})
        thresholds = role_change_thresholds(train)
        future = pd.DataFrame({"role_change_score": [0.0, 70.0, 99.0]})
        classes = apply_role_change_class(future, thresholds).tolist()
        self.assertEqual(
            classes, ["stable_role", "moderate_role_change", "large_role_change"]
        )

    def test_competition_stage_uses_explicit_phase_and_schedule_order(self) -> None:
        start = datetime(2025, 4, 1, tzinfo=UTC)
        rows = [
            game("rs", "RS", 99, start, "A", "B", 80, 70),
            game("pi", "PI", 1, start + timedelta(days=1), "C", "D", 80, 70),
        ]
        for number, winner in enumerate(("A", "B", "A", "B", "A"), start=1):
            home, away = ("A", "B") if number % 2 else ("B", "A")
            home_score = 80 if winner == home else 70
            away_score = 80 if winner == away else 70
            rows.append(game(
                f"po{number}", "PO", number,
                start + timedelta(days=number + 1), home, away,
                home_score, away_score,
            ))
        rows.extend([
            game("semi1", "FF", 42, start + timedelta(days=10), "A", "C", 80, 70),
            game("semi2", "FF", 42, start + timedelta(days=10, hours=3), "B", "D", 80, 70),
            game("third", "FF", 43, start + timedelta(days=12), "C", "D", 80, 70),
            game("final", "FF", 43, start + timedelta(days=12, hours=3), "A", "B", 80, 70),
        ])
        context = competition_context(pd.DataFrame(rows)).set_index("game_id")
        self.assertEqual(context.loc["rs", "competition_stage"], "REGULAR_SEASON")
        self.assertEqual(context.loc["pi", "competition_stage"], "PLAY_IN")
        self.assertEqual(context.loc["po1", "competition_stage"], "PLAYOFFS")
        self.assertFalse(bool(context.loc["po3", "pregame_elimination_game"]))
        self.assertTrue(bool(context.loc["po4", "pregame_elimination_game"]))
        self.assertTrue(bool(context.loc["po5", "pregame_deciding_game"]))
        self.assertEqual(context.loc["semi1", "competition_stage"], "FINAL_FOUR_SEMIFINAL")
        self.assertEqual(context.loc["third", "competition_stage"], "FINAL_FOUR_THIRD_PLACE")
        self.assertEqual(context.loc["final", "competition_stage"], "FINAL_FOUR_FINAL")

    def test_raw_weighted_and_stabilized_rate_formulas(self) -> None:
        frame = pd.DataFrame({
            "target_minutes": [10.0, 30.0],
            "actual_fantasy_points": [5.0, 30.0],
            "standardized_fantasy_points": [5.0, 30.0],
        })
        raw = training_target(frame, "production", "raw", {})
        weighted = training_target(frame, "production", "weighted", {})
        stabilized = training_target(
            frame, "production", "stabilized", {"stabilization_minutes": 10.0}
        )
        np.testing.assert_allclose(raw["values"], [0.5, 1.0])
        np.testing.assert_allclose(weighted["weights"], [0.5, 1.5])
        self.assertAlmostEqual(stabilized["prior_rate"], 0.875)
        np.testing.assert_allclose(
            stabilized["values"], [(5 + 8.75) / 20, (30 + 8.75) / 40]
        )

    def test_fp_per_min_denominator_guard_fails_closed(self) -> None:
        frame = pd.DataFrame({
            "target_minutes": [0.0], "actual_fantasy_points": [5.0],
            "standardized_fantasy_points": [5.0],
        })
        with self.assertRaisesRegex(ValueError, "positive played minutes"):
            training_target(frame, "production", "raw", {})

    def test_hybrid_weight_is_selected_only_from_supplied_inner_predictions(self) -> None:
        direct = {"fold": prediction_frame([0.0, 10.0], [2.0, 8.0])}
        decomp = {"fold": prediction_frame([0.0, 10.0], [0.0, 10.0])}
        weight, rows = select_hybrid_weight(direct, decomp)
        self.assertEqual(weight, 0.25)
        self.assertTrue(all(row["outer_test_rows_seen"] == 0 for row in rows))

    def test_calibration_split_is_strictly_chronological(self) -> None:
        frame = prediction_frame([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        frame["target_game_time"] = pd.date_range(
            "2024-01-01", periods=4, tz="UTC"
        )
        fit, evaluation = chronological_calibration_split(frame)
        self.assertLess(fit["target_game_time"].max(), evaluation["target_game_time"].min())

    def test_affine_calibration_and_finite_sample_radius(self) -> None:
        calibrator = fit_calibrator(
            "affine", np.array([1.0, 2.0, 3.0]), np.array([3.0, 5.0, 7.0])
        )
        np.testing.assert_allclose(calibrator.transform(np.array([4.0])), [9.0])
        self.assertEqual(finite_sample_radius(np.arange(1.0, 11.0), 0.8), 9.0)


@unittest.skipUnless(canonical_has_phase4b(), "canonical Phase 4B artifacts absent")
class CanonicalPhase4BTests(unittest.TestCase):
    def scalar(self, query: str):
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            return connection.execute(query).fetchone()[0]

    def test_every_architecture_has_identical_outer_rows(self) -> None:
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM (SELECT architecture_name, count(*) n, "
            "count(DISTINCT model_row_id) d FROM ml_phase4b_outer_predictions_v1 "
            "GROUP BY architecture_name HAVING n<>22399 OR d<>22399)"
        ), 0)

    def test_selected_components_have_complete_genuine_outer_predictions(self) -> None:
        results = json.loads((SAMPLE_ROOT / "results.json").read_text())
        minutes_id = results["selected_minutes_experiment_id"]
        production_id = results["selected_production_experiment_id"]
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            minutes = connection.execute(
                "SELECT count(*), count(DISTINCT model_row_id) FROM "
                "ml_phase4b_minutes_predictions_v1 WHERE experiment_id=?", [minutes_id]
            ).fetchone()
            production = connection.execute(
                "SELECT count(*), count(DISTINCT model_row_id) FROM "
                "ml_phase4b_production_predictions_v1 WHERE experiment_id=?", [production_id]
            ).fetchone()
        self.assertEqual(minutes, (22399, 22399))
        self.assertEqual(production, (22399, 22399))

    def test_no_outer_target_fits_calibration_or_intervals(self) -> None:
        results = json.loads((SAMPLE_ROOT / "results.json").read_text())
        audit = results["leakage_audit"]
        self.assertEqual(audit["violations_detected"], 0)
        self.assertFalse(audit["outer_test_targets_fit_calibration"])
        self.assertFalse(audit["outer_test_residuals_fit_intervals"])
        self.assertFalse(audit["outer_test_scores_select_blend_weight"])

    def test_phase4a_artifacts_remain_frozen(self) -> None:
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM ml_phase4a_outer_predictions_v1"
        ), 470379)
        self.assertAlmostEqual(self.scalar(
            "SELECT avg(abs(actual_fp-predicted_fp)) FROM "
            "ml_phase4a_outer_predictions_v1 WHERE experiment_id='" +
            "p4a__catboost__core_rotation__verified_only__player__s17'"
        ), 5.8272, places=4)

    def test_final_intervals_are_complete_and_not_fixed_width(self) -> None:
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM ml_phase4b_prediction_intervals_v1 WHERE "
            "floor_fp IS NULL OR ceiling_fp IS NULL OR predicted_fp IS NULL"
        ), 0)
        self.assertGreater(self.scalar(
            "SELECT count(DISTINCT round(interval_width, 6)) FROM "
            "ml_phase4b_prediction_intervals_v1"
        ), 1)


def game(
    game_id: str, phase: str, round_number: int, game_date: datetime,
    home: str, away: str, home_score: int, away_score: int,
) -> dict[str, object]:
    return {
        "canonical_game_id": game_id, "season_code": "E2024",
        "phase_code": phase, "round_number": round_number,
        "game_date": game_date, "home_team_id": home, "away_team_id": away,
        "home_score": home_score, "away_score": away_score,
    }


def prediction_frame(actual: list[float], predicted: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "model_row_id": [f"row{index}" for index in range(len(actual))],
        "game_id": [f"game{index}" for index in range(len(actual))],
        "actual_fp": actual, "predicted_fp": predicted,
        "target_game_time": [
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
            for index in range(len(actual))
        ],
    })


if __name__ == "__main__":
    unittest.main()
