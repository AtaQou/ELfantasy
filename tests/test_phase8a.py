from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import pandas as pd

from src.control_center.http import create_server
from src.control_center.recommendations import (
    _rotation_layout_shortlist,
    generate_recommendations,
    validate_current_team,
)
from src.control_center.repository import ControlCenterRepository
from src.control_center.service import ControlCenterService
from src.control_center.state import (
    ControlCenterState,
    availability_presentation,
    rank_players,
)
from src.live.rules_compatibility import resolve_ruleset_compatibility
from src.db.database import connect_database, initialize_database
from src.strategy.optimizer import optimize_deterministic
from src.strategy.rules import RuleViolation, load_rules
from tests.test_phase5a import seed_minimal_database
from tests.test_phase7 import _pools


def _current_state(*, transfers: int = 3, bank: float = 10.0) -> tuple:
    players, coaches = _pools();
    rules = load_rules()
    initial = optimize_deterministic(players, coaches, rules)
    entities = list(players[players.player_id.isin(initial.lineup.player_ids)].entity_id)
    entities += list(coaches[coaches.coach_id.eq(initial.lineup.coach_id)].entity_id)
    state = ControlCenterState(
        season_code="E2025", fantasy_matchday=1,
        roster_entity_ids=tuple(map(str, entities)), bank_credits=bank,
        transfers_available=transfers,
    )
    return players, coaches, rules, initial, state


def test_phase8a_status_groups_are_color_and_text_safe() -> None:
    assert availability_presentation("AVAILABLE") == {
        "group": "GREEN", "label": "AVAILABLE", "icon": "check-circle",
    }
    assert availability_presentation("QUESTIONABLE")["group"] == "YELLOW"
    assert availability_presentation("LIMITED")["group"] == "YELLOW"
    assert availability_presentation("OUT")["group"] == "RED"


def test_phase8a_player_views_rank_the_intended_metric() -> None:
    rows = [
        {"name": "A", "expected_fp": 20, "fp_per_credit": 2, "expected_minutes": 25,
         "p90_fp": 30, "prob_fp_le_15": .4},
        {"name": "B", "expected_fp": 19, "fp_per_credit": 3, "expected_minutes": 30,
         "p90_fp": 35, "prob_fp_le_15": .1},
    ]
    assert rank_players(rows, "BEST_EXPECTED_FP")[0]["name"] == "A"
    assert rank_players(rows, "BEST_VALUE")[0]["name"] == "B"
    assert rank_players(rows, "MOST_EXPECTED_MINUTES")[0]["name"] == "B"
    assert rank_players(rows, "HIGHEST_UPSIDE")[0]["name"] == "B"
    assert rank_players(rows, "SAFEST")[0]["name"] == "B"


def test_phase8a_state_persists_roster_bank_transfers_and_constraints() -> None:
    with TemporaryDirectory() as directory:
        repository = ControlCenterRepository(Path(directory) / "phase8a.duckdb")
        value = ControlCenterState(
            profile_id="daily", season_code="E2026", fantasy_matchday=4,
            roster_entity_ids=("A", "B"), bank_credits=2.3, transfers_available=2,
            scenario_id="known", player_constraints={"A": "FORCE_INCLUDE", "C": "EXCLUDE"},
        )
        repository.save_state(value)
        loaded = repository.load_state("daily")
        assert loaded == value
        assert loaded.fingerprint == value.fingerprint


def test_control_center_advances_saved_state_to_newer_verified_market() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(
            Path(directory) / "phase8a.duckdb",
            snapshot_root=Path(directory) / "snapshots",
        )
        service.repository.latest_market_matchday = lambda _season: 2
        state = ControlCenterState(
            season_code="E2026", fantasy_matchday=1,
            roster_entity_ids=("player",), scenario_id="old-scenario",
        )
        advanced = service._state_with_matchday(state)
        assert advanced.fantasy_matchday == 2
        assert advanced.roster_entity_ids == ("player",)
        assert advanced.scenario_id is None


def test_phase8a_migration_adds_reproducible_control_center_tables() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8a.duckdb"
        initialize_database(database)
        with connect_database(database, read_only=True) as connection:
            assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 14
            columns = connection.execute("DESCRIBE fantasy_control_center_runs").df().column_name
        assert {"market_snapshot_fingerprint", "prediction_snapshot_fingerprint",
                "manual_override_fingerprint", "current_roster_json",
                "optimization_constraints_json", "recommendations_json"}.issubset(columns)


