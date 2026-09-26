"""Immutable shadow outcome attachment and prospective evaluation for Phase 8B."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.live.evaluation import evaluate_prediction_run
from src.strategy.engine import replay_actual_round
from src.strategy.optimizer import optimize_deterministic
from src.strategy.rules import FantasyRules, Lineup, RuleViolation, coach_fantasy_score

from .repository import ControlCenterRepository


def attach_completed_turn_results(
    repository: ControlCenterRepository,
    shadow_snapshot_id: str,
    *,
    current_lineup: Mapping[str, Any] | None = None,
    attached_at: datetime | None = None,
) -> dict[str, Any]:
    """Attach only scores from the longest contiguous sequence of completed Turns."""

    snapshot = repository.shadow_snapshot(shadow_snapshot_id)
    if snapshot is None:
        raise ValueError(f"unknown shadow snapshot: {shadow_snapshot_id}")
    now = attached_at or datetime.now(UTC)
    evaluate_prediction_run(
        snapshot["prediction_run_id"], database_path=repository.database_path,
        attach_outcomes=True, attached_at=now,
    )
    recommendation = recommendation_for_lineup(snapshot, current_lineup)
    selected = recommendation["players"]
    player_ids = [str(row["player_id"]) for row in selected]
    placeholders = ",".join("?" for _ in player_ids)
    with connect_database(repository.database_path, read_only=True) as connection:
        frame = connection.execute(
            f"""
            SELECT prediction.canonical_player_id AS player_id,
                   prediction.canonical_game_id AS game_id,
                   game.played, game.game_date, game.local_game_date,
                   outcome.actual_fantasy_points,
                   outcome.actual_minutes,
                   run.source_live_run_id
            FROM live_player_predictions AS prediction
            JOIN live_prediction_runs AS run USING(prediction_run_id)
            JOIN current_games AS game ON game.canonical_game_id=prediction.canonical_game_id
            LEFT JOIN live_prediction_outcomes AS outcome
              ON outcome.live_prediction_id=prediction.live_prediction_id
            WHERE prediction.prediction_run_id=? AND prediction.scenario_id=?
              AND prediction.canonical_player_id IN ({placeholders})
            """,
            [snapshot["prediction_run_id"], snapshot["scenario_id"], *player_ids],
        ).df()
    lookup = {str(row.player_id): row for row in frame.itertuples(index=False)}
    turn_games: dict[int, set[str]] = {}
    for row in selected:
        turn_games.setdefault(int(row["turn"]), set()).add(str(row.get("game_id") or ""))
    # Some Phase 8A presentation records predate game_id persistence; recover it by player.
    for row in selected:
        player = lookup.get(str(row["player_id"]))
        if player is not None:
            turn_games.setdefault(int(row["turn"]), set()).add(str(player.game_id))
            turn_games[int(row["turn"])].discard("")
    completed_turn = 0
    completed_games: list[dict[str, Any]] = []
    for turn in sorted(turn_games):
        games = turn_games[turn]
        if not games:
            break
        records = frame[frame["game_id"].astype(str).isin(games)]
        played_by_game = records.groupby("game_id")["played"].max().to_dict()
        if not all(bool(played_by_game.get(game, False)) for game in games):
            break
        completed_turn = turn
        completed_games.extend({"turn": turn, "game_id": game, "played": True}
                               for game in sorted(games))
    if completed_turn == 0:
        return {"status": "NO_COMPLETED_TURN", "shadow_snapshot_id": shadow_snapshot_id}

    observed: dict[str, float] = {}
    provenance: dict[str, str] = {}
    for row in selected:
        if int(row["turn"]) > completed_turn:
            continue
        player_id = str(row["player_id"])
        source = lookup.get(player_id)
        if source is None or not bool(source.played):
            raise ValueError(f"completed-Turn source row is missing for {player_id}")
        if pd.notna(source.actual_fantasy_points):
            observed[player_id] = float(source.actual_fantasy_points)
            provenance[player_id] = "CANONICAL_VERIFIED_TARGET"
        else:
            observed[player_id] = 0.0
            provenance[player_id] = "DNP_ZERO_AFTER_COMPLETED_GAME"
    coach_record = dict(recommendation.get("coach", {}))
    coach_input = next((row for row in snapshot["knowledge"]["decision_inputs"].get("coaches", [])
                        if str(row.get("coach_id")) == str(coach_record.get("coach_id"))), {})
    coach_record.update(coach_input)
    coach_score = _selected_coach_actual(
        coach_record, repository.database_path,
        maximum_turn=completed_turn,
    )
    payload = {"players": observed, "coach": coach_score, "provenance": provenance}
    source_live_run = next((str(value) for value in frame["source_live_run_id"].dropna()), None)
    attached = repository.persist_turn_outcome(
        shadow_snapshot_id, completed_turn, payload, completed_games,
        prediction_run_id=snapshot["prediction_run_id"],
        source_live_run_id=source_live_run, attached_at=now,
    )
    return {"status": "ATTACHED", **attached}


def evaluate_shadow_snapshot(
    repository: ControlCenterRepository,
    shadow_snapshot_id: str,
    rules: FantasyRules,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate immutable predictions and strategy after a Matchday is complete."""

    snapshot = repository.shadow_snapshot(shadow_snapshot_id)
    if snapshot is None:
        raise ValueError(f"unknown shadow snapshot: {shadow_snapshot_id}")
    with connect_database(repository.database_path, read_only=True) as connection:
        frame = connection.execute(
            """
            SELECT prediction.canonical_player_id AS player_id,
                   prediction.expected_fp, prediction.adjusted_expected_minutes AS expected_minutes,
                   prediction.p10_fp, prediction.p50_fp, prediction.p90_fp, prediction.p95_fp,
                   prediction.prob_fp_ge_20, prediction.prob_fp_ge_25,
                   prediction.prob_fp_ge_30, prediction.prob_fp_ge_35,
                   prediction.prob_fp_ge_40, prediction.prob_fp_le_5,
                   prediction.prob_fp_le_10, prediction.prob_fp_le_15,
                   outcome.actual_fantasy_points AS actual_fp,
                   outcome.actual_minutes
            FROM live_player_predictions AS prediction
            JOIN live_prediction_outcomes AS outcome USING(live_prediction_id)
            WHERE prediction.prediction_run_id=? AND prediction.scenario_id=?
              AND prediction.prediction_status='SCORED'
            """, [snapshot["prediction_run_id"], snapshot["scenario_id"]],
        ).df()
    played = frame[pd.to_numeric(frame["actual_minutes"], errors="coerce").gt(0)].copy()
    player_metrics = _player_metrics(played)
    latest = repository.latest_turn_outcome(shadow_snapshot_id)
    recommendation = _best_recommendation(snapshot)
    last_turn = max(int(row["turn"]) for row in recommendation["players"])
    strategy_metrics: dict[str, Any] = {"status": "MATCHDAY_INCOMPLETE"}
    if latest and int(latest["completed_turn"]) >= last_turn:
        actual = {str(key): float(value) for key, value in latest["observed_scores"]["players"].items()}
        coach_actual = latest["observed_scores"].get("coach")
        if coach_actual is not None:
            lineup = lineup_from_recommendation(recommendation)
            roster = pd.DataFrame(snapshot["knowledge"]["decision_inputs"]["players"])
            roster = roster[roster["player_id"].astype(str).isin(lineup.player_ids)].copy()
            roster["actual_fp"] = roster["player_id"].astype(str).map(actual)
            replay = replay_actual_round(lineup, roster, float(coach_actual), rules)
            expected = float(recommendation["expected_final_score"])
            strategy_metrics = {
                "status": "EVALUATED", "pre_matchday_recommended_score": replay.static_score,
                "actual_realized_score": replay.static_score,
                "dynamic_between_turn_score": replay.final_score,
                "simulated_expected_final_score": expected,
                "simulation_error": replay.static_score - expected,
                "captain_switches": replay.captain_switches,
                "substitutions": replay.substitutions,
                "captain_decisions": [item for item in replay.actions
                                      if item["action"] == "CHANGE_CAPTAIN"],
                "switch_decisions": [item for item in replay.actions
                                     if item["action"] != "CHANGE_CAPTAIN"],
                "recommended_transfers": int(recommendation.get("transfers_required", 0)),
                "hindsight_oracle_score": None, "regret_vs_hindsight_oracle": None,
                "oracle_status": "UNAVAILABLE_UNLESS_FULL_POOL_OUTCOMES_ARE_VERIFIED",
            }
            oracle = _hindsight_oracle(snapshot, rules, repository.database_path)
            if oracle is not None:
                strategy_metrics.update({
                    "hindsight_oracle_score": oracle,
                    "regret_vs_hindsight_oracle": float(oracle - replay.final_score),
                    "oracle_status": "EVALUATION_ONLY_NEVER_ACTIONABLE",
                })
    evaluation_id = repository.persist_shadow_evaluation(
        shadow_snapshot_id, player_metrics, strategy_metrics,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )
    return {"shadow_evaluation_id": evaluation_id, "player_model": player_metrics,
            "strategy": strategy_metrics}


