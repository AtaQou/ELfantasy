"""User-controlled availability scenarios and known-absence minute redistribution.

No participation probability is estimated here.  QUESTIONABLE, DOUBTFUL,
GAME_TIME_DECISION, and UNKNOWN remain user decisions; an unresolved player is
represented by explicit play/out scenarios rather than a probability-weighted row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import pickle
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.modeling.ml_experiments import _catboost_feature_frame, _raw_feature_frame
from src.modeling.phase5b_dataset import fallback_baseline_minutes
from src.modeling.phase5b_protocol import (
    DEFAULT_RECONCILIATION,
    FINAL_PHASE4B_MODEL_VERSION,
    PHASE5B_PROTOCOL_VERSION,
    REGULATION_TEAM_MINUTES,
    capped_simplex_projection,
    default_status_decision,
    normalize_baseline_minutes,
)

from .availability import set_availability_override


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "data" / "derived" / "phase5b" / "frozen_redistribution"
VALID_USER_DECISIONS = frozenset({"PLAY", "OUT", "UNKNOWN", "LIMITED"})
VALID_LIMIT_TYPES = frozenset({"MAX_MINUTES", "EXPECTED_MINUTES", "PERCENT_REDUCTION"})


@dataclass(frozen=True, slots=True)
class RoleLimit:
    player_id: str
    limit_type: str
    limit_value: float
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityScenario:
    scenario_name: str
    decisions: dict[str, str]
    unresolved_players: tuple[str, ...]
    warning: str | None = None


def review_availability(
    season_code: str,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Return the practical pre-prediction review table without mutating state."""

    with connect_database(database_path, read_only=True) as connection:
        exists = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name='current_upcoming_player_slate_v1'
            """
        ).fetchone()[0]
        if not exists:
            return pd.DataFrame(columns=[
                "player_id", "player", "team", "opponent", "tip_time", "credits",
                "source_status", "source", "last_updated", "manual_override",
                "recent_expected_minutes", "required_decision",
            ])
        slate = connection.execute(
            "SELECT * FROM current_upcoming_player_slate_v1 WHERE season=?",
            [season_code],
        ).df()
        names = connection.execute(
            "SELECT canonical_player_id AS player_id, display_name AS player FROM players"
        ).df()
    if slate.empty:
        return pd.DataFrame(columns=[
            "player_id", "player", "team", "opponent", "tip_time", "credits",
            "source_status", "source", "last_updated", "manual_override",
            "recent_expected_minutes", "required_decision",
        ])
    baseline = _baseline_series(slate)
    result = pd.DataFrame({
        "player_id": slate["player_id"].astype(str),
        "team": slate["team_id"].astype(str),
        "opponent": slate["opponent_team_id"].astype(str),
        "tip_time": slate.get("scheduled_tip_time", slate.get("target_game_time")),
        "credits": slate.get("current_fantasy_credits", slate.get("fantasy_credits")),
        "source_status": slate["resolved_availability_status"].fillna("UNKNOWN"),
        "source": slate.get("availability_source"),
        "last_updated": slate.get("availability_timestamp"),
        "manual_override": slate.get("manual_override_active", False),
        "recent_expected_minutes": baseline,
    }).merge(names, on="player_id", how="left")
    result["player"] = result["player"].fillna(result["player_id"])
    result["required_decision"] = result["source_status"].map(
        lambda value: default_status_decision(str(value))
    )
    return result[[
        "player_id", "player", "team", "opponent", "tip_time", "credits",
        "source_status", "source", "last_updated", "manual_override",
        "recent_expected_minutes", "required_decision",
    ]].sort_values(["tip_time", "team", "player"])


def apply_user_decision(
    player: str,
    decision: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str,
    game_id: str | None = None,
    fantasy_matchday: int | None = None,
    expires_at: datetime | None = None,
    note: str | None = None,
) -> str:
    """Persist PLAY/OUT/UNKNOWN/LIMITED through the Phase 5A override backend."""

    normalized = decision.upper()
    if normalized not in VALID_USER_DECISIONS:
        raise ValueError(f"Unsupported user decision: {decision}")
    status = {"PLAY": "AVAILABLE", "OUT": "OUT"}.get(normalized, normalized)
    return set_availability_override(
        player, status, database_path=database_path, season_code=season_code,
        game_id=game_id, fantasy_matchday=fantasy_matchday,
        expires_at=expires_at, note=note,
    )


def set_role_limit(
    player_id: str,
    limit_type: str,
    limit_value: float,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str | None = None,
    game_id: str | None = None,
    fantasy_matchday: int | None = None,
    expires_at: datetime | None = None,
    note: str | None = None,
    created_at: datetime | None = None,
) -> str:
    normalized = limit_type.upper()
    value = float(limit_value)
    if normalized not in VALID_LIMIT_TYPES:
        raise ValueError(f"Unsupported role-limit type: {limit_type}")
    if game_id is None and fantasy_matchday is None and expires_at is None:
        raise ValueError("Role limit requires a game, Fantasy matchday, or expiration")
    if normalized == "PERCENT_REDUCTION" and not 0 <= value <= 1:
        raise ValueError("Percentage role reduction must be between 0 and 1")
    if normalized != "PERCENT_REDUCTION" and not 0 <= value <= 40:
        raise ValueError("Minute limit must be between 0 and 40")
    initialize_database(database_path)
    now = created_at or datetime.now(UTC)
    key = stable_id("role_limit_scope", player_id, season_code, game_id, fantasy_matchday)
    event_id = stable_id("role_limit", key, "SET", now.isoformat())
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO role_limit_override_events (
              role_limit_event_id, role_limit_key, action, canonical_player_id,
              season_code, canonical_game_id, fantasy_matchday, limit_type,
              limit_value, expires_at, created_at, note, created_by
            ) VALUES (?, ?, 'SET', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'LOCAL_CLI')
            """,
            [event_id, key, player_id, season_code, game_id, fantasy_matchday,
             normalized, value, expires_at, now, note],
        )
    return event_id


