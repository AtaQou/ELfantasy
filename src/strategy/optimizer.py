"""Exact deterministic roster optimization and bounded stochastic candidate search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from src.strategy.rules import (
    FantasyRules,
    Lineup,
    POSITIONS,
    RuleViolation,
    normalize_position,
    validate_lineup,
)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    lineup: Lineup
    objective_value: float
    total_credits: float
    solver_status: str
    solver_gap: float | None
    optimal: bool
    roster_fingerprint: str


def optimize_deterministic(
    players: pd.DataFrame,
    coaches: pd.DataFrame,
    rules: FantasyRules,
    *,
    budget: float | None = None,
    player_objective: str = "expected_fp",
    coach_objective: str = "expected_score",
    roster_only: bool = False,
    previous_entity_ids: Iterable[str] = (),
    trade_limit: int | None = None,
    excluded_rosters: Sequence[frozenset[str]] = (),
) -> OptimizationResult:
    """Solve the complete legal roster/formation/captain problem with MILP."""

    player_frame = _prepare_players(players, player_objective)
    coach_frame = _prepare_coaches(coaches, coach_objective)
    if player_frame.empty or coach_frame.empty:
        raise RuleViolation("player and coach candidate pools must both be non-empty")
    ceiling = rules.budget_credits if budget is None else float(budget)
    n_player = len(player_frame)
    n_coach = len(coach_frame)
    n_formation = len(rules.formations)
    # Four player variables: roster, starter, sixth man, captain.
    x0, s0, u0, c0 = 0, n_player, 2 * n_player, 3 * n_player
    y0, z0 = 4 * n_player, 4 * n_player + n_coach
    n_var = z0 + n_formation
    objective = np.zeros(n_var, dtype=float)
    values = player_frame["_objective"].to_numpy(float)
    if roster_only:
        objective[x0:x0 + n_player] = -values
    else:
        objective[x0:x0 + n_player] = -rules.bench_multiplier * values
        objective[s0:s0 + n_player] = -(1.0 - rules.bench_multiplier) * values
        objective[u0:u0 + n_player] = -(
            rules.sixth_man_multiplier - rules.bench_multiplier
        ) * values
        objective[c0:c0 + n_player] = -(rules.captain_multiplier - 1.0) * values
    objective[y0:y0 + n_coach] = -coach_frame["_objective"].to_numpy(float)

    rows: list[dict[int, float]] = []
    lower: list[float] = []
    upper: list[float] = []

    def add(coefficients: Mapping[int, float], low: float, high: float) -> None:
        rows.append(dict(coefficients)); lower.append(low); upper.append(high)

    add({x0 + i: 1.0 for i in range(n_player)}, rules.player_count, rules.player_count)
    add({y0 + i: 1.0 for i in range(n_coach)}, rules.coach_count, rules.coach_count)
    for position, count in rules.position_counts.items():
        add({
            x0 + i: 1.0 for i, value in enumerate(player_frame["_position"])
            if value == position
        }, count, count)
    add({s0 + i: 1.0 for i in range(n_player)}, rules.starter_count, rules.starter_count)
    add({u0 + i: 1.0 for i in range(n_player)}, 1.0, 1.0)
    add({c0 + i: 1.0 for i in range(n_player)}, 1.0, 1.0)
    add({z0 + i: 1.0 for i in range(n_formation)}, 1.0, 1.0)
    for i in range(n_player):
        add({s0 + i: 1.0, x0 + i: -1.0}, -np.inf, 0.0)
        add({u0 + i: 1.0, x0 + i: -1.0}, -np.inf, 0.0)
        add({c0 + i: 1.0, s0 + i: -1.0}, -np.inf, 0.0)
        add({s0 + i: 1.0, u0 + i: 1.0}, -np.inf, 1.0)
    for p_idx, position in enumerate(POSITIONS):
        coefficients = {
            s0 + i: 1.0 for i, value in enumerate(player_frame["_position"])
            if value == position
        }
        for f_idx, formation in enumerate(rules.formations):
            coefficients[z0 + f_idx] = -float(formation[p_idx])
        add(coefficients, 0.0, 0.0)
    for _, indices in player_frame.groupby("team_id", sort=False).groups.items():
        add({x0 + int(i): 1.0 for i in indices}, -np.inf, rules.team_player_limit)
    credit_coefficients = {
        x0 + i: float(value) for i, value in enumerate(player_frame["credits"])
    }
    credit_coefficients.update({
        y0 + i: float(value) for i, value in enumerate(coach_frame["credits"])
    })
    add(credit_coefficients, -np.inf, ceiling)

    previous = set(map(str, previous_entity_ids))
    if previous and trade_limit is not None:
        retained: dict[int, float] = {}
        for i, value in enumerate(player_frame["entity_id"].astype(str)):
            if value in previous:
                retained[x0 + i] = 1.0
        for i, value in enumerate(coach_frame["entity_id"].astype(str)):
            if value in previous:
                retained[y0 + i] = 1.0
        minimum = max(len(previous) - int(trade_limit), 0)
        add(retained, minimum, np.inf)
    entity_to_variable = {
        **{str(value): x0 + i for i, value in enumerate(player_frame["entity_id"])},
        **{str(value): y0 + i for i, value in enumerate(coach_frame["entity_id"])},
    }
    for roster in excluded_rosters:
        variables = [entity_to_variable[value] for value in roster if value in entity_to_variable]
        if variables:
            add({index: 1.0 for index in variables}, -np.inf, len(roster) - 1)

    matrix = lil_matrix((len(rows), n_var), dtype=float)
    for row_index, coefficients in enumerate(rows):
        for column, value in coefficients.items():
            matrix[row_index, column] = value
    result = milp(
        c=objective,
        integrality=np.ones(n_var, dtype=np.int8),
        bounds=Bounds(np.zeros(n_var), np.ones(n_var)),
        constraints=LinearConstraint(matrix.tocsr(), np.asarray(lower), np.asarray(upper)),
        options={"presolve": True, "mip_rel_gap": 0.0},
    )
    if result.x is None:
        raise RuleViolation(f"no legal Fantasy roster: solver status {result.message}")
    solution = np.asarray(result.x)
    selected_players = player_frame.loc[solution[x0:x0 + n_player] > 0.5]
    selected_coach = coach_frame.loc[solution[y0:y0 + n_coach] > 0.5].iloc[0]
    starters = frozenset(
        player_frame.loc[solution[s0:s0 + n_player] > 0.5, "player_id"].astype(str)
    )
    sixth = str(player_frame.loc[solution[u0:u0 + n_player] > 0.5, "player_id"].iloc[0])
    captain = str(player_frame.loc[solution[c0:c0 + n_player] > 0.5, "player_id"].iloc[0])
    lineup = Lineup(
        tuple(selected_players["player_id"].astype(str)), str(selected_coach["coach_id"]),
        starters, sixth, captain,
    )
    player_records = {
        str(row["player_id"]): {
            "position": row["_position"], "team_id": str(row["team_id"]),
            "credits": row["credits"],
        }
        for _, row in selected_players.iterrows()
    }
    validate_lineup(
        lineup, player_records,
        {"credits": float(selected_coach["credits"]), "team_id": selected_coach["team_id"]},
        rules, budget=ceiling,
    )
    entity_ids = sorted([
        *selected_players["entity_id"].astype(str), str(selected_coach["entity_id"]),
    ])
    gap_value = getattr(result, "mip_gap", None)
    gap = float(gap_value) if gap_value is not None and np.isfinite(gap_value) else None
    return OptimizationResult(
        lineup=lineup,
        objective_value=float(-result.fun),
        total_credits=float(selected_players["credits"].sum() + selected_coach["credits"]),
        solver_status=str(result.message),
        solver_gap=gap,
        optimal=bool(result.success and (gap is None or gap <= 1e-9)),
        roster_fingerprint=_roster_fingerprint(entity_ids),
    )


def optimize_roles_for_roster(
    players: pd.DataFrame,
    coaches: pd.DataFrame,
    rules: FantasyRules,
    player_ids: Iterable[str],
    coach_id: str,
    *,
    objective: str = "expected_fp",
) -> OptimizationResult:
    selected = set(map(str, player_ids))
    player_pool = players[players["player_id"].astype(str).isin(selected)].copy()
    coach_pool = coaches[coaches["coach_id"].astype(str).eq(str(coach_id))].copy()
    if len(player_pool) != rules.player_count or len(coach_pool) != 1:
        raise RuleViolation("fixed roster is incomplete")
    budget = float(player_pool["credits"].sum() + coach_pool["credits"].sum())
    return optimize_deterministic(
        player_pool, coach_pool, rules, budget=budget,
        player_objective=objective, excluded_rosters=(),
    )


def enumerate_candidate_lineups(
    player_ids: Sequence[str],
    coach_id: str,
    positions: Mapping[str, str],
    rules: FantasyRules,
    *,
    max_candidates: int | None = None,
) -> tuple[Lineup, ...]:
    """Enumerate legal starting/captain/sixth layouts for a fixed roster."""

    from itertools import combinations

    ids = tuple(map(str, player_ids))
    values: list[Lineup] = []
    for starters_tuple in combinations(ids, rules.starter_count):
        starters = frozenset(starters_tuple)
        formation = tuple(
            sum(normalize_position(positions[player]) == position for player in starters)
            for position in POSITIONS
        )
        if formation not in rules.formations:
            continue
        bench = [player for player in ids if player not in starters]
        for sixth in bench:
            for captain in starters_tuple:
                values.append(Lineup(ids, str(coach_id), starters, sixth, captain))
                if max_candidates is not None and len(values) >= max_candidates:
                    return tuple(values)
    return tuple(values)


def entity_ids_for_result(
    result: OptimizationResult,
    players: pd.DataFrame,
    coaches: pd.DataFrame,
) -> frozenset[str]:
    selected = players[players["player_id"].astype(str).isin(result.lineup.player_ids)]
    coach = coaches[coaches["coach_id"].astype(str).eq(result.lineup.coach_id)]
    return frozenset([*selected["entity_id"].astype(str), *coach["entity_id"].astype(str)])


def _prepare_players(frame: pd.DataFrame, objective: str) -> pd.DataFrame:
    required = {"player_id", "entity_id", "team_id", "position", "credits", objective}
    missing = required - set(frame.columns)
    if missing:
        raise RuleViolation(f"player pool missing columns: {sorted(missing)}")
    output = frame.copy().reset_index(drop=True)
    output["_position"] = output["position"].map(normalize_position)
    output["credits"] = pd.to_numeric(output["credits"], errors="coerce")
    output["_objective"] = pd.to_numeric(output[objective], errors="coerce")
    output = output[
        np.isfinite(output["credits"]) & np.isfinite(output["_objective"])
        & output["player_id"].notna() & output["entity_id"].notna()
    ].copy()
    return output.reset_index(drop=True)


def _prepare_coaches(frame: pd.DataFrame, objective: str) -> pd.DataFrame:
    required = {"coach_id", "entity_id", "team_id", "credits", objective}
    missing = required - set(frame.columns)
    if missing:
        raise RuleViolation(f"coach pool missing columns: {sorted(missing)}")
    output = frame.copy().reset_index(drop=True)
    output["credits"] = pd.to_numeric(output["credits"], errors="coerce")
    output["_objective"] = pd.to_numeric(output[objective], errors="coerce")
    output = output[np.isfinite(output["credits"]) & np.isfinite(output["_objective"])].copy()
    return output.reset_index(drop=True)


def _roster_fingerprint(entity_ids: Sequence[str]) -> str:
    import hashlib

    return hashlib.sha256("\n".join(sorted(map(str, entity_ids))).encode()).hexdigest()