def test_phase8a_credit_calculation_uses_current_value_plus_bank() -> None:
    players, coaches, rules, _, state = _current_state(bank=7.5)
    result = validate_current_team(state, players, coaches, rules)
    assert result["legal"]
    assert result["available_budget"] == result["market_value"] + 7.5


def test_phase8a_played_current_player_is_not_mislabeled_as_missing_from_market() -> None:
    players, coaches, rules, _, state = _current_state()
    entity = next(
        value for value in state.roster_entity_ids
        if value in set(players["entity_id"].astype(str))
    )
    remaining = players[~players["entity_id"].astype(str).eq(entity)].copy()
    market = [
        {"entity_id": value, "name": (
            "Already Played" if value == entity else value
        )}
        for value in state.roster_entity_ids
    ]
    result = validate_current_team(
        state, remaining, coaches, rules, market_entities=market,
    )
    assert not result["legal"]
    assert result["missing_entity_ids"] == []
    assert result["unavailable_entity_ids"] == [entity]
    message = " ".join(result["errors"])
    assert "Already Played" in message
    assert entity not in message
    assert "Update Turn Results / Re-evaluate Strategy" in message


def test_phase8a_partial_current_team_locks_selected_and_fills_only_empty_slots() -> None:
    players, coaches, rules, _, state = _current_state()
    current = set(state.roster_entity_ids)
    selected_players = players[players["entity_id"].astype(str).isin(current)]
    removed = [
        selected_players[selected_players.position.eq(position)].iloc[0]
        for position in ("FORWARD", "CENTER")
    ]
    removed_ids = {str(row.entity_id) for row in removed}
    locked = current - removed_ids
    partial = replace(
        state, roster_entity_ids=tuple(sorted(locked)),
        bank_credits=sum(float(row.credits) for row in removed), transfers_available=2,
    )
    validation = validate_current_team(partial, players, coaches, rules)
    assert validation["legal"] and not validation["complete"]
    assert validation["missing_positions"] == {"FORWARD": 1, "CENTER": 1}
    result = generate_recommendations(
        players, coaches, rules, partial, mode="COMPLETE_ROSTER",
        training_simulations=6, evaluation_simulations=12,
        max_rosters=2, max_layouts_per_roster=2,
    )
    for recommendation in result["recommendations"]:
        selected = {row["entity_id"] for row in recommendation["players"]}
        selected.add(recommendation["coach"]["entity_id"])
        assert locked.issubset(selected)
        assert recommendation["coach"]["entity_id"] in locked
        assert recommendation["transfers_required"] == 2
        assert recommendation["credit_accounting"]["credits_spent"] <= partial.bank_credits


def test_phase8a_partial_current_team_can_fill_coach_and_reports_infeasible_budget() -> None:
    players, coaches, rules, _, state = _current_state()
    coach_id = next(entity for entity in state.roster_entity_ids if str(entity).startswith("EC_"))
    coach_credit = float(coaches.loc[
        coaches["entity_id"].astype(str).eq(str(coach_id)), "credits"
    ].iloc[0])
    without_coach = replace(
        state,
        roster_entity_ids=tuple(entity for entity in state.roster_entity_ids
                                if not str(entity).startswith("EC_")),
        bank_credits=state.bank_credits + coach_credit,
    )
    validation = validate_current_team(without_coach, players, coaches, rules)
    assert validation["legal"] and validation["missing_positions"] == {"COACH": 1}
    completed = generate_recommendations(
        players, coaches, rules, without_coach, mode="COMPLETE_ROSTER",
        training_simulations=4, evaluation_simulations=8,
        max_rosters=1, max_layouts_per_roster=1,
    )
    assert completed["recommendations"][0]["coach"]["entity_id"]

    selected_players = players[
        players["entity_id"].astype(str).isin(set(state.roster_entity_ids))
    ]
    removed_ids = {
        str(selected_players[selected_players.position.eq(position)].iloc[0].entity_id)
        for position in ("FORWARD", "CENTER")
    }
    partial = replace(
        state,
        roster_entity_ids=tuple(entity for entity in state.roster_entity_ids
                                if entity not in removed_ids),
        bank_credits=0.1, transfers_available=2,
    )
    with unittest.TestCase().assertRaisesRegex(
            RuleViolation, r"No legal 1 Forward \+ 1 Center combination fits within 0.1"):
        generate_recommendations(
            players, coaches, rules, partial, mode="COMPLETE_ROSTER",
            training_simulations=4, evaluation_simulations=8,
            max_rosters=1, max_layouts_per_roster=1,
        )


