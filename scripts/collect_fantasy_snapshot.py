#!/usr/bin/env python3
"""Collect and immediately archive one legitimate Fantasy market observation.

The script reads the protected Playwright state only through ``FantasyClient``.
It never logs, serializes, or saves request credentials.  Rerun the manual
login bootstrap if the saved session has expired.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

from src.data.fantasy_client import (
    FantasyAPIError,
    FantasyAuthenticationError,
    FantasyClient,
)
from src.db.database import DEFAULT_DATABASE_PATH
from src.db.fantasy_ingestion import FantasySnapshotSpec, ingest_fantasy_market_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = PROJECT_ROOT / "data" / "samples" / "e2025_rounds_1_2" / "roster_players.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season", required=True)
    parser.add_argument("--competition-id", required=True, type=int)
    parser.add_argument("--players-list-id", required=True, type=int)
    parser.add_argument("--matchday-id", required=True, type=int)
    parser.add_argument("--matchday-number", required=True, type=int)
    parser.add_argument("--historical-request", action="store_true")
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    observed_at = datetime.now(UTC)
    try:
        with FantasyClient.from_storage_state() as client:
            payload = client.get_market(args.players_list_id, args.matchday_id)
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
                # Conservative by default: the endpoint has no publication timestamp,
                # and old-matchday status fields are demonstrated current overlays.
                status_valid_as_of_matchday=False,
            ),
            args.database,
            roster_path=args.roster,
        )
    except FantasyAuthenticationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (FantasyAPIError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"{result.status}: safely archived matchday {args.matchday_number} at "
        f"{observed_at.isoformat()}; inserted={result.inserted_records}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
