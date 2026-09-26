#!/usr/bin/env python3
"""Generate Phase 4A metrics and post-hoc outer-prediction diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.ml_diagnostics import generate_phase4a_diagnostics
from src.modeling.ml_runner import PHASE4A_SAMPLE_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--sample-root", type=Path, default=PHASE4A_SAMPLE_ROOT)
    args = parser.parse_args()
    result = generate_phase4a_diagnostics(
        args.database, sample_root=args.sample_root
    )
    winner = result["winner"]
    print(
        f"Winner {winner['experiment_id']} has outer MAE "
        f"{winner['metrics']['mae']:.5f} on {result['outer_rows']:,} rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