def test_phase8a_eleven_changes_can_fill_all_player_slots_around_locked_coach() -> None:
    players, coaches, rules, _, state = _current_state()
    coach_id = next(entity for entity in state.roster_entity_ids if str(entity).startswith("EC_"))
    partial = replace(
        state, roster_entity_ids=(coach_id,), bank_credits=100.0,
        transfers_available=11,
    )
    result = generate_recommendations(
        players, coaches, rules, partial, mode="COMPLETE_ROSTER",
        training_simulations=4, evaluation_simulations=8,
        max_rosters=1, max_layouts_per_roster=1,
    )
    recommendation = result["recommendations"][0]
    assert recommendation["coach"]["entity_id"] == coach_id
    assert len(recommendation["players"]) == 10
    assert recommendation["transfers_required"] == 10


def test_phase8a_build_new_team_accepts_arbitrary_total_budget() -> None:
    players, coaches, rules, _, _ = _current_state()
    state = ControlCenterState(season_code="E2026", fantasy_matchday=1)
    result = generate_recommendations(
        players, coaches, rules, state, mode="BUILD_NEW", total_budget=112.3,
        training_simulations=10, evaluation_simulations=20,
        max_rosters=3, max_layouts_per_roster=2,
    )
    best = result["recommendations"][0]
    counts = best["players"]
    assert result["optimization_mode"] == "BUILD_NEW"
    assert sum(row["position"].upper().startswith("G") for row in counts) == 4
    assert sum(row["position"].upper().startswith("F") for row in counts) == 4
    assert sum(row["position"].upper().startswith("C") for row in counts) == 2
    assert best["credits_used"] <= 112.3
    assert best["credit_accounting"]["credits_remaining"] >= 0


def test_phase8a_pre_round_optimizer_preserves_a_usable_later_turn_substitute() -> None:
    players, coaches = _pools()
    rules = load_rules()
    players["turn"] = 1
    later = players[players.position.eq("GUARD")].sort_values("expected_fp").iloc[0]
    players.loc[players.player_id.eq(later.player_id), "turn"] = 2
    state = ControlCenterState(season_code="E2026", fantasy_matchday=1)

    result = generate_recommendations(
        players, coaches, rules, state, mode="BUILD_NEW", total_budget=100.0,
        training_simulations=6, evaluation_simulations=12,
        max_rosters=2, max_layouts_per_roster=3,
    )

    for recommendation in result["recommendations"]:
        selected_later = [
            player for player in recommendation["players"] if player["turn"] == 2
        ]
        assert selected_later
        assert recommendation["rotation_options"]
        assert any(player["role"] == "BENCH" for player in selected_later)


def test_phase8a_current_team_accounting_and_at_most_three_changes() -> None:
    players, coaches, rules, _, state = _current_state(transfers=3, bank=7.5)
    result = generate_recommendations(
        players, coaches, rules, state, mode="CURRENT_TEAM",
        training_simulations=10, evaluation_simulations=20,
        max_rosters=3, max_layouts_per_roster=2,
    )
    for recommendation in result["recommendations"]:
        accounting = recommendation["credit_accounting"]
        assert recommendation["transfers_required"] <= 3
        assert accounting["credits_remaining"] >= 0
        assert abs(
            state.bank_credits + accounting["credits_received"]
            - accounting["credits_spent"] - accounting["credits_remaining"]
        ) < 1e-8
        assert recommendation["predicted_team_fp_before"] is not None
        assert recommendation["predicted_team_fp_after"] is not None


