"""Pure state, status, and serialization helpers for the Phase 8A UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

CONTROL_CENTER_VERSION = "phase8c_control_center_polished_v1"
CONSTRAINTS = frozenset({"NORMAL", "EXCLUDE", "FORCE_INCLUDE"})
GREEN_STATUSES = frozenset({"AVAILABLE", "PROBABLE"})
YELLOW_STATUSES = frozenset({
    "QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION", "LIMITED", "UNKNOWN",
})
RED_STATUSES = frozenset({"OUT", "INJURED", "SUSPENDED", "NOT_REGISTERED"})
RANKING_VIEWS = {
    "BEST_EXPECTED_FP": ("expected_fp", False),
    "BEST_VALUE": ("fp_per_credit", False),
    "MOST_EXPECTED_MINUTES": ("expected_minutes", False),
    "HIGHEST_UPSIDE": ("p90_fp", False),
    "SAFEST": ("prob_fp_le_15", True),
    "VALUE_CREDIT_GROWTH": ("value_growth_score", False),
}


@dataclass(frozen=True, slots=True)
class ControlCenterState:
    profile_id: str = "default"
    season_code: str = "E2026"
    fantasy_matchday: int | None = None
    roster_entity_ids: tuple[str, ...] = ()
    bank_credits: float = 0.0
    transfers_available: int = 3
    scenario_id: str | None = None
    player_constraints: Mapping[str, str] = field(default_factory=dict)

    def validated(self) -> "ControlCenterState":
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if self.fantasy_matchday is not None and int(self.fantasy_matchday) < 1:
            raise ValueError("fantasy_matchday must be positive")
        if not math.isfinite(float(self.bank_credits)) or float(self.bank_credits) < 0:
            raise ValueError("bank credits must be finite and cannot be negative")
        if int(self.transfers_available) < 0:
            raise ValueError("transfers available cannot be negative")
        if len(set(map(str, self.roster_entity_ids))) != len(self.roster_entity_ids):
            raise ValueError("current roster contains duplicate entities")
        normalized = {str(key): str(value).upper() for key, value in self.player_constraints.items()}
        invalid = sorted(set(normalized.values()) - CONSTRAINTS)
        if invalid:
            raise ValueError(f"unsupported optimization constraints: {invalid}")
        return ControlCenterState(
            profile_id=self.profile_id.strip(), season_code=str(self.season_code),
            fantasy_matchday=(
                int(self.fantasy_matchday) if self.fantasy_matchday is not None else None
            ),
            roster_entity_ids=tuple(map(str, self.roster_entity_ids)),
            bank_credits=float(self.bank_credits),
            transfers_available=int(self.transfers_available),
            scenario_id=str(self.scenario_id) if self.scenario_id else None,
            player_constraints=normalized,
        )

    def as_payload(self) -> dict[str, Any]:
        value = asdict(self.validated())
        value["roster_entity_ids"] = list(value["roster_entity_ids"])
        value["player_constraints"] = dict(value["player_constraints"])
        return value

    @property
    def fingerprint(self) -> str:
        return json_fingerprint(self.as_payload())


def state_from_payload(payload: Mapping[str, Any], *, profile_id: str = "default") -> ControlCenterState:
    return ControlCenterState(
        profile_id=str(payload.get("profile_id", profile_id)),
        season_code=str(payload.get("season_code", "E2026")),
        fantasy_matchday=payload.get("fantasy_matchday"),
        roster_entity_ids=tuple(map(str, payload.get("roster_entity_ids", ()))),
        bank_credits=float(payload.get("bank_credits", 0.0)),
        transfers_available=int(payload.get("transfers_available", 3)),
        scenario_id=payload.get("scenario_id"),
        player_constraints=dict(payload.get("player_constraints", {})),
    ).validated()


def availability_presentation(status: Any) -> dict[str, str]:
    normalized = str(status or "UNKNOWN").upper()
    if normalized in GREEN_STATUSES:
        return {"group": "GREEN", "label": normalized, "icon": "check-circle"}
    if normalized in RED_STATUSES:
        return {"group": "RED", "label": normalized, "icon": "x-circle"}
    return {"group": "YELLOW", "label": normalized, "icon": "alert-triangle"}


def rank_players(
        rows: Sequence[Mapping[str, Any]], view: str = "BEST_EXPECTED_FP",
) -> list[dict[str, Any]]:
    normalized_view = str(view).upper()
    if normalized_view not in RANKING_VIEWS:
        raise ValueError(f"unknown player ranking view: {view}")
    column, ascending = RANKING_VIEWS[normalized_view]

    def key(row: Mapping[str, Any]) -> tuple[float, str]:
        value = row.get(column)
        try:
            number = float(value) if value is not None else float("nan")
        except (TypeError, ValueError):
            number = float("nan")
        if number != number:
            number = float("inf") if ascending else float("-inf")
        return number, str(row.get("name", row.get("player_id", "")))

    return [dict(row) for row in sorted(rows, key=key, reverse=not ascending)]


def json_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
