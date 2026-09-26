"""Clients and parsers for the official historical Fantasy Stats surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
import json
from typing import Any, Mapping, Sequence

import requests


HISTORICAL_STATS_PAGE = (
    "https://www.dunkest.com/en/euroleague/stats/players/table"
)
HISTORICAL_STATS_API = "https://www.dunkest.com/api/stats/table"
HISTORICAL_ROUNDS_API = "https://www.dunkest.com/api/stats/dunkest/week/rounds"
HISTORICAL_STATS_PARSER_VERSION = "historical_fantasy_stats_v1"


class HistoricalFantasyStatsError(RuntimeError):
    """Raised when a public official Stats response is unusable."""


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    url: str
    content: bytes
    fetched_at: datetime
    media_type: str | None

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalFantasyStatsError(
                f"Official Stats response from {self.url} was not valid JSON"
            ) from exc


@dataclass(frozen=True, slots=True)
class HistoricalStatsPage:
    season_id: int
    season_slug: str
    season_label: str
    matchdays: tuple[int, ...]
    team_ids: tuple[int, ...]
    position_ids: tuple[int, ...]
    exposed_seasons: tuple[tuple[int, str, str], ...]


class _SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_select: str | None = None
        self.current_option: dict[str, str | bool] | None = None
        self.option_text: list[str] = []
        self.options: dict[str, list[dict[str, str | bool]]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key: (value if value is not None else True) for key, value in attrs}
        if tag == "select" and isinstance(values.get("id"), str):
            self.current_select = str(values["id"])
            self.options.setdefault(self.current_select, [])
        elif tag == "option" and self.current_select is not None:
            self.current_option = dict(values)
            self.option_text = []

    def handle_data(self, data: str) -> None:
        if self.current_option is not None:
            self.option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self.current_option is not None:
            self.current_option["text"] = " ".join(
                "".join(self.option_text).split()
            )
            assert self.current_select is not None
            self.options[self.current_select].append(self.current_option)
            self.current_option = None
            self.option_text = []
        elif tag == "select":
            self.current_select = None


def parse_historical_stats_page(content: bytes) -> HistoricalStatsPage:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoricalFantasyStatsError("Stats page was not UTF-8") from exc
    parser = _SelectParser()
    parser.feed(text)
    seasons = parser.options.get("seasonSelect", [])
    selected = [row for row in seasons if row.get("selected") is True]
    if len(selected) != 1:
        raise HistoricalFantasyStatsError("Stats page has no unique selected season")
    selected_row = selected[0]
    season_id = _positive_int(selected_row.get("value"), "season ID")
    raw_slug = selected_row.get("data-slug")
    if not isinstance(raw_slug, str) or not raw_slug.startswith("season/"):
        raise HistoricalFantasyStatsError("Stats page selected season has no slug")
    matchdays = _option_values(parser, "weeksSelect", "matchday")
    team_ids = _option_values(parser, "teamsSelect", "team")
    position_ids = _option_values(parser, "positionsSelect", "position")
    if not matchdays or len(matchdays) != len(set(matchdays)):
        raise HistoricalFantasyStatsError("Stats page matchdays are missing or duplicated")
    exposed: list[tuple[int, str, str]] = []
    for row in seasons:
        value, slug, label = row.get("value"), row.get("data-slug"), row.get("text")
        if isinstance(slug, str) and isinstance(label, str):
            exposed.append((_positive_int(value, "season ID"), slug, label))
    return HistoricalStatsPage(
        season_id=season_id,
        season_slug=raw_slug,
        season_label=str(selected_row.get("text") or ""),
        matchdays=tuple(sorted(matchdays)),
        team_ids=tuple(team_ids),
        position_ids=tuple(position_ids),
        exposed_seasons=tuple(exposed),
    )


def legacy_stats_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise HistoricalFantasyStatsError("Legacy Stats payload is not a JSON list")
    rows: list[dict[str, Any]] = []
    for value in payload:
        if not isinstance(value, Mapping):
            raise HistoricalFantasyStatsError("Legacy Stats row is not an object")
        row = dict(value)
        required = {
            "id",
            "first_name",
            "last_name",
            "team_id",
            "team_code",
            "team_name",
            "position_id",
            "position",
            "cr",
        }
        missing = sorted(required - row.keys())
        if missing:
            raise HistoricalFantasyStatsError(
                f"Legacy Stats row is missing fields: {', '.join(missing)}"
            )
        rows.append(row)
    return rows


def modern_stats_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        raise HistoricalFantasyStatsError("Modern Stats payload has no data object")
    data = payload["data"]
    columns, players = data.get("columns"), data.get("players")
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        raise HistoricalFantasyStatsError("Modern Stats columns are malformed")
    if not isinstance(players, list):
        raise HistoricalFantasyStatsError("Modern Stats players are malformed")
    records: list[dict[str, Any]] = []
    for player in players:
        if not isinstance(player, Mapping) or not isinstance(player.get("row"), list):
            raise HistoricalFantasyStatsError("Modern Stats player row is malformed")
        values = player["row"]
        if len(values) != len(columns):
            raise HistoricalFantasyStatsError("Modern Stats row/column length differs")
        records.append({"id": player.get("id"), **dict(zip(columns, values))})
    return records


class HistoricalFantasyStatsClient:
    """Read only the public Stats page routes selected by the official UI."""

    def __init__(
        self, *, timeout: float = 30.0, session: requests.Session | None = None
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Referer": HISTORICAL_STATS_PAGE,
                "User-Agent": "ELfantasy historical Stats research",
            }
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HistoricalFantasyStatsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_season_page(self, season_slug: str) -> FetchedResponse:
        if not season_slug.startswith("season/") or ".." in season_slug:
            raise ValueError("season_slug is invalid")
        return self._get(
            f"{HISTORICAL_STATS_PAGE}/{season_slug}",
            params={"iframe": "yes", "noadv": "yes"},
        )

    def get_rounds(self, season_id: int, matchday: int) -> FetchedResponse:
        _positive_int(season_id, "season ID")
        _positive_int(matchday, "matchday")
        return self._get(
            HISTORICAL_ROUNDS_API,
            params={"season_id": season_id, "week_number": matchday},
        )

    def get_matchday_stats(
        self,
        page: HistoricalStatsPage,
        matchday: int,
        rounds: Sequence[int],
    ) -> FetchedResponse:
        if matchday not in page.matchdays or not rounds:
            raise ValueError("matchday/rounds are not exposed by the Stats UI")
        params: list[tuple[str, str | int]] = [
            ("season_id", page.season_id),
            ("mode", "dunkest"),
            ("stats_type", "tot"),
            ("weeks[]", matchday),
        ]
        params.extend(("rounds[]", value) for value in rounds)
        params.extend(("teams[]", value) for value in page.team_ids)
        params.extend(("positions[]", value) for value in page.position_ids)
        params.extend(
            [
                ("player_search", ""),
                ("min_cr", "0"),
                ("max_cr", "100"),
                ("sort_by", "pdk"),
                ("sort_order", "desc"),
                ("iframe", "yes"),
            ]
        )
        return self._get(HISTORICAL_STATS_API, params=params)

    def get_date_stats(
        self,
        page: HistoricalStatsPage,
        date_from: date,
        date_to: date,
    ) -> FetchedResponse:
        if date_to < date_from:
            raise ValueError("date_to precedes date_from")
        params: list[tuple[str, str | int]] = [
            ("season_id", page.season_id),
            ("mode", "nba"),
            ("stats_type", "tot"),
            ("date_from", date_from.isoformat()),
            ("date_to", date_to.isoformat()),
        ]
        params.extend(("teams[]", value) for value in page.team_ids)
        params.extend(("positions[]", value) for value in page.position_ids)
        params.extend(
            [
                ("player_search", ""),
                ("min_cr", "0"),
                ("max_cr", "100"),
                ("sort_by", "pdk"),
                ("sort_order", "desc"),
                ("iframe", "yes"),
            ]
        )
        return self._get(HISTORICAL_STATS_API, params=params)

    def _get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> FetchedResponse:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise HistoricalFantasyStatsError("Official Stats request failed") from exc
        if response.status_code != 200:
            raise HistoricalFantasyStatsError(
                f"Official Stats request returned HTTP {response.status_code}"
            )
        return FetchedResponse(
            url=response.url,
            content=response.content,
            fetched_at=datetime.now(UTC),
            media_type=response.headers.get("content-type"),
        )


def _option_values(parser: _SelectParser, select_id: str, label: str) -> list[int]:
    return [
        _positive_int(row.get("value"), label)
        for row in parser.options.get(select_id, [])
    ]


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalFantasyStatsError(f"Invalid {label}") from exc
    if parsed <= 0:
        raise HistoricalFantasyStatsError(f"Invalid {label}")
    return parsed
