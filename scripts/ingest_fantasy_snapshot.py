#!/usr/bin/env python3
"""Ingest one already-sanitized Fantasy market artifact without authentication."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.fantasy_ingestion import FantasySnapshotSpec, ingest_fantasy_market_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = PROJECT_ROOT / "data" / "samples" / "e2025_rounds_1_2" / "roster_players.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season", required=True)
    parser.add_argument("--competition-id", required=True, type=int)
    parser.add_argument("--players-list-id", required=True, type=int)
    parser.add_argument("--matchday-id", required=True, type=int)
    parser.add_argument("--matchday-number", required=True, type=int)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--historical-request", action="store_true")
    parser.add_argument(
        "--status-valid-as-of-matchday",
        action="store_true",
        help="Use only with independent evidence; historical endpoints default unsafe.",
    )
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    observed_at = datetime.fromisoformat(args.observed_at.replace("Z", "+00:00"))
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    result = ingest_fantasy_market_payload(
        payload,
        FantasySnapshotSpec(
            season_code=args.season,
            competition_id=args.competition_id,
            players_list_id=args.players_list_id,
            matchday_id=args.matchday_id,
            matchday_number=args.matchday_number,
            observed_at=observed_at,
            historical_request=args.historical_request,
            status_valid_as_of_matchday=args.status_valid_as_of_matchday,
        ),
        args.database,
        roster_path=args.roster,
    )
    print(
        f"{result.status}: run={result.run_id} inserted={result.inserted_records} "
        f"raw_artifacts={result.raw_artifacts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
