#!/usr/bin/env python3
"""Run canonical integrity checks and optionally refresh the quality report."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.integrity import validate_database
from src.db.quality import DEFAULT_REPORT_PATH, generate_database_quality_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--no-report", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = validate_database(args.database)
    if not args.no_report:
        generate_database_quality_report(args.database, args.report)
    print(
        f"{'PASS' if result['passed'] else 'FAIL'}: "
        f"{result['check_count']} checks, {result['failure_count']} failures"
    )
    for check in result["checks"]:
        if not check["passed"]:
            print(f"  {check['name']}: observed {check['observed']} ({check['expectation']})")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
