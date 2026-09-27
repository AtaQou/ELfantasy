from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.request import Request, urlopen

from src.control_center.demo import (
    DEMO_SCENARIOS,
    DemoControlCenterService,
    build_demo_payload,
)
from src.control_center.http import create_server
from src.control_center.service import ControlCenterService
from src.control_center.state import CONTROL_CENTER_VERSION
from src.strategy.artifact import validate_phase7_artifact


def test_phase8c_demo_catalog_covers_exact_a_through_t_states() -> None:
    assert len(DEMO_SCENARIOS) == 20
    assert [item["key"] for item in DEMO_SCENARIOS] == list("ABCDEFGHIJKLMNOPQRST")
    assert len({item["id"] for item in DEMO_SCENARIOS}) == 20


def test_phase8c_demo_mode_is_explicit_in_memory_and_production_disconnected() -> None:
    service = DemoControlCenterService()
    payload = service.bootstrap()
    assert payload["mode"]["demo"] is True
    assert payload["mode"]["production_data_used"] is False
    assert not hasattr(service, "database_path")
    assert all(row["name"].startswith("Demo ") for row in payload["players"])


def test_phase8c_no_season_state_has_intentional_empty_data_and_block_reasons() -> None:
    payload = build_demo_payload("no-season-data")
    assert payload["market"] == [] and payload["players"] == []
    assert payload["dashboard"]["optimization_blocked"] is True
    assert any("market is not yet available" in reason for reason in payload["dashboard"]["blocked_reasons"])


def test_phase8c_market_without_predictions_keeps_market_but_blocks_prediction_use() -> None:
    payload = build_demo_payload("market-no-predictions")
    assert payload["market"]
    assert all(row["expected_fp"] is None for row in payload["players"])
    assert payload["dashboard"]["predictions_current"] is False


def test_phase8c_mixed_availability_has_text_safe_green_yellow_red_states() -> None:
    rows = build_demo_payload("mixed-availability")["players"]
    assert {row["availability_group"] for row in rows} == {"GREEN", "YELLOW", "RED"}
    assert all(row["availability"] for row in rows)


def test_phase8c_manual_override_keeps_source_user_and_resolved_status_separate() -> None:
    rows = build_demo_payload("manual-overrides")["players"]
    changed = [row for row in rows if row["manual_override"]]
    assert len(changed) == 2
    assert any(row["source_availability_status"] == "AVAILABLE"
               and row["manual_override_status"] == "OUT"
               and row["availability"] == "OUT" for row in changed)


def test_phase8c_roster_states_expose_exact_slot_and_budget_errors() -> None:
    valid = build_demo_payload("roster-valid")["current_team"]
    incomplete = build_demo_payload("roster-incomplete")["current_team"]
    budget = build_demo_payload("budget-exceeded")["current_team"]
    assert valid["complete"] and valid["position_counts"] == {
        "GUARD": 4, "FORWARD": 4, "CENTER": 2, "COACH": 1,
    }
    assert not incomplete["complete"] and incomplete["validation_errors"]
    assert not budget["complete"]
    assert any("Budget exceeded" in value for value in budget["validation_errors"])


def test_phase8c_transfer_demo_and_zero_transfer_recommendations_are_consistent() -> None:
    zero = DemoControlCenterService("zero-transfers")
    result = zero.optimize()
    assert result["status"] == "SUCCEEDED"
    assert all(row["transfers_required"] == 0 for row in result["recommendations"])
    service = DemoControlCenterService("transfer-limits")
    assert service.bootstrap()["state"]["transfers_available"] == 1
    service.save_team({"transfers_available": 4})
    assert service.bootstrap()["state"]["transfers_available"] == 4


def test_phase8c_rules_scoring_and_stale_market_states_remain_blocked() -> None:
    rules = build_demo_payload("rules-blocked")["dashboard"]
    scoring = build_demo_payload("scoring-blocked")["dashboard"]
    stale = build_demo_payload("stale-market")["dashboard"]
    assert rules["optimization_blocked"] and not rules["rules_gate"]["passed"]
    assert scoring["optimization_blocked"] and not scoring["scoring_gate"]["passed"]
    assert stale["optimization_blocked"] and stale["freshness"]["fantasy_market"]["is_stale"]


def test_phase8c_refresh_success_and_failure_return_steps_changes_and_retry_state() -> None:
    succeeded = DemoControlCenterService("mixed-availability").refresh()
    failed = DemoControlCenterService("refresh-failure").refresh()
    assert succeeded["status"] == "SUCCEEDED" and succeeded["steps"] and succeeded["changes"]
    assert failed["status"] == "FAILED" and failed["retryable"] is True
    assert any(step["status"] == "FAILED" for step in failed["steps"])


