"""Leakage-safe historical rotation-absence and recipient dataset construction."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.modeling.ml_runner import _write_parquet

from .phase5b_protocol import (
    NON_TRIVIAL_ROLE_MINUTES,
    PHASE5B_DATASET_VERSION,
    normalize_baseline_minutes,
    regulation_equivalent_minutes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DERIVED_ROOT = PROJECT_ROOT / "data" / "derived" / "phase5b"


@dataclass(frozen=True, slots=True)
class AbsenceDatasetSummary:
    candidate_rows: int
    usable_team_games: int
    absence_team_games: int
    zero_minute_observations: int
    non_trivial_absences: int
    recipient_rows: int
    first_season: str
    last_season: str
    fingerprint: str
    generated_at: str


def build_candidate_features(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Create one point-in-time feature row for every box-score roster candidate."""

    with connect_database(database_path, read_only=True) as connection:
        candidates = connection.execute(
            """
            SELECT target.player_game_id AS model_row_id,
                   target.player_game_id, target.season,
                   target.season_start_year, target.round_number,
                   target.game_id, target.game_code, target.target_game_time,
                   target.feature_cutoff_time, target.player_id, target.team_id,
                   target.opponent_team_id, target.home_away,
                   target.target_minutes AS actual_minutes,
                   target.target_started AS actual_started,
                   target.eligibility_status,
                   target.standardized_fantasy_points,
                   target.actual_fantasy_points,
                   target.points, target.total_rebounds, target.assists,
                   target.turnovers,
                   membership.position_code, membership.position_name,
                   CASE membership.position_code
                     WHEN '1' THEN 'GUARD'
                     WHEN '2' THEN 'FORWARD'
                     WHEN '3' THEN 'CENTER'
                     ELSE 'UNKNOWN'
                   END AS broad_position
            FROM ml_player_game_targets_v1 AS target
            LEFT JOIN seasons AS season
              ON season.season_code=target.season
            LEFT JOIN player_team_memberships AS membership
              ON membership.season_id=season.season_id
             AND membership.canonical_player_id=target.player_id
             AND membership.canonical_team_id=target.team_id
            WHERE target.target_game_time IS NOT NULL
              AND (
                target.eligibility_status IN ('PLAYED','DNP')
                OR coalesce(target.target_minutes,0)=0
              )
            ORDER BY target.target_game_time, target.game_id,
                     target.team_id, target.player_id
            """
        ).df()
        team_context = connection.execute(
            """
            SELECT game_id, team_id,
                   any_value(team_games_before) AS team_games_before,
                   any_value(team_season_pace_before) AS team_season_pace_before,
                   any_value(team_last5_pace) AS team_last5_pace,
                   any_value(team_season_ortg_before) AS team_season_ortg_before,
                   any_value(team_last5_ortg) AS team_last5_ortg,
                   any_value(opp_games_before) AS opp_games_before,
                   any_value(opp_season_drtg_before) AS opp_season_drtg_before,
                   any_value(opp_last5_drtg) AS opp_last5_drtg,
                   any_value(team_days_since_previous_el_game)
                     AS team_days_since_previous_el_game
            FROM ml_player_game_core_features_v1
            GROUP BY game_id, team_id
            """
        ).df()
        rotation = connection.execute(
            """
            SELECT player_id, season, game_id,
                   to_timestamp(game_time_us / 1000000.0) AS game_time,
                   stint_count, rotation_seconds, avg_stint_seconds,
                   longest_stint_seconds, first_entry_second,
                   first_sub_out_second, first_stint_seconds,
                   closing_lineup, starter, game_seconds, source_game_time_us
            FROM player_game_rotation_observations_v1
            ORDER BY player_id, game_time_us, game_id
            """
        ).df()
    candidates["target_game_time"] = pd.to_datetime(
        candidates["target_game_time"], utc=True
    )
    candidates["feature_cutoff_time"] = pd.to_datetime(
        candidates["feature_cutoff_time"], utc=True
    )
    candidates["broad_position"] = candidates["broad_position"].fillna("UNKNOWN")
    candidates = _lagged_player_features(candidates)
    candidates = candidates.merge(
        team_context, on=["game_id", "team_id"], how="left", validate="many_to_one"
    )
    candidates["home_game"] = candidates["home_away"].eq("home").astype(float)
    candidates["away_game"] = candidates["home_away"].eq("away").astype(float)
    candidates = _attach_rotation_features(candidates, rotation)
    if (candidates["player_history_max_game_time"].notna() & (
        candidates["player_history_max_game_time"] >= candidates["target_game_time"]
    )).any():
        raise ValueError("Player role features crossed the target-game cutoff")
    if (candidates["rotation_history_max_time"].notna() & (
        candidates["rotation_history_max_time"] >= candidates["target_game_time"]
    )).any():
        raise ValueError("Rotation features crossed the target-game cutoff")
    return candidates.sort_values(
        ["target_game_time", "game_id", "team_id", "player_id"]
    ).reset_index(drop=True)


