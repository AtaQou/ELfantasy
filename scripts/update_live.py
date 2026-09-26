#!/usr/bin/env python3
"""Run the end-to-end Phase 5A live/current-state update workflow."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code
from src.live.pipeline import update_live


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--fantasy-config", type=Path)
    parser.add_argument("--no-availability", action="store_true")
    parser.add_argument("--historical-availability", action="store_true")
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--max-round-requests", type=int, default=2)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = update_live(
        args.season, database_path=args.database,
        fantasy_config=args.fantasy_config,
        collect_official_availability=not args.no_availability,
        include_historical_availability=args.historical_availability,
        include_rich=not args.no_rich,
        max_round_metadata_requests=args.max_round_requests,
        strict=args.strict, dry_run=args.dry_run,
    )
    print(json.dumps(asdict(result), default=str, indent=2))
    return 0 if result.status in {"SUCCEEDED", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
