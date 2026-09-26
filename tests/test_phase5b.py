from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import duckdb
import numpy as np
import pandas as pd

from src.db.database import connect_database, initialize_database
from src.live.availability import AvailabilityResolver
from src.live.phase5b import (
    RoleLimit,
    apply_user_decision,
    clear_role_limit,
    generate_availability_scenarios,
    redistribute_live_team,
    resolve_decisions,
    resolve_role_limits,
    set_role_limit,
)
from src.modeling.phase5b_protocol import (
    FORBIDDEN_REDISTRIBUTION_INPUTS,
    capped_simplex_projection,
    default_status_decision,
    normalize_baseline_minutes,
    redistribution_manifest,
    regulation_equivalent_minutes,
    validate_redistribution_feature_names,
)
from tests.test_phase5a import seed_minimal_database


class Phase5BProtocolTests(unittest.TestCase):
    def test_status_semantics_keep_participation_and_role_separate(self) -> None:
        self.assertEqual(default_status_decision("AVAILABLE"), "PLAY")
        self.assertEqual(default_status_decision("PROBABLE"), "PLAY")
        self.assertEqual(default_status_decision("OUT"), "OUT")
        self.assertEqual(default_status_decision("SUSPENDED"), "OUT")
        self.assertEqual(default_status_decision("NOT_REGISTERED"), "OUT")
        self.assertEqual(default_status_decision("LIMITED"), "LIMITED")
        for status in ("QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION", "UNKNOWN"):
            self.assertEqual(default_status_decision(status), "USER_DECISION")

    def test_questionable_never_reduces_conditional_minutes(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "AVAILABLE"
        frame.loc[0, "resolved_availability_status"] = "QUESTIONABLE"
        decisions = resolve_decisions(frame)
        self.assertEqual(decisions["P0"], "UNKNOWN")
        self.assertNotIn(decisions["P0"], {"LIMITED", "OUT"})

    def test_one_unknown_creates_explicit_play_and_out_scenarios(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "AVAILABLE"
        frame.loc[0, "resolved_availability_status"] = "QUESTIONABLE"
        scenarios = generate_availability_scenarios(frame)
        self.assertEqual([item.scenario_name for item in scenarios], ["P0_PLAYS", "P0_OUT"])
        self.assertEqual({item.decisions["P0"] for item in scenarios}, {"PLAY", "OUT"})

    def test_multiple_unknowns_require_user_selected_scenarios(self) -> None:
        frame = synthetic_team()
        frame["resolved_availability_status"] = "AVAILABLE"
        frame.loc[:1, "resolved_availability_status"] = "UNKNOWN"
        unresolved = generate_availability_scenarios(frame)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].scenario_name, "UNRESOLVED")
        selected = generate_availability_scenarios(
            frame, scenario_sets=[{"P0": "PLAY", "P1": "OUT"}]
        )
        self.assertFalse(selected[0].unresolved_players)

    def test_regulation_normalization_handles_overtime(self) -> None:
        actual = np.array([45, 45, 45, 45, 45, 0, 0], dtype=float)
        normalized = regulation_equivalent_minutes(actual)
        self.assertAlmostEqual(float(normalized.sum()), 200.0)
        self.assertAlmostEqual(float(normalized[0]), 40.0)

    def test_baseline_normalization_and_projection_conserve_200(self) -> None:
        baseline = normalize_baseline_minutes(np.array([50, 40, 30, 20, 15, 10, 5]))
        self.assertAlmostEqual(float(baseline.sum()), 200.0, places=7)
        self.assertTrue(np.all((baseline >= 0) & (baseline <= 40)))
        adjusted = capped_simplex_projection(baseline + np.arange(len(baseline)))
        self.assertAlmostEqual(float(adjusted.sum()), 200.0, places=7)
        self.assertTrue(np.all((adjusted >= 0) & (adjusted <= 40)))

    def test_manifest_rejects_target_outcomes_ids_and_credits(self) -> None:
        manifest = redistribution_manifest(True)
        self.assertFalse(set(manifest["features"]) & FORBIDDEN_REDISTRIBUTION_INPUTS)
        self.assertNotIn("player_id", manifest["features"])
        self.assertNotIn("fantasy_credits_pre_matchday", manifest["features"])

    def test_synthetic_target_game_injections_fail_closed(self) -> None:
        for injected in (
            "actual_minutes", "target_teammate_minutes", "target_teammate_fp",
            "target_lineup_outcome", "target_substitution_pattern",
        ):
            with self.subTest(injected=injected), self.assertRaises(ValueError):
                validate_redistribution_feature_names(
                    ["baseline_expected_minutes", injected], context="synthetic leakage test"
                )

    def test_star_absence_reallocates_exact_missing_role(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "OUT"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        self.assertAlmostEqual(output.adjusted_expected_minutes.sum(), 200.0, places=7)
        self.assertEqual(output.loc[output.player_id.eq("P0"), "adjusted_expected_minutes"].item(), 0)
        self.assertAlmostEqual(output.team_total_missing_minutes.iloc[0], 30.0)

    def test_three_small_absences_are_cumulative(self) -> None:
        frame = synthetic_deep_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        for player in ("P7", "P8", "P9"):
            decisions[player] = "OUT"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        self.assertAlmostEqual(output.team_total_missing_minutes.iloc[0], 24.0)
        self.assertAlmostEqual(output.adjusted_expected_minutes.sum(), 200.0, places=7)

    def test_near_zero_absence_has_near_zero_effect(self) -> None:
        frame = synthetic_team()
        frame.loc[7, "baseline_expected_minutes"] = 0.01
        frame.loc[:2, "baseline_expected_minutes"] += 14.99 / 3
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P7"] = "OUT"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        self.assertLess(output.team_total_missing_minutes.iloc[0], 0.02)

    def test_cross_position_is_not_hard_constrained(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "OUT"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        forward_gain = output.loc[
            output.broad_position.eq("FORWARD"), "minutes_delta_due_to_absences"
        ]
        self.assertTrue((forward_gain > 0).any())

    def test_limited_without_explicit_limit_does_not_reduce_minutes(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "LIMITED"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        self.assertAlmostEqual(
            output.loc[output.player_id.eq("P0"), "adjusted_expected_minutes"].item(),
            output.loc[output.player_id.eq("P0"), "baseline_expected_minutes"].item(),
            places=6,
        )

    def test_explicit_max_minute_limit_is_honored(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "LIMITED"
        limit = RoleLimit("P0", "MAX_MINUTES", 20.0)
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, role_limits={"P0": limit},
                model_root=Path(directory) / "no-model",
            )
        self.assertLessEqual(
            output.loc[output.player_id.eq("P0"), "adjusted_expected_minutes"].item(),
            20.0 + 1e-8,
        )
        self.assertAlmostEqual(output.adjusted_expected_minutes.sum(), 200.0, places=7)

    def test_frozen_hybrid_propagation_changes_only_decomposed_component(self) -> None:
        frame = synthetic_team()
        frame["direct_fp"] = 20.0
        frame["predicted_fp_per_min"] = 0.8
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "OUT"
        with TemporaryDirectory() as directory:
            output = redistribute_live_team(
                frame, decisions, model_root=Path(directory) / "no-model"
            )
        played = output[output.scenario_decision.eq("PLAY")].iloc[0]
        expected = 0.25 * 20 + 0.75 * played.adjusted_expected_minutes * 0.8
        self.assertAlmostEqual(played.expected_fp_after_absence_adjustment, expected)
        self.assertEqual(output.loc[output.player_id.eq("P0"),
                                    "expected_fp_after_absence_adjustment"].item(), 0)
        self.assertTrue(output.lower_prediction_interval.isna().all())
        self.assertTrue(output.upper_prediction_interval.isna().all())
        self.assertTrue(output.interval_calibration_status.eq(
            "NOT_RECALIBRATED_AFTER_AVAILABILITY_ADJUSTMENT"
        ).all())

    def test_no_absence_does_not_create_role_changes(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        output = redistribute_live_team(frame, decisions)
        np.testing.assert_allclose(
            output.adjusted_expected_minutes,
            output.baseline_expected_minutes,
            atol=1e-9,
        )

    def test_unchanged_inputs_are_reproducible(self) -> None:
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "OUT"
        with TemporaryDirectory() as directory:
            root = Path(directory) / "no-model"
            first = redistribute_live_team(frame, decisions, model_root=root)
            second = redistribute_live_team(frame, decisions, model_root=root)
        np.testing.assert_allclose(
            first.adjusted_expected_minutes, second.adjusted_expected_minutes
        )


class Phase5BDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.duckdb"
        initialize_database(self.database)
        self.player_id, self.team_id, self.game_id = seed_minimal_database(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_play_out_unknown_and_limited_use_scoped_override_backend(self) -> None:
        for decision, expected in (
            ("PLAY", "AVAILABLE"), ("OUT", "OUT"),
            ("UNKNOWN", "UNKNOWN"), ("LIMITED", "LIMITED"),
        ):
            now = datetime.now(UTC)
            apply_user_decision(
                self.player_id, decision, database_path=self.database,
                season_code="E2026", game_id=self.game_id,
                expires_at=now + timedelta(days=1),
            )
            as_of = datetime.now(UTC)
            resolved = AvailabilityResolver(self.database).resolve(
                self.player_id, season_code="E2026", game_id=self.game_id,
                matchday_number=1, tip_time=as_of + timedelta(hours=6), as_of=as_of,
            )
            self.assertEqual(resolved.status, expected)
            self.assertTrue(resolved.manual_override_active)

    def test_role_limit_is_append_only_scoped_and_clearable(self) -> None:
        now = datetime(2026, 9, 1, 12, tzinfo=UTC)
        set_role_limit(
            self.player_id, "MAX_MINUTES", 18, database_path=self.database,
            season_code="E2026", game_id=self.game_id,
            expires_at=now + timedelta(hours=12), created_at=now,
        )
        active = resolve_role_limits(
            [self.player_id], database_path=self.database, season_code="E2026",
            game_id=self.game_id, fantasy_matchday=1, as_of=now + timedelta(hours=1),
        )
        self.assertEqual(active[self.player_id].limit_value, 18)
        clear_role_limit(
            self.player_id, database_path=self.database, season_code="E2026",
            game_id=self.game_id, created_at=now + timedelta(hours=2),
        )
        cleared = resolve_role_limits(
            [self.player_id], database_path=self.database, season_code="E2026",
            game_id=self.game_id, fantasy_matchday=1, as_of=now + timedelta(hours=3),
        )
        self.assertEqual(cleared, {})
        with connect_database(self.database, read_only=True) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM role_limit_override_events"
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM player_game_stats"
            ).fetchone()[0], 0)


class Phase5BCanonicalArtifactTests(unittest.TestCase):
    def test_frozen_redistribution_model_serves_a_known_absence_scenario(self) -> None:
        manifest = Path("data/derived/phase5b/frozen_redistribution/manifest.json")
        if not manifest.is_file():
            self.skipTest("Phase 5B redistribution model has not been frozen")
        frame = synthetic_team()
        decisions = {player: "PLAY" for player in frame.player_id}
        decisions["P0"] = "OUT"
        output = redistribute_live_team(frame, decisions)
        self.assertTrue(np.isfinite(output.adjusted_expected_minutes).all())
        self.assertAlmostEqual(output.adjusted_expected_minutes.sum(), 200.0, places=7)
        self.assertTrue(output.adjusted_expected_minutes.between(0, 40).all())

    def test_historical_artifact_is_point_in_time_and_regulation_normalized(self) -> None:
        path = Path("data/derived/phase5b/phase5b_normalized_team_rotations.parquet")
        event_path = Path("data/derived/phase5b/phase5b_absence_recipient_rows.parquet")
        if not path.is_file() or not event_path.is_file():
            self.skipTest("Phase 5B historical artifact has not been built")
        connection = duckdb.connect()
        try:
            violations = connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE (player_history_max_game_time IS NOT NULL
                       AND player_history_max_game_time >= target_game_time)
                   OR (rotation_history_max_time IS NOT NULL
                       AND rotation_history_max_time >= target_game_time)
                """,
                [str(path)],
            ).fetchone()[0]
            team_error = connection.execute(
                """
                SELECT max(abs(actual_total-200)), max(abs(baseline_total-200)) FROM (
                  SELECT game_id, team_id,
                         sum(actual_regulation_minutes) actual_total,
                         sum(baseline_expected_minutes) baseline_total
                  FROM read_parquet(?) GROUP BY game_id, team_id
                )
                """,
                [str(path)],
            ).fetchone()
            relationship_violations = connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE relationship_history_max_time IS NOT NULL
                  AND relationship_history_max_time >= target_game_time
                """,
                [str(event_path)],
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(violations, 0)
        self.assertLess(team_error[0], 1e-6)
        self.assertLess(team_error[1], 1e-6)
        self.assertEqual(relationship_violations, 0)


def synthetic_team() -> pd.DataFrame:
    baseline = [30, 30, 30, 28, 25, 22, 20, 15]
    positions = ["GUARD", "GUARD", "FORWARD", "FORWARD", "CENTER", "CENTER",
                 "GUARD", "FORWARD"]
    return pd.DataFrame({
        "player_id": [f"P{index}" for index in range(len(baseline))],
        "baseline_expected_minutes": np.asarray(baseline, dtype=float),
        "broad_position": positions,
        "resolved_availability_status": "AVAILABLE",
        "season": "E2026", "home_away": "home", "team_id": "T",
        "opponent_team_id": "O",
    })


def synthetic_deep_team() -> pd.DataFrame:
    baseline = [30, 30, 28, 25, 22, 20, 15, 9, 8, 7, 6]
    return pd.DataFrame({
        "player_id": [f"P{index}" for index in range(len(baseline))],
        "baseline_expected_minutes": np.asarray(baseline, dtype=float),
        "broad_position": ["GUARD", "GUARD", "FORWARD", "FORWARD", "CENTER",
                           "CENTER", "GUARD", "FORWARD", "CENTER", "GUARD", "FORWARD"],
        "resolved_availability_status": "AVAILABLE",
        "season": "E2026", "home_away": "away", "team_id": "T",
        "opponent_team_id": "O",
    })
