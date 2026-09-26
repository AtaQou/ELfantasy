#!/usr/bin/env python3
"""Incrementally update the current official EuroLeague season."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code, update_current_season


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-round-requests", type=int, default=2)
    args = parser.parse_args()
    result = update_current_season(
        args.season, database_path=args.database, timeout_seconds=args.timeout,
        include_rich=not args.no_rich, dry_run=args.dry_run,
        max_round_metadata_requests=args.max_round_requests,
    )
    print(json.dumps(asdict(result), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
