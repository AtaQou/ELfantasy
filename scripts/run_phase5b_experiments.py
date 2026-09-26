#!/usr/bin/env python3
"""Run the frozen-protocol Phase 5B known-absence redistribution backtest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase5b_runner import run_phase5b


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_phase5b(args.database, force=args.force), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