def _lagged_player_features(frame: pd.DataFrame) -> pd.DataFrame:
    records: dict[int, dict[str, Any]] = {}
    for _, group in frame.groupby("player_id", sort=False):
        history: list[dict[str, Any]] = []
        season_history: list[dict[str, Any]] = []
        active_season: str | None = None
        recent_seven_days: deque[dict[str, Any]] = deque()
        minutes_ewma_numerator = 0.0
        fp_ewma_numerator = 0.0
        ewma_denominator = 0.0
        fp_ewma_denominator = 0.0
        ordered = group.sort_values(["target_game_time", "game_id"])
        for index, row in ordered.iterrows():
            if row["season"] != active_season:
                active_season = str(row["season"])
                season_history = []
            seven_day_cutoff = row["target_game_time"] - pd.Timedelta(days=7)
            while recent_seven_days and recent_seven_days[0]["time"] < seven_day_cutoff:
                recent_seven_days.popleft()
            prior = history
            season_prior = season_history
            last10 = prior[-10:]
            last5 = prior[-5:]
            last3 = prior[-3:]
            previous3 = prior[-6:-3]
            last5_minutes = _sum(last5, "minutes")
            season_minutes = _sum(season_prior, "minutes")
            rec = {
                "career_el_games_before": len(prior),
                "season_games_before": len(season_prior),
                "career_minutes_avg_before": _mean(prior, "minutes"),
                "season_minutes_avg_before": _mean(season_prior, "minutes"),
                "last_1_minutes": prior[-1]["minutes"] if prior else np.nan,
                "last_3_minutes_avg": _mean(last3, "minutes"),
                "last_5_minutes_avg": _mean(last5, "minutes"),
                "last_10_minutes_avg": _mean(last10, "minutes"),
                "minutes_ewma": (
                    minutes_ewma_numerator / ewma_denominator
                    if ewma_denominator else np.nan
                ),
                "minutes_trend": (
                    _mean(last3, "minutes") - _mean(previous3, "minutes")
                    if len(last3) == 3 and len(previous3) == 3 else np.nan
                ),
                "season_start_rate_before": _mean(season_prior, "started"),
                "last_5_start_rate": _mean(last5, "started"),
                "season_fp_avg_before": _mean(season_prior, "fp"),
                "last_3_fp_avg": _mean(last3, "fp"),
                "last_5_fp_avg": _mean(last5, "fp"),
                "last_10_fp_avg": _mean(last10, "fp"),
                "fp_ewma": (
                    fp_ewma_numerator / fp_ewma_denominator
                    if fp_ewma_denominator else np.nan
                ),
                "season_fp_per_min_before": (
                    _sum(season_prior, "fp") / season_minutes
                    if season_minutes >= 10 else np.nan
                ),
                "last_5_fp_per_min": (
                    _sum(last5, "fp") / last5_minutes
                    if last5_minutes >= 10 else np.nan
                ),
                "points_per_min": _rate(last5, "points", last5_minutes),
                "assists_per_min": _rate(last5, "assists", last5_minutes),
                "rebounds_per_min": _rate(last5, "rebounds", last5_minutes),
                "turnovers_per_min": _rate(last5, "turnovers", last5_minutes),
                "days_since_previous_el_game": (
                    (row["target_game_time"] - prior[-1]["time"]).total_seconds() / 86400
                    if prior else np.nan
                ),
                "player_games_last_7_calendar_days_el": len(recent_seven_days),
                "player_minutes_last_7_calendar_days_el": sum(
                    item["minutes"] for item in recent_seven_days
                ),
                "player_history_max_game_time": prior[-1]["time"] if prior else pd.NaT,
            }
            records[index] = rec
            if row["eligibility_status"] == "PLAYED" and float(row["actual_minutes"]) > 0:
                fp = row["standardized_fantasy_points"]
                observation = {
                    "season": row["season"], "time": row["target_game_time"],
                    "minutes": float(row["actual_minutes"]),
                    "started": float(bool(row["actual_started"])),
                    "fp": float(fp) if pd.notna(fp) else np.nan,
                    "points": float(row["points"] or 0),
                    "assists": float(row["assists"] or 0),
                    "rebounds": float(row["total_rebounds"] or 0),
                    "turnovers": float(row["turnovers"] or 0),
                }
                history.append(observation)
                season_history.append(observation)
                recent_seven_days.append(observation)
                minutes_ewma_numerator = (
                    0.65 * minutes_ewma_numerator + observation["minutes"]
                )
                ewma_denominator = 0.65 * ewma_denominator + 1.0
                if np.isfinite(observation["fp"]):
                    fp_ewma_numerator = 0.65 * fp_ewma_numerator + observation["fp"]
                    fp_ewma_denominator = 0.65 * fp_ewma_denominator + 1.0
    additions = pd.DataFrame.from_dict(records, orient="index")
    return frame.join(additions)


