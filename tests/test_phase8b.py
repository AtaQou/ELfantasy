from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from src.control_center.advisor import build_after_turn_advice
from src.control_center.monitoring import monitoring_report
from src.control_center.price import (
    DEFAULT_ARTIFACT_PATH,
    PRICE_FEATURES,
    build_price_training_frame,
    load_price_artifact,
    predict_price_rows,
)
from src.control_center.repository import ControlCenterRepository
from src.control_center.service import ControlCenterService
from src.control_center.shadow import attach_completed_turn_results
from src.control_center.state import rank_players
from src.db.database import connect_database, initialize_database
from src.db.integrity import validate_database
from src.strategy.artifact import validate_phase7_artifact
from src.strategy.rules import Lineup, load_rules, validate_turn_transition
from tests.test_phase7 import _pools
from tests.test_phase5a import seed_minimal_database


def _advisor_fixture() -> tuple[dict, dict, Lineup]:
    players, coaches = _pools()
    selected = players[
        players.player_id.isin([
            "GUARD_0", "GUARD_1", "GUARD_2", "GUARD_3",
            "FORWARD_0", "FORWARD_1", "FORWARD_2", "FORWARD_3",
            "CENTER_0", "CENTER_1",
        ])
    ].copy()
    turn_one = {"GUARD_0", "GUARD_2", "FORWARD_0", "FORWARD_2", "CENTER_0"}
    selected["turn"] = selected.player_id.map(lambda value: 1 if value in turn_one else 2)
    selected["expected_minutes"] = 26.0
    selected["local_game_date"] = selected.turn.map({1: "2025-01-01", 2: "2025-01-02"})
    # Upcoming players are deliberately stronger, so legal cross-formation recourse is useful.
    selected.loc[selected.turn.eq(2), "expected_fp"] += 12.0
    for quantile, delta in (("p10_fp", -7), ("p25_fp", -4), ("p50_fp", 0),
                            ("p75_fp", 4), ("p90_fp", 7), ("p95_fp", 9)):
        selected[quantile] = selected.expected_fp + delta
    coach = coaches.iloc[0].to_dict()
    coach.update({"game_id": "GAME_COACH", "turn": 2})
    current = Lineup(
        tuple(selected.player_id.astype(str)), str(coach["coach_id"]),
        frozenset({"GUARD_0", "GUARD_2", "GUARD_3", "FORWARD_0", "CENTER_0"}),
        "FORWARD_2", "GUARD_0",
    )
    records = []
    for row in selected.itertuples(index=False):
        records.append({
            "player_id": str(row.player_id), "entity_id": str(row.entity_id),
            "name": str(row.name), "team_id": str(row.team_id), "position": str(row.position),
            "credits": float(row.credits), "turn": int(row.turn), "game_id": str(row.game_id),
            "role": "STARTER" if row.player_id in current.starters else
                    "SIXTH_MAN" if row.player_id == current.sixth_man else "BENCH",
            "captain": row.player_id == current.captain,
            "expected_fp": float(row.expected_fp), "expected_minutes": 26.0,
            "p10": float(row.p10_fp), "p50": float(row.p50_fp),
            "p90": float(row.p90_fp), "p95": float(row.p95_fp),
            "downside_probability": float(row.prob_fp_le_15),
            "upside_probability": float(row.prob_fp_ge_30),
        })
    recommendation = {
        "label": "BEST_OVERALL", "players": records,
        "coach": {"coach_id": str(coach["coach_id"]), "entity_id": str(coach["entity_id"]),
                  "name": str(coach["name"]), "team_id": str(coach["team_id"]),
                  "credits": 5.0, "turn": 2, "expected_score": 0.0,
                  "game_id": "GAME_COACH"},
        "starting_five": sorted(current.starters), "sixth_man": current.sixth_man,
        "captain": current.captain, "expected_final_score": 100.0,
        "transfers_required": 0,
    }
    snapshot = {
        "snapshot_fingerprint": "a" * 64, "recommendations": [recommendation],
        "knowledge": {"decision_inputs": {
            "players": selected.to_dict("records"), "coaches": [coach],
        }},
    }
    observed = {player: 2.0 for player in turn_one}
    outcome = {
        "completed_turn": 1, "observed_scores": {"players": observed, "coach": None},
        "outcome_fingerprint": "b" * 64, "turn_outcome_id": "OUTCOME",
    }
    return snapshot, outcome, current


