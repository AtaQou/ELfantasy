#!/usr/bin/env python3
"""Run fixed transparent baselines on the chronological Phase 3A dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.baselines import run_baseline_backtest
from src.modeling.core_features import DEFAULT_DERIVED_ROOT, DEFAULT_SAMPLE_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--derived-root", type=Path, default=DEFAULT_DERIVED_ROOT)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    args = parser.parse_args()
    result = run_baseline_backtest(
        args.database,
        derived_root=args.derived_root,
        sample_root=args.sample_root,
    )
    best = next(
        row for row in result.metrics if row["baseline"] == result.best_mae_baseline
    )
    print(
        f"Backtested {result.row_count:,} future rows; best MAE is "
        f"{result.best_mae_baseline} ({best['mae']:.4f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
