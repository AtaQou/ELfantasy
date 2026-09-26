#!/usr/bin/env python3
"""Generate, validate, and diagnose the Phase 3B rich feature layer."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.rich_diagnostics import generate_rich_diagnostics
from src.modeling.rich_features import (
    RICH_DERIVED_ROOT,
    RICH_SAMPLE_ROOT,
    build_phase3b_datasets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--derived-root", type=Path, default=RICH_DERIVED_ROOT)
    parser.add_argument("--sample-root", type=Path, default=RICH_SAMPLE_ROOT)
    args = parser.parse_args()
    summary = build_phase3b_datasets(
        args.database, derived_root=args.derived_root, sample_root=args.sample_root
    )
    diagnostics = generate_rich_diagnostics(
        args.database, sample_root=args.sample_root
    )
    print(
        f"Generated {summary.core_rows:,} Core+Rich rows, "
        f"{summary.primary_eval_rows:,} primary verified rows, and "
        f"{diagnostics['feature_count']:,} rich features."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
