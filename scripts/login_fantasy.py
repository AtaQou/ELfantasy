#!/usr/bin/env python3
"""Create a reusable EuroLeague Fantasy browser session by manual login.

The script never accepts a username or password. It waits for a successful
protected API response produced by the official frontend before saving state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import monotonic

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.fantasy_security import (  # noqa: E402
    AUTH_STATE_PATH,
    FANTASY_SITE_URL,
    atomic_write_private_json,
    fantasy_api_path,
    is_protected_fantasy_request,
    storage_state_has_fantasy_token,
    storage_state_has_material,
    verify_private_state_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log in manually to EuroLeague Fantasy and save private browser state."
    )
    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=15.0,
        help="Maximum time to wait for a verified login (default: 15)",
    )
    parser.add_argument(
        "--channel",
        default="chrome",
        choices=("chrome", "bundled-chromium"),
        help="Use installed Chrome or Playwright's bundled Chromium",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_minutes <= 0:
        print("ERROR: --timeout-minutes must be positive", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: Playwright is not installed. Run: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    authenticated_response_seen = False
    unauthorized_response_seen = False

    def observe_response(response: object) -> None:
        nonlocal authenticated_response_seen, unauthorized_response_seen
        try:
            url = response.url  # type: ignore[attr-defined]
            status = response.status  # type: ignore[attr-defined]
            method = response.request.method  # type: ignore[attr-defined]
        except (AttributeError, PlaywrightError):
            return
        if (
            method == "POST"
            and fantasy_api_path(url) == "/api/v1/social/login"
            and 200 <= status < 300
        ):
            # The response body contains a credential and is intentionally never read.
            authenticated_response_seen = True
            return
        if not is_protected_fantasy_request(url):
            return
        if 200 <= status < 300:
            authenticated_response_seen = True
        elif status in {401, 403}:
            unauthorized_response_seen = True

    print(
        "A visible browser will open the official EuroLeague Fantasy site.\n"
        "Log in manually in that browser; do not type credentials into this terminal.\n"
        "After login, open My Team or the player market so the frontend makes an "
        "authenticated request.\n"
        "The script will save the session automatically and will never print its contents."
    )

    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": False}
            if args.channel == "chrome":
                launch_options["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context()
            context.on("response", observe_response)
            page = context.new_page()
            page.goto(FANTASY_SITE_URL, wait_until="domcontentloaded", timeout=60_000)

            verified_state: dict[str, object] | None = None
            deadline = monotonic() + args.timeout_minutes * 60
            while monotonic() < deadline:
                if page.is_closed():
                    break
                if authenticated_response_seen:
                    candidate_state = context.storage_state(indexed_db=True)
                    if storage_state_has_fantasy_token(candidate_state):
                        verified_state = candidate_state
                        break
                page.wait_for_timeout(500)

            if verified_state is None:
                context.close()
                browser.close()
                detail = (
                    " The frontend still returned an unauthenticated response."
                    if unauthorized_response_seen
                    else ""
                )
                print(
                    "ERROR: Login could not be verified; no session state was written."
                    f"{detail} Rerun the script and open My Team or the market after login.",
                    file=sys.stderr,
                )
                return 1

            state = verified_state
            if not storage_state_has_material(state):
                context.close()
                browser.close()
                print(
                    "ERROR: Login succeeded but no reusable browser storage was found; "
                    "no state was written.",
                    file=sys.stderr,
                )
                return 1

            atomic_write_private_json(AUTH_STATE_PATH, state)
            verify_private_state_file(AUTH_STATE_PATH)
            context.close()
            browser.close()
    except PlaywrightError as exc:
        print(
            "ERROR: The browser could not complete the login flow. "
            "No credentials were logged. "
            f"Playwright reported: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nLogin cancelled; existing state was not replaced.", file=sys.stderr)
        return 130

    print("Authenticated session saved privately to .auth/euroleague.json")
    print("File permissions verified as owner-only. You may close this terminal command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
