#!/usr/bin/env python3
"""Run live inference through the frozen Phase 6C predictive uplift layer."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH
from src.live.current_features import build_current_slate
from src.live.current_season import current_season_code
from src.live.phase5b import apply_user_decision, review_availability, set_role_limit
from src.live.pipeline import update_live
from src.live.prediction import run_live_prediction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=current_season_code())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--fantasy-config", type=Path)
    parser.add_argument(
        "--refresh", action=argparse.BooleanOptionalAction, default=False,
        help="run Phase 5A updating first (use --no-refresh for stored-state reruns)",
    )
    parser.add_argument("--decision-file", type=Path, help="reproducible JSON decisions")
    parser.add_argument("--cutoff", type=datetime.fromisoformat)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument(
        "--allow-unverified-scoring-dry-run", action="store_true",
        help="allow clearly labelled technical predictions when current player scoring is unverified",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cutoff = args.cutoff or datetime.now(UTC)
    if cutoff.tzinfo is None:
        raise SystemExit("--cutoff must include a timezone offset")
    cutoff = cutoff.astimezone(UTC)
    runner_refresh = args.refresh
    if not args.non_interactive and args.decision_file is None and sys.stdin.isatty():
        if args.refresh:
            refreshed = update_live(
                args.season, database_path=args.database,
                fantasy_config=args.fantasy_config, as_of=cutoff,
            )
            print(
                f"Phase 5A refresh: {refreshed.status} "
                f"({refreshed.elapsed_seconds:.2f}s)"
            )
            runner_refresh = False
        build_current_slate(args.season, database_path=args.database, as_of=cutoff)
        _interactive_review(args.season, args.database, cutoff)
    keywords = {
        "database_path": args.database,
        "fantasy_config": args.fantasy_config,
        "refresh": runner_refresh,
        "decision_file": args.decision_file,
        "cutoff": cutoff,
        "allow_unverified_scoring_dry_run": args.allow_unverified_scoring_dry_run,
    }
    if args.output_root is not None:
        keywords["output_root"] = args.output_root
    result = run_live_prediction(args.season, **keywords)
    _print_result(result)
    return 0 if result.status in {
        "SUCCEEDED", "PARTIAL", "NO_CURRENT_FANTASY_SLATE",
    } else 2


def _interactive_review(season: str, database: Path, cutoff: datetime) -> None:
    review = review_availability(season, database)
    if review.empty:
        print("Availability review: no current roster slate.")
        return
    noteworthy = review[
        ~review.source_status.eq("AVAILABLE") | review.manual_override.astype(bool)
    ]
    if noteworthy.empty:
        print("Availability review: all players AVAILABLE.")
        return
    for row in noteworthy.itertuples():
        print(
            f"\n{row.team} — {row.player}\n"
            f"Status: {row.source_status}\nSource: {row.source or 'none'}\n"
            f"Updated: {row.last_updated or 'unknown'}\n"
            f"Recent expected minutes: {row.recent_expected_minutes:.1f}"
        )
        if row.required_decision != "USER_DECISION" and row.source_status != "LIMITED":
            continue
        choice = input("Choose [P]LAY [O]UT [U]NKNOWN [L]IMITED: ").strip().upper()
        decision = {"P": "PLAY", "O": "OUT", "U": "UNKNOWN", "L": "LIMITED"}.get(
            choice
        )
        if decision is None:
            print("No decision stored; player remains unresolved.")
            continue
        event_id = apply_user_decision(
            row.player_id, decision, database_path=database, season_code=season,
            fantasy_matchday=None, expires_at=pd.Timestamp(row.tip_time).to_pydatetime(),
            note="Phase 6A/6B/6C interactive availability review",
        )
        print(f"Stored {decision} ({event_id})")
        if decision == "LIMITED":
            value = input("Optional maximum minutes (blank means no restriction): ").strip()
            if value:
                set_role_limit(
                    row.player_id, "MAX_MINUTES", float(value), database_path=database,
                    season_code=season, expires_at=pd.Timestamp(row.tip_time).to_pydatetime(),
                    note="Phase 6A/6B/6C interactive explicit restriction",
                )


def _print_result(result: object) -> None:
    payload = asdict(result)
    print(json.dumps(payload, indent=2, default=str))
    csv_path = payload.get("csv_path")
    if not csv_path or not Path(csv_path).is_file():
        return
    frame = pd.read_csv(csv_path)
    scored = frame[frame.prediction_status.eq("SCORED")].copy()
    if scored.empty:
        return
    scored = scored.sort_values(["scenario_id", "expected_fp"], ascending=[True, False])
    columns = {
        "player_name": "Player", "team": "Team", "fantasy_position": "Pos",
        "credits": "Credits", "resolved_availability": "Status",
        "baseline_expected_minutes": "Exp Min",
        "adjusted_expected_minutes": "Adj Min", "expected_fp": "Exp FP",
        "median_fp": "P50", "p90_fp": "P90", "p95_fp": "P95",
        "prob_fp_ge_30": "P(30+)",
        "prob_fp_le_10": "P(<=10)",
        "fp_delta_due_to_absences": "ΔFP", "expected_fp_per_credit": "FP/Credit",
    }
    for scenario, group in scored.groupby("scenario_id", sort=True):
        print(f"\nScenario: {scenario}")
        print(group[list(columns)].rename(columns=columns).to_string(index=False))


if __name__ == "__main__":
    raise SystemExit(main())
