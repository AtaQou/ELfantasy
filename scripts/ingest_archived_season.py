#!/usr/bin/env python3
"""Rebuild a retained season from local immutable raw artifacts only."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.remote_ingestion import ingest_archived_official_season


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, help="Season code such as E2024")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--components",
        default="core,play_by_play,shots",
        help="Comma-separated: core,play_by_play,shots",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if len(args.season) != 5 or args.season[0].upper() != "E" or not args.season[1:].isdigit():
        raise SystemExit("ERROR: --season must look like E2024")
    result = ingest_archived_official_season(
        int(args.season[1:]),
        database_path=args.database,
        components=frozenset(
            component.strip()
            for component in args.components.split(",")
            if component.strip()
        ),
    )
    print(
        f"{result.status}: run={result.run_id} inserted={result.inserted_records} "
        f"raw_artifacts={result.raw_artifacts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
