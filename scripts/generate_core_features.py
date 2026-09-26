#!/usr/bin/env python3
"""Generate reproducible leakage-safe Phase 3A core feature datasets."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.core_features import (
    DEFAULT_DERIVED_ROOT,
    DEFAULT_SAMPLE_ROOT,
    build_phase3a_datasets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--derived-root", type=Path, default=DEFAULT_DERIVED_ROOT)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    args = parser.parse_args()
    result = build_phase3a_datasets(
        args.database,
        derived_root=args.derived_root,
        sample_root=args.sample_root,
    )
    print(
        f"Generated {result.modelling_rows:,} conditional-on-playing rows "
        f"({result.e2025_price_rows:,} with validated E2025 prices)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