def _attach_rotation_features(
    candidates: pd.DataFrame,
    observations: pd.DataFrame,
) -> pd.DataFrame:
    output: dict[int, dict[str, Any]] = {}
    observations["game_time"] = pd.to_datetime(observations["game_time"], utc=True)
    by_player = {
        str(player): group.sort_values(["game_time", "game_id"]).to_dict("records")
        for player, group in observations.groupby("player_id", sort=False)
    }
    for player, group in candidates.groupby("player_id", sort=False):
        history = by_player.get(str(player), [])
        pointer = 0
        prior: list[dict[str, Any]] = []
        for index, row in group.sort_values(["target_game_time", "game_id"]).iterrows():
            while pointer < len(history) and history[pointer]["game_time"] < row["target_game_time"]:
                prior.append(history[pointer])
                pointer += 1
            recent = prior[-5:]
            season_prior = [item for item in prior if item["season"] == row["season"]]
            minutes = [float(item["rotation_seconds"]) / 60 for item in recent]
            stints = [float(item["stint_count"]) for item in recent]
            weights = np.power(0.65, np.arange(len(prior) - 1, -1, -1))
            output[index] = {
                "rot_games_before": len(prior),
                "rot_last5_minutes_avg": _numeric_mean(minutes),
                "rot_last5_minutes_share": _ratio_sum(recent, "rotation_seconds", "game_seconds"),
                "rot_season_minutes_share_before": _ratio_sum(
                    season_prior, "rotation_seconds", "game_seconds"
                ),
                "rot_last5_closing_lineup_rate": _row_mean(recent, "closing_lineup"),
                "rot_last5_starter_rate": _row_mean(recent, "starter"),
                "rot_last5_minutes_std": _std(minutes),
                "rot_last5_stint_count_avg": _numeric_mean(stints),
                "rot_last5_stint_count_std": _std(stints),
                "rot_last5_first_entry_minutes_avg": _seconds_mean(recent, "first_entry_second"),
                "rot_last5_first_sub_out_minutes_avg": _seconds_mean(recent, "first_sub_out_second"),
                "rot_minutes_trend": (
                    _numeric_mean([float(item["rotation_seconds"]) / 60 for item in prior[-3:]])
                    - _numeric_mean([float(item["rotation_seconds"]) / 60 for item in prior[-6:-3]])
                    if len(prior) >= 6 else np.nan
                ),
                "rot_minutes_ewma": _weighted_rows(prior, "rotation_seconds", weights, 60.0),
                "rotation_history_max_time": prior[-1]["game_time"] if prior else pd.NaT,
            }
            std_minutes = output[index]["rot_last5_minutes_std"]
            std_stints = output[index]["rot_last5_stint_count_std"]
            output[index]["rot_role_stability"] = (
                1 / (1 + std_minutes / 10 + std_stints / 3)
                if pd.notna(std_minutes) and pd.notna(std_stints) else np.nan
            )
    return candidates.join(pd.DataFrame.from_dict(output, orient="index"))