def clear_role_limit(
    player_id: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str | None = None,
    game_id: str | None = None,
    fantasy_matchday: int | None = None,
    created_at: datetime | None = None,
) -> str:
    initialize_database(database_path)
    now = created_at or datetime.now(UTC)
    key = stable_id("role_limit_scope", player_id, season_code, game_id, fantasy_matchday)
    event_id = stable_id("role_limit", key, "CLEAR", now.isoformat())
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO role_limit_override_events (
              role_limit_event_id, role_limit_key, action, canonical_player_id,
              season_code, canonical_game_id, fantasy_matchday, limit_type,
              limit_value, expires_at, created_at, note, created_by
            ) VALUES (?, ?, 'CLEAR', ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, 'LOCAL_CLI')
            """,
            [event_id, key, player_id, season_code, game_id, fantasy_matchday, now],
        )
    return event_id


def resolve_role_limits(
    player_ids: Sequence[str],
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str,
    game_id: str | None,
    fantasy_matchday: int | None,
    as_of: datetime | None = None,
) -> dict[str, RoleLimit]:
    if not player_ids:
        return {}
    cutoff = as_of or datetime.now(UTC)
    with connect_database(database_path, read_only=True) as connection:
        rows = connection.execute(
            """
            WITH ranked AS (
              SELECT event.*, row_number() OVER (
                PARTITION BY role_limit_key
                ORDER BY created_at DESC, role_limit_event_id DESC
              ) AS event_rank
              FROM role_limit_override_events AS event
              WHERE created_at <= ?
            )
            SELECT canonical_player_id, limit_type, limit_value, role_limit_event_id,
                   canonical_game_id, fantasy_matchday, created_at
            FROM ranked
            WHERE event_rank=1 AND action='SET'
              AND canonical_player_id IN (SELECT unnest(?))
              AND (season_code IS NULL OR season_code=?)
              AND (canonical_game_id IS NULL OR canonical_game_id=?)
              AND (fantasy_matchday IS NULL OR fantasy_matchday=?)
              AND (expires_at IS NULL OR expires_at>?)
            ORDER BY (canonical_game_id IS NOT NULL) DESC,
                     (fantasy_matchday IS NOT NULL) DESC, created_at DESC
            """,
            [cutoff, list(player_ids), season_code, game_id, fantasy_matchday, cutoff],
        ).fetchall()
    resolved: dict[str, RoleLimit] = {}
    for player_id, limit_type, value, event_id, *_ in rows:
        resolved.setdefault(
            str(player_id),
            RoleLimit(str(player_id), str(limit_type), float(value), str(event_id)),
        )
    return resolved


def resolve_decisions(
    slate: pd.DataFrame,
    user_decisions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply deterministic semantics; source uncertainty never changes minutes."""

    user = {str(key): value.upper() for key, value in (user_decisions or {}).items()}
    invalid = sorted(set(user.values()) - VALID_USER_DECISIONS)
    if invalid:
        raise ValueError(f"Unsupported scenario decisions: {invalid}")
    resolved: dict[str, str] = {}
    for row in slate.itertuples():
        player = str(row.player_id)
        if player in user:
            resolved[player] = user[player]
            continue
        source_status = str(getattr(row, "resolved_availability_status", "UNKNOWN"))
        default = default_status_decision(source_status)
        resolved[player] = "UNKNOWN" if default == "USER_DECISION" else default
    return resolved


