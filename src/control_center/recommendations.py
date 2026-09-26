"""Phase 8A constraints and presentation around the frozen Phase 7 engine."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from src.live.minutes import expected_role_label
from src.strategy.coach import simulate_coach_outcomes
from src.strategy.engine import _layout_shortlist
from src.strategy.optimizer import (
    OptimizationResult,
    enumerate_candidate_lineups,
    entity_ids_for_result,
    optimize_deterministic,
    optimize_roles_for_roster,
)
from src.strategy.rules import (
    FantasyRules,
    Lineup,
    RuleViolation,
    normalize_position,
    score_lineup,
)
from src.strategy.simulation import (
    DynamicEvaluation,
    recommend_next_turn,
    simulate_dynamic_scores,
    simulate_player_outcomes,
)

from .state import ControlCenterState


@dataclass(frozen=True, slots=True)
class EvaluatedLineup:
    result: OptimizationResult
    lineup: Lineup
    training_scores: np.ndarray
    evaluation: DynamicEvaluation


def validate_current_team(
        state: ControlCenterState,
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        *,
        market_entities: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    value = state.validated()
    current = set(value.roster_entity_ids)
    entity_pool = set(players["entity_id"].astype(str)) | set(coaches["entity_id"].astype(str))
    absent_from_pool = current - entity_pool
    market_lookup = {
        str(row.get("entity_id")): row for row in (market_entities or ())
        if row.get("entity_id") is not None
    }
    if market_entities is None:
        missing = sorted(absent_from_pool)
        unavailable: list[str] = []
    else:
        missing = sorted(absent_from_pool - set(market_lookup))
        unavailable = sorted(absent_from_pool & set(market_lookup))
    selected_players = players[players["entity_id"].astype(str).isin(current)].copy()
    selected_coaches = coaches[coaches["entity_id"].astype(str).isin(current)].copy()
    positions = {
        position: int(selected_players["position"].map(normalize_position).eq(position).sum())
        for position in rules.position_counts
    } if not selected_players.empty else {position: 0 for position in rules.position_counts}
    errors: list[str] = []
    if missing:
        labels = [str(market_lookup.get(entity, {}).get("name") or entity) for entity in missing]
        errors.append(
            "Saved current-team selections are no longer in the current Fantasy market: "
            + ", ".join(labels)
        )
    if unavailable:
        labels = [str(market_lookup[entity].get("name") or entity) for entity in unavailable]
        errors.append(
            "These current-team players are still in the market but cannot enter a new "
            "roster optimization because their game has finished or no scored prediction "
            "is available: " + ", ".join(labels) + ". Use Update Turn Results / "
            "Re-evaluate Strategy for legal between-Turn decisions."
        )
    if len(selected_coaches) > rules.coach_count:
        errors.append(f"Current team can contain at most {rules.coach_count} Coach")
    overflow = {
        position: count for position, count in positions.items()
        if count > rules.position_counts[position]
    }
    if len(selected_players) > rules.player_count or overflow:
        errors.append(
            f"Current team exceeds its positional limits: {overflow or positions}"
        )
    missing_positions = {
        position: int(rules.position_counts[position] - positions[position])
        for position in rules.position_counts
        if positions[position] < rules.position_counts[position]
    }
    if len(selected_coaches) < rules.coach_count:
        missing_positions["COACH"] = rules.coach_count - len(selected_coaches)
    market_value = float(
        selected_players["credits"].sum() + selected_coaches["credits"].sum()
    )
    return {
        "legal": not errors, "errors": errors, "missing_entity_ids": missing,
        "unavailable_entity_ids": unavailable,
        "player_count": len(selected_players), "coach_count": len(selected_coaches),
        "position_counts": positions, "market_value": market_value,
        "available_budget": market_value + value.bank_credits,
        "missing_positions": missing_positions,
        "slots_to_fill": sum(missing_positions.values()),
        "complete": not errors and not missing_positions,
    }


def generate_recommendations(
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        state: ControlCenterState,
        *,
        seed: int = 20250801,
        training_simulations: int = 96,
        evaluation_simulations: int = 512,
        max_rosters: int = 10,
        max_layouts_per_roster: int = 7,
        mode: str = "CURRENT_TEAM",
        total_budget: float | None = None,
) -> dict[str, Any]:
    """Return three distribution-selected teams under current-team constraints."""

    state = state.validated()
    optimization_mode = str(mode).upper()
    if optimization_mode not in {"BUILD_NEW", "COMPLETE_ROSTER", "CURRENT_TEAM"}:
        raise RuleViolation(f"unsupported optimization mode: {mode}")
    if optimization_mode == "BUILD_NEW":
        budget = rules.budget_credits if total_budget is None else float(total_budget)
        if not np.isfinite(budget) or budget < 0:
            raise RuleViolation("total budget must be a non-negative number")
        optimization_state = replace(
            state, roster_entity_ids=(), bank_credits=budget, transfers_available=0,
        )
        validation = {
            "legal": True, "errors": [], "market_value": 0.0,
            "available_budget": budget,
        }
    else:
        optimization_state = state
        validation = validate_current_team(optimization_state, players, coaches, rules)
        if not validation["legal"]:
            raise RuleViolation("; ".join(validation["errors"]))
        if optimization_mode == "CURRENT_TEAM" and not validation["complete"]:
            raise RuleViolation(
                "Improve My Team requires a complete current roster; "
                "use Complete My Roster to fill empty positions"
            )
        if (optimization_mode == "COMPLETE_ROSTER"
                and optimization_state.transfers_available < validation["slots_to_fill"]):
            raise RuleViolation(
                f"Maximum changes must be at least {validation['slots_to_fill']} "
                "to fill every empty roster slot"
            )
        budget = float(validation["available_budget"])
    constraints = {
        str(key): str(value).upper()
        for key, value in optimization_state.player_constraints.items()
    }
    conflicts = sorted(
        entity for entity, value in constraints.items()
        if value == "EXCLUDE" and entity in {
            key for key, other in constraints.items() if other == "FORCE_INCLUDE"
        }
    )
    if conflicts:
        raise RuleViolation("entities cannot be both excluded and forced: " + ", ".join(conflicts))
    excluded = {key for key, value in constraints.items() if value == "EXCLUDE"}
    forced = {key for key, value in constraints.items() if value == "FORCE_INCLUDE"}
    locked: set[str] = set()
    if optimization_mode == "COMPLETE_ROSTER":
        locked.update(optimization_state.roster_entity_ids)
    if locked:
        excluded_locked = sorted(locked & excluded)
        if excluded_locked:
            raise RuleViolation(
                "Locked Current Team entities cannot also be excluded: "
                + ", ".join(excluded_locked)
            )
        forced.update(locked)
    player_pool = players[~players["entity_id"].astype(str).isin(excluded)].copy().reset_index(drop=True)
    coach_pool = coaches[~coaches["entity_id"].astype(str).isin(excluded)].copy().reset_index(drop=True)
    turn_values = pd.to_numeric(
        player_pool["turn"] if "turn" in player_pool else pd.Series(dtype=float),
        errors="coerce",
    )
    rotation_required = (
        optimization_mode in {"BUILD_NEW", "CURRENT_TEAM"}
        and turn_values.notna().any()
        and turn_values.nunique() > 1
    )
    minimum_later_turn_players = 1 if rotation_required else 0
    available_entities = set(player_pool["entity_id"].astype(str)) | set(
        coach_pool["entity_id"].astype(str)
    )
    unavailable_forced = sorted(forced - available_entities)
    if unavailable_forced:
        raise RuleViolation("forced entities are unavailable: " + ", ".join(unavailable_forced))

    train_players, diagnostics = simulate_player_outcomes(
        player_pool, training_simulations, seed=seed,
        dependence_mode="GAME_GAUSSIAN_COPULA_VALIDATED", dependence_rho=-0.015,
    )
    train_coaches = simulate_coach_outcomes(
        coach_pool, training_simulations, seed=seed + 11,
    )
    objectives: list[tuple[str, np.ndarray, np.ndarray]] = [
        ("expected", player_pool["expected_fp"].to_numpy(float),
         coach_pool["expected_score"].to_numpy(float)),
        ("safer", player_pool["p10_fp"].to_numpy(float),
         np.quantile(train_coaches, 0.10, axis=0)),
        ("upside", player_pool["p90_fp"].to_numpy(float),
         np.quantile(train_coaches, 0.90, axis=0)),
    ]
    for index in range(min(5, training_simulations)):
        objectives.append((f"scenario_{index}", train_players[index], train_coaches[index]))

    candidate_results: dict[str, OptimizationResult] = {}
    excluded_rosters: list[frozenset[str]] = []
    for name, player_values, coach_values in objectives:
        if len(candidate_results) >= max_rosters:
            break
        result = _solve_roster(
            player_pool, coach_pool, rules, budget, optimization_state.roster_entity_ids,
            optimization_state.transfers_available, forced, player_values, coach_values,
            objective_name=f"_phase8a_{name}", excluded_rosters=tuple(excluded_rosters),
            minimum_later_turn_players=minimum_later_turn_players,
        )
        if result is None:
            continue
        entities = entity_ids_for_result(result, player_pool, coach_pool)
        candidate_results.setdefault(result.roster_fingerprint, result)
        excluded_rosters.append(entities)
    if not candidate_results:
        if optimization_mode == "COMPLETE_ROSTER" and validation["slots_to_fill"]:
            needs = " + ".join(
                f"{count} {position.title()}{'' if count == 1 else 's'}"
                for position, count in validation["missing_positions"].items()
            )
            raise RuleViolation(
                f"No legal {needs} combination fits within "
                f"{optimization_state.bank_credits:.1f} remaining credits."
            )
        rotation_reason = (
            " and includes a usable later-Turn substitute"
            if rotation_required else ""
        )
        raise RuleViolation(
            "no legal roster satisfies the current team, budget, manual constraints"
            + rotation_reason
        )

    evaluation_players, _ = simulate_player_outcomes(
        player_pool, evaluation_simulations, seed=seed + 1_000_003,
        dependence_mode="GAME_GAUSSIAN_COPULA_VALIDATED", dependence_rho=-0.015,
    )
    evaluation_coaches = simulate_coach_outcomes(
        coach_pool, evaluation_simulations, seed=seed + 1_000_019,
    )
    evaluated = _evaluate_candidates(
        tuple(candidate_results.values()), player_pool, coach_pool, rules,
        train_players, train_coaches, evaluation_players, evaluation_coaches,
        max_layouts_per_roster=max_layouts_per_roster,
        require_later_turn_substitution=rotation_required,
    )
    predicted_before = (
        0.0 if optimization_mode == "BUILD_NEW" else _current_team_expected_score(
            optimization_state, player_pool, coach_pool, rules,
            evaluation_players, evaluation_coaches,
        )
    )
    overall = max(evaluated, key=lambda item: float(np.mean(item.training_scores)))
    standard_error = float(np.std(overall.training_scores, ddof=1)) / max(
        np.sqrt(len(overall.training_scores)), 1.0
    )
    competitive_floor = float(np.mean(overall.training_scores)) - 1.96 * standard_error
    competitive = [
                      item for item in evaluated if float(np.mean(item.training_scores)) >= competitive_floor
                  ] or list(evaluated)
    safer = _distinct_best(
        competitive, overall, key=lambda item: float(np.quantile(item.training_scores, 0.10))
    )
    used = {_lineup_identity(overall.lineup), _lineup_identity(safer.lineup)}
    upside = _distinct_best(
        competitive, overall,
        key=lambda item: float(np.quantile(item.training_scores, 0.90)),
        excluded_identities=used,
    )
    selections = (("BEST_OVERALL", overall), ("SAFER_ALTERNATIVE", safer),
                  ("HIGHER_UPSIDE_ALTERNATIVE", upside))
    recommendations = [
        _recommendation_payload(
            label, item, player_pool, coach_pool, optimization_state, rules,
            mode=optimization_mode, budget=budget, predicted_before=predicted_before,
        )
        for label, item in selections
    ]
    alternatives = _player_alternatives(
        overall, player_pool, coach_pool, rules, optimization_state, budget,
        evaluation_players, evaluation_coaches, locked_entities=forced,
        minimum_later_turn_players=minimum_later_turn_players,
    )
    strategy = _strategy_payload(overall.lineup, player_pool, rules)
    return {
        "recommendations": recommendations, "player_alternatives": alternatives,
        "strategy": strategy, "available_budget": budget,
        "optimization_mode": optimization_mode,
        "candidate_rosters": len(candidate_results), "candidate_layouts": len(evaluated),
        "competitive_mean_floor": competitive_floor,
        "selection_principle": (
            "Best Overall maximizes simulated final-score mean. Safer and Higher-Upside "
            "select P10/P90 from strategies whose mean is statistically indistinguishable "
            "within the Monte Carlo 95% error band; no risk bonus is used."
        ),
        "simulation": {
            "version": "phase7_piecewise_inverse_cdf_mc_v1",
            "seed": seed, "training_simulations": training_simulations,
            "evaluation_simulations": evaluation_simulations,
            "dependence_mode": "GAME_GAUSSIAN_COPULA_VALIDATED", "dependence_rho": -0.015,
            "diagnostics": _slots(diagnostics),
        },
    }


def _solve_roster(
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        budget: float,
        previous: Iterable[str],
        trade_limit: int,
        forced: set[str],
        player_values: np.ndarray,
        coach_values: np.ndarray,
        *,
        objective_name: str,
        excluded_rosters: tuple[frozenset[str], ...] = (),
        minimum_later_turn_players: int = 0,
) -> OptimizationResult | None:
    if minimum_later_turn_players not in {0, 1}:
        raise RuleViolation("rotation-aware optimization currently supports one required substitute")
    boost = max(1_000_000.0, 1000.0 * (float(np.nanmax(np.abs(player_values))) + 1.0))
    rotation_candidates: list[str | None] = [None]
    if minimum_later_turn_players:
        turns = pd.to_numeric(players["turn"], errors="coerce")
        first_turn = float(turns[turns.notna()].min())
        later = players.loc[turns.gt(first_turn)].copy()
        later["_rotation_value"] = np.asarray(player_values, dtype=float)[turns.gt(first_turn)]
        later_entities = later.sort_values(
            ["_rotation_value", "credits"], ascending=[False, True]
        )["entity_id"].astype(str).tolist()
        if not later_entities:
            return None
        rotation_candidates = (
            [None] if forced.intersection(later_entities) else later_entities
        )

    for rotation_entity in rotation_candidates:
        attempt_forced = forced | ({rotation_entity} if rotation_entity else set())
        player_work = players.copy()
        coach_work = coaches.copy()
        player_work[objective_name] = np.asarray(player_values, dtype=float)
        coach_work[objective_name] = np.asarray(coach_values, dtype=float)
        player_work.loc[
            player_work["entity_id"].astype(str).isin(attempt_forced), objective_name
        ] += boost
        coach_work.loc[
            coach_work["entity_id"].astype(str).isin(attempt_forced), objective_name
        ] += boost
        try:
            result = optimize_deterministic(
                player_work, coach_work, rules, budget=budget,
                player_objective=objective_name, coach_objective=objective_name,
                roster_only=True, previous_entity_ids=previous, trade_limit=trade_limit,
                excluded_rosters=excluded_rosters,
            )
        except (ValueError, RuleViolation):
            continue
        entities = entity_ids_for_result(result, player_work, coach_work)
        if not attempt_forced.issubset(entities):
            continue
        roles = optimize_roles_for_roster(
            players, coaches, rules, result.lineup.player_ids, result.lineup.coach_id,
        )
        return replace(result, lineup=roles.lineup)
    return None


def _evaluate_candidates(
        results: tuple[OptimizationResult, ...],
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        train_players: np.ndarray,
        train_coaches: np.ndarray,
        evaluation_players: np.ndarray,
        evaluation_coaches: np.ndarray,
        *,
        max_layouts_per_roster: int,
        require_later_turn_substitution: bool = False,
) -> list[EvaluatedLineup]:
    player_index = {str(value): index for index, value in enumerate(players["player_id"])}
    coach_index = {str(value): index for index, value in enumerate(coaches["coach_id"])}
    values: list[EvaluatedLineup] = []
    for result in results:
        roster = players[players["player_id"].astype(str).isin(result.lineup.player_ids)].copy()
        roster.index = roster["player_id"].astype(str)
        roster = roster.loc[list(result.lineup.player_ids)].reset_index(drop=True)
        layouts = (
            _rotation_layout_shortlist(
                result.lineup, roster, rules, max_layouts=max_layouts_per_roster,
            )
            if require_later_turn_substitution else
            _layout_shortlist(
                result.lineup, roster, rules, max_layouts=max_layouts_per_roster
            )
        )
        columns = [player_index[player] for player in result.lineup.player_ids]
        for lineup in layouts:
            training = simulate_dynamic_scores(
                lineup, roster, train_players[:, columns],
                train_coaches[:, coach_index[lineup.coach_id]], rules,
            ).scores
            evaluation = simulate_dynamic_scores(
                lineup, roster, evaluation_players[:, columns],
                evaluation_coaches[:, coach_index[lineup.coach_id]], rules,
            )
            values.append(EvaluatedLineup(result, lineup, training, evaluation))
    return values


def _rotation_layout_shortlist(
        mean_layout: Lineup,
        roster: pd.DataFrame,
        rules: FantasyRules,
        *,
        max_layouts: int,
) -> tuple[Lineup, ...]:
    """Prefer strong initial layouts that leave a legal later-Turn substitute unused."""

    candidates = list(_layout_shortlist(
        mean_layout, roster, rules, max_layouts=max(max_layouts * 2, 9),
    ))
    positions = {
        str(row.player_id): str(row.position) for row in roster.itertuples(index=False)
    }
    if sum(_has_later_turn_substitution(value, roster) for value in candidates) < max_layouts:
        candidates.extend(enumerate_candidate_lineups(
            mean_layout.player_ids, mean_layout.coach_id, positions, rules,
        ))
    means = {
        str(row.player_id): float(row.expected_fp) for row in roster.itertuples(index=False)
    }
    turns = {
        str(row.player_id): int(row.turn) for row in roster.itertuples(index=False)
    }
    unique = {
        _lineup_identity(value): value for value in candidates
        if _has_later_turn_substitution(value, roster)
    }
    ordered = sorted(
        unique.values(),
        key=lambda value: (
            score_lineup(value, means, 0.0, rules),
            -sum(turns[player] for player in value.starters),
            -turns[value.captain],
        ),
        reverse=True,
    )
    return tuple(ordered[:max_layouts])


def _has_later_turn_substitution(lineup: Lineup, roster: pd.DataFrame) -> bool:
    return bool(_rotation_options(lineup, roster))


def _rotation_options(lineup: Lineup, roster: pd.DataFrame) -> list[dict[str, Any]]:
    turns = {
        str(row.player_id): int(row.turn) for row in roster.itertuples(index=False)
    }
    if not turns or len(set(turns.values())) < 2:
        return []
    first_turn = min(turns.values())
    positions = {
        str(row.player_id): normalize_position(row.position)
        for row in roster.itertuples(index=False)
    }
    names = {
        str(row.player_id): str(row.name) for row in roster.itertuples(index=False)
    }
    options: list[dict[str, Any]] = []
    for later in sorted(lineup.bench, key=lambda player: (turns[player], names[player])):
        if turns[later] <= first_turn:
            continue
        replaceable = sorted(
            (
                player for player in lineup.starters
                if turns[player] == first_turn and positions[player] == positions[later]
            ),
            key=lambda player: names[player],
        )
        if replaceable:
            options.append({
                "player_id": later,
                "name": names[later],
                "turn": turns[later],
                "position": positions[later],
                "can_replace": [
                    {"player_id": player, "name": names[player]}
                    for player in replaceable
                ],
            })
    return options


def _current_team_expected_score(
        state: ControlCenterState,
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        evaluation_players: np.ndarray,
        evaluation_coaches: np.ndarray,
) -> float | None:
    current = set(state.roster_entity_ids)
    selected_players = players[players["entity_id"].astype(str).isin(current)]
    selected_coaches = coaches[coaches["entity_id"].astype(str).isin(current)]
    if len(selected_players) != rules.player_count or len(selected_coaches) != rules.coach_count:
        return None
    player_ids = tuple(selected_players["player_id"].astype(str))
    coach_id = str(selected_coaches.iloc[0]["coach_id"])
    roles = optimize_roles_for_roster(players, coaches, rules, player_ids, coach_id)
    player_index = {str(value): index for index, value in enumerate(players["player_id"])}
    coach_index = {str(value): index for index, value in enumerate(coaches["coach_id"])}
    roster = players[players["player_id"].astype(str).isin(player_ids)].copy()
    roster.index = roster["player_id"].astype(str)
    roster = roster.loc[list(roles.lineup.player_ids)].reset_index(drop=True)
    columns = [player_index[player] for player in roles.lineup.player_ids]
    return float(simulate_dynamic_scores(
        roles.lineup, roster, evaluation_players[:, columns],
        evaluation_coaches[:, coach_index[coach_id]], rules,
    ).expected_final_score)


def _recommendation_payload(
        label: str,
        evaluated: EvaluatedLineup,
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        state: ControlCenterState,
        rules: FantasyRules,
        *,
        mode: str,
        budget: float,
        predicted_before: float | None,
) -> dict[str, Any]:
    lineup = evaluated.lineup
    roster = players[players["player_id"].astype(str).isin(lineup.player_ids)].copy()
    roster.index = roster["player_id"].astype(str)
    current = set(state.roster_entity_ids)
    records: list[dict[str, Any]] = []
    for player_id in lineup.player_ids:
        row = roster.loc[player_id]
        if player_id in lineup.starters:
            role = "STARTER"
        elif player_id == lineup.sixth_man:
            role = "SIXTH_MAN"
        else:
            role = "BENCH"
        last5_games = row.get("last5_games")
        if not isinstance(last5_games, list):
            last5_games = []
        records.append({
            "player_id": player_id, "entity_id": str(row["entity_id"]),
            "name": str(row["name"]), "team_id": str(row["team_id"]),
            "position": str(row["position"]), "credits": float(row["credits"]),
            "turn": int(row["turn"]), "game_id": str(row.get("game_id") or ""), "role": role,
            "captain": player_id == lineup.captain,
            "expected_minutes": (
                float(row["expected_minutes"]) if pd.notna(row.get("expected_minutes")) else None
            ),
            "expected_role": expected_role_label(row.get("expected_minutes")),
            "fp_per_minute": (
                float(row["expected_fp"]) / float(row["expected_minutes"])
                if pd.notna(row.get("expected_minutes")) and float(row["expected_minutes"]) > 0
                else None
            ),
            "expected_fp": float(row["expected_fp"]), "p10": float(row["p10_fp"]),
            "p50": float(row["p50_fp"]), "p90": float(row["p90_fp"]),
            "p95": float(row["p95_fp"]),
            "last5_fp_average": (
                float(row["last5_fp_average"])
                if pd.notna(row.get("last5_fp_average")) else None
            ),
            "last5_minutes_median": (
                float(row["last5_minutes_median"])
                if pd.notna(row.get("last5_minutes_median")) else None
            ),
            "last5_games": last5_games,
            "downside_probability": float(row["prob_fp_le_15"]),
            "upside_probability": float(row["prob_fp_ge_30"]),
        })
    coach = coaches[coaches["coach_id"].astype(str).eq(lineup.coach_id)].iloc[0]
    selected_entities = {record["entity_id"] for record in records} | {str(coach["entity_id"])}
    incoming = selected_entities - current;
    outgoing = current - selected_entities
    entity_details = {
        **{
            str(row.entity_id): {
                "entity_type": "PLAYER", "player_id": str(row.player_id),
                "name": str(row.name), "team_id": str(row.team_id),
                "position": str(row.position), "credits": float(row.credits),
                "turn": int(row.turn), "expected_fp": float(row.expected_fp),
                "expected_minutes": (
                    float(row.expected_minutes)
                    if pd.notna(getattr(row, "expected_minutes", None)) else None
                ),
                "last5_fp_average": (
                    float(row.last5_fp_average)
                    if pd.notna(getattr(row, "last5_fp_average", None)) else None
                ),
                "last5_minutes_median": (
                    float(row.last5_minutes_median)
                    if pd.notna(getattr(row, "last5_minutes_median", None)) else None
                ),
                "last5_games": (
                    list(row.last5_games)
                    if isinstance(getattr(row, "last5_games", None), list) else []
                ),
            }
            for row in players.itertuples(index=False)
        },
        **{
            str(row.entity_id): {
                "entity_type": "COACH", "name": str(row.name),
                "team_id": str(row.team_id), "position": "COACH",
                "credits": float(row.credits), "turn": int(row.turn),
                "expected_score": float(row.expected_score),
            }
            for row in coaches.itertuples(index=False)
        },
    }
    credits_received = float(sum(
        entity_details.get(key, {}).get("credits", 0.0) for key in outgoing
    ))
    credits_spent = float(sum(
        entity_details.get(key, {}).get("credits", 0.0) for key in incoming
    ))
    credits_remaining = float(budget - evaluated.result.total_credits)
    expected_after = float(evaluated.evaluation.expected_final_score)
    transfer_count = 0 if mode == "BUILD_NEW" else len(incoming)
    rotation_options = _rotation_options(lineup, roster.reset_index(drop=True))
    return {
        "label": label, "players": records,
        "coach": {
            "coach_id": str(coach["coach_id"]), "entity_id": str(coach["entity_id"]),
            "name": str(coach["name"]), "team_id": str(coach["team_id"]),
            "credits": float(coach["credits"]), "turn": int(coach["turn"]),
            "game_id": str(coach.get("game_id") or ""),
            "expected_score": float(coach["expected_score"]),
        },
        "starting_five": sorted(lineup.starters), "sixth_man": lineup.sixth_man,
        "bench": sorted(lineup.bench), "captain": lineup.captain,
        "credits_used": float(evaluated.result.total_credits),
        "transfers_required": transfer_count,
        "players_in": [
            {"entity_id": key, **entity_details.get(key, {"name": key, "credits": 0.0})}
            for key in sorted(incoming)
        ],
        "players_out": [
            {"entity_id": key, **entity_details.get(key, {"name": key, "credits": 0.0})}
            for key in sorted(outgoing)
        ],
        "credit_accounting": {
            "credits_before": float(state.bank_credits if mode != "BUILD_NEW" else budget),
            "bank_before": float(state.bank_credits),
            "credits_received": credits_received,
            "credits_spent": credits_spent,
            "credits_remaining": max(credits_remaining, 0.0),
        },
        "predicted_team_fp_before": predicted_before,
        "predicted_team_fp_after": expected_after,
        "predicted_improvement": (
            expected_after - predicted_before if predicted_before is not None else None
        ),
        "expected_final_score": expected_after,
        "p10_team_score": evaluated.evaluation.p10_final_score,
        "p50_team_score": evaluated.evaluation.median_final_score,
        "p90_team_score": evaluated.evaluation.p90_final_score,
        "downside_frequency": evaluated.evaluation.downside_frequency,
        "high_score_frequency": evaluated.evaluation.high_score_frequency,
        "captain_switch_frequency": evaluated.evaluation.captain_switch_frequency,
        "substitution_frequency": evaluated.evaluation.substitution_frequency,
        "rotation_options": rotation_options,
        "major_characteristic": {
            "BEST_OVERALL": "Highest simulated expected final Fantasy score.",
            "SAFER_ALTERNATIVE": "Strongest simulated P10 within the competitive mean band.",
            "HIGHER_UPSIDE_ALTERNATIVE": "Strongest simulated P90 within the competitive mean band.",
        }[label],
        "between_turn_strategy": (
            "Rerun after each Turn with every realized score. The frozen Phase 7 recourse "
            "engine will change only eligible field/bench and captain roles."
        ),
        "rules_fingerprint": rules.fingerprint,
    }


def _player_alternatives(
        overall: EvaluatedLineup,
        players: pd.DataFrame,
        coaches: pd.DataFrame,
        rules: FantasyRules,
        state: ControlCenterState,
        budget: float,
        evaluation_players: np.ndarray,
        evaluation_coaches: np.ndarray,
        *,
        locked_entities: set[str],
        minimum_later_turn_players: int = 0,
) -> list[dict[str, Any]]:
    current = set(state.roster_entity_ids)
    selected = players[players["player_id"].astype(str).isin(overall.lineup.player_ids)].copy()
    incoming = selected[~selected["entity_id"].astype(str).isin(current)]
    if incoming.empty:
        incoming = selected.sort_values("expected_fp", ascending=False).head(1)
    target = incoming.sort_values("expected_fp", ascending=False).iloc[0]
    position = normalize_position(target["position"])
    unavailable = set(overall.lineup.player_ids)
    pool = players[
        players["position"].map(normalize_position).eq(position)
        & ~players["player_id"].astype(str).isin(unavailable)
        ].copy()
    constraints = {str(key): str(value).upper() for key, value in state.player_constraints.items()}
    pool = pool[~pool["entity_id"].astype(str).map(constraints).eq("EXCLUDE")]
    specifications = (
        ("SAFER", "p10_fp", False), ("HIGHER_UPSIDE", "p90_fp", False),
    )
    output: list[dict[str, Any]] = []
    used: set[str] = set()
    player_index = {str(value): index for index, value in enumerate(players["player_id"])}
    coach_index = {str(value): index for index, value in enumerate(coaches["coach_id"])}
    for label, column, ascending in specifications:
        candidates = pool.sort_values(column, ascending=ascending)
        candidate = next((row for _, row in candidates.iterrows()
                          if str(row["entity_id"]) not in used), None)
        if candidate is None:
            continue
        used.add(str(candidate["entity_id"]))
        forced = {
                     key for key, value in constraints.items() if value == "FORCE_INCLUDE"
                 } | locked_entities | {str(candidate["entity_id"])}
        player_work = players[~players["entity_id"].astype(str).eq(str(target["entity_id"]))].copy()
        result = _solve_roster(
            player_work, coaches, rules, budget, state.roster_entity_ids,
            state.transfers_available, forced,
            player_work["expected_fp"].to_numpy(float),
            coaches["expected_score"].to_numpy(float),
            objective_name=f"_phase8a_alternative_{label.lower()}",
            minimum_later_turn_players=minimum_later_turn_players,
        )
        if result is None:
            continue
        roster = players[players["player_id"].astype(str).isin(result.lineup.player_ids)].copy()
        roster.index = roster["player_id"].astype(str)
        roster = roster.loc[list(result.lineup.player_ids)].reset_index(drop=True)
        columns = [player_index[value] for value in result.lineup.player_ids]
        evaluation = simulate_dynamic_scores(
            result.lineup, roster, evaluation_players[:, columns],
            evaluation_coaches[:, coach_index[result.lineup.coach_id]], rules,
        )
        output.append({
            "replaces": {"player_id": str(target["player_id"]), "name": str(target["name"])},
            "alternative_type": label,
            "player": {"player_id": str(candidate["player_id"]), "name": str(candidate["name"])},
            "credits_difference": float(candidate["credits"] - target["credits"]),
            "expected_final_team_fp_difference": (
                    evaluation.expected_final_score - overall.evaluation.expected_final_score
            ),
            "p10_difference": evaluation.p10_final_score - overall.evaluation.p10_final_score,
            "p90_difference": evaluation.p90_final_score - overall.evaluation.p90_final_score,
            "position": position, "turn": int(candidate["turn"]),
            "reoptimized_roster_fingerprint": result.roster_fingerprint,
        })
    return output


def _strategy_payload(lineup: Lineup, players: pd.DataFrame, rules: FantasyRules) -> dict[str, Any]:
    roster = players[players["player_id"].astype(str).isin(lineup.player_ids)].copy()
    roster.index = roster["player_id"].astype(str)
    last_turn = int(roster["turn"].max())
    later = roster[roster["turn"].gt(1)].sort_values("expected_fp", ascending=False)
    later_captain = next(
        (str(row.player_id) for row in later.itertuples(index=False)
         if str(row.player_id) in lineup.starters),
        str(later.iloc[0]["player_id"]) if not later.empty else None,
    )
    thresholds: list[dict[str, Any]] = []
    for target in sorted(lineup.starters):
        row = roster.loc[target]
        if int(row["turn"]) != 1:
            continue
        threshold, action = _decision_threshold(lineup, roster.reset_index(drop=True), target, rules)
        if threshold is not None:
            thresholds.append({
                "player_id": target, "player": str(row["name"]),
                "threshold": threshold, "action_below_threshold": action,
                "derivation": "Frozen Phase 7 expected-final-value decision boundary",
            })
    early_upside = [
        {"player_id": str(row.player_id), "name": str(row.name), "p90": float(row.p90_fp)}
        for row in roster[roster["turn"].lt(last_turn)].sort_values("p90_fp", ascending=False)
        .head(3).itertuples(index=False)
    ]
    protected = {item["player_id"] for item in thresholds}
    unprotected = [
        {"player_id": str(row.player_id), "name": str(row.name)}
        for row in roster[
            roster["player_id"].astype(str).isin(lineup.starters - protected)
            & roster["turn"].eq(last_turn)
            ].itertuples(index=False)
    ]
    return {
        "initial_captain": lineup.captain,
        "best_later_captain_alternative": later_captain,
        "useful_early_upside": early_upside,
        "decision_rules": thresholds,
        "downside_protected_players": sorted(protected),
        "players_without_later_safety_net": unprotected,
        "instruction": (
            "After T1, enter all realized T1 scores and rerun. Thresholds are roster- "
            "and opponent-specific outputs of the legal Phase 7 decision engine."
        ),
    }


def _decision_threshold(
        lineup: Lineup,
        roster: pd.DataFrame,
        target: str,
        rules: FantasyRules,
) -> tuple[float | None, str | None]:
    played = roster[roster["turn"].le(1)]
    baseline = {str(row.player_id): float(row.expected_fp) for row in played.itertuples(index=False)}
    row = roster[roster["player_id"].astype(str).eq(target)].iloc[0]
    low = float(row["p10_fp"]) - 30.0;
    high = float(row["p95_fp"]) + 30.0

    def action(value: float) -> tuple[bool, str | None]:
        observed = dict(baseline);
        observed[target] = value
        after, actions = recommend_next_turn(lineup, roster, observed, 1, rules)
        moved = target in lineup.starters and target not in after.starters
        changed_captain = target == lineup.captain and target != after.captain
        relevant = moved or changed_captain
        description = None
        if relevant:
            promoted = next((item["player_id"] for item in actions
                             if item["action"] == "MOVE_TO_FIELD"), None)
            description = (
                f"Replace with {promoted}" if moved and promoted else
                f"Change captain to {after.captain}" if changed_captain else "Apply recommended recourse"
            )
        return relevant, description

    low_active, low_action = action(low);
    high_active, _ = action(high)
    if not low_active or high_active:
        return None, None
    for _ in range(28):
        middle = (low + high) / 2.0
        if action(middle)[0]:
            low = middle
        else:
            high = middle
    return round((low + high) / 2.0, 2), low_action


def _distinct_best(
        values: list[EvaluatedLineup],
        fallback: EvaluatedLineup,
        *,
        key: Any,
        excluded_identities: set[str] | None = None,
) -> EvaluatedLineup:
    excluded = set(excluded_identities or ())
    excluded.add(_lineup_identity(fallback.lineup))
    candidates = [value for value in values if _lineup_identity(value.lineup) not in excluded]
    return max(candidates, key=key) if candidates else fallback


def _lineup_identity(lineup: Lineup) -> str:
    value = "|".join((
        *sorted(lineup.player_ids), lineup.coach_id, *sorted(lineup.starters),
        lineup.sixth_man, lineup.captain,
    ))
    return hashlib.sha256(value.encode()).hexdigest()


def _slots(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}
