#!/usr/bin/env python3
"""Run the frozen Phase 4A nested chronological ML experiment matrix."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.ml_runner import (
    PHASE4A_DERIVED_ROOT,
    PHASE4A_SAMPLE_ROOT,
    run_phase4a_experiments,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--derived-root", type=Path, default=PHASE4A_DERIVED_ROOT)
    parser.add_argument("--sample-root", type=Path, default=PHASE4A_SAMPLE_ROOT)
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore compatible completed experiment caches and rerun every fit.",
    )
    args = parser.parse_args()
    summary = run_phase4a_experiments(
        args.database, derived_root=args.derived_root,
        sample_root=args.sample_root, force=args.force,
    )
    print(
        f"Completed {summary['experiment_count']} experiments and persisted "
        f"{summary['prediction_rows']:,} genuine outer predictions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
