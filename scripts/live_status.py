#!/usr/bin/env python3
"""Report current live-data state without mutating it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code
from src.live.freshness import live_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    print(json.dumps(live_status(args.season, args.database), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
