"""Security and sanitization helpers for EuroLeague Fantasy discovery.

This module deliberately has no Playwright dependency so its handling of
paths, permissions, and sanitized artifacts can be unit-tested without a real
browser or authenticated state.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTH_DIRECTORY = PROJECT_ROOT / ".auth"
AUTH_STATE_PATH = AUTH_DIRECTORY / "euroleague.json"

FANTASY_SITE_URL = "https://euroleaguefantasy.euroleaguebasketball.net/"
FANTASY_API_ORIGIN = "https://fantaking-api.dunkest.com"
FANTASY_API_HOST = "fantaking-api.dunkest.com"
FANTASY_CONFIG_URL = f"{FANTASY_API_ORIGIN}/api/v1/leagues/10/config"

MARKET_PATH_PATTERN = re.compile(
    r"^/api/v1/players-lists/(?P<players_list_id>\d+)/"
    r"matchdays/(?P<matchday_id>\d+)/players/?$"
)

_PROTECTED_PATH_PATTERNS = (
    re.compile(r"^/api/v1/players-lists/\d+/matchdays/\d+/players/?$"),
    re.compile(r"^/api/v1/fantasy-teams(?:/|$)"),
    re.compile(r"^/api/v1/competitions/\d+/stats/players(?:/|$)"),
    re.compile(r"^/api/v1/(?:users|profile|account)(?:/|$)"),
)

_SENSITIVE_KEYS = {
    "access_token",
    "accesstoken",
    "authorization",
    "cookie",
    "csrf",
    "csrf_token",
    "csrftoken",
    "email",
    "id_token",
    "jwt",
    "password",
    "phone",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "set_cookie",
    "token",
    "username",
    "xsrf_token",
}
_USER_SPECIFIC_MARKET_KEYS = {"fantasy_team"}
_SAFE_QUERY_KEYS = {"fantasy_league", "page", "per_page", "sort_by", "sort_order"}
_JWT_LIKE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$")
_JWT_ANYWHERE = re.compile(r"(?:^|[^A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_FANTASY_AUTH_STORAGE_KEYS = {"authToken", "flutter.authToken"}


class FantasySecurityError(RuntimeError):
    """Raised when authentication state or an artifact is unsafe to use."""


def fantasy_api_path(url: str) -> str | None:
    """Return the path only for the exact observed Fantasy API host."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FANTASY_API_HOST:
        return None
    return parsed.path


def market_route_parts(url: str) -> dict[str, int] | None:
    """Extract non-secret list/matchday IDs from the known market route."""

    path = fantasy_api_path(url)
    if path is None or (match := MARKET_PATH_PATTERN.fullmatch(path)) is None:
        return None
    return {key: int(value) for key, value in match.groupdict().items()}


def is_protected_fantasy_request(url: str) -> bool:
    """Whether a URL matches a protected route observed in the frontend."""

    path = fantasy_api_path(url)
    return bool(path and any(pattern.search(path) for pattern in _PROTECTED_PATH_PATTERNS))


