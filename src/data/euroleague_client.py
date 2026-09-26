"""Small, raw-response client for EuroLeague's public data endpoints.

The third-party ``euroleague-api`` package is useful for interactive dataframe
work, but this client deliberately keeps transport separate from analysis and
preserves the original response body.  That makes schema auditing and later
reprocessing possible without silently accepting a wrapper's normalization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ResponseFormat = Literal["json", "xml"]


class EuroLeagueAPIError(RuntimeError):
    """Raised when a EuroLeague endpoint cannot return a usable response."""


@dataclass(frozen=True, slots=True)
class APIResponse:
    """An immutable response with both parsed data and the original body."""

    endpoint: str
    url: str
    status_code: int
    content_type: str
    fetched_at_utc: str
    text: str
    payload: Any

    @property
    def byte_count(self) -> int:
        """Return the UTF-8 byte length of the preserved response body."""

        return len(self.text.encode("utf-8"))

    @property
    def sha256(self) -> str:
        """Return a checksum that can be stored in a sample manifest."""

        return sha256(self.text.encode("utf-8")).hexdigest()


class EuroLeagueClient:
    """HTTP client covering the endpoints needed for the Phase 1 audit.

    ``season`` always means the starting year, so ``2025`` becomes ``E2025``
    for the 2025-26 EuroLeague season.  Competition ``E`` is EuroLeague and
    ``U`` is EuroCup.
    """

    API_BASE_URL = "https://api-live.euroleague.net"
    LIVE_BASE_URL = "https://live.euroleague.net/api"

    def __init__(
        self,
        competition: str = "E",
        *,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        competition = competition.upper()
        if competition not in {"E", "U"}:
            raise ValueError("competition must be 'E' (EuroLeague) or 'U' (EuroCup)")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retries < 0:
            raise ValueError("retries cannot be negative")

        self.competition = competition
        self.timeout_seconds = timeout_seconds
        self._owns_session = session is None
        self.session = session or requests.Session()

        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Accept": "application/json, application/xml, text/xml;q=0.9, */*;q=0.1",
                "User-Agent": "euroleague-fantasy-ai-data-audit/0.1",
                # The legacy live host has intermittently held reused sockets
                # open during long sequential season downloads. Fresh
                # connections are slower in theory but measurably safer and
                # more respectful for this low-rate historical collector.
                "Connection": "close",
            }
        )

    def __enter__(self) -> EuroLeagueClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally-created HTTP session."""

        if self._owns_session:
            self.session.close()

    def season_code(self, season: int) -> str:
        """Convert a starting year to the API's competition-prefixed code."""

        if isinstance(season, bool) or not isinstance(season, int):
            raise TypeError("season must be an integer starting year")
        if not 1900 <= season <= 2100:
            raise ValueError("season must be a plausible four-digit starting year")
        return f"{self.competition}{season}"

    def schedule(self, season: int) -> APIResponse:
        return self._get(
            "v1_schedule",
            f"{self.API_BASE_URL}/v1/schedules",
            params={"seasonCode": self.season_code(season)},
            response_format="xml",
        )

    def results(self, season: int) -> APIResponse:
        return self._get(
            "v1_results",
            f"{self.API_BASE_URL}/v1/results",
            params={"seasonCode": self.season_code(season)},
            response_format="xml",
        )

    def round_games(self, season: int, round_number: int) -> APIResponse:
        if round_number <= 0:
            raise ValueError("round_number must be positive")
        code = self.season_code(season)
        return self._get(
            "v2_round_games",
            f"{self.API_BASE_URL}/v2/competitions/{self.competition}/seasons/{code}/games",
            params={"roundNumber": round_number},
        )

    def season_clubs(self, season: int) -> APIResponse:
        code = self.season_code(season)
        return self._get(
            "v2_season_clubs",
            f"{self.API_BASE_URL}/v2/competitions/{self.competition}/seasons/{code}/clubs",
            params={"Limit": 500, "Offset": 0},
        )

    def season_players(self, season: int) -> APIResponse:
        """Return season player memberships and biographical details.

        This endpoint is not exposed by the requested ``euroleague-api``
        package.  The returned person code is a stable-looking identifier;
        membership records also include club, active dates and position.
        """

        return self._get(
            "v2_season_players",
            f"{self.API_BASE_URL}/v2/competitions/{self.competition}/players",
            params={
                "seasonCode": self.season_code(season),
                "Limit": 1000,
                "Offset": 0,
            },
        )

    def season_teams_xml(self, season: int) -> APIResponse:
        """Return v1 season clubs with embedded roster snapshots."""

        return self._get(
            "v1_season_teams",
            f"{self.API_BASE_URL}/v1/teams",
            params={"seasonCode": self.season_code(season)},
            response_format="xml",
        )

    def game_report(self, season: int, game_code: int) -> APIResponse:
        return self._v3_game_get(season, game_code, "report", "v3_game_report")

    def game_stats(self, season: int, game_code: int) -> APIResponse:
        return self._v3_game_get(season, game_code, "stats", "v3_game_stats")

    def game_team_comparison(self, season: int, game_code: int) -> APIResponse:
        return self._v3_game_get(
            season,
            game_code,
            "teamsComparison",
            "v3_game_team_comparison",
        )

    def game_boxscore(self, season: int, game_code: int) -> APIResponse:
        return self._live_game_get("Boxscore", "live_boxscore", season, game_code)

    def game_play_by_play(self, season: int, game_code: int) -> APIResponse:
        return self._live_game_get("PlaybyPlay", "live_play_by_play", season, game_code)

    def game_shots(self, season: int, game_code: int) -> APIResponse:
        return self._live_game_get("Points", "live_shots", season, game_code)

    def game_header(self, season: int, game_code: int) -> APIResponse:
        return self._live_game_get("Header", "live_header", season, game_code)

    def _v3_game_get(
        self,
        season: int,
        game_code: int,
        suffix: str,
        endpoint: str,
    ) -> APIResponse:
        game_code = self._validate_game_code(game_code)
        code = self.season_code(season)
        return self._get(
            endpoint,
            (
                f"{self.API_BASE_URL}/v3/competitions/{self.competition}/"
                f"seasons/{code}/games/{game_code}/{suffix}"
            ),
        )

    def _live_game_get(
        self,
        path: str,
        endpoint: str,
        season: int,
        game_code: int,
    ) -> APIResponse:
        game_code = self._validate_game_code(game_code)
        return self._get(
            endpoint,
            f"{self.LIVE_BASE_URL}/{path}",
            params={"gamecode": game_code, "seasoncode": self.season_code(season)},
        )

    @staticmethod
    def _validate_game_code(game_code: int) -> int:
        if isinstance(game_code, bool) or not isinstance(game_code, int):
            raise TypeError("game_code must be an integer")
        if game_code <= 0:
            raise ValueError("game_code must be positive")
        return game_code

    def _get(
        self,
        endpoint: str,
        url: str,
        *,
        params: dict[str, object] | None = None,
        response_format: ResponseFormat = "json",
    ) -> APIResponse:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            query = f"?{urlencode(params)}" if params else ""
            raise EuroLeagueAPIError(
                f"{endpoint} request failed for {url}{query}: {exc}"
            ) from exc

        response.encoding = response.encoding or "utf-8"
        text = response.text
        try:
            payload: Any = response.json() if response_format == "json" else text
        except requests.JSONDecodeError as exc:
            preview = text[:160].replace("\n", " ")
            raise EuroLeagueAPIError(
                f"{endpoint} returned invalid JSON from {response.url}: {preview!r}"
            ) from exc

        return APIResponse(
            endpoint=endpoint,
            url=response.url,
            status_code=response.status_code,
            content_type=response.headers.get("Content-Type", ""),
            fetched_at_utc=datetime.now(UTC).isoformat(),
            text=text,
            payload=payload,
        )
