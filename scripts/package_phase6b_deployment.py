#!/usr/bin/env python3
"""Rebuild the frozen Phase 6B deployment fits from accepted research selections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase6b_runner import (
    DEFAULT_BUNDLE_ROOT,
    DEFAULT_SAMPLE_ROOT,
    repackage_phase6b_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--results", type=Path, default=DEFAULT_SAMPLE_ROOT / "results.json"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_BUNDLE_ROOT)
    args = parser.parse_args()
    result = repackage_phase6b_bundle(
        args.database, result_path=args.results, output_root=args.output
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