def _insert_prediction_run(database: Path) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """
            INSERT INTO live_prediction_runs (
              prediction_run_id, season_code, run_mode, status, prediction_generated_at,
              feature_cutoff_time, season_matchday, matchday_resolution_status,
              refresh_requested, input_fingerprint, run_fingerprint,
              predictive_model_version, predictive_artifact_fingerprint,
              feature_manifest_fingerprint, market_snapshot_fingerprint,
              availability_state_json, scenario_definitions_json, source_freshness_json,
              coverage_json, timing_json, warnings_json
            ) VALUES (
              'PRED', 'E2025', 'LIVE', 'SUCCEEDED', ?, ?, 1, 'MATCHDAY_RESOLVED',
              false, ?, ?, 'phase6c_predictive_uplift_frozen_v1', ?, ?, ?,
              '{}', '{}', '{}', '{}', '{}', '[]'
            )
            """,
            [datetime(2025, 1, 1, 9, tzinfo=UTC), datetime(2025, 1, 1, 9, tzinfo=UTC),
             "1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64],
        )


def _prelock_payload(repository: ControlCenterRepository) -> dict:
    payload = {
        "status": "SUCCEEDED", "version": "phase8b_live_advisor_v1",
        "profile_id": "default", "season": "E2025", "matchday": 1,
        "generated_at": "2025-01-01T10:00:00+00:00",
        "snapshot": {"market_fingerprint": "4" * 64,
                     "prediction_fingerprint": "2" * 64, "prediction_run_id": "PRED",
                     "ruleset_version": "elfc_classic_e2025_v1",
                     "rules_fingerprint": "6" * 64,
                     "manual_override_fingerprint": "7" * 64},
        "manual_overrides": [], "current_roster": [], "bank_credits": 0.0,
        "transfers_available": 3, "constraints": {}, "recommendations": [{"label": "A"}],
        "simulation": {"seed": 8}, "input_fingerprint": "8" * 64,
    }
    payload["control_center_run_id"] = repository.persist_run(payload, snapshot_path=None)
    return payload


def test_phase8b_migration_adds_append_only_advisor_and_price_tables() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8b.duckdb"
        initialize_database(database)
        with connect_database(database, read_only=True) as connection:
            version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            tables = set(connection.execute(
                "SELECT table_name FROM information_schema.tables"
            ).df().table_name)
    assert version == 14
    assert {"fantasy_shadow_prelock_snapshots", "fantasy_shadow_turn_outcomes",
            "fantasy_turn_advisor_runs", "fantasy_shadow_matchday_evaluations",
            "fantasy_price_prediction_snapshots", "fantasy_price_prediction_outcomes"}.issubset(tables)


def test_phase8b_prelock_snapshot_is_immutable_and_outcomes_are_separate() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8b.duckdb"
        repository = ControlCenterRepository(database); _insert_prediction_run(database)
        payload = _prelock_payload(repository)
        stored = repository.persist_prelock_snapshot(
            payload, scenario_id="BASE", decision_cutoff_at=datetime(2025, 1, 1, 18, tzinfo=UTC),
            decision_inputs={"players": [], "coaches": []},
        )
        original = repository.shadow_snapshot(stored["shadow_snapshot_id"])
        payload["recommendations"] = [{"label": "CHANGED_AFTER_OUTCOME"}]
        with unittest.TestCase().assertRaisesRegex(ValueError, "immutable"):
            repository.persist_prelock_snapshot(
                payload, scenario_id="BASE",
                decision_cutoff_at=datetime(2025, 1, 1, 18, tzinfo=UTC),
                decision_inputs={"players": [], "coaches": []},
            )
        repository.persist_turn_outcome(
            stored["shadow_snapshot_id"], 1, {"players": {"P": 11.0}},
            [{"turn": 1, "game_id": "G", "played": True}], prediction_run_id="PRED",
            attached_at=datetime(2025, 1, 2, tzinfo=UTC),
        )
        after = repository.shadow_snapshot(stored["shadow_snapshot_id"])
    assert original["snapshot_fingerprint"] == after["snapshot_fingerprint"]
    assert original["recommendations"] == after["recommendations"]


