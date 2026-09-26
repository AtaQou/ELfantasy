#!/usr/bin/env python3
"""Collect every config-listed current-season Fantasy matchday exactly once."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

from src.data.fantasy_client import (
    FantasyAPIError,
    FantasyAuthenticationError,
    FantasyClient,
)
from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.db.fantasy_ingestion import FantasySnapshotSpec, ingest_fantasy_market_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    PROJECT_ROOT / "data" / "samples" / "fantasy_current" /
    "league_config.sanitized.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--season", default="E2025")
    parser.add_argument("--pace-seconds", type=float, default=1.0)
    return parser


def config_market_scope(payload: Any) -> tuple[int, int, int, list[tuple[int, int]]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise ValueError("Fantasy config has no data object")
    competition_id = _positive_int(data.get("current_competition_id"), "competition")
    players_list_id = _positive_int(data.get("current_players_list_id"), "players list")
    current = data.get("current_matchday")
    if not isinstance(current, Mapping):
        raise ValueError("Fantasy config has no current matchday")
    current_number = _positive_int(current.get("number"), "current matchday number")
    matchdays = data.get("matchdays")
    if not isinstance(matchdays, list) or not matchdays:
        raise ValueError("Fantasy config exposes no legitimate matchdays")
    pairs: list[tuple[int, int]] = []
    for row in matchdays:
        if not isinstance(row, Mapping):
            raise ValueError("Fantasy config matchday is malformed")
        pairs.append(
            (
                _positive_int(row.get("id"), "matchday ID"),
                _positive_int(row.get("number"), "matchday number"),
            )
        )
    if len({item[0] for item in pairs}) != len(pairs) or len(
        {item[1] for item in pairs}
    ) != len(pairs):
        raise ValueError("Fantasy config contains duplicate matchday IDs/numbers")
    return competition_id, players_list_id, current_number, sorted(
        pairs, key=lambda item: item[1]
    )


def existing_matchday_ids(database: Path, season_code: str) -> set[int]:
    if not database.is_file():
        return set()
    with connect_database(database, read_only=True) as connection:
        return {
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT matchday_id FROM fantasy_market_snapshots "
                "WHERE season_code = ?",
                [season_code],
            ).fetchall()
        }


def main() -> int:
    args = build_parser().parse_args()
    if args.pace_seconds < 0:
        raise SystemExit("ERROR: --pace-seconds must be non-negative")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    competition_id, players_list_id, current_number, matchdays = config_market_scope(
        config
    )
    existing = existing_matchday_ids(args.database, args.season)
    pending = [item for item in matchdays if item[0] not in existing]
    print(
        f"Config lists {len(matchdays)} matchdays; existing={len(matchdays)-len(pending)} "
        f"pending={len(pending)}",
        flush=True,
    )
    if not pending:
        print("No Fantasy market requests required; archive is complete.")
        return 0
    try:
        with FantasyClient.from_storage_state(timeout=30.0) as client:
            for index, (matchday_id, matchday_number) in enumerate(pending, start=1):
                observed_at = datetime.now(UTC)
                payload = client.get_market(players_list_id, matchday_id)
                result = ingest_fantasy_market_payload(
                    payload,
                    FantasySnapshotSpec(
                        season_code=args.season,
                        competition_id=competition_id,
                        players_list_id=players_list_id,
                        matchday_id=matchday_id,
                        matchday_number=matchday_number,
                        observed_at=observed_at,
                        historical_request=matchday_number != current_number,
                        status_valid_as_of_matchday=False,
                    ),
                    args.database,
                    roster_path=None,
                )
                print(
                    f"  matchday {matchday_number:02d} ({index}/{len(pending)}): "
                    f"{result.status} inserted={result.inserted_records}",
                    flush=True,
                )
                if args.pace_seconds and index != len(pending):
                    time.sleep(args.pace_seconds)
    except FantasyAuthenticationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (FantasyAPIError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Fantasy config {label} is invalid") from error
    if parsed <= 0:
        raise ValueError(f"Fantasy config {label} must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
