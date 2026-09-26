#!/usr/bin/env python3
"""Run the frozen Phase 4B minutes/production/calibration experiment protocol."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase4b_runner import (
    PHASE4B_DERIVED_ROOT,
    PHASE4B_REPORT_ROOT,
    PHASE4B_SAMPLE_ROOT,
    run_phase4b_experiments,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--derived-root", type=Path, default=PHASE4B_DERIVED_ROOT)
    parser.add_argument("--sample-root", type=Path, default=PHASE4B_SAMPLE_ROOT)
    parser.add_argument("--report-root", type=Path, default=PHASE4B_REPORT_ROOT)
    parser.add_argument(
        "--force", action="store_true",
        help="Rerun compatible cached component and direct-loss fits.",
    )
    args = parser.parse_args()
    summary = run_phase4b_experiments(
        args.database, derived_root=args.derived_root,
        sample_root=args.sample_root, report_root=args.report_root,
        force=args.force,
    )
    final = summary["final_selection"]
    print(
        f"Frozen {final['final_performance_model_version']}: "
        f"{final['architecture']} MAE={final['metrics']['mae']:.5f} "
        f"on {summary['outer_rows']:,} conditional-on-playing rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
