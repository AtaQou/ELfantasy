"""Discover and validate the official current EuroLeague Fantasy scope."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .fantasy_security import FANTASY_CONFIG_URL, FANTASY_SITE_URL


class FantasyConfigDiscoveryError(RuntimeError):
    """Raised when the official bootstrap cannot be retrieved or decoded."""


class FantasyConfigValidationError(ValueError):
    """Raised when a bootstrap response is not a usable current-market scope."""


@dataclass(frozen=True, slots=True)
class CurrentFantasyScope:
    competition_id: int
    players_list_id: int
    matchday_id: int
    matchday_number: int
    matchday_catalog: tuple[tuple[int, int], ...]
    configured_team_ids: frozenset[int]
    source_identifier: str
    automatically_discovered: bool


def load_current_fantasy_config(
    config_path: Path | None = None,
    *,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> tuple[Any, CurrentFantasyScope]:
    """Load an explicit fixture/override or discover the official live bootstrap.

    The official league bootstrap is public and contains no account credential. The
    authenticated saved browser state is used only for the subsequent market read.
    """

    if timeout <= 0:
        raise ValueError("Fantasy configuration timeout must be positive")
    if config_path is not None:
        path = Path(config_path)
        if not path.is_file():
            raise FantasyConfigDiscoveryError(
                f"Explicit Fantasy config file does not exist: {path}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise FantasyConfigDiscoveryError(
                "Explicit Fantasy config file could not be read"
            ) from error
        except json.JSONDecodeError as error:
            raise FantasyConfigDiscoveryError(
                "Explicit Fantasy config file is not valid JSON"
            ) from error
        return payload, parse_current_fantasy_scope(
            payload,
            source_identifier=str(path.resolve()),
            automatically_discovered=False,
        )

    owned = session is None
    http = session or requests.Session()
    try:
        try:
            response = http.get(
                FANTASY_CONFIG_URL,
                headers={
                    "Accept": "application/json",
                    "Origin": FANTASY_SITE_URL.rstrip("/"),
                    "Referer": FANTASY_SITE_URL,
                },
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise FantasyConfigDiscoveryError(
                "Official Fantasy configuration endpoint could not be reached"
            ) from error
        if response.status_code != 200:
            raise FantasyConfigDiscoveryError(
                "Official Fantasy configuration endpoint returned "
                f"HTTP {response.status_code}"
            )
        content_type = str(response.headers.get("content-type", "")).lower()
        if "json" not in content_type:
            raise FantasyConfigDiscoveryError(
                "Official Fantasy configuration endpoint did not return JSON"
            )
        try:
            payload = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as error:
            raise FantasyConfigDiscoveryError(
                "Official Fantasy configuration response was invalid JSON"
            ) from error
    finally:
        if owned:
            http.close()
    return payload, parse_current_fantasy_scope(
        payload,
        source_identifier=FANTASY_CONFIG_URL,
        automatically_discovered=True,
    )


def parse_current_fantasy_scope(
    payload: Any,
    *,
    source_identifier: str = FANTASY_CONFIG_URL,
    automatically_discovered: bool = True,
) -> CurrentFantasyScope:
    """Validate current competition/list/matchday and configured-team identifiers."""

    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise FantasyConfigValidationError(
            "Fantasy configuration has no data object"
        )
    current_matchday = data.get("current_matchday")
    if not isinstance(current_matchday, Mapping):
        raise FantasyConfigValidationError(
            "Fantasy configuration has no current matchday"
        )
    competition_id = _positive_int(
        data.get("current_competition_id"), "current competition ID"
    )
    players_list_id = _positive_int(
        data.get("current_players_list_id"), "current players-list ID"
    )
    matchday_id = _positive_int(current_matchday.get("id"), "current matchday ID")
    matchday_number = _positive_int(
        current_matchday.get("number"), "current matchday number"
    )

    matchdays = data.get("matchdays")
    if not isinstance(matchdays, list):
        raise FantasyConfigValidationError(
            "Fantasy configuration has no matchday catalog"
        )
    matchday_catalog: list[tuple[int, int]] = []
    for row in matchdays:
        if not isinstance(row, Mapping):
            raise FantasyConfigValidationError(
                "Fantasy matchday catalog contains a malformed entry"
            )
        catalog_id = _positive_int(row.get("id"), "matchday catalog ID")
        catalog_number = _positive_int(
            row.get("number"), "matchday catalog number"
        )
        matchday_catalog.append((catalog_number, catalog_id))
    if len(set(matchday_catalog)) != len(matchday_catalog):
        raise FantasyConfigValidationError(
            "Fantasy matchday catalog contains duplicate entries"
        )
    if len({row[0] for row in matchday_catalog}) != len(matchday_catalog):
        raise FantasyConfigValidationError(
            "Fantasy matchday catalog contains duplicate numbers"
        )
    if len({row[1] for row in matchday_catalog}) != len(matchday_catalog):
        raise FantasyConfigValidationError(
            "Fantasy matchday catalog contains duplicate IDs"
        )
    current_matches = [
        row for row in matchday_catalog
        if row == (matchday_number, matchday_id)
    ]
    if len(current_matches) != 1:
        raise FantasyConfigValidationError(
            "Fantasy current matchday is not uniquely present in its matchday catalog"
        )

    teams = data.get("teams")
    if not isinstance(teams, list):
        raise FantasyConfigValidationError(
            "Fantasy configuration has no team catalog"
        )
    team_ids: list[int] = []
    for team in teams:
        if not isinstance(team, Mapping):
            raise FantasyConfigValidationError(
                "Fantasy team catalog contains a malformed entry"
            )
        team_ids.append(_positive_int(team.get("id"), "Fantasy team ID"))
        if not str(team.get("name") or "").strip():
            raise FantasyConfigValidationError(
                "Fantasy team catalog contains an unnamed team"
            )
    if len(team_ids) < 10:
        raise FantasyConfigValidationError(
            f"Fantasy team catalog is implausibly small ({len(team_ids)} teams)"
        )
    if len(set(team_ids)) != len(team_ids):
        raise FantasyConfigValidationError(
            "Fantasy team catalog contains duplicate IDs"
        )
    return CurrentFantasyScope(
        competition_id=competition_id,
        players_list_id=players_list_id,
        matchday_id=matchday_id,
        matchday_number=matchday_number,
        matchday_catalog=tuple(sorted(matchday_catalog)),
        configured_team_ids=frozenset(team_ids),
        source_identifier=source_identifier,
        automatically_discovered=automatically_discovered,
    )


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise FantasyConfigValidationError(f"Fantasy {label} is invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise FantasyConfigValidationError(f"Fantasy {label} is invalid") from error
    if parsed <= 0:
        raise FantasyConfigValidationError(f"Fantasy {label} must be positive")
    return parsed
