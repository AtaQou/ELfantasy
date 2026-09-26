#!/usr/bin/env python3
"""Clear one scoped Phase 5B role-limit override."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.phase5b import clear_role_limit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player_id")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season")
    parser.add_argument("--game-id")
    parser.add_argument("--matchday", type=int)
    args = parser.parse_args()
    print(clear_role_limit(
        args.player_id, database_path=args.database, season_code=args.season,
        game_id=args.game_id, fantasy_matchday=args.matchday,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
