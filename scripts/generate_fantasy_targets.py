#!/usr/bin/env python3
"""Generate and validate the versioned Phase 3A Fantasy scoring target."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.core_features import DEFAULT_SAMPLE_ROOT, generate_targets
from src.modeling.scoring_validation import validate_scoring_engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    args = parser.parse_args()
    generate_targets(args.database)
    result = validate_scoring_engine(args.database, sample_root=args.sample_root)
    validation = result["canonical_base_formula_vs_pir"]
    print(
        "Generated scoring targets; explicit base formula matches canonical PIR "
        f"for {validation['exact']:,}/{validation['rows']:,} rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
