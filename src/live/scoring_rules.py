"""Fail-closed compatibility gate for current Fantasy player scoring rules."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from src.modeling.fantasy_scoring import (
    TARGET_RULE_VERSION,
    scoring_rule_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCORING_EVIDENCE = (
    PROJECT_ROOT / "data" / "reference" / "fantasy_scoring" /
    "phase6a1_current_player_scoring.json"
)
SCORING_STATES = frozenset({
    "SCORING_RULES_COMPATIBLE", "SCORING_RULES_MISMATCH",
    "SCORING_RULES_UNVERIFIED",
})


@dataclass(frozen=True, slots=True)
class ScoringRuleCompatibility:
    season_code: str
    training_rule_version: str
    training_rule_fingerprint: str
    current_rule_fingerprint: str | None
    state: str
    evidence_fingerprint: str
    verified_at: str | None
    reason: str

    @property
    def production_ready(self) -> bool:
        return self.state == "SCORING_RULES_COMPATIBLE"


def resolve_scoring_rule_compatibility(
    season_code: str,
    *,
    evidence_path: Path = DEFAULT_SCORING_EVIDENCE,
) -> ScoringRuleCompatibility:
    """Compare verified current player rules with the frozen target semantics."""

    target_fingerprint = scoring_rule_fingerprint()
    try:
        raw = evidence_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        return ScoringRuleCompatibility(
            season_code, TARGET_RULE_VERSION, target_fingerprint, None,
            "SCORING_RULES_UNVERIFIED", _sha_bytes(b"MISSING:" + str(error).encode()),
            None, f"Current-season scoring evidence is unavailable: {error}",
        )
    evidence_fingerprint = _sha_bytes(raw)
    if not isinstance(payload, dict) or payload.get("season_code") != season_code:
        return ScoringRuleCompatibility(
            season_code, TARGET_RULE_VERSION, target_fingerprint, None,
            "SCORING_RULES_UNVERIFIED", evidence_fingerprint,
            payload.get("verified_at") if isinstance(payload, dict) else None,
            "Scoring evidence does not explicitly cover the requested season.",
        )
    observed = payload.get("player_scoring_rule_specification")
    current_fingerprint = (
        scoring_rule_fingerprint(observed) if isinstance(observed, dict) else None
    )
    verification = payload.get("verification_status")
    if verification != "VERIFIED_CURRENT_SEASON":
        state = "SCORING_RULES_UNVERIFIED"
        reason = str(payload.get(
            "reason", "Current-season player scoring has not been authoritatively verified."
        ))
    elif current_fingerprint != target_fingerprint:
        state = "SCORING_RULES_MISMATCH"
        reason = "Verified current player scoring differs from the frozen training target."
    else:
        state = "SCORING_RULES_COMPATIBLE"
        reason = "Verified current player scoring matches the frozen training target."
    return ScoringRuleCompatibility(
        season_code, TARGET_RULE_VERSION, target_fingerprint, current_fingerprint,
        state, evidence_fingerprint, payload.get("verified_at"), reason,
    )


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
