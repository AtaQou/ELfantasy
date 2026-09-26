"""Frozen research contract for predictive uplift from existing data only."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .fantasy_scoring import TARGET_RULE_VERSION, scoring_rule_fingerprint
from .ml_protocol import (
    CORE_CATEGORICAL_FEATURES,
    FORBIDDEN_MODEL_INPUTS,
    INTERACTION_FEATURES as SHOT_INTERACTION_FEATURES,
    LINEUP_TEAMMATE_FEATURES,
    ONOFF_FEATURES,
    OPPONENT_SHOT_FEATURES,
    OUTER_FOLDS,
    PLAYER_SHOT_FEATURES,
    feature_manifest,
)
from .phase6b_protocol import QUANTILES, THRESHOLDS
from .phase6c_features import (
    ABSENCE_HISTORY_FEATURES,
    COACH_ROTATION_FEATURES,
    CREATION_FEATURES,
    GENERAL_OPPONENT_FEATURES,
    INTERACTION_FEATURES,
    PACE_FEATURES,
    PHASE6C_FEATURE_VERSION,
    RECENT_ROLE_FEATURES,
    ROLE_MATCHUP_FEATURES,
    USAGE_FORMULA_VERSION,
    USAGE_OFFENSIVE_FEATURES,
)


PHASE6C_PROTOCOL_VERSION = "phase6c_predictive_uplift_v1"
PHASE6C_MODEL_VERSION = "phase6c_predictive_uplift_frozen_v1"
PHASE6C_CALIBRATION_VERSION = "phase6c_chronological_calibration_v1"
PHASE6C_RANDOM_SEED = 17
DOWNSIDE_THRESHOLDS = (5.0, 10.0, 15.0)
PRIMARY_SEASONS = ("E2022", "E2023", "E2024", "E2025")
EVALUATION_SEASONS = ("E2023", "E2024", "E2025")
CONDITIONAL_TARGET = "actual_fantasy_points | actual_minutes > 0"


FAMILY_ADDITIONS: dict[str, tuple[str, ...]] = {
    "usage_offensive_involvement": USAGE_OFFENSIVE_FEATURES,
    "creation": CREATION_FEATURES,
    "expected_next_game_usage": (
        "expected_usage_next_game", *INTERACTION_FEATURES,
    ),
    "absence_role_redistribution": ABSENCE_HISTORY_FEATURES,
    "pace": PACE_FEATURES,
    "general_opponent_allowances": GENERAL_OPPONENT_FEATURES,
    "role_specific_opponent_matchup": ROLE_MATCHUP_FEATURES,
    "recent_role_changes": RECENT_ROLE_FEATURES,
    "targeted_pbp_coach_rotation": (
        *ONOFF_FEATURES, *LINEUP_TEAMMATE_FEATURES, *COACH_ROTATION_FEATURES,
    ),
    "shot_profile": (
        *PLAYER_SHOT_FEATURES, *OPPONENT_SHOT_FEATURES,
        *SHOT_INTERACTION_FEATURES,
    ),
}

FAMILY_CATEGORICAL: dict[str, tuple[str, ...]] = {
    "role_specific_opponent_matchup": ("p6c_player_archetype",),
}

PHASE6C_FORBIDDEN_INPUTS = frozenset({
    *FORBIDDEN_MODEL_INPUTS,
    "actual_usage_next_game", "actual_usage_next_game_raw",
    "actual_minutes", "actual_started",
    "points", "assists", "total_rebounds", "turnovers", "fouls_drawn",
    "number_players_out", "absent_player_ids", "absent_positions",
    "p6c_player_source_max_game_time", "p6c_opponent_source_max_game_time",
    "p6c_role_matchup_source_max_game_time", "p6c_feature_cutoff_time",
})


def phase6c_feature_manifest(
    families: str | Sequence[str],
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Return Core+Rotation plus the requested independently named families."""

    selected = (families,) if isinstance(families, str) else tuple(families)
    unknown = sorted(set(selected) - set(FAMILY_ADDITIONS))
    if unknown:
        raise KeyError(f"unknown Phase 6C families: {unknown}")
    base = feature_manifest("CORE_ROTATION", include_player_id=True)
    numeric = list(base["numeric"])
    categorical = list(base["categorical"])
    for family in selected:
        numeric.extend(FAMILY_ADDITIONS[family])
        categorical.extend(FAMILY_CATEGORICAL.get(family, ()))
    numeric = list(dict.fromkeys(numeric))
    categorical = list(dict.fromkeys(categorical))
    overlap = sorted(set(numeric) & set(categorical))
    if overlap:
        raise ValueError(f"Phase 6C numeric/categorical overlap: {overlap}")
    features = [*numeric, *categorical]
    forbidden = sorted(set(features) & PHASE6C_FORBIDDEN_INPUTS)
    if forbidden:
        raise ValueError(f"forbidden Phase 6C features: {forbidden}")
    identity = name or "+".join(selected) or "phase6b_core_rotation"
    return {
        "name": identity,
        "families": list(selected),
        "numeric": numeric,
        "categorical": categorical,
        "features": features,
        "feature_count": len(features),
        "sha256": hashlib.sha256("\n".join(features).encode()).hexdigest(),
        "includes_player_id": "player_id" in categorical,
    }


