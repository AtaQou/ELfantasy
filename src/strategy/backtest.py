"""Point-in-time E2025 Fantasy strategy replay and baseline comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.strategy.coach import (
    CoachDistributionModel,
    build_coach_feature_history,
    coach_market_for_matchday,
    train_coach_model,
)
from src.strategy.engine import optimize_stochastic, replay_actual_round
from src.strategy.historical import (
    DEFAULT_PHASE7_RESEARCH_ROOT,
    load_historical_market,
    read_cached_predictions,
)
from src.strategy.optimizer import (
    OptimizationResult,
    entity_ids_for_result,
    optimize_deterministic,
    optimize_roles_for_roster,
)
from src.strategy.rules import FantasyRules, load_rules, register_rules_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE7_ROOT = PROJECT_ROOT / "data" / "derived" / "phase7"


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    backtest_version: str
    season: str
    matchdays_replayed: int
    matchday_min: int
    matchday_max: int
    metrics: dict[str, dict[str, float]]
    full_dynamic_gain_over_deterministic: float
    between_turn_adaptation_gain: float
    captain_switching_gain: float
    turn_diversification_gain: float
    average_regret_to_oracle: float
    coach_validation: dict[str, Any]
    rules_version: str
    rules_fingerprint: str
    predictions_fingerprint: str
    results_fingerprint: str


@dataclass(slots=True)
class _PortfolioState:
    entity_ids: frozenset[str] = frozenset()
    bank: float = 0.0
    last_prices: dict[str, float] | None = None


def run_e2025_backtest(
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    prediction_path: Path | str | None = None,
    matchdays: Iterable[int] = range(1, 39),
    rules: FantasyRules | None = None,
    training_simulations: int = 64,
    evaluation_simulations: int = 384,
    stochastic_candidates: int = 4,
    output_root: Path | str = DEFAULT_PHASE7_ROOT,
    seed: int = 20250701,
    persist_database: bool = True,
) -> tuple[pd.DataFrame, BacktestSummary]:
    rules = rules or load_rules()
    register_rules_manifest(rules, database_path=database_path)
    season = "E2025"
    predictions = read_cached_predictions(season, path=prediction_path)
    coach_history = build_coach_feature_history(database_path)
    coach_model = train_coach_model(coach_history, validation_season=season)
    strategies = (
        "HIGHEST_EXPECTED_ROSTER",
        "FP_PER_CREDIT",
        "DETERMINISTIC_EXPECTED",
        "STATIC_STOCHASTIC",
        "FULL_DYNAMIC_STOCHASTIC",
    )
    states = {name: _PortfolioState(last_prices={}) for name in (*strategies, "ORACLE")}
    rows: list[dict[str, Any]] = []
    matchday_values = tuple(sorted(set(map(int, matchdays))))
    for matchday in matchday_values:
        player_pool = predictions[predictions["round_number"].eq(matchday)].copy()
        if player_pool.empty:
            continue
        market = load_historical_market(
            season, matchday, database_path=database_path
        )
        coach_pool = coach_market_for_matchday(
            coach_history, coach_model, season, matchday, market
        )
        if len(coach_pool) == 0:
            continue
        player_pool["position"] = player_pool["position"].astype(str)
        player_pool["entity_id"] = player_pool["entity_id"].astype(str)
        player_pool["credits"] = pd.to_numeric(player_pool["credits"], errors="coerce")
        coach_pool["entity_id"] = coach_pool["entity_id"].astype(str)
        coach_pool["coach_id"] = coach_pool["coach_id"].astype(str)
        current_prices = {
            **dict(zip(player_pool["entity_id"].astype(str), player_pool["credits"].astype(float))),
            **dict(zip(coach_pool["entity_id"].astype(str), coach_pool["credits"].astype(float))),
        }
        selected: dict[str, OptimizationResult] = {}
        available_budgets: dict[str, float] = {}
        trade_limits: dict[str, int | None] = {}
        for strategy in strategies:
            state = states[strategy]
            budget = _available_budget(state, current_prices, rules.budget_credits)
            trade_limit = _trade_limit(state, matchday, rules)
            available_budgets[strategy] = budget
            trade_limits[strategy] = trade_limit
            if strategy == "HIGHEST_EXPECTED_ROSTER":
                roster = optimize_deterministic(
                    player_pool, coach_pool, rules, budget=budget, roster_only=True,
                    previous_entity_ids=state.entity_ids, trade_limit=trade_limit,
                )
                selected[strategy] = optimize_roles_for_roster(
                    player_pool, coach_pool, rules, roster.lineup.player_ids,
                    roster.lineup.coach_id,
                )
            elif strategy == "FP_PER_CREDIT":
                player_pool["_fp_per_credit"] = player_pool["expected_fp"] / player_pool["credits"]
                coach_pool["_fp_per_credit"] = coach_pool["expected_score"] / coach_pool["credits"]
                roster = optimize_deterministic(
                    player_pool, coach_pool, rules, budget=budget,
                    player_objective="_fp_per_credit", coach_objective="_fp_per_credit",
                    roster_only=True, previous_entity_ids=state.entity_ids,
                    trade_limit=trade_limit,
                )
                selected[strategy] = optimize_roles_for_roster(
                    player_pool, coach_pool, rules, roster.lineup.player_ids,
                    roster.lineup.coach_id,
                )
            elif strategy == "DETERMINISTIC_EXPECTED":
                selected[strategy] = optimize_deterministic(
                    player_pool, coach_pool, rules, budget=budget,
                    previous_entity_ids=state.entity_ids, trade_limit=trade_limit,
                )
            else:
                stochastic = optimize_stochastic(
                    player_pool, coach_pool, rules, budget=budget,
                    seed=seed + matchday * 100 + (0 if strategy.startswith("STATIC") else 1),
                    previous_entity_ids=state.entity_ids, trade_limit=trade_limit,
                    training_simulations=training_simulations,
                    evaluation_simulations=evaluation_simulations,
                    dynamic=strategy == "FULL_DYNAMIC_STOCHASTIC",
                    scenario_candidates=stochastic_candidates,
                    alternative_mean_rosters=1,
                    max_layouts_per_roster=5,
                )
                selected[strategy] = stochastic.optimizer_result
        for strategy, result in selected.items():
            coach_actual = float(coach_pool.loc[
                coach_pool["coach_id"].astype(str).eq(result.lineup.coach_id), "actual_score"
            ].iloc[0])
            replay = replay_actual_round(
                result.lineup, player_pool, coach_actual, rules,
                allow_substitutions=strategy == "FULL_DYNAMIC_STOCHASTIC",
                allow_captain_switch=strategy == "FULL_DYNAMIC_STOCHASTIC",
            )
            captain_frozen = replay_actual_round(
                result.lineup, player_pool, coach_actual, rules,
                allow_substitutions=True, allow_captain_switch=False,
            ) if strategy == "FULL_DYNAMIC_STOCHASTIC" else replay
            entity_ids = entity_ids_for_result(result, player_pool, coach_pool)
            previous = states[strategy].entity_ids
            trades = len(previous - entity_ids) if previous else len(entity_ids)
            rows.append({
                "season": season, "matchday": matchday, "strategy": strategy,
                "actual_final_score": replay.final_score,
                "actual_static_score": replay.static_score,
                "actual_captain_frozen_score": captain_frozen.final_score,
                "predicted_objective": result.objective_value,
                "total_credits": result.total_credits,
                "available_budget": available_budgets[strategy],
                "bank_after": available_budgets[strategy] - result.total_credits,
                "trades": trades,
                "trade_limit": trade_limits[strategy],
                "captain_switches": replay.captain_switches,
                "substitutions": replay.substitutions,
                "captain": result.lineup.captain,
                "sixth_man": result.lineup.sixth_man,
                "starters": "|".join(sorted(result.lineup.starters)),
                "roster": "|".join(sorted(result.lineup.player_ids)),
                "coach_id": result.lineup.coach_id,
                "roster_fingerprint": result.roster_fingerprint,
                "solver_gap": result.solver_gap,
                "solver_optimal": result.optimal,
            })
            _update_state(states[strategy], entity_ids, current_prices,
                          available_budgets[strategy] - result.total_credits)

        oracle_state = states["ORACLE"]
        oracle_budget = _available_budget(oracle_state, current_prices, rules.budget_credits)
        oracle_trade_limit = _trade_limit(oracle_state, matchday, rules)
        oracle = optimize_deterministic(
            player_pool, coach_pool, rules, budget=oracle_budget,
            player_objective="actual_fp", coach_objective="actual_score",
            previous_entity_ids=oracle_state.entity_ids, trade_limit=oracle_trade_limit,
        )
        oracle_entities = entity_ids_for_result(oracle, player_pool, coach_pool)
        rows.append({
            "season": season, "matchday": matchday, "strategy": "ORACLE",
            "actual_final_score": oracle.objective_value,
            "actual_static_score": oracle.objective_value,
            "actual_captain_frozen_score": oracle.objective_value,
            "predicted_objective": oracle.objective_value,
            "total_credits": oracle.total_credits, "available_budget": oracle_budget,
            "bank_after": oracle_budget - oracle.total_credits,
            "trades": len(oracle_state.entity_ids - oracle_entities)
            if oracle_state.entity_ids else len(oracle_entities),
            "trade_limit": oracle_trade_limit, "captain_switches": 0,
            "substitutions": 0, "captain": oracle.lineup.captain,
            "sixth_man": oracle.lineup.sixth_man,
            "starters": "|".join(sorted(oracle.lineup.starters)),
            "roster": "|".join(sorted(oracle.lineup.player_ids)),
            "coach_id": oracle.lineup.coach_id,
            "roster_fingerprint": oracle.roster_fingerprint,
            "solver_gap": oracle.solver_gap, "solver_optimal": oracle.optimal,
        })
        _update_state(
            oracle_state, oracle_entities, current_prices,
            oracle_budget - oracle.total_credits,
        )
    results = pd.DataFrame(rows)
    summary = summarize_backtest(results, predictions, coach_model, rules)
    _persist_backtest(
        results, summary, coach_model, Path(output_root),
        database_path if persist_database else None, predictions, rules
    )
    return results, summary


def summarize_backtest(
    results: pd.DataFrame,
    predictions: pd.DataFrame,
    coach_model: CoachDistributionModel,
    rules: FantasyRules,
) -> BacktestSummary:
    metrics: dict[str, dict[str, float]] = {}
    for strategy, frame in results.groupby("strategy", sort=True):
        values = frame["actual_final_score"].to_numpy(float)
        metrics[strategy] = {
            "mean": float(np.mean(values)), "median": float(np.median(values)),
            "p10": float(np.quantile(values, 0.10)), "p90": float(np.quantile(values, 0.90)),
            "downside_frequency_le_120": float(np.mean(values <= 120.0)),
            "high_score_frequency_ge_180": float(np.mean(values >= 180.0)),
        }
    pivot = results.pivot(index="matchday", columns="strategy", values="actual_final_score")
    full = pivot["FULL_DYNAMIC_STOCHASTIC"]
    deterministic = pivot["DETERMINISTIC_EXPECTED"]
    static_stochastic = pivot["STATIC_STOCHASTIC"]
    full_rows = results[results["strategy"].eq("FULL_DYNAMIC_STOCHASTIC")]
    between = float(np.mean(
        full_rows["actual_final_score"] - full_rows["actual_static_score"]
    ))
    captain = float(np.mean(
        full_rows["actual_final_score"] - full_rows["actual_captain_frozen_score"]
    ))
    result_fingerprint = _frame_fingerprint(results)
    prediction_fingerprint = _frame_fingerprint(predictions[[
        "player_game_id", "expected_fp", "p10_fp", "p50_fp", "p90_fp",
        "actual_fp", "prediction_cutoff",
    ]])
    return BacktestSummary(
        backtest_version="phase7_e2025_point_in_time_replay_v1",
        season="E2025", matchdays_replayed=int(results.matchday.nunique()),
        matchday_min=int(results.matchday.min()), matchday_max=int(results.matchday.max()),
        metrics=metrics,
        full_dynamic_gain_over_deterministic=float(np.mean(full - deterministic)),
        between_turn_adaptation_gain=between,
        captain_switching_gain=captain,
        turn_diversification_gain=float(np.mean(full - static_stochastic)),
        average_regret_to_oracle=float(np.mean(pivot["ORACLE"] - full)),
        coach_validation=asdict(coach_model.validation),
        rules_version=rules.ruleset_version, rules_fingerprint=rules.fingerprint,
        predictions_fingerprint=prediction_fingerprint,
        results_fingerprint=result_fingerprint,
    )


def _available_budget(
    state: _PortfolioState, current_prices: dict[str, float], starting_budget: float
) -> float:
    if not state.entity_ids:
        return float(starting_budget)
    previous_prices = state.last_prices or {}
    liquidation = sum(
        current_prices.get(entity, previous_prices.get(entity, 0.0))
        for entity in state.entity_ids
    )
    return float(liquidation + state.bank)


def _trade_limit(
    state: _PortfolioState, matchday: int, rules: FantasyRules
) -> int | None:
    if not state.entity_ids:
        return None
    return rules.trade_limit_after(int(matchday) - 1)


def _update_state(
    state: _PortfolioState,
    entity_ids: frozenset[str],
    prices: dict[str, float],
    bank: float,
) -> None:
    state.entity_ids = entity_ids
    state.bank = float(bank)
    state.last_prices = {entity: prices[entity] for entity in entity_ids if entity in prices}


def _persist_backtest(
    results: pd.DataFrame,
    summary: BacktestSummary,
    coach_model: CoachDistributionModel,
    root: Path,
    database_path: Path | str | None,
    predictions: pd.DataFrame,
    rules: FantasyRules,
) -> None:
    research = root / "research"
    research.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.register("phase7_backtest", results)
    try:
        escaped = str((research / "e2025_strategy_backtest.parquet").resolve()).replace("'", "''")
        connection.execute(
            f"COPY phase7_backtest TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.close()
    (research / "e2025_strategy_backtest_summary.json").write_text(
        json.dumps(asdict(summary), indent=2, default=str) + "\n", encoding="utf-8"
    )
    with (research / "coach_distribution_model.pkl").open("wb") as handle:
        pickle.dump(coach_model, handle)
    if database_path is None:
        return
    oracle = results[results["strategy"].eq("ORACLE")].set_index("matchday")[
        "actual_final_score"
    ]
    with connect_database(database_path) as database:
        for row in results.itertuples(index=False):
            round_predictions = predictions[predictions["round_number"].eq(row.matchday)]
            prediction_fingerprint = _frame_fingerprint(round_predictions[[
                "player_game_id", "expected_fp", "p10_fp", "p50_fp", "p90_fp",
                "prediction_cutoff",
            ]])
            cutoff = pd.to_datetime(round_predictions["prediction_cutoff"], utc=True).min()
            row_payload = {
                key: value for key, value in row._asdict().items()
            }
            result_fingerprint = hashlib.sha256(
                json.dumps(row_payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            replay_id = hashlib.sha256(
                f"{summary.backtest_version}|{row.season}|{row.matchday}|{row.strategy}".encode()
            ).hexdigest()
            database.execute(
                """
                INSERT INTO fantasy_strategy_replay_results (
                  replay_result_id, backtest_version, season_code, fantasy_matchday,
                  strategy_name, decision_cutoff, available_budget, total_credits,
                  trades_used, trade_limit, predicted_final_score, actual_final_score,
                  oracle_final_score, captain_switches, substitutions, roster_fingerprint,
                  rules_fingerprint, prediction_fingerprint, result_fingerprint,
                  oracle_is_evaluation_only
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true)
                ON CONFLICT DO NOTHING
                """,
                [
                    replay_id, summary.backtest_version, row.season, row.matchday,
                    row.strategy, cutoff, row.available_budget, row.total_credits,
                    row.trades,
                    None if pd.isna(row.trade_limit) else int(row.trade_limit),
                    row.predicted_objective,
                    row.actual_final_score, float(oracle.loc[row.matchday]),
                    row.captain_switches, row.substitutions, row.roster_fingerprint,
                    rules.fingerprint, prediction_fingerprint, result_fingerprint,
                ],
            )


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    columns = sorted(frame.columns)
    for row in frame[columns].astype(str).sort_values(columns).itertuples(index=False, name=None):
        digest.update(json.dumps(row, separators=(",", ":")).encode()); digest.update(b"\n")
    return digest.hexdigest()
