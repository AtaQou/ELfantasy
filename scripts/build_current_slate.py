#!/usr/bin/env python3
"""Build current upcoming player rows with frozen Phase 4B feature compatibility."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_features import build_current_slate
from src.live.current_season import current_season_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    result = build_current_slate(args.season, database_path=args.database)
    print(json.dumps(asdict(result), default=str, indent=2))
    return 0 if result.schema_compatible else 1


if __name__ == "__main__":
    raise SystemExit(main())