def expected_usage_feature_manifest() -> dict[str, Any]:
    """Inputs for the separately trained, conditional next-game usage model."""

    base = feature_manifest("CORE_ROTATION", include_player_id=True)
    numeric = list(dict.fromkeys([
        *base["numeric"], *USAGE_OFFENSIVE_FEATURES, *CREATION_FEATURES,
        *RECENT_ROLE_FEATURES,
    ]))
    categorical = list(dict.fromkeys([
        *base["categorical"], "p6c_player_archetype",
    ]))
    features = [*numeric, *categorical]
    forbidden = sorted(set(features) & PHASE6C_FORBIDDEN_INPUTS)
    if forbidden:
        raise ValueError(f"forbidden expected-usage inputs: {forbidden}")
    return {
        "name": "expected_usage_next_game",
        "numeric": numeric, "categorical": categorical, "features": features,
        "feature_count": len(features),
        "sha256": hashlib.sha256("\n".join(features).encode()).hexdigest(),
        "includes_player_id": True,
    }


def protocol_payload() -> dict[str, Any]:
    return {
        "protocol_version": PHASE6C_PROTOCOL_VERSION,
        "model_version": PHASE6C_MODEL_VERSION,
        "calibration_version": PHASE6C_CALIBRATION_VERSION,
        "feature_version": PHASE6C_FEATURE_VERSION,
        "usage_formula_version": USAGE_FORMULA_VERSION,
        "target_rule_version": TARGET_RULE_VERSION,
        "target_rule_fingerprint": scoring_rule_fingerprint(),
        "conditional_target": CONDITIONAL_TARGET,
        "primary_seasons": list(PRIMARY_SEASONS),
        "evaluation_seasons": list(EVALUATION_SEASONS),
        "outer_folds": [asdict(fold) for fold in OUTER_FOLDS],
        "random_seed": PHASE6C_RANDOM_SEED,
        "quantiles": list(QUANTILES),
        "upside_thresholds": list(THRESHOLDS),
        "downside_thresholds": list(DOWNSIDE_THRESHOLDS),
        "families": {
            family: phase6c_feature_manifest(family)
            for family in FAMILY_ADDITIONS
        },
        "expected_usage_manifest": expected_usage_feature_manifest(),
        "usage_oof_policy": (
            "for target season Y, train before Y-1, select iterations on Y-1, "
            "and predict Y; target-game usage is never a downstream input"
        ),
        "family_selection": (
            "independent inner-chronological ablations first; combine only "
            "families with positive inner incremental value; outer rows are evaluation only"
        ),
        "distribution_selection": (
            "retain a Phase 6C component only for chronological probabilistic "
            "improvement without unacceptable central degradation"
        ),
        "unsupported_matchup_policy": "no individual defender assignments",
    }


def protocol_fingerprint(payload: Mapping[str, Any] | None = None) -> str:
    encoded = json.dumps(
        dict(payload or protocol_payload()), sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_columns(columns: Iterable[str], available: Iterable[str]) -> None:
    missing = sorted(set(columns) - set(available))
    if missing:
        raise ValueError(f"required Phase 6C columns missing: {missing}")


def families_from_manifest(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    families = tuple(str(value) for value in manifest.get("families", ()))
    if not families or any(value not in FAMILY_ADDITIONS for value in families):
        raise ValueError("invalid retained Phase 6C family list")
    return families
