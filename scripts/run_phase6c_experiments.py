#!/usr/bin/env python3
"""Run Phase 6C existing-data feature ablations and freeze accepted uplift."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase6c_runner import (
    DEFAULT_BUNDLE_ROOT,
    DEFAULT_RESEARCH_ROOT,
    DEFAULT_SAMPLE_ROOT,
    run_phase6c_research,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--bundle-root", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = run_phase6c_research(
        args.database, research_root=args.research_root,
        bundle_root=args.bundle_root, sample_root=args.sample_root,
        force=args.force,
    )
    print(json.dumps({
        "status": result["status"],
        "rows": result["dataset"]["rows"],
        "outer_rows": result["dataset"]["outer_rows"],
        "retained_families": result["retained_families"],
        "phase6b_mae": result["central_comparison"]["phase6b"]["mae"],
        "phase6c_mae": result["central_comparison"]["phase6c"]["mae"],
        "deployment_bundle": result["deployment_bundle"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