def fallback_baseline_minutes(frame: pd.DataFrame) -> pd.Series:
    """Documented train/live fallback when no conditional model score is available."""

    values = []
    columns = (
        ("minutes_ewma", 0.35), ("last_5_minutes_avg", 0.25),
        ("last_3_minutes_avg", 0.15), ("season_minutes_avg_before", 0.15),
        ("last_1_minutes", 0.10),
    )
    for row in frame.itertuples():
        weighted = [
            (float(getattr(row, column)), weight)
            for column, weight in columns
            if pd.notna(getattr(row, column, np.nan))
        ]
        if weighted:
            values.append(sum(value * weight for value, weight in weighted) /
                          sum(weight for _, weight in weighted))
        elif pd.notna(getattr(row, "career_minutes_avg_before", np.nan)):
            values.append(float(row.career_minutes_avg_before))
        else:
            values.append(np.nan)
    return pd.Series(values, index=frame.index, dtype=float)


def construct_absence_dataset(
    candidates: pd.DataFrame,
    baseline_column: str = "baseline_minutes_raw",
) -> tuple[pd.DataFrame, pd.DataFrame, AbsenceDatasetSummary]:
    """Normalize team rotations and construct available-recipient delta targets."""

    frame = candidates.copy()
    if baseline_column not in frame:
        frame[baseline_column] = fallback_baseline_minutes(frame)
    fallback = fallback_baseline_minutes(frame)
    frame[baseline_column] = pd.to_numeric(frame[baseline_column], errors="coerce").fillna(fallback)
    normalized_groups: list[pd.DataFrame] = []
    usable_team_games = 0
    for _, group in frame.groupby(["game_id", "team_id"], sort=False):
        group = group.copy()
        actual = pd.to_numeric(group["actual_minutes"], errors="coerce").fillna(0).to_numpy(float)
        if len(group) < 5 or np.count_nonzero(actual > 0) < 5 or actual.sum() <= 0:
            continue
        raw = pd.to_numeric(group[baseline_column], errors="coerce").to_numpy(float)
        if np.isnan(raw).any():
            raw = np.where(np.isnan(raw), 200.0 / len(group), raw)
        group["actual_regulation_minutes"] = regulation_equivalent_minutes(actual)
        group["baseline_expected_minutes"] = normalize_baseline_minutes(raw)
        group["rotation_absent"] = actual <= 0
        group["historical_role_rank"] = group["baseline_expected_minutes"].rank(
            method="min", ascending=False
        )
        normalized_groups.append(group)
        usable_team_games += 1
    # Group-wise assignment leaves a highly fragmented block layout.  Consolidate
    # once before the second team-game pass; otherwise boolean recipient slicing is
    # needlessly quadratic on the full eight-season frame.
    normalized = pd.concat(normalized_groups, ignore_index=True).copy()
    event_rows: list[dict[str, Any]] = []
    absence_games = 0
    for (_, _), group in normalized.groupby(["game_id", "team_id"], sort=False):
        absent = group[group["rotation_absent"]]
        available = group[~group["rotation_absent"]]
        if absent.empty:
            continue
        absence_games += 1
        context = _absence_context(group, absent, available)
        absent_positions = set(absent["broad_position"].astype(str))
        for _, row in available.iterrows():
            record = row.to_dict()
            record.update(context)
            position = str(row["broad_position"])
            record["same_position_competition"] = int(
                available["broad_position"].eq(position).sum() - 1
            )
            record["same_position_missing_minutes"] = float(
                absent.loc[absent["broad_position"].eq(position),
                           "baseline_expected_minutes"].sum()
            )
            record["recipient_matches_missing_position"] = position in absent_positions
            record["minutes_delta"] = (
                float(row["actual_regulation_minutes"])
                - float(row["baseline_expected_minutes"])
            )
            event_rows.append(record)
    events = pd.DataFrame(event_rows).sort_values(
        ["target_game_time", "game_id", "team_id", "player_id"]
    ).reset_index(drop=True)
    events = _attach_historical_relationships(events, normalized)
    fingerprint = _fingerprint(events, [
        "model_row_id", "baseline_expected_minutes", "minutes_delta",
        "missing_expected_minutes", "number_players_out",
    ])
    summary = AbsenceDatasetSummary(
        candidate_rows=len(normalized), usable_team_games=usable_team_games,
        absence_team_games=absence_games,
        zero_minute_observations=int(normalized["rotation_absent"].sum()),
        non_trivial_absences=int((
            normalized["rotation_absent"]
            & normalized["baseline_expected_minutes"].ge(NON_TRIVIAL_ROLE_MINUTES)
        ).sum()),
        recipient_rows=len(events), first_season=str(normalized["season"].min()),
        last_season=str(normalized["season"].max()), fingerprint=fingerprint,
        generated_at=datetime.now(UTC).isoformat(),
    )
    return normalized, events, summary


