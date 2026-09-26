#!/usr/bin/env python3
"""Launch the local EuroLeague Fantasy Control Center."""

from __future__ import annotations

import argparse
from pathlib import Path
import threading
import webbrowser

from src.control_center.http import create_server
from src.control_center.demo import DEFAULT_DEMO_SCENARIO, DEMO_SCENARIO_IDS, DemoControlCenterService
from src.control_center.service import ControlCenterService, DEFAULT_FANTASY_CONFIG
from src.db.database import (
    DEFAULT_DATABASE_PATH,
    hold_database_open,
    release_database_anchor,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--fantasy-config", type=Path, default=DEFAULT_FANTASY_CONFIG)
    parser.add_argument("--profile", default="default")
    parser.add_argument(
        "--demo", action="store_true",
        help="Use isolated in-memory UI fixtures; never reads or writes production data.",
    )
    parser.add_argument(
        "--demo-state", default=DEFAULT_DEMO_SCENARIO, choices=sorted(DEMO_SCENARIO_IDS),
        help="Initial Phase 8C demo scenario (requires --demo).",
    )
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.demo and args.demo_state != DEFAULT_DEMO_SCENARIO:
        parser.error("--demo-state can only be used together with --demo")
    database_anchor = None
    server = None
    try:
        if args.demo:
            service = DemoControlCenterService(args.demo_state)
        else:
            database_anchor = hold_database_open(args.database)
            service = ControlCenterService(
                args.database, profile_id=args.profile, fantasy_config=args.fantasy_config,
            )
        server = create_server(service, host=args.host, port=args.port)
        url = f"http://{args.host}:{args.port}"
        mode = f"DEMO ({args.demo_state})" if args.demo else "NORMAL / LIVE"
        print(f"EuroLeague Fantasy Control Center: {url} [{mode}]")
        if args.demo:
            print("Demo mode is in-memory and cannot read or write production Fantasy data.")
        print("Press Ctrl-C to stop. Frozen prediction and strategy artifacts remain read-only.")
        if not args.no_browser:
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        if database_anchor is not None:
            release_database_anchor(args.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
