#!/usr/bin/env python3
"""Audit Fantasy-to-official player identity using sanitized local data only.

Run after the authenticated market explorer has created its sanitized sample:

    python -m scripts.audit_fantasy_identity

No browser state is opened and no network request is made.  The report is
always written beside the selected market sample as
``identity_mapping_audit.json``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.fantasy_identity import (  # noqa: E402
    FantasyIdentityAuditError,
    audit_files,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET = (
    PROJECT_ROOT
    / "data"
    / "samples"
    / "fantasy_current"
    / "market_response.sanitized.json"
)
DEFAULT_ROSTER = (
    PROJECT_ROOT / "data" / "samples" / "e2025_rounds_1_2" / "roster_players.csv"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Conservatively audit Fantasy player identities against the official roster "
            "using offline, sanitized inputs."
        )
    )
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument(
        "--season-code",
        help="Official season code; inferred only when the roster contains one season",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output, report = audit_files(
            args.market,
            args.roster,
            season_code=args.season_code,
        )
    except FantasyIdentityAuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = report["summary"]
    print(f"Identity audit saved: {output}")
    print(
        "Accepted unique player matches: "
        f"{summary['accepted_unique_matches']}/{summary['fantasy_player_records']}"
    )
    print(
        "Review required: "
        f"{summary['unique_name_only_review']} name-only, "
        f"{summary['ambiguous']} ambiguous, {summary['unmatched']} unmatched"
    )
    print(f"Coaches excluded: {summary['fantasy_coach_records']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
