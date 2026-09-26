#!/usr/bin/env python3
"""Package and validate the frozen Phase 4B deployment bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase4b_deployment import (
    DEFAULT_BUNDLE_ROOT,
    package_frozen_phase4b_bundle,
    verify_serialized_reproduction,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = (
        verify_serialized_reproduction(
            database_path=args.database, bundle_root=args.output
        )
        if args.verify_only else
        package_frozen_phase4b_bundle(
            args.database, output_root=args.output, force=args.force
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    passed = result.get("passed", result.get("reproduction_gate", {}).get("passed"))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