def _absence_context(
    all_players: pd.DataFrame,
    absent: pd.DataFrame,
    available: pd.DataFrame,
) -> dict[str, Any]:
    baseline = absent["baseline_expected_minutes"].to_numpy(float)
    total_missing = float(baseline.sum())
    starts = pd.to_numeric(absent["last_5_start_rate"], errors="coerce").fillna(
        pd.to_numeric(absent["season_start_rate_before"], errors="coerce")
    ).fillna(0).to_numpy(float)
    fppm = _coalesce_series(absent, "season_fp_per_min_before", "last_5_fp_per_min")
    points = pd.to_numeric(absent["points_per_min"], errors="coerce").fillna(0).to_numpy()
    assists = pd.to_numeric(absent["assists_per_min"], errors="coerce").fillna(0).to_numpy()
    rebounds = pd.to_numeric(absent["rebounds_per_min"], errors="coerce").fillna(0).to_numpy()
    turnovers = pd.to_numeric(absent["turnovers_per_min"], errors="coerce").fillna(0).to_numpy()
    all_share = all_players["baseline_expected_minutes"].to_numpy(float) / 200.0
    remaining = available["baseline_expected_minutes"].to_numpy(float)
    remaining_share = remaining / max(float(remaining.sum()), 1e-12)
    dominant = (
        absent.groupby("broad_position")["baseline_expected_minutes"].sum()
        .sort_values(ascending=False)
    )
    return {
        "number_players_out": len(absent),
        "number_rotation_players_out": int(absent["historical_role_rank"].le(10).sum()),
        "missing_expected_minutes": total_missing,
        "missing_rotation_share": total_missing / 200.0,
        "missing_starter_minutes": float(np.sum(baseline * starts)),
        "missing_bench_minutes": float(np.sum(baseline * (1 - starts))),
        "missing_recent_starts": float(starts.sum()),
        "missing_fp_before": float(np.sum(baseline * fppm)),
        "missing_fp_per_min_before": _weighted_average(fppm, baseline),
        "missing_points_rate": _weighted_average(points, baseline),
        "missing_assist_rate": _weighted_average(assists, baseline),
        "missing_rebound_rate": _weighted_average(rebounds, baseline),
        "missing_ballhandling_proxy": float(np.sum(baseline * (assists + turnovers))),
        "missing_rebounding_proxy": float(np.sum(baseline * rebounds)),
        "missing_guard_minutes": _position_minutes(absent, "GUARD"),
        "missing_forward_minutes": _position_minutes(absent, "FORWARD"),
        "missing_center_minutes": _position_minutes(absent, "CENTER"),
        "available_rotation_players": int(available["historical_role_rank"].le(10).sum()),
        "remaining_player_count": len(available),
        "remaining_starter_count": float(_coalesce_series(
            available, "last_5_start_rate", "season_start_rate_before"
        ).sum()),
        "remaining_guard_depth": int(available["broad_position"].eq("GUARD").sum()),
        "remaining_forward_depth": int(available["broad_position"].eq("FORWARD").sum()),
        "remaining_center_depth": int(available["broad_position"].eq("CENTER").sum()),
        "remaining_high_role_players": int(available["baseline_expected_minutes"].ge(20).sum()),
        "remaining_expected_minutes_mean": float(np.mean(remaining)),
        "remaining_expected_minutes_std": float(np.std(remaining, ddof=1)) if len(remaining)>1 else 0.0,
        "remaining_expected_minutes_max": float(np.max(remaining)),
        "remaining_role_concentration": float(np.sum(remaining_share ** 2)),
        "team_role_concentration_before": float(np.sum(all_share ** 2)),
        "team_rotation_depth_before": float(1 / np.sum(all_share ** 2)),
        "dominant_missing_position": str(dominant.index[0]) if len(dominant) else "UNKNOWN",
        "absent_player_ids": tuple(sorted(absent["player_id"].astype(str))),
        "absent_positions": tuple(sorted(absent["broad_position"].astype(str))),
    }


