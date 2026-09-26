"""Phase 7 marginal outcome sampling and legal between-Turn recourse."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri

from src.strategy.optimizer import enumerate_candidate_lineups
from src.strategy.rules import FantasyRules, Lineup, score_lineup


QUANTILE_COLUMNS = (
    (0.10, "p10_fp"), (0.25, "p25_fp"), (0.50, "p50_fp"),
    (0.75, "p75_fp"), (0.90, "p90_fp"), (0.95, "p95_fp"),
)
DOWNSIDE_COLUMNS = ((5.0, "prob_fp_le_5"), (10.0, "prob_fp_le_10"), (15.0, "prob_fp_le_15"))
UPSIDE_COLUMNS = (
    (20.0, "prob_fp_ge_20"), (25.0, "prob_fp_ge_25"),
    (30.0, "prob_fp_ge_30"), (35.0, "prob_fp_ge_35"),
    (40.0, "prob_fp_ge_40"),
)


@dataclass(frozen=True, slots=True)
class MarginalDistribution:
    probabilities: np.ndarray
    values: np.ndarray
    expected_fp: float
    implied_mean: float
    mean_error: float
    reconciliation_max_adjustment: float

    def sample(self, uniforms: np.ndarray) -> np.ndarray:
        return np.interp(
            np.clip(np.asarray(uniforms, dtype=float), 0.0, 1.0),
            self.probabilities, self.values,
        )


@dataclass(frozen=True, slots=True)
class SimulationDiagnostics:
    simulations: int
    mean_mae: float
    quantile_mae: float
    upside_probability_mae: float
    downside_probability_mae: float
    max_absolute_probability_error: float
    reconciliation_max_adjustment: float
    dependence_mode: str
    dependence_rho: float


@dataclass(frozen=True, slots=True)
class DynamicEvaluation:
    scores: np.ndarray
    expected_final_score: float
    median_final_score: float
    p10_final_score: float
    p90_final_score: float
    downside_frequency: float
    high_score_frequency: float
    captain_switch_frequency: float
    substitution_frequency: float


def build_marginal(
    row: Mapping[str, Any], *, include_upside: bool = True,
    include_downside: bool = True,
) -> MarginalDistribution:
    """Reconcile Phase 6C quantiles/tails into a bounded inverse CDF."""

    mean = _finite(row.get("expected_fp", row.get("phase6c_expected_fp")), 0.0)
    anchors: list[tuple[float, float, float]] = []
    for probability, column in QUANTILE_COLUMNS:
        anchors.append((probability, _finite(row.get(column), mean), 5.0))
    if include_downside:
        for threshold, column in DOWNSIDE_COLUMNS:
            anchors.append((_probability(row.get(column)), threshold, 2.0))
    if include_upside:
        for threshold, column in UPSIDE_COLUMNS:
            anchors.append((1.0 - _probability(row.get(column)), threshold, 2.0))
    # Merge probability collisions before monotone reconciliation.
    grouped: dict[float, list[tuple[float, float]]] = {}
    for probability, value, weight in anchors:
        grouped.setdefault(round(float(probability), 10), []).append((value, weight))
    probabilities = []
    values = []
    weights = []
    for probability in sorted(grouped):
        pairs = grouped[probability]
        total_weight = sum(weight for _, weight in pairs)
        probabilities.append(float(probability))
        values.append(sum(value * weight for value, weight in pairs) / total_weight)
        weights.append(total_weight)
    probability_array = np.asarray(probabilities, dtype=float)
    raw_values = np.asarray(values, dtype=float)
    reconciled = _weighted_isotonic(raw_values, np.asarray(weights, dtype=float))
    adjustment = float(np.max(np.abs(reconciled - raw_values))) if len(raw_values) else 0.0
    low = min(-20.0, float(reconciled[0]) - 12.0)
    high = max(65.0, float(reconciled[-1]) + 18.0)
    probability_array = np.r_[0.0, probability_array, 1.0]
    reconciled = np.r_[low, reconciled, high]
    implied = float(np.trapezoid(reconciled, probability_array))
    delta = mean - implied
    if delta > 0:
        mass_width = max(1.0 - probability_array[-2], 1e-3)
        reconciled[-1] = max(reconciled[-2], reconciled[-1] + 2.0 * delta / mass_width)
    elif delta < 0:
        mass_width = max(probability_array[1], 1e-3)
        reconciled[0] = min(reconciled[1], reconciled[0] + 2.0 * delta / mass_width)
    reconciled[0] = max(reconciled[0], -50.0)
    reconciled[-1] = min(reconciled[-1], 150.0)
    implied = float(np.trapezoid(reconciled, probability_array))
    residual = mean - implied
    # Frozen outputs occasionally contain mutually incompatible mean/tail anchors.
    # A small location reconciliation is preferable to inventing a parametric tail.
    if abs(residual) > 1e-10:
        reconciled = reconciled + residual
    reconciled = np.maximum.accumulate(reconciled)
    implied = float(np.trapezoid(reconciled, probability_array))
    return MarginalDistribution(
        probability_array, reconciled, mean, implied, implied - mean, adjustment,
    )


def simulate_player_outcomes(
    players: pd.DataFrame,
    simulations: int,
    *,
    seed: int,
    dependence_mode: str = "INDEPENDENT_VALIDATED",
    dependence_rho: float = 0.0,
    include_upside: bool = True,
    include_downside: bool = True,
) -> tuple[np.ndarray, SimulationDiagnostics]:
    """Sample player outcomes from frozen Phase 6C outputs, never refitting FP."""

    if simulations <= 0:
        raise ValueError("simulations must be positive")
    marginals = [
        build_marginal(
            row, include_upside=include_upside, include_downside=include_downside
        ) for row in players.to_dict("records")
    ]
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    uniforms = rng.random((simulations, len(players)))
    rho = float(np.clip(dependence_rho, -0.05, 0.25))
    if dependence_mode != "INDEPENDENT_VALIDATED" and rho > 0 and len(players):
        independent_z = ndtri(np.clip(uniforms, 1e-9, 1 - 1e-9))
        game_keys = players.get(
            "game_id", pd.Series("GAME", index=players.index)
        ).astype(str)
        keys = game_keys if dependence_mode.startswith("GAME_") else (
            game_keys + ":" + players.get(
                "team_id", pd.Series("TEAM", index=players.index)
            ).astype(str)
        )
        for key in sorted(keys.unique()):
            columns = np.flatnonzero(keys.to_numpy() == key)
            common = rng.standard_normal((simulations, 1))
            independent_z[:, columns] = (
                math.sqrt(rho) * common
                + math.sqrt(1.0 - rho) * independent_z[:, columns]
            )
        uniforms = ndtr(independent_z)
    elif dependence_mode != "INDEPENDENT_VALIDATED" and rho < 0 and len(players):
        independent_z = ndtri(np.clip(uniforms, 1e-9, 1 - 1e-9))
        game_keys = players.get(
            "game_id", pd.Series("GAME", index=players.index)
        ).astype(str)
        keys = game_keys if dependence_mode.startswith("GAME_") else (
            game_keys + ":" + players.get(
                "team_id", pd.Series("TEAM", index=players.index)
            ).astype(str)
        )
        for key in sorted(keys.unique()):
            columns = np.flatnonzero(keys.to_numpy() == key)
            if len(columns) <= 1:
                continue
            bounded_rho = max(rho, -1.0 / (len(columns) - 1) + 1e-6)
            covariance = (
                (1.0 - bounded_rho) * np.eye(len(columns))
                + bounded_rho * np.ones((len(columns), len(columns)))
            )
            cholesky = np.linalg.cholesky(covariance)
            independent_z[:, columns] = independent_z[:, columns] @ cholesky.T
        uniforms = ndtr(independent_z)
    outcomes = np.column_stack([
        marginal.sample(uniforms[:, index]) for index, marginal in enumerate(marginals)
    ]) if marginals else np.empty((simulations, 0), dtype=float)
    diagnostics = marginal_calibration_diagnostics(
        players, outcomes,
        dependence_mode=dependence_mode,
        dependence_rho=rho,
        reconciliation=max((value.reconciliation_max_adjustment for value in marginals), default=0.0),
    )
    return outcomes, diagnostics


def marginal_calibration_diagnostics(
    players: pd.DataFrame,
    outcomes: np.ndarray,
    *,
    dependence_mode: str,
    dependence_rho: float,
    reconciliation: float = 0.0,
) -> SimulationDiagnostics:
    if len(players) == 0:
        return SimulationDiagnostics(
            len(outcomes), 0.0, 0.0, 0.0, 0.0, 0.0, reconciliation,
            dependence_mode, dependence_rho,
        )
    mean_target = pd.to_numeric(
        players.get("expected_fp", players.get("phase6c_expected_fp")), errors="coerce"
    ).to_numpy(float)
    mean_mae = float(np.nanmean(np.abs(outcomes.mean(axis=0) - mean_target)))
    quantile_errors = []
    for probability, column in QUANTILE_COLUMNS:
        target = pd.to_numeric(players[column], errors="coerce").to_numpy(float)
        sampled = np.quantile(outcomes, probability, axis=0)
        quantile_errors.extend(np.abs(sampled - target)[np.isfinite(target)])
    upside_errors = []
    downside_errors = []
    all_probability_errors = []
    for threshold, column in UPSIDE_COLUMNS:
        target = pd.to_numeric(players[column], errors="coerce").to_numpy(float)
        sampled = (outcomes >= threshold).mean(axis=0)
        errors = np.abs(sampled - target)
        upside_errors.extend(errors[np.isfinite(target)])
        all_probability_errors.extend(errors[np.isfinite(target)])
    for threshold, column in DOWNSIDE_COLUMNS:
        target = pd.to_numeric(players[column], errors="coerce").to_numpy(float)
        sampled = (outcomes <= threshold).mean(axis=0)
        errors = np.abs(sampled - target)
        downside_errors.extend(errors[np.isfinite(target)])
        all_probability_errors.extend(errors[np.isfinite(target)])
    return SimulationDiagnostics(
        simulations=len(outcomes),
        mean_mae=mean_mae,
        quantile_mae=float(np.mean(quantile_errors)) if quantile_errors else 0.0,
        upside_probability_mae=float(np.mean(upside_errors)) if upside_errors else 0.0,
        downside_probability_mae=float(np.mean(downside_errors)) if downside_errors else 0.0,
        max_absolute_probability_error=(
            float(np.max(all_probability_errors)) if all_probability_errors else 0.0
        ),
        reconciliation_max_adjustment=float(reconciliation),
        dependence_mode=dependence_mode,
        dependence_rho=float(dependence_rho),
    )


def simulate_static_scores(
    lineup: Lineup,
    player_ids: Sequence[str],
    player_outcomes: np.ndarray,
    coach_outcomes: np.ndarray,
    rules: FantasyRules,
) -> np.ndarray:
    index = {str(player): offset for offset, player in enumerate(player_ids)}
    values = np.asarray(coach_outcomes, dtype=float).copy()
    for player in lineup.player_ids:
        multiplier = (
            1.0 if player in lineup.starters else
            rules.sixth_man_multiplier if player == lineup.sixth_man else
            rules.bench_multiplier
        )
        if player == lineup.captain:
            multiplier += rules.captain_multiplier - 1.0
        values += multiplier * player_outcomes[:, index[player]]
    return values


def simulate_dynamic_scores(
    initial: Lineup,
    players: pd.DataFrame,
    player_outcomes: np.ndarray,
    coach_outcomes: np.ndarray,
    rules: FantasyRules,
    *,
    high_score_threshold: float = 180.0,
    downside_threshold: float = 120.0,
) -> DynamicEvaluation:
    """Apply a no-lookahead expected-value policy after each completed Turn."""

    roster = players[players["player_id"].astype(str).isin(initial.player_ids)].copy()
    roster = roster.set_index(roster["player_id"].astype(str), drop=False)
    ids = tuple(map(str, players["player_id"]))
    source_index = {player: index for index, player in enumerate(ids)}
    roster_columns = [source_index[player] for player in initial.player_ids]
    positions = {player: str(roster.loc[player, "position"]) for player in initial.player_ids}
    turns = {player: int(roster.loc[player, "turn"]) for player in initial.player_ids}
    means = {
        player: float(roster.loc[player, "expected_fp"]) for player in initial.player_ids
    }
    states = tuple(
        lineup for lineup in enumerate_candidate_lineups(
            initial.player_ids, initial.coach_id, positions, rules
        ) if lineup.sixth_man == initial.sixth_man
    )
    last_turn = max(turns.values())
    scores = np.empty(len(player_outcomes), dtype=float)
    captain_switches = 0
    substitutions = 0
    for simulation_index in range(len(player_outcomes)):
        actual = {
            player: float(player_outcomes[simulation_index, source_index[player]])
            for player in initial.player_ids
        }
        current = initial
        for completed_turn in range(1, last_turn):
            played = {player for player, turn in turns.items() if turn <= completed_turn}
            projected = {player: actual[player] if player in played else means[player]
                         for player in initial.player_ids}
            best = current
            best_score = _projected_player_score(current, projected, rules)
            for candidate in states:
                if not _legal_transition_fast(current, candidate, played):
                    continue
                candidate_score = _projected_player_score(candidate, projected, rules)
                candidate_key = _lineup_key(candidate)
                if candidate_score > best_score + 1e-10 or (
                    abs(candidate_score - best_score) <= 1e-10
                    and candidate_key < _lineup_key(best)
                ):
                    best = candidate
                    best_score = candidate_score
            captain_switches += int(best.captain != current.captain)
            substitutions += len(best.starters - current.starters)
            current = best
        scores[simulation_index] = score_lineup(
            current, actual, float(coach_outcomes[simulation_index]), rules
        )
    return DynamicEvaluation(
        scores=scores,
        expected_final_score=float(np.mean(scores)),
        median_final_score=float(np.median(scores)),
        p10_final_score=float(np.quantile(scores, 0.10)),
        p90_final_score=float(np.quantile(scores, 0.90)),
        downside_frequency=float(np.mean(scores <= downside_threshold)),
        high_score_frequency=float(np.mean(scores >= high_score_threshold)),
        captain_switch_frequency=float(captain_switches / max(len(scores), 1)),
        substitution_frequency=float(substitutions / max(len(scores), 1)),
    )


def recommend_next_turn(
    current: Lineup,
    players: pd.DataFrame,
    observed_scores: Mapping[str, float],
    completed_turn: int,
    rules: FantasyRules,
) -> tuple[Lineup, tuple[dict[str, Any], ...]]:
    """Return the legal action maximizing expected final score from the current state."""

    roster = players[players["player_id"].astype(str).isin(current.player_ids)].copy()
    roster = roster.set_index(roster["player_id"].astype(str), drop=False)
    positions = {player: str(roster.loc[player, "position"]) for player in current.player_ids}
    turns = {player: int(roster.loc[player, "turn"]) for player in current.player_ids}
    played = {player for player, turn in turns.items() if turn <= int(completed_turn)}
    missing = sorted(played - set(map(str, observed_scores)))
    if missing:
        raise ValueError(
            "observed scores are required for every completed-Turn roster player: "
            + ", ".join(missing)
        )
    projected = {
        player: float(observed_scores[player]) if player in played
        else float(roster.loc[player, "expected_fp"])
        for player in current.player_ids
    }
    candidates = (
        lineup for lineup in enumerate_candidate_lineups(
            current.player_ids, current.coach_id, positions, rules
        ) if lineup.sixth_man == current.sixth_man
        and _legal_transition_fast(current, lineup, played)
    )
    best = max(candidates, key=lambda value: (
        _projected_player_score(value, projected, rules),
        tuple(sorted(value.starters)), value.captain,
    ))
    actions: list[dict[str, Any]] = []
    for player in sorted(current.starters - best.starters):
        actions.append({"action": "MOVE_TO_BENCH", "player_id": player})
    for player in sorted(best.starters - current.starters):
        actions.append({"action": "MOVE_TO_FIELD", "player_id": player})
    if best.captain != current.captain:
        actions.append({
            "action": "CHANGE_CAPTAIN", "from_player_id": current.captain,
            "to_player_id": best.captain,
        })
    return best, tuple(actions)


def _legal_transition_fast(before: Lineup, after: Lineup, played: set[str]) -> bool:
    return (
        before.player_ids == after.player_ids
        and before.coach_id == after.coach_id
        and before.sixth_man == after.sixth_man
        and not ((after.starters - before.starters) & played)
        and (after.captain == before.captain or after.captain not in played)
        and after.captain in after.starters
    )


def _projected_player_score(
    lineup: Lineup, projected: Mapping[str, float], rules: FantasyRules
) -> float:
    return score_lineup(lineup, projected, 0.0, rules)


def _lineup_key(lineup: Lineup) -> tuple[Any, ...]:
    return tuple(sorted(lineup.starters)), lineup.sixth_man, lineup.captain


def _weighted_isotonic(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    # Weighted pool-adjacent-violators, implemented here to keep the sampler stable.
    blocks: list[list[float]] = []
    for index, (value, weight) in enumerate(zip(values, weights, strict=True)):
        blocks.append([float(index), float(index), float(weight), float(value * weight)])
        while len(blocks) >= 2:
            previous = blocks[-2][3] / blocks[-2][2]
            current = blocks[-1][3] / blocks[-1][2]
            if previous <= current:
                break
            right = blocks.pop(); left = blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    output = np.empty(len(values), dtype=float)
    for start, end, weight, weighted_sum in blocks:
        output[int(start):int(end) + 1] = weighted_sum / weight
    return output


def _finite(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if np.isfinite(parsed) else float(default)


def _probability(value: Any) -> float:
    return float(np.clip(_finite(value, 0.5), 1e-6, 1.0 - 1e-6))
