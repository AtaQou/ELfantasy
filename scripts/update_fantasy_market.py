#!/usr/bin/env python3
"""Append one current official Fantasy market/status snapshot."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code
from src.live.fantasy import update_current_fantasy_market


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "optional sanitized/controlled config override; by default the current "
            "official league bootstrap is discovered automatically"
        ),
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    result = update_current_fantasy_market(
        args.season, args.config, database_path=args.database
    )
    print(json.dumps(asdict(result), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
