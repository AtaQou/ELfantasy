from __future__ import annotations

import json
from pathlib import Path
import unittest

import duckdb
import numpy as np
import pandas as pd

from src.modeling.fantasy_scoring import score_player_game
from src.modeling.phase9a_features import (
    add_shifted_environment_history,
    validate_environment_cutoffs,
    validate_stat_targets,
)
from src.modeling.phase9a_protocol import (
    DOWNSIDE_THRESHOLDS,
    FORBIDDEN_MODEL_INPUTS,
    GAME_ENVIRONMENT_TARGETS,
    OPPORTUNITY_COMPONENTS,
    PBP_FEATURE_FAMILIES,
    QUANTILES,
    UPSIDE_THRESHOLDS,
    player_feature_manifest,
    protocol_fingerprint,
)
from src.modeling.phase9a_runner import learn_convex_weight
from src.modeling.phase9a_simulation import (
    ResidualBank,
    build_residual_bank,
    distribution_metrics,
    enforce_constraints,
    score_stat_arrays,
    simulate_stat_lines,
    summarize_distribution,
)


RESULTS_PATH = Path("data/samples/phase9a/results.json")
RESEARCH_RESULTS_PATH = Path("data/derived/phase9a/research/results.json")
ENVIRONMENT_PATH = Path("data/derived/phase9a/research/game_environment_frame.parquet")
PREDICTION_PATH = Path("data/derived/phase9a/research/outer_predictions.parquet")


