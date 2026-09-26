#!/usr/bin/env python3
"""Resumable selective historical ingestion with storage policy guards."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.bulk_ingestion import BulkHistoryRunner
from src.db.database import DEFAULT_DATABASE_PATH


CORE_POLICY_RANGE = range(2018, 2026)
RICH_POLICY_RANGE = range(2021, 2026)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("core", "events", "all"), required=True)
    parser.add_argument("--start-season", type=int, default=2018)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--pace-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--limit-games",
        type=int,
        help="Development-only cap; omit for complete ingestion",
    )
    parser.add_argument(
        "--allow-outside-policy",
        action="store_true",
        help=(
            "Explicitly allow a non-standard window. The revised default policy is "
            "core E2018-E2025 and events E2021-E2025."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_season > args.end_season:
        raise SystemExit("ERROR: start season must not exceed end season")
    seasons = list(range(args.start_season, args.end_season + 1))
    runner = BulkHistoryRunner(
        database_path=args.database,
        pace_seconds=args.pace_seconds,
        timeout_seconds=args.timeout,
        limit_games=args.limit_games,
    )
    if args.stage in {"core", "all"}:
        _require_policy(seasons, CORE_POLICY_RANGE, "core", args.allow_outside_policy)
        runner.run(seasons, "core")
    if args.stage in {"events", "all"}:
        event_seasons = (
            [season for season in seasons if season in RICH_POLICY_RANGE]
            if args.stage == "all" and not args.allow_outside_policy
            else seasons
        )
        _require_policy(
            event_seasons, RICH_POLICY_RANGE, "events", args.allow_outside_policy
        )
        if event_seasons:
            runner.run(event_seasons, "events")
    return 0


def _require_policy(
    seasons: list[int], allowed: range, stage: str, allow_outside_policy: bool
) -> None:
    outside = [season for season in seasons if season not in allowed]
    if outside and not allow_outside_policy:
        allowed_label = f"E{allowed.start}-E{allowed.stop - 1}"
        requested = ", ".join(f"E{season}" for season in outside)
        raise SystemExit(
            f"ERROR: {stage} seasons outside revised policy ({allowed_label}): "
            f"{requested}. Use --allow-outside-policy only after an explicit decision."
        )


if __name__ == "__main__":
    raise SystemExit(main())
