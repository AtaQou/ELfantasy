#!/usr/bin/env python3
"""Set a scoped manual availability override without changing historical facts."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.availability import AVAILABILITY_STATUSES, REASON_CATEGORIES, set_availability_override


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player", help="Canonical player ID or unique exact alias")
    parser.add_argument("status", choices=AVAILABILITY_STATUSES)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season")
    parser.add_argument("--game-id")
    parser.add_argument("--matchday", type=int)
    parser.add_argument("--expires-at", type=datetime.fromisoformat)
    parser.add_argument("--reason", choices=REASON_CATEGORIES, default="UNKNOWN")
    parser.add_argument("--note")
    args = parser.parse_args()
    event_id = set_availability_override(
        args.player, args.status, database_path=args.database,
        season_code=args.season, game_id=args.game_id,
        fantasy_matchday=args.matchday, expires_at=args.expires_at,
        reason_category=args.reason, note=args.note,
    )
    print(event_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
