#!/usr/bin/env python3
"""Resolve current Fantasy players and write the Phase 2.5 identity report."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.database import DEFAULT_DATABASE_PATH
from src.db.identity_resolution import resolve_current_fantasy_players


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "fantasy_identity_resolution.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--season", default="E2025")
    parser.add_argument("--matchday", type=int, default=38)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = resolve_current_fantasy_players(
        args.database, season_code=args.season, matchday_number=args.matchday
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_markdown(result), encoding="utf-8")
    print(result["counts"])
    return 0


def _markdown(result: dict) -> str:
    counts = result["counts"]
    total = len(result["records"])
    matched = counts.get("MATCHED", 0)
    lines = [
        "# Fantasy identity resolution",
        "",
        f"Resolved from the complete retained official dataset at `{result['resolved_at']}`. The population is the {result['season_code']} Matchday {result['matchday_number']} player market; coaches are excluded.",
        "",
        f"- Confidently matched: **{matched}/{total} ({matched / total:.1%})**",
        f"- Ambiguous: **{counts.get('AMBIGUOUS', 0)}**",
        f"- No official candidate: **{counts.get('NO_OFFICIAL_CANDIDATE', 0)}**",
        f"- Name/team conflict: **{counts.get('NAME_TEAM_CONFLICT', 0)}**",
        f"- Other: **{counts.get('OTHER', 0)}**",
        "",
        "No edit-distance score or unconstrained fuzzy match was accepted. New matches require a unique explicit official-name variant, or a unique same-season team+jersey candidate with strong token/prefix compatibility. Previous mappings are version-closed; new mappings are append-versioned.",
        "",
        "## Unresolved players",
        "",
        "| Classification | Fantasy ID | Player | Team | Jersey | Official candidate(s) |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]
    for record in result["records"]:
        if record["classification"] == "MATCHED":
            continue
        candidates = ", ".join(
            f"{item['name']} ({item['official_player_code']})"
            for item in record["candidate_official_players"]
        ) or "—"
        lines.append(
            f"| {record['classification']} | {record['fantasy_id']} | "
            f"{record['fantasy_name']} | {record['fantasy_team']} | "
            f"{record['jersey'] or '—'} | {candidates} |"
        )
    lines += [
        "",
        "## Mapping policy",
        "",
        "- Preserve Fantasy IDs and official EuroLeague IDs in separate namespaces.",
        "- Exact provider-format reversal, suffix removal (`Jr.`, `IV`), and a trailing middle initial are explicit variants, not fuzzy matches.",
        "- A same-team/same-jersey candidate must be unique and still pass strong name compatibility.",
        "- `AMBIGUOUS`, `NAME_TEAM_CONFLICT`, and `NO_OFFICIAL_CANDIDATE` remain unmapped; a missing link is safer than a wrong player history.",
        "- Mid-season transfers are represented by memberships/game teams and never encoded into permanent player identity.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
