#!/usr/bin/env python3
"""Attach completed outcomes to an immutable Phase 6A prediction run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.evaluation import evaluate_prediction_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--no-attach", action="store_true")
    args = parser.parse_args()
    result = evaluate_prediction_run(
        args.run_id, database_path=args.database,
        attach_outcomes=not args.no_attach,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
