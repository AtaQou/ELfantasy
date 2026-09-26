"""Small authenticated HTTP client for the EuroLeague Fantasy market.

The browser login remains manual.  This client only reuses the bearer token
that the official frontend has already placed in Playwright storage state.
It deliberately exposes a narrow, read-only market API and never logs or
serializes authentication material.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from .fantasy_security import (
    AUTH_STATE_PATH,
    FANTASY_API_ORIGIN,
    FANTASY_SITE_URL,
    load_private_storage_state,
)


FANTASY_SITE_ORIGIN = FANTASY_SITE_URL.rstrip("/")
_AUTH_STORAGE_KEYS = {"authToken", "flutter.authToken"}


class FantasyAuthenticationError(RuntimeError):
    """Raised when the saved browser session is missing, unusable, or expired."""


class FantasyAPIError(RuntimeError):
    """Raised for a safe-to-report Fantasy API failure."""


@dataclass(frozen=True, slots=True)
class SafeFantasyResponse:
    """Response material safe to archive; request/auth headers are excluded."""

    url: str
    content: bytes
    fetched_at: datetime
    media_type: str | None

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FantasyAPIError("Fantasy API response was not valid JSON") from exc


def extract_bearer_token(storage_state: Mapping[str, Any]) -> str:
    """Extract the frontend's token without exposing it to callers or logs.

    Playwright stores origin-local storage as name/value pairs. Flutter's web
    shared-preferences adapter may JSON-encode string values, so a single
    decoding pass is supported.
    """

    for origin in storage_state.get("origins", []):
        if not isinstance(origin, Mapping) or origin.get("origin") != FANTASY_SITE_ORIGIN:
            continue
        local_storage = origin.get("localStorage", [])
        if not isinstance(local_storage, list):
            continue
        for item in local_storage:
            if not isinstance(item, Mapping) or item.get("name") not in _AUTH_STORAGE_KEYS:
                continue
            value = item.get("value")
            if not isinstance(value, str):
                continue
            token = _decode_storage_string(value)
            if token and "\n" not in token and "\r" not in token:
                return token
    raise FantasyAuthenticationError(
        "Saved session has no reusable Fantasy API token; rerun scripts/login_fantasy.py"
    )


class FantasyClient:
    """Narrow read-only client for matchday-addressed player-market JSON."""

    def __init__(
        self,
        bearer_token: str,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not bearer_token or "\n" in bearer_token or "\r" in bearer_token:
            raise FantasyAuthenticationError("Fantasy API token is missing or malformed")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._session = session or requests.Session()

    @classmethod
    def from_storage_state(
        cls,
        path: Path = AUTH_STATE_PATH,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> "FantasyClient":
        state = load_private_storage_state(path)
        return cls(
            extract_bearer_token(state),
            timeout=timeout,
            session=session,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(authenticated=True, timeout={self._timeout!r})"

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "FantasyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_market(
        self,
        players_list_id: int,
        matchday_id: int,
        *,
        page: int = 1,
        per_page: int = -1,
    ) -> Any:
        """Return the structured market body for IDs supplied by league config."""

        if players_list_id <= 0 or matchday_id <= 0:
            raise ValueError("players_list_id and matchday_id must be positive")
        if page <= 0 or per_page == 0 or per_page < -1:
            raise ValueError("page/per_page values are invalid")
        url = (
            f"{FANTASY_API_ORIGIN}/api/v1/players-lists/{players_list_id}/"
            f"matchdays/{matchday_id}/players"
        )
        return self._get_json(
            url,
            params={"page": page, "per_page": per_page},
            resource_name="Fantasy market",
        )

    def get_player_fantasy_points(
        self,
        player_id: int,
        matchday_id: int,
        *,
        league_id: int = 10,
        language: str = "en",
    ) -> Any:
        """Read the frontend-supported fantasy-points view for one market player."""

        if player_id <= 0 or matchday_id <= 0 or league_id <= 0:
            raise ValueError("player_id, matchday_id, and league_id must be positive")
        if re.fullmatch(r"[a-z]{2}", language) is None:
            raise ValueError("language must be a two-letter lowercase code")
        url = f"{FANTASY_API_ORIGIN}/api/v1/players/{player_id}/fantasy-pts"
        return self._get_json(
            url,
            params={
                "league": league_id,
                "matchday": matchday_id,
                "lang": language,
            },
            resource_name="Fantasy player-points",
        )

    def get_competition_player_stats(
        self,
        competition_id: int,
        *,
        matchday_ids: list[int] | tuple[int, ...] = (),
        page: int = 1,
        per_page: int = 100,
        stats_type: str = "tot",
        sort_by: str = "fpt",
        sort_order: str = "desc",
    ) -> Any:
        """Read the app's paginated player Stats table.

        ``matchday_ids`` are opaque IDs exposed by the league configuration,
        not user-supplied EuroLeague round numbers.
        """

        url, params = self._competition_stats_request(
            competition_id,
            matchday_ids=matchday_ids,
            page=page,
            per_page=per_page,
            stats_type=stats_type,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return self._get_json(
            url,
            params=params,
            resource_name="Fantasy competition player Stats",
        )

    def get_competition_player_stats_response(
        self,
        competition_id: int,
        *,
        matchday_ids: list[int] | tuple[int, ...] = (),
        page: int = 1,
        per_page: int = 100,
        stats_type: str = "tot",
        sort_by: str = "fpt",
        sort_order: str = "desc",
    ) -> SafeFantasyResponse:
        """Return exact Stats bytes without ever exposing request credentials."""

        url, params = self._competition_stats_request(
            competition_id,
            matchday_ids=matchday_ids,
            page=page,
            per_page=per_page,
            stats_type=stats_type,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        response = self._request(
            url,
            params=params,
            resource_name="Fantasy competition player Stats",
        )
        return SafeFantasyResponse(
            url=response.url,
            content=response.content,
            fetched_at=datetime.now(UTC),
            media_type=response.headers.get("content-type"),
        )

    @staticmethod
    def _competition_stats_request(
        competition_id: int,
        *,
        matchday_ids: list[int] | tuple[int, ...],
        page: int,
        per_page: int,
        stats_type: str,
        sort_by: str,
        sort_order: str,
    ) -> tuple[str, dict[str, Any]]:
        if competition_id <= 0:
            raise ValueError("competition_id must be positive")
        if any(value <= 0 for value in matchday_ids):
            raise ValueError("matchday IDs must be positive")
        if page <= 0 or not 1 <= per_page <= 100:
            raise ValueError("page/per_page values are invalid")
        if stats_type not in {"avg", "tot"}:
            raise ValueError("stats_type must be avg or tot")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be asc or desc")
        url = (
            f"{FANTASY_API_ORIGIN}/api/v1/competitions/{competition_id}/"
            "stats/players/table"
        )
        params: dict[str, Any] = {
            "stats_type": stats_type,
            "quotations": "0,100",
            "page": page,
            "per_page": per_page,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        if matchday_ids:
            params["matchdays"] = ",".join(str(value) for value in matchday_ids)
        return url, params

    def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        resource_name: str,
    ) -> Any:
        response = self._request(url, params=params, resource_name=resource_name)
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise FantasyAPIError(f"{resource_name} response was not valid JSON") from exc

    def _request(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        resource_name: str,
    ) -> requests.Response:
        try:
            response = self._session.get(
                url,
                params=dict(params),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Origin": FANTASY_SITE_ORIGIN,
                    "Referer": FANTASY_SITE_URL,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise FantasyAPIError(
                "Fantasy API request failed; no authentication details were logged"
            ) from exc
        if response.status_code in {401, 403}:
            raise FantasyAuthenticationError(
                "Saved Fantasy session was rejected; rerun scripts/login_fantasy.py"
            )
        if response.status_code != 200:
            raise FantasyAPIError(
                f"{resource_name} returned HTTP {response.status_code}"
            )
        return response


def _decode_storage_string(value: str) -> str:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = value
    return decoded.strip() if isinstance(decoded, str) else ""
