#!/usr/bin/env python3
"""Register or conservatively collect official EuroLeague availability reports."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_season import current_season_code
from src.live.official_availability import (
    collect_official_report,
    load_source_manifest,
    register_discovered_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--include-historical", action="store_true")
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    sources = load_source_manifest(args.manifest) if args.manifest else load_source_manifest()
    register_discovered_sources(sources, args.database)
    selected = [
        source for source in sources
        if args.include_historical or source.season_code == args.season
    ]
    if args.register_only:
        print(json.dumps({"registered": len(sources), "selected": len(selected)}, indent=2))
        return 0
    results = [
        asdict(collect_official_report(source, database_path=args.database))
        for source in selected
    ]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