def test_phase8a_improve_team_keeps_manually_locked_players_and_coach() -> None:
    players, coaches = _pools()
    rules = load_rules()
    selected = []
    for position, count in rules.position_counts.items():
        selected.extend(
            players[players.position.eq(position)]
            .sort_values("expected_fp")
            .head(count)
            .entity_id.astype(str)
        )
    coach_id = str(coaches.sort_values("expected_score").iloc[0].entity_id)
    locked_player_id = selected[0]
    state = ControlCenterState(
        season_code="E2026", fantasy_matchday=1,
        roster_entity_ids=tuple(selected + [coach_id]),
        bank_credits=100.0, transfers_available=3,
        player_constraints={
            locked_player_id: "FORCE_INCLUDE", coach_id: "FORCE_INCLUDE",
        },
    )

    result = generate_recommendations(
        players, coaches, rules, state, mode="CURRENT_TEAM",
        training_simulations=6, evaluation_simulations=12,
        max_rosters=2, max_layouts_per_roster=2,
    )
    best = result["recommendations"][0]
    result_entities = {row["entity_id"] for row in best["players"]}

    assert result["optimization_mode"] == "CURRENT_TEAM"
    assert best["coach"]["entity_id"] == coach_id
    assert locked_player_id in result_entities
    assert 1 <= best["transfers_required"] <= 3
    assert best["players_out"] and best["players_in"]
    assert best["predicted_improvement"] > 0


def test_phase8a_e2026_rules_attestation_requires_same_fresh_market_state() -> None:
    rules = load_rules()
    fresh = {
        "fantasy_players": 325, "fantasy_snapshot_batch_id": "batch-1",
        "freshness": {"fantasy_market": {"is_stale": False}},
    }
    stale = {**fresh, "freshness": {"fantasy_market": {"is_stale": True}}}
    assert resolve_ruleset_compatibility(rules, "E2026", fresh).production_ready
    assert not resolve_ruleset_compatibility(rules, "E2026", stale).production_ready


def test_phase8a_optimize_generates_missing_prediction_before_solving() -> None:
    players, coaches, _, _, _ = _current_state()
    prediction_calls: list[str] = []
    with TemporaryDirectory() as directory:
        service = ControlCenterService(
            Path(directory) / "phase8a.duckdb",
            fantasy_config=None,
            snapshot_root=Path(directory) / "snapshots",
            prediction_runner=lambda season, **kwargs: prediction_calls.append(season) or {
                "status": "SUCCEEDED",
            },
            pool_loader=lambda *args: (players, coaches),
            recommendation_runner=lambda *args, **kwargs: {
                "recommendations": [], "player_alternatives": [], "strategy": {},
                "available_budget": kwargs["total_budget"],
                "simulation": {"seed": kwargs["seed"]},
            },
        )
        state = ControlCenterState(season_code="E2026", fantasy_matchday=1)
        service.repository.load_state = lambda profile="default": state
        context = {
            "prediction_run_id": "prediction-1", "status": "SUCCEEDED",
            "scenarios": ["BASE"], "market_snapshot_fingerprint": "market-fp",
            "prediction_fingerprint": "prediction-fp",
            "predictive_artifact_fingerprint": "artifact-fp",
        }
        dashboards = iter((
            {"optimization_blocked": False, "blocked_reasons": [],
             "predictions_current": False, "prediction": None,
             "prediction_refresh_reasons": ["missing"]},
            {"optimization_blocked": False, "blocked_reasons": [],
             "predictions_current": True, "prediction": context,
             "prediction_refresh_reasons": []},
        ))
        service.dashboard = lambda current=None: next(dashboards)
        service.repository.player_predictions = lambda *args: []
        service.repository.active_overrides = lambda *args: []
        service.repository.persist_run = lambda *args, **kwargs: "run-1"
        result = service.optimize(
            mode="BUILD_NEW", total_budget=100.0,
            training_simulations=2, evaluation_simulations=2,
        )
    assert prediction_calls == ["E2026"]
    assert result["status"] == "SUCCEEDED"
    assert result["prediction_generated_on_demand"] is True


def test_phase8a_zero_transfers_preserves_all_players_and_coach() -> None:
    players, coaches, rules, _, state = _current_state(transfers=0, bank=50)
    result = generate_recommendations(
        players, coaches, rules, state, training_simulations=10,
        evaluation_simulations=20, max_rosters=3, max_layouts_per_roster=2,
    )
    current = set(state.roster_entity_ids)
    for recommendation in result["recommendations"]:
        selected = {row["entity_id"] for row in recommendation["players"]}
        selected.add(recommendation["coach"]["entity_id"])
        assert selected == current
        assert recommendation["transfers_required"] == 0


