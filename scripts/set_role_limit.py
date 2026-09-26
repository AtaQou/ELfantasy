#!/usr/bin/env python3
"""Store an explicit, scoped role restriction for a LIMITED player."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.phase5b import VALID_LIMIT_TYPES, set_role_limit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player_id")
    parser.add_argument("limit_type", choices=sorted(VALID_LIMIT_TYPES))
    parser.add_argument("value", type=float)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season")
    parser.add_argument("--game-id")
    parser.add_argument("--matchday", type=int)
    parser.add_argument("--expires-at", type=datetime.fromisoformat)
    parser.add_argument("--note")
    args = parser.parse_args()
    print(set_role_limit(
        args.player_id, args.limit_type, args.value, database_path=args.database,
        season_code=args.season, game_id=args.game_id,
        fantasy_matchday=args.matchday, expires_at=args.expires_at, note=args.note,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
