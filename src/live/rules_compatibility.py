"""Current-season compatibility attestation for the frozen Fantasy ruleset."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from src.strategy.rules import FantasyRules


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_EVIDENCE = (
    PROJECT_ROOT / "data" / "reference" / "fantasy_rules"
    / "current_rules_compatibility.json"
)


@dataclass(frozen=True, slots=True)
class RulesetCompatibility:
    season_code: str
    state: str
    reason: str
    verified_at: str | None

    @property
    def production_ready(self) -> bool:
        return self.state == "RULESET_COMPATIBLE"


def resolve_ruleset_compatibility(
    rules: FantasyRules,
    season_code: str,
    market_status: Mapping[str, Any],
    *,
    evidence_path: Path = DEFAULT_RULES_EVIDENCE,
) -> RulesetCompatibility:
    """Allow a current season only when attestation and canonical market agree."""

    try:
        rules.assert_production_allowed(season_code)
    except ValueError:
        pass
    else:
        return RulesetCompatibility(
            season_code, "RULESET_COMPATIBLE",
            "The frozen rules manifest directly permits this season.", None,
        )

    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return RulesetCompatibility(
            season_code, "RULESET_UNVERIFIED",
            f"Current-season Fantasy rules attestation is unavailable: {error}", None,
        )
    verified_at = payload.get("verified_at") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("season_code") != season_code:
        return RulesetCompatibility(
            season_code, "RULESET_UNVERIFIED",
            "Fantasy rules attestation does not cover the requested season.", verified_at,
        )
    if payload.get("verification_status") != "VERIFIED_CURRENT_SEASON":
        return RulesetCompatibility(
            season_code, "RULESET_UNVERIFIED",
            str(payload.get("reason") or "Current-season Fantasy rules are unverified."),
            verified_at,
        )
    if (
        payload.get("base_ruleset_version") != rules.ruleset_version
        or payload.get("base_rules_fingerprint") != rules.fingerprint
    ):
        return RulesetCompatibility(
            season_code, "RULESET_MISMATCH",
            "The current-season attestation does not match the loaded frozen ruleset.",
            verified_at,
        )
    market_freshness = dict(market_status.get("freshness", {})).get(
        "fantasy_market", {}
    )
    market_present = bool(
        market_status.get("fantasy_snapshot_batch_id")
        and int(market_status.get("fantasy_players") or 0) > 0
        and isinstance(market_freshness, Mapping)
        and not market_freshness.get("is_stale", True)
    )
    if payload.get("requires_current_market", True) and not market_present:
        return RulesetCompatibility(
            season_code, "RULESET_UNVERIFIED",
            "The E2026 rules attestation requires a fresh canonical E2026 Fantasy market.",
            verified_at,
        )
    return RulesetCompatibility(
        season_code, "RULESET_COMPATIBLE",
        "Verified current rules match the frozen ruleset and a fresh canonical market is present.",
        verified_at,
    )
