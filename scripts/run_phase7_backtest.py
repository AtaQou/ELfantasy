#!/usr/bin/env python3
"""Generate strict E2025 slates, replay Phase 7 strategies, and freeze the winner."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.strategy.artifact import package_phase7_artifact
from src.strategy.backtest import run_e2025_backtest
from src.strategy.historical import cache_historical_predictions
from src.strategy.risk_analysis import run_tail_probability_ablations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--reuse-predictions", action="store_true")
    parser.add_argument("--training-simulations", type=int, default=64)
    parser.add_argument("--simulations", type=int, default=384)
    args = parser.parse_args()
    prediction_path = None
    if not args.reuse_predictions:
        prediction_path, _ = cache_historical_predictions(database_path=args.database)
    results, summary = run_e2025_backtest(
        database_path=args.database, prediction_path=prediction_path,
        training_simulations=args.training_simulations,
        evaluation_simulations=args.simulations,
    )
    run_tail_probability_ablations(
        database_path=args.database,
        training_simulations=args.training_simulations,
        evaluation_simulations=args.simulations,
    )
    manifest = package_phase7_artifact()
    print(
        f"Replayed {summary.matchdays_replayed} Matchdays / {len(results)} strategy rows; "
        f"frozen artifact: {manifest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