def test_phase8a_coach_change_counts_as_one_transfer() -> None:
    players, coaches, rules, initial, state = _current_state(transfers=1, bank=50)
    current_coach = coaches.loc[coaches.coach_id.eq(initial.lineup.coach_id), "entity_id"].iloc[0]
    replacement = coaches.loc[~coaches.entity_id.eq(current_coach), "entity_id"].iloc[-1]
    constrained = replace(state, player_constraints={str(replacement): "FORCE_INCLUDE"})
    result = generate_recommendations(
        players, coaches, rules, constrained, training_simulations=10,
        evaluation_simulations=20, max_rosters=2, max_layouts_per_roster=2,
    )
    assert all(item["coach"]["entity_id"] == replacement for item in result["recommendations"])
    assert all(item["transfers_required"] == 1 for item in result["recommendations"])


def test_phase8a_force_include_and_exclude_are_hard_constraints() -> None:
    players, coaches, rules, initial, state = _current_state(transfers=11, bank=100)
    unselected = players[~players.player_id.isin(initial.lineup.player_ids)].iloc[0]
    selected = players[players.player_id.isin(initial.lineup.player_ids)].iloc[0]
    constrained = replace(state, player_constraints={
        str(unselected.entity_id): "FORCE_INCLUDE", str(selected.entity_id): "EXCLUDE",
    })
    result = generate_recommendations(
        players, coaches, rules, constrained, training_simulations=10,
        evaluation_simulations=20, max_rosters=3, max_layouts_per_roster=2,
    )
    for item in result["recommendations"]:
        entities = {row["entity_id"] for row in item["players"]}
        assert str(unselected.entity_id) in entities
        assert str(selected.entity_id) not in entities


def test_phase8a_returns_three_distribution_selected_legal_teams() -> None:
    players, coaches, rules, _, state = _current_state()
    result = generate_recommendations(
        players, coaches, rules, state, training_simulations=12,
        evaluation_simulations=24, max_rosters=4, max_layouts_per_roster=3,
    )
    assert [item["label"] for item in result["recommendations"]] == [
        "BEST_OVERALL", "SAFER_ALTERNATIVE", "HIGHER_UPSIDE_ALTERNATIVE",
    ]
    for item in result["recommendations"]:
        assert len(item["players"]) == 10
        assert all(player["expected_role"] for player in item["players"])
        assert item["captain"] in item["starting_five"]
        assert item["transfers_required"] <= state.transfers_available


def test_phase8a_player_alternatives_are_full_reoptimizations() -> None:
    players, coaches, rules, _, state = _current_state()
    result = generate_recommendations(
        players, coaches, rules, state, training_simulations=12,
        evaluation_simulations=24, max_rosters=4, max_layouts_per_roster=3,
    )
    assert 1 <= len(result["player_alternatives"]) <= 2
    assert all(len(item["reoptimized_roster_fingerprint"]) == 64
               for item in result["player_alternatives"])
    assert {item["alternative_type"] for item in result["player_alternatives"]}.issubset(
        {"SAFER", "HIGHER_UPSIDE"}
    )


def test_phase8a_simulation_and_recommendations_are_reproducible() -> None:
    players, coaches, rules, _, state = _current_state()
    kwargs = dict(training_simulations=10, evaluation_simulations=20,
                  max_rosters=3, max_layouts_per_roster=2, seed=81)
    first = generate_recommendations(players, coaches, rules, state, **kwargs)
    second = generate_recommendations(players, coaches, rules, state, **kwargs)
    assert first["recommendations"] == second["recommendations"]
    assert first["simulation"] == second["simulation"]


def test_phase8a_strategy_thresholds_come_from_phase7_recourse() -> None:
    players, coaches, rules, _, state = _current_state()
    result = generate_recommendations(
        players, coaches, rules, state, training_simulations=10,
        evaluation_simulations=20, max_rosters=3, max_layouts_per_roster=3,
    )
    assert result["strategy"]["initial_captain"]
    player_names = set(players["name"].astype(str))
    for rule in result["strategy"]["decision_rules"]:
        assert rule["derivation"] == "Frozen Phase 7 expected-final-value decision boundary"
        assert any(rule["action_below_threshold"].endswith(name) for name in player_names)