def generate_availability_scenarios(
    slate: pd.DataFrame,
    *,
    user_decisions: Mapping[str, str] | None = None,
    scenario_sets: Sequence[Mapping[str, str]] | None = None,
) -> list[AvailabilityScenario]:
    """Generate explicit branches without probabilities or exponential guessing."""

    base = resolve_decisions(slate, user_decisions)
    unresolved = tuple(sorted(player for player, value in base.items() if value == "UNKNOWN"))
    if scenario_sets:
        scenarios: list[AvailabilityScenario] = []
        for index, choices in enumerate(scenario_sets, start=1):
            decisions = dict(base)
            for player, choice in choices.items():
                value = choice.upper()
                if value not in VALID_USER_DECISIONS:
                    raise ValueError(f"Unsupported scenario decision: {value}")
                decisions[str(player)] = value
            remaining = tuple(sorted(
                player for player, value in decisions.items() if value == "UNKNOWN"
            ))
            scenarios.append(AvailabilityScenario(
                f"USER_SCENARIO_{index}", decisions, remaining,
                "Scenario retains unresolved players" if remaining else None,
            ))
        return scenarios
    if len(unresolved) == 1:
        player = unresolved[0]
        plays = dict(base)
        plays[player] = "PLAY"
        out = dict(base)
        out[player] = "OUT"
        return [
            AvailabilityScenario(f"{player}_PLAYS", plays, ()),
            AvailabilityScenario(f"{player}_OUT", out, ()),
        ]
    if len(unresolved) > 1:
        return [AvailabilityScenario(
            "UNRESOLVED", base, unresolved,
            "Multiple uncertain players remain; supply explicit --scenario decisions "
            "instead of generating every combination",
        )]
    return [AvailabilityScenario("RESOLVED", base, ())]


def redistribute_live_team(
    team: pd.DataFrame,
    decisions: Mapping[str, str],
    *,
    role_limits: Mapping[str, RoleLimit] | None = None,
    model_root: Path = DEFAULT_MODEL_ROOT,
) -> pd.DataFrame:
    """Run one fully resolved known-absence team scenario and conserve 200 minutes."""

    frame = team.copy().reset_index(drop=True)
    if frame.empty:
        return frame
    frame["baseline_expected_minutes"] = normalize_baseline_minutes(
        _baseline_series(frame).to_numpy(float)
    )
    frame["scenario_decision"] = frame["player_id"].astype(str).map(decisions).fillna("UNKNOWN")
    if frame["scenario_decision"].eq("UNKNOWN").any():
        raise ValueError("Cannot redistribute an unresolved scenario")
    absent = frame["scenario_decision"].eq("OUT")
    available = frame[~absent].copy()
    if len(available) < 5:
        raise ValueError("Known-absence scenario has fewer than five available players")
    missing = float(frame.loc[absent, "baseline_expected_minutes"].sum())
    available = _attach_live_context(available, frame.loc[absent], frame)
    if not absent.any():
        # The frozen absence model has no role to redistribute when nobody is out.
        # Explicit role limits are still reconciled below, but availability labels
        # alone never perturb a normal rotation.
        raw_delta = np.zeros(len(available), dtype=float)
    else:
        raw_delta = _predict_frozen_delta(available, model_root)
    raw_minutes = available["baseline_expected_minutes"].to_numpy(float) + raw_delta
    lower = np.zeros(len(available), dtype=float)
    upper = np.full(len(available), 40.0, dtype=float)
    limits = role_limits or {}
    for index, row in enumerate(available.itertuples()):
        limit = limits.get(str(row.player_id))
        if limit is None:
            continue
        baseline = float(row.baseline_expected_minutes)
        if limit.limit_type == "MAX_MINUTES":
            upper[index] = min(upper[index], limit.limit_value)
        elif limit.limit_type == "EXPECTED_MINUTES":
            lower[index] = upper[index] = limit.limit_value
        elif limit.limit_type == "PERCENT_REDUCTION":
            upper[index] = min(upper[index], baseline * (1.0 - limit.limit_value))
    adjusted = capped_simplex_projection(raw_minutes, lower=lower, upper=upper)
    frame["adjusted_expected_minutes"] = 0.0
    frame.loc[available.index, "adjusted_expected_minutes"] = adjusted
    frame["minutes_delta_due_to_absences"] = (
        frame["adjusted_expected_minutes"] - frame["baseline_expected_minutes"]
    )
    frame["team_total_missing_minutes"] = missing
    frame["number_teammates_out"] = int(absent.sum())
    team_context_columns = (
        "dominant_missing_position", "number_rotation_players_out",
        "missing_guard_minutes", "missing_forward_minutes", "missing_center_minutes",
        "remaining_guard_depth", "remaining_forward_depth", "remaining_center_depth",
        "remaining_high_role_players", "team_role_concentration_before",
    )
    for column in team_context_columns:
        if column in available:
            frame[column] = available[column].iloc[0]
    recipient_context_columns = (
        "historical_role_rank", "same_position_competition",
        "same_position_missing_minutes", "historical_shared_games_with_absent",
        "historical_shared_minutes_with_absent", "prior_absence_response_games",
        "prior_absence_response_avg_delta",
    )
    for column in recipient_context_columns:
        if column in available:
            frame.loc[available.index, column] = available[column]
    return _propagate_fantasy(frame)