def _attach_historical_relationships(
    events: pd.DataFrame,
    normalized: pd.DataFrame,
) -> pd.DataFrame:
    pair_state: dict[tuple[str, str, str], dict[str, Any]] = {}
    response_state: dict[str, list[tuple[float, pd.Timestamp]]] = defaultdict(list)
    relationship: dict[int, dict[str, Any]] = {}
    events_by_group = {
        key: group for key, group in events.groupby(["game_id", "team_id"], sort=False)
    }
    for (game_id, team_id), roster in normalized.sort_values(
        ["target_game_time", "game_id", "team_id"]
    ).groupby(["game_id", "team_id"], sort=False):
        event = events_by_group.get((game_id, team_id))
        game_time = roster["target_game_time"].iloc[0]
        if event is not None:
            for index, row in event.iterrows():
                shared_games: list[float] = []
                shared_minutes: list[float] = []
                history_times: list[pd.Timestamp] = []
                for absent_id in row["absent_player_ids"]:
                    key = (str(team_id), *sorted((str(row["player_id"]), str(absent_id))))
                    state = pair_state.get(key)
                    if state:
                        shared_games.append(float(state["games"]))
                        shared_minutes.append(
                            float(state["shared_minutes"]) / float(state["games"])
                        )
                        history_times.append(state["last_time"])
                prior_response = response_state[str(row["player_id"])]
                if prior_response:
                    history_times.append(max(value[1] for value in prior_response))
                prior_deltas = [value[0] for value in prior_response]
                relationship[index] = {
                    "historical_shared_games_with_absent": (
                        float(np.mean(shared_games)) if shared_games else 0.0
                    ),
                    "historical_shared_minutes_with_absent": (
                        float(np.mean(shared_minutes)) if shared_minutes else 0.0
                    ),
                    "prior_absence_response_games": len(prior_response),
                    "prior_absence_response_avg_delta": (
                        float(np.mean(prior_deltas)) if len(prior_deltas) >= 2 else np.nan
                    ),
                    "relationship_history_max_time": (
                        max(history_times) if history_times else pd.NaT
                    ),
                }
            for _, row in event.iterrows():
                response_state[str(row["player_id"])].append(
                    (float(row["minutes_delta"]), game_time)
                )
        played = roster[~roster["rotation_absent"]]
        player_rows = list(played[["player_id", "actual_regulation_minutes"]].itertuples(index=False))
        for left_index, left in enumerate(player_rows):
            for right in player_rows[left_index + 1:]:
                key = (str(team_id), *sorted((str(left.player_id), str(right.player_id))))
                state = pair_state.setdefault(
                    key,
                    {"games": 0.0, "shared_minutes": 0.0, "last_time": game_time},
                )
                state["games"] += 1.0
                state["shared_minutes"] += min(
                    float(left.actual_regulation_minutes),
                    float(right.actual_regulation_minutes),
                )
                state["last_time"] = game_time
    additions = pd.DataFrame.from_dict(relationship, orient="index")
    output = events.join(additions)
    if (output["relationship_history_max_time"].notna() & (
        output["relationship_history_max_time"] >= output["target_game_time"]
    )).any():
        raise ValueError("Historical teammate feature used the target event")
    return output