def test_phase8b_turn_outcome_cannot_be_rewritten() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8b.duckdb"
        repository = ControlCenterRepository(database); _insert_prediction_run(database)
        payload = _prelock_payload(repository)
        shadow = repository.persist_prelock_snapshot(
            payload, scenario_id="BASE", decision_cutoff_at=datetime(2025, 1, 1, 18, tzinfo=UTC),
            decision_inputs={"players": [], "coaches": []},
        )
        kwargs = dict(completed_games=[{"game_id": "G"}], prediction_run_id="PRED",
                      attached_at=datetime(2025, 1, 2, tzinfo=UTC))
        repository.persist_turn_outcome(shadow["shadow_snapshot_id"], 1,
                                        {"players": {"P": 8.0}}, **kwargs)
        with unittest.TestCase().assertRaisesRegex(ValueError, "immutable"):
            repository.persist_turn_outcome(shadow["shadow_snapshot_id"], 1,
                                            {"players": {"P": 9.0}}, **kwargs)


def test_phase8b_completed_live_result_is_automatically_attached_without_future_rows() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8b.duckdb"; initialize_database(database)
        player_id, team_id, game_id = seed_minimal_database(database)
        _insert_prediction_run(database)
        with connect_database(database) as connection:
            connection.execute(
                """CREATE VIEW ml_player_game_targets_v1 AS
                   SELECT cast(NULL AS VARCHAR) AS player_game_id,
                          cast(NULL AS VARCHAR) AS game_id,
                          cast(NULL AS VARCHAR) AS player_id,
                          cast(NULL AS DOUBLE) AS actual_fantasy_points,
                          cast(NULL AS DOUBLE) AS target_minutes
                   WHERE false"""
            )
            connection.execute(
                "UPDATE games SET played=true, home_score=80, away_score=70 WHERE canonical_game_id=?",
                [game_id],
            )
            connection.execute(
                """INSERT INTO live_prediction_scenarios VALUES (
                     'SCENARIO', 'PRED', 'BASE', 'GENERATED', '{}', '{}', '[]', '[]',
                     '{}', ?, ?
                   )""", ["9" * 64, datetime(2025, 1, 1, 9, tzinfo=UTC)],
            )
            connection.execute(
                """
                INSERT INTO live_player_predictions (
                  live_prediction_id, prediction_run_id, prediction_scenario_id, scenario_id,
                  prediction_generated_at, season_code, fantasy_matchday, canonical_game_id,
                  scheduled_tip_time, canonical_player_id, player_name, canonical_team_id,
                  resolved_availability, availability_decision_source,
                  adjusted_expected_minutes, expected_fp, interval_calibration_status,
                  cold_start_flag, feature_completeness, freshness_status, prediction_status,
                  missing_role_status, feature_manifest_fingerprint,
                  feature_snapshot_fingerprint, feature_snapshot_json, provenance_json,
                  explanation_json, row_fingerprint
                ) VALUES (
                  'LIVE_PLAYER', 'PRED', 'SCENARIO', 'BASE', ?, 'E2025', 1, ?, ?, ?,
                  'Test Player', ?, 'PLAY', 'SOURCE', 25, 18, 'CALIBRATED', false, 1,
                  'FRESH', 'SCORED', 'NOT_APPLICABLE', ?, ?, '{}', '{}', '{}', ?
                )
                """,
                [datetime(2025, 1, 1, 9, tzinfo=UTC), game_id,
                 datetime(2026, 10, 1, 18, tzinfo=UTC), player_id, team_id,
                 "3" * 64, "a" * 64, "b" * 64],
            )
            connection.execute(
                "INSERT INTO live_prediction_outcomes VALUES "
                "('OUT', 'LIVE_PLAYER', 11.4, 22, 'CANONICAL_VERIFIED_TARGET', NULL, ?, ?)",
                [datetime(2026, 10, 1, 21, tzinfo=UTC), "c" * 64],
            )
        repository = ControlCenterRepository(database)
        payload = _prelock_payload(repository)
        payload["recommendations"] = [{
            "label": "BEST_OVERALL", "players": [{
                "player_id": player_id, "name": "Test Player", "turn": 1,
                "game_id": game_id,
            }], "coach": {},
        }]
        # The control-center run is already persisted; create a new immutable run identity.
        payload["input_fingerprint"] = "d" * 64
        payload["control_center_run_id"] = repository.persist_run(payload, snapshot_path=None)
        shadow = repository.persist_prelock_snapshot(
            payload, scenario_id="BASE", decision_cutoff_at=datetime(2025, 1, 1, 18, tzinfo=UTC),
            decision_inputs={"players": [{"player_id": player_id, "turn": 1,
                                           "game_id": game_id}], "coaches": []},
        )
        attached = attach_completed_turn_results(repository, shadow["shadow_snapshot_id"])
    assert attached["status"] == "ATTACHED"
    assert attached["completed_turn"] == 1
    assert attached["observed_scores"]["players"] == {player_id: 11.4}


