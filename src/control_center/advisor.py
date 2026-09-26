"""After-Turn statistical advice around the frozen Phase 7 recourse policy."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.strategy.coach import simulate_coach_outcomes
from src.strategy.rules import (
    FantasyRules,
    Lineup,
    lineup_formation,
    score_lineup,
    validate_turn_transition,
)
from src.strategy.simulation import recommend_next_turn, simulate_player_outcomes

from .shadow import lineup_from_recommendation, recommendation_for_lineup
from .state import json_fingerprint


def build_after_turn_advice(
    snapshot: Mapping[str, Any],
    turn_outcome: Mapping[str, Any],
    rules: FantasyRules,
    *,
    current_lineup: Mapping[str, Any] | None = None,
    simulations: int = 512,
    seed: int = 20250802,
) -> dict[str, Any]:
    """Evaluate legal actions using realized past and probabilistic future outcomes."""

    if not snapshot.get("recommendations"):
        raise ValueError("shadow snapshot has no recommendation")
    recommendation = recommendation_for_lineup(snapshot, current_lineup)
    original = lineup_from_recommendation(recommendation)
    current = (
        _lineup_from_payload(current_lineup) if current_lineup
        else original
    )
    if (set(current.player_ids) != set(original.player_ids)
            or current.coach_id != original.coach_id
            or current.sixth_man != original.sixth_man):
        raise ValueError("current between-Turn state cannot change roster, coach, or sixth man")
    completed_turn = int(turn_outcome["completed_turn"])
    observed = {str(key): float(value) for key, value in
                turn_outcome["observed_scores"]["players"].items()}
    inputs = snapshot["knowledge"]["decision_inputs"]
    all_players = pd.DataFrame(inputs["players"])
    roster = all_players[all_players["player_id"].astype(str).isin(current.player_ids)].copy()
    roster.index = roster["player_id"].astype(str)
    roster = roster.loc[list(current.player_ids)].reset_index(drop=True)
    positions = {str(row.player_id): str(row.position) for row in roster.itertuples(index=False)}
    if (len(current.starters) != rules.starter_count
            or current.captain not in current.starters
            or lineup_formation(current, positions) not in rules.formations):
        raise ValueError("current between-Turn state is not a legal formation/captain setup")
    last_turn = int(roster["turn"].max())
    if completed_turn >= last_turn:
        return {
            "status": "NO_ACTION", "completed_turn": completed_turn,
            "reason": "All roster Turns are complete; no future player remains eligible.",
            "current_lineup": _lineup_payload(current), "recommended_lineup": _lineup_payload(current),
            "actions": [], "evidence": {}, "simulation": {"simulations": 0, "seed": seed},
        }
    played = set(roster.loc[roster["turn"].le(completed_turn), "player_id"].astype(str))
    if played - set(observed):
        raise ValueError("realized scores are missing for: " + ", ".join(sorted(played - set(observed))))
    recommended, phase7_actions = recommend_next_turn(
        current, roster, observed, completed_turn, rules
    )
    validate_turn_transition(current, recommended, played, rules)

    samples, diagnostics = simulate_player_outcomes(
        roster, simulations, seed=seed,
        dependence_mode="GAME_GAUSSIAN_COPULA_VALIDATED", dependence_rho=-0.015,
    )
    player_index = {str(value): index for index, value in enumerate(roster["player_id"])}
    for player, actual in observed.items():
        if player in player_index:
            samples[:, player_index[player]] = float(actual)
    coach_row = _selected_coach(inputs.get("coaches", []), current.coach_id)
    coach_frame = pd.DataFrame([coach_row])
    coach_samples = simulate_coach_outcomes(coach_frame, simulations, seed=seed + 17)[:, 0]
    observed_coach = turn_outcome["observed_scores"].get("coach")
    if observed_coach is not None:
        coach_samples[:] = float(observed_coach)

    keep_scores = _conditional_dynamic_scores(
        current, roster, samples, coach_samples, completed_turn, rules
    )
    switch_scores = _conditional_dynamic_scores(
        recommended, roster, samples, coach_samples, completed_turn, rules
    )
    comparison = _comparison(keep_scores, switch_scores)
    player_lookup = {str(row.player_id): row for row in roster.itertuples(index=False)}
    switch_evidence = _switch_evidence(
        current, recommended, observed, samples, player_index, player_lookup,
        comparison, rules,
    )
    captain = _captain_evidence(
        current, recommended, observed, roster, samples, coach_samples,
        completed_turn, keep_scores, switch_scores, player_lookup, rules,
    )
    portfolio = _portfolio(current, recommended, roster, completed_turn, switch_evidence)
    actions = [
        {**dict(action), "statistical_reasons": _action_reasons(
            action, switch_evidence, captain, comparison, player_lookup
        )}
        for action in phase7_actions
    ]
    result = {
        "status": "READY" if actions else "NO_ACTION",
        "completed_turn": completed_turn,
        "current_lineup": _lineup_payload(current),
        "recommended_lineup": _lineup_payload(recommended),
        "actions": actions,
        "evidence": {
            "full_team_keep_vs_switch": comparison,
            "player_switches": switch_evidence,
            "captain": captain,
            "turn_portfolio": portfolio,
            "selected_player_statistics": [
                _player_statistics(row) for row in roster.itertuples(index=False)
            ],
            "decision_basis": (
                "Frozen Phase 7 legal recourse, evaluated with common Phase 6C Monte Carlo "
                "draws. No probability cutoff or risk bonus is used."
            ),
        },
        "simulation": {
            "simulations": simulations, "seed": seed,
            "dependence_mode": "GAME_GAUSSIAN_COPULA_VALIDATED",
            "dependence_rho": -0.015,
            "marginal_diagnostics": {
                name: getattr(diagnostics, name) for name in diagnostics.__dataclass_fields__
            },
            "common_random_numbers": True,
        },
    }
    result["input_fingerprint"] = json_fingerprint({
        "shadow_snapshot_fingerprint": snapshot["snapshot_fingerprint"],
        "turn_outcome_fingerprint": turn_outcome["outcome_fingerprint"],
        "current_lineup": result["current_lineup"], "simulations": simulations, "seed": seed,
    })
    return result


def _conditional_dynamic_scores(
    after_current_turn: Lineup,
    roster: pd.DataFrame,
    player_samples: np.ndarray,
    coach_samples: np.ndarray,
    completed_turn: int,
    rules: FantasyRules,
) -> np.ndarray:
    """Continue the frozen no-lookahead Phase 7 policy from a conditional state."""

    ids = tuple(map(str, roster["player_id"]))
    index = {player: offset for offset, player in enumerate(ids)}
    turns = {str(row.player_id): int(row.turn) for row in roster.itertuples(index=False)}
    last_turn = max(turns.values())
    output = np.empty(len(player_samples), dtype=float)
    for simulation_index in range(len(player_samples)):
        actual = {player: float(player_samples[simulation_index, index[player]]) for player in ids}
        current = after_current_turn
        for turn in range(completed_turn + 1, last_turn):
            revealed = {player: score for player, score in actual.items() if turns[player] <= turn}
            current, _ = recommend_next_turn(current, roster, revealed, turn, rules)
        output[simulation_index] = score_lineup(
            current, actual, float(coach_samples[simulation_index]), rules
        )
    return output


def _comparison(keep: np.ndarray, switch: np.ndarray) -> dict[str, Any]:
    difference = switch - keep
    return {
        "expected_final_team_score_keep": float(np.mean(keep)),
        "expected_final_team_score_switch": float(np.mean(switch)),
        "expected_final_team_gain": float(np.mean(difference)),
        "probability_switch_final_score_exceeds_keep": float(np.mean(difference > 1e-10)),
        "p10_keep": float(np.quantile(keep, 0.10)),
        "p10_switch": float(np.quantile(switch, 0.10)),
        "p50_keep": float(np.quantile(keep, 0.50)),
        "p50_switch": float(np.quantile(switch, 0.50)),
        "p90_keep": float(np.quantile(keep, 0.90)),
        "p90_switch": float(np.quantile(switch, 0.90)),
    }


def _switch_evidence(
    before: Lineup, after: Lineup, observed: Mapping[str, float],
    samples: np.ndarray, index: Mapping[str, int], lookup: Mapping[str, Any],
    comparison: Mapping[str, Any], rules: FantasyRules,
) -> list[dict[str, Any]]:
    demoted = sorted(before.starters - after.starters)
    promoted = sorted(after.starters - before.starters)
    output = []
    for old, new in zip(demoted, promoted, strict=True):
        candidate = lookup[new]
        realized = observed.get(old)
        probability = (
            float(np.mean(samples[:, index[new]] > float(realized)))
            if realized is not None else None
        )
        output.append({
            "recommendation": "SWITCH" if comparison["expected_final_team_gain"] > 0 else "KEEP",
            "keep_player_id": old, "keep_player": str(lookup[old].name),
            "keep_realized_fp": realized,
            "switch_player_id": new, "switch_player": str(candidate.name),
            "cross_position": str(lookup[old].position) != str(candidate.position),
            "new_player": _player_statistics(candidate),
            "old_player": _player_statistics(lookup[old]),
            "probability_new_player_exceeds_realized_player": probability,
            "expected_fantasy_gain": comparison["expected_final_team_gain"],
            "expected_final_team_score_keep": comparison["expected_final_team_score_keep"],
            "expected_final_team_score_switch": comparison["expected_final_team_score_switch"],
            "probability_final_team_switch_exceeds_keep": comparison[
                "probability_switch_final_score_exceeds_keep"
            ],
            "bench_multiplier_effect": float(1.0 - rules.bench_multiplier),
            "sixth_man_effect": "UNCHANGED_AND_FROZEN",
            "later_optionality": "PRESERVED" if int(candidate.turn) < max(
                int(row.turn) for row in lookup.values()
            ) else "NO_LATER_PLAYER_TURN_AFTER_THIS_CANDIDATE",
        })
    return output


def _captain_evidence(
    before: Lineup, after: Lineup, observed: Mapping[str, float], roster: pd.DataFrame,
    samples: np.ndarray, coach_samples: np.ndarray, completed_turn: int,
    keep_scores: np.ndarray, switch_scores: np.ndarray, lookup: Mapping[str, Any],
    rules: FantasyRules,
) -> dict[str, Any]:
    current_realized = observed.get(before.captain)
    candidate = lookup[after.captain]
    probability = float(np.mean(switch_scores > keep_scores + 1e-10))
    gain = float(np.mean(switch_scores - keep_scores))
    return {
        "recommendation": "SWITCH_CAPTAIN" if after.captain != before.captain else "KEEP_CAPTAIN",
        "current_captain_id": before.captain,
        "current_captain": str(lookup[before.captain].name),
        "current_captain_realized_fp": current_realized,
        "candidate_captain_id": after.captain,
        "candidate_captain": str(candidate.name),
        "candidate_statistics": _player_statistics(candidate),
        "captain_multiplier": rules.captain_multiplier,
        "expected_final_team_score_keep": float(np.mean(keep_scores)),
        "expected_final_team_score_switch": float(np.mean(switch_scores)),
        "expected_gain": gain,
        "probability_switch_produces_better_final_result": probability,
        "statistical_reasons": [
            f"Expected final-team change {gain:+.2f} FP under the frozen dynamic policy.",
            f"Switch beats keep in {probability:.1%} of paired simulations; no fixed threshold was used.",
        ],
        "eligible_candidates": [
            _player_statistics(row) for row in roster.itertuples(index=False)
            if int(row.turn) > completed_turn and str(row.player_id) in after.starters
        ][:3],
    }


def _portfolio(
    before: Lineup, after: Lineup, roster: pd.DataFrame, completed_turn: int,
    switches: list[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = {str(int(turn)): int(count) for turn, count in roster.groupby("turn").size().items()}
    latest = int(roster["turn"].max())
    later = roster[roster["turn"].gt(completed_turn)]
    protected = [item["keep_player_id"] for item in switches]
    return {
        "players_by_turn": counts,
        "later_replacements": [
            {"player_id": str(row.player_id), "name": str(row.name), "turn": int(row.turn),
             "position": str(row.position)} for row in later.itertuples(index=False)
            if str(row.player_id) not in before.starters
        ],
        "later_captain_options": [
            {"player_id": str(row.player_id), "name": str(row.name), "turn": int(row.turn),
             "p90": float(row.p90_fp)} for row in later.itertuples(index=False)
            if str(row.player_id) in after.starters
        ],
        "risk_protected_players": protected,
        "positions_without_later_safety_net": sorted({
            str(row.position) for row in roster.itertuples(index=False)
            if int(row.turn) == latest and str(row.player_id) in after.starters
        }),
        "turn_diversification_forced": False,
    }


def _action_reasons(
    action: Mapping[str, Any], switches: list[Mapping[str, Any]], captain: Mapping[str, Any],
    comparison: Mapping[str, Any], lookup: Mapping[str, Any],
) -> list[str]:
    if action["action"] == "CHANGE_CAPTAIN":
        return list(captain["statistical_reasons"])
    player_id = str(action.get("player_id"))
    switch = next((row for row in switches if player_id in {
        row["keep_player_id"], row["switch_player_id"]
    }), None)
    if switch:
        probability = switch["probability_new_player_exceeds_realized_player"]
        probability_text = f"{probability:.1%}" if probability is not None else "not applicable"
        return [
            f"Full-team paired simulation changes expected score by "
            f"{comparison['expected_final_team_gain']:+.2f} FP.",
            f"Candidate exceeds the realized player in {probability_text} of draws; "
            "the recommendation still uses full-team value.",
        ]
    return ["Action is part of the highest expected-value legal formation transition."]


def _player_statistics(row: Any) -> dict[str, Any]:
    expected = float(row.expected_fp)
    credits = float(row.credits)
    return {
        "player_id": str(row.player_id), "name": str(row.name),
        "position": str(row.position), "turn": int(row.turn), "credits": credits,
        "expected_minutes": _number(getattr(row, "expected_minutes", None)),
        "expected_fp": expected, "fp_per_credit": expected / credits if credits else None,
        "p10": float(row.p10_fp), "p50": float(row.p50_fp),
        "p90": float(row.p90_fp), "p95": float(row.p95_fp),
        "upside_probabilities": {
            str(threshold): _number(getattr(row, f"prob_fp_ge_{threshold}", None))
            for threshold in (20, 25, 30, 35, 40)
        },
        "downside_probabilities": {
            str(threshold): _number(getattr(row, f"prob_fp_le_{threshold}", None))
            for threshold in (5, 10, 15)
        },
    }


def _selected_coach(rows: list[Mapping[str, Any]], coach_id: str) -> dict[str, Any]:
    value = next((dict(row) for row in rows if str(row.get("coach_id")) == str(coach_id)), None)
    if value is None:
        raise ValueError(f"coach {coach_id} is absent from immutable decision inputs")
    return value


def _lineup_payload(lineup: Lineup) -> dict[str, Any]:
    return {"player_ids": list(lineup.player_ids), "coach_id": lineup.coach_id,
            "starters": sorted(lineup.starters), "sixth_man": lineup.sixth_man,
            "captain": lineup.captain}


def _lineup_from_payload(value: Mapping[str, Any]) -> Lineup:
    return Lineup(tuple(map(str, value["player_ids"])), str(value["coach_id"]),
                  frozenset(map(str, value["starters"])), str(value["sixth_man"]),
                  str(value["captain"]))


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None
