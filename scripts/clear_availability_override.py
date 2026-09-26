#!/usr/bin/env python3
"""Clear one scoped manual availability override by appending a CLEAR event."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.availability import clear_availability_override


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player", help="Canonical player ID or unique exact alias")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season")
    parser.add_argument("--game-id")
    parser.add_argument("--matchday", type=int)
    args = parser.parse_args()
    event_id = clear_availability_override(
        args.player, database_path=args.database, season_code=args.season,
        game_id=args.game_id, fantasy_matchday=args.matchday,
    )
    print(event_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
