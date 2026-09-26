#!/usr/bin/env python3
"""Generate measured Phase 5A source, availability, and pipeline reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code
from src.live.reports import generate_phase5a_reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    for name, path in generate_phase5a_reports(args.season, args.database).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
