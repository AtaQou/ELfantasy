#!/usr/bin/env python3
"""Ingest the bounded retained Phase 1 fixtures into canonical DuckDB tables."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.ingestion import ingest_retained_samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = ingest_retained_samples(args.database)
    print(
        f"{result.status}: run={result.run_id} "
        f"inserted={result.inserted_records} raw_artifacts={result.raw_artifacts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