def test_phase8b_after_turn_advisor_uses_only_unplayed_promotions() -> None:
    snapshot, outcome, current = _advisor_fixture()
    advice = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=64, seed=18)
    played = set(outcome["observed_scores"]["players"])
    promoted = {action["player_id"] for action in advice["actions"]
                if action["action"] == "MOVE_TO_FIELD"}
    assert promoted
    assert not promoted & played
    validate_turn_transition(current, Lineup(
        tuple(advice["recommended_lineup"]["player_ids"]),
        advice["recommended_lineup"]["coach_id"],
        frozenset(advice["recommended_lineup"]["starters"]),
        advice["recommended_lineup"]["sixth_man"], advice["recommended_lineup"]["captain"],
    ), played, load_rules())


def test_phase8b_cross_position_switches_and_full_formation_reoptimization_work() -> None:
    snapshot, outcome, _ = _advisor_fixture()
    advice = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=64, seed=19)
    assert any(row["cross_position"] for row in advice["evidence"]["player_switches"])
    assert len(advice["recommended_lineup"]["starters"]) == 5


def test_phase8b_switch_probabilities_and_keep_switch_team_values_are_exposed() -> None:
    snapshot, outcome, _ = _advisor_fixture()
    advice = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=64, seed=20)
    comparison = advice["evidence"]["full_team_keep_vs_switch"]
    assert comparison["expected_final_team_score_keep"] is not None
    assert comparison["expected_final_team_score_switch"] is not None
    assert 0 <= comparison["probability_switch_final_score_exceeds_keep"] <= 1
    assert all(0 <= row["probability_new_player_exceeds_realized_player"] <= 1
               for row in advice["evidence"]["player_switches"])


def test_phase8b_sixth_man_is_frozen_and_bench_effect_is_reported() -> None:
    snapshot, outcome, current = _advisor_fixture()
    advice = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=48, seed=21)
    assert advice["recommended_lineup"]["sixth_man"] == current.sixth_man
    assert all(row["sixth_man_effect"] == "UNCHANGED_AND_FROZEN"
               for row in advice["evidence"]["player_switches"])


def test_phase8b_captain_keep_switch_has_statistical_reasons() -> None:
    snapshot, outcome, _ = _advisor_fixture()
    captain = build_after_turn_advice(
        snapshot, outcome, load_rules(), simulations=64, seed=22
    )["evidence"]["captain"]
    assert captain["recommendation"] in {"KEEP_CAPTAIN", "SWITCH_CAPTAIN"}
    assert len(captain["statistical_reasons"]) == 2
    assert 0 <= captain["probability_switch_produces_better_final_result"] <= 1
    assert {"expected_fp", "p10", "p50", "p90", "upside_probabilities"}.issubset(
        captain["candidate_statistics"]
    )


def test_phase8b_advisor_is_reproducible() -> None:
    snapshot, outcome, _ = _advisor_fixture()
    first = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=48, seed=23)
    second = build_after_turn_advice(snapshot, outcome, load_rules(), simulations=48, seed=23)
    assert first == second


def test_phase8b_turn_optionality_is_visible_but_not_forced() -> None:
    snapshot, outcome, _ = _advisor_fixture()
    portfolio = build_after_turn_advice(
        snapshot, outcome, load_rules(), simulations=32, seed=24
    )["evidence"]["turn_portfolio"]
    assert portfolio["players_by_turn"] == {"1": 5, "2": 5}
    assert portfolio["later_replacements"]
    assert portfolio["turn_diversification_forced"] is False


