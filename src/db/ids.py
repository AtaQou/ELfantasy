"""Deterministic canonical identifiers and conservative provider-code cleanup."""

from __future__ import annotations

import re
import uuid
from typing import Any


CANONICAL_NAMESPACE = uuid.UUID("7599249d-3caf-4fd1-9464-81fbd914a891")
_LIVE_NUMERIC_PLAYER = re.compile(r"^P(?P<digits>[0-9]+)$")


def stable_id(entity: str, *parts: Any) -> str:
    """Return a reproducible UUID for a provider entity and natural key."""

    value = "|".join([entity, *(str(part).strip() for part in parts)])
    return str(uuid.uuid5(CANONICAL_NAMESPACE, value))


def canonical_official_player_code(value: Any) -> str | None:
    """Align only the demonstrated ``P007200``/``007200`` ID variants.

    Alphanumeric legacy codes are retained exactly because stripping their
    leading ``P`` could merge unrelated identifiers.
    """

    if value is None:
        return None
    code = str(value).strip()
    if not code:
        return None
    match = _LIVE_NUMERIC_PLAYER.fullmatch(code)
    return match.group("digits") if match else code


def normalized_text(value: Any) -> str:
    """Normalize provider aliases for exact, non-fuzzy lookups."""

    return " ".join(str(value or "").casefold().split())