def attach_live_relationship_history(
    team: pd.DataFrame,
    decisions: Mapping[str, str],
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    cutoff: datetime,
) -> pd.DataFrame:
    """Attach only prior-game pair/absence-response evidence for a live scenario."""

    output = team.copy()
    player_ids = output["player_id"].astype(str).tolist()
    absent_ids = {
        player for player, decision in decisions.items() if decision.upper() == "OUT"
    }
    if not absent_ids:
        for column in (
            "historical_shared_games_with_absent",
            "historical_shared_minutes_with_absent",
            "prior_absence_response_games",
            "prior_absence_response_avg_delta",
        ):
            output[column] = 0.0 if column.endswith("games") else np.nan
        return output
    team_id = str(output["team_id"].iloc[0])
    with connect_database(database_path, read_only=True) as connection:
        history = connection.execute(
            """
            SELECT stats.canonical_game_id AS game_id,
                   stats.canonical_player_id AS player_id, stats.minutes
            FROM player_game_stats AS stats
            JOIN games AS game USING (canonical_game_id)
            WHERE stats.canonical_team_id=? AND stats.canonical_player_id IN (
              SELECT unnest(?)
            ) AND stats.minutes>0 AND game.game_date<?
            """,
            [team_id, player_ids, cutoff],
        ).df()
        event_table = bool(connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='ml_phase5b_absence_recipient_rows_v1'"
        ).fetchone()[0])
        response = connection.execute(
            """
            SELECT player_id, minutes_delta, target_game_time
            FROM ml_phase5b_absence_recipient_rows_v1
            WHERE player_id IN (SELECT unnest(?)) AND target_game_time<?
            """,
            [player_ids, cutoff],
        ).df() if event_table else pd.DataFrame()
    by_game = {
        game: group.set_index("player_id")["minutes"].to_dict()
        for game, group in history.groupby("game_id")
    }
    shared_games: dict[str, float] = {}
    shared_minutes: dict[str, float] = {}
    for player in player_ids:
        pair_counts = []
        pair_minutes = []
        for absent in absent_ids:
            common = [minutes for minutes in by_game.values()
                      if player in minutes and absent in minutes]
            pair_counts.append(float(len(common)))
            if common:
                pair_minutes.append(float(np.mean([
                    min(float(values[player]), float(values[absent])) for values in common
                ])))
        shared_games[player] = float(np.mean(pair_counts)) if pair_counts else 0.0
        shared_minutes[player] = float(np.mean(pair_minutes)) if pair_minutes else 0.0
    output["historical_shared_games_with_absent"] = output.player_id.astype(str).map(
        shared_games
    )
    output["historical_shared_minutes_with_absent"] = output.player_id.astype(str).map(
        shared_minutes
    )
    if response.empty:
        output["prior_absence_response_games"] = 0.0
        output["prior_absence_response_avg_delta"] = np.nan
    else:
        response_summary = response.groupby("player_id").minutes_delta.agg(
            ["count", "mean"]
        )
        output["prior_absence_response_games"] = output.player_id.map(
            response_summary["count"]
        ).fillna(0)
        averages = output.player_id.map(response_summary["mean"])
        output["prior_absence_response_avg_delta"] = averages.where(
            output["prior_absence_response_games"].ge(2)
        )
    return output