def test_phase8b_reevaluation_refreshes_only_turn_results() -> None:
    calls: list[dict] = []
    shadow_calls: list[dict] = []

    class Repository:
        @staticmethod
        def load_state(profile_id: str):
            from src.control_center.state import ControlCenterState
            return ControlCenterState(
                profile_id=profile_id, season_code="E2026", fantasy_matchday=1,
                roster_entity_ids=("PLAYER", "COACH"),
            )

        @staticmethod
        def latest_shadow_snapshot(profile_id: str, **filters):
            shadow_calls.append({"profile_id": profile_id, **filters})
            return None

        @staticmethod
        def market_entities(season: str, matchday: int):
            return []

    service = object.__new__(ControlCenterService)
    service.database_path = Path("unused.duckdb")
    service.profile_id = "default"
    service.repository = Repository()
    service.rules = load_rules()
    service.turn_update_runner = lambda season, **kwargs: calls.append(
        {"season": season, **kwargs}
    ) or {"warnings": (), "completed_games_ingested": 1}
    service._state_with_matchday = lambda state: state

    result = service.reevaluate_strategy(refresh_first=True)

    assert result["status"] == "BLOCKED"
    assert result["refresh"]["mode"] == "TURN_RESULTS_ONLY"
    assert calls == [{
        "season": "E2026",
        "database_path": Path("unused.duckdb"),
        "include_rich": False,
    }]
    assert shadow_calls == [{
        "profile_id": "default", "season": "E2026", "matchday": 1,
        "roster_entity_ids": ("PLAYER", "COACH"),
    }]


def test_phase8b_current_roster_turn_analysis_shows_actual_fp_and_conditional_swap() -> None:
    from src.control_center.state import ControlCenterState

    positions = ["Guard"] * 4 + ["Forward"] * 4 + ["Center"] * 2
    market = [
        {
            "entity_id": f"E{index}", "entity_type": "PLAYER",
            "player_id": f"P{index}", "name": f"Player {index}",
            "team_id": f"T{index}", "team_name": f"Team {index}",
            "position": position, "source_turn": 1 if index in {0, 4, 8} else 2,
        }
        for index, position in enumerate(positions)
    ]
    market.append({
        "entity_id": "COACH", "entity_type": "COACH", "name": "Coach",
        "team_id": "TC", "position": "Head Coach", "source_turn": 1,
    })
    predictions = [
        {
            "entity_id": f"E{index}", "player_id": f"P{index}",
            "expected_fp": 20.0 - index, "expected_minutes": 25.0,
            "p10_fp": 8.0, "p50_fp": 18.0, "p90_fp": 30.0,
        }
        for index in range(10) if index not in {0, 4, 8}
    ]
    outcomes = {
        "P0": {"played": True, "actual_fp": 2.0, "actual_minutes": 18.0},
        "P4": {"played": True, "actual_fp": 25.0, "actual_minutes": 28.0},
        "P8": {"played": True, "actual_fp": 12.0, "actual_minutes": 22.0},
    }

    class Repository:
        @staticmethod
        def market_entities(season: str, matchday: int):
            return market

        @staticmethod
        def current_roster_game_outcomes(season: str, matchday: int, players):
            return outcomes

    service = object.__new__(ControlCenterService)
    service.profile_id = "default"
    service.repository = Repository()
    service.rules = load_rules()
    service.players = lambda state: predictions
    state = ControlCenterState(
        season_code="E2026", fantasy_matchday=1,
        roster_entity_ids=tuple([f"E{index}" for index in range(10)] + ["COACH"]),
    )

    result = service._current_roster_turn_analysis(state, None)

    assert result["status"] == "CONDITIONAL_READY"
    assert result["completed_turn"] == 1
    assert next(row for row in result["played_players"] if row["player_id"] == "P0")[
        "actual_fp"
    ] == 2.0
    assert any(
        row["played_player_id"] == "P0" and row["new_player_id"] == "P1"
        for row in result["conditional_swaps"]
    )


def test_phase8b_live_turn_strategy_resolves_each_saved_team_profile_independently() -> None:
    from src.control_center.state import ControlCenterState

    market = [
        {
            "entity_id": f"E{index}", "entity_type": "PLAYER",
            "player_id": f"P{index}", "name": f"Player {index}",
            "team_id": f"T{index}", "position": (
                "Guard" if index < 4 else "Forward" if index < 8 else "Center"
            ), "source_turn": 1,
        }
        for index in range(10)
    ] + [{
        "entity_id": "COACH", "entity_type": "COACH", "name": "Coach",
        "team_id": "TC", "position": "Head Coach", "source_turn": 1,
    }]

    class Repository:
        @staticmethod
        def market_entities(season: str, matchday: int):
            return market

        @staticmethod
        def current_roster_game_outcomes(season: str, matchday: int, players):
            return {
                row["player_id"]: {"played": True, "actual_fp": 10.0}
                for row in players
            }

    service = object.__new__(ControlCenterService)
    service.repository = Repository()
    service.rules = load_rules()
    service.players = lambda state: []
    roster = tuple([f"E{index}" for index in range(10)] + ["COACH"])

    for profile_id in ("default", "default:team-2", "default:team-3"):
        state = ControlCenterState(
            profile_id=profile_id, season_code="E2026", fantasy_matchday=1,
            roster_entity_ids=roster,
        )
        result = service._current_roster_turn_analysis(state, None)
        assert result["status"] == "CONDITIONAL_READY"
        assert len(result["played_players"]) == 10