def test_phase8c_force_include_resolved_out_warns_but_remains_explicit() -> None:
    service = DemoControlCenterService("mixed-availability")
    out = next(row for row in service.bootstrap()["players"] if row["availability_group"] == "RED")
    result = service.set_constraint(out["entity_id"], "FORCE_INCLUDE")
    assert result["warnings"]
    assert service.bootstrap()["state"]["player_constraints"][out["entity_id"]] == "FORCE_INCLUDE"


def test_phase8c_demo_override_is_reversible_without_source_mutation() -> None:
    service = DemoControlCenterService()
    before = service.bootstrap()["players"][0]
    service.set_override(before["player_id"], "OUT", note="demo knowledge")
    changed = service.bootstrap()["players"][0]
    assert changed["source_availability_status"] == before["source_availability_status"]
    assert changed["availability"] == "OUT" and changed["manual_override"]
    service.set_override(before["player_id"], "UNKNOWN", clear=True)
    assert not service.bootstrap()["players"][0]["manual_override"]


def test_phase8c_demo_rejects_duplicates_overflow_and_invalid_transfer_count() -> None:
    service = DemoControlCenterService()
    roster = service.bootstrap()["state"]["roster_entity_ids"]
    with unittest.TestCase().assertRaisesRegex(ValueError, "duplicate"):
        service.save_team({"roster_entity_ids": roster + [roster[0]]})
    with unittest.TestCase().assertRaisesRegex(ValueError, "at most"):
        service.save_team({"roster_entity_ids": roster + ["DEMO-E15"]})
    with unittest.TestCase().assertRaisesRegex(ValueError, "negative"):
        service.save_team({"transfers_available": -1})


def test_phase8c_after_turn_demo_exposes_cross_position_formation_and_probabilities() -> None:
    advisor = DemoControlCenterService("multiple-switches").reevaluate_strategy()
    switch = advisor["evidence"]["player_switches"][0]
    assert switch["cross_position"] is True
    assert switch["formation_before"] == "2-2-1"
    assert switch["formation_after"] == "1-3-1"
    assert 0 <= switch["probability_new_player_exceeds_realized_player"] <= 1
    assert switch["expected_final_team_score_switch"] > switch["expected_final_team_score_keep"]


def test_phase8c_no_legal_switch_demo_has_explicit_no_action_evidence() -> None:
    advisor = DemoControlCenterService("no-legal-switch").reevaluate_strategy()
    assert advisor["actions"] == []
    assert advisor["evidence"]["player_switches"] == []
    assert advisor["evidence"]["full_team_keep_vs_switch"]["expected_final_team_gain"] == 0


def test_phase8c_monitoring_marks_low_sample_and_stays_read_only() -> None:
    report = DemoControlCenterService().monitoring()
    assert report["sample_status"] == "INSUFFICIENT_LIVE_SAMPLE"
    assert len(report["rounds"]) < report["minimum_trend_sample"]
    assert report["optimization_modified"] is False


def test_phase8c_production_refresh_rejects_duplicate_requests() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(Path(directory) / "control.duckdb", fantasy_config=None)
        service._refresh_lock.acquire()
        try:
            result = service.refresh()
        finally:
            service._refresh_lock.release()
    assert result["status"] == "ALREADY_RUNNING"
    assert "ignored" in result["message"]


def test_phase8c_production_refresh_failure_is_structured_and_preserves_last_state() -> None:
    with TemporaryDirectory() as directory:
        service = ControlCenterService(
            Path(directory) / "control.duckdb", fantasy_config=None,
            update_runner=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline")),
        )
        result = service.refresh()
    assert result["status"] == "FAILED" and result["retryable"] is True
    assert result["error_code"] == "LIVE_REFRESH_FAILED"
    assert result["steps"][0]["status"] == "FAILED"


def test_phase8c_ui_has_global_readiness_demo_empty_and_page_landmarks() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    assert 'id="global-readiness"' in html
    assert 'id="demo-scenario"' in html
    assert 'id="preseason-state"' in html
    assert "VALUE / CREDIT GROWTH" in html
    assert "READ-ONLY DIAGNOSTICS" in html
    assert "Update Turn Results / Re-evaluate Strategy" in html