def persist_absence_dataset(
    candidates: pd.DataFrame,
    normalized: pd.DataFrame,
    events: pd.DataFrame,
    summary: AbsenceDatasetSummary,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    derived_root: Path = DEFAULT_DERIVED_ROOT,
) -> None:
    derived_root.mkdir(parents=True, exist_ok=True)
    _write_parquet(candidates, derived_root / "phase5b_candidate_features.parquet")
    _write_parquet(normalized, derived_root / "phase5b_normalized_team_rotations.parquet")
    _write_parquet(events, derived_root / "phase5b_absence_recipient_rows.parquet")
    (derived_root / "absence_dataset_summary.json").write_text(
        json.dumps({**asdict(summary), "dataset_version": PHASE5B_DATASET_VERSION},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with connect_database(database_path) as connection:
        for name, frame in (
            ("ml_phase5b_candidate_features_v1", candidates),
            ("ml_phase5b_normalized_team_rotations_v1", normalized),
            ("ml_phase5b_absence_recipient_rows_v1", events),
        ):
            relation = f"_{name}_source"
            connection.register(relation, frame)
            try:
                connection.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {relation}")
            finally:
                connection.unregister(relation)


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if pd.notna(row[key])]
    return float(np.mean(values)) if values else np.nan


def _sum(rows: list[dict[str, Any]], key: str) -> float:
    return float(sum(float(row[key]) for row in rows if pd.notna(row[key])))


def _weighted(rows: list[dict[str, Any]], key: str, weights: np.ndarray) -> float:
    if not rows:
        return np.nan
    values = np.asarray([row[key] for row in rows], dtype=float)
    valid = np.isfinite(values)
    return float(np.average(values[valid], weights=weights[valid])) if valid.any() else np.nan


def _rate(rows: list[dict[str, Any]], key: str, minutes: float) -> float:
    return _sum(rows, key) / minutes if minutes >= 10 else np.nan


def _numeric_mean(values: list[float]) -> float:
    return float(np.mean(values)) if values else np.nan


def _std(values: list[float]) -> float:
    return float(np.std(values, ddof=1)) if len(values) >= 2 else np.nan


def _row_mean(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if pd.notna(row[key])]
    return _numeric_mean(values)


def _seconds_mean(rows: list[dict[str, Any]], key: str) -> float:
    value = _row_mean(rows, key)
    return value / 60 if pd.notna(value) else np.nan


def _ratio_sum(rows: list[dict[str, Any]], numerator: str, denominator: str) -> float:
    den = sum(float(row[denominator]) for row in rows if pd.notna(row[denominator]))
    return (sum(float(row[numerator]) for row in rows if pd.notna(row[numerator])) / den
            if den > 0 else np.nan)


def _weighted_rows(
    rows: list[dict[str, Any]], key: str, weights: np.ndarray, divisor: float
) -> float:
    if not rows:
        return np.nan
    return float(np.average(
        np.asarray([float(row[key]) / divisor for row in rows]), weights=weights
    ))


def _coalesce_series(frame: pd.DataFrame, primary: str, secondary: str) -> np.ndarray:
    return pd.to_numeric(frame[primary], errors="coerce").fillna(
        pd.to_numeric(frame[secondary], errors="coerce")
    ).fillna(0).to_numpy(float)


def _weighted_average(values: np.ndarray, weights: np.ndarray) -> float:
    return float(np.average(values, weights=weights)) if float(weights.sum()) > 0 else 0.0


def _position_minutes(frame: pd.DataFrame, position: str) -> float:
    return float(frame.loc[
        frame["broad_position"].eq(position), "baseline_expected_minutes"
    ].sum())


def _fingerprint(frame: pd.DataFrame, columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in frame.sort_values("model_row_id")[columns].itertuples(index=False, name=None):
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()