def test_phase8b_monitoring_is_read_only() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8b.duckdb"; initialize_database(database)
        before = database.read_bytes()
        report = monitoring_report(database)
        after = database.read_bytes()
    assert report["mode"] == "READ_ONLY_DIAGNOSTIC"
    assert report["optimization_modified"] is False
    assert before == after


def test_phase8b_price_history_is_e2025_point_in_time_and_one_step_ahead() -> None:
    frame = build_price_training_frame()
    assert len(frame) == 11445
    assert set(frame["season_code"]) == {"E2025"}
    assert (frame["next_matchday"] == frame["matchday_number"] + 1).all()
    assert not any("next" in feature or feature == "actual_fp" for feature in PRICE_FEATURES)


def test_phase8b_price_model_has_chronological_rmse_value_but_is_secondary() -> None:
    artifact = load_price_artifact()
    assert artifact is not None
    assert artifact["acceptance_status"] == "ACCEPTED_SECONDARY_RMSE_ONLY"
    assert artifact["validation"]["rmse_fold_wins"] == 3
    assert artifact["validation"]["pooled"]["ridge"]["rmse"] < (
        artifact["validation"]["pooled"]["no_change"]["rmse"]
    )
    assert artifact["validation"]["pooled"]["ridge"]["mae"] > (
        artifact["validation"]["pooled"]["no_change"]["mae"]
    )
    assert artifact["primary_phase7_objective_changed"] is False


def test_phase8b_price_predictions_include_direction_probabilities() -> None:
    artifact = load_price_artifact(); assert artifact is not None
    row = {feature: 0.0 for feature in PRICE_FEATURES}
    row.update({"entity_id": "E", "player_id": "P", "credits": 10.0,
                "expected_fp": 20.0, "expected_minutes": 25.0, "matchday_number": 10})
    predicted = predict_price_rows([row], artifact)[0]
    assert np.isfinite(predicted["expected_next_price"])
    assert 0 <= predicted["probability_increase"] <= 1
    assert 0 <= predicted["probability_decrease"] <= 1
    assert predicted["price_primary_objective"] is False


def test_phase8b_value_growth_view_requires_competitive_fp_value() -> None:
    rows = [
        {"name": "Good FP", "value_growth_score": 2.1, "expected_fp": 22},
        {"name": "Mediocre FP", "value_growth_score": None, "expected_fp": 8},
    ]
    assert rank_players(rows, "VALUE_CREDIT_GROWTH")[0]["name"] == "Good FP"


def test_phase8b_ui_contains_live_advisor_price_detail_and_monitoring() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    assert "Update Turn Results / Re-evaluate Strategy" in html
    assert "VALUE CREDIT GROWTH" in html
    assert "READ-ONLY DIAGNOSTICS" in html
    assert "probability_new_player_exceeds_realized_player" in script
    assert "expected_final_team_score_keep" in script
    assert "shadow.knowledge?.current_roster" in script
    assert "price never changes the Phase 7 FP objective" in script


def test_phase8b_integrity_checks_pass() -> None:
    result = validate_database()
    phase8b = [row for row in result["checks"] if row["name"].startswith("phase8b_")]
    assert len(phase8b) == 8
    assert all(row["passed"] for row in phase8b), phase8b


def test_phase8b_does_not_modify_frozen_phase7_artifact() -> None:
    assert validate_phase7_artifact()["strategy_model_version"] == (
        "phase7_dynamic_strategy_engine_frozen_v1"
    )
    assert DEFAULT_ARTIFACT_PATH.is_file()


class Phase8BTests(unittest.TestCase):
    """Expose concise Phase 8B function tests to the repository unittest runner."""


_PHASE8B_TEST_FUNCTIONS = {
    name: value for name, value in tuple(globals().items())
    if name.startswith("test_phase8b_") and callable(value)
}
for _test_name, _test_function in _PHASE8B_TEST_FUNCTIONS.items():
    setattr(Phase8BTests, _test_name, staticmethod(_test_function))
del _test_name, _test_function, _PHASE8B_TEST_FUNCTIONS