def safe_endpoint_metadata(url: str) -> dict[str, Any]:
    """Describe an endpoint without retaining secret or unknown query values."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FANTASY_API_HOST:
        raise FantasySecurityError("Refusing to describe a non-Fantasy API URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    safe_query: dict[str, Any] = {}
    for key, values in sorted(query.items()):
        safe_query[key] = values if key in _SAFE_QUERY_KEYS else "[REDACTED]"
    return {
        "origin": FANTASY_API_ORIGIN,
        "path": parsed.path,
        "query": safe_query,
    }


def authentication_mechanism_summary(headers: Mapping[str, str]) -> dict[str, Any]:
    """Report only the presence/type of auth headers, never their values."""

    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    authorization = normalized.get("authorization", "").strip()
    scheme = None
    if authorization:
        first_word = authorization.split(maxsplit=1)[0]
        scheme = first_word if first_word.lower() in {"bearer", "basic"} else "present"
    return {
        "authorization_header_present": bool(authorization),
        "authorization_scheme": scheme,
        "cookie_header_present": bool(normalized.get("cookie")),
        "csrf_header_present": any(
            key in normalized for key in ("x-csrf-token", "x-xsrf-token")
        ),
    }


def sanitize_for_artifact(value: Any) -> Any:
    """Recursively redact credential/PII values while preserving field names."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            sanitized[key] = (
                "[REDACTED: USER-SPECIFIC]"
                if normalized_key in _USER_SPECIFIC_MARKET_KEYS
                else "[REDACTED]"
                if _is_sensitive_key(normalized_key)
                else sanitize_for_artifact(nested)
            )
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_artifact(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_artifact(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower().startswith(("bearer ", "basic ")) or _JWT_LIKE.fullmatch(
            stripped
        ):
            return "[REDACTED]"
        if _JWT_ANYWHERE.search(stripped):
            return "[REDACTED]"
        return _redact_sensitive_url_query(stripped)
    return value


def contains_sensitive_artifact_value(value: Any) -> bool:
    """Conservatively detect credentials that should never reach an artifact."""

    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = re.sub(r"[^a-z0-9]+", "_", str(raw_key).lower()).strip("_")
            if _is_sensitive_key(key) and nested != "[REDACTED]":
                return True
            if contains_sensitive_artifact_value(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_artifact_value(item) for item in value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower().startswith(("bearer ", "basic ")):
            return True
        if _JWT_LIKE.fullmatch(stripped) or _JWT_ANYWHERE.search(stripped):
            return True
        parsed = urlparse(stripped)
        return bool(
            parsed.scheme in {"http", "https"}
            and any(
                _is_sensitive_key(
                    re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
                )
                and nested != "[REDACTED]"
                for key, nested in parse_qsl(parsed.query, keep_blank_values=True)
            )
        )
    return False


def _is_sensitive_key(normalized_key: str) -> bool:
    return (
        normalized_key in _SENSITIVE_KEYS
        or normalized_key.endswith("_token")
        or normalized_key.endswith("token")
        or normalized_key.endswith("_cookie")
        or normalized_key.endswith("_secret")
        or normalized_key.startswith("session_")
    )


def _redact_sensitive_url_query(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.query:
        return value
    changed = False
    safe_pairs: list[tuple[str, str]] = []
    for key, nested in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        if _is_sensitive_key(normalized):
            nested = "[REDACTED]"
            changed = True
        safe_pairs.append((key, nested))
    if not changed:
        return value
    return urlunparse(parsed._replace(query=urlencode(safe_pairs)))


def storage_state_has_material(state: Mapping[str, Any]) -> bool:
    """Check structure only; do not inspect or expose stored secret values."""

    if state.get("cookies"):
        return True
    for origin in state.get("origins", []):
        if not isinstance(origin, Mapping):
            continue
        if origin.get("localStorage") or origin.get("indexedDB"):
            return True
    return False


def storage_state_has_fantasy_token(state: Mapping[str, Any]) -> bool:
    """Check for the frontend auth key without returning or logging its value."""

    expected_origin = FANTASY_SITE_URL.rstrip("/")
    for origin in state.get("origins", []):
        if not isinstance(origin, Mapping) or origin.get("origin") != expected_origin:
            continue
        for item in origin.get("localStorage", []):
            if (
                isinstance(item, Mapping)
                and item.get("name") in _FANTASY_AUTH_STORAGE_KEYS
                and isinstance(item.get("value"), str)
                and bool(item["value"].strip())
            ):
                return True
    return False


def atomic_write_private_json(path: Path, value: Any) -> None:
    """Atomically write JSON with directory 0700 and file 0600 permissions."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    serialized = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def verify_private_state_file(path: Path = AUTH_STATE_PATH) -> None:
    """Validate that a state file exists, is regular, and is mode 0600-ish."""

    try:
        details = path.stat()
    except FileNotFoundError as exc:
        raise FantasySecurityError(
            "Authenticated state is missing; rerun scripts/login_fantasy.py"
        ) from exc
    if not stat.S_ISREG(details.st_mode):
        raise FantasySecurityError("Authenticated state path is not a regular file")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise FantasySecurityError(
            "Authenticated state permissions are too broad; expected mode 0600"
        )
    if details.st_uid != os.getuid():
        raise FantasySecurityError("Authenticated state is owned by another user")


def load_private_storage_state(path: Path = AUTH_STATE_PATH) -> dict[str, Any]:
    """Load protected state without logging any of its contents."""

    verify_private_state_file(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FantasySecurityError(
            "Authenticated state is unreadable; rerun scripts/login_fantasy.py"
        ) from exc
    if not isinstance(state, dict) or not storage_state_has_material(state):
        raise FantasySecurityError(
            "Authenticated state has no reusable browser storage; rerun login"
        )
    return state