class Phase9AUnitTests(unittest.TestCase):
    def test_manifests_exclude_target_game_stats(self) -> None:
        for include_environment in (False, True):
            manifest = player_feature_manifest(include_environment=include_environment)
            self.assertFalse(set(manifest["features"]) & FORBIDDEN_MODEL_INPUTS)
            self.assertNotIn("target_minutes", manifest["features"])

    def test_shifted_environment_history_is_strictly_pregame(self) -> None:
        rows = []
        for index in range(4):
            for team, opponent in (("A", "B"), ("B", "A")):
                row = {
                    "game_id": f"g{index}", "season": "E2022",
                    "team_id": team, "opponent_team_id": opponent,
                    "game_time": pd.Timestamp("2022-01-01", tz="UTC") + pd.Timedelta(days=index),
                }
                for target in (*GAME_ENVIRONMENT_TARGETS,):
                    row[target] = float(index + (team == "B"))
                rows.append(row)
        shifted = add_shifted_environment_history(
            pd.DataFrame(rows), GAME_ENVIRONMENT_TARGETS
        )
        validate_environment_cutoffs(shifted)
        a = shifted[shifted.team_id.eq("A")].sort_values("game_time")
        self.assertTrue(np.isnan(a.iloc[0]["env_team_points_last5"]))
        self.assertEqual(a.iloc[1]["env_team_points_last5"], 0.0)

    def test_stat_target_validation_and_basketball_constraints(self) -> None:
        frame = pd.DataFrame({
            "two_points_made": [3], "two_points_attempted": [5],
            "three_points_made": [2], "three_points_attempted": [6],
            "free_throws_made": [4], "free_throws_attempted": [5],
            "points": [16],
        })
        validate_stat_targets(frame)
        constrained = enforce_constraints({
            "two_points_made": np.array([[7.0]]),
            "two_points_attempted": np.array([[5.0]]),
            "three_points_made": np.array([[3.0]]),
            "three_points_attempted": np.array([[2.0]]),
            "free_throws_made": np.array([[5.0]]),
            "free_throws_attempted": np.array([[4.0]]),
            "offensive_rebounds": np.array([[2.0]]),
            "defensive_rebounds": np.array([[3.0]]),
        })
        self.assertEqual(constrained["two_points_made"].item(), 5)
        self.assertEqual(constrained["total_rebounds"].item(), 5)

    def test_vectorized_fantasy_scoring_matches_verified_formula(self) -> None:
        stats = {
            "two_points_made": np.array([[3]]), "two_points_attempted": np.array([[5]]),
            "three_points_made": np.array([[2]]), "three_points_attempted": np.array([[6]]),
            "free_throws_made": np.array([[4]]), "free_throws_attempted": np.array([[5]]),
            "offensive_rebounds": np.array([[2]]), "defensive_rebounds": np.array([[4]]),
            "assists": np.array([[5]]), "steals": np.array([[2]]),
            "blocks": np.array([[1]]), "turnovers": np.array([[3]]),
            "fouls_drawn": np.array([[6]]), "fouls_committed": np.array([[2]]),
            "blocks_received": np.array([[1]]),
        }
        observed = score_stat_arrays(stats, np.array([[True]])).item()
        scalar = score_player_game({
            "points": 16, "total_rebounds": 6, "assists": 5, "steals": 2,
            "blocks": 1, "fouls_drawn": 6, "turnovers": 3,
            "blocks_received": 1, "fouls_committed": 2,
            "two_points_attempted": 5, "two_points_made": 3,
            "three_points_attempted": 6, "three_points_made": 2,
            "free_throws_attempted": 5, "free_throws_made": 4,
        }, team_won=True)
        self.assertAlmostEqual(observed, float(scalar.total))

    def test_probabilistic_minutes_and_joint_component_sampling(self) -> None:
        rows = 4
        frame = pd.DataFrame({
            "season_games_before": [15] * rows,
            "season_minutes_avg_before": [24.0] * rows,
            "season_2p_pct_before": [0.55] * rows, "last_5_2p_pct": [0.56] * rows,
            "season_3p_pct_before": [0.36] * rows, "last_5_3p_pct": [0.35] * rows,
            "season_ft_pct_before": [0.80] * rows, "last_5_ft_pct": [0.79] * rows,
            "two_pa_per_min": [0.25] * rows, "three_pa_per_min": [0.18] * rows,
            "fta_per_min": [0.12] * rows,
        })
        bank_rows = 160
        common = np.linspace(-1.5, 1.5, bank_rows)
        bank = ResidualBank(
            minutes_residual=common * 3.0,
            standardized_opportunity_residual=np.column_stack([
                common + index * 0.01 for index in range(len(OPPORTUNITY_COMPONENTS))
            ]),
            archetype=np.array(["creator"] * bank_rows),
            expected_minute_band=np.array([2] * bank_rows),
        )
        opportunities = np.tile(
            np.array([6, 4, 3, 1, 3, 4, 1, 0.5, 2, 3, 2, 0.5]), (rows, 1)
        )
        fixed, _, fixed_minutes = simulate_stat_lines(
            frame=frame, predicted_minutes=np.array([24.0] * rows),
            predicted_opportunities=opportunities, residual_bank=bank,
            win_probability=np.array([0.5] * rows), archetype=["creator"] * rows,
            simulations=256, probabilistic_minutes=False, random_seed=10,
        )
        probabilistic, _, sampled_minutes = simulate_stat_lines(
            frame=frame, predicted_minutes=np.array([24.0] * rows),
            predicted_opportunities=opportunities, residual_bank=bank,
            win_probability=np.array([0.5] * rows), archetype=["creator"] * rows,
            simulations=256, probabilistic_minutes=True, random_seed=10,
        )
        self.assertEqual(fixed.shape, (rows, 256))
        self.assertEqual(probabilistic.shape, (rows, 256))
        self.assertAlmostEqual(float(fixed_minutes.std()), 0.0)
        self.assertGreater(float(sampled_minutes.std()), 1.0)

    def test_distribution_quantiles_probabilities_and_calibration_metrics(self) -> None:
        samples = np.tile(np.linspace(-5, 45, 512), (20, 1))
        summary = summarize_distribution(samples)
        quantile_columns = [f"p{int(value * 100):02d}_fp" for value in QUANTILES]
        self.assertTrue((np.diff(summary[quantile_columns], axis=1) >= 0).all())
        self.assertTrue((summary["prob_fp_ge_20"] >= summary["prob_fp_ge_40"]).all())
        self.assertTrue((summary["prob_fp_le_5"] <= summary["prob_fp_le_15"]).all())
        metrics = distribution_metrics(np.linspace(0, 30, 20), summary, samples)
        self.assertTrue(np.isfinite(metrics["crps"]))
        self.assertIn("calibration_slope", metrics["probabilities"]["prob_fp_ge_20"])

    def test_ensemble_weight_is_oof_and_data_learned(self) -> None:
        result = learn_convex_weight(
            [1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0]
        )
        self.assertEqual(result["challenger_weight"], 1.0)
        self.assertEqual(result["outer_test_rows_seen"], 0)
        self.assertEqual(len(result["trials"]), 41)

    def test_residual_bank_rejects_wrong_component_width(self) -> None:
        with self.assertRaisesRegex(ValueError, "wrong shape"):
            build_residual_bank(
                actual_minutes=np.ones(120), predicted_minutes=np.ones(120),
                actual_opportunities=np.ones((120, 2)),
                predicted_opportunities=np.ones((120, 2)),
                archetype=["x"] * 120,
            )


