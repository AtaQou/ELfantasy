#!/usr/bin/env python3
"""Bounded, restartable official EuroLeague season ingestion."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.remote_ingestion import ingest_official_season


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, help="Season code such as E2024")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--max-games",
        type=int,
        default=2,
        help="Representative detailed games to load (safe default: 2)",
    )
    scope.add_argument(
        "--all-games",
        action="store_true",
        help="Explicitly opt into every played detailed game in the season",
    )
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if len(args.season) != 5 or args.season[0].upper() != "E" or not args.season[1:].isdigit():
        raise SystemExit("ERROR: --season must look like E2024")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = ingest_official_season(
        int(args.season[1:]),
        database_path=args.database,
        max_detailed_games=None if args.all_games else args.max_games,
        timeout_seconds=args.timeout,
    )
    print(
        f"{result.status}: run={result.run_id} inserted={result.inserted_records} "
        f"raw_artifacts={result.raw_artifacts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
