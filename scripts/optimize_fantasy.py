#!/usr/bin/env python3
"""Select a legal Fantasy roster or optimize legal actions after a completed Turn."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any

import pandas as pd

from src.data.normalizers import display_person_name
from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.db.ids import stable_id
from src.live.freshness import live_status
from src.live.rules_compatibility import resolve_ruleset_compatibility
from src.live.scoring_rules import resolve_scoring_rule_compatibility
from src.strategy.backtest import DEFAULT_PHASE7_ROOT
from src.strategy.coach import (
    build_coach_feature_history,
    coach_market_for_matchday,
    coach_market_for_upcoming,
    train_coach_model,
)
from src.strategy.engine import optimize_stochastic
from src.strategy.historical import load_historical_market, read_cached_predictions
from src.strategy.optimizer import optimize_deterministic
from src.strategy.rules import (
    Lineup,
    RuleViolation,
    load_rules,
    register_rules_manifest,
)
from src.strategy.simulation import recommend_next_turn


PREDICTIVE_VERSION = "phase6c_predictive_uplift_frozen_v1"
OPTIMIZER_VERSION = "phase7_dynamic_strategy_engine_v1"
SIMULATION_VERSION = "phase7_piecewise_inverse_cdf_mc_v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True)
    parser.add_argument("--matchday", required=True, type=int)
    parser.add_argument("--budget", type=float, default=100.0)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--mode", choices=("dynamic", "deterministic"), default="dynamic"
    )
    parser.add_argument("--seed", type=int, default=20250701)
    parser.add_argument("--training-simulations", type=int, default=256)
    parser.add_argument("--simulations", type=int, default=2000)
    parser.add_argument("--scenario-id")
    parser.add_argument("--after-turn-results", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument(
        "--output-root", type=Path,
        default=DEFAULT_PHASE7_ROOT / "optimization_snapshots",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rules = load_rules()
    register_rules_manifest(rules, database_path=args.database)
    if args.season != "E2025":
        rules_compatibility = resolve_ruleset_compatibility(
            rules, args.season, live_status(args.season, args.database),
        )
        if not rules_compatibility.production_ready:
            print(json.dumps({
                "status": "BLOCKED_RULES", "season": args.season,
                "matchday": args.matchday, "error": rules_compatibility.reason,
                "ruleset_version": rules.ruleset_version,
                "rules_fingerprint": rules.fingerprint,
            }, indent=2))
            return 2
        scoring = resolve_scoring_rule_compatibility(args.season)
        if not scoring.production_ready:
            print(json.dumps({
                "status": "BLOCKED_RULES", "season": args.season,
                "matchday": args.matchday, "error": scoring.reason,
                "scoring_compatibility": scoring.state,
                "scoring_evidence_fingerprint": scoring.evidence_fingerprint,
            }, indent=2))
            return 2
    players, coaches = _load_pools(
        args.season, args.matchday, args.database, args.scenario_id
    )
    if args.after_turn_results is not None:
        if args.snapshot is None:
            raise SystemExit("--snapshot is required with --after-turn-results")
        payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
        observed = json.loads(args.after_turn_results.read_text(encoding="utf-8"))
        lineup = _lineup_from_payload(payload["lineup"])
        next_lineup, actions = recommend_next_turn(
            lineup, players, observed.get("player_scores", {}),
            int(observed["completed_turn"]), rules,
        )
        output = {
            "status": "SUCCEEDED", "decision_stage": "BETWEEN_TURNS",
            "season": args.season, "matchday": args.matchday,
            "completed_turn": int(observed["completed_turn"]),
            "ruleset_version": rules.ruleset_version,
            "lineup": _lineup_payload(next_lineup),
            "recommended_actions": list(actions),
            "decision_principle": (
                "Actions maximize expected final score using realized completed-Turn "
                "scores and frozen expectations for players who have not played."
            ),
        }
        path = _write_snapshot(output, args.output_root)
        output["snapshot_path"] = str(path)
        print(json.dumps(output, indent=2, default=str))
        return 0

    if args.mode == "deterministic":
        result = optimize_deterministic(players, coaches, rules, budget=args.budget)
        simulation = None
        diagnostics: dict[str, Any] = {}
        method = "DETERMINISTIC_EXPECTED"
    else:
        stochastic = optimize_stochastic(
            players, coaches, rules, budget=args.budget, seed=args.seed,
            training_simulations=args.training_simulations,
            evaluation_simulations=args.simulations, dynamic=True,
        )
        result = stochastic.optimizer_result
        simulation = stochastic.simulation
        diagnostics = dict(stochastic.simulation_diagnostics)
        method = stochastic.method
    output = _pre_t1_output(
        args, players, coaches, rules, result.lineup, result.total_credits,
        result.solver_status, result.solver_gap, result.optimal,
        method, simulation, diagnostics,
    )
    path = _write_snapshot(output, args.output_root)
    output["snapshot_path"] = str(path)
    _persist_run(output, args.database, path)
    print(json.dumps(output, indent=2, default=str))
    return 0


def _load_pools(
    season: str, matchday: int, database: Path, scenario_id: str | None,
    prediction_run_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if season == "E2025":
        players = read_cached_predictions(season)
        players = players[players["round_number"].eq(int(matchday))].copy()
        market = load_historical_market(season, matchday, database_path=database)
    else:
        players, market = _load_live_pools(
            season, matchday, database, scenario_id, prediction_run_id
        )
    history = build_coach_feature_history(database)
    model_path = DEFAULT_PHASE7_ROOT / "research" / "coach_distribution_model.pkl"
    if model_path.is_file():
        with model_path.open("rb") as handle:
            coach_model = pickle.load(handle)
    else:
        coach_model = train_coach_model(history)
    coaches = (
        coach_market_for_matchday(history, coach_model, season, matchday, market)
        if season == "E2025" else
        coach_market_for_upcoming(
            coach_model, season, matchday, market, database_path=database
        )
    )
    return players, coaches


def _load_live_pools(
    season: str, matchday: int, database: Path, scenario_id: str | None,
    prediction_run_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    with connect_database(database, read_only=True) as connection:
        run_filter = " AND prediction_run_id=?" if prediction_run_id else ""
        parameters: list[Any] = [season, int(matchday)]
        if prediction_run_id:
            parameters.append(str(prediction_run_id))
        run = connection.execute(
            f"""
            SELECT prediction_run_id FROM live_prediction_runs
            WHERE season_code=? AND season_matchday=?
              AND status IN ('SUCCEEDED','PARTIAL')
              AND predictive_model_version='phase6c_predictive_uplift_frozen_v1'
              AND scoring_rules_compatibility='SCORING_RULES_COMPATIBLE'
              AND EXISTS (
                  SELECT 1 FROM live_player_predictions AS scored
                  WHERE scored.prediction_run_id=live_prediction_runs.prediction_run_id
                    AND scored.prediction_status='SCORED'
              )
              {run_filter}
            ORDER BY prediction_generated_at DESC, prediction_run_id DESC LIMIT 1
            """,
            parameters,
        ).fetchone()
        if run is None:
            suffix = f" {prediction_run_id}" if prediction_run_id else ""
            raise RuleViolation(f"no compatible frozen Phase 6C live prediction run{suffix}")
        scenarios = connection.execute(
            "SELECT DISTINCT scenario_id FROM live_player_predictions "
            "WHERE prediction_run_id=? ORDER BY scenario_id",
            [run[0]],
        ).fetchall()
        selected_scenario = scenario_id or (str(scenarios[0][0]) if len(scenarios) == 1 else None)
        if selected_scenario is None:
            raise RuleViolation("multiple availability scenarios exist; supply --scenario-id")
        players = connection.execute(
            """
            SELECT prediction.canonical_player_id AS player_id,
                   prediction.fantasy_entity_id AS entity_id,
                   prediction.player_name AS name,
                   prediction.canonical_team_id AS team_id,
                   prediction.canonical_game_id AS game_id,
                   prediction.fantasy_position AS position,
                   prediction.credits,
                   prediction.expected_fp,
                   prediction.p10_fp, prediction.p25_fp, prediction.p50_fp,
                   prediction.p75_fp, prediction.p90_fp, prediction.p95_fp,
                   prediction.prob_fp_ge_20, prediction.prob_fp_ge_25,
                   prediction.prob_fp_ge_30, prediction.prob_fp_ge_35,
                   prediction.prob_fp_ge_40, prediction.prob_fp_le_5,
                   prediction.prob_fp_le_10, prediction.prob_fp_le_15,
                   prediction.predictive_artifact_fingerprint,
                   games.local_game_date
            FROM live_player_predictions AS prediction
            JOIN games ON games.canonical_game_id=prediction.canonical_game_id
            WHERE prediction.prediction_run_id=? AND prediction.scenario_id=?
              AND prediction.prediction_status='SCORED'
              AND prediction.resolved_availability<>'OUT'
            ORDER BY prediction.scheduled_tip_time, prediction.canonical_player_id
            """,
            [run[0], selected_scenario],
        ).df()
        market = _load_live_market(connection, season, matchday)
    dates = sorted(pd.to_datetime(players["local_game_date"]).dt.date.unique())
    players["turn"] = pd.to_datetime(players["local_game_date"]).dt.date.map(
        {value: index + 1 for index, value in enumerate(dates)}
    )
    return players, market


def _load_live_market(connection: Any, season: str, matchday: int) -> pd.DataFrame:
    market = connection.execute(
        """
            WITH latest_official_coach AS (
              SELECT canonical_team_id, coach_name FROM (
                SELECT stat.canonical_team_id, stat.coach_name,
                       row_number() OVER (
                         PARTITION BY stat.canonical_team_id
                         ORDER BY game.game_date DESC NULLS LAST,
                                  game.game_code DESC, stat.team_game_id DESC
                       ) AS priority
                FROM team_game_stats AS stat
                JOIN current_games AS game USING(canonical_game_id)
                WHERE game.season_code=? AND game.played
                  AND nullif(trim(stat.coach_name), '') IS NOT NULL
              ) WHERE priority=1
            )
            SELECT snapshot.fantasy_entity_id AS entity_id, entity.entity_type,
                   entity.display_name AS name, snapshot.canonical_team_id AS team_id,
                   snapshot.position_name AS position, snapshot.credits,
                   snapshot.fantasy_entity_id AS coach_id,
                   latest_official_coach.coach_name AS official_coach_name
            FROM fantasy_market_snapshots AS snapshot
            JOIN fantasy_entities AS entity USING(fantasy_entity_id)
            LEFT JOIN latest_official_coach
              ON latest_official_coach.canonical_team_id=snapshot.canonical_team_id
            WHERE snapshot.season_code=? AND snapshot.matchday_number=?
            QUALIFY row_number() OVER (
              PARTITION BY snapshot.fantasy_entity_id
              ORDER BY snapshot.observed_at DESC, snapshot.snapshot_record_id DESC
            )=1
            """,
        [season, season, int(matchday)],
    ).df()
    coach_rows = market["entity_type"].eq("COACH") & market["official_coach_name"].notna()
    market.loc[coach_rows, "name"] = market.loc[
        coach_rows, "official_coach_name"
    ].map(display_person_name)
    market = market.drop(columns=["official_coach_name"])
    return market


def _pre_t1_output(
    args: argparse.Namespace,
    players: pd.DataFrame,
    coaches: pd.DataFrame,
    rules: Any,
    lineup: Lineup,
    total_credits: float,
    solver_status: str,
    solver_gap: float | None,
    optimal: bool,
    method: str,
    simulation: Any,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    roster = players[players["player_id"].astype(str).isin(lineup.player_ids)].copy()
    roster.index = roster["player_id"].astype(str)
    records = []
    last_turn = int(roster["turn"].max())
    for player_id in lineup.player_ids:
        row = roster.loc[player_id]
        if player_id == lineup.sixth_man:
            role = "SIXTH_MAN"
            reason = "Designated full-score bench player."
        elif player_id in lineup.starters:
            role = "STARTER"
            reason = (
                "Early-Turn option with a later legal replacement."
                if int(row["turn"]) < last_turn else "Selected in the optimal legal formation."
            )
        else:
            role = "BENCH"
            reason = (
                "Later replacement preserves between-Turn option value."
                if int(row["turn"]) > 1 else "Half-score depth compatible with the legal roster."
            )
        if player_id == lineup.captain:
            role += "+CAPTAIN"
            reason += (
                " Captain value was selected by expected final-score simulation."
                if method != "DETERMINISTIC_EXPECTED" else
                " Captain value was selected by the exact expected-FP objective."
            )
        records.append({
            "player_id": player_id, "name": row["name"], "team_id": row["team_id"],
            "position": row["position"], "credits": float(row["credits"]),
            "turn": int(row["turn"]), "role": role,
            "expected_fp": float(row["expected_fp"]), "p10": float(row["p10_fp"]),
            "p50": float(row["p50_fp"]), "p90": float(row["p90_fp"]),
            "downside": {
                "le_5": float(row["prob_fp_le_5"]),
                "le_10": float(row["prob_fp_le_10"]),
                "le_15": float(row["prob_fp_le_15"]),
            },
            "upside": {
                "ge_20": float(row["prob_fp_ge_20"]),
                "ge_25": float(row["prob_fp_ge_25"]),
                "ge_30": float(row["prob_fp_ge_30"]),
                "ge_35": float(row["prob_fp_ge_35"]),
                "ge_40": float(row["prob_fp_ge_40"]),
            },
            "reason": reason,
        })
    coach = coaches[coaches["coach_id"].astype(str).eq(lineup.coach_id)].iloc[0]
    return {
        "status": "SUCCEEDED", "decision_stage": "PRE_T1",
        "season": args.season, "matchday": args.matchday, "method": method,
        "generated_at": datetime.now(UTC).isoformat(),
        "ruleset_version": rules.ruleset_version,
        "rules_fingerprint": rules.fingerprint,
        "predictive_model_version": PREDICTIVE_VERSION,
        "predictive_artifact_fingerprint": str(
            roster["predictive_artifact_fingerprint"].iloc[0]
        ),
        "optimizer_version": OPTIMIZER_VERSION,
        "simulation_version": SIMULATION_VERSION,
        "simulation_seed": args.seed,
        "simulation_count": args.simulations if simulation is not None else 0,
        "budget": args.budget, "total_credits": total_credits,
        "solver": {"status": solver_status, "gap": solver_gap, "optimal": optimal},
        "lineup": _lineup_payload(lineup),
        "players": records,
        "coach": {
            "coach_id": lineup.coach_id, "name": coach["name"],
            "team_id": coach["team_id"], "credits": float(coach["credits"]),
            "turn": int(coach["turn"]),
            "expected_score": float(coach["expected_score"]),
        },
        "simulated_final_score": (
            {
                "mean": simulation.expected_final_score,
                "p10": simulation.p10_final_score,
                "p50": simulation.median_final_score,
                "p90": simulation.p90_final_score,
                "downside_frequency": simulation.downside_frequency,
                "high_score_frequency": simulation.high_score_frequency,
            } if simulation is not None else None
        ),
        "simulation_diagnostics": diagnostics,
        "recommended_between_turn_rule": (
            "After each Turn, rerun with realized scores. Keep a completed score or "
            "replace it only when the legal action has greater expected final value; "
            "captain switching uses the same comparison and no fixed FP threshold."
        ),
    }


def _lineup_payload(lineup: Lineup) -> dict[str, Any]:
    return {
        "player_ids": list(lineup.player_ids), "coach_id": lineup.coach_id,
        "starters": sorted(lineup.starters), "sixth_man": lineup.sixth_man,
        "bench": sorted(lineup.bench), "captain": lineup.captain,
    }


def _lineup_from_payload(payload: dict[str, Any]) -> Lineup:
    return Lineup(
        tuple(map(str, payload["player_ids"])), str(payload["coach_id"]),
        frozenset(map(str, payload["starters"])), str(payload["sixth_man"]),
        str(payload["captain"]),
    )


def _write_snapshot(payload: dict[str, Any], root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    fingerprint = _reproducible_payload_fingerprint(payload)
    payload["snapshot_fingerprint"] = fingerprint
    run_id = stable_id("phase7_strategy_snapshot", fingerprint)
    path = root / f"{run_id}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if _reproducible_payload_fingerprint(existing) != fingerprint:
            raise ValueError(f"immutable strategy snapshot conflict: {path}")
        if "generated_at" in existing:
            payload["generated_at"] = existing["generated_at"]
        return path
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _persist_run(payload: dict[str, Any], database: Path, path: Path) -> None:
    fingerprint = _reproducible_payload_fingerprint(payload)
    run_id = stable_id("phase7_strategy_run", fingerprint)
    simulated = payload.get("simulated_final_score") or {}
    with connect_database(database) as connection:
        connection.execute(
            """
            INSERT INTO fantasy_strategy_runs (
              strategy_run_id, season_code, fantasy_matchday, decision_stage, status,
              ruleset_version, rules_fingerprint, predictive_model_version,
              predictive_artifact_fingerprint, optimizer_version, simulation_version,
              simulation_seed, simulation_count, budget, total_credits, solver_status,
              solver_gap, expected_final_score, p10_final_score, p50_final_score,
              p90_final_score, input_fingerprint, run_fingerprint,
              observed_results_json, selected_roster_json, recommended_actions_json,
              simulation_diagnostics_json, output_json_path
            ) VALUES (?, ?, ?, 'PRE_T1', 'SUCCEEDED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, '{}', ?, '[]', ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                run_id, payload["season"], payload["matchday"],
                payload["ruleset_version"], payload["rules_fingerprint"],
                payload["predictive_model_version"],
                payload["predictive_artifact_fingerprint"], payload["optimizer_version"],
                payload["simulation_version"], payload["simulation_seed"],
                payload["simulation_count"], payload["budget"], payload["total_credits"],
                payload["solver"]["status"], payload["solver"]["gap"],
                simulated.get("mean"), simulated.get("p10"), simulated.get("p50"),
                simulated.get("p90"), fingerprint, fingerprint,
                json.dumps(payload["lineup"]), json.dumps(payload["simulation_diagnostics"]),
                str(path),
            ],
        )


def _reproducible_payload_fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        key: value for key, value in payload.items()
        if key not in {"generated_at", "snapshot_path", "snapshot_fingerprint"}
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
