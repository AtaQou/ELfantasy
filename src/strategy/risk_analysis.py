"""Held-out tail/option-value analyses for the Phase 7 strategy decision."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH
from src.strategy.backtest import (
    DEFAULT_PHASE7_ROOT,
    _PortfolioState,
    _available_budget,
    _trade_limit,
    _update_state,
)
from src.strategy.coach import (
    build_coach_feature_history,
    coach_market_for_matchday,
    train_coach_model,
)
from src.strategy.engine import optimize_stochastic, replay_actual_round
from src.strategy.historical import load_historical_market, read_cached_predictions
from src.strategy.optimizer import entity_ids_for_result
from src.strategy.rules import load_rules


@dataclass(frozen=True, slots=True)
class RiskAnalysis:
    full_dynamic_mean: float
    no_downside_probability_mean: float
    downside_probability_increment: float
    no_upside_probability_mean: float
    upside_probability_increment: float
    full_captain_switches: int
    no_upside_captain_switches: int
    full_substitutions: int
    no_downside_substitutions: int
    strong_later_replacement_adaptation_gain: float
    weak_later_replacement_adaptation_gain: float
    later_replacement_option_value_difference: float
    t1_starter_average_width: float
    final_turn_starter_average_width: float
    risky_t1_share_with_later_replacement: float


def run_tail_probability_ablations(
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    training_simulations: int = 48,
    evaluation_simulations: int = 256,
    scenario_candidates: int = 4,
    seed: int = 20250701,
    output_root: Path | str = DEFAULT_PHASE7_ROOT,
) -> RiskAnalysis:
    rules = load_rules()
    predictions = read_cached_predictions("E2025")
    history = build_coach_feature_history(database_path)
    coach_model = train_coach_model(history)
    states = {
        "NO_DOWNSIDE": _PortfolioState(last_prices={}),
        "NO_UPSIDE": _PortfolioState(last_prices={}),
    }
    rows: list[dict[str, Any]] = []
    for matchday in range(1, 39):
        players = predictions[predictions["round_number"].eq(matchday)].copy()
        market = load_historical_market(
            "E2025", matchday, database_path=database_path
        )
        coaches = coach_market_for_matchday(
            history, coach_model, "E2025", matchday, market
        )
        prices = {
            **dict(zip(players.entity_id.astype(str), players.credits.astype(float))),
            **dict(zip(coaches.entity_id.astype(str), coaches.credits.astype(float))),
        }
        for name, include_upside, include_downside in (
            ("NO_DOWNSIDE", True, False), ("NO_UPSIDE", False, True),
        ):
            state = states[name]
            budget = _available_budget(state, prices, rules.budget_credits)
            trade_limit = _trade_limit(state, matchday, rules)
            result = optimize_stochastic(
                players, coaches, rules, budget=budget,
                seed=seed + matchday * 100 + 1,
                previous_entity_ids=state.entity_ids, trade_limit=trade_limit,
                training_simulations=training_simulations,
                evaluation_simulations=evaluation_simulations,
                scenario_candidates=scenario_candidates,
                alternative_mean_rosters=1, max_layouts_per_roster=5,
                include_upside=include_upside, include_downside=include_downside,
            )
            lineup = result.optimizer_result.lineup
            coach_actual = float(coaches.loc[
                coaches.coach_id.astype(str).eq(lineup.coach_id), "actual_score"
            ].iloc[0])
            replay = replay_actual_round(lineup, players, coach_actual, rules)
            entities = entity_ids_for_result(result.optimizer_result, players, coaches)
            rows.append({
                "matchday": matchday, "strategy": name,
                "actual_final_score": replay.final_score,
                "captain_switches": replay.captain_switches,
                "substitutions": replay.substitutions,
                "roster": "|".join(sorted(lineup.player_ids)),
                "starters": "|".join(sorted(lineup.starters)),
                "sixth_man": lineup.sixth_man,
            })
            _update_state(state, entities, prices, budget - result.optimizer_result.total_credits)
    ablations = pd.DataFrame(rows)
    root = Path(output_root) / "research"
    backtest = _read_parquet(root / "e2025_strategy_backtest.parquet")
    analysis = summarize_risk_analysis(backtest, ablations, predictions)
    _write_parquet(ablations, root / "e2025_tail_probability_ablations.parquet")
    (root / "e2025_risk_analysis.json").write_text(
        json.dumps(asdict(analysis), indent=2) + "\n", encoding="utf-8"
    )
    return analysis


def summarize_risk_analysis(
    backtest: pd.DataFrame,
    ablations: pd.DataFrame,
    predictions: pd.DataFrame,
) -> RiskAnalysis:
    full = backtest[backtest.strategy.eq("FULL_DYNAMIC_STOCHASTIC")].copy()
    no_down = ablations[ablations.strategy.eq("NO_DOWNSIDE")]
    no_up = ablations[ablations.strategy.eq("NO_UPSIDE")]
    player_index = predictions.set_index(["round_number", "player_id"])
    option_rows: list[dict[str, float]] = []
    widths: list[tuple[str, float]] = []
    risky_with_option = 0
    risky_t1 = 0
    global_width_median = float(predictions["distribution_width"].median())
    for row in full.itertuples(index=False):
        roster_ids = str(row.roster).split("|")
        starter_ids = set(str(row.starters).split("|"))
        roster = predictions[
            predictions.round_number.eq(row.matchday)
            & predictions.player_id.astype(str).isin(roster_ids)
        ].copy()
        max_turn = int(roster.turn.max())
        for player in starter_ids:
            item = player_index.loc[(row.matchday, player)]
            label = "T1" if int(item.turn) == 1 else "FINAL" if int(item.turn) == max_turn else "MIDDLE"
            widths.append((label, float(item.distribution_width)))
            if int(item.turn) != 1 or float(item.distribution_width) < global_width_median:
                continue
            risky_t1 += 1
            alternatives = roster[
                ~roster.player_id.astype(str).isin(starter_ids)
                & roster.turn.gt(1) & roster.position.eq(item.position)
                & ~roster.player_id.astype(str).eq(str(row.sixth_man))
            ]
            risky_with_option += int(not alternatives.empty)
        later = roster[
            ~roster.player_id.astype(str).isin(starter_ids)
            & roster.turn.gt(1)
            & ~roster.player_id.astype(str).eq(str(row.sixth_man))
        ]
        option_rows.append({
            "later_strength": float(later.expected_fp.max()) if not later.empty else 0.0,
            "adaptation_gain": float(row.actual_final_score - row.actual_static_score),
        })
    option = pd.DataFrame(option_rows)
    median_strength = option.later_strength.median()
    strong = float(option.loc[option.later_strength.ge(median_strength), "adaptation_gain"].mean())
    weak = float(option.loc[option.later_strength.lt(median_strength), "adaptation_gain"].mean())
    width_frame = pd.DataFrame(widths, columns=["turn_band", "width"])
    return RiskAnalysis(
        full_dynamic_mean=float(full.actual_final_score.mean()),
        no_downside_probability_mean=float(no_down.actual_final_score.mean()),
        downside_probability_increment=float(
            full.actual_final_score.mean() - no_down.actual_final_score.mean()
        ),
        no_upside_probability_mean=float(no_up.actual_final_score.mean()),
        upside_probability_increment=float(
            full.actual_final_score.mean() - no_up.actual_final_score.mean()
        ),
        full_captain_switches=int(full.captain_switches.sum()),
        no_upside_captain_switches=int(no_up.captain_switches.sum()),
        full_substitutions=int(full.substitutions.sum()),
        no_downside_substitutions=int(no_down.substitutions.sum()),
        strong_later_replacement_adaptation_gain=strong,
        weak_later_replacement_adaptation_gain=weak,
        later_replacement_option_value_difference=strong - weak,
        t1_starter_average_width=float(
            width_frame.loc[width_frame.turn_band.eq("T1"), "width"].mean()
        ),
        final_turn_starter_average_width=float(
            width_frame.loc[width_frame.turn_band.eq("FINAL"), "width"].mean()
        ),
        risky_t1_share_with_later_replacement=float(risky_with_option / max(risky_t1, 1)),
    )


def _read_parquet(path: Path) -> pd.DataFrame:
    with duckdb.connect() as connection:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    with duckdb.connect() as connection:
        connection.register("phase7_risk", frame)
        escaped = str(path.resolve()).replace("'", "''")
        connection.execute(
            f"COPY phase7_risk TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