def test_phase8a_rotation_shortlist_does_not_rescan_dataframe_per_lineup() -> None:
    players, coaches, rules, initial, _ = _current_state()
    roster = players[players["player_id"].isin(initial.lineup.player_ids)].copy()
    original = pd.DataFrame.itertuples
    calls = 0

    def counted(frame, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(frame, *args, **kwargs)

    with patch.object(pd.DataFrame, "itertuples", counted):
        layouts = _rotation_layout_shortlist(
            initial.lineup, roster, rules, max_layouts=7,
        )

    assert layouts
    assert calls < 20


def test_phase8a_manual_override_uses_append_only_phase5a_precedence() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8a.duckdb"
        initialize_database(database)
        player_id, _, _ = seed_minimal_database(database)
        repository = ControlCenterRepository(database)
        repository.save_state(ControlCenterState(
            season_code="E2026", fantasy_matchday=1,
        ))
        service = ControlCenterService(database, snapshot_root=Path(directory) / "snapshots")
        set_result = service.set_override(player_id, "OUT", note="local knowledge")
        overrides = repository.active_overrides("E2026", 1)
        assert set_result["override_event_id"]
        assert overrides[0]["status"] == "OUT"
        service.set_override(player_id, "UNKNOWN", clear=True)
        assert repository.active_overrides("E2026", 1) == []
        with connect_database(database, read_only=True) as connection:
            assert connection.execute(
                "SELECT count(*) FROM availability_override_events"
            ).fetchone()[0] == 2


def test_phase8a_live_refresh_preserves_blocked_rules_gate() -> None:
    calls: list[str] = []
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8a.duckdb"
        service = ControlCenterService(
            database, fantasy_config=None, snapshot_root=Path(directory) / "snapshots",
            update_runner=lambda season, **kwargs: calls.append(season) or {"status": "SUCCEEDED"},
            prediction_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("blocked E2026 prediction must not run")
            ),
        )
        result = service.refresh()
    assert calls == ["E2026"]
    assert result["prediction"] is None
    assert result["dashboard"]["rules_gate"]["passed"] is False


def test_phase8a_blocked_optimization_is_persisted_without_bypass() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase8a.duckdb"
        service = ControlCenterService(database, snapshot_root=Path(directory) / "snapshots")
        result = service.optimize()
        latest = service.repository.latest_run()
    assert result["status"] == "BLOCKED"
    assert latest is not None and latest["status"] == "BLOCKED"
    assert any("E2026" in value for value in result["blocked_reasons"])


def test_phase8a_snapshot_identity_ignores_wall_clock_time() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(
            Path(directory) / "phase8a.duckdb", snapshot_root=Path(directory) / "snapshots"
        )
        first = {"status": "SUCCEEDED", "generated_at": "first", "value": 1}
        second = {"status": "SUCCEEDED", "generated_at": "second", "value": 1}
        assert service._write_snapshot(first) == service._write_snapshot(second)


def test_phase8a_http_backend_serves_ui_and_json_integration() -> None:
    class FakeService:
        profile_id = "default"
        repository = type("Repository", (), {"latest_run": lambda self, profile: None})()

        @staticmethod
        def bootstrap() -> dict:
            return {"version": "phase8a_control_center_v1", "players": [], "metric": float("nan")}

        @staticmethod
        def dashboard() -> dict:
            return {"optimization_blocked": True}

        @staticmethod
        def players(**kwargs) -> list:
            return []

    with TemporaryDirectory() as directory:
        root = Path(directory);
        (root / "index.html").write_text("<h1>Control Center</h1>")
        server = create_server(FakeService(), port=0, static_root=root)
        thread = threading.Thread(target=server.serve_forever, daemon=True);
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            assert "Control Center" in urlopen(base, timeout=5).read().decode()
            payload = json.loads(urlopen(base + "/api/bootstrap", timeout=5).read())
            assert payload["version"] == "phase8a_control_center_v1"
            assert payload["metric"] is None
        finally:
            server.shutdown();
            server.server_close();
            thread.join(timeout=5)


class Phase8ATests(unittest.TestCase):
    """Expose concise Phase 8A function tests to the repository unittest runner."""


_PHASE8A_TEST_FUNCTIONS = {
    name: value for name, value in tuple(globals().items())
    if name.startswith("test_phase8a_") and callable(value)
}
for _test_name, _test_function in _PHASE8A_TEST_FUNCTIONS.items():
    setattr(Phase8ATests, _test_name, staticmethod(_test_function))
del _test_name, _test_function, _PHASE8A_TEST_FUNCTIONS
