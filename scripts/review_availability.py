#!/usr/bin/env python3
"""Review uncertain players and optionally store scoped PLAY/OUT/UNKNOWN/LIMITED choices."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.phase5b import apply_user_decision, review_availability


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--decision", action="append", default=[], metavar="PLAYER=DECISION",
        help="Repeatable scoped choice: PLAY, OUT, UNKNOWN, or LIMITED",
    )
    parser.add_argument("--game-id")
    parser.add_argument("--matchday", type=int)
    parser.add_argument("--expires-at", type=datetime.fromisoformat)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    review = review_availability(args.season, args.database)
    uncertain = review[review["required_decision"].eq("USER_DECISION")]
    if review.empty:
        print("No current player slate is available.")
    elif uncertain.empty:
        print("No unresolved availability decisions.")
    else:
        print(uncertain.to_string(index=False))
    decisions: list[tuple[str, str]] = []
    for item in args.decision:
        if "=" not in item:
            parser.error("--decision must be PLAYER=PLAY|OUT|UNKNOWN|LIMITED")
        decisions.append(tuple(item.rsplit("=", 1)))
    if args.interactive:
        if not sys.stdin.isatty():
            parser.error("--interactive requires a terminal")
        for row in uncertain.itertuples():
            value = input(f"{row.player} [{row.source_status}] PLAY/OUT/UNKNOWN/LIMITED: ").strip()
            if value:
                decisions.append((row.player_id, value))
    for player, decision in decisions:
        event_id = apply_user_decision(
            player, decision, database_path=args.database, season_code=args.season,
            game_id=args.game_id, fantasy_matchday=args.matchday,
            expires_at=args.expires_at,
            note="Phase 5B availability review decision",
        )
        print(f"stored {player}={decision.upper()} ({event_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
