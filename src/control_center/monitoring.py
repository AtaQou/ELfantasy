"""Read-only Phase 8B strategy, shadow, and price monitoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database


def monitoring_report(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    profile_id: str = "default",
) -> dict[str, Any]:
    """Observe stored behavior without writing data or altering optimization inputs."""

    with connect_database(database_path, read_only=True) as connection:
        snapshots = connection.execute(
            """
            SELECT shadow_snapshot_id, season_code, fantasy_matchday,
                   knowledge_json, recommendations_json, created_at
            FROM fantasy_shadow_prelock_snapshots WHERE profile_id=?
            ORDER BY season_code, fantasy_matchday, created_at
            """, [profile_id],
        ).df()
        advisors = connection.execute(
            """
            SELECT shadow_snapshot_id, completed_turn, actions_json, evidence_json
            FROM fantasy_turn_advisor_runs
            QUALIFY row_number() OVER (
              PARTITION BY shadow_snapshot_id, completed_turn ORDER BY created_at DESC
            )=1
            """
        ).df()
        evaluations = connection.execute(
            """
            SELECT shadow_snapshot_id, player_metrics_json, strategy_metrics_json
            FROM fantasy_shadow_matchday_evaluations
            """
        ).df()
        price = connection.execute(
            """
            SELECT absolute_error, squared_error, direction_correct
            FROM fantasy_price_prediction_outcomes
            """
        ).df()
    advisor_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    for row in advisors.itertuples(index=False):
        advisor_by_snapshot.setdefault(str(row.shadow_snapshot_id), []).append({
            "completed_turn": int(row.completed_turn),
            "actions": _json(row.actions_json, []),
            "evidence": _json(row.evidence_json, {}),
        })
    rounds = []
    for row in snapshots.itertuples(index=False):
        knowledge = _json(row.knowledge_json, {})
        recommendations = _json(row.recommendations_json, [])
        best = next((item for item in recommendations if item.get("label") == "BEST_OVERALL"),
                    recommendations[0] if recommendations else {})
        players = best.get("players", [])
        credits = np.asarray([float(item.get("credits", 0.0)) for item in players])
        widths = np.asarray([float(item.get("p90", 0.0)) - float(item.get("p10", 0.0))
                             for item in players])
        t1_widths = [float(item.get("p90", 0.0)) - float(item.get("p10", 0.0))
                     for item in players if int(item.get("turn", 0)) == 1]
        fp_credit = [float(item.get("expected_fp", 0.0)) / float(item.get("credits", 1.0))
                     for item in players if float(item.get("credits", 0.0)) > 0]
        captain = next((item for item in players if item.get("captain")), {})
        advisor = advisor_by_snapshot.get(str(row.shadow_snapshot_id), [])
        actions = [action for run in advisor for action in run["actions"]]
        constraints = knowledge.get("constraints", {})
        transfer_limit = int(knowledge.get("transfers_available", 0))
        transfers_used = int(best.get("transfers_required", 0))
        turn_counts: dict[str, int] = {}
        for item in players:
            turn_counts[str(int(item.get("turn", 0)))] = turn_counts.get(
                str(int(item.get("turn", 0))), 0
            ) + 1
        rounds.append({
            "shadow_snapshot_id": str(row.shadow_snapshot_id),
            "season": str(row.season_code), "matchday": int(row.fantasy_matchday),
            "transfers_used": transfers_used,
            "transfers_saved": max(transfer_limit - transfers_used, 0),
            "average_roster_credits": float(np.mean(credits)) if len(credits) else None,
            "bank_credits": float(knowledge.get("bank_credits", 0.0)),
            "expensive_player_concentration": (
                float(np.sort(credits)[-3:].sum() / credits.sum()) if credits.sum() else None
            ),
            "average_fp_per_credit": float(np.mean(fp_credit)) if fp_credit else None,
            "turn_allocation": turn_counts,
            "captain_risk_width": (
                float(captain.get("p90", 0.0)) - float(captain.get("p10", 0.0))
                if captain else None
            ),
            "captain_switches_recommended": sum(
                action.get("action") == "CHANGE_CAPTAIN" for action in actions
            ),
            "bench_switches_recommended": sum(
                action.get("action") in {"MOVE_TO_BENCH", "MOVE_TO_FIELD"} for action in actions
            ),
            "sixth_man_usage": 1 if best.get("sixth_man") else 0,
            "average_roster_p10_p90_width": float(np.mean(widths)) if len(widths) else None,
            "t1_volatility_width": float(np.mean(t1_widths)) if t1_widths else None,
            "high_risk_selection_frequency": (
                float(np.mean(widths >= np.quantile(widths, 0.75))) if len(widths) else None
            ),
            "force_include_count": sum(str(value).upper() == "FORCE_INCLUDE"
                                       for value in constraints.values()),
            "exclude_count": sum(str(value).upper() == "EXCLUDE"
                                 for value in constraints.values()),
            "availability_override_count": len(knowledge.get("manual_overrides", [])),
            "recommendation_reversed_after_turn": bool(actions),
        })
    trends = pd.DataFrame(rounds)
    alerts = _diagnostic_alerts(trends)
    evaluation_rows = []
    for row in evaluations.itertuples(index=False):
        evaluation_rows.append({
            "shadow_snapshot_id": str(row.shadow_snapshot_id),
            "player": _json(row.player_metrics_json, {}),
            "strategy": _json(row.strategy_metrics_json, {}),
        })
    completed = [row for row in evaluation_rows if row["strategy"].get("status") == "EVALUATED"]
    summary = {
        "shadow_matchdays": len(rounds), "evaluated_matchdays": len(completed),
        "recommendations_reversed_percentage": (
            float(np.mean([row["recommendation_reversed_after_turn"] for row in rounds]))
            if rounds else None
        ),
        "mean_player_mae": _nested_mean(evaluation_rows, "player", "mae"),
        "mean_player_rmse": _nested_mean(evaluation_rows, "player", "rmse"),
        "mean_expected_minutes_mae": _nested_mean(
            evaluation_rows, "player", "expected_minutes_mae"
        ),
        "mean_strategy_simulation_error": _nested_mean(
            completed, "strategy", "simulation_error"
        ),
        "mean_regret_vs_hindsight_oracle": _nested_mean(
            completed, "strategy", "regret_vs_hindsight_oracle"
        ),
        "price_shadow_rows": len(price),
        "price_mae": float(price["absolute_error"].mean()) if not price.empty else None,
        "price_rmse": float(np.sqrt(price["squared_error"].mean())) if not price.empty else None,
        "price_direction_accuracy": float(price["direction_correct"].mean())
        if not price.empty else None,
    }
    return {
        "mode": "READ_ONLY_DIAGNOSTIC",
        "optimization_modified": False,
        "sample_status": (
            "INSUFFICIENT_LIVE_SAMPLE" if len(rounds) < 6 else "TREND_MONITORING_ACTIVE"
        ),
        "minimum_trend_sample": 6,
        "summary": summary, "rounds": rounds, "alerts": alerts,
        "live_validation": evaluation_rows,
        "tracked_metrics": [
            "transfers_used", "transfers_saved", "average_roster_credits", "bank_credits",
            "expensive_player_concentration", "average_fp_per_credit", "turn_allocation",
            "captain_risk_width", "captain_switches_recommended", "bench_switches_recommended",
            "sixth_man_usage", "average_roster_p10_p90_width", "t1_volatility_width",
            "high_risk_selection_frequency", "force_include_count", "exclude_count",
            "availability_override_count", "recommendation_reversed_after_turn",
        ],
    }


def _diagnostic_alerts(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if len(frame) < 6:
        return []
    alerts = []
    for column, label in (
        ("average_roster_credits", "unusually expensive rosters"),
        ("t1_volatility_width", "T1 volatility exposure"),
    ):
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if len(values) < 6:
            continue
        historical = values.iloc[:-5]
        if historical.empty:
            continue
        threshold = float(historical.quantile(0.90))
        if bool((values.iloc[-5:] > threshold).all()):
            alerts.append({
                "severity": "DIAGNOSTIC", "metric": column,
                "message": f"Optimizer has shown {label} for 5 consecutive rounds.",
                "threshold": threshold, "automatic_action": None,
            })
    return alerts


def _nested_mean(rows: list[Mapping[str, Any]], group: str, key: str) -> float | None:
    values = [row.get(group, {}).get(key) for row in rows]
    finite = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    return float(np.mean(finite)) if finite else None


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default