def lineup_from_recommendation(recommendation: Mapping[str, Any]) -> Lineup:
    return Lineup(
        tuple(str(row["player_id"]) for row in recommendation["players"]),
        str(recommendation["coach"]["coach_id"]),
        frozenset(map(str, recommendation["starting_five"])),
        str(recommendation["sixth_man"]), str(recommendation["captain"]),
    )


def _best_recommendation(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return next(
        (dict(row) for row in snapshot["recommendations"] if row.get("label") == "BEST_OVERALL"),
        dict(snapshot["recommendations"][0]) if snapshot["recommendations"] else {},
    )


def recommendation_for_lineup(
    snapshot: Mapping[str, Any], current_lineup: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the frozen recommendation that owns the user's active roster."""

    if not current_lineup:
        return _best_recommendation(snapshot)
    player_ids = set(map(str, current_lineup.get("player_ids", [])))
    coach_id = str(current_lineup.get("coach_id", ""))
    sixth_man = str(current_lineup.get("sixth_man", ""))
    for row in snapshot.get("recommendations", []):
        recommendation = dict(row)
        lineup = lineup_from_recommendation(recommendation)
        if (set(lineup.player_ids) == player_ids
                and lineup.coach_id == coach_id
                and lineup.sixth_man == sixth_man):
            return recommendation
    raise ValueError("current lineup does not match a recommendation in this shadow snapshot")


def _selected_coach_actual(
    coach: Mapping[str, Any], database_path: Path | str, *, maximum_turn: int,
) -> float | None:
    if not coach or int(coach.get("turn", 10**6)) > maximum_turn:
        return None
    game_id = coach.get("game_id")
    if not game_id:
        return None
    with connect_database(database_path, read_only=True) as connection:
        row = connection.execute(
            """SELECT played, home_team_id, away_team_id, home_score, away_score,
                      coalesce(overtime_count, 0) > 0
               FROM current_games WHERE canonical_game_id=?""", [game_id],
        ).fetchone()
    if row is None or not bool(row[0]) or row[3] is None or row[4] is None:
        return None
    team = str(coach.get("team_id"))
    return coach_fantasy_score(int(row[3]), int(row[4]), home=team == str(row[1]), overtime=bool(row[5]))


def _player_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "status": "NO_CONDITIONAL_PLAYING_OUTCOMES"}
    actual = frame["actual_fp"].to_numpy(float)
    expected = frame["expected_fp"].to_numpy(float)
    metrics: dict[str, Any] = {
        "rows": len(frame), "mae": float(np.mean(np.abs(actual - expected))),
        "rmse": float(np.sqrt(np.mean((actual - expected) ** 2))),
        "mean_expected_fp": float(np.mean(expected)), "mean_realized_fp": float(np.mean(actual)),
        "expected_minutes_mae": float(np.mean(np.abs(
            frame["actual_minutes"].to_numpy(float) - frame["expected_minutes"].to_numpy(float)
        ))),
        "coverage": {}, "upside_calibration": {}, "downside_calibration": {},
    }
    for level in (10, 50, 90, 95):
        metrics["coverage"][f"p{level}"] = float(np.mean(actual <= frame[f"p{level}_fp"]))
    for threshold in (20, 25, 30, 35, 40):
        probability = frame[f"prob_fp_ge_{threshold}"].to_numpy(float)
        observed = (actual >= threshold).astype(float)
        metrics["upside_calibration"][str(threshold)] = {
            "predicted_rate": float(np.mean(probability)), "observed_rate": float(np.mean(observed)),
            "brier": float(np.mean((probability - observed) ** 2)),
        }
    for threshold in (5, 10, 15):
        probability = frame[f"prob_fp_le_{threshold}"].to_numpy(float)
        observed = (actual <= threshold).astype(float)
        metrics["downside_calibration"][str(threshold)] = {
            "predicted_rate": float(np.mean(probability)), "observed_rate": float(np.mean(observed)),
            "brier": float(np.mean((probability - observed) ** 2)),
        }
    return metrics


def _hindsight_oracle(
    snapshot: Mapping[str, Any], rules: FantasyRules, database_path: Path | str,
) -> float | None:
    inputs = snapshot["knowledge"].get("decision_inputs", {})
    players = pd.DataFrame(inputs.get("players", []))
    coaches = pd.DataFrame(inputs.get("coaches", []))
    if players.empty or coaches.empty or "game_id" not in players or "game_id" not in coaches:
        return None
    game_ids = sorted(set(players["game_id"].astype(str)) | set(coaches["game_id"].astype(str)))
    placeholders = ",".join("?" for _ in game_ids)
    with connect_database(database_path, read_only=True) as connection:
        game_frame = connection.execute(
            f"""SELECT canonical_game_id, played, home_team_id, away_team_id,
                       home_score, away_score, coalesce(overtime_count, 0) > 0 AS overtime
                FROM current_games WHERE canonical_game_id IN ({placeholders})""", game_ids,
        ).df()
        target = connection.execute(
            f"""SELECT game_id, player_id, actual_fantasy_points
                FROM ml_player_game_targets_v1 WHERE game_id IN ({placeholders})""", game_ids,
        ).df()
    if len(set(game_ids) - set(game_frame[game_frame["played"]].canonical_game_id.astype(str))):
        return None
    actual_lookup = {(str(row.game_id), str(row.player_id)): float(row.actual_fantasy_points)
                     for row in target.itertuples(index=False)}
    players["actual_fp"] = [actual_lookup.get((str(row.game_id), str(row.player_id)), 0.0)
                            for row in players.itertuples(index=False)]
    game_lookup = {str(row.canonical_game_id): row for row in game_frame.itertuples(index=False)}
    coaches["actual_score"] = [
        coach_fantasy_score(
            int(game_lookup[str(row.game_id)].home_score),
            int(game_lookup[str(row.game_id)].away_score),
            home=str(row.team_id) == str(game_lookup[str(row.game_id)].home_team_id),
            overtime=bool(game_lookup[str(row.game_id)].overtime),
        ) for row in coaches.itertuples(index=False)
    ]
    knowledge = snapshot["knowledge"]
    current = set(map(str, knowledge.get("current_roster", [])))
    current_prices = pd.concat((players[["entity_id", "credits"]], coaches[["entity_id", "credits"]]))
    budget = float(current_prices[current_prices["entity_id"].astype(str).isin(current)]["credits"].sum())
    budget += float(knowledge.get("bank_credits", 0.0))
    constraints = {str(key): str(value).upper()
                   for key, value in knowledge.get("constraints", {}).items()}
    excluded = {key for key, value in constraints.items() if value == "EXCLUDE"}
    forced = {key for key, value in constraints.items() if value == "FORCE_INCLUDE"}
    if forced:
        # The exact solver has no mandatory-entity constraint; do not label a relaxed
        # upper bound as the same-policy oracle.
        return None
    player_pool = players[~players["entity_id"].astype(str).isin(excluded)].copy()
    coach_pool = coaches[~coaches["entity_id"].astype(str).isin(excluded)].copy()
    player_pool["_phase8b_hindsight_oracle"] = player_pool["actual_fp"]
    coach_pool["_phase8b_hindsight_oracle"] = coach_pool["actual_score"]
    try:
        result = optimize_deterministic(
            player_pool, coach_pool, rules, budget=budget,
            player_objective="_phase8b_hindsight_oracle",
            coach_objective="_phase8b_hindsight_oracle", roster_only=False,
            previous_entity_ids=current,
            trade_limit=int(knowledge.get("transfers_available", 0)),
        )
    except (ValueError, RuleViolation):
        # Pool gaps make the upper bound unavailable; they must never leak into the
        # actionable recommendation path.
        return None
    return float(result.objective_value)
