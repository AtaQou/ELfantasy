#!/usr/bin/env python3
"""Run the bounded Phase 6A E2025 historical technical rehearsal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.rehearsal import DEFAULT_REHEARSAL_OUTPUT, run_historical_rehearsal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--matchdays", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--output", type=Path, default=DEFAULT_REHEARSAL_OUTPUT)
    args = parser.parse_args()
    result = run_historical_rehearsal(
        database_path=args.database, matchdays=args.matchdays,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