@unittest.skipUnless(RESULTS_PATH.is_file(), "run Phase 9A research first")
class Phase9AResearchIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = json.loads(RESULTS_PATH.read_text())

    def test_protocol_and_artifact_reproducibility(self) -> None:
        research = json.loads(RESEARCH_RESULTS_PATH.read_text())
        self.assertEqual(self.results["protocol_fingerprint"], protocol_fingerprint())
        self.assertEqual(self.results["research_fingerprint"], research["research_fingerprint"])

    def test_game_environment_sources_are_strictly_before_game(self) -> None:
        with duckdb.connect() as connection:
            violations = connection.execute(
                "SELECT count(*) FILTER (WHERE env_source_max_game_time>=game_time "
                "OR opp_env_source_max_game_time>=game_time) FROM read_parquet(?)",
                [str(ENVIRONMENT_PATH)],
            ).fetchone()[0]
        self.assertEqual(violations, 0)

    def test_chronological_outer_folds_and_oof_safety(self) -> None:
        self.assertEqual(set(self.results["outer_folds"]), {
            "outer_e2023", "outer_e2024", "outer_e2025",
        })
        for fold in self.results["outer_folds"].values():
            for audit in fold["leakage_audits"].values():
                self.assertEqual(audit["outer_test_rows_seen_during_selection"], 0)
                self.assertLess(audit["outer_train_max_time"], audit["outer_test_min_time"])
                self.assertFalse(audit["target_game_actual_minutes_used_at_inference"])
            for ensemble in fold["ensemble_weights"].values():
                self.assertEqual(ensemble["outer_test_rows_seen"], 0)

    def test_pbp_families_are_bounded_and_independently_reported(self) -> None:
        ablations = self.results["game_environment"]["pbp_feature_ablations"]
        self.assertEqual(set(ablations), {"box_history_only", *PBP_FEATURE_FAMILIES})
        self.assertIn("early_clock_shot_tendency", self.results["game_environment"]["unsupported"])

    def test_component_and_minutes_outputs_cover_required_stats(self) -> None:
        metrics = self.results["component_accuracy"]["C_environment_statline"]["metrics"]
        required = {
            "minutes", "points", "two_points_made", "two_points_attempted",
            "three_points_made", "three_points_attempted", "free_throws_made",
            "free_throws_attempted", "total_rebounds", "assists", "steals",
            "blocks", "turnovers", "fouls_drawn", "fouls_committed",
            "blocks_received",
        }
        self.assertTrue(required.issubset(metrics))
        minutes = self.results["minutes_distribution"]
        self.assertIn("probabilistic_quantiles", minutes)
        self.assertEqual(set(minutes["by_fold"]), set(self.results["outer_folds"]))
        coverages = [
            minutes["probabilistic_quantiles"][quantile]["coverage"]
            for quantile in ("p10", "p50", "p90")
        ]
        self.assertTrue(coverages[0] < coverages[1] < coverages[2])

    def test_segment_diagnostics_compare_distinct_architectures(self) -> None:
        compared = {
            "A_phase6c_frozen", "B_statline", "C_environment_statline",
            self.results["selected_on_inner_validation"],
        }
        for grouping, segments in self.results["segments"].items():
            self.assertTrue(segments, grouping)
            for segment in segments:
                self.assertEqual(set(segment["architectures"]), compared)
                self.assertEqual(
                    set(segment["mae_improvement_vs_phase6c"]), compared
                )

    def test_phase7_interface_and_frozen_artifacts(self) -> None:
        self.assertTrue(self.results["phase7_interface"]["passed"])
        self.assertFalse(self.results["phase7_interface"]["phase7_retrained"])
        self.assertTrue(self.results["frozen_artifacts"]["phase6c_phase7_untouched"])

    def test_final_distribution_is_finite_ordered_and_coherent(self) -> None:
        with duckdb.connect() as connection:
            frame = connection.execute(
                "SELECT * FROM read_parquet(?)", [str(PREDICTION_PATH)]
            ).df()
        quantiles = frame[[f"p{int(value * 100):02d}_fp" for value in QUANTILES]].to_numpy(float)
        self.assertTrue(np.isfinite(quantiles).all())
        self.assertTrue((np.diff(quantiles, axis=1) >= 0).all())
        upside = frame[[f"prob_fp_ge_{int(value)}" for value in UPSIDE_THRESHOLDS]].to_numpy(float)
        downside = frame[[f"prob_fp_le_{int(value)}" for value in DOWNSIDE_THRESHOLDS]].to_numpy(float)
        self.assertTrue((np.diff(upside, axis=1) <= 1e-12).all())
        self.assertTrue((np.diff(downside, axis=1) >= -1e-12).all())

    def test_freeze_decision_matches_strict_gate(self) -> None:
        passed = self.results["freeze_gate"]["passed"]
        self.assertEqual(self.results["status"] == "FROZEN", passed)
        self.assertEqual(self.results["deployment_bundle"] is not None, passed)


if __name__ == "__main__":
    unittest.main()
