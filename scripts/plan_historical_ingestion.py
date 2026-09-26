#!/usr/bin/env python3
"""Inspect season listings and write the controlled Phase 2.5 ingestion estimate."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any, Callable

from src.data.euroleague_client import APIResponse, EuroLeagueClient
from src.data.normalizers import extract_xml_rows
from src.db.raw_store import DEFAULT_RAW_ROOT, archive_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "historical_ingestion_plan.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=2007)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--pace-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_season > args.end_season:
        raise SystemExit("ERROR: start season must not be later than end season")
    if args.pace_seconds < 0:
        raise SystemExit("ERROR: pace must be non-negative")
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    with EuroLeagueClient(timeout_seconds=args.timeout) as client:
        for season in range(args.start_season, args.end_season + 1):
            code = client.season_code(season)
            schedule_text, schedule_fetched = _cached_xml(
                DEFAULT_RAW_ROOT / "euroleague" / code / "schedules" / "season.xml",
                client.schedule,
                season,
                args.pace_seconds,
            )
            results_text, results_fetched = _cached_xml(
                DEFAULT_RAW_ROOT / "euroleague" / code / "results" / "season.xml",
                client.results,
                season,
                args.pace_seconds,
            )
            schedules = extract_xml_rows(schedule_text, "item")
            results = extract_xml_rows(results_text, "game")
            completed = [
                row for row in results if str(row.get("played", "")).casefold() == "true"
            ]
            gamedays = {
                int(value)
                for row in completed
                if (value := str(row.get("gameday", ""))).isdigit()
            }
            rows.append(
                {
                    "season": code,
                    "schedule_games": len(schedules),
                    "result_games": len(results),
                    "completed_games": len(completed),
                    "gamedays": len(gamedays),
                    "schedule_fetched": schedule_fetched,
                    "results_fetched": results_fetched,
                }
            )
            print(
                f"{code}: schedule={len(schedules)} results={len(results)} "
                f"completed={len(completed)} gamedays={len(gamedays)}"
            )
    _write_report(args.report, rows, time.monotonic() - started, args.pace_seconds)
    print(f"Wrote {args.report}")
    return 0


def _cached_xml(
    path: Path,
    fetch: Callable[[int], APIResponse],
    season: int,
    pace_seconds: float,
) -> tuple[str, bool]:
    if path.is_file() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8"), False
    response = fetch(season)
    archive_bytes(
        response.text.encode("utf-8"),
        path.relative_to(DEFAULT_RAW_ROOT),
        raw_root=DEFAULT_RAW_ROOT,
    )
    if pace_seconds:
        time.sleep(pace_seconds)
    return response.text, True


def _write_report(
    path: Path,
    rows: list[dict[str, Any]],
    elapsed_seconds: float,
    pace_seconds: float,
) -> None:
    seasons = len(rows)
    completed_games = sum(row["completed_games"] for row in rows)
    round_requests = sum(row["gamedays"] for row in rows)
    season_requests = 3 * seasons  # schedule, results, roster
    core_requests = season_requests + round_requests + completed_games
    detail_requests = 2 * completed_games
    fantasy_requests = 38
    total_requests = core_requests + detail_requests + fantasy_requests
    player_rows_low = completed_games * 22
    player_rows_high = completed_games * 24
    pbp_low, pbp_high = completed_games * 425, completed_games * 575
    shots_low, shots_high = completed_games * 130, completed_games * 175
    request_floor_minutes = total_requests * pace_seconds / 60
    lines = [
        "# Historical Ingestion Plan",
        "",
        f"Generated: `{datetime.now(UTC).isoformat()}`",
        "",
        "## Controlled scope",
        "",
        f"- Detailed ML history target: **E2007–E2025 ({seasons} seasons)**.",
        f"- Completed games reported by the season results feeds: **{completed_games:,}**.",
        "- Execution order: core schedules/rosters/box scores, then play-by-play/shots, then Fantasy markets and identity review.",
        "- Existing valid raw artifacts are reused; no request is made merely to overwrite an immutable file.",
        f"- New requests use at least `{pace_seconds:.2f}s` application pacing in addition to network latency and retry backoff.",
        "",
        "## Season estimates",
        "",
        "| Season | Schedule rows | Result rows | Completed | Gamedays/round requests |",
        "| --- | ---: | ---: | ---: | ---: |",
        *[
            f"| {row['season']} | {row['schedule_games']:,} | {row['result_games']:,} | {row['completed_games']:,} | {row['gamedays']:,} |"
            for row in rows
        ],
        "",
        "## Volume estimate",
        "",
        "| Item | Estimate |",
        "| --- | ---: |",
        f"| Season-level schedule/result/roster requests | {season_requests:,} |",
        f"| v2 round requests | {round_requests:,} |",
        f"| Box-score requests | {completed_games:,} |",
        f"| Play-by-play requests | {completed_games:,} |",
        f"| Shot requests | {completed_games:,} |",
        f"| Current-season Fantasy market requests | up to {fantasy_requests:,} |",
        f"| Expected total requests | approximately {total_requests:,} |",
        f"| Expected player-game rows | {player_rows_low:,}–{player_rows_high:,} |",
        f"| Expected team-game rows | approximately {completed_games * 2:,} |",
        f"| Expected play-by-play events | {pbp_low:,}–{pbp_high:,} |",
        f"| Expected shot rows | {shots_low:,}–{shots_high:,} |",
        f"| Pacing-only time floor | {request_floor_minutes:.1f} minutes |",
        "",
        f"The listing inspection itself completed in {elapsed_seconds:.1f} seconds. Actual ingestion will be longer because response transfer, retries, JSON parsing, raw hashing, and normalization dominate the pacing-only floor.",
        "",
        "## Quality gates",
        "",
        "A season is complete only when completed-game counts match and each core game has two team rows plus usable player rows. Play-by-play and shots are reported separately so an optional detailed-feed failure cannot invalidate completed core box-score ingestion. Every failure is classified and retained for retry; anomalies remain in raw storage and are quarantined rather than rewritten.",
        "",
        "## Future Live Update Strategy",
        "",
        "The same paths support future incremental seasons: refresh mutable schedule/round observations, select only newly completed game codes whose immutable box/PBP/shot artifacts are absent, normalize them with the existing natural keys, and append—not overwrite—Fantasy snapshots and future availability events. Completed-game raw artifacts remain immutable; pre-game/current-state observations are timestamped and versioned. Rerunning the current season therefore converges without duplicate games while still allowing new completed games and new market observations to be appended. No scheduler or polling service is introduced in Phase 2.5.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
