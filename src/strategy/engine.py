"""Strategy-aware roster selection and live between-Turn decisions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from src.strategy.coach import simulate_coach_outcomes
from src.strategy.optimizer import (
    OptimizationResult,
    entity_ids_for_result,
    enumerate_candidate_lineups,
    optimize_deterministic,
    optimize_roles_for_roster,
)
from src.strategy.rules import FantasyRules, Lineup, score_lineup
from src.strategy.simulation import (
    DynamicEvaluation,
    recommend_next_turn,
    simulate_dynamic_scores,
    simulate_player_outcomes,
    simulate_static_scores,
)


@dataclass(frozen=True, slots=True)
class StrategyResult:
    method: str
    optimizer_result: OptimizationResult
    simulation: DynamicEvaluation
    simulation_seed: int
    training_simulations: int
    evaluation_simulations: int
    candidate_rosters: int
    candidate_layouts: int
    simulation_diagnostics: Mapping[str, Any]
    strategy_fingerprint: str


@dataclass(frozen=True, slots=True)
class ActualReplay:
    final_lineup: Lineup
    final_score: float
    actions: tuple[dict[str, Any], ...]
    captain_switches: int
    substitutions: int
    static_score: float


def apply_availability_scenario(
    players: pd.DataFrame,
    decisions: Mapping[str, str],
) -> pd.DataFrame:
    """Apply explicit point-in-time PLAY/OUT decisions without forecasting participation."""

    normalized = {str(player): str(value).upper() for player, value in decisions.items()}
    invalid = sorted(set(normalized.values()) - {"PLAY", "OUT"})
    if invalid:
        raise ValueError(f"availability scenario has unresolved decisions: {invalid}")
    output = players.copy()
    output["availability_scenario"] = output["player_id"].astype(str).map(
        normalized
    ).fillna("PLAY")
    return output[~output["availability_scenario"].eq("OUT")].copy()


def optimize_stochastic(
    players: pd.DataFrame,
    coaches: pd.DataFrame,
    rules: FantasyRules,
    *,
    budget: float,
    seed: int,
    previous_entity_ids: Iterable[str] = (),
    trade_limit: int | None = None,
    training_simulations: int = 96,
    evaluation_simulations: int = 512,
    dynamic: bool = True,
    scenario_candidates: int = 5,
    alternative_mean_rosters: int = 2,
    max_layouts_per_roster: int = 7,
    dependence_mode: str = "GAME_GAUSSIAN_COPULA_VALIDATED",
    dependence_rho: float = -0.015,
    include_upside: bool = True,
    include_downside: bool = True,
) -> StrategyResult:
    """Select by sample-average final Fantasy score with legal recourse."""

    player_pool = players.reset_index(drop=True).copy()
    coach_pool = coaches.reset_index(drop=True).copy()
    train_players, train_diagnostics = simulate_player_outcomes(
        player_pool, training_simulations, seed=seed,
        dependence_mode=dependence_mode, dependence_rho=dependence_rho,
        include_upside=include_upside, include_downside=include_downside,
    )
    train_coaches = simulate_coach_outcomes(
        coach_pool, training_simulations, seed=seed + 11,
    )
    ranking_start = min(int(scenario_candidates), max(training_simulations - 1, 0))
    candidate_results: list[OptimizationResult] = []
    excluded: list[frozenset[str]] = []
    for _ in range(max(1, int(alternative_mean_rosters) + 1)):
        try:
            result = optimize_deterministic(
                player_pool, coach_pool, rules, budget=budget,
                previous_entity_ids=previous_entity_ids, trade_limit=trade_limit,
                excluded_rosters=tuple(excluded),
            )
        except ValueError:
            break
        candidate_results.append(result)
        excluded.append(entity_ids_for_result(result, player_pool, coach_pool))
    for scenario in range(min(int(scenario_candidates), training_simulations)):
        player_column = f"_phase7_scenario_{scenario}"
        coach_column = f"_phase7_scenario_{scenario}"
        player_pool[player_column] = train_players[scenario]
        coach_pool[coach_column] = train_coaches[scenario]
        try:
            candidate_results.append(optimize_deterministic(
                player_pool, coach_pool, rules, budget=budget,
                player_objective=player_column, coach_objective=coach_column,
                previous_entity_ids=previous_entity_ids, trade_limit=trade_limit,
            ))
        except ValueError:
            continue
    unique_results: dict[str, OptimizationResult] = {}
    for result in candidate_results:
        unique_results.setdefault(result.roster_fingerprint, result)
    player_index = {
        str(player): index for index, player in enumerate(player_pool["player_id"].astype(str))
    }
    coach_index = {
        str(coach): index for index, coach in enumerate(coach_pool["coach_id"].astype(str))
    }
    best_result: OptimizationResult | None = None
    best_lineup: Lineup | None = None
    best_training_score = -np.inf
    layouts_evaluated = 0
    for candidate in unique_results.values():
        roster_players = player_pool[
            player_pool["player_id"].astype(str).isin(candidate.lineup.player_ids)
        ].copy()
        roster_coach = coach_pool[
            coach_pool["coach_id"].astype(str).eq(candidate.lineup.coach_id)
        ]
        mean_layout = optimize_roles_for_roster(
            player_pool, coach_pool, rules, candidate.lineup.player_ids,
            candidate.lineup.coach_id,
        ).lineup
        layouts = _layout_shortlist(
            mean_layout, roster_players, rules, max_layouts=max_layouts_per_roster
        )
        selected_columns = [player_index[player] for player in mean_layout.player_ids]
        roster_outcomes = train_players[ranking_start:, selected_columns]
        coach_outcomes = train_coaches[
            ranking_start:, coach_index[mean_layout.coach_id]
        ]
        # simulate_dynamic_scores expects its player frame in the same order as outcomes.
        ordered_roster = roster_players.set_index(
            roster_players["player_id"].astype(str)
        ).loc[list(mean_layout.player_ids)].reset_index(drop=True)
        for layout in layouts:
            layouts_evaluated += 1
            if dynamic:
                expected = simulate_dynamic_scores(
                    layout, ordered_roster, roster_outcomes, coach_outcomes, rules
                ).expected_final_score
            else:
                expected = float(np.mean(simulate_static_scores(
                    layout, layout.player_ids, roster_outcomes, coach_outcomes, rules
                )))
            if expected > best_training_score + 1e-10:
                best_training_score = expected
                best_result = candidate
                best_lineup = layout
    if best_result is None or best_lineup is None:
        raise ValueError("stochastic optimizer generated no legal candidate")

    evaluation_players, evaluation_diagnostics = simulate_player_outcomes(
        player_pool, evaluation_simulations, seed=seed + 1_000_003,
        dependence_mode=dependence_mode, dependence_rho=dependence_rho,
        include_upside=include_upside, include_downside=include_downside,
    )
    evaluation_coaches = simulate_coach_outcomes(
        coach_pool, evaluation_simulations, seed=seed + 1_000_019,
    )
    selected = player_pool[
        player_pool["player_id"].astype(str).isin(best_lineup.player_ids)
    ].copy()
    selected.index = selected["player_id"].astype(str)
    selected = selected.loc[list(best_lineup.player_ids)].reset_index(drop=True)
    selected_outcomes = evaluation_players[:, [player_index[value] for value in best_lineup.player_ids]]
    selected_coach = evaluation_coaches[:, coach_index[best_lineup.coach_id]]
    if dynamic:
        evaluation = simulate_dynamic_scores(
            best_lineup, selected, selected_outcomes, selected_coach, rules
        )
    else:
        scores = simulate_static_scores(
            best_lineup, best_lineup.player_ids, selected_outcomes, selected_coach, rules
        )
        evaluation = _static_evaluation(scores)
    replaced = OptimizationResult(
        lineup=best_lineup,
        objective_value=best_result.objective_value,
        total_credits=best_result.total_credits,
        solver_status=best_result.solver_status,
        solver_gap=best_result.solver_gap,
        optimal=best_result.optimal,
        roster_fingerprint=best_result.roster_fingerprint,
    )
    fingerprint_payload = {
        "method": "DYNAMIC_STOCHASTIC" if dynamic else "STATIC_STOCHASTIC",
        "seed": int(seed), "rules": rules.fingerprint,
        "roster": best_result.roster_fingerprint,
        "starters": sorted(best_lineup.starters), "sixth": best_lineup.sixth_man,
        "captain": best_lineup.captain,
        "training_simulations": training_simulations,
        "evaluation_simulations": evaluation_simulations,
        "dependence_mode": dependence_mode, "dependence_rho": dependence_rho,
        "include_upside": include_upside, "include_downside": include_downside,
    }
    return StrategyResult(
        method=fingerprint_payload["method"], optimizer_result=replaced,
        simulation=evaluation, simulation_seed=int(seed),
        training_simulations=int(training_simulations),
        evaluation_simulations=int(evaluation_simulations),
        candidate_rosters=len(unique_results), candidate_layouts=layouts_evaluated,
        simulation_diagnostics={
            "training": train_diagnostics.__dict__ if hasattr(train_diagnostics, "__dict__")
            else _slots_dict(train_diagnostics),
            "evaluation": evaluation_diagnostics.__dict__
            if hasattr(evaluation_diagnostics, "__dict__") else _slots_dict(evaluation_diagnostics),
        },
        strategy_fingerprint=_json_sha(fingerprint_payload),
    )


def replay_actual_round(
    initial: Lineup,
    players: pd.DataFrame,
    coach_actual_score: float,
    rules: FantasyRules,
    *,
    allow_substitutions: bool = True,
    allow_captain_switch: bool = True,
) -> ActualReplay:
    roster = players[players["player_id"].astype(str).isin(initial.player_ids)].copy()
    actual = {
        str(row.player_id): float(row.actual_fp) for row in roster.itertuples(index=False)
    }
    last_turn = int(roster["turn"].max())
    current = initial
    actions: list[dict[str, Any]] = []
    captain_switches = 0
    substitutions = 0
    for completed_turn in range(1, last_turn):
        best, proposed = recommend_next_turn(
            current, roster, actual, completed_turn, rules
        )
        if not allow_substitutions:
            best = Lineup(
                current.player_ids, current.coach_id, current.starters,
                current.sixth_man, best.captain if allow_captain_switch else current.captain,
            )
            proposed = tuple(
                action for action in proposed if action["action"] == "CHANGE_CAPTAIN"
            ) if allow_captain_switch else ()
        elif not allow_captain_switch and best.captain != current.captain:
            # Re-evaluate substitutions with captain frozen by filtering legal states.
            best = _best_with_captain_frozen(current, roster, actual, completed_turn, rules)
            proposed = tuple(
                action for action in proposed if action["action"] != "CHANGE_CAPTAIN"
            )
        captain_switches += int(best.captain != current.captain)
        substitutions += len(best.starters - current.starters)
        actions.extend({**action, "after_turn": completed_turn} for action in proposed)
        current = best
    static = score_lineup(initial, actual, coach_actual_score, rules)
    final = score_lineup(current, actual, coach_actual_score, rules)
    return ActualReplay(
        current, float(final), tuple(actions), captain_switches, substitutions, float(static)
    )


def _layout_shortlist(
    mean_layout: Lineup,
    roster: pd.DataFrame,
    rules: FantasyRules,
    *,
    max_layouts: int,
) -> tuple[Lineup, ...]:
    positions = {
        str(row.player_id): str(row.position) for row in roster.itertuples(index=False)
    }
    means = {
        str(row.player_id): float(row.expected_fp) for row in roster.itertuples(index=False)
    }
    turns = {str(row.player_id): int(row.turn) for row in roster.itertuples(index=False)}
    all_layouts = enumerate_candidate_lineups(
        mean_layout.player_ids, mean_layout.coach_id, positions, rules
    )
    # These orderings only provide broad legal candidates; Monte Carlo final score
    # chooses among them. No risk coefficient enters either ordering.
    orderings = [
        lambda value: (_static_mean(value, means, rules),),
        lambda value: (
            -sum(turns[player] for player in value.starters),
            _static_mean(value, means, rules),
        ),
        lambda value: (
            -turns[value.captain], _static_mean(value, means, rules),
        ),
        lambda value: (
            turns[value.captain], _static_mean(value, means, rules),
        ),
    ]
    selected: list[Lineup] = [mean_layout]
    for ordering in orderings:
        for value in sorted(all_layouts, key=ordering, reverse=True)[:2]:
            if value not in selected:
                selected.append(value)
            if len(selected) >= max_layouts:
                return tuple(selected)
    return tuple(selected[:max_layouts])


def _static_mean(lineup: Lineup, means: Mapping[str, float], rules: FantasyRules) -> float:
    return score_lineup(lineup, means, 0.0, rules)


def _static_evaluation(scores: np.ndarray) -> DynamicEvaluation:
    return DynamicEvaluation(
        scores=scores,
        expected_final_score=float(np.mean(scores)),
        median_final_score=float(np.median(scores)),
        p10_final_score=float(np.quantile(scores, 0.10)),
        p90_final_score=float(np.quantile(scores, 0.90)),
        downside_frequency=float(np.mean(scores <= 120.0)),
        high_score_frequency=float(np.mean(scores >= 180.0)),
        captain_switch_frequency=0.0,
        substitution_frequency=0.0,
    )


def _best_with_captain_frozen(
    current: Lineup,
    roster: pd.DataFrame,
    actual: Mapping[str, float],
    completed_turn: int,
    rules: FantasyRules,
) -> Lineup:
    positions = {str(row.player_id): str(row.position) for row in roster.itertuples(index=False)}
    turns = {str(row.player_id): int(row.turn) for row in roster.itertuples(index=False)}
    means = {str(row.player_id): float(row.expected_fp) for row in roster.itertuples(index=False)}
    played = {player for player, turn in turns.items() if turn <= completed_turn}
    projected = {player: actual[player] if player in played else means[player] for player in turns}
    candidates = [
        value for value in enumerate_candidate_lineups(
            current.player_ids, current.coach_id, positions, rules
        ) if value.sixth_man == current.sixth_man
        and value.captain == current.captain
        and not ((value.starters - current.starters) & played)
    ]
    return max(candidates, key=lambda value: _static_mean(value, projected, rules))


def _slots_dict(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def _json_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
