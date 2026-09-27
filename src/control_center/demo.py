"""Development-only Control Center fixtures for safe UI inspection.

The demo service is deliberately disconnected from DuckDB, live refresh sources,
prediction runners, and optimization snapshots.  It can never write fake rows to
production state; the launcher must opt into it explicitly with ``--demo``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from .state import CONTROL_CENTER_VERSION, availability_presentation, rank_players


DEMO_SCENARIOS: tuple[dict[str, str], ...] = (
    {"id": "no-season-data", "key": "A", "title": "No season data yet",
     "description": "Pre-season: no market, slate, or predictions."},
    {"id": "market-no-predictions", "key": "B", "title": "Market, no predictions",
     "description": "Market is present but the predictive snapshot is missing."},
    {"id": "predictions-available", "key": "C", "title": "Predictions available",
     "description": "Fresh market and predictions with a valid roster."},
    {"id": "mixed-availability", "key": "D", "title": "Mixed availability",
     "description": "GREEN, YELLOW, and RED player states."},
    {"id": "manual-overrides", "key": "E", "title": "Manual overrides active",
     "description": "Source, user override, and resolved status differ."},
    {"id": "roster-incomplete", "key": "F", "title": "Roster incomplete",
     "description": "Missing positions and coach are called out."},
    {"id": "roster-valid", "key": "G", "title": "Roster valid",
     "description": "Complete 4G/4F/2C/coach current roster."},
    {"id": "budget-exceeded", "key": "H", "title": "Budget exceeded",
     "description": "An invalid credit state with precise remediation."},
    {"id": "zero-transfers", "key": "I", "title": "Zero transfers",
     "description": "Optimizer must preserve the current roster."},
    {"id": "transfer-limits", "key": "J", "title": "1 / 2 / 4 transfers",
     "description": "Edit the transfer input to inspect each constrained state."},
    {"id": "rules-blocked", "key": "K", "title": "Rules blocked",
     "description": "Production compatibility gate blocks optimization."},
    {"id": "scoring-blocked", "key": "L", "title": "Scoring blocked",
     "description": "Scoring compatibility gate blocks optimization."},
    {"id": "stale-market", "key": "M", "title": "Stale market",
     "description": "Staleness is visible globally and at source level."},
    {"id": "refresh-failure", "key": "N", "title": "Failed live refresh",
     "description": "Partial stages, preserved state, and retry guidance."},
    {"id": "t1-completed", "key": "O", "title": "T1 complete / T2 pending",
     "description": "Realized T1 outcomes with legal future actions."},
    {"id": "multiple-switches", "key": "P", "title": "Multiple legal switches",
     "description": "Several formation-aware recourse options."},
    {"id": "captain-decision", "key": "Q", "title": "Captain keep / switch",
     "description": "Explicit simulated captain comparison."},
    {"id": "no-legal-switch", "key": "R", "title": "No legal switch",
     "description": "A deliberate NO ACTION state."},
    {"id": "value-growth", "key": "S", "title": "Value growth available",
     "description": "Secondary price intelligence with competitive FP."},
    {"id": "empty-filters", "key": "T", "title": "Empty filter result",
     "description": "A valid slate with a deliberately empty search result."},
)
DEMO_SCENARIO_IDS = frozenset(item["id"] for item in DEMO_SCENARIOS)
DEFAULT_DEMO_SCENARIO = "mixed-availability"


class _DemoRepository:
    """Small adapter for the existing HTTP routes; it never touches disk."""

    def __init__(self, service: "DemoControlCenterService") -> None:
        self.service = service

    def latest_run(self, profile_id: str = "demo") -> dict[str, Any] | None:
        return self.service.bootstrap().get("latest_recommendation")

    def latest_shadow_snapshot(self, profile_id: str = "demo") -> dict[str, Any] | None:
        return self.service.bootstrap().get("latest_shadow_snapshot")

    def latest_advisor_run(self, shadow_snapshot_id: str) -> dict[str, Any] | None:
        return self.service.bootstrap().get("latest_advisor")


class DemoControlCenterService:
    """In-memory UI service selected only by the explicit ``--demo`` flag."""

    profile_id = "demo"

    def __init__(self, scenario: str = DEFAULT_DEMO_SCENARIO) -> None:
        self.repository = _DemoRepository(self)
        self._scenario = ""
        self._saved_state: dict[str, Any] | None = None
        self._overrides: dict[str, dict[str, Any]] = {}
        self.set_demo_scenario(scenario)

    @property
    def scenario(self) -> str:
        return self._scenario

    def set_demo_scenario(self, scenario: str) -> dict[str, Any]:
        normalized = str(scenario).strip().lower()
        if normalized not in DEMO_SCENARIO_IDS:
            raise ValueError(f"Unknown demo state: {scenario}")
        self._scenario = normalized
        self._saved_state = None
        self._overrides = {}
        return self.bootstrap()

    def bootstrap(self) -> dict[str, Any]:
        payload = build_demo_payload(self._scenario)
        if self._saved_state is not None:
            payload["state"] = deepcopy(self._saved_state)
            _apply_demo_team(payload)
        for player in payload["players"]:
            player["constraint"] = payload["state"].get("player_constraints", {}).get(
                player["entity_id"], "NORMAL"
            )
            override = self._overrides.get(player["player_id"])
            if override:
                player.update(override)
                player.update(availability_presentation(override["availability"]))
                player["availability_group"] = player.pop("group")
                player["availability_icon"] = player.pop("icon")
        payload["availability"] = deepcopy(payload["players"])
        payload["dashboard"]["active_manual_overrides"] = sum(
            bool(row.get("manual_override")) for row in payload["players"]
        )
        payload["mode"] = {
            "demo": True, "production_data_used": False, "scenario": self._scenario,
            "scenario_catalog": deepcopy(list(DEMO_SCENARIOS)),
            "notice": "Synthetic development fixtures — never written to production data.",
        }
        return payload

    def dashboard(self) -> dict[str, Any]:
        return self.bootstrap()["dashboard"]

    def players(
        self, *, view: str = "BEST_EXPECTED_FP", filters: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        rows = deepcopy(self.bootstrap()["players"])
        filters = dict(filters or {})
        query = str(filters.get("query", "")).casefold().strip()
        if query:
            rows = [row for row in rows if query in row["name"].casefold()]
        for key in ("team_id", "position", "turn", "availability_group"):
            if filters.get(key) not in (None, ""):
                rows = [row for row in rows if str(row.get(key)) == str(filters[key])]
        return rank_players(rows, view)

    def monitoring(self) -> dict[str, Any]:
        return deepcopy(self.bootstrap()["monitoring"])

    def save_team(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = deepcopy(self.bootstrap()["state"])
        previous_roster = set(map(str, current.get("roster_entity_ids", ())))
        current.update(dict(payload))
        roster = list(map(str, current.get("roster_entity_ids", [])))
        if "roster_entity_ids" in payload:
            next_roster = set(roster)
            constraints = dict(current.get("player_constraints", {}))
            unlock_ids = set(constraints) if not next_roster else previous_roster - next_roster
            for entity_id in unlock_ids:
                if str(constraints.get(entity_id, "")).upper() == "FORCE_INCLUDE":
                    constraints.pop(entity_id, None)
            current["player_constraints"] = constraints
        if len(roster) != len(set(roster)):
            raise ValueError("Current roster contains duplicate entities.")
        if len(roster) > 11:
            raise ValueError("Current roster can contain at most 10 players and one coach.")
        if float(current.get("bank_credits", 0)) < 0:
            raise ValueError("Bank credits cannot be negative.")
        transfers = int(current.get("transfers_available", 0))
        if transfers < 0:
            raise ValueError("Transfers available cannot be negative.")
        current["transfers_available"] = transfers
        market_ids = {row["entity_id"] for row in build_demo_payload(self._scenario)["market"]}
        unknown = sorted(set(roster) - market_ids)
        if unknown:
            raise ValueError("Roster contains entities that are not in the current market: " + ", ".join(unknown))
        self._saved_state = current
        value = self.bootstrap()
        return {"state": value["state"], "current_team": value["current_team"]}

    def set_constraint(self, entity_id: str, value: str) -> dict[str, Any]:
        normalized = str(value).upper()
        if normalized not in {"NORMAL", "EXCLUDE", "FORCE_INCLUDE"}:
            raise ValueError(f"Unsupported optimization constraint: {value}")
        state = deepcopy(self.bootstrap()["state"])
        constraints = dict(state.get("player_constraints", {}))
        if normalized == "NORMAL":
            constraints.pop(str(entity_id), None)
        else:
            constraints[str(entity_id)] = normalized
        result = self.save_team({"player_constraints": constraints})
        player = next((row for row in self.bootstrap()["players"]
                       if row["entity_id"] == str(entity_id)), None)
        result["warnings"] = []
        if normalized == "FORCE_INCLUDE" and player and player["availability_group"] == "RED":
            result["warnings"].append(
                f"{player['name']} is resolved {player['availability']}; FORCE INCLUDE remains explicit."
            )
        return result

    def set_override(
        self, player_id: str, decision: str, *, note: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        if clear:
            self._overrides.pop(str(player_id), None)
            message = "Override cleared. Demo predictions are shown as immediately refreshed."
        else:
            normalized = str(decision).upper()
            if normalized not in {"PLAY", "OUT", "UNKNOWN", "LIMITED"}:
                raise ValueError(f"Unsupported manual availability decision: {decision}")
            resolved = "AVAILABLE" if normalized == "PLAY" else normalized
            self._overrides[str(player_id)] = {
                "availability": resolved, "manual_override": True,
                "manual_override_status": normalized,
                "manual_override_timestamp": datetime.now(UTC).isoformat(),
                "manual_override_note": note,
            }
            message = "Manual demo override saved. No production source row was changed."
        value = self.bootstrap()
        return {"override_event_id": "DEMO-ONLY", "message": message,
                "dashboard": value["dashboard"], "players": value["players"]}

    def refresh(self) -> dict[str, Any]:
        if self._scenario == "refresh-failure":
            return {
                "status": "FAILED", "retryable": True,
                "message": "Fantasy market refresh failed; the last valid demo snapshot was preserved.",
                "steps": [
                    {"name": "schedule/results", "status": "SUCCEEDED", "detail": "2 games updated"},
                    {"name": "fantasy market", "status": "FAILED", "detail": "simulated source timeout"},
                    {"name": "predictions", "status": "SKIPPED", "detail": "market gate unavailable"},
                ],
                "changes": {"results": 2, "completed_turns": 1, "market": 0,
                            "prices": 0, "rosters": 0, "availability": 0,
                            "predictions": 0},
                "dashboard": self.dashboard(),
            }
        return {
            "status": "SUCCEEDED", "retryable": False,
            "message": "Demo refresh completed; synthetic fixtures were reloaded in memory.",
            "steps": [
                {"name": "schedule/results", "status": "SUCCEEDED", "detail": "current"},
                {"name": "market/prices", "status": "SUCCEEDED", "detail": "16 players"},
                {"name": "availability", "status": "SUCCEEDED", "detail": "mixed states"},
                {"name": "predictions", "status": "SUCCEEDED", "detail": "Phase 6C-shaped fixture"},
            ],
            "changes": {"results": 2 if self._scenario in _AFTER_TURN else 0,
                        "completed_turns": 1 if self._scenario in _AFTER_TURN else 0,
                        "market": 16, "prices": 16, "rosters": 8,
                        "availability": 16, "predictions": 16},
            "prediction": {"status": "SUCCEEDED"}, "dashboard": self.dashboard(),
        }

    def optimize(
            self, *, mode: str = "CURRENT_TEAM", bank_credits: float | None = None,
            max_changes: int | None = None,
            temporary_excluded_entity_ids: list[str] | tuple[str, ...] = (), **_: Any,
    ) -> dict[str, Any]:
        payload = self.bootstrap()
        if payload["dashboard"]["optimization_blocked"]:
            return {"status": "BLOCKED", "blocked_reasons":
                    payload["dashboard"]["blocked_reasons"], "recommendations": []}
        result = deepcopy(payload["latest_recommendation"])
        result["optimization_mode"] = str(mode).upper()
        temporary_excluded = {
            str(entity) for entity in temporary_excluded_entity_ids if str(entity)
        }
        result["temporary_run"] = bool(temporary_excluded)
        result["temporary_excluded_entity_ids"] = sorted(temporary_excluded)
        result["control_center_run_id"] = None if temporary_excluded else result.get(
            "control_center_run_id"
        )
        result["snapshot_path"] = None if temporary_excluded else result.get("snapshot_path")
        if temporary_excluded:
            result["shadow"] = {
                "status": "TEMPORARY_NOT_PERSISTED",
                "reason": "Try-without optimization results are session-only",
            }
        if result["optimization_mode"] == "BUILD_NEW":
            return result
        team = payload["current_team"]
        if not team["valid_partial"]:
            return {"status": "BLOCKED", "blocked_reasons":
                    team["partial_validation_errors"], "recommendations": []}
        changes = int(max_changes if max_changes is not None
                      else payload["state"]["transfers_available"])
        if not 0 <= changes <= 11:
            return {"status": "BLOCKED", "blocked_reasons":
                    ["Maximum changes must be a whole number from 0 to 11."],
                    "recommendations": []}
        completion_mode = result["optimization_mode"] == "COMPLETE_ROSTER"
        if not completion_mode and not team["complete"]:
            return {"status": "BLOCKED", "blocked_reasons": [
                "Improve My Team requires a complete current roster; "
                "use Complete My Roster to fill empty positions"
            ], "recommendations": []}
        if completion_mode and changes < team["slots_to_fill"]:
            return {"status": "BLOCKED", "blocked_reasons": [
                f"Maximum changes must be at least {team['slots_to_fill']} "
                "to fill every empty roster slot"
            ], "recommendations": []}
        remaining = float(bank_credits if bank_credits is not None
                          else payload["state"]["bank_credits"])
        current = set(payload["state"]["roster_entity_ids"])
        locked = (
            current if completion_mode else
            {
                entity for entity, constraint
                in payload["state"].get("player_constraints", {}).items()
                if constraint == "FORCE_INCLUDE"
            }
        )
        market_lookup = {row["entity_id"]: row for row in payload["market"]}
        viable = []
        for recommendation in result["recommendations"]:
            selected = {row["entity_id"] for row in recommendation["players"]}
            selected.add(recommendation["coach"]["entity_id"])
            if selected & temporary_excluded:
                continue
            if not locked.issubset(selected):
                continue
            incoming = selected - current
            outgoing = current - selected
            if not completion_mode and len(incoming) > changes:
                continue
            details = {
                row["entity_id"]: row
                for row in [*recommendation["players"], recommendation["coach"]]
            }
            spent = round(sum(float(details[entity]["credits"]) for entity in incoming), 1)
            received = round(sum(float(market_lookup[entity]["credits"]) for entity in outgoing), 1)
            if spent > remaining + received + 1e-9:
                continue
            recommendation["players_in"] = [details[entity] for entity in sorted(incoming)]
            recommendation["players_out"] = [market_lookup[entity] for entity in sorted(outgoing)]
            recommendation["transfers_required"] = len(incoming)
            recommendation["credit_accounting"] = {
                "credits_before": remaining, "bank_before": remaining,
                "credits_received": received, "credits_spent": spent,
                "credits_remaining": round(remaining + received - spent, 1),
            }
            before = None if completion_mode else round(sum(
                float(row.get("expected_fp") or 0)
                for row in payload["players"] if row["entity_id"] in current
            ), 1)
            recommendation["predicted_team_fp_before"] = before
            recommendation["predicted_team_fp_after"] = recommendation["expected_final_score"]
            recommendation["predicted_improvement"] = (
                None if before is None else round(recommendation["expected_final_score"] - before, 1)
            )
            viable.append(recommendation)
        if not viable:
            needs = " + ".join(
                f"{count} {position.title()}{'' if count == 1 else 's'}"
                for position, count in team["missing_positions"].items()
            ) or "roster"
            return {"status": "BLOCKED", "blocked_reasons": [
                f"No legal {needs} combination fits the credits, locks, and change limit."
            ], "recommendations": [], "temporary_run": bool(temporary_excluded),
                "temporary_excluded_entity_ids": sorted(temporary_excluded)}
        result["recommendations"] = viable
        return result

    def reevaluate_strategy(self, **_: Any) -> dict[str, Any]:
        advisor = self.bootstrap().get("latest_advisor")
        if advisor:
            return deepcopy(advisor)
        return {"status": "NO_COMPLETED_TURN",
                "reason": "No completed Turn exists in this demo state."}

    def shadow_validation(self, shadow_snapshot_id: str | None = None) -> dict[str, Any]:
        return {"status": "DEMO_ONLY", "shadow_snapshot_id": shadow_snapshot_id,
                "message": "Synthetic fixtures are never appended to shadow validation."}

    def player_detail(self, player_id: str) -> dict[str, Any]:
        player = next((row for row in self.bootstrap()["players"]
                       if row["player_id"] == str(player_id)), None)
        if player is None:
            raise ValueError(f"Unknown player in current demo slate: {player_id}")
        selected = player["entity_id"] in set(self.bootstrap()["state"]["roster_entity_ids"])
        return {
            "player": player,
            "performance": {key: player.get(key) for key in (
                "expected_fp", "expected_minutes", "p10_fp", "p50_fp", "p90_fp", "p95_fp",
                "prob_fp_ge_20", "prob_fp_ge_25", "prob_fp_ge_30", "prob_fp_ge_35",
                "prob_fp_ge_40", "prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15",
                "recent_form", "last5_fp_average", "last5_minutes_median", "last5_games",
            )},
            "value": {key: player.get(key) for key in (
                "credits", "fp_per_credit", "expected_next_price", "expected_credit_change",
                "probability_increase", "probability_decrease", "price_model_confidence",
            )},
            "status": {key: player.get(key) for key in (
                "availability", "availability_group", "source_availability_status",
                "availability_source", "availability_timestamp", "manual_override",
                "manual_override_status", "manual_override_timestamp", "manual_override_note",
            )},
            "strategy": {
                "turn": player["turn"], "selected_in": ["BEST_OVERALL"] if selected else [],
                "role": "STARTER" if selected and player["turn"] == 1 else "BENCH/FUTURE OPTION",
                "captain_suitability": "Strong later captain candidate" if player["p90_fp"] >= 32 else "Secondary",
                "replacement_optionality": "Protected by a later legal option" if player["turn"] == 1 else "Final-Turn risk is not replaceable",
                "why_selected": "High expected final-team value with legal Turn recourse." if selected else None,
                "why_not_selected": None if selected else "A legal re-optimization produced a stronger full-team distribution.",
                "alternatives": [],
            },
        }


_AFTER_TURN = {"t1-completed", "multiple-switches", "captain-decision", "no-legal-switch"}


def build_demo_payload(scenario: str = DEFAULT_DEMO_SCENARIO) -> dict[str, Any]:
    """Build one deterministic, production-disconnected UI fixture."""

    if scenario not in DEMO_SCENARIO_IDS:
        raise ValueError(f"Unknown demo state: {scenario}")
    now = datetime(2026, 9, 18, 12, tzinfo=UTC)
    players = _demo_players(now)
    coaches = _demo_coaches()
    market = [{key: row.get(key) for key in (
        "entity_id", "player_id", "name", "team_id", "team_name", "position", "credits", "entity_type",
    )} for row in players] + coaches
    roster = [row["entity_id"] for row in players[:4] + players[6:10] + players[12:14]]
    roster.append(coaches[0]["entity_id"])
    state = {
        "profile_id": "demo", "season_code": "E2026", "fantasy_matchday": 1,
        "roster_entity_ids": roster, "bank_credits": 3.4, "transfers_available": 2,
        "scenario_id": "DEMO_BASE", "player_constraints": {},
    }
    freshness = {
        name: {"is_stale": False, "last_success_at": (now - timedelta(minutes=8)).isoformat(),
               "message": "Demo source current"}
        for name in ("schedule", "basketball_stats", "fantasy_market", "availability",
                     "current_features")
    }
    dashboard = {
        "current_season": "E2026", "current_matchday": 1, "current_turn": "PRE-T1",
        "slate_rows": len(players), "slate_rows_with_credits": len(players),
        "fantasy_players": len(players), "fantasy_players_mapped": len(players),
        "freshness": freshness, "market_snapshot_timestamp": (now - timedelta(minutes=8)).isoformat(),
        "availability_timestamp": (now - timedelta(minutes=5)).isoformat(),
        "last_successful_refresh_timestamp": (now - timedelta(minutes=4)).isoformat(),
        "active_manual_overrides": 0, "predictions_current": True,
        "prediction": {"status": "SUCCEEDED", "predictive_model_version":
                       "phase6c_predictive_uplift_frozen_v1"},
        "rules_gate": {"passed": True, "version": "demo_verified_rules_v1",
                       "reason": "Demo of a verified rules state", "fingerprint": "demo-rules"},
        "scoring_gate": {"passed": True, "state": "VERIFIED",
                         "reason": "Demo of a verified scoring state", "fingerprint": "demo-scoring"},
        "optimization_blocked": False, "blocked_reasons": [],
        "latest_live_run": {"status": "SUCCEEDED"},
    }
    recommendation = _demo_recommendations(players, coaches[0], now)
    shadow = {"shadow_snapshot_id": "DEMO-SHADOW-E2026-MD1", "snapshot_fingerprint": "demo-shadow",
              "created_at": (now - timedelta(hours=3)).isoformat()}
    advisor = _demo_advisor(players) if scenario in _AFTER_TURN else None
    monitoring = _demo_monitoring(now)
    payload = {
        "version": CONTROL_CENTER_VERSION, "dashboard": dashboard, "state": state,
        "players": players, "availability": deepcopy(players), "market": market,
        "latest_recommendation": recommendation, "latest_shadow_snapshot": shadow,
        "latest_advisor": advisor, "monitoring": monitoring,
        "ranking_views": ["BEST_EXPECTED_FP", "BEST_VALUE", "MOST_EXPECTED_MINUTES",
                          "HIGHEST_UPSIDE", "SAFEST", "VALUE_CREDIT_GROWTH"],
        "demo_ui": {},
    }

    if scenario == "no-season-data":
        payload["players"] = []; payload["availability"] = []; payload["market"] = []
        state["fantasy_matchday"] = None; state["roster_entity_ids"] = []
        dashboard.update({"current_matchday": None, "current_turn": "PRE-SEASON",
                          "slate_rows": 0, "slate_rows_with_credits": 0,
                          "fantasy_players": 0, "fantasy_players_mapped": 0,
                          "market_snapshot_timestamp": None, "predictions_current": False,
                          "prediction": None, "optimization_blocked": True,
                          "blocked_reasons": ["Current E2026 Fantasy market is not yet available.",
                                              "No Phase 6C prediction snapshot exists yet."]})
        payload["latest_recommendation"] = None; payload["latest_shadow_snapshot"] = None
    elif scenario == "market-no-predictions":
        for player in payload["players"]:
            for key in ("expected_minutes", "expected_fp", "p10_fp", "p50_fp", "p90_fp",
                        "p95_fp", "prob_fp_le_15", "prob_fp_ge_30", "fp_per_credit"):
                player[key] = None
        dashboard.update({"predictions_current": False, "prediction": None,
                          "optimization_blocked": True,
                          "blocked_reasons": ["Market is available, but predictions have not been generated."]})
        payload["latest_recommendation"] = None
    elif scenario == "manual-overrides":
        _set_override(payload["players"][2], "QUESTIONABLE", "PLAY", "AVAILABLE", now,
                      "Confirmed active at shootaround")
        _set_override(payload["players"][5], "AVAILABLE", "OUT", "OUT", now,
                      "User knows player did not travel")
    elif scenario == "roster-incomplete":
        state["roster_entity_ids"] = roster[:6]
    elif scenario == "budget-exceeded":
        state["bank_credits"] = 0.0
        payload["demo_ui"]["budget_limit"] = 80.0
        payload["demo_ui"]["forced_validation_error"] = "Budget exceeded by 19.8 credits."
    elif scenario == "zero-transfers":
        state["transfers_available"] = 0
        best = payload["latest_recommendation"]["recommendations"][0]
        for recommendation in payload["latest_recommendation"]["recommendations"]:
            recommendation["players"] = deepcopy(best["players"])
            recommendation["starting_five"] = deepcopy(best["starting_five"])
            recommendation["captain"] = best["captain"]
            recommendation["sixth_man"] = best["sixth_man"]
            recommendation["transfers_required"] = 0
            recommendation["players_in"] = []
            recommendation["players_out"] = []
    elif scenario == "transfer-limits":
        state["transfers_available"] = 1
    elif scenario == "rules-blocked":
        dashboard["rules_gate"].update({"passed": False, "reason": "E2026 rules manifest is not verified."})
        _block(dashboard, "Rules compatibility gate is BLOCKED; verify the official E2026 manifest.")
    elif scenario == "scoring-blocked":
        dashboard["scoring_gate"].update({"passed": False, "state": "BLOCKED",
                                          "reason": "E2026 scoring evidence is incomplete."})
        _block(dashboard, "Scoring compatibility gate is BLOCKED; optimization remains disabled.")
    elif scenario == "stale-market":
        dashboard["freshness"]["fantasy_market"].update({
            "is_stale": True, "last_success_at": (now - timedelta(days=3)).isoformat(),
            "message": "Last valid market is older than the freshness policy.",
        })
        dashboard["market_snapshot_timestamp"] = (now - timedelta(days=3)).isoformat()
        dashboard["predictions_current"] = False
        _block(dashboard, "Fantasy market and dependent predictions are stale.")
    elif scenario == "refresh-failure":
        dashboard["latest_live_run"] = {"status": "PARTIAL"}
    elif scenario in _AFTER_TURN:
        dashboard["current_turn"] = "T2 PENDING"
    elif scenario == "value-growth":
        for player in payload["players"][:5]:
            player["value_growth_qualified"] = True
            player["value_growth_score"] = player["fp_per_credit"] * 1.05
    elif scenario == "empty-filters":
        payload["demo_ui"]["initial_player_query"] = "No Such Demo Player"
    if scenario == "no-legal-switch" and payload["latest_advisor"]:
        payload["latest_advisor"]["actions"] = []
        payload["latest_advisor"]["evidence"]["player_switches"] = []
        comparison = payload["latest_advisor"]["evidence"]["full_team_keep_vs_switch"]
        comparison.update({"expected_final_team_score_switch": comparison["expected_final_team_score_keep"],
                           "expected_final_team_gain": 0.0,
                           "probability_switch_final_score_exceeds_keep": 0.0})
    _apply_demo_team(payload)
    return payload


def _demo_players(now: datetime) -> list[dict[str, Any]]:
    positions = ["GUARD"] * 6 + ["FORWARD"] * 6 + ["CENTER"] * 4
    statuses = ["AVAILABLE", "PROBABLE", "QUESTIONABLE", "AVAILABLE", "OUT", "LIMITED",
                "AVAILABLE", "DOUBTFUL", "AVAILABLE", "PROBABLE", "SUSPENDED", "AVAILABLE",
                "AVAILABLE", "QUESTIONABLE", "NOT_REGISTERED", "PROBABLE"]
    names = [
        "Demo Orion", "Demo Atlas", "Demo Nova", "Demo Vega", "Demo Lyra", "Demo Draco",
        "Demo Sol", "Demo Titan", "Demo Echo", "Demo Phoenix", "Demo Aster", "Demo Helios",
        "Demo Zenith", "Demo Cosmos", "Demo Comet", "Demo Pulsar",
    ]
    rows = []
    for index, (name, position, status) in enumerate(zip(names, positions, statuses)):
        expected = round(28.0 - index * 0.72 + (2.2 if index in {4, 10, 14} else 0), 1)
        p10 = round(max(expected - 10.0 - (index % 3), 1.0), 1)
        p90 = round(expected + 10.5 + (index % 4), 1)
        credits = round(14.8 - index * 0.35, 1)
        presentation = availability_presentation(status)
        rows.append({
            "entity_id": f"DEMO-E{index + 1:02d}", "player_id": f"DEMO-P{index + 1:02d}",
            "entity_type": "PLAYER", "name": name, "team_id": f"D{index % 8 + 1}",
            "team_name": f"Demo Team {index % 8 + 1}",
            "opponent_team_id": f"D{(index + 3) % 8 + 1}", "position": position,
            "credits": credits, "turn": index % 3 + 1,
            "availability": status, "availability_group": presentation["group"],
            "availability_icon": presentation["icon"], "source_availability_status": status,
            "availability_source": "DEMO_OFFICIAL_REPORT",
            "availability_timestamp": (now - timedelta(minutes=20 + index)).isoformat(),
            "manual_override": False, "manual_override_status": None,
            "manual_override_timestamp": None, "manual_override_note": None,
            "expected_minutes": round(31.0 - index * 0.35, 1), "expected_fp": expected,
            "median_fp": round(expected - 0.4, 1), "p50_fp": round(expected - 0.4, 1),
            "p10_fp": p10, "p90_fp": p90, "p95_fp": round(p90 + 4.2, 1),
            "prob_fp_le_5": round(max(0.01, 0.08 - index * 0.002), 3),
            "prob_fp_le_10": round(min(0.35, 0.10 + index * 0.009), 3),
            "prob_fp_le_15": round(min(0.55, 0.16 + index * 0.012), 3),
            "prob_fp_ge_20": round(max(0.1, 0.74 - index * 0.025), 3),
            "prob_fp_ge_25": round(max(0.06, 0.59 - index * 0.024), 3),
            "prob_fp_ge_30": round(max(0.03, 0.42 - index * 0.020), 3),
            "prob_fp_ge_35": round(max(0.01, 0.26 - index * 0.014), 3),
            "prob_fp_ge_40": round(max(0.005, 0.13 - index * 0.007), 3),
            "fp_per_credit": round(expected / credits, 2), "recent_form": round(expected - 1.2, 1),
            "last5_fp_average": round(expected - 1.0, 1),
            "last5_minutes_median": round(29.8 - index * 0.25, 1),
            "last5_games": [
                {
                    "game_id": f"DEMO-G{game_index + 1}-{index + 1}",
                    "game_date": (now - timedelta(days=game_index * 3 + 1)).isoformat(),
                    "game_type": "PRESEASON_TOURNAMENT" if game_index < 2 else "EUROLEAGUE",
                    "fantasy_points": round(expected - 3 + game_index * 1.1, 1),
                    "minutes": round(27.5 + game_index * 0.8 - index * 0.15, 1),
                }
                for game_index in range(5)
            ],
            "expected_next_price": round(credits + (0.2 if index % 3 == 0 else -0.1), 1),
            "expected_credit_change": 0.2 if index % 3 == 0 else -0.1,
            "probability_increase": 0.64 if index % 3 == 0 else 0.36,
            "probability_decrease": 0.26 if index % 3 == 0 else 0.54,
            "price_model_confidence": "SECONDARY_RMSE_ONLY",
            "value_growth_qualified": index % 3 == 0,
            "value_growth_score": round(expected / credits * 1.04, 3) if index % 3 == 0 else None,
            "price_growth_affects_phase7_objective": False, "constraint": "NORMAL",
        })
    return rows


def _demo_coaches() -> list[dict[str, Any]]:
    return [
        {"entity_id": f"DEMO-COACH-{index}", "coach_id": f"DEMO-C{index}",
         "entity_type": "COACH", "name": f"Demo Coach {index}", "team_id": f"D{index}",
         "team_name": f"Demo Team {index}",
         "position": "COACH", "credits": 7.0 + index * 0.5, "turn": index}
        for index in (1, 2, 3)
    ]


def _apply_demo_team(payload: dict[str, Any]) -> None:
    state = payload["state"]
    lookup = {row["entity_id"]: row for row in payload["market"] + payload["players"]}
    selected = []
    seen = set()
    for entity_id in state["roster_entity_ids"]:
        row = lookup.get(entity_id)
        if row and entity_id not in seen:
            selected.append(deepcopy(row)); seen.add(entity_id)
    counts = {"GUARD": 0, "FORWARD": 0, "CENTER": 0, "COACH": 0}
    for row in selected:
        key = "COACH" if row.get("entity_type") == "COACH" else row.get("position")
        counts[key] = counts.get(key, 0) + 1
    errors = []
    expected = {"GUARD": 4, "FORWARD": 4, "CENTER": 2, "COACH": 1}
    for key, target in expected.items():
        if counts.get(key, 0) != target:
            errors.append(f"{key.title()}: {counts.get(key, 0)}/{target} selected")
    forced = payload.get("demo_ui", {}).get("forced_validation_error")
    if forced:
        errors.append(forced)
    value = round(sum(float(row.get("credits") or 0) for row in selected), 1)
    payload["current_team"] = {
        "entities": selected, "roster_size": len(selected), "roster_market_value": value,
        "bank_credits": state["bank_credits"],
        "available_budget": round(value + float(state["bank_credits"]), 1),
        "transfers_available": state["transfers_available"],
        "complete": not errors and len(selected) == 11,
        "valid_partial": counts["COACH"] == 1 and not forced,
        "position_counts": counts, "requirements": expected, "validation_errors": errors,
        "partial_validation_errors": (
            [] if counts["COACH"] == 1 and not forced
            else [forced or "Select one Coach before optimizing"]
        ),
        "missing_positions": {
            position: target - counts[position]
            for position, target in expected.items()
            if position != "COACH" and counts[position] < target
        },
        "slots_to_fill": sum(
            target - counts[position]
            for position, target in expected.items()
            if position != "COACH" and counts[position] < target
        ),
    }


def _set_override(
    row: dict[str, Any], source: str, override: str, resolved: str,
    now: datetime, note: str,
) -> None:
    row.update({"source_availability_status": source, "availability": resolved,
                "manual_override": True, "manual_override_status": override,
                "manual_override_timestamp": now.isoformat(), "manual_override_note": note})
    presentation = availability_presentation(resolved)
    row["availability_group"] = presentation["group"]
    row["availability_icon"] = presentation["icon"]


def _block(dashboard: dict[str, Any], reason: str) -> None:
    dashboard["optimization_blocked"] = True
    dashboard["blocked_reasons"] = [reason]


def _demo_recommendations(
    players: list[dict[str, Any]], coach: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    choices = [
        ("BEST_OVERALL", list(range(4)) + list(range(6, 10)) + [12, 13], 242.7, 188.0, 240.1, 299.4,
         "Best expected final score with two later options"),
        ("SAFER_ALTERNATIVE", list(range(4)) + [6, 7, 8, 10] + [12, 13], 241.2, 192.8, 239.0, 291.5,
         "Stronger P10 with competitive mean"),
        ("HIGHER_UPSIDE_ALTERNATIVE", [0, 1, 2, 4] + list(range(6, 10)) + [12, 13], 241.5, 181.2, 238.7, 307.8,
         "Higher P90 and early-Turn optionality"),
    ]
    recommendations = []
    for label, indexes, mean, p10, p50, p90, reason in choices:
        selected = []
        for slot, index in enumerate(indexes):
            row = players[index]
            selected.append({
                "entity_id": row["entity_id"], "player_id": row["player_id"],
                "name": row["name"], "position": row["position"], "turn": row["turn"],
                "credits": row["credits"], "expected_fp": row["expected_fp"],
                "p10": row["p10_fp"], "p50": row["p50_fp"], "p90": row["p90_fp"],
                "role": "STARTER" if slot < 5 else ("SIXTH_MAN" if slot == 5 else "BENCH"),
                "captain": slot == 0,
            })
        recommendations.append({
            "label": label, "major_characteristic": reason, "players": selected,
            "coach": {**coach, "expected_score": 7.4},
            "captain": selected[0]["player_id"],
            "starting_five": [row["player_id"] for row in selected[:5]],
            "sixth_man": selected[5]["player_id"],
            "expected_final_score": mean, "p10_team_score": p10,
            "p50_team_score": p50, "p90_team_score": p90,
            "credits_used": round(sum(row["credits"] for row in selected) + coach["credits"], 1),
            "transfers_required": 0 if label == "BEST_OVERALL" else 1,
            "players_in": [] if label == "BEST_OVERALL" else [selected[8]],
            "players_out": [] if label == "BEST_OVERALL" else [players[9]],
            "captain_switch_frequency": 0.31, "substitution_frequency": 0.46,
        })
    return {
        "status": "SUCCEEDED", "version": CONTROL_CENTER_VERSION,
        "season": "E2026", "matchday": 1,
        "control_center_run_id": "DEMO-RUN-8C", "generated_at": now.isoformat(),
        "recommendations": recommendations,
        "player_alternatives": [
            {"player": players[10], "replaces": players[9], "alternative_type": "SAFER",
             "credits_difference": -0.4, "expected_final_team_fp_difference": -1.5,
             "p10_difference": 4.8, "p90_difference": -7.9, "turn": players[10]["turn"]},
            {"player": players[4], "replaces": players[3], "alternative_type": "HIGHER_UPSIDE",
             "credits_difference": -0.3, "expected_final_team_fp_difference": -1.2,
             "p10_difference": -6.8, "p90_difference": 8.4, "turn": players[4]["turn"]},
        ],
        "strategy": {
            "initial_captain": players[0]["player_id"],
            "best_later_captain_alternative": players[7]["player_id"],
            "instruction": "Keep the T1 captain only when the frozen Phase 7 expected-final-value boundary is met.",
            "useful_early_upside": [{"name": players[0]["name"], "p90": players[0]["p90_fp"]}],
            "decision_rules": [{"player": players[0]["name"], "threshold": 20.4,
                                "action_below_threshold": f"Promote {players[7]['name']} in T2",
                                "derivation": "Frozen Phase 7 expected-final-value decision boundary"}],
            "downside_protected_players": [players[0]["player_id"]],
            "players_without_later_safety_net": [{"name": players[13]["name"]}],
        },
        "shadow": {"shadow_snapshot_id": "DEMO-SHADOW-E2026-MD1"},
    }


def _demo_advisor(players: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "SUCCEEDED", "completed_turn": 1,
        "shadow_snapshot_id": "DEMO-SHADOW-E2026-MD1", "advisor_run_id": "DEMO-ADVISOR-T1",
        "actions": [
            {"action": "MOVE_TO_BENCH", "player_id": players[0]["player_id"],
             "statistical_reasons": ["Realized 11.4 FP", "Full-team simulation gains +5.4 FP"]},
            {"action": "MOVE_TO_FIELD", "player_id": players[7]["player_id"],
             "from_player_id": players[0]["player_id"], "cross_position": True,
             "statistical_reasons": ["Expected 18.2 FP", "68% probability of exceeding 11.4"]},
        ],
        "recommended_lineup": {
            "player_ids": [row["player_id"] for row in players[:4] + players[6:10] + players[12:14]],
            "coach_id": "DEMO-C1",
            "starters": [players[i]["player_id"] for i in (1, 6, 7, 8, 12)],
            "sixth_man": players[2]["player_id"], "captain": players[7]["player_id"],
        },
        "evidence": {
            "full_team_keep_vs_switch": {
                "expected_final_team_score_keep": 226.1, "expected_final_team_score_switch": 231.5,
                "expected_final_team_gain": 5.4,
                "probability_switch_final_score_exceeds_keep": 0.61,
                "keep_p10": 181.2, "keep_p90": 276.0,
                "switch_p10": 182.8, "switch_p90": 285.7,
            },
            "player_switches": [{
                "played_player_id": players[0]["player_id"], "played_player_name": players[0]["name"],
                "realized_fp": 11.4, "new_player_id": players[7]["player_id"],
                "new_player_name": players[7]["name"], "new_player_position": "FORWARD",
                "played_player_position": "GUARD", "expected_fp": 18.2,
                "p10": 7.2, "p50": 17.7, "p90": 31.8,
                "upside_probabilities": {"30": 0.24}, "downside_probabilities": {"10": 0.18},
                "probability_new_player_exceeds_realized_player": 0.68,
                "expected_final_team_score_keep": 226.1,
                "expected_final_team_score_switch": 231.5,
                "expected_final_team_gain": 5.4,
                "probability_switch_final_score_exceeds_keep": 0.61,
                "sixth_man_effect": "UNCHANGED_AND_FROZEN", "bench_multiplier_effect": "LEGAL_RECOURSE",
                "optionality_effect": "Preserves a T3 Center safety net", "cross_position": True,
                "formation_before": "2-2-1", "formation_after": "1-3-1",
            }],
            "captain": {
                "recommendation": "SWITCH_CAPTAIN", "current_captain": players[0]["name"],
                "current_captain_realized_fp": 11.4, "candidate_captain": players[7]["name"],
                "candidate_statistics": {"expected_fp": 25.3, "p10": 10.5, "p50": 24.8,
                                         "p90": 39.1, "upside_probabilities": {"30": 0.34}},
                "expected_final_score_keep": 226.1, "expected_final_score_switch": 231.5,
                "expected_gain": 5.4,
                "probability_switch_produces_better_final_result": 0.63,
                "statistical_reasons": ["Candidate P90 is 39.1 FP", "Simulated final-team mean improves by 5.4 FP"],
            },
            "turn_portfolio": {
                "players_by_turn": {"1": 4, "2": 4, "3": 2},
                "later_replacements": [{"name": players[7]["name"], "turn": 2, "position": "FORWARD"}],
                "later_captain_options": [{"name": players[7]["name"], "p90": 39.1}],
                "positions_without_later_safety_net": ["CENTER"],
                "turn_diversification_forced": False,
                "expected_adaptation_value": 5.4,
            },
        },
    }


def _demo_monitoring(now: datetime) -> dict[str, Any]:
    rounds = []
    for matchday in (1, 2):
        rounds.append({
            "season": "E2025", "matchday": matchday, "transfers_used": 2,
            "transfers_saved": 1, "average_roster_credits": 10.8 + matchday * .1,
            "bank_credits": 2.0, "expensive_player_concentration": .39,
            "average_fp_per_credit": 1.91, "turn_allocation": {"1": 4, "2": 4, "3": 2},
            "captain_risk_width": 27.2, "captain_switches_recommended": 1,
            "bench_switches_recommended": 1, "sixth_man_usage": 1,
            "average_roster_p10_p90_width": 22.4, "t1_volatility_width": 24.0,
            "high_risk_selection_frequency": .3, "force_include_count": 0,
            "exclude_count": 1, "availability_override_count": 1,
            "recommendation_reversed_after_turn": matchday == 2,
        })
    return {
        "mode": "READ_ONLY_DIAGNOSTIC", "optimization_modified": False,
        "sample_status": "INSUFFICIENT_LIVE_SAMPLE", "minimum_trend_sample": 6,
        "summary": {"shadow_matchdays": 2, "evaluated_matchdays": 1,
                    "mean_player_mae": 5.8, "mean_player_rmse": 7.4,
                    "mean_expected_minutes_mae": 3.2,
                    "mean_regret_vs_hindsight_oracle": 28.1,
                    "price_shadow_rows": 14, "price_mae": .42, "price_rmse": .58,
                    "price_direction_accuracy": .57},
        "rounds": rounds, "alerts": [], "live_validation": [],
        "tracked_metrics": [], "generated_at": now.isoformat(),
    }
