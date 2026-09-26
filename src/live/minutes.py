"""Leakage-safe presentation and cold-start calibration for expected minutes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.db.database import connect_database


MINUTES_CALIBRATION_VERSION = "previous_season_mpg_zero_games_w30_v1"
PREVIOUS_SEASON_WEIGHT_NO_CURRENT_GAMES = 0.30


def expected_role_label(value: Any) -> str:
    """Map the existing expected-minutes role signal to a concise UI label."""

    minutes = _finite_float(value)
    if minutes is None:
        return "UNKNOWN"
    if minutes >= 28.0:
        return "STAR / PRIMARY"
    if minutes >= 22.0:
        return "STARTER"
    if minutes >= 14.0:
        return "ROTATION"
    return "BENCH / LIMITED"


def role_trend_label(value: Any) -> str | None:
    """Present the existing leakage-safe minutes-trend feature without replacing it."""

    trend = _finite_float(value)
    if trend is None:
        return None
    if trend >= 1.5:
        return "TRENDING UP"
    if trend <= -1.5:
        return "TRENDING DOWN"
    return "STABLE"


def attach_previous_season_minutes(
    frame: pd.DataFrame,
    *,
    database_path: Path | str,
    cutoff: datetime,
) -> pd.DataFrame:
    """Attach previous-season-only MPG for players in a live slate."""

    output = frame.copy()
    output["previous_season_games"] = 0
    output["previous_season_minutes_avg"] = np.nan
    output["previous_season_last5_minutes_avg"] = np.nan
    output["previous_season_history_max_time"] = pd.Series(
        [None] * len(output), index=output.index, dtype="object"
    )
    if output.empty or "season" not in output or "player_id" not in output:
        return output

    player_ids = sorted(set(output["player_id"].dropna().astype(str)))
    if not player_ids:
        return output
    for season in output["season"].dropna().astype(str).unique():
        previous = _previous_season_code(season)
        if previous is None:
            continue
        with connect_database(database_path, read_only=True) as connection:
            history = connection.execute(
                """
                WITH eligible AS (
                  SELECT stat.canonical_player_id AS player_id,
                         stat.minutes,
                         game.game_date,
                         game.game_code,
                         row_number() OVER (
                           PARTITION BY stat.canonical_player_id
                           ORDER BY game.game_date DESC, game.game_code DESC
                         ) AS recent_rank
                  FROM player_game_stats AS stat
                  JOIN games AS game USING (canonical_game_id)
                  WHERE game.season_code=?
                    AND game.played
                    AND NOT stat.did_not_play
                    AND stat.minutes IS NOT NULL
                    AND stat.minutes > 0
                    AND stat.canonical_player_id IN (SELECT unnest(?))
                )
                SELECT player_id,
                       count(*) AS previous_season_games,
                       avg(minutes) AS previous_season_minutes_avg,
                       avg(minutes) FILTER (WHERE recent_rank <= 5)
                           AS previous_season_last5_minutes_avg,
                       max(game_date) AS previous_season_history_max_time
                FROM eligible
                GROUP BY player_id
                """,
                [previous, player_ids],
            ).df()
        if history.empty:
            continue
        max_times = pd.to_datetime(history["previous_season_history_max_time"], utc=True)
        cutoff_value = pd.Timestamp(cutoff)
        cutoff_value = (
            cutoff_value.tz_localize("UTC")
            if cutoff_value.tzinfo is None else cutoff_value.tz_convert("UTC")
        )
        if max_times.notna().any() and (max_times >= cutoff_value).any():
            raise ValueError("Previous-season minutes crossed the prediction cutoff")
        history = history.set_index(history["player_id"].astype(str))
        scope = output["season"].astype(str).eq(season)
        keys = output.loc[scope, "player_id"].astype(str)
        for column in (
            "previous_season_games",
            "previous_season_minutes_avg",
            "previous_season_last5_minutes_avg",
            "previous_season_history_max_time",
        ):
            output.loc[scope, column] = keys.map(history[column]).to_numpy()
    output["previous_season_games"] = pd.to_numeric(
        output["previous_season_games"], errors="coerce"
    ).fillna(0).astype(int)
    return output


def calibrate_cold_start_minutes(
    frame: pd.DataFrame,
    *,
    minutes_column: str = "baseline_expected_minutes",
) -> pd.DataFrame:
    """Blend 30% prior-season MPG only before a current-season appearance."""

    output = frame.copy()
    raw = pd.to_numeric(output.get(minutes_column), errors="coerce")
    previous = pd.to_numeric(
        output.get("previous_season_minutes_avg"), errors="coerce"
    )
    season_games = pd.to_numeric(output.get("season_games_before"), errors="coerce")
    previous_games = pd.to_numeric(
        output.get("previous_season_games"), errors="coerce"
    ).fillna(0)
    eligible = (
        raw.notna()
        & previous.notna()
        & previous_games.gt(0)
        & season_games.eq(0)
    )
    weight = pd.Series(0.0, index=output.index, dtype=float)
    weight.loc[eligible] = PREVIOUS_SEASON_WEIGHT_NO_CURRENT_GAMES
    corrected = raw.copy()
    corrected.loc[eligible] = (
        (1.0 - weight.loc[eligible]) * raw.loc[eligible]
        + weight.loc[eligible] * previous.loc[eligible]
    )
    output["uncalibrated_expected_minutes"] = raw
    output["minutes_calibration_weight"] = weight
    output["minutes_calibration_version"] = MINUTES_CALIBRATION_VERSION
    output[minutes_column] = corrected.clip(0.0, 40.0)
    return output


def _previous_season_code(season: str) -> str | None:
    if len(season) < 2 or not season[1:].isdigit():
        return None
    return f"{season[0]}{int(season[1:]) - 1}"


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None
