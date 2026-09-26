#!/usr/bin/env python3
"""Build and persist resolved live known-absence scenarios without probabilities."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.live.phase5b import (
    attach_live_relationship_history,
    generate_availability_scenarios,
    persist_scenario,
    redistribute_live_team,
    resolve_role_limits,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--game-id")
    parser.add_argument(
        "--decision", action="append", default=[], metavar="PLAYER=DECISION",
        help="Repeatable PLAY/OUT/UNKNOWN/LIMITED scenario decision",
    )
    args = parser.parse_args()
    decisions = {}
    for item in args.decision:
        if "=" not in item:
            parser.error("--decision must be PLAYER=PLAY|OUT|UNKNOWN|LIMITED")
        player, value = item.rsplit("=", 1)
        decisions[player] = value
    with connect_database(args.database, read_only=True) as connection:
        exists = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='current_upcoming_player_slate_v1'"
        ).fetchone()[0]
        if not exists:
            print("No current slate table; run build_current_slate first.")
            return 1
        where = "season=?" + (" AND game_id=?" if args.game_id else "")
        params = [args.season] + ([args.game_id] if args.game_id else [])
        slate = connection.execute(
            f"SELECT * FROM current_upcoming_player_slate_v1 WHERE {where}", params
        ).df()
        latest = connection.execute(
            "SELECT slate_run_id FROM live_slate_runs WHERE season_code=? "
            "ORDER BY generated_at DESC LIMIT 1", [args.season],
        ).fetchone()
    if slate.empty:
        print("Current slate is empty; no scenarios generated.")
        return 0
    generated = 0
    for (game_id, team_id), team in slate.groupby(["game_id", "team_id"], sort=True):
        scenarios = generate_availability_scenarios(team, user_decisions=decisions)
        for scenario in scenarios:
            if scenario.unresolved_players:
                print(f"{game_id}/{team_id}: {scenario.warning}; "
                      f"unresolved={','.join(scenario.unresolved_players)}")
                continue
            matchday = _optional_int(team, "fantasy_matchday")
            limits = resolve_role_limits(
                team.player_id.astype(str).tolist(), database_path=args.database,
                season_code=args.season, game_id=str(game_id),
                fantasy_matchday=matchday,
            )
            cutoff = _cutoff(team)
            team_with_history = attach_live_relationship_history(
                team, scenario.decisions, database_path=args.database, cutoff=cutoff
            )
            output = redistribute_live_team(
                team_with_history, scenario.decisions, role_limits=limits
            )
            scenario_id = persist_scenario(
                output, scenario, database_path=args.database, season_code=args.season,
                game_id=str(game_id), team_id=str(team_id),
                feature_cutoff_time=cutoff,
                source_slate_run_id=str(latest[0]) if latest else None,
            )
            generated += 1
            print(f"{scenario_id} {game_id}/{team_id} {scenario.scenario_name}")
    print(f"generated={generated}")
    return 0


def _optional_int(frame: pd.DataFrame, column: str) -> int | None:
    if column not in frame or frame[column].dropna().empty:
        return None
    return int(frame[column].dropna().iloc[0])


def _cutoff(frame: pd.DataFrame) -> datetime:
    for column in ("feature_cutoff_time", "generated_at"):
        if column in frame and not frame[column].dropna().empty:
            value = pd.Timestamp(frame[column].dropna().iloc[0])
            return value.to_pydatetime().replace(
                tzinfo=value.tzinfo or UTC
            )
    return datetime.now(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