def persist_scenario(
    output: pd.DataFrame,
    scenario: AvailabilityScenario,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str,
    game_id: str,
    team_id: str,
    feature_cutoff_time: datetime,
    source_slate_run_id: str | None = None,
    model_root: Path = DEFAULT_MODEL_ROOT,
) -> str:
    initialize_database(database_path)
    now = datetime.now(UTC)
    input_payload = {
        "season": season_code, "game": game_id, "team": team_id,
        "decisions": scenario.decisions,
        "baseline": output[["player_id", "baseline_expected_minutes"]].to_dict("records"),
        "cutoff": feature_cutoff_time.isoformat(),
    }
    input_fingerprint = hashlib.sha256(
        json.dumps(input_payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    scenario_id = stable_id("absence_scenario", input_fingerprint, scenario.scenario_name)
    output_fingerprint = hashlib.sha256(
        output[["player_id", "adjusted_expected_minutes"]].to_json().encode()
    ).hexdigest()
    manifest_path = model_root / "manifest.json"
    model_version = None
    if manifest_path.is_file():
        model_version = json.loads(manifest_path.read_text())["redistribution_model_version"]
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO absence_scenarios VALUES (?, ?, ?, ?, ?, 'GENERATED', ?, ?,
              ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (absence_scenario_id) DO NOTHING
            """,
            [scenario_id, season_code, game_id, team_id, scenario.scenario_name, now,
             feature_cutoff_time, json.dumps(scenario.decisions, sort_keys=True),
             json.dumps(scenario.unresolved_players), source_slate_run_id, model_version,
             DEFAULT_RECONCILIATION, input_fingerprint, output_fingerprint, scenario.warning],
        )
        for row in output.itertuples():
            explanation = {
                "context_not_causality": True,
                "number_teammates_out": int(row.number_teammates_out),
                "team_total_missing_minutes": float(row.team_total_missing_minutes),
                "baseline_minutes": float(row.baseline_expected_minutes),
                "adjusted_minutes": float(row.adjusted_expected_minutes),
                "context_signals": {
                    "historical_role_rank": _optional_value(row, "historical_role_rank"),
                    "recent_rotation_share": _optional_value(
                        row, "rot_last5_minutes_share"
                    ),
                    "same_position_missing_minutes": _optional_value(
                        row, "same_position_missing_minutes"
                    ),
                    "prior_absence_response_games": _optional_value(
                        row, "prior_absence_response_games"
                    ),
                    "prior_absence_response_avg_delta": _optional_value(
                        row, "prior_absence_response_avg_delta"
                    ),
                    "dominant_missing_position": _optional_value(
                        row, "dominant_missing_position"
                    ),
                },
            }
            output_id = stable_id("scenario_player_output", scenario_id, row.player_id)
            connection.execute(
                """
                INSERT INTO absence_scenario_player_outputs VALUES (
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL,
                  'UNCALIBRATED_AFTER_MINUTES_ADJUSTMENT', ?, ?, ?
                ) ON CONFLICT (scenario_player_output_id) DO NOTHING
                """,
                [output_id, scenario_id, row.player_id, row.scenario_decision,
                 row.baseline_expected_minutes, row.adjusted_expected_minutes,
                 row.minutes_delta_due_to_absences, row.team_total_missing_minutes,
                 row.number_teammates_out,
                 getattr(row, "expected_fp_before_absence_adjustment", None),
                 getattr(row, "expected_fp_after_absence_adjustment", None),
                 json.dumps(explanation, sort_keys=True), model_version, now],
            )
    return scenario_id


def _baseline_series(frame: pd.DataFrame) -> pd.Series:
    for column in ("baseline_expected_minutes", "predicted_minutes", "expected_minutes_if_playing"):
        if column in frame and pd.to_numeric(frame[column], errors="coerce").notna().any():
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.isna().any():
                values = values.fillna(fallback_baseline_minutes(frame))
            return values.fillna(REGULATION_TEAM_MINUTES / len(frame))
    return fallback_baseline_minutes(frame).fillna(REGULATION_TEAM_MINUTES / len(frame))


def _attach_live_context(
    available: pd.DataFrame,
    absent: pd.DataFrame,
    full: pd.DataFrame,
) -> pd.DataFrame:
    output = available.copy()
    missing = float(absent["baseline_expected_minutes"].sum())
    output["number_players_out"] = len(absent)
    output["number_rotation_players_out"] = int(
        absent["baseline_expected_minutes"].ge(5).sum()
    )
    output["missing_expected_minutes"] = missing
    output["missing_rotation_share"] = missing / REGULATION_TEAM_MINUTES
    start_rate = pd.to_numeric(
        absent.get("last_5_start_rate", pd.Series(0, index=absent.index)), errors="coerce"
    ).fillna(0)
    output["missing_starter_minutes"] = float(
        (absent["baseline_expected_minutes"] * start_rate).sum()
    )
    output["missing_bench_minutes"] = missing - output["missing_starter_minutes"].iloc[0]
    output["missing_recent_starts"] = float(start_rate.sum())
    baseline = absent["baseline_expected_minutes"].to_numpy(float)

    def absent_values(primary: str, secondary: str | None = None) -> np.ndarray:
        values = pd.to_numeric(
            absent.get(primary, pd.Series(np.nan, index=absent.index)), errors="coerce"
        )
        if secondary:
            values = values.fillna(pd.to_numeric(
                absent.get(secondary, pd.Series(np.nan, index=absent.index)),
                errors="coerce",
            ))
        return values.fillna(0).to_numpy(float)

    def weighted_rate(values: np.ndarray) -> float:
        return float(np.average(values, weights=baseline)) if baseline.sum() > 0 else 0.0

    fppm = absent_values("season_fp_per_min_before", "last_5_fp_per_min")
    points = absent_values("points_per_min")
    assists = absent_values("assists_per_min")
    rebounds = absent_values("rebounds_per_min")
    turnovers = absent_values("turnovers_per_min")
    output["missing_fp_before"] = float(np.sum(baseline * fppm))
    output["missing_fp_per_min_before"] = weighted_rate(fppm)
    output["missing_points_rate"] = weighted_rate(points)
    output["missing_assist_rate"] = weighted_rate(assists)
    output["missing_rebound_rate"] = weighted_rate(rebounds)
    output["missing_ballhandling_proxy"] = float(
        np.sum(baseline * (assists + turnovers))
    )
    output["missing_rebounding_proxy"] = float(np.sum(baseline * rebounds))
    position = absent.get("broad_position", pd.Series("UNKNOWN", index=absent.index))
    for label, column in (
        ("GUARD", "missing_guard_minutes"),
        ("FORWARD", "missing_forward_minutes"),
        ("CENTER", "missing_center_minutes"),
    ):
        output[column] = float(absent.loc[position.eq(label), "baseline_expected_minutes"].sum())
    totals = {label: float(output[column].iloc[0]) for label, column in (
        ("GUARD", "missing_guard_minutes"), ("FORWARD", "missing_forward_minutes"),
        ("CENTER", "missing_center_minutes"),
    )}
    output["dominant_missing_position"] = max(totals, key=totals.get) if missing else "UNKNOWN"
    output["available_rotation_players"] = int(
        available["baseline_expected_minutes"].ge(5).sum()
    )
    output["remaining_player_count"] = len(available)
    output["remaining_starter_count"] = float(pd.to_numeric(
        available.get("last_5_start_rate", pd.Series(0, index=available.index)),
        errors="coerce",
    ).fillna(pd.to_numeric(
        available.get("season_start_rate_before", pd.Series(0, index=available.index)),
        errors="coerce",
    )).fillna(0).sum())
    for label, column in (
        ("GUARD", "remaining_guard_depth"), ("FORWARD", "remaining_forward_depth"),
        ("CENTER", "remaining_center_depth"),
    ):
        output[column] = int(available.get(
            "broad_position", pd.Series("UNKNOWN", index=available.index)
        ).eq(label).sum())
    output["remaining_high_role_players"] = int(
        available["baseline_expected_minutes"].ge(20).sum()
    )
    output["remaining_expected_minutes_mean"] = float(
        available["baseline_expected_minutes"].mean()
    )
    output["remaining_expected_minutes_std"] = float(
        available["baseline_expected_minutes"].std(ddof=1) if len(available) > 1 else 0
    )
    output["remaining_expected_minutes_max"] = float(
        available["baseline_expected_minutes"].max()
    )
    available_share = available["baseline_expected_minutes"] / max(
        float(available["baseline_expected_minutes"].sum()), 1e-12
    )
    full_share = full["baseline_expected_minutes"] / REGULATION_TEAM_MINUTES
    output["remaining_role_concentration"] = float((available_share ** 2).sum())
    output["team_role_concentration_before"] = float((full_share ** 2).sum())
    output["team_rotation_depth_before"] = 1.0 / max(float((full_share ** 2).sum()), 1e-12)
    output["historical_role_rank"] = output["baseline_expected_minutes"].rank(
        method="min", ascending=False
    )
    output["same_position_competition"] = output.groupby(
        output.get("broad_position", pd.Series("UNKNOWN", index=output.index))
    )["player_id"].transform("count") - 1
    output["same_position_missing_minutes"] = output.get(
        "broad_position", pd.Series("UNKNOWN", index=output.index)
    ).map(totals).fillna(0)
    # Live relationship history is optional; absent evidence is represented as missing,
    # never as a fabricated player-pair rule.
    for column in (
        "historical_shared_games_with_absent", "historical_shared_minutes_with_absent",
        "prior_absence_response_games", "prior_absence_response_avg_delta",
    ):
        if column not in output:
            output[column] = np.nan
    return output


def _predict_frozen_delta(frame: pd.DataFrame, model_root: Path) -> np.ndarray:
    manifest_path = model_root / "manifest.json"
    if not manifest_path.is_file():
        # A naive proportional allocation is the safe deployment fallback when the
        # experiment protocol declines to freeze ML.
        missing = float(frame["missing_expected_minutes"].iloc[0])
        weights = frame["baseline_expected_minutes"].to_numpy(float)
        return missing * weights / max(float(weights.sum()), 1e-12)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = manifest_payload["feature_manifest"]
    for column in manifest["numeric"]:
        if column not in frame:
            frame[column] = np.nan
    for column in manifest["categorical"]:
        if column not in frame:
            frame[column] = "__MISSING__"
    model_type = manifest_payload["model_type"]
    if model_type == "catboost":
        from catboost import CatBoostRegressor

        model = CatBoostRegressor()
        model.load_model(str(model_root / "model.cbm"))
        return np.asarray(model.predict(_catboost_feature_frame(frame, manifest)), dtype=float)
    with (model_root / "model.pkl").open("rb") as handle:
        model = pickle.load(handle)
    return np.asarray(model.predict(_raw_feature_frame(frame, manifest)), dtype=float)


def _propagate_fantasy(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    direct_column = next((name for name in (
        "phase4a_direct_fp", "direct_fp", "predicted_fp_direct"
    ) if name in output), None)
    rate_column = next((name for name in (
        "predicted_fp_per_min", "expected_fp_per_min_if_playing"
    ) if name in output), None)
    if direct_column and rate_column:
        output["expected_fp_before_absence_adjustment"] = (
            0.25 * output[direct_column] + 0.75 *
            output["baseline_expected_minutes"] * output[rate_column]
        )
        output["expected_fp_after_absence_adjustment"] = (
            0.25 * output[direct_column] + 0.75 *
            output["adjusted_expected_minutes"] * output[rate_column]
        )
        output.loc[output["scenario_decision"].eq("OUT"),
                   "expected_fp_after_absence_adjustment"] = 0.0
    else:
        output["expected_fp_before_absence_adjustment"] = np.nan
        output["expected_fp_after_absence_adjustment"] = np.nan
    output["lower_prediction_interval"] = np.nan
    output["upper_prediction_interval"] = np.nan
    output["interval_calibration_status"] = (
        "NOT_RECALIBRATED_AFTER_AVAILABILITY_ADJUSTMENT"
    )
    output["performance_model_version"] = FINAL_PHASE4B_MODEL_VERSION
    output["phase5b_protocol_version"] = PHASE5B_PROTOCOL_VERSION
    return output


def _optional_value(row: Any, name: str) -> Any:
    value = getattr(row, name, None)
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value