def test_phase8c_ui_exposes_direct_budget_and_transfer_limited_modes() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    assert all(f'value="{mode}"' in html for mode in (
        "COMPLETE_ROSTER", "CURRENT_TEAM", "BUILD_NEW",
    ))
    assert 'id="total-budget"' in html and 'step="0.1"' in html
    assert 'id="maximum-changes"' in html
    assert "adjustBudget(-0.1)" in script and "adjustBudget(0.1)" in script
    assert "body.total_budget = budget" in script
    assert "body.max_changes = changes" in script


def test_phase8c_ui_connects_team_builder_and_recommendations() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    styles = Path("web/control_center/styles.css").read_text(encoding="utf-8")
    assert all(f'id="{control}"' in html for control in (
        "team-search", "team-add-selected", "team-remove-selected", "team-clear",
        "team-complete-roster", "team-improve-current",
    ))
    assert "Clear role" in script and "suggestion-select" in script and "quick-add" in script
    assert "name, team, or position" in html.lower()
    assert "Remaining credits" in html and "Maximum changes" in html
    assert "Credits available to purchase players" in html
    assert "<option>11</option>" in html
    assert "AI will fill" in script and "Empty slots" in script
    assert "valid_partial" in script and "LOCKED" in script and "CHANGEABLE" in script
    assert "toggle-team-lock" in script and "teamName(row)" in script
    assert "Use as Current Team" in script
    assert "Apply Recommendation" in script and "Use Result as Current Team" in script
    assert "CREDIT IMPACT" in script and "FP IMPROVEMENT" in script
    assert 'api("/api/team"' in script and "useRecommendationAsCurrentTeam" in script
    assert ".suggestion-list" in styles and ".recommendation-actions" in styles
    assert 'id="temporary-exclusions-panel"' in html
    assert "temporary_excluded_entity_ids" in script
    assert "Find lineups without selected" in script
    assert "Clear all and restore original" in script
    assert ".temporary-exclusion-choice" in styles


def test_phase8c_ui_has_presets_override_dialog_and_accessible_status_text() -> None:
    html = Path("web/control_center/index.html").read_text(encoding="utf-8")
    assert all(f'data-preset="{name}"' in html
               for name in ("performance", "risk", "value", "minutes", "strategy"))
    assert 'id="override-dialog"' in html
    assert "GREEN · available/probable" in html
    assert "YELLOW · uncertain/limited" in html
    assert "RED · out/ineligible" in html


def test_phase8c_ui_guards_stale_loads_requests_and_shadow_scoped_lineups() -> None:
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    assert "loadEpoch" in script and "epoch !== app.loadEpoch" in script
    assert "recommendationMatchesCurrent" in script
    assert "isBusy(\"refresh\")" in script
    assert "shadow_snapshot_id:advisor.shadow_snapshot_id" in script


def test_phase8c_ui_renders_switch_captain_formations_and_full_team_evidence() -> None:
    script = Path("web/control_center/app.js").read_text(encoding="utf-8")
    assert "probability_new_player_exceeds_realized_player" in script
    assert "expected_final_team_score_keep" in script
    assert "formation_before" in script and "formation_after" in script
    assert "CAPTAIN KEEP / SWITCH" in script
    assert "NO ACTION" in script


def test_phase8c_critical_layouts_have_keyboard_tablet_and_mobile_rules() -> None:
    css = Path("web/control_center/styles.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "@media (max-width: 980px)" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 480px)" in css
    assert ".table-wrap { overflow: auto" in css


def test_phase8c_http_demo_scenario_switch_is_integrated() -> None:
    service = DemoControlCenterService()
    server = create_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        request = Request(
            base + "/api/demo/scenario", method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"scenario": "captain-decision"}).encode(),
        )
        payload = json.loads(urlopen(request, timeout=5).read())
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert payload["mode"]["scenario"] == "captain-decision"
    assert payload["latest_advisor"]["evidence"]["captain"]["recommendation"]


def test_phase8c_version_and_frozen_phase7_artifact_are_unchanged() -> None:
    assert CONTROL_CENTER_VERSION == "phase8c_control_center_polished_v1"
    assert validate_phase7_artifact()["strategy_model_version"] == (
        "phase7_dynamic_strategy_engine_frozen_v1"
    )


class Phase8CTests(unittest.TestCase):
    """Expose Phase 8C functions to the repository's unittest runner."""


_PHASE8C_TEST_FUNCTIONS = {
    name: value for name, value in tuple(globals().items())
    if name.startswith("test_phase8c_") and callable(value)
}
for _test_name, _test_function in _PHASE8C_TEST_FUNCTIONS.items():
    setattr(Phase8CTests, _test_name, staticmethod(_test_function))
del _test_name, _test_function, _PHASE8C_TEST_FUNCTIONS
