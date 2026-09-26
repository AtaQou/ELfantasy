#!/usr/bin/env python3
"""Run fail-closed Phase 3B rich-feature quality and leakage gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.modeling.rich_features import (
    RICH_SAMPLE_ROOT,
    rich_dataset_fingerprint,
    validate_rich_cutoffs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--build-summary",
        type=Path,
        default=RICH_SAMPLE_ROOT / "rich_feature_build_summary.json",
    )
    args = parser.parse_args()
    expected = json.loads(args.build_summary.read_text(encoding="utf-8"))
    with connect_database(args.database, read_only=True) as connection:
        cutoffs = validate_rich_cutoffs(connection, fail=False)
        checks = {
            **{f"{family}_cutoff_violations": value for family, value in cutoffs.items()},
            "core_rich_row_difference": int(connection.execute(
                "SELECT (SELECT count(*) FROM ml_player_game_core_features_v1) - "
                "(SELECT count(*) FROM ml_player_game_rich_features_v1)"
            ).fetchone()[0]),
            "duplicate_model_rows": int(connection.execute(
                "SELECT count(*)-count(DISTINCT model_row_id) "
                "FROM ml_player_game_rich_features_v1"
            ).fetchone()[0]),
            "invalid_lineup_stints": int(connection.execute(
                "SELECT count(*) FROM pbp_lineup_stints_v1 WHERE duration_seconds<=0 "
                "OR list_unique([player_1_id,player_2_id,player_3_id,"
                "player_4_id,player_5_id])<>5"
            ).fetchone()[0]),
            "invalid_player_stints": int(connection.execute(
                "SELECT count(*) FROM pbp_player_stints_v1 "
                "WHERE duration_seconds<=0 OR start_second<0 OR end_second<=start_second"
            ).fetchone()[0]),
            "negative_possession_proxies": int(connection.execute(
                "SELECT count(*) FROM pbp_possessions_v1 "
                "WHERE estimated_possessions<0"
            ).fetchone()[0]),
            "usable_minute_disagreements": int(connection.execute(
                "SELECT count(*) FROM pbp_rotation_player_minute_audit_v1 "
                "WHERE rotation_usable AND NOT within_tolerance"
            ).fetchone()[0]),
            "primary_semantic_violations": int(connection.execute(
                "SELECT count(*) FROM ml_phase3b_primary_evaluation_v1 WHERE "
                "season NOT IN ('E2022','E2023','E2024','E2025') OR "
                "actual_fantasy_points IS NULL OR NOT rich_all_major_families_available"
            ).fetchone()[0]),
        }
        fingerprint = rich_dataset_fingerprint(connection)
    checks["fingerprint_mismatch"] = int(
        fingerprint != expected["dataset_fingerprint"]
    )
    failed = {name: value for name, value in checks.items() if value != 0}
    print(json.dumps({
        "passed": not failed,
        "checks": checks,
        "dataset_fingerprint": fingerprint,
    }, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
