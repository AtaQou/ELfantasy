#!/usr/bin/env python3
"""Capture a small sanitized Fantasy market sample from a legitimate session.

No request headers, cookies, callback URLs, or authentication values are ever
written. Historical probes are limited to matchday IDs supplied by the current
public league configuration.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.fantasy_market import (  # noqa: E402
    compare_market_payloads,
    extract_config_matchdays,
    market_records,
    representative_historical_matchdays,
    sanitized_payload_sha256,
    summarize_market_payload,
    write_sanitized_json,
)
from src.data.fantasy_client import (  # noqa: E402
    FantasyAPIError,
    FantasyAuthenticationError,
    FantasyClient,
)
from src.data.fantasy_security import (  # noqa: E402
    AUTH_STATE_PATH,
    FANTASY_API_ORIGIN,
    FANTASY_CONFIG_URL,
    FANTASY_SITE_URL,
    FantasySecurityError,
    authentication_mechanism_summary,
    market_route_parts,
    safe_endpoint_metadata,
    sanitize_for_artifact,
    verify_private_state_file,
)
from src.data.normalizers import schema_inventory  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "samples" / "fantasy_current"


@dataclass(slots=True)
class CapturedMarketResponse:
    url: str
    method: str
    status: int
    fetched_at_utc: str
    payload: Any = field(repr=False)
    request_headers: dict[str, str] = field(repr=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe and save a sanitized current Fantasy market response."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Time allowed to navigate to the player market (default: 180)",
    )
    parser.add_argument(
        "--historical-probes",
        type=int,
        default=3,
        choices=(0, 1, 2, 3),
        help="Config-driven prior matchdays to test; never more than three",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--direct-http",
        action="store_true",
        help=(
            "Use the verified read-only HTTP client after Playwright login; "
            "no browser window is needed"
        ),
    )
    parser.add_argument(
        "--channel", default="chrome", choices=("chrome", "bundled-chromium")
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        print("ERROR: --timeout-seconds must be positive", file=sys.stderr)
        return 2
    try:
        verify_private_state_file(AUTH_STATE_PATH)
    except FantasySecurityError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.direct_http:
        return _run_direct_http_discovery(args)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: Playwright is not installed. Run: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    captured: CapturedMarketResponse | None = None
    unauthorized_market_seen = False

    def observe_response(response: object) -> None:
        nonlocal captured, unauthorized_market_seen
        if captured is not None:
            return
        try:
            url = response.url  # type: ignore[attr-defined]
            status = response.status  # type: ignore[attr-defined]
            request = response.request  # type: ignore[attr-defined]
        except (AttributeError, PlaywrightError):
            return
        if market_route_parts(url) is None:
            return
        if status in {401, 403}:
            unauthorized_market_seen = True
            return
        if not (200 <= status < 300) or request.method != "GET":
            return
        try:
            content_type = response.header_value("content-type") or ""  # type: ignore[attr-defined]
            if "json" not in content_type.lower():
                return
            payload = response.json()  # type: ignore[attr-defined]
            headers = request.all_headers()
        except (PlaywrightError, ValueError):
            return
        captured = CapturedMarketResponse(
            url=url,
            method="GET",
            status=status,
            fetched_at_utc=datetime.now(UTC).isoformat(),
            payload=payload,
            request_headers={str(key): str(value) for key, value in headers.items()},
        )

    if not args.headless:
        print(
            "A browser will reuse .auth/euroleague.json. Navigate to the player market "
            "if it is not opened automatically.\n"
            "Only the allowlisted market JSON body will be saved; headers and session "
            "values are never persisted."
        )

    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": args.headless}
            if args.channel == "chrome":
                launch_options["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(storage_state=str(AUTH_STATE_PATH))
            context.on("response", observe_response)
            page = context.new_page()
            page.goto(FANTASY_SITE_URL, wait_until="domcontentloaded", timeout=60_000)

            deadline = monotonic() + args.timeout_seconds
            while monotonic() < deadline and captured is None:
                if page.is_closed():
                    break
                page.wait_for_timeout(250)

            if captured is None:
                context.close()
                browser.close()
                message = (
                    "The stored session was rejected. Rerun scripts/login_fantasy.py."
                    if unauthorized_market_seen
                    else "No market request was observed. Rerun with a visible browser and open the player market."
                )
                print(f"ERROR: {message}", file=sys.stderr)
                return 1

            sanitized_market = sanitize_for_artifact(captured.payload)
            market_summary = summarize_market_payload(sanitized_market)
            auth_summary = authentication_mechanism_summary(captured.request_headers)
            endpoint = safe_endpoint_metadata(captured.url)
            route_parts = market_route_parts(captured.url)
            assert route_parts is not None

            config_payload, config_status = _get_json(context, FANTASY_CONFIG_URL)
            sanitized_config = (
                sanitize_for_artifact(config_payload) if config_payload is not None else None
            )

            reusable_headers = _headers_for_same_session_request(
                captured.request_headers
            )
            playwright_direct_payload, playwright_direct_status = _get_json(
                context,
                captured.url,
                headers=reusable_headers,
            )
            playwright_direct_reuse = playwright_direct_status == 200 and bool(
                market_records(playwright_direct_payload)
            )

            normal_client: FantasyClient | None = None
            normal_http_payload: Any | None = None
            normal_http_status: int | None = None
            try:
                normal_client = FantasyClient.from_storage_state(
                    AUTH_STATE_PATH, timeout=30.0
                )
                normal_http_payload = normal_client.get_market(
                    route_parts["players_list_id"], route_parts["matchday_id"]
                )
                normal_http_status = 200
            except FantasyAuthenticationError:
                normal_http_status = 401
            except FantasyAPIError:
                normal_http_status = None
            normal_http_reuse = normal_http_status == 200 and bool(
                market_records(normal_http_payload)
            )

            historical_results: list[dict[str, Any]] = []
            if (normal_http_reuse or playwright_direct_reuse) and sanitized_config is not None:
                matchdays = extract_config_matchdays(sanitized_config)
                selected = representative_historical_matchdays(
                    matchdays,
                    route_parts["matchday_id"],
                    limit=args.historical_probes,
                )
                for matchday in selected:
                    historical_url = _replace_matchday_id(
                        captured.url,
                        route_parts["matchday_id"],
                        matchday["id"],
                    )
                    if normal_http_reuse and normal_client is not None:
                        try:
                            payload = normal_client.get_market(
                                route_parts["players_list_id"], matchday["id"]
                            )
                            status = 200
                        except FantasyAuthenticationError:
                            payload, status = None, 401
                        except FantasyAPIError:
                            payload, status = None, None
                    else:
                        payload, status = _get_json(
                            context,
                            historical_url,
                            headers=reusable_headers,
                        )
                    result: dict[str, Any] = {
                        "basis": "matchday ID supplied by anonymous current league config",
                        "matchday_id": matchday["id"],
                        "matchday_number": matchday["number"],
                        "status": status,
                        "endpoint": safe_endpoint_metadata(historical_url),
                    }
                    if status == 200 and payload is not None:
                        sanitized_historical = sanitize_for_artifact(payload)
                        result["response_sha256"] = sanitized_payload_sha256(
                            sanitized_historical
                        )
                        result["summary"] = summarize_market_payload(
                            sanitized_historical
                        )
                        result["comparison_with_current"] = compare_market_payloads(
                            sanitized_market, sanitized_historical
                        )
                    historical_results.append(result)

            classification = _classify_historical_prices(
                historical_results, normal_http_reuse or playwright_direct_reuse
            )
            manifest = {
                "description": (
                    "Sanitized Fantasy market discovery. No request headers, cookies, "
                    "tokens, callback URLs, or user-specific Fantasy ownership were saved."
                ),
                "captured_at_utc": captured.fetched_at_utc,
                "endpoint": endpoint,
                "method": captured.method,
                "status": captured.status,
                "route_parameters": route_parts,
                "authentication_mechanism": auth_summary,
                "playwright_storage_state_reused": True,
                "playwright_api_request_context": {
                    "tested": True,
                    "status": playwright_direct_status,
                    "successful": playwright_direct_reuse,
                    "note": (
                        "Tested inside the authenticated Playwright context; no credential "
                        "value was persisted or logged."
                    ),
                },
                "normal_authenticated_http_client": {
                    "tested": True,
                    "implementation": "requests.Session using token recovered in memory from Playwright storage state",
                    "status": normal_http_status,
                    "successful": normal_http_reuse,
                    "note": (
                        "No credential value was persisted outside .auth or logged. A successful "
                        "result means Playwright is needed for login/bootstrap, not each JSON read."
                    ),
                },
                "public_config_status": config_status,
                "historical_price_classification": classification,
                "historical_probes": historical_results,
                "saved_files": [
                    "market_response.sanitized.json",
                    "market_summary.json",
                    "discovery_manifest.json",
                ],
            }

            write_sanitized_json(
                args.output / "market_response.sanitized.json", sanitized_market
            )
            write_sanitized_json(args.output / "market_summary.json", market_summary)
            if sanitized_config is not None:
                write_sanitized_json(
                    args.output / "league_config.sanitized.json", sanitized_config
                )
                manifest["saved_files"].append("league_config.sanitized.json")
            write_sanitized_json(args.output / "discovery_manifest.json", manifest)
            if normal_client is not None:
                normal_client.close()
            context.close()
            browser.close()
    except PlaywrightError as exc:
        print(
            "ERROR: Fantasy discovery failed without logging session data. "
            f"Playwright reported: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nFantasy discovery cancelled; no session data was printed.", file=sys.stderr)
        return 130

    print(f"Sanitized Fantasy sample saved to {args.output}")
    print(f"Historical price result: {classification}")
    return 0


def _run_direct_http_discovery(args: argparse.Namespace) -> int:
    """Use the session bootstrapped by Playwright without launching a browser."""

    try:
        config_response = requests.get(
            FANTASY_CONFIG_URL,
            headers={"Accept": "application/json", "Origin": FANTASY_SITE_URL.rstrip("/")},
            timeout=30.0,
        )
        config_status = config_response.status_code
        config_payload = config_response.json() if config_status == 200 else None
    except (requests.RequestException, requests.exceptions.JSONDecodeError):
        print("ERROR: Public Fantasy configuration could not be loaded.", file=sys.stderr)
        return 1
    route_parts = _current_market_route(config_payload)
    if route_parts is None:
        print("ERROR: Current market IDs were absent from league configuration.", file=sys.stderr)
        return 1

    current_url = _market_url(
        route_parts["players_list_id"], route_parts["matchday_id"]
    )
    try:
        client = FantasyClient.from_storage_state(AUTH_STATE_PATH, timeout=30.0)
        current_payload = client.get_market(
            route_parts["players_list_id"], route_parts["matchday_id"]
        )
    except FantasyAuthenticationError:
        print(
            "ERROR: The stored session was rejected. Rerun scripts/login_fantasy.py.",
            file=sys.stderr,
        )
        return 1
    except FantasyAPIError:
        print(
            "ERROR: The authenticated market request failed without exposing session data.",
            file=sys.stderr,
        )
        return 1

    captured_at = datetime.now(UTC).isoformat()
    sanitized_market = sanitize_for_artifact(current_payload)
    sanitized_config = sanitize_for_artifact(config_payload)
    market_summary = summarize_market_payload(sanitized_market)
    historical_results: list[dict[str, Any]] = []
    matchdays = extract_config_matchdays(sanitized_config)
    selected = representative_historical_matchdays(
        matchdays,
        route_parts["matchday_id"],
        limit=args.historical_probes,
    )
    for matchday in selected:
        historical_url = _market_url(
            route_parts["players_list_id"], matchday["id"]
        )
        result: dict[str, Any] = {
            "basis": "matchday ID supplied by anonymous current league config",
            "matchday_id": matchday["id"],
            "matchday_number": matchday["number"],
            "endpoint": safe_endpoint_metadata(historical_url),
        }
        try:
            historical_payload = client.get_market(
                route_parts["players_list_id"], matchday["id"]
            )
            result["status"] = 200
            sanitized_historical = sanitize_for_artifact(historical_payload)
            result["response_sha256"] = sanitized_payload_sha256(
                sanitized_historical
            )
            result["summary"] = summarize_market_payload(sanitized_historical)
            result["comparison_with_current"] = compare_market_payloads(
                sanitized_market, sanitized_historical
            )
        except FantasyAuthenticationError:
            result["status"] = 401
        except FantasyAPIError:
            result["status"] = None
        historical_results.append(result)

    points_payload: Any | None = None
    points_summary: dict[str, Any] | None = None
    historical_points_payload: Any | None = None
    historical_points_summary: dict[str, Any] | None = None
    points_probe: dict[str, Any] = {"tested": False, "status": None}
    representative = _representative_player_record(market_records(sanitized_market))
    if representative is not None:
        fantasy_player_id = representative.get("id")
        try:
            points_payload = sanitize_for_artifact(
                client.get_player_fantasy_points(
                    int(fantasy_player_id), route_parts["matchday_id"]
                )
            )
            points_summary = _summarize_player_points(points_payload)
            points_probe = {
                "tested": True,
                "status": 200,
                "fantasy_player_id": fantasy_player_id,
                "matchday_id": route_parts["matchday_id"],
                "method": "GET",
                "path_template": "/api/v1/players/{fantasy_player_id}/fantasy-pts",
            }
            if selected:
                earliest = min(selected, key=lambda row: row["number"])
                points_probe["historical_matchday_probe"] = {
                    "status": None,
                    "matchday_id": earliest["id"],
                    "matchday_number": earliest["number"],
                }
                try:
                    historical_points_payload = sanitize_for_artifact(
                        client.get_player_fantasy_points(
                            int(fantasy_player_id), earliest["id"]
                        )
                    )
                    historical_points_summary = _summarize_player_points(
                        historical_points_payload
                    )
                    points_probe["historical_matchday_probe"]["status"] = 200
                except (FantasyAuthenticationError, FantasyAPIError):
                    pass
        except (FantasyAuthenticationError, FantasyAPIError, TypeError, ValueError):
            points_probe = {
                "tested": True,
                "status": None,
                "fantasy_player_id": fantasy_player_id,
                "matchday_id": route_parts["matchday_id"],
            }
    client.close()

    classification = _classify_historical_prices(historical_results, True)
    manifest = {
        "description": (
            "Sanitized Fantasy market discovery. No request headers, cookies, "
            "tokens, callback URLs, or user-specific Fantasy ownership were saved."
        ),
        "captured_at_utc": captured_at,
        "endpoint": safe_endpoint_metadata(current_url),
        "method": "GET",
        "status": 200,
        "route_parameters": route_parts,
        "authentication_mechanism": {
            "authorization_header_present": True,
            "authorization_scheme": "Bearer",
            "cookie_header_present": False,
            "csrf_header_present": False,
        },
        "playwright_storage_state_reused": True,
        "playwright_api_request_context": {
            "tested": False,
            "successful": False,
            "note": "Not needed in direct mode.",
        },
        "normal_authenticated_http_client": {
            "tested": True,
            "implementation": (
                "requests.Session using token recovered in memory from Playwright storage state"
            ),
            "status": 200,
            "successful": True,
            "note": (
                "No credential value was persisted outside .auth or logged. Playwright "
                "is needed for login/bootstrap, not normal JSON reads."
            ),
        },
        "public_config_status": config_status,
        "historical_price_classification": classification,
        "historical_probes": historical_results,
        "single_player_fantasy_points_probe": points_probe,
        "saved_files": [
            "market_response.sanitized.json",
            "market_summary.json",
            "league_config.sanitized.json",
            "discovery_manifest.json",
        ],
    }
    if points_payload is not None and points_summary is not None:
        manifest["saved_files"].extend(
            [
                "player_fantasy_points_sample.sanitized.json",
                "player_fantasy_points_summary.json",
            ]
        )
    if historical_points_payload is not None and historical_points_summary is not None:
        manifest["saved_files"].extend(
            [
                "player_fantasy_points_matchday_1.sanitized.json",
                "player_fantasy_points_matchday_1_summary.json",
            ]
        )
    write_sanitized_json(
        args.output / "market_response.sanitized.json", sanitized_market
    )
    write_sanitized_json(args.output / "market_summary.json", market_summary)
    write_sanitized_json(
        args.output / "league_config.sanitized.json", sanitized_config
    )
    if points_payload is not None and points_summary is not None:
        write_sanitized_json(
            args.output / "player_fantasy_points_sample.sanitized.json",
            points_payload,
        )
        write_sanitized_json(
            args.output / "player_fantasy_points_summary.json", points_summary
        )
    if historical_points_payload is not None and historical_points_summary is not None:
        write_sanitized_json(
            args.output / "player_fantasy_points_matchday_1.sanitized.json",
            historical_points_payload,
        )
        write_sanitized_json(
            args.output / "player_fantasy_points_matchday_1_summary.json",
            historical_points_summary,
        )
    write_sanitized_json(args.output / "discovery_manifest.json", manifest)
    print(f"Sanitized Fantasy sample saved to {args.output}")
    print(f"Historical price result: {classification}")
    return 0


def _current_market_route(payload: Any) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    players_list_id = data.get("current_players_list_id")
    current_matchday = data.get("current_matchday")
    matchday_id = (
        current_matchday.get("id") if isinstance(current_matchday, dict) else None
    )
    if not isinstance(players_list_id, int) or not isinstance(matchday_id, int):
        return None
    return {
        "players_list_id": players_list_id,
        "matchday_id": matchday_id,
    }


def _market_url(players_list_id: int, matchday_id: int) -> str:
    return (
        f"{FANTASY_API_ORIGIN}/api/v1/players-lists/{players_list_id}/"
        f"matchdays/{matchday_id}/players?page=1&per_page=-1"
    )


def _representative_player_record(
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    players = [
        record
        for record in records
        if not (
            isinstance(record.get("position"), dict)
            and record["position"].get("id") == 31
        )
        and isinstance(record.get("id"), int)
    ]
    return min(players, key=lambda record: record["id"]) if players else None


def _summarize_player_points(payload: Any) -> dict[str, Any]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    stats_items = data.get("stats_items", []) if isinstance(data, dict) else []
    return {
        "top_level_fields": sorted(payload) if isinstance(payload, dict) else [],
        "data_fields": sorted(data) if isinstance(data, dict) else [],
        "stats_item_names": sorted(
            {
                str(item.get("name"))
                for item in stats_items
                if isinstance(item, dict) and item.get("name") is not None
            }
        ),
        "schema_inventory": schema_inventory(payload),
    }


def _headers_for_same_session_request(headers: dict[str, str]) -> dict[str, str]:
    """Keep credentials in memory only for a same-session read-only replay."""

    normalized = {key.lower(): value for key, value in headers.items()}
    selected = {
        "Accept": "application/json",
        "Origin": FANTASY_SITE_URL.rstrip("/"),
        "Referer": FANTASY_SITE_URL,
    }
    for source, target in (
        ("authorization", "Authorization"),
        ("x-csrf-token", "X-CSRF-Token"),
        ("x-xsrf-token", "X-XSRF-Token"),
    ):
        if normalized.get(source):
            selected[target] = normalized[source]
    return selected


def _get_json(
    context: object,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[Any | None, int | None]:
    try:
        response = context.request.get(url, headers=headers or {})  # type: ignore[attr-defined]
        status = response.status
        if status != 200:
            return None, status
        return response.json(), status
    except Exception:
        # Do not surface exception text: transports may include a credentialed URL.
        return None, None


def _replace_matchday_id(url: str, current_id: int, historical_id: int) -> str:
    parsed = urlparse(url)
    old = f"/matchdays/{current_id}/players"
    new = f"/matchdays/{historical_id}/players"
    path, replacements = re.subn(re.escape(old), new, parsed.path, count=1)
    if replacements != 1:
        raise ValueError("Current market URL did not contain the expected matchday path")
    return urlunparse(parsed._replace(path=path))


def _classify_historical_prices(
    results: list[dict[str, Any]], direct_reuse: bool
) -> str:
    usable = [
        result
        for result in results
        if result.get("status") == 200
        and result.get("summary", {}).get("record_count", 0) > 0
    ]
    if usable and any(
        result.get("comparison_with_current", {}).get(
            "players_with_different_price", 0
        )
        > 0
        for result in usable
    ):
        return "B. Limited historical Fantasy prices appear available."
    if results and not usable and direct_reuse:
        return "C. Only current Fantasy prices appear available in tested routes."
    return "D. Historical Fantasy price availability remains inconclusive."


if __name__ == "__main__":
    raise SystemExit(main())
