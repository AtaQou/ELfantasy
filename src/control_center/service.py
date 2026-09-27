"""Application service connecting the browser UI to existing live and strategy pipelines."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable, Mapping

import pandas as pd

from scripts.optimize_fantasy import _load_pools
from src.db.database import DEFAULT_DATABASE_PATH
from src.live.availability import clear_availability_override
from src.live.current_season import update_current_season
from src.live.freshness import live_status
from src.live.phase5b import apply_user_decision
from src.live.pipeline import update_live
from src.live.prediction import run_live_prediction
from src.live.rules_compatibility import resolve_ruleset_compatibility
from src.live.scoring_rules import resolve_scoring_rule_compatibility
from src.strategy.rules import (
    FantasyRules, RuleViolation, load_rules, normalize_position, register_rules_manifest,
)

from .advisor import build_after_turn_advice
from .monitoring import monitoring_report
from .price import (
    DEFAULT_ARTIFACT_PATH,
    load_price_artifact,
    predict_price_rows,
    register_price_artifact,
)
from .recommendations import generate_recommendations, validate_current_team
from .repository import ControlCenterRepository, recommendation_for_entity_roster
from .shadow import (
    attach_completed_turn_results,
    evaluate_shadow_snapshot,
    lineup_from_recommendation,
)
from .state import (
    CONTROL_CENTER_VERSION,
    ControlCenterState,
    availability_presentation,
    json_fingerprint,
    rank_players,
    state_from_payload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)
DEFAULT_FANTASY_CONFIG = PROJECT_ROOT / "data" / "reference" / "fantasy_live_sources.json"
DEFAULT_SNAPSHOT_ROOT = (
        PROJECT_ROOT / "data" / "derived" / "phase8a" / "optimization_snapshots"
)


class ControlCenterService:
    def __init__(
            self,
            database_path: Path | str = DEFAULT_DATABASE_PATH,
            *,
            profile_id: str = "default",
            fantasy_config: Path | str | None = DEFAULT_FANTASY_CONFIG,
            snapshot_root: Path | str = DEFAULT_SNAPSHOT_ROOT,
            rules: FantasyRules | None = None,
            update_runner: Callable[..., Any] = update_live,
            turn_update_runner: Callable[..., Any] = update_current_season,
            prediction_runner: Callable[..., Any] = run_live_prediction,
            pool_loader: Callable[..., tuple[pd.DataFrame, pd.DataFrame]] = _load_pools,
            recommendation_runner: Callable[..., dict[str, Any]] = generate_recommendations,
            price_artifact_path: Path | str = DEFAULT_ARTIFACT_PATH,
    ) -> None:
        self.database_path = Path(database_path)
        self.profile_id = profile_id
        self.fantasy_config = Path(fantasy_config) if fantasy_config else None
        self.snapshot_root = Path(snapshot_root)
        self.rules = rules or load_rules()
        self.update_runner = update_runner
        self.turn_update_runner = turn_update_runner
        self.prediction_runner = prediction_runner
        self.pool_loader = pool_loader
        self.recommendation_runner = recommendation_runner
        self._refresh_lock = threading.Lock()
        self.repository = ControlCenterRepository(self.database_path)
        register_rules_manifest(self.rules, database_path=self.database_path)
        self.price_artifact_path = Path(price_artifact_path)
        self.price_artifact = load_price_artifact(self.price_artifact_path)
        if self.price_artifact is not None:
            register_price_artifact(
                self.price_artifact, self.database_path,
                artifact_path=self.price_artifact_path,
            )

    def bootstrap(self) -> dict[str, Any]:
        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        if state.fingerprint != self.repository.load_state(self.profile_id).fingerprint:
            self.repository.save_state(state)
        dashboard = self.dashboard(state)
        players = self.players(state=state)
        shadow = self.current_shadow_snapshot(state)
        return {
            "version": CONTROL_CENTER_VERSION, "dashboard": dashboard,
            "mode": {"demo": False, "production_data_used": True},
            "state": state.as_payload(), "current_team": self.current_team(state, players),
            "players": players, "availability": players,
            "market": self.repository.market_entities(
                state.season_code, state.fantasy_matchday
            ),
            "latest_recommendation": self.repository.latest_run(self.profile_id),
            "latest_shadow_snapshot": shadow,
            "latest_advisor": self.repository.latest_advisor_run(
                shadow["shadow_snapshot_id"]
            ) if shadow else None,
            "monitoring": self.monitoring(),
            "ranking_views": [
                "BEST_EXPECTED_FP", "BEST_VALUE", "MOST_EXPECTED_MINUTES",
                "HIGHEST_UPSIDE", "SAFEST", "VALUE_CREDIT_GROWTH",
            ],
        }

    def team_context(self) -> dict[str, Any]:
        """Return only profile-specific roster state for fast team switching."""

        stored = self.repository.load_state(self.profile_id)
        state = self._state_with_matchday(stored)
        if state.fingerprint != stored.fingerprint:
            self.repository.save_state(state)
        return {"state": state.as_payload()}

    def dashboard(self, state: ControlCenterState | None = None) -> dict[str, Any]:
        state = state or self._state_with_matchday(self.repository.load_state(self.profile_id))
        status = live_status(state.season_code, self.database_path)
        scoring = resolve_scoring_rule_compatibility(state.season_code)
        rules_allowed, rules_reason = self._rules_gate(state.season_code, status)
        context = self.repository.prediction_context(
            state.season_code, state.fantasy_matchday
        )
        current, current_reasons = self._prediction_current(context, state, status)
        blocked = []
        if not rules_allowed:
            blocked.append(rules_reason)
        if not scoring.production_ready:
            blocked.append(scoring.reason)
        return {
            **status,
            "current_matchday": state.fantasy_matchday,
            "market_snapshot_timestamp": status.get("fantasy_captured_at"),
            "availability_timestamp": _latest_freshness_success(status, "availability"),
            "last_successful_refresh_timestamp": _last_success(status),
            "predictions_current": current,
            "prediction": context,
            "rules_gate": {
                "passed": rules_allowed, "reason": rules_reason,
                "version": self.rules.ruleset_version,
                "fingerprint": self.rules.fingerprint,
            },
            "scoring_gate": {
                "passed": scoring.production_ready, "state": scoring.state,
                "reason": scoring.reason, "fingerprint": scoring.evidence_fingerprint,
            },
            "optimization_blocked": bool(blocked),
            "blocked_reasons": list(dict.fromkeys(filter(None, blocked))),
            "prediction_refresh_needed": not current,
            "prediction_refresh_reasons": current_reasons,
        }

    def refresh(self) -> dict[str, Any]:
        if not self._refresh_lock.acquire(blocking=False):
            return {
                "status": "ALREADY_RUNNING", "retryable": True,
                "message": "A live refresh is already running. This duplicate request was ignored.",
                "steps": [], "changes": _empty_refresh_changes(),
                "prediction": None, "dashboard": self.dashboard(),
            }
        started = time.monotonic()
        steps: list[dict[str, Any]] = []
        refreshed: Any = None
        prediction: Any = None
        attached_price_outcomes = 0
        shadow_update: list[dict[str, Any]] = []
        partial = False
        try:
            state = self._state_with_matchday(self.repository.load_state(self.profile_id))
            kwargs: dict[str, Any] = {"database_path": self.database_path}
            if self.fantasy_config and self.fantasy_config.is_file():
                kwargs["fantasy_config"] = self.fantasy_config
            try:
                refreshed = self.update_runner(state.season_code, **kwargs)
                refresh_value = _serialize(refreshed) or {}
                for name, value in dict(refresh_value.get("stages", {})).items():
                    steps.append({
                        "name": str(name).replace("_", " "),
                        "status": value.get("status", "UNKNOWN"),
                        "detail": value.get("error") or value.get("reason") or "Completed",
                    })
                partial = refresh_value.get("status") == "PARTIAL" or any(
                    row["status"] in {"FAILED", "PARTIAL"} for row in steps
                )
            except Exception as error:
                LOGGER.exception("Live source refresh failed for %s", state.season_code)
                steps.append({"name": "live sources", "status": "FAILED",
                              "detail": _friendly_error(error)})
                return {
                    "status": "FAILED", "retryable": True,
                    "message": (
                        "Live data refresh failed. The last valid snapshots were preserved; "
                        "review source status and retry."
                    ),
                    "error_code": "LIVE_REFRESH_FAILED", "steps": steps,
                    "changes": _empty_refresh_changes(), "refresh": None,
                    "prediction": None, "price_outcomes_attached": 0,
                    "shadow_update": [], "dashboard": self.dashboard(state),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }

            dashboard = self.dashboard(state)
            if dashboard["rules_gate"]["passed"] and dashboard["scoring_gate"]["passed"]:
                prediction_kwargs: dict[str, Any] = {
                    "database_path": self.database_path, "refresh": False,
                }
                if self.fantasy_config and self.fantasy_config.is_file():
                    prediction_kwargs["fantasy_config"] = self.fantasy_config
                try:
                    prediction = self.prediction_runner(state.season_code, **prediction_kwargs)
                    prediction_value = _serialize(prediction) or {}
                    unresolved = _unresolved_player_ids(prediction_value)
                    if unresolved:
                        with TemporaryDirectory(prefix="elfantasy-refresh-") as directory:
                            decision_file = Path(directory) / "availability.json"
                            decision_file.write_text(
                                json.dumps(
                                    _automatic_availability_decision_payload(), sort_keys=True,
                                ) + "\n",
                                encoding="utf-8",
                            )
                            prediction = self.prediction_runner(
                                state.season_code, decision_file=decision_file,
                                **prediction_kwargs,
                            )
                        prediction_value = _serialize(prediction) or {}
                    prediction_status = prediction_value.get("status", "UNKNOWN")
                    partial = partial or prediction_status != "SUCCEEDED"
                    steps.append({
                        "name": "predictions", "status": prediction_status,
                        "detail": (
                            _prediction_failure_message(prediction_value)
                            or prediction_value.get("error_code")
                            or (
                                "Frozen Phase 6C live predictions refreshed"
                                if prediction_status == "SUCCEEDED"
                                else f"Prediction refresh returned {prediction_status}"
                            )
                        ),
                    })
                except Exception as error:
                    LOGGER.exception("Live prediction refresh failed for %s", state.season_code)
                    partial = True
                    steps.append({"name": "predictions", "status": "FAILED",
                                  "detail": _friendly_error(error)})
            else:
                steps.append({"name": "predictions", "status": "SKIPPED",
                              "detail": "Rules/scoring compatibility gate is blocked"})

            current_state = self._state_with_matchday(self.repository.load_state(self.profile_id))
            try:
                # Materialize immutable secondary price forecasts before a later market appears.
                self.players(state=current_state)
                attached_price_outcomes = self.repository.attach_price_outcomes()
                shadow_update = self._attach_all_shadows()
                steps.append({"name": "shadow validation", "status": "SUCCEEDED",
                              "detail": "Completed outcomes attached without rewriting pre-lock state"})
            except Exception as error:
                LOGGER.exception("Post-refresh shadow/price attachment failed")
                partial = True
                steps.append({"name": "shadow validation", "status": "FAILED",
                              "detail": _friendly_error(error)})
            changes = _refresh_changes(
                _serialize(refreshed) or {}, _serialize(prediction),
                attached_price_outcomes, shadow_update,
            )
            status = "PARTIAL" if partial else "SUCCEEDED"
            return {
                "status": status, "retryable": partial,
                "message": (
                    "Refresh completed with partial failures; last valid values remain clearly marked."
                    if partial else "All supported live inputs refreshed successfully."
                ),
                "refresh": _serialize(refreshed), "prediction": _serialize(prediction),
                "price_outcomes_attached": attached_price_outcomes,
                "shadow_update": shadow_update, "steps": steps, "changes": changes,
                "dashboard": self.dashboard(current_state),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        finally:
            self._refresh_lock.release()

    def save_team(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = self.repository.load_state(self.profile_id).as_payload()
        merged = {**current, **dict(payload), "profile_id": self.profile_id}
        if "roster_entity_ids" in payload:
            previous_roster = set(map(str, current.get("roster_entity_ids", ())))
            next_roster = set(map(str, payload.get("roster_entity_ids", ())))
            unlock_ids = set(merged.get("player_constraints", {})) if not next_roster else previous_roster - next_roster
            constraints = dict(merged.get("player_constraints", {}))
            for entity_id in unlock_ids:
                if str(constraints.get(entity_id, "")).upper() == "FORCE_INCLUDE":
                    constraints.pop(entity_id, None)
            merged["player_constraints"] = constraints
        candidate = state_from_payload(merged, profile_id=self.profile_id)
        total_entities = self.rules.player_count + self.rules.coach_count
        if len(candidate.roster_entity_ids) > total_entities:
            raise ValueError(
                f"Current roster can contain at most {self.rules.player_count} players "
                f"and {self.rules.coach_count} coach."
            )
        if "roster_entity_ids" in payload:
            market = self.repository.market_entities(
                candidate.season_code, candidate.fantasy_matchday
            )
            if market:
                lookup = {str(row["entity_id"]): row for row in market}
                unknown = sorted(set(candidate.roster_entity_ids) - set(lookup))
                if unknown:
                    raise ValueError(
                        "Roster contains entities outside the current market: " + ", ".join(unknown)
                    )
                counts = _roster_counts(candidate.roster_entity_ids, lookup)
                limits = {**dict(self.rules.position_counts), "COACH": self.rules.coach_count}
                overflow = [
                    f"{position.title()} {counts.get(position, 0)}/{limit}"
                    for position, limit in limits.items() if counts.get(position, 0) > limit
                ]
                if overflow:
                    raise ValueError("Roster exceeds positional limits: " + ", ".join(overflow))
        state = self.repository.save_state(candidate)
        return {"state": state.as_payload(), "current_team": self.current_team(state)}

    def set_constraint(self, entity_id: str, value: str) -> dict[str, Any]:
        state = self.repository.load_state(self.profile_id)
        constraints = dict(state.player_constraints)
        normalized = str(value).upper()
        if normalized == "NORMAL":
            constraints.pop(str(entity_id), None)
        else:
            constraints[str(entity_id)] = normalized
        result = self.save_team({"player_constraints": constraints})
        result["warnings"] = []
        if normalized == "FORCE_INCLUDE":
            player = next((row for row in self.players()
                           if str(row.get("entity_id")) == str(entity_id)), None)
            if player and player.get("availability_group") == "RED":
                result["warnings"].append(
                    f"{player.get('name')} is resolved {player.get('availability')}; "
                    "FORCE INCLUDE is explicit and may make optimization infeasible."
                )
        return result

    def set_override(
            self,
            player_id: str,
            decision: str,
            *,
            note: str | None = None,
            clear: bool = False,
    ) -> dict[str, Any]:
        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        if state.fantasy_matchday is None:
            raise ValueError("A current Matchday is required for an availability override")
        if clear:
            event_id = clear_availability_override(
                player_id, database_path=self.database_path,
                season_code=state.season_code, fantasy_matchday=state.fantasy_matchday,
            )
        else:
            event_id = apply_user_decision(
                player_id, decision, database_path=self.database_path,
                season_code=state.season_code, fantasy_matchday=state.fantasy_matchday,
                note=note,
            )
        return {
            "override_event_id": event_id,
            "message": (
                "Override cleared. Refresh predictions before optimizing."
                if clear else "Manual override saved. Refresh predictions before optimizing."
            ),
            "dashboard": self.dashboard(state), "players": self.players(state=state),
        }

    def players(
            self,
            *,
            state: ControlCenterState | None = None,
            view: str = "BEST_EXPECTED_FP",
            filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        state = state or self._state_with_matchday(self.repository.load_state(self.profile_id))
        context = self.repository.prediction_context(
            state.season_code, state.fantasy_matchday, require_scored=True,
        )
        scenario = state.scenario_id
        if context and not scenario and len(context["scenarios"]) == 1:
            scenario = context["scenarios"][0]
        prediction_rows = (
            self.repository.player_predictions(context["prediction_run_id"], scenario)
            if context and scenario else []
        )
        market_rows = self.repository.market_entities(
            state.season_code, state.fantasy_matchday
        )
        market_by_entity = {str(row.get("entity_id")): row for row in market_rows}
        prediction_by_entity = {
            str(row.get("entity_id")): row for row in prediction_rows
        }
        rows = []
        for market_row in market_rows:
            if str(market_row.get("entity_type")).upper() != "PLAYER":
                continue
            market_status = _market_availability_status(market_row)
            row = {
                **market_row, "expected_minutes": None, "expected_fp": None,
                "median_fp": None, "p10_fp": None, "p90_fp": None,
                "p95_fp": None, "prob_fp_le_15": None,
                "prob_fp_ge_30": None, "fp_per_credit": None,
                "recent_form": None, "availability": market_status,
                "source_availability_status": market_status,
                "availability_source": (
                    "official_fantasy_market" if market_status != "UNKNOWN" else None
                ),
                "availability_timestamp": (
                    market_row.get("observed_at") if market_status != "UNKNOWN" else None
                ),
                "manual_override": False, "manual_override_status": None,
                "turn": market_row.get("source_turn"),
            }
            row.update({
                key: value for key, value in prediction_by_entity.get(
                    str(market_row.get("entity_id")), {}
                ).items() if value is not None
            })
            if not row.get("manual_override") and market_status != "UNKNOWN":
                row.update({
                    "availability": market_status,
                    "source_availability_status": market_status,
                    "availability_source": "official_fantasy_market",
                    "availability_timestamp": market_row.get("observed_at"),
                })
            rows.append(row)
        recent_form = self.repository.player_recent_form([
            str(row.get("player_id")) for row in rows if row.get("player_id")
        ])
        for row in rows:
            form = recent_form.get(str(row.get("player_id")), {})
            row["last5_fp_average"] = form.get("last5_fp_average")
            row["last5_minutes_median"] = form.get("last5_minutes_median")
            row["last_game_fp"] = form.get("last_game_fp")
            row["last_game_minutes"] = form.get("last_game_minutes")
            row["season_fp_average"] = form.get("season_fp_average")
            row["season_minutes_average"] = form.get("season_minutes_average")
            row["stats_season_code"] = form.get("season_code")
            row["last5_games"] = form.get("last5_games", [])
        constraints = dict(state.player_constraints)
        for row in rows:
            market_row = market_by_entity.get(str(row.get("entity_id")), {})
            row["team_name"] = (
                market_row.get("team_name") or market_row.get("team_code")
                or row.get("team_name") or row.get("team_id")
            )
            presentation = availability_presentation(row.get("availability"))
            row["availability_group"] = presentation["group"]
            row["availability_icon"] = presentation["icon"]
            row["constraint"] = constraints.get(str(row.get("entity_id")), "NORMAL")
        if context and scenario and rows:
            rows = self._enrich_price_intelligence(rows, context, scenario, state)
        filters = dict(filters or {})
        if filters.get("availability_group"):
            rows = [row for row in rows if row["availability_group"] == filters["availability_group"]]
        for key in ("team_id", "position", "turn"):
            if filters.get(key) not in (None, ""):
                rows = [row for row in rows if str(row.get(key)) == str(filters[key])]
        query = str(filters.get("query", "")).casefold().strip()
        if query:
            rows = [row for row in rows if query in str(row.get("name", "")).casefold()]
        return rank_players(rows, view)

    def team_strategy(self) -> dict[str, Any]:
        """Analyze the saved Current Team; generated rosters are never treated as state."""

        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        players = self.players(state=state)
        current = self.current_team(state, players)
        selected_ids = {str(value) for value in state.roster_entity_ids}
        locked_ids = {
            str(key) for key, value in state.player_constraints.items()
            if value == "FORCE_INCLUDE"
        }
        candidates = [
            row for row in players
            if str(row.get("entity_id")) not in selected_ids
            and row.get("availability_group") != "RED"
        ]
        actions = []
        for row in current["entities"]:
            coach = str(row.get("entity_type", "PLAYER")).upper() == "COACH"
            locked = coach or str(row.get("entity_id")) in locked_ids
            expected = _optional_number(row.get("expected_fp"))
            alternatives = [
                candidate for candidate in candidates
                if not coach
                and normalize_position(str(candidate.get("position")))
                == normalize_position(str(row.get("position")))
                and float(candidate.get("credits") or 0.0)
                <= float(row.get("credits") or 0.0) + state.bank_credits
            ]
            alternatives.sort(
                key=lambda item: _optional_number(item.get("expected_fp")) or float("-inf"),
                reverse=True,
            )
            best = alternatives[0] if alternatives else None
            best_expected = _optional_number(best.get("expected_fp")) if best else None
            recommendation = "KEEP"
            if not locked and best_expected is not None and (
                expected is None or best_expected > expected + 0.05
            ):
                recommendation = (
                    "POSSIBLE_UPGRADE"
                    if float(best.get("credits") or 0.0) > float(row.get("credits") or 0.0)
                    else "POSSIBLE_REPLACEMENT"
                )
            downgrade = next((
                candidate for candidate in sorted(
                    alternatives, key=lambda item: float(item.get("credits") or 0.0)
                )
                if float(candidate.get("credits") or 0.0) < float(row.get("credits") or 0.0)
                and (
                    expected is None
                    or _optional_number(candidate.get("expected_fp")) is not None
                    and float(candidate["expected_fp"]) >= expected - 3.0
                )
            ), None)
            actions.append({
                "entity_id": row.get("entity_id"), "player_id": row.get("player_id"),
                "name": row.get("name"), "team_name": row.get("team_name"),
                "position": "COACH" if coach else normalize_position(str(row.get("position"))),
                "credits": row.get("credits"), "expected_fp": expected,
                "expected_minutes": row.get("expected_minutes"),
                "last5_fp_average": row.get("last5_fp_average"),
                "last5_minutes_median": row.get("last5_minutes_median"),
                "last5_games": row.get("last5_games", []),
                "availability": row.get("availability", "UNKNOWN"),
                "locked": locked, "recommendation": recommendation,
                "alternative": best, "downgrade": downgrade,
            })
        fill_candidates = {}
        for position, count in current["missing_positions"].items():
            choices = [
                row for row in candidates
                if normalize_position(str(row.get("position"))) == position
            ]
            choices.sort(
                key=lambda row: _optional_number(row.get("expected_fp")) or float("-inf"),
                reverse=True,
            )
            fill_candidates[position] = choices[:max(3, count)]
        position_scores = {}
        for position in self.rules.position_counts:
            values = [
                _optional_number(row.get("expected_fp")) for row in current["entities"]
                if str(row.get("entity_type", "PLAYER")).upper() != "COACH"
                and normalize_position(str(row.get("position"))) == position
            ]
            finite = [value for value in values if value is not None]
            position_scores[position] = round(sum(finite) / len(finite), 2) if finite else None
        ranked_positions = sorted(
            (value, position) for position, value in position_scores.items() if value is not None
        )
        return {
            "source": "CURRENT_TEAM", "state_fingerprint": state.fingerprint,
            "season": state.season_code, "matchday": state.fantasy_matchday,
            "bank_credits": state.bank_credits, "current_team": current,
            "actions": actions, "fill_candidates": fill_candidates,
            "position_scores": position_scores,
            "weak_position": ranked_positions[0][1] if ranked_positions else None,
            "strong_position": ranked_positions[-1][1] if ranked_positions else None,
        }

    def history(self, section: str, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        normalized = str(section or "overview").lower()
        catalog = self.repository.history_catalog()
        if normalized == "overview":
            data: Any = self.repository.history_overview()
        elif normalized == "games":
            data = self.repository.history_games(filters)
        elif normalized == "players":
            data = self.repository.history_players(filters)
        elif normalized == "teams":
            data = self.repository.history_teams(filters)
        elif normalized == "preseason":
            data = self.repository.history_preseason_games(filters)
        else:
            raise ValueError(f"unsupported History section: {section}")
        return {"section": normalized, "catalog": catalog, "data": data}

    def history_game_detail(self, game_id: str) -> dict[str, Any]:
        return self.repository.history_game_detail(game_id)

    def history_player_detail(
            self, player_id: str, filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.history_player_detail(player_id, filters)

    def history_team_detail(
            self, team_id: str, filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.history_team_detail(team_id, filters)

    def _resolved_current_roster(
            self, state: ControlCenterState,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Resolve every saved entity through the same market path for all workflows."""

        requested = list(map(str, state.roster_entity_ids))
        lookup = {
            str(row["entity_id"]): row
            for row in self.repository.market_entities(
                state.season_code, state.fantasy_matchday
            )
        }
        return (
            [dict(lookup[entity_id]) for entity_id in requested if entity_id in lookup],
            [entity_id for entity_id in requested if entity_id not in lookup],
        )

    def current_team(
            self, state: ControlCenterState, players: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        market, missing_entities = self._resolved_current_roster(state)
        predictions = {str(row.get("entity_id")): row for row in (players or [])}
        selected = []
        for row in market:
            merged = {**row, **{
                key: value for key, value in predictions.get(str(row["entity_id"]), {}).items()
                if value is not None
            }}
            merged.update(availability_presentation(merged.get("availability")))
            selected.append(merged)
        value = float(sum(float(row.get("credits") or 0.0) for row in selected))
        lookup = {str(row["entity_id"]): row for row in selected}
        counts = _roster_counts(state.roster_entity_ids, lookup)
        requirements = {**dict(self.rules.position_counts), "COACH": self.rules.coach_count}
        validation_errors = [
            f"{position.title()}: {counts.get(position, 0)}/{required} selected"
            for position, required in requirements.items()
            if counts.get(position, 0) != required
        ]
        missing_positions = {
            position: required - counts.get(position, 0)
            for position, required in requirements.items()
            if counts.get(position, 0) < required
        }
        partial_validation_errors = []
        if missing_entities:
            partial_validation_errors.append(
                "Selected entities are missing from the current market: "
                + ", ".join(missing_entities)
            )
        overflow = [
            f"{position.title()} {counts.get(position, 0)}/{required}"
            for position, required in requirements.items()
            if counts.get(position, 0) > required
        ]
        if overflow:
            partial_validation_errors.append(
                "Selected roster exceeds positional limits: " + ", ".join(overflow)
            )
        return {
            "entities": selected, "roster_size": len(selected),
            "roster_market_value": value, "bank_credits": state.bank_credits,
            "available_budget": value + state.bank_credits,
            "transfers_available": state.transfers_available,
            "complete": not validation_errors,
            "valid_partial": not partial_validation_errors,
            "position_counts": counts, "requirements": requirements,
            "validation_errors": validation_errors,
            "partial_validation_errors": partial_validation_errors,
            "missing_positions": missing_positions,
            "slots_to_fill": sum(missing_positions.values()),
        }

    def optimize(
            self,
            *,
            mode: str = "CURRENT_TEAM",
            total_budget: float | None = None,
            bank_credits: float | None = None,
            max_changes: int | None = None,
            seed: int = 20250801,
            training_simulations: int = 96,
            evaluation_simulations: int = 512,
    ) -> dict[str, Any]:
        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        optimization_mode = str(mode).upper()
        if optimization_mode not in {"BUILD_NEW", "COMPLETE_ROSTER", "CURRENT_TEAM"}:
            raise ValueError(f"unsupported optimization mode: {mode}")
        state_payload = state.as_payload()
        if bank_credits is not None:
            state_payload["bank_credits"] = float(bank_credits)
        if max_changes is not None:
            state_payload["transfers_available"] = int(max_changes)
        state = state_from_payload(state_payload, profile_id=state.profile_id)
        maximum_changes = self.rules.player_count + self.rules.coach_count
        if state.transfers_available > maximum_changes:
            raise ValueError(
                f"maximum changes cannot exceed the roster size of {maximum_changes}"
            )
        if total_budget is not None and float(total_budget) < 0:
            raise ValueError("total budget cannot be negative")
        dashboard = self.dashboard(state)
        if dashboard["optimization_blocked"]:
            return self._blocked_run(state, dashboard["blocked_reasons"])
        prediction_generated_on_demand = False
        conservative_availability_assumptions = False
        availability_assumption_policy: str | None = None
        if not dashboard["predictions_current"]:
            prediction_kwargs: dict[str, Any] = {
                "database_path": self.database_path, "refresh": False,
            }
            if self.fantasy_config and self.fantasy_config.is_file():
                prediction_kwargs["fantasy_config"] = self.fantasy_config
            try:
                context = dashboard.get("prediction") or {}
                unresolved = _unresolved_player_ids(context)
                same_market = (
                    context.get("market_snapshot_batch_id")
                    == dashboard.get("fantasy_snapshot_batch_id")
                )
                if not unresolved or not same_market:
                    generated = self.prediction_runner(
                        state.season_code, **prediction_kwargs
                    )
                    unresolved = _unresolved_player_ids(_serialize(generated) or {})
                if unresolved:
                    decision_payload = _automatic_availability_decision_payload()
                    with TemporaryDirectory(prefix="elfantasy-optimize-") as directory:
                        decision_file = Path(directory) / "availability.json"
                        decision_file.write_text(
                            json.dumps(decision_payload, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                        generated = self.prediction_runner(
                            state.season_code,
                            decision_file=decision_file,
                            **prediction_kwargs,
                        )
                    conservative_availability_assumptions = True
                    availability_assumption_policy = "PLAY_ALL_UNRESOLVED"
                prediction_generated_on_demand = True
            except Exception as error:
                LOGGER.exception("On-demand prediction generation failed for %s", state.season_code)
                return self._blocked_run(
                    state,
                    [f"Prediction generation failed: {_friendly_error(error)}"],
                )
            dashboard = self.dashboard(state)
            if not dashboard["predictions_current"]:
                generated_value = _serialize(generated) or {}
                reasons = [
                    generated_value.get("error_message"),
                    *(dashboard.get("prediction_refresh_reasons") or []),
                ]
                if not any(reasons):
                    reasons = [
                        f"Prediction generation returned {generated_value.get('status', 'no usable snapshot')}"
                    ]
                return self._blocked_run(
                    state, list(dict.fromkeys(str(value) for value in reasons if value)),
                    context=dashboard.get("prediction"),
                )
        context = dashboard["prediction"]
        if state.fantasy_matchday is None or context is None:
            return self._blocked_run(state, ["Current Matchday/prediction is unavailable"])
        scenario = state.scenario_id
        if not scenario and len(context["scenarios"]) == 1:
            scenario = context["scenarios"][0]
        if not scenario:
            return self._blocked_run(
                state, ["Multiple availability scenarios exist; select one before optimization"]
            )
        try:
            players, coaches = self.pool_loader(
                state.season_code, state.fantasy_matchday, self.database_path, scenario,
                context["prediction_run_id"],
            )
            detail_rows = self.repository.player_predictions(
                context["prediction_run_id"], scenario
            )
            detail_lookup = {str(row["player_id"]): row for row in detail_rows}
            if "expected_minutes" not in players:
                players["expected_minutes"] = players["player_id"].astype(str).map(
                    lambda player: detail_lookup.get(player, {}).get("expected_minutes")
                )
            recent_form = self.repository.player_recent_form(
                players["player_id"].astype(str).tolist()
            )
            players["last5_fp_average"] = players["player_id"].astype(str).map(
                lambda player: recent_form.get(player, {}).get("last5_fp_average")
            )
            players["last5_minutes_median"] = players["player_id"].astype(str).map(
                lambda player: recent_form.get(player, {}).get("last5_minutes_median")
            )
            players["last5_games"] = players["player_id"].astype(str).map(
                lambda player: recent_form.get(player, {}).get("last5_games", [])
            )
            market_rows = self.repository.market_entities(
                state.season_code, state.fantasy_matchday
            )
            if optimization_mode in {"COMPLETE_ROSTER", "CURRENT_TEAM"}:
                validation = validate_current_team(
                    state, players, coaches, self.rules, market_entities=market_rows,
                )
                if not validation["legal"]:
                    return self._blocked_run(state, validation["errors"], context=context)
                if optimization_mode == "CURRENT_TEAM" and not validation["complete"]:
                    return self._blocked_run(state, [
                        "Improve My Team requires a complete current roster; "
                        "use Complete My Roster to fill empty positions"
                    ], context=context)
            result = self.recommendation_runner(
                players, coaches, self.rules, state, seed=seed,
                training_simulations=training_simulations,
                evaluation_simulations=evaluation_simulations,
                mode=optimization_mode, total_budget=total_budget,
            )
            market_lookup = {
                str(row["entity_id"]): row
                for row in market_rows
            }
            for recommendation in result.get("recommendations", []):
                for entity in [
                    *recommendation.get("players", []),
                    recommendation.get("coach", {}),
                    *recommendation.get("players_out", []),
                    *recommendation.get("players_in", []),
                ]:
                    market_row = market_lookup.get(str(entity.get("entity_id")), {})
                    entity["team_name"] = (
                        market_row.get("team_name") or market_row.get("team_code")
                        or entity.get("team_name") or entity.get("team_id")
                    )
        except (ValueError, RuleViolation) as error:
            return self._blocked_run(state, [str(error)], context=context)
        overrides = self.repository.active_overrides(state.season_code, state.fantasy_matchday)
        snapshot = {
            "market_fingerprint": context["market_snapshot_fingerprint"],
            "prediction_fingerprint": context["prediction_fingerprint"],
            "prediction_run_id": context["prediction_run_id"],
            "scenario_id": state.scenario_id,
            "predictive_artifact_fingerprint": context["predictive_artifact_fingerprint"],
            "ruleset_version": self.rules.ruleset_version,
            "rules_fingerprint": self.rules.fingerprint,
            "manual_override_fingerprint": json_fingerprint(overrides),
        }
        payload = {
            "status": "SUCCEEDED", "version": CONTROL_CENTER_VERSION,
            "profile_id": state.profile_id, "season": state.season_code,
            "matchday": state.fantasy_matchday, "generated_at": datetime.now(UTC).isoformat(),
            "optimization_mode": optimization_mode,
            "prediction_generated_on_demand": prediction_generated_on_demand,
            "conservative_availability_assumptions": conservative_availability_assumptions,
            "availability_assumption_policy": availability_assumption_policy,
            "snapshot": snapshot, "manual_overrides": overrides,
            "current_roster": list(state.roster_entity_ids),
            "bank_credits": state.bank_credits,
            "transfers_available": state.transfers_available,
            "total_budget": (
                float(result["available_budget"])
                if optimization_mode == "BUILD_NEW" else None
            ),
            "constraints": dict(state.player_constraints), **result,
        }
        payload["input_fingerprint"] = json_fingerprint({
            "snapshot": snapshot, "current_roster": payload["current_roster"],
            "bank_credits": state.bank_credits,
            "transfers_available": state.transfers_available,
            "optimization_mode": optimization_mode,
            "total_budget": payload["total_budget"],
            "constraints": payload["constraints"], "scenario_id": scenario,
            "simulation": result["simulation"],
        })
        path = self._write_snapshot(payload)
        payload["snapshot_path"] = str(path)
        payload["control_center_run_id"] = self.repository.persist_run(
            payload, snapshot_path=path
        )
        cutoffs = [
            pd.to_datetime(row.get("scheduled_tip_time"), utc=True).to_pydatetime()
            for row in detail_rows if row.get("scheduled_tip_time") is not None
        ]
        if cutoffs:
            payload["shadow"] = self.repository.persist_prelock_snapshot(
                payload, scenario_id=scenario, decision_cutoff_at=min(cutoffs),
                decision_inputs={
                    "players": _serialize(players.to_dict("records")),
                    "coaches": _serialize(coaches.to_dict("records")),
                },
            )
        else:
            payload["shadow"] = {
                "status": "NOT_PRELOCK", "reason": "first-tip cutoff is unavailable"
            }
        return payload

    def reevaluate_strategy(
            self,
            *,
            current_lineup: Mapping[str, Any] | None = None,
            refresh_first: bool = True,
            simulations: int = 512,
            seed: int = 20250802,
    ) -> dict[str, Any]:
        """Refresh completed results and advise only on still-unplayed roster players."""

        refresh_payload = self._refresh_turn_results() if refresh_first else None
        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        snapshot = self.current_shadow_snapshot(state)
        if snapshot is None:
            return self._current_roster_turn_analysis(state, refresh_payload)
        if current_lineup is None:
            matched_recommendation = snapshot.get(
                "matched_recommendation"
            ) or recommendation_for_entity_roster(
                snapshot.get("recommendations", []), state.roster_entity_ids,
            )
            if matched_recommendation is None:
                return self._current_roster_turn_analysis(state, refresh_payload)
            matched_lineup = lineup_from_recommendation(matched_recommendation)
            current_lineup = {
                "player_ids": list(matched_lineup.player_ids),
                "coach_id": matched_lineup.coach_id,
                "starters": sorted(matched_lineup.starters),
                "sixth_man": matched_lineup.sixth_man,
                "captain": matched_lineup.captain,
            }
        attached = attach_completed_turn_results(
            self.repository, snapshot["shadow_snapshot_id"], current_lineup=current_lineup,
        )
        if attached.get("status") == "NO_COMPLETED_TURN":
            return {**attached, "refresh": refresh_payload}
        outcome = self.repository.latest_turn_outcome(snapshot["shadow_snapshot_id"])
        if outcome is None:
            return {"status": "BLOCKED", "reason": "Completed-Turn outcome attachment failed"}
        advice = build_after_turn_advice(
            snapshot, outcome, self.rules, current_lineup=current_lineup,
            simulations=simulations, seed=seed,
        )
        persist_payload = {
            **advice, "shadow_snapshot_id": snapshot["shadow_snapshot_id"],
            "turn_outcome_id": outcome["turn_outcome_id"],
        }
        advisor_id = self.repository.persist_advisor_run(persist_payload)
        return {**advice, "advisor_run_id": advisor_id,
                "shadow_snapshot_id": snapshot["shadow_snapshot_id"],
                "turn_outcome_id": outcome["turn_outcome_id"], "refresh": refresh_payload}

    def _current_roster_turn_analysis(
            self,
            state: ControlCenterState,
            refresh_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Provide role-conditional advice when only the exact current roster is known."""

        if state.fantasy_matchday is None:
            return {
                "status": "BLOCKED", "reason": "Current Matchday is unavailable",
                "refresh": refresh_payload,
            }
        selected_rows, missing_entities = self._resolved_current_roster(state)
        player_market = [
            row for row in selected_rows
            if str(row.get("entity_type")).upper() == "PLAYER" and row.get("player_id")
        ]
        coaches = [
            row for row in selected_rows
            if str(row.get("entity_type")).upper() == "COACH"
        ]
        unmapped_players = [
            row for row in selected_rows
            if str(row.get("entity_type")).upper() == "PLAYER" and not row.get("player_id")
        ]
        if (len(player_market) != self.rules.player_count
                or len(coaches) != self.rules.coach_count
                or missing_entities or unmapped_players):
            details = [
                f"resolved {len(player_market)}/{self.rules.player_count} players",
                f"resolved {len(coaches)}/{self.rules.coach_count} coach",
            ]
            if missing_entities:
                details.append("missing market entities: " + ", ".join(missing_entities))
            if unmapped_players:
                details.append(
                    "unmapped players: "
                    + ", ".join(str(row.get("name") or row.get("entity_id"))
                                for row in unmapped_players)
                )
            return {
                "status": "BLOCKED",
                "reason": "The saved roster could not be resolved: " + "; ".join(details),
                "refresh": refresh_payload,
            }
        coach = coaches[0]
        prediction_rows = self.players(state=state)
        predictions = {
            str(row.get("player_id")): row for row in prediction_rows
            if row.get("player_id")
        }
        outcomes = self.repository.current_roster_game_outcomes(
            state.season_code, state.fantasy_matchday, player_market
        )
        roster: list[dict[str, Any]] = []
        for market_row in player_market:
            player_id = str(market_row["player_id"])
            prediction = predictions.get(player_id, {})
            outcome = outcomes.get(player_id, {})
            turn = int(market_row.get("source_turn") or prediction.get("turn") or 1)
            played = bool(outcome.get("played"))
            roster.append({
                "entity_id": str(market_row["entity_id"]),
                "player_id": player_id,
                "name": str(market_row.get("name") or prediction.get("name") or player_id),
                "team_name": market_row.get("team_name"),
                "position": normalize_position(market_row.get("position")),
                "turn": turn,
                "game_id": outcome.get("game_id") or prediction.get("game_id"),
                "game_date": outcome.get("game_date") or prediction.get("scheduled_tip_time"),
                "played": played,
                "actual_fp": outcome.get("actual_fp") if played else None,
                "actual_minutes": outcome.get("actual_minutes") if played else None,
                "actual_fp_provenance": outcome.get("actual_fp_provenance") if played else None,
                "expected_fp": _optional_number(prediction.get("expected_fp")),
                "expected_minutes": _optional_number(prediction.get("expected_minutes")),
                "p10": _optional_number(prediction.get("p10_fp")),
                "p50": _optional_number(prediction.get("p50_fp")),
                "p90": _optional_number(prediction.get("p90_fp")),
            })
        turns = sorted({int(row["turn"]) for row in roster})
        completed_turn = 0
        for turn in turns:
            group = [row for row in roster if int(row["turn"]) == turn]
            if group and all(row["played"] for row in group):
                completed_turn = turn
            else:
                break
        played_rows = [
            row for row in roster if row["played"] and int(row["turn"]) <= completed_turn
        ]
        upcoming_rows = [row for row in roster if not row["played"]]
        if completed_turn == 0:
            return {
                "status": "NO_COMPLETED_TURN",
                "reason": "No complete Turn is available for the current roster",
                "roster": roster, "refresh": refresh_payload,
            }
        swaps: list[dict[str, Any]] = []
        for position in ("GUARD", "FORWARD", "CENTER"):
            completed = sorted(
                (row for row in played_rows
                 if row["position"] == position and row.get("actual_fp") is not None),
                key=lambda row: float(row["actual_fp"]),
            )
            future = sorted(
                (row for row in upcoming_rows
                 if row["position"] == position and row.get("expected_fp") is not None),
                key=lambda row: float(row["expected_fp"]), reverse=True,
            )
            for outgoing, incoming in zip(completed, future):
                gain = float(incoming["expected_fp"]) - float(outgoing["actual_fp"])
                if gain <= 0:
                    continue
                swaps.append({
                    "recommendation": "SWAP_IF_LEGAL",
                    "played_player_id": outgoing["player_id"],
                    "played_player_name": outgoing["name"],
                    "played_player_position": position,
                    "realized_fp": outgoing["actual_fp"],
                    "new_player_id": incoming["player_id"],
                    "new_player_name": incoming["name"],
                    "new_player_position": position,
                    "expected_fp": incoming["expected_fp"],
                    "expected_minutes": incoming["expected_minutes"],
                    "p10": incoming["p10"], "p50": incoming["p50"],
                    "p90": incoming["p90"], "projected_gain": gain,
                    "condition": (
                        "Apply only if the played player is currently on the field and the "
                        "upcoming player is currently on the bench."
                    ),
                })
        captain_candidate = max(
            (row for row in upcoming_rows if row.get("expected_fp") is not None),
            key=lambda row: float(row["expected_fp"]), default=None,
        )
        return {
            "status": "CONDITIONAL_READY",
            "completed_turn": completed_turn,
            "reason": (
                "Turn split and realized scores are verified. Starter, bench, sixth-man, and "
                "captain roles were not saved, so actions are conditional on the roles shown "
                "in your Fantasy team."
            ),
            "role_context": "INFERRED_FROM_CURRENT_ROSTER_AND_TURNS",
            "roster": sorted(roster, key=lambda row: (row["turn"], row["position"], row["name"])),
            "played_players": sorted(played_rows, key=lambda row: float(row.get("actual_fp") or 0.0),
                                     reverse=True),
            "upcoming_players": sorted(
                upcoming_rows, key=lambda row: float(row.get("expected_fp") or -1e9), reverse=True
            ),
            "conditional_swaps": swaps,
            "captain_candidate": captain_candidate,
            "coach": coach,
            "refresh": refresh_payload,
        }

    def current_shadow_snapshot(
            self, state: ControlCenterState | None = None,
    ) -> dict[str, Any] | None:
        """Return only a pre-lock snapshot for the exact currently saved roster."""

        current = state or self._state_with_matchday(
            self.repository.load_state(self.profile_id)
        )
        return self.repository.latest_shadow_snapshot(
            self.profile_id,
            season=current.season_code,
            matchday=current.fantasy_matchday,
            roster_entity_ids=current.roster_entity_ids,
        )

    def _refresh_turn_results(self) -> dict[str, Any]:
        """Refresh schedule and box scores without rebuilding the prediction market."""

        started = time.monotonic()
        state = self._state_with_matchday(self.repository.load_state(self.profile_id))
        try:
            refreshed = self.turn_update_runner(
                state.season_code,
                database_path=self.database_path,
                include_rich=False,
            )
            value = _serialize(refreshed) or {}
            warnings = list(value.get("warnings") or [])
            return {
                "status": "PARTIAL" if warnings else "SUCCEEDED",
                "mode": "TURN_RESULTS_ONLY",
                "warnings": warnings,
                "result": value,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        except Exception as error:
            LOGGER.exception("Turn-results refresh failed for %s", state.season_code)
            return {
                "status": "FAILED", "mode": "TURN_RESULTS_ONLY",
                "error": _friendly_error(error),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }

    def shadow_validation(self, shadow_snapshot_id: str | None = None) -> dict[str, Any]:
        snapshot = (
            self.repository.shadow_snapshot(shadow_snapshot_id) if shadow_snapshot_id
            else self.current_shadow_snapshot()
        )
        if snapshot is None:
            return {"status": "NO_SHADOW_SNAPSHOT"}
        attachment = attach_completed_turn_results(
            self.repository, snapshot["shadow_snapshot_id"]
        )
        latest = self.repository.latest_turn_outcome(snapshot["shadow_snapshot_id"])
        if latest is None:
            return attachment
        last_turn = max(
            int(player["turn"]) for recommendation in snapshot["recommendations"]
            if recommendation.get("label") == "BEST_OVERALL"
            for player in recommendation["players"]
        )
        if int(latest["completed_turn"]) < last_turn:
            return {"status": "MATCHDAY_INCOMPLETE", "turn_outcome": latest}
        return evaluate_shadow_snapshot(
            self.repository, snapshot["shadow_snapshot_id"], self.rules
        )

    def monitoring(self) -> dict[str, Any]:
        return monitoring_report(self.database_path, profile_id=self.profile_id)

    def player_detail(self, player_id: str) -> dict[str, Any]:
        rows = self.players()
        player = next((row for row in rows if str(row.get("player_id")) == str(player_id)), None)
        if player is None:
            raise ValueError(f"unknown player in current slate: {player_id}")
        latest = self.repository.latest_run(self.profile_id) or {}
        recommendations = latest.get("recommendations", [])
        selections = [
            recommendation["label"] for recommendation in recommendations
            if any(str(row.get("player_id")) == str(player_id)
                   for row in recommendation.get("players", []))
        ]
        selected_rows = [
            row for recommendation in recommendations
            for row in recommendation.get("players", [])
            if str(row.get("player_id")) == str(player_id)
        ]
        selected_row = selected_rows[0] if selected_rows else {}
        alternatives = []
        snapshot = self.current_shadow_snapshot()
        if snapshot:
            alternatives = [
                row for row in snapshot["knowledge"].get("player_alternatives", [])
                if str(row.get("player", {}).get("player_id")) == str(player_id)
                or str(row.get("replaces", {}).get("player_id")) == str(player_id)
            ][:2]
        return {
            "player": player,
            "performance": {key: player.get(key) for key in (
                "expected_fp", "expected_minutes", "p10_fp", "p50_fp", "p90_fp", "p95_fp",
                "prob_fp_ge_20", "prob_fp_ge_25", "prob_fp_ge_30", "prob_fp_ge_35",
                "prob_fp_ge_40", "prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15",
                "recent_form", "expected_role", "recent_minutes", "previous_season_minutes",
                "minutes_trend", "role_trend", "uncalibrated_expected_minutes",
                "fp_per_minute", "last5_fp_average", "last5_minutes_median",
                "last5_games",
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
                "turn": player.get("turn"), "selected_in": selections,
                "role": selected_row.get("role"),
                "captain_suitability": (
                    "Selected captain" if selected_row.get("captain")
                    else "Available to Phase 7 captain recourse" if selections else "Not selected"
                ),
                "replacement_optionality": (
                    "Inspect the Strategy page for the snapshot-specific later safety net."
                    if selections else "No optionality because the player is outside this roster."
                ),
                "why_selected": (
                    "Selected by frozen Phase 7 expected final-team value and legal Turn recourse."
                    if selections else None
                ),
                "why_not_selected": (
                    "Another legal roster had higher simulated final-team value under current constraints."
                    if not selections else None
                ),
                "alternatives": alternatives,
            },
        }

    def _enrich_price_intelligence(
            self, rows: list[dict[str, Any]], context: Mapping[str, Any], scenario: str,
            state: ControlCenterState,
    ) -> list[dict[str, Any]]:
        if self.price_artifact is None or state.fantasy_matchday is None:
            for row in rows:
                row.update(_empty_price_fields())
            return rows
        existing = self.repository.latest_price_predictions(
            context["prediction_run_id"], scenario
        )
        if not existing and context.get("status") in {"SUCCEEDED", "PARTIAL"}:
            features = self.repository.price_feature_rows(
                state.season_code, state.fantasy_matchday, rows
            )
            predicted = predict_price_rows(features, self.price_artifact)
            self.repository.persist_price_predictions(
                context["prediction_run_id"], scenario, state.season_code,
                state.fantasy_matchday, predicted,
                self.price_artifact["artifact_fingerprint"],
                predicted_at=pd.to_datetime(context["generated_at"], utc=True).to_pydatetime(),
            )
            existing = self.repository.latest_price_predictions(
                context["prediction_run_id"], scenario
            )
        for row in rows:
            value = existing.get(str(row.get("entity_id")))
            row.update(value or _empty_price_fields())
        values = [float(row["fp_per_credit"]) for row in rows
                  if row.get("fp_per_credit") is not None]
        median_value = float(pd.Series(values).median()) if values else float("inf")
        for row in rows:
            current_value = row.get("fp_per_credit")
            qualified = bool(
                current_value is not None and float(current_value) >= median_value
                and row.get("probability_increase") is not None
                and float(row["probability_increase"]) >= 0.50
            )
            row["value_growth_qualified"] = qualified
            row["value_growth_score"] = (
                float(current_value) * (0.75 + 0.25 * float(row["probability_increase"]))
                + 0.05 * max(min(float(row["expected_credit_change"]), 0.5), 0.0)
                if qualified else None
            )
            row["price_growth_affects_phase7_objective"] = False
        return rows

    def _attach_all_shadows(self) -> list[dict[str, Any]]:
        updates = []
        for shadow_id in self.repository.shadow_snapshot_ids(self.profile_id):
            try:
                attached = attach_completed_turn_results(self.repository, shadow_id)
                updates.append({"shadow_snapshot_id": shadow_id, **attached})
                snapshot = self.repository.shadow_snapshot(shadow_id)
                latest = self.repository.latest_turn_outcome(shadow_id)
                if snapshot and latest:
                    last_turn = max(
                        int(player["turn"])
                        for recommendation in snapshot["recommendations"]
                        if recommendation.get("label") == "BEST_OVERALL"
                        for player in recommendation["players"]
                    )
                    if int(latest["completed_turn"]) >= last_turn:
                        evaluate_shadow_snapshot(self.repository, shadow_id, self.rules)
            except ValueError as error:
                updates.append({"shadow_snapshot_id": shadow_id,
                                "status": "NOT_ATTACHED", "reason": str(error)})
        return updates

    def _blocked_run(
            self,
            state: ControlCenterState,
            reasons: list[str],
            *,
            context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        overrides = self.repository.active_overrides(state.season_code, state.fantasy_matchday)
        snapshot = {
            "market_fingerprint": context.get("market_snapshot_fingerprint") if context else None,
            "prediction_fingerprint": context.get("prediction_fingerprint") if context else None,
            "prediction_run_id": context.get("prediction_run_id") if context else None,
            "ruleset_version": self.rules.ruleset_version,
            "rules_fingerprint": self.rules.fingerprint,
            "manual_override_fingerprint": json_fingerprint(overrides),
        }
        payload = {
            "status": "BLOCKED", "version": CONTROL_CENTER_VERSION,
            "profile_id": state.profile_id, "season": state.season_code,
            "matchday": state.fantasy_matchday or 0,
            "generated_at": datetime.now(UTC).isoformat(), "blocked_reasons": reasons,
            "snapshot": snapshot, "current_roster": list(state.roster_entity_ids),
            "bank_credits": state.bank_credits,
            "transfers_available": state.transfers_available,
            "constraints": dict(state.player_constraints), "recommendations": [],
        }
        payload["input_fingerprint"] = json_fingerprint({
            "snapshot": snapshot, "current_roster": payload["current_roster"],
            "bank_credits": state.bank_credits,
            "transfers_available": state.transfers_available,
            "constraints": payload["constraints"], "blocked_reasons": reasons,
        })
        self.repository.persist_run(payload, snapshot_path=None)
        return payload

    def _write_snapshot(self, payload: Mapping[str, Any]) -> Path:
        stable = {key: value for key, value in payload.items()
                  if key not in {"generated_at", "snapshot_path", "control_center_run_id"}}
        fingerprint = json_fingerprint(stable)
        path = self.snapshot_root / f"phase8a_{fingerprint}.json"
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_stable = {key: value for key, value in existing.items()
                               if key not in {"generated_at", "snapshot_path", "control_center_run_id"}}
            if json_fingerprint(existing_stable) != fingerprint:
                raise ValueError(f"immutable Phase 8A snapshot conflict: {path}")
            return path
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        return path

    def _state_with_matchday(self, state: ControlCenterState) -> ControlCenterState:
        market_matchday = self.repository.latest_market_matchday(state.season_code)
        if market_matchday is not None and (
                state.fantasy_matchday is None
                or market_matchday > state.fantasy_matchday
        ):
            payload = state.as_payload()
            payload["fantasy_matchday"] = market_matchday
            payload["scenario_id"] = None
            return state_from_payload(payload, profile_id=state.profile_id)
        if state.fantasy_matchday is not None:
            return state
        status = live_status(state.season_code, self.database_path)
        rounds = [item.get("round") for item in status.get("next_games", [])
                  if item.get("round") is not None]
        if not rounds:
            return state
        payload = state.as_payload();
        payload["fantasy_matchday"] = int(min(rounds))
        return state_from_payload(payload, profile_id=state.profile_id)

    def _rules_gate(
            self, season: str, market_status: Mapping[str, Any],
    ) -> tuple[bool, str]:
        compatibility = resolve_ruleset_compatibility(
            self.rules, season, market_status,
        )
        return compatibility.production_ready, compatibility.reason

    def _prediction_current(
            self, context: Mapping[str, Any] | None, state: ControlCenterState,
            status: Mapping[str, Any],
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if context is None:
            return False, ["No Phase 6C prediction snapshot exists for this Matchday"]
        if context.get("status") not in {"SUCCEEDED", "PARTIAL"}:
            reasons.append(f"Latest prediction status is {context.get('status')}")
        if context.get("predictive_model_version") != "phase6c_predictive_uplift_frozen_v1":
            reasons.append("Latest predictions are not from frozen Phase 6C")
        if context.get("overrides_newer_than_prediction"):
            reasons.append("Availability overrides changed after prediction generation")
        required_sources = {"schedule", "basketball_stats", "fantasy_market", "current_features"}
        stale = [
            name for name, value in context.get("source_freshness", {}).items()
            if name in required_sources and isinstance(value, dict) and value.get("is_stale")
        ]
        if stale:
            reasons.append("Prediction inputs are stale: " + ", ".join(sorted(stale)))
        if state.scenario_id and state.scenario_id not in context.get("scenarios", []):
            reasons.append("Selected availability scenario is not in the latest prediction run")
        if not context.get("market_snapshot_fingerprint"):
            reasons.append("Prediction has no market snapshot fingerprint")
        if (
            status.get("fantasy_snapshot_batch_id")
            and context.get("market_snapshot_batch_id")
            != status.get("fantasy_snapshot_batch_id")
        ):
            reasons.append("Fantasy market changed since prediction generation")
        return not reasons, reasons


def _optional_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _market_availability_status(row: Mapping[str, Any]) -> str:
    """Translate only explicit Fantasy market evidence; absence remains unknown."""

    if row.get("market_injured") is True:
        return "INJURED"
    probability = _optional_number(row.get("market_play_probability"))
    if probability is None:
        return "UNKNOWN"
    if probability > 1:
        probability /= 100.0
    if probability <= 0:
        return "OUT"
    if probability < 0.5:
        return "DOUBTFUL"
    if probability < 1:
        return "QUESTIONABLE"
    return "AVAILABLE"


def _unresolved_player_ids(value: Mapping[str, Any]) -> set[str]:
    if (
        value.get("status") != "AVAILABILITY_UNRESOLVED"
        and value.get("error_code") != "AVAILABILITY_UNRESOLVED"
    ):
        return set()
    return {
        item.strip()
        for item in str(value.get("error_message") or "").split(",")
        if item.strip()
    }


def _prediction_failure_message(value: Mapping[str, Any]) -> str | None:
    unresolved = _unresolved_player_ids(value)
    if unresolved:
        return f"Availability remains unresolved for {len(unresolved)} players"
    return value.get("error_message") or value.get("error_code")


def _automatic_availability_decision_payload() -> dict[str, Any]:
    """Resolve only unknowns, identically for every saved team profile."""

    return {"players": {}, "default_unknown_decision": "PLAY"}


def _serialize(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _last_success(status: Mapping[str, Any]) -> str | None:
    values = [
        item.get("last_success_at") for item in status.get("freshness", {}).values()
        if isinstance(item, dict) and item.get("last_success_at")
    ]
    return max(values) if values else None


def _latest_freshness_success(status: Mapping[str, Any], source: str) -> str | None:
    value = status.get("freshness", {}).get(source, {})
    return value.get("last_success_at") if isinstance(value, dict) else None


def _empty_price_fields() -> dict[str, Any]:
    return {
        "expected_next_price": None, "expected_credit_change": None,
        "probability_increase": None, "probability_decrease": None,
        "price_model_version": None, "price_model_confidence": "UNAVAILABLE",
        "value_growth_qualified": False, "value_growth_score": None,
        "price_growth_affects_phase7_objective": False,
    }


def _roster_counts(
    entity_ids: tuple[str, ...] | list[str], lookup: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    counts = {"GUARD": 0, "FORWARD": 0, "CENTER": 0, "COACH": 0}
    for entity_id in entity_ids:
        row = lookup.get(str(entity_id))
        if not row:
            continue
        if str(row.get("entity_type", "PLAYER")).upper() == "COACH":
            counts["COACH"] += 1
            continue
        try:
            position = normalize_position(str(row.get("position")))
        except (RuleViolation, ValueError):
            continue
        counts[position] = counts.get(position, 0) + 1
    return counts


def _empty_refresh_changes() -> dict[str, int | None]:
    return {
        "market": 0, "prices": 0, "rosters": 0, "availability": 0,
        "results": 0, "predictions": 0, "completed_turns": 0,
    }


def _refresh_changes(
    refresh: Mapping[str, Any], prediction: Mapping[str, Any] | None,
    price_outcomes: int, shadow_updates: list[Mapping[str, Any]],
) -> dict[str, int | None]:
    changes = _empty_refresh_changes()
    stages = dict(refresh.get("stages", {}))
    season = dict(stages.get("current_season", {}).get("result") or {})
    market = dict(stages.get("fantasy_market", {}).get("result") or {})
    changes["results"] = _first_int(
        season, "completed_games_ingested", "completed_games", default=0
    )
    changes["rosters"] = _first_int(
        season, "memberships_inserted", "memberships", "roster_rows", default=0
    )
    changes["market"] = _first_int(market, "player_rows", "rows", default=0)
    availability = 0
    for name, stage in stages.items():
        if not str(name).startswith("availability:"):
            continue
        availability += _first_int(dict(stage.get("result") or {}),
                                   "observation_count", "rows", default=0)
    changes["availability"] = availability
    changes["prices"] = int(price_outcomes or 0)
    if prediction:
        coverage = dict(prediction.get("coverage") or {})
        changes["predictions"] = _first_int(
            coverage, "prediction_rows", "rows", "scored_players", default=0
        )
    changes["completed_turns"] = sum(
        row.get("status") == "ATTACHED" for row in shadow_updates
    )
    return changes


def _first_int(value: Mapping[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        try:
            if value.get(key) is not None:
                return int(value[key])
        except (TypeError, ValueError):
            continue
    return default


def _friendly_error(error: Exception) -> str:
    if isinstance(error, (OSError, ConnectionError, TimeoutError)):
        return "Source or database unavailable; last valid state preserved"
    if isinstance(error, (ValueError, RuleViolation)):
        return str(error)
    return f"{type(error).__name__}; see local technical log"
