#!/usr/bin/env python3
"""Create or migrate the local canonical DuckDB database."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH, database_version, initialize_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    applied = initialize_database(args.database)
    action = f"applied {', '.join(applied)}" if applied else "schema already current"
    print(f"DuckDB {database_version(args.database)}: {action}: {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
