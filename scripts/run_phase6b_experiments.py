#!/usr/bin/env python3
"""Run bounded Phase 6B chronological probabilistic experiments and freeze the winner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.modeling.phase6b_runner import (
    DEFAULT_BUNDLE_ROOT,
    DEFAULT_RESEARCH_ROOT,
    DEFAULT_SAMPLE_ROOT,
    run_phase6b_research,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--research-root", type=Path, default=DEFAULT_RESEARCH_ROOT)
    parser.add_argument("--bundle-root", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = run_phase6b_research(
        args.database, research_root=args.research_root,
        bundle_root=args.bundle_root, sample_root=args.sample_root,
        force=args.force,
    )
    summary = {
        "status": result["status"],
        "primary_rows": result["dataset"]["primary_rows"],
        "outer_evaluation_rows": result["dataset"]["outer_evaluation_rows"],
        "selected_expected_fp_source": result["central_analysis"][
            "selected_expected_fp_source"
        ],
        "selected_quantile_model_family": result["selected_quantile_model_family"],
        "selected_quantile_feature_set": result["selected_quantile_feature_set"],
        "older_standardized_history_adopted": result[
            "older_standardized_history"
        ]["adopted"],
        "bundle_identifier": result["deployment_bundle"]["bundle_identifier"],
        "bundle_fingerprint": result["deployment_bundle"]["bundle_fingerprint"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
