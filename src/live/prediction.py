"""Live prediction service with frozen Phase 6B and Phase 6C inference.

The service composes Phase 5A refresh/features, explicit availability scenarios,
the frozen Phase 5B minute redistribution, and frozen Phase 4B inference.  It
never trains a model and never estimates participation probabilities.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.modeling.ml_protocol import feature_manifest
from src.modeling.phase4b_protocol import phase4b_feature_manifest
from src.modeling.phase6c_features import USAGE_OFFENSIVE_FEATURES

from .current_features import (
    LIVE_FEATURE_VERSION,
    build_current_slate,
    frozen_required_features,
)
from .freshness import source_freshness
from .minutes import (
    MINUTES_CALIBRATION_VERSION,
    attach_previous_season_minutes,
    calibrate_cold_start_minutes,
    expected_role_label,
)
from .phase5b import (
    AvailabilityScenario,
    DEFAULT_MODEL_ROOT,
    attach_live_relationship_history,
    generate_availability_scenarios,
    redistribute_live_team,
    resolve_role_limits,
)
from .pipeline import update_live
from .preparation import apply_preparation_context, PREPARATION_CONTEXT_VERSION
from .prediction_artifacts import (
    DEFAULT_PHASE4B_ARTIFACT_ROOT,
    ArtifactValidation,
    FrozenPerformanceBundle,
    ModelArtifactMismatch,
    load_and_validate_frozen_artifacts,
)
from .probabilistic_artifacts import (
    DEFAULT_PHASE6B_ARTIFACT_ROOT,
    FrozenProbabilisticBundle,
    ProbabilisticArtifactValidation,
    load_and_validate_probabilistic_artifact,
)
from .predictive_uplift_artifacts import (
    DEFAULT_PHASE6C_ARTIFACT_ROOT,
    FrozenPredictiveUpliftBundle,
    PredictiveUpliftArtifactValidation,
    load_and_validate_predictive_uplift_artifact,
)
from .scoring_rules import (
    ScoringRuleCompatibility,
    resolve_scoring_rule_compatibility,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "predictions"
PHASE6A_PROTOCOL_VERSION = "phase6a1_live_prediction_runner_v1"
MATCHDAY_STATES = frozenset({
    "MATCHDAY_RESOLVED", "MATCHDAY_AMBIGUOUS", "MARKET_NOT_AVAILABLE",
})
RUN_STATUSES = frozenset({
    "SUCCEEDED", "PARTIAL", "NO_CURRENT_FANTASY_SLATE",
    "MATCHDAY_AMBIGUOUS", "AVAILABILITY_UNRESOLVED",
    "MODEL_ARTIFACT_MISMATCH", "FAILED",
})
MAX_EXPLICIT_SCENARIOS = 64


@dataclass(frozen=True, slots=True)
class MatchdayResolution:
    status: str
    fantasy_matchday: int | None
    competition_round: int | None
    method: str | None
    market_snapshot_batch_id: str | None
    market_player_count: int
    market_fingerprint: str | None
    message: str


@dataclass(frozen=True, slots=True)
class DecisionConfig:
    player_decisions: dict[str, str]
    scenarios: tuple[tuple[str, dict[str, str]], ...]
    manual_role_estimates: dict[str, float]
    default_unknown_decision: str | None
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class PlayerPoolResult:
    frame: pd.DataFrame
    market_players: int
    mapped_players: int
    roster_matched_players: int
    mapping_ambiguous: int
    mapping_unmatched: int
    roster_mismatches: int


@dataclass(frozen=True, slots=True)
class LivePredictionResult:
    prediction_run_id: str
    status: str
    season_code: str
    prediction_generated_at: datetime
    fantasy_matchday_status: str
    fantasy_matchday: int | None
    scenarios: int
    coverage: dict[str, Any]
    timings: dict[str, float]
    warnings: tuple[str, ...]
    json_path: str | None
    csv_path: str | None
    error_code: str | None = None
    error_message: str | None = None
    reused_immutable_snapshot: bool = False
    scoring_rule_version: str | None = None
    scoring_rules_compatibility: str = "SCORING_RULES_UNVERIFIED"
    scoring_rules_evidence_fingerprint: str | None = None
    production_ready: bool = False
    probabilistic_model_version: str | None = None
    calibration_version: str | None = None
    probabilistic_artifact_fingerprint: str | None = None
    predictive_model_version: str | None = None
    predictive_calibration_version: str | None = None
    predictive_artifact_fingerprint: str | None = None


def resolve_fantasy_matchday(
    season_code: str,
    slate: pd.DataFrame,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> MatchdayResolution:
    """Resolve a latest whole-market snapshot to the canonical basketball round."""

    with connect_database(database_path, read_only=True) as connection:
        batch = connection.execute(
            """
            SELECT snapshot_batch_id, max(observed_at), count(*) FILTER (
                     WHERE entity.entity_type='PLAYER'
                   ), count(DISTINCT market.matchday_number)
            FROM fantasy_market_snapshots AS market
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            WHERE market.season_code=?
            GROUP BY snapshot_batch_id
            ORDER BY max(observed_at) DESC, snapshot_batch_id DESC LIMIT 1
            """,
            [season_code],
        ).fetchone()
        if batch is None:
            return MatchdayResolution(
                "MARKET_NOT_AVAILABLE", None, _slate_round(slate), None, None, 0,
                None, "No Fantasy market snapshot exists for the requested season.",
            )
        batch_id, _, player_count, matchday_count = batch
        matchdays = connection.execute(
            "SELECT DISTINCT matchday_number FROM fantasy_market_snapshots "
            "WHERE snapshot_batch_id=? ORDER BY 1", [batch_id],
        ).fetchall()
        market_fingerprint = _query_fingerprint(
            connection,
            """
            SELECT fantasy_entity_id, canonical_team_id, opponent_team_id,
                   position_name, credits, matchday_number, epoch_us(observed_at)
            FROM fantasy_market_snapshots WHERE snapshot_batch_id=?
            ORDER BY fantasy_entity_id
            """,
            [batch_id],
        )
        if int(matchday_count) != 1 or len(matchdays) != 1:
            return MatchdayResolution(
                "MATCHDAY_AMBIGUOUS", None, _slate_round(slate), None,
                str(batch_id), int(player_count), market_fingerprint,
                "The latest Fantasy snapshot contains more than one matchday.",
            )
        fantasy_matchday = int(matchdays[0][0])
        slate_round = _slate_round(slate)
        mappings = connection.execute(
            """
            SELECT competition_round, mapping_status
            FROM fantasy_matchday_round_mapping
            WHERE season_code=? AND fantasy_matchday=?
            ORDER BY competition_round
            """,
            [season_code, fantasy_matchday],
        ).fetchall()
        validated = [int(row[0]) for row in mappings if row[1] == "VALIDATED_DIRECT"]
        if slate_round is not None and validated == [slate_round]:
            return MatchdayResolution(
                "MATCHDAY_RESOLVED", fantasy_matchday, slate_round,
                "VALIDATED_DIRECT_MAPPING", str(batch_id), int(player_count),
                market_fingerprint, "Validated Fantasy matchday-to-round mapping.",
            )
        if not slate.empty and slate_round is not None:
            pairs = {
                (str(row.team_id), str(row.opponent_team_id))
                for row in slate[["team_id", "opponent_team_id"]].drop_duplicates().itertuples()
            }
            market_pairs = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    """
                    SELECT DISTINCT canonical_team_id, opponent_team_id
                    FROM fantasy_market_snapshots
                    WHERE snapshot_batch_id=? AND canonical_team_id IS NOT NULL
                      AND opponent_team_id IS NOT NULL
                    """,
                    [batch_id],
                ).fetchall()
            }
            if market_pairs and market_pairs.issubset(pairs) and len(market_pairs) >= 2:
                return MatchdayResolution(
                    "MATCHDAY_RESOLVED", fantasy_matchday, slate_round,
                    "VALIDATED_TEAM_OPPONENT_CONTEXT", str(batch_id),
                    int(player_count), market_fingerprint,
                    "Fantasy team/opponent context exactly matches the canonical slate.",
                )
            # Between Turns, the slate excludes completed games but the market
            # still describes the whole Matchday. Validate against the complete
            # official round, including completed fixtures, without guessing from
            # the matchday number or accepting conflicting opponent pairs.
            round_pairs: dict[int, set[tuple[str, str]]] = {}
            for round_number, home, away in connection.execute(
                "SELECT round_number, home_team_id, away_team_id "
                "FROM current_live_schedule WHERE season_code=? "
                "AND round_number IS NOT NULL", [season_code],
            ).fetchall():
                round_pairs.setdefault(int(round_number), set()).update(
                    {(str(home), str(away)), (str(away), str(home))}
                )
            matching_rounds = [
                number for number, schedule_pairs in round_pairs.items()
                if market_pairs and market_pairs.issubset(schedule_pairs)
            ]
            if (
                len(market_pairs) >= 2 and pairs and pairs.issubset(market_pairs)
                and matching_rounds == [slate_round]
            ):
                return MatchdayResolution(
                    "MATCHDAY_RESOLVED", fantasy_matchday, slate_round,
                    "VALIDATED_TEAM_OPPONENT_CONTEXT", str(batch_id),
                    int(player_count), market_fingerprint,
                    "Fantasy team/opponent context matches the complete official round, "
                    "including completed games.",
                )
        return MatchdayResolution(
            "MATCHDAY_AMBIGUOUS", fantasy_matchday, slate_round, None,
            str(batch_id), int(player_count), market_fingerprint,
            "No validated mapping connects the latest Fantasy market to the upcoming round.",
        )


def build_current_player_pool(
    season_code: str,
    slate: pd.DataFrame,
    resolution: MatchdayResolution,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> PlayerPoolResult:
    """Return latest Fantasy players with conservative canonical/roster joins."""

    if resolution.market_snapshot_batch_id is None:
        return PlayerPoolResult(pd.DataFrame(), 0, 0, 0, 0, 0, 0)
    with connect_database(database_path, read_only=True) as connection:
        market = connection.execute(
            """
            WITH mapping AS (
              SELECT * EXCLUDE (mapping_rank) FROM (
                SELECT crosswalk.*, classification.resolution_classification,
                       row_number() OVER (
                         PARTITION BY crosswalk.fantasy_entity_id, crosswalk.season_code
                         ORDER BY (crosswalk.valid_to IS NULL) DESC,
                                  (crosswalk.mapping_status='MATCHED') DESC,
                                  crosswalk.valid_from DESC NULLS LAST,
                                  crosswalk.crosswalk_id DESC
                       ) AS mapping_rank
                FROM fantasy_player_crosswalk AS crosswalk
                LEFT JOIN fantasy_identity_classifications AS classification
                  USING (crosswalk_id)
                WHERE crosswalk.season_code=?
              ) WHERE mapping_rank=1
            )
            SELECT market.fantasy_entity_id AS fantasy_id,
                   entity.fantasy_id AS fantasy_player_id,
                   entity.display_name AS fantasy_player_name,
                   market.canonical_team_id AS market_team_id,
                   market.opponent_team_id AS market_opponent_team_id,
                   market.position_name AS market_fantasy_position,
                   market.credits, market.matchday_number AS fantasy_matchday,
                   market.observed_at AS fantasy_observed_at,
                   mapping.canonical_player_id AS mapped_player_id,
                   coalesce(mapping.mapping_status, 'UNMATCHED') AS mapping_status,
                   coalesce(mapping.resolution_classification,
                            mapping.mapping_status, 'UNMATCHED') AS mapping_classification,
                   mapping.match_method AS mapping_method,
                   mapping.manually_reviewed AS mapping_manually_reviewed,
                   mapping.crosswalk_id
            FROM fantasy_market_snapshots AS market
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN mapping USING (fantasy_entity_id, season_code)
            WHERE market.snapshot_batch_id=? AND entity.entity_type='PLAYER'
            ORDER BY entity.display_name, market.fantasy_entity_id
            """,
            [season_code, resolution.market_snapshot_batch_id],
        ).df()
        names = connection.execute(
            """
            SELECT player.canonical_player_id AS mapped_player_id,
                   player.canonical_name AS canonical_player_name
            FROM players AS player
            """
        ).df()
    if market.empty:
        return PlayerPoolResult(market, 0, 0, 0, 0, 0, 0)
    market = market.merge(names, on="mapped_player_id", how="left")
    slate_join = slate.copy()
    if not slate_join.empty:
        slate_join = slate_join.rename(columns={"player_id": "mapped_player_id"})
        duplicate_mappings = set(
            market.loc[
                market["mapped_player_id"].notna()
                & market["mapped_player_id"].duplicated(keep=False),
                "mapped_player_id",
            ].astype(str)
        )
        if duplicate_mappings:
            unsafe = market["mapped_player_id"].astype("string").isin(duplicate_mappings)
            market.loc[unsafe, "mapping_status"] = "AMBIGUOUS"
            market.loc[unsafe, "mapping_classification"] = "DUPLICATE_CANONICAL_MAPPING"
        market = market.merge(
            slate_join, on="mapped_player_id", how="left", validate="many_to_one",
            suffixes=("", "_slate"),
        )
    else:
        market["team_id"] = pd.NA
        market["game_id"] = pd.NA
    market["team_membership_valid"] = (
        market["team_id"].notna()
        & (
            market["market_team_id"].isna()
            | market["market_team_id"].astype("string").eq(
                market["team_id"].astype("string")
            )
        )
    )
    market["pool_status"] = np.select(
        [
            market["mapping_status"].eq("AMBIGUOUS"),
            ~market["mapping_status"].eq("MATCHED"),
            market["game_id"].isna(),
            ~market["team_membership_valid"],
        ],
        ["MAPPING_AMBIGUOUS", "MAPPING_UNMATCHED", "CURRENT_ROSTER_MISSING",
         "CURRENT_TEAM_MISMATCH"],
        default="READY",
    )
    return PlayerPoolResult(
        frame=market,
        market_players=len(market),
        mapped_players=int(market["mapping_status"].eq("MATCHED").sum()),
        roster_matched_players=int(market["pool_status"].eq("READY").sum()),
        mapping_ambiguous=int(market["pool_status"].eq("MAPPING_AMBIGUOUS").sum()),
        mapping_unmatched=int(market["pool_status"].eq("MAPPING_UNMATCHED").sum()),
        roster_mismatches=int(market["pool_status"].isin(
            ["CURRENT_ROSTER_MISSING", "CURRENT_TEAM_MISMATCH"]
        ).sum()),
    )


def load_decision_file(path: Path | None) -> DecisionConfig:
    if path is None:
        return DecisionConfig({}, (), {}, None, _json_sha({}))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Decision file must contain a JSON object")
    players = _parse_decision_mapping(payload.get("players", {}))
    roles = _parse_role_estimates(payload.get("players", {}))
    scenario_rows = payload.get("scenarios", {})
    if not isinstance(scenario_rows, dict):
        raise ValueError("decision-file scenarios must be an object keyed by scenario ID")
    if len(scenario_rows) > MAX_EXPLICIT_SCENARIOS:
        raise ValueError(f"at most {MAX_EXPLICIT_SCENARIOS} explicit scenarios are allowed")
    scenarios = tuple(
        (str(name), _parse_decision_mapping(value))
        for name, value in scenario_rows.items()
    )
    default = payload.get("default_unknown_decision")
    if default is not None:
        default = str(default).upper()
        if default not in {"PLAY", "OUT", "UNKNOWN", "LIMITED"}:
            raise ValueError("default_unknown_decision is invalid")
    return DecisionConfig(
        players, scenarios, roles, default,
        _json_sha(payload),
    )


def build_prediction_scenarios(
    slate: pd.DataFrame,
    decisions: DecisionConfig,
) -> list[AvailabilityScenario]:
    player_decisions = _canonicalize_decisions(slate, decisions.player_decisions)
    if decisions.default_unknown_decision is not None:
        preliminary = generate_availability_scenarios(
            slate, user_decisions=player_decisions
        )
        if preliminary and preliminary[0].scenario_name == "UNRESOLVED":
            for player in preliminary[0].unresolved_players:
                player_decisions[player] = decisions.default_unknown_decision
    if decisions.scenarios:
        scenario_sets = [
            _canonicalize_decisions(slate, mapping)
            for _, mapping in decisions.scenarios
        ]
        generated = generate_availability_scenarios(
            slate, user_decisions=player_decisions, scenario_sets=scenario_sets
        )
        return [
            replace(item, scenario_name=decisions.scenarios[index][0])
            for index, item in enumerate(generated)
        ]
    return generate_availability_scenarios(slate, user_decisions=player_decisions)


def predict_fantasy_points(
    slate: pd.DataFrame,
    scenario: AvailabilityScenario,
    bundle: FrozenPerformanceBundle,
    validation: ArtifactValidation,
    probabilistic_bundle: FrozenProbabilisticBundle,
    probabilistic_validation: ProbabilisticArtifactValidation,
    predictive_bundle: FrozenPredictiveUpliftBundle | None = None,
    predictive_validation: PredictiveUpliftArtifactValidation | None = None,
    *,
    database_path: Path | str,
    cutoff: datetime,
    fantasy_matchday: int | None,
    manual_role_estimates: Mapping[str, float] | None = None,
    redistribution_model_root: Path = DEFAULT_MODEL_ROOT,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Score and redistribute every current roster team for one scenario."""

    frame = slate.copy()
    warnings: list[str] = []
    frame["broad_position"] = frame.get(
        "fantasy_position", pd.Series("UNKNOWN", index=frame.index)
    ).map(_broad_position).fillna("UNKNOWN")
    frame = attach_previous_season_minutes(
        frame, database_path=database_path, cutoff=cutoff
    )
    components = bundle.predict_components(frame)
    component_columns = list(components.columns)
    component_frame = pd.DataFrame(
        {column: components[column].to_numpy() for column in component_columns},
        index=frame.index,
    )
    frame = pd.concat(
        [frame.drop(columns=component_columns, errors="ignore"), component_frame],
        axis=1,
    ).copy()
    frame["baseline_expected_minutes"] = frame[
        "baseline_expected_minutes_if_available"
    ]
    frame = calibrate_cold_start_minutes(frame)
    frame = apply_preparation_context(frame)
    frame["direct_fp"] = frame["direct_prediction"]
    frame["predicted_fp_per_min"] = frame["expected_fp_per_min"]
    role_estimates = _canonicalize_role_estimates(frame, manual_role_estimates or {})
    for player, value in role_estimates.items():
        frame.loc[frame.player_id.astype(str).eq(player), "baseline_expected_minutes"] = value
    outputs: list[pd.DataFrame] = []
    team_summaries: dict[str, Any] = {}
    for (game_id, team_id), team in frame.groupby(["game_id", "team_id"], sort=True):
        team = team.copy()
        out_ids = [
            str(player) for player in team.player_id
            if scenario.decisions.get(str(player), "UNKNOWN") == "OUT"
        ]
        unknown_role = [
            player for player in out_ids
            if not np.isfinite(pd.to_numeric(
                team.loc[team.player_id.astype(str).eq(player), "baseline_expected_minutes"],
                errors="coerce",
            )).all() and player not in role_estimates
        ]
        team_key = f"{game_id}:{team_id}"
        if unknown_role:
            team["scenario_decision"] = team.player_id.astype(str).map(
                scenario.decisions
            ).fillna("UNKNOWN")
            team["adjusted_expected_minutes"] = np.nan
            team["minutes_delta_due_to_absences"] = np.nan
            team["expected_fp_before_absence_adjustment"] = np.nan
            team["expected_fp_after_absence_adjustment"] = np.nan
            team["prediction_status_override"] = "MISSING_ROLE_UNKNOWN"
            team["missing_role_status"] = "UNKNOWN"
            warnings.append(
                f"{team_key}: missing role unknown for OUT player(s) {','.join(unknown_role)}"
            )
            team_summaries[team_key] = {
                "known_out_players": out_ids,
                "missing_role_status": "UNKNOWN",
                "unknown_role_players": unknown_role,
            }
            outputs.append(team)
            continue
        limits = resolve_role_limits(
            team.player_id.astype(str).tolist(), database_path=database_path,
            season_code=str(team.season.iloc[0]), game_id=str(game_id),
            fantasy_matchday=fantasy_matchday, as_of=cutoff,
        )
        team = attach_live_relationship_history(
            team, scenario.decisions, database_path=database_path, cutoff=cutoff
        )
        try:
            adjusted = redistribute_live_team(
                team, scenario.decisions, role_limits=limits,
                model_root=redistribution_model_root,
            )
        except ValueError as error:
            team["scenario_decision"] = team.player_id.astype(str).map(
                scenario.decisions
            ).fillna("UNKNOWN")
            team["adjusted_expected_minutes"] = np.nan
            team["minutes_delta_due_to_absences"] = np.nan
            team["expected_fp_before_absence_adjustment"] = np.nan
            team["expected_fp_after_absence_adjustment"] = np.nan
            team["prediction_status_override"] = "UNSCORABLE"
            team["missing_role_status"] = "ESTIMATED"
            warnings.append(f"{team_key}: redistribution failed: {error}")
            team_summaries[team_key] = {
                "known_out_players": out_ids, "redistribution_error": str(error),
            }
            outputs.append(team)
            continue
        adjusted["prediction_status_override"] = pd.NA
        adjusted["missing_role_status"] = np.where(
            adjusted.player_id.astype(str).isin(role_estimates), "MANUAL",
            np.where(adjusted.scenario_decision.eq("OUT"), "ESTIMATED", "NOT_APPLICABLE"),
        )
        summary = _team_summary(adjusted, out_ids)
        team_summaries[team_key] = summary
        outputs.append(adjusted)
    output = (
        pd.concat(outputs, ignore_index=True).copy()
        if outputs else frame.iloc[0:0].copy()
    )
    output["scenario_id"] = scenario.scenario_name
    output["performance_model_version"] = validation.performance_model_version
    output["redistribution_model_version"] = validation.redistribution_model_version
    output["feature_manifest_fingerprint"] = validation.feature_manifest_fingerprint
    phase4b_delta = (
        pd.to_numeric(output["expected_fp_after_absence_adjustment"], errors="coerce")
        - pd.to_numeric(output["expected_fp_before_absence_adjustment"], errors="coerce")
    )
    distribution = probabilistic_bundle.predict_distribution(
        output, fp_location_delta=(phase4b_delta.fillna(0.0) + output.preparation_fp_delta).to_numpy(float)
    )
    if (predictive_bundle is None) != (predictive_validation is None):
        raise ValueError("Phase 6C bundle and validation must be supplied together")
    if predictive_bundle is not None:
        distribution, _ = predictive_bundle.predict_distribution(
            output,
            distribution,
            phase5b_location_delta=(phase4b_delta.fillna(0.0) + output.preparation_fp_delta).to_numpy(float),
            database_path=database_path,
        )
    distribution_columns = list(distribution.columns)
    distribution_frame = pd.DataFrame(
        {column: distribution[column].to_numpy() for column in distribution_columns},
        index=output.index,
    )
    output = pd.concat(
        [
            output.drop(columns=distribution_columns, errors="ignore"),
            distribution_frame,
        ],
        axis=1,
    ).copy()
    # External context is part of the baseline; keep absence deltas separate.
    for column in ("phase6b_expected_fp_before_availability_adjustment",
                   "phase6c_expected_fp_before_availability_adjustment"):
        if column in output:
            output[column] += output.preparation_fp_delta
    output["phase4b_central_fp_before_adjustment"] = output[
        "expected_fp_before_absence_adjustment"
    ]
    output["phase4b_central_fp"] = output[
        "expected_fp_after_absence_adjustment"
    ]
    output["probabilistic_model_version"] = (
        probabilistic_validation.probabilistic_model_version
    )
    output["calibration_version"] = probabilistic_validation.calibration_version
    output["probabilistic_artifact_fingerprint"] = (
        probabilistic_validation.artifact_fingerprint
    )
    output["predictive_model_version"] = (
        predictive_validation.predictive_model_version
        if predictive_validation else pd.NA
    )
    output["predictive_calibration_version"] = (
        predictive_validation.calibration_version
        if predictive_validation else pd.NA
    )
    output["predictive_artifact_fingerprint"] = (
        predictive_validation.artifact_fingerprint
        if predictive_validation else pd.NA
    )
    output["predictive_feature_manifest_fingerprint"] = (
        predictive_validation.feature_manifest_fingerprint
        if predictive_validation else pd.NA
    )
    output["expected_role"] = output["adjusted_expected_minutes"].map(
        expected_role_label
    )
    return output, team_summaries, warnings


def store_prediction_run(
    result: LivePredictionResult,
    *,
    database_path: Path | str,
    resolution: MatchdayResolution,
    input_fingerprint: str,
    run_fingerprint: str,
    slate_run_id: str | None,
    live_run_id: str | None,
    refresh_requested: bool,
    validation: ArtifactValidation | None,
    probabilistic_validation: ProbabilisticArtifactValidation | None,
    availability_state: Mapping[str, Any],
    scenario_definitions: Sequence[Mapping[str, Any]],
    freshness: Mapping[str, Any],
    output: pd.DataFrame | None = None,
    scenario_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    predictive_validation: PredictiveUpliftArtifactValidation | None = None,
) -> None:
    """Append an immutable run and its rows; no prediction row is ever updated."""

    if result.status not in RUN_STATUSES:
        raise ValueError(f"invalid terminal prediction status: {result.status}")
    initialize_database(database_path)
    with connect_database(database_path) as connection:
        existing = connection.execute(
            "SELECT run_fingerprint FROM live_prediction_runs WHERE prediction_run_id=?",
            [result.prediction_run_id],
        ).fetchone()
        if existing:
            if str(existing[0]) != run_fingerprint:
                raise ValueError("immutable prediction run ID collision")
            return
        # Publish the run, scenarios and every player row as one atomic unit.
        # Closing the connection on an exception rolls back this transaction.
        connection.execute("BEGIN TRANSACTION")
        run_row = {
            "prediction_run_id": result.prediction_run_id,
            "season_code": result.season_code,
            "run_mode": "LIVE",
            "status": result.status,
            "prediction_generated_at": result.prediction_generated_at,
            "feature_cutoff_time": result.prediction_generated_at,
            "completed_at": datetime.now(UTC),
            "season_matchday": result.fantasy_matchday,
            "matchday_resolution_status": resolution.status,
            "matchday_resolution_method": resolution.method,
            "source_slate_run_id": slate_run_id,
            "source_live_run_id": live_run_id,
            "refresh_requested": refresh_requested,
            "input_fingerprint": input_fingerprint,
            "run_fingerprint": run_fingerprint,
            "performance_model_version": (
                validation.performance_model_version if validation else None
            ),
            "redistribution_model_version": (
                validation.redistribution_model_version if validation else None
            ),
            "performance_artifact_fingerprint": (
                validation.performance_artifact_fingerprint if validation else None
            ),
            "redistribution_artifact_fingerprint": (
                validation.redistribution_artifact_fingerprint if validation else None
            ),
            "feature_manifest_fingerprint": (
                validation.feature_manifest_fingerprint
                if validation else _feature_fingerprint()
            ),
            "market_snapshot_batch_id": resolution.market_snapshot_batch_id,
            "market_snapshot_fingerprint": resolution.market_fingerprint,
            "availability_state_json": json.dumps(
                availability_state, sort_keys=True, default=str
            ),
            "scenario_definitions_json": json.dumps(
                list(scenario_definitions), sort_keys=True, default=str
            ),
            "source_freshness_json": json.dumps(
                freshness, sort_keys=True, default=str
            ),
            "coverage_json": json.dumps(result.coverage, sort_keys=True, default=str),
            "timing_json": json.dumps(result.timings, sort_keys=True, default=str),
            "warnings_json": json.dumps(result.warnings),
            "error_code": result.error_code,
            "error_message": result.error_message,
            "output_json_path": result.json_path,
            "output_tabular_path": result.csv_path,
            "created_before_outcomes": True,
            "scoring_rule_version": result.scoring_rule_version,
            "scoring_rules_compatibility": result.scoring_rules_compatibility,
            "scoring_rules_evidence_fingerprint": (
                result.scoring_rules_evidence_fingerprint
            ),
            "production_ready": result.production_ready,
            "probabilistic_model_version": (
                probabilistic_validation.probabilistic_model_version
                if probabilistic_validation else None
            ),
            "calibration_version": (
                probabilistic_validation.calibration_version
                if probabilistic_validation else None
            ),
            "probabilistic_artifact_fingerprint": (
                probabilistic_validation.artifact_fingerprint
                if probabilistic_validation else None
            ),
            "predictive_model_version": (
                predictive_validation.predictive_model_version
                if predictive_validation else None
            ),
            "predictive_calibration_version": (
                predictive_validation.calibration_version
                if predictive_validation else None
            ),
            "predictive_artifact_fingerprint": (
                predictive_validation.artifact_fingerprint
                if predictive_validation else None
            ),
            "predictive_feature_manifest_fingerprint": (
                predictive_validation.feature_manifest_fingerprint
                if predictive_validation else None
            ),
        }
        run_frame = pd.DataFrame([run_row])
        connection.register("_phase6a_run", run_frame)
        try:
            connection.execute(
                "INSERT INTO live_prediction_runs BY NAME SELECT * FROM _phase6a_run"
            )
        finally:
            connection.unregister("_phase6a_run")
        if output is None or output.empty:
            connection.execute("COMMIT")
            return
        for scenario_id, group in output.groupby("scenario_id", sort=True):
            definition = next(
                (item for item in scenario_definitions if item["scenario_id"] == scenario_id),
                {"scenario_id": scenario_id, "decisions": {}},
            )
            scenario_fingerprint = _json_sha(definition)
            prediction_scenario_id = stable_id(
                "live_prediction_scenario", result.prediction_run_id, scenario_id
            )
            team_summary = (scenario_summaries or {}).get(str(scenario_id), {})
            known_out = sorted(
                player for player, decision in definition.get("decisions", {}).items()
                if decision == "OUT"
            )
            connection.execute(
                """
                INSERT INTO live_prediction_scenarios VALUES (
                  ?, ?, ?, 'GENERATED', ?, ?, '[]', ?, ?, ?, ?
                )
                """,
                [prediction_scenario_id, result.prediction_run_id, scenario_id,
                 json.dumps(definition.get("decisions", {}), sort_keys=True),
                 json.dumps(definition.get("manual_role_estimates", {}), sort_keys=True),
                 json.dumps(known_out), json.dumps(team_summary, sort_keys=True, default=str),
                 scenario_fingerprint, result.prediction_generated_at],
            )
            for row in group.itertuples():
                _insert_prediction_row(
                    connection, result, prediction_scenario_id, row,
                    validation.feature_manifest_fingerprint if validation else _feature_fingerprint(),
                )
        connection.execute("COMMIT")


def run_live_prediction(
    season_code: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    fantasy_config: Path | None = None,
    refresh: bool = False,
    decision_file: Path | None = None,
    cutoff: datetime | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    phase4b_artifact_root: Path = DEFAULT_PHASE4B_ARTIFACT_ROOT,
    phase5b_artifact_root: Path = DEFAULT_MODEL_ROOT,
    phase6b_artifact_root: Path = DEFAULT_PHASE6B_ARTIFACT_ROOT,
    phase6c_artifact_root: Path = DEFAULT_PHASE6C_ARTIFACT_ROOT,
    model_bundle: FrozenPerformanceBundle | None = None,
    artifact_validation: ArtifactValidation | None = None,
    probabilistic_bundle: FrozenProbabilisticBundle | None = None,
    probabilistic_artifact_validation: ProbabilisticArtifactValidation | None = None,
    predictive_bundle: FrozenPredictiveUpliftBundle | None = None,
    predictive_artifact_validation: PredictiveUpliftArtifactValidation | None = None,
    allow_unverified_scoring_dry_run: bool = False,
    scoring_evidence_path: Path | None = None,
) -> LivePredictionResult:
    """Orchestrate one deterministic, prospective Phase 6A/6B/6C run."""

    started = time.monotonic()
    generated_at = cutoff or datetime.now(UTC)
    if generated_at.tzinfo is None:
        raise ValueError("prediction cutoff must be timezone-aware")
    generated_at = generated_at.astimezone(UTC)
    initialize_database(database_path)
    timings = {"update_seconds": 0.0, "feature_seconds": 0.0,
               "redistribution_prediction_seconds": 0.0, "storage_seconds": 0.0}
    warnings: list[str] = []
    live_run_id: str | None = None
    if refresh:
        stage = time.monotonic()
        refreshed = update_live(
            season_code, database_path=database_path, fantasy_config=fantasy_config,
            as_of=generated_at,
        )
        timings["update_seconds"] = time.monotonic() - stage
        live_run_id = refreshed.live_run_id
        warnings.extend(refreshed.warnings)
    stage = time.monotonic()
    slate_build = build_current_slate(
        season_code, database_path=database_path, as_of=generated_at
    )
    timings["feature_seconds"] = time.monotonic() - stage
    warnings.extend(slate_build.warnings)
    with connect_database(database_path, read_only=True) as connection:
        slate = connection.execute(
            "SELECT * FROM current_upcoming_player_slate_v1 WHERE season=? "
            "ORDER BY scheduled_tip_time, game_id, team_id, player_id",
            [season_code],
        ).df()
    resolution = resolve_fantasy_matchday(
        season_code, slate, database_path=database_path
    )
    decisions = load_decision_file(decision_file)
    scoring = resolve_scoring_rule_compatibility(
        season_code,
        **({"evidence_path": scoring_evidence_path} if scoring_evidence_path else {}),
    )
    pool = build_current_player_pool(
        season_code, slate, resolution, database_path=database_path
    )
    freshness_objects = source_freshness(database_path, as_of=generated_at)
    freshness = {name: asdict(value) for name, value in freshness_objects.items()}
    coverage = _coverage(pool, slate)
    base_input = {
        "season": season_code, "cutoff": generated_at.isoformat(),
        "slate": slate_build.output_fingerprint,
        "market": resolution.market_fingerprint,
        "decisions": decisions.source_fingerprint,
        "scoring_rules": scoring.evidence_fingerprint,
        "scoring_compatibility": scoring.state,
        "allow_unverified_scoring_dry_run": allow_unverified_scoring_dry_run,
        "phase6a_protocol": PHASE6A_PROTOCOL_VERSION,
        "minutes_calibration": MINUTES_CALIBRATION_VERSION,
        "preparation_context": PREPARATION_CONTEXT_VERSION,
    }
    input_fingerprint = _json_sha(base_input)

    def terminal(
        status: str, *, error_code: str | None = None,
        error_message: str | None = None,
        scenario_count: int = 0,
        validation: ArtifactValidation | None = None,
        probabilistic_validation: ProbabilisticArtifactValidation | None = None,
        predictive_validation: PredictiveUpliftArtifactValidation | None = None,
    ) -> LivePredictionResult:
        run_fingerprint = _json_sha({
            **base_input,
            "performance": validation.performance_artifact_fingerprint if validation else None,
            "redistribution": validation.redistribution_artifact_fingerprint if validation else None,
            "probabilistic": (
                probabilistic_validation.artifact_fingerprint
                if probabilistic_validation else None
            ),
            "predictive": (
                predictive_validation.artifact_fingerprint
                if predictive_validation else None
            ),
            "status": status,
        })
        run_id = stable_id("live_prediction_run", run_fingerprint)
        timings["total_seconds"] = time.monotonic() - started
        result = LivePredictionResult(
            run_id, status, season_code, generated_at, resolution.status,
            resolution.fantasy_matchday, scenario_count, coverage, dict(timings),
            tuple(dict.fromkeys(warnings)), None, None, error_code, error_message,
            _run_exists(database_path, run_id),
            scoring.training_rule_version, scoring.state,
            scoring.evidence_fingerprint, False,
            probabilistic_validation.probabilistic_model_version
            if probabilistic_validation else None,
            probabilistic_validation.calibration_version
            if probabilistic_validation else None,
            probabilistic_validation.artifact_fingerprint
            if probabilistic_validation else None,
            predictive_validation.predictive_model_version
            if predictive_validation else None,
            predictive_validation.calibration_version
            if predictive_validation else None,
            predictive_validation.artifact_fingerprint
            if predictive_validation else None,
        )
        store_prediction_run(
            result, database_path=database_path, resolution=resolution,
            input_fingerprint=input_fingerprint, run_fingerprint=run_fingerprint,
            slate_run_id=slate_build.slate_run_id, live_run_id=live_run_id,
            refresh_requested=refresh, validation=validation,
            probabilistic_validation=probabilistic_validation,
            predictive_validation=predictive_validation,
            availability_state=_availability_state(slate), scenario_definitions=[],
            freshness=freshness,
        )
        return result

    try:
        if model_bundle is None or artifact_validation is None:
            model_bundle, artifact_validation = load_and_validate_frozen_artifacts(
                phase4b_root=phase4b_artifact_root,
                phase5b_root=phase5b_artifact_root,
            )
        if probabilistic_bundle is None or probabilistic_artifact_validation is None:
            probabilistic_bundle, probabilistic_artifact_validation = (
                load_and_validate_probabilistic_artifact(phase6b_artifact_root)
            )
        if predictive_bundle is None or predictive_artifact_validation is None:
            predictive_bundle, predictive_artifact_validation = (
                load_and_validate_predictive_uplift_artifact(phase6c_artifact_root)
            )
    except ModelArtifactMismatch as error:
        warnings.append(str(error))
        return terminal(
            "MODEL_ARTIFACT_MISMATCH", error_code=error.code,
            error_message=str(error),
        )
    if resolution.status == "MARKET_NOT_AVAILABLE":
        warnings.append(resolution.message)
        return terminal(
            "NO_CURRENT_FANTASY_SLATE", validation=artifact_validation,
            probabilistic_validation=probabilistic_artifact_validation,
            predictive_validation=predictive_artifact_validation,
        )
    if resolution.status == "MATCHDAY_AMBIGUOUS":
        warnings.append(resolution.message)
        return terminal(
            "MATCHDAY_AMBIGUOUS", validation=artifact_validation,
            probabilistic_validation=probabilistic_artifact_validation,
            predictive_validation=predictive_artifact_validation,
        )
    if slate.empty or pool.roster_matched_players == 0:
        warnings.append(
            "Fantasy market exists, but no safely mapped player is on the current canonical slate."
        )
        return terminal(
            "NO_CURRENT_FANTASY_SLATE", validation=artifact_validation,
            probabilistic_validation=probabilistic_artifact_validation,
            predictive_validation=predictive_artifact_validation,
        )
    if not scoring.production_ready and not allow_unverified_scoring_dry_run:
        warnings.append(scoring.reason)
        return terminal(
            "FAILED", error_code=scoring.state, error_message=scoring.reason,
            validation=artifact_validation,
            probabilistic_validation=probabilistic_artifact_validation,
            predictive_validation=predictive_artifact_validation,
        )
    if not scoring.production_ready:
        warnings.append(
            f"TECHNICAL_DRY_RUN: {scoring.state}; output is not production-ready."
        )
    scenarios = build_prediction_scenarios(slate, decisions)
    unresolved = sorted({
        player for scenario in scenarios for player in scenario.unresolved_players
    })
    coverage["players_unresolved"] = len(unresolved)
    if unresolved:
        warnings.append(
            "Multiple uncertain players remain; provide explicit scenario combinations."
        )
        return terminal(
            "AVAILABILITY_UNRESOLVED", error_code="AVAILABILITY_UNRESOLVED",
            error_message=",".join(unresolved), scenario_count=len(scenarios),
            validation=artifact_validation,
            probabilistic_validation=probabilistic_artifact_validation,
            predictive_validation=predictive_artifact_validation,
        )
    stage = time.monotonic()
    # Phase 6C history is scenario-invariant; attach it once even when the user
    # requests several availability scenarios.
    slate = predictive_bundle.prepare_features(
        slate, database_path=database_path
    )
    outputs: list[pd.DataFrame] = []
    scenario_definitions: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        output, team_summary, scenario_warnings = predict_fantasy_points(
            slate, scenario, model_bundle, artifact_validation,
            probabilistic_bundle, probabilistic_artifact_validation,
            predictive_bundle, predictive_artifact_validation,
            database_path=database_path, cutoff=generated_at,
            fantasy_matchday=resolution.fantasy_matchday,
            manual_role_estimates=decisions.manual_role_estimates,
            redistribution_model_root=phase5b_artifact_root,
        )
        output = _select_market_outputs(output, pool.frame, generated_at, freshness_objects)
        outputs.append(output)
        summaries[scenario.scenario_name] = team_summary
        warnings.extend(scenario_warnings)
        scenario_definitions.append({
            "scenario_id": scenario.scenario_name,
            "decisions": scenario.decisions,
            "manual_role_estimates": decisions.manual_role_estimates,
        })
    predictions = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    timings["redistribution_prediction_seconds"] = time.monotonic() - stage
    coverage.update(_prediction_coverage(pool, predictions, slate))
    run_fingerprint = _json_sha({
        **base_input,
        "performance": artifact_validation.performance_artifact_fingerprint,
        "redistribution": artifact_validation.redistribution_artifact_fingerprint,
        "probabilistic": probabilistic_artifact_validation.artifact_fingerprint,
        "predictive": predictive_artifact_validation.artifact_fingerprint,
        "scenarios": scenario_definitions,
    })
    run_id = stable_id("live_prediction_run", run_fingerprint)
    stage = time.monotonic()
    json_path, csv_path, reused = _write_outputs(
        output_root, run_id, season_code, generated_at, resolution,
        coverage, freshness, scenario_definitions, summaries, predictions,
        artifact_validation, scoring, warnings,
        probabilistic_artifact_validation,
        predictive_artifact_validation,
    )
    timings["storage_seconds"] = time.monotonic() - stage
    timings["total_seconds"] = time.monotonic() - started
    status = (
        "SUCCEEDED"
        if coverage.get("players_unscorable", 0) == 0 and scoring.production_ready
        else "PARTIAL"
    )
    result = LivePredictionResult(
        run_id, status, season_code, generated_at, resolution.status,
        resolution.fantasy_matchday, len(scenarios), coverage, timings,
        tuple(dict.fromkeys(warnings)), str(json_path), str(csv_path),
        reused_immutable_snapshot=reused or _run_exists(database_path, run_id),
        scoring_rule_version=scoring.training_rule_version,
        scoring_rules_compatibility=scoring.state,
        scoring_rules_evidence_fingerprint=scoring.evidence_fingerprint,
        production_ready=(status == "SUCCEEDED" and scoring.production_ready),
        probabilistic_model_version=(
            probabilistic_artifact_validation.probabilistic_model_version
        ),
        calibration_version=probabilistic_artifact_validation.calibration_version,
        probabilistic_artifact_fingerprint=(
            probabilistic_artifact_validation.artifact_fingerprint
        ),
        predictive_model_version=(
            predictive_artifact_validation.predictive_model_version
        ),
        predictive_calibration_version=(
            predictive_artifact_validation.calibration_version
        ),
        predictive_artifact_fingerprint=(
            predictive_artifact_validation.artifact_fingerprint
        ),
    )
    store_prediction_run(
        result, database_path=database_path, resolution=resolution,
        input_fingerprint=input_fingerprint, run_fingerprint=run_fingerprint,
        slate_run_id=slate_build.slate_run_id, live_run_id=live_run_id,
        refresh_requested=refresh, validation=artifact_validation,
        probabilistic_validation=probabilistic_artifact_validation,
        predictive_validation=predictive_artifact_validation,
        availability_state=_availability_state(slate),
        scenario_definitions=scenario_definitions, freshness=freshness,
        output=predictions, scenario_summaries=summaries,
    )
    return result


def _select_market_outputs(
    output: pd.DataFrame,
    pool: pd.DataFrame,
    generated_at: datetime,
    freshness: Mapping[str, Any],
) -> pd.DataFrame:
    ready = pool[pool.pool_status.eq("READY")].copy()
    market_columns = [
        "mapped_player_id", "fantasy_id", "fantasy_player_id",
        "fantasy_player_name", "credits", "fantasy_matchday",
        "mapping_method", "mapping_manually_reviewed", "crosswalk_id",
        "fantasy_observed_at",
    ]
    canonical_market_columns = set(market_columns) - {"mapped_player_id"}
    output = output.drop(
        columns=sorted(canonical_market_columns.intersection(output.columns)),
        errors="ignore",
    )
    selected = output.merge(
        ready[market_columns], left_on="player_id", right_on="mapped_player_id",
        how="inner", validate="one_to_one",
    ).copy()
    if selected.empty:
        return selected
    selected["prediction_generated_at"] = generated_at
    selected["player_name"] = selected["fantasy_player_name"]
    selected["credits"] = pd.to_numeric(selected["credits"], errors="coerce")
    has_phase6c = "phase6c_expected_fp" in selected
    expected_before_column = (
        "phase6c_expected_fp_before_availability_adjustment"
        if has_phase6c else "phase6b_expected_fp_before_availability_adjustment"
    )
    expected_column = "phase6c_expected_fp" if has_phase6c else "phase6b_expected_fp"
    selected["expected_fp_before_adjustment"] = selected[expected_before_column]
    selected["expected_fp"] = selected[expected_column]
    selected["fp_delta_due_to_absences"] = (
        selected["expected_fp"] - selected["expected_fp_before_adjustment"]
    )
    selected["lower_prediction_interval"] = np.nan
    selected["upper_prediction_interval"] = np.nan
    selected["interval_calibration_status"] = (
        "NOT_RECALIBRATED_AFTER_AVAILABILITY_ADJUSTMENT"
    )
    career = pd.to_numeric(selected["career_el_games_before"], errors="coerce").fillna(0)
    season = pd.to_numeric(selected["season_games_before"], errors="coerce").fillna(0)
    selected["career_el_games_before"] = career.astype(int)
    selected["season_games_before"] = season.astype(int)
    selected["cold_start_flag"] = career.lt(5)
    required = list(frozen_required_features())
    selected["feature_completeness"] = selected[required].notna().mean(axis=1)
    stale = [name for name, value in freshness.items() if value.is_stale]
    selected["freshness_status"] = "STALE:" + "|".join(stale) if stale else "FRESH"
    selected["source_availability_status"] = selected[
        "resolved_availability_status"
    ].fillna("UNKNOWN")
    selected["resolved_availability"] = selected["scenario_decision"]
    selected["availability_decision_source"] = np.where(
        selected.get("manual_override_active", False), "MANUAL_OVERRIDE",
        np.where(
            selected["source_availability_status"].isin(
                ["QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION", "UNKNOWN"]
            ),
            "SCENARIO_DECISION", "SOURCE_DEFAULT",
        ),
    )
    override = selected.get("prediction_status_override", pd.Series(pd.NA, index=selected.index))
    finite = (
        selected["component_predictions_finite"].fillna(False)
        & selected["probabilistic_predictions_finite"].fillna(False)
    )
    if has_phase6c:
        finite &= selected["predictive_predictions_finite"].fillna(False)
    selected["prediction_status"] = np.where(
        override.notna(), override,
        np.where(selected.scenario_decision.eq("OUT"), "OUT",
                 np.where(finite, "SCORED", "UNSCORABLE")),
    )
    inactive = ~selected["prediction_status"].eq("SCORED")
    active_prediction_columns = [
        "phase4b_central_fp_before_adjustment", "phase4b_central_fp",
        "phase6b_expected_fp_before_availability_adjustment", "phase6b_expected_fp",
        "expected_fp_before_adjustment", "expected_fp", "fp_delta_due_to_absences",
        "median_fp", "p10_fp", "p25_fp", "p50_fp", "p75_fp", "p90_fp", "p95_fp",
        "prob_fp_ge_20", "prob_fp_ge_25", "prob_fp_ge_30", "prob_fp_ge_35",
        "prob_fp_ge_40", "distribution_width", "p90_minus_p50", "p50_minus_p10",
        "p95_minus_expected", "phase6c_expected_fp_before_availability_adjustment",
        "phase6c_expected_fp", "phase6c_location_delta", "expected_usage_next_game",
        "prob_fp_le_5", "prob_fp_le_10", "prob_fp_le_15",
    ]
    selected.loc[inactive, [
        column for column in active_prediction_columns if column in selected
    ]] = np.nan
    selected["game_date"] = pd.to_datetime(
        selected.get("local_game_date", selected["scheduled_tip_time"])
    ).dt.date
    selected["expected_fp_per_credit"] = selected["expected_fp"] / selected[
        "credits"
    ].replace(0, np.nan)
    selected["feature_snapshot_fingerprint"] = selected.apply(
        lambda row: _feature_row_fingerprint(
            row, _prediction_feature_columns()
        ), axis=1
    )
    return selected


def _insert_prediction_row(
    connection: Any,
    result: LivePredictionResult,
    prediction_scenario_id: str,
    row: Any,
    manifest_fingerprint: str,
) -> None:
    feature_snapshot = {
        column: _json_value(getattr(row, column, None))
        for column in _prediction_feature_columns()
    }
    provenance = {
        "availability_source": _json_value(getattr(row, "availability_source", None)),
        "availability_observed_at": _json_value(
            getattr(row, "availability_timestamp", None)
        ),
        "market_observed_at": _json_value(getattr(row, "fantasy_observed_at", None)),
        "mapping_method": _json_value(getattr(row, "mapping_method", None)),
        "mapping_manually_reviewed": bool(
            getattr(row, "mapping_manually_reviewed", False) or False
        ),
        "feature_cutoff_time": result.prediction_generated_at.isoformat(),
        "phase6c_player_source_max_game_time": _json_value(getattr(
            row, "p6c_player_source_max_game_time", None
        )),
        "phase6c_opponent_source_max_game_time": _json_value(getattr(
            row, "p6c_opponent_source_max_game_time", None
        )),
        "phase6c_role_matchup_source_max_game_time": _json_value(getattr(
            row, "p6c_role_matchup_source_max_game_time", None
        )),
    }
    explanation = {
        "context_not_causality": True,
        "minutes_delta": _json_value(getattr(row, "minutes_delta_due_to_absences", None)),
        "team_total_missing_minutes": _json_value(
            getattr(row, "team_total_missing_minutes", None)
        ),
        "number_teammates_out": _json_value(getattr(row, "number_teammates_out", None)),
        "historical_role_rank": _json_value(getattr(row, "historical_role_rank", None)),
        "dominant_missing_position": _json_value(
            getattr(row, "dominant_missing_position", None)
        ),
    }
    row_payload = {
        "run": result.prediction_run_id, "scenario": row.scenario_id,
        "fantasy": row.fantasy_id, "player": row.player_id,
        "game": row.game_id, "expected_fp": _json_value(row.expected_fp),
        "quantiles": [
            _json_value(getattr(row, column, None))
            for column in ("p10_fp", "p25_fp", "p50_fp", "p75_fp", "p90_fp", "p95_fp")
        ],
        "threshold_probabilities": [
            _json_value(getattr(row, f"prob_fp_ge_{threshold}", None))
            for threshold in (20, 25, 30, 35, 40)
        ],
        "downside_probabilities": [
            _json_value(getattr(row, f"prob_fp_le_{threshold}", None))
            for threshold in (5, 10, 15)
        ],
        "expected_usage_next_game": _json_value(getattr(
            row, "expected_usage_next_game", None
        )),
        "adjusted_minutes": _json_value(row.adjusted_expected_minutes),
    }
    row_fingerprint = _json_sha(row_payload)
    prediction_id = stable_id(
        "live_player_prediction", result.prediction_run_id,
        row.scenario_id, row.fantasy_id,
    )
    prediction_row = {
        "live_prediction_id": prediction_id,
        "prediction_run_id": result.prediction_run_id,
        "prediction_scenario_id": prediction_scenario_id,
        "scenario_id": row.scenario_id,
        "prediction_generated_at": result.prediction_generated_at,
        "season_code": result.season_code,
        "fantasy_matchday": _optional_int(row.fantasy_matchday),
        "canonical_game_id": row.game_id,
        "game_date": row.game_date,
        "scheduled_tip_time": row.scheduled_tip_time,
        "canonical_player_id": row.player_id,
        "fantasy_entity_id": row.fantasy_id,
        "fantasy_player_id": row.fantasy_player_id,
        "player_name": row.player_name,
        "canonical_team_id": row.team_id,
        "opponent_team_id": row.opponent_team_id,
        "home_away": row.home_away,
        "fantasy_position": _json_value(row.fantasy_position),
        "credits": _optional_float(row.credits),
        "source_availability_status": row.source_availability_status,
        "resolved_availability": row.resolved_availability,
        "availability_decision_source": row.availability_decision_source,
        "baseline_expected_minutes": _optional_float(row.baseline_expected_minutes),
        "adjusted_expected_minutes": _optional_float(row.adjusted_expected_minutes),
        "minutes_delta_due_to_absences": _optional_float(
            row.minutes_delta_due_to_absences
        ),
        "direct_prediction": _optional_float(row.direct_prediction),
        "expected_fp_per_min": _optional_float(row.expected_fp_per_min),
        "expected_fp_before_adjustment": _optional_float(
            row.expected_fp_before_adjustment
        ),
        "expected_fp": _optional_float(row.expected_fp),
        "fp_delta_due_to_absences": _optional_float(row.fp_delta_due_to_absences),
        "phase4b_central_fp_before_adjustment": _optional_float(
            row.phase4b_central_fp_before_adjustment
        ),
        "phase4b_central_fp": _optional_float(row.phase4b_central_fp),
        "phase6b_expected_fp_before_adjustment": _optional_float(getattr(
            row, "phase6b_expected_fp_before_availability_adjustment", None
        )),
        "phase6b_expected_fp": _optional_float(getattr(
            row, "phase6b_expected_fp", None
        )),
        "phase6c_expected_fp_before_adjustment": _optional_float(getattr(
            row, "phase6c_expected_fp_before_availability_adjustment", None
        )),
        "phase6c_expected_fp": _optional_float(getattr(
            row, "phase6c_expected_fp", None
        )),
        "phase6c_location_delta": _optional_float(getattr(
            row, "phase6c_location_delta", None
        )),
        "expected_usage_next_game": _optional_float(getattr(
            row, "expected_usage_next_game", None
        )),
        "median_fp": _optional_float(row.median_fp),
        "p10_fp": _optional_float(row.p10_fp),
        "p25_fp": _optional_float(row.p25_fp),
        "p50_fp": _optional_float(row.p50_fp),
        "p75_fp": _optional_float(row.p75_fp),
        "p90_fp": _optional_float(row.p90_fp),
        "p95_fp": _optional_float(row.p95_fp),
        "prob_fp_ge_20": _optional_float(row.prob_fp_ge_20),
        "prob_fp_ge_25": _optional_float(row.prob_fp_ge_25),
        "prob_fp_ge_30": _optional_float(row.prob_fp_ge_30),
        "prob_fp_ge_35": _optional_float(row.prob_fp_ge_35),
        "prob_fp_ge_40": _optional_float(row.prob_fp_ge_40),
        "prob_fp_le_5": _optional_float(getattr(row, "prob_fp_le_5", None)),
        "prob_fp_le_10": _optional_float(getattr(row, "prob_fp_le_10", None)),
        "prob_fp_le_15": _optional_float(getattr(row, "prob_fp_le_15", None)),
        "distribution_width": _optional_float(row.distribution_width),
        "p90_minus_p50": _optional_float(row.p90_minus_p50),
        "p50_minus_p10": _optional_float(row.p50_minus_p10),
        "p95_minus_expected": _optional_float(row.p95_minus_expected),
        "history_sample_count": int(row.history_sample_count),
        "distribution_confidence": row.distribution_confidence,
        "lower_prediction_interval": _optional_float(row.lower_prediction_interval),
        "upper_prediction_interval": _optional_float(row.upper_prediction_interval),
        "interval_calibration_status": row.interval_calibration_status,
        "career_el_games_before": int(row.career_el_games_before),
        "season_games_before": int(row.season_games_before),
        "cold_start_flag": bool(row.cold_start_flag),
        "feature_completeness": float(row.feature_completeness),
        "freshness_status": row.freshness_status,
        "prediction_status": row.prediction_status,
        "missing_role_status": row.missing_role_status,
        "manual_expected_minutes_if_available": _optional_float(
            getattr(row, "manual_expected_minutes_if_available", None)
        ),
        "performance_model_version": row.performance_model_version,
        "redistribution_model_version": row.redistribution_model_version,
        "probabilistic_model_version": row.probabilistic_model_version,
        "calibration_version": row.calibration_version,
        "probabilistic_artifact_fingerprint": row.probabilistic_artifact_fingerprint,
        "predictive_model_version": _json_value(getattr(
            row, "predictive_model_version", None
        )),
        "predictive_calibration_version": _json_value(getattr(
            row, "predictive_calibration_version", None
        )),
        "predictive_artifact_fingerprint": _json_value(getattr(
            row, "predictive_artifact_fingerprint", None
        )),
        "predictive_feature_manifest_fingerprint": _json_value(getattr(
            row, "predictive_feature_manifest_fingerprint", None
        )),
        "feature_manifest_fingerprint": manifest_fingerprint,
        "feature_snapshot_fingerprint": row.feature_snapshot_fingerprint,
        "feature_snapshot_json": json.dumps(feature_snapshot, sort_keys=True, default=str),
        "provenance_json": json.dumps(provenance, sort_keys=True, default=str),
        "explanation_json": json.dumps(explanation, sort_keys=True, default=str),
        "row_fingerprint": row_fingerprint,
    }
    prediction_frame = pd.DataFrame([prediction_row])
    connection.register("_phase6a_prediction", prediction_frame)
    try:
        connection.execute(
            "INSERT INTO live_player_predictions BY NAME SELECT * FROM _phase6a_prediction"
        )
    finally:
        connection.unregister("_phase6a_prediction")


def _write_outputs(
    root: Path,
    run_id: str,
    season_code: str,
    generated_at: datetime,
    resolution: MatchdayResolution,
    coverage: Mapping[str, Any],
    freshness: Mapping[str, Any],
    scenario_definitions: Sequence[Mapping[str, Any]],
    summaries: Mapping[str, Any],
    predictions: pd.DataFrame,
    validation: ArtifactValidation,
    scoring: ScoringRuleCompatibility,
    warnings: Sequence[str],
    probabilistic_validation: ProbabilisticArtifactValidation,
    predictive_validation: PredictiveUpliftArtifactValidation,
) -> tuple[Path, Path, bool]:
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "predictions.json"
    csv_path = directory / "predictions.csv"
    records = [_record_for_output(row) for row in predictions.itertuples()]
    hierarchy = []
    for definition in scenario_definitions:
        scenario_id = definition["scenario_id"]
        hierarchy.append({
            **definition,
            "team_availability_summary": summaries.get(scenario_id, {}),
            "players": [row for row in records if row["scenario_id"] == scenario_id],
        })
    payload = {
        "prediction_run_id": run_id,
        "prediction_generated_at": generated_at.isoformat(),
        "season": season_code,
        "fantasy_matchday_resolution": asdict(resolution),
        "coverage": coverage,
        "source_freshness": freshness,
        "models": asdict(validation),
        "probabilistic_model": asdict(probabilistic_validation),
        "predictive_uplift_model": asdict(predictive_validation),
        "player_scoring_rules": asdict(scoring),
        "production_ready": scoring.production_ready,
        "interval_note": "Intervals are not recalibrated after availability adjustment.",
        "scenarios": hierarchy,
        "warnings": list(dict.fromkeys(warnings)),
    }
    json_bytes = (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode()
    csv_columns = list(records[0]) if records else _output_columns()
    csv_frame = pd.DataFrame(records, columns=csv_columns)
    csv_bytes = csv_frame.to_csv(index=False).encode()
    reused = _write_immutable(json_path, json_bytes) | _write_immutable(csv_path, csv_bytes)
    return json_path, csv_path, reused


def _record_for_output(row: Any) -> dict[str, Any]:
    values = {
        "scenario_id": row.scenario_id,
        "prediction_generated_at": row.prediction_generated_at,
        "season": row.season,
        "fantasy_matchday": row.fantasy_matchday,
        "game_id": row.game_id,
        "game_date": row.game_date,
        "tip_time": row.scheduled_tip_time,
        "player_id": row.player_id,
        "fantasy_player_id": row.fantasy_player_id,
        "player_name": row.player_name,
        "team": row.team_id,
        "opponent": row.opponent_team_id,
        "home_away": row.home_away,
        "fantasy_position": row.fantasy_position,
        "credits": row.credits,
        "source_availability_status": row.source_availability_status,
        "resolved_availability": row.resolved_availability,
        "availability_decision_source": row.availability_decision_source,
        "baseline_expected_minutes": row.baseline_expected_minutes,
        "uncalibrated_expected_minutes": getattr(
            row, "uncalibrated_expected_minutes", None
        ),
        "previous_season_minutes_avg": getattr(
            row, "previous_season_minutes_avg", None
        ),
        "previous_season_last5_minutes_avg": getattr(
            row, "previous_season_last5_minutes_avg", None
        ),
        "minutes_calibration_weight": getattr(
            row, "minutes_calibration_weight", None
        ),
        "minutes_calibration_version": getattr(
            row, "minutes_calibration_version", None
        ),
        "expected_role": getattr(row, "expected_role", None),
        "preparation_games": getattr(row, "preparation_games", 0),
        "preparation_latest_game": getattr(row, "preparation_latest_game", None),
        "preparation_weight": getattr(row, "preparation_weight", 0.0),
        "preparation_minutes": getattr(row, "preparation_minutes", None),
        "preparation_starter_rate": getattr(row, "preparation_starter_rate", None),
        "preparation_fp_per_minute": getattr(row, "preparation_fp_per_minute", None),
        "preparation_expected_starter_rate": getattr(row, "preparation_expected_starter_rate", None),
        "preparation_minutes_delta": getattr(row, "preparation_minutes_delta", 0.0),
        "preparation_role_change": getattr(row, "preparation_role_change", None),
        "preparation_form_fp_delta": getattr(row, "preparation_form_fp_delta", 0.0),
        "preparation_fp_delta": getattr(row, "preparation_fp_delta", 0.0),
        "adjusted_expected_minutes": row.adjusted_expected_minutes,
        "minutes_delta_due_to_absences": row.minutes_delta_due_to_absences,
        "direct_prediction": row.direct_prediction,
        "expected_fp_per_min": row.expected_fp_per_min,
        "expected_fp_before_adjustment": row.expected_fp_before_adjustment,
        "expected_fp": row.expected_fp,
        "fp_delta_due_to_absences": row.fp_delta_due_to_absences,
        "phase4b_central_fp_before_adjustment": row.phase4b_central_fp_before_adjustment,
        "phase4b_central_fp": row.phase4b_central_fp,
        "phase6b_expected_fp_before_adjustment": getattr(
            row, "phase6b_expected_fp_before_availability_adjustment", None
        ),
        "phase6b_expected_fp": getattr(row, "phase6b_expected_fp", None),
        "phase6c_expected_fp_before_adjustment": getattr(
            row, "phase6c_expected_fp_before_availability_adjustment", None
        ),
        "phase6c_expected_fp": getattr(row, "phase6c_expected_fp", None),
        "phase6c_location_delta": getattr(row, "phase6c_location_delta", None),
        "expected_usage_next_game": getattr(row, "expected_usage_next_game", None),
        "median_fp": row.median_fp,
        "p10_fp": row.p10_fp,
        "p25_fp": row.p25_fp,
        "p50_fp": row.p50_fp,
        "p75_fp": row.p75_fp,
        "p90_fp": row.p90_fp,
        "p95_fp": row.p95_fp,
        "prob_fp_ge_20": row.prob_fp_ge_20,
        "prob_fp_ge_25": row.prob_fp_ge_25,
        "prob_fp_ge_30": row.prob_fp_ge_30,
        "prob_fp_ge_35": row.prob_fp_ge_35,
        "prob_fp_ge_40": row.prob_fp_ge_40,
        "prob_fp_le_5": getattr(row, "prob_fp_le_5", None),
        "prob_fp_le_10": getattr(row, "prob_fp_le_10", None),
        "prob_fp_le_15": getattr(row, "prob_fp_le_15", None),
        "distribution_width": row.distribution_width,
        "p90_minus_p50": row.p90_minus_p50,
        "p50_minus_p10": row.p50_minus_p10,
        "p95_minus_expected": row.p95_minus_expected,
        "history_sample_count": row.history_sample_count,
        "distribution_confidence": row.distribution_confidence,
        "lower_prediction_interval": row.lower_prediction_interval,
        "upper_prediction_interval": row.upper_prediction_interval,
        "interval_calibration_status": row.interval_calibration_status,
        "career_el_games_before": row.career_el_games_before,
        "season_games_before": row.season_games_before,
        "cold_start_flag": row.cold_start_flag,
        "feature_completeness": row.feature_completeness,
        "freshness_status": row.freshness_status,
        "prediction_status": row.prediction_status,
        "missing_role_status": row.missing_role_status,
        "performance_model_version": row.performance_model_version,
        "redistribution_model_version": row.redistribution_model_version,
        "probabilistic_model_version": row.probabilistic_model_version,
        "calibration_version": row.calibration_version,
        "probabilistic_artifact_fingerprint": row.probabilistic_artifact_fingerprint,
        "predictive_model_version": getattr(row, "predictive_model_version", None),
        "predictive_calibration_version": getattr(
            row, "predictive_calibration_version", None
        ),
        "predictive_artifact_fingerprint": getattr(
            row, "predictive_artifact_fingerprint", None
        ),
        "predictive_feature_manifest_fingerprint": getattr(
            row, "predictive_feature_manifest_fingerprint", None
        ),
        "feature_manifest_fingerprint": row.feature_manifest_fingerprint,
        "feature_snapshot_fingerprint": row.feature_snapshot_fingerprint,
        "expected_fp_per_credit": row.expected_fp_per_credit,
    }
    return {key: _json_value(value) for key, value in values.items()}


def _output_columns() -> list[str]:
    return list(_record_for_output(type("Empty", (), {
        key: None for key in (
            "scenario_id prediction_generated_at season fantasy_matchday game_id game_date "
            "scheduled_tip_time player_id fantasy_player_id player_name team_id "
            "opponent_team_id home_away fantasy_position credits source_availability_status "
            "resolved_availability availability_decision_source baseline_expected_minutes "
            "uncalibrated_expected_minutes previous_season_minutes_avg "
            "previous_season_last5_minutes_avg minutes_calibration_weight "
            "minutes_calibration_version expected_role "
            "adjusted_expected_minutes minutes_delta_due_to_absences direct_prediction "
            "expected_fp_per_min expected_fp_before_adjustment expected_fp "
            "fp_delta_due_to_absences phase4b_central_fp_before_adjustment "
            "phase4b_central_fp phase6b_expected_fp_before_availability_adjustment "
            "phase6b_expected_fp phase6c_expected_fp_before_availability_adjustment "
            "phase6c_expected_fp phase6c_location_delta expected_usage_next_game "
            "median_fp p10_fp p25_fp p50_fp p75_fp p90_fp p95_fp "
            "prob_fp_ge_20 prob_fp_ge_25 prob_fp_ge_30 prob_fp_ge_35 prob_fp_ge_40 "
            "prob_fp_le_5 prob_fp_le_10 prob_fp_le_15 "
            "distribution_width p90_minus_p50 p50_minus_p10 p95_minus_expected "
            "history_sample_count distribution_confidence lower_prediction_interval "
            "upper_prediction_interval "
            "interval_calibration_status career_el_games_before season_games_before "
            "cold_start_flag feature_completeness freshness_status prediction_status "
            "missing_role_status performance_model_version redistribution_model_version "
            "probabilistic_model_version calibration_version probabilistic_artifact_fingerprint "
            "predictive_model_version predictive_calibration_version "
            "predictive_artifact_fingerprint predictive_feature_manifest_fingerprint "
            "feature_manifest_fingerprint feature_snapshot_fingerprint expected_fp_per_credit"
        ).split()
    })()).keys())


def _team_summary(frame: pd.DataFrame, out_ids: Sequence[str]) -> dict[str, Any]:
    first = frame.iloc[0]
    return {
        "known_out_players": list(out_ids),
        "number_players_out": int(first.get("number_players_out", len(out_ids))),
        "total_missing_expected_minutes": float(first.get("missing_expected_minutes", 0)),
        "missing_expected_minutes_share": float(first.get("missing_rotation_share", 0)),
        "missing_starter_minutes": float(first.get("missing_starter_minutes", 0)),
        "missing_bench_minutes": float(first.get("missing_bench_minutes", 0)),
        "missing_guard_minutes": float(first.get("missing_guard_minutes", 0)),
        "missing_forward_minutes": float(first.get("missing_forward_minutes", 0)),
        "missing_center_minutes": float(first.get("missing_center_minutes", 0)),
        "missing_FP_context": float(first.get("missing_fp_before", 0)),
        "missing_FP_per_min_context": float(first.get("missing_fp_per_min_before", 0)),
        "missing_ballhandling_proxy": float(first.get("missing_ballhandling_proxy", 0)),
        "missing_rebounding_proxy": float(first.get("missing_rebounding_proxy", 0)),
        "adjusted_team_minutes": float(frame.adjusted_expected_minutes.sum()),
        "minimum_player_minutes": float(frame.adjusted_expected_minutes.min()),
        "maximum_player_minutes": float(frame.adjusted_expected_minutes.max()),
    }


def _coverage(pool: PlayerPoolResult, slate: pd.DataFrame) -> dict[str, Any]:
    statuses = slate.get(
        "resolved_availability_status", pd.Series(dtype="string")
    ).fillna("UNKNOWN")
    result = {
        "players_in_fantasy_market": pool.market_players,
        "players_mapped_to_canonical": pool.mapped_players,
        "players_on_current_roster": pool.roster_matched_players,
        "mapping_ambiguous": pool.mapping_ambiguous,
        "mapping_unmatched": pool.mapping_unmatched,
        "roster_or_team_mismatch": pool.roster_mismatches,
        "players_out": int(statuses.isin(["OUT", "SUSPENDED", "NOT_REGISTERED"]).sum()),
        "players_questionable": int(statuses.eq("QUESTIONABLE").sum()),
        "players_unknown": int(statuses.eq("UNKNOWN").sum()),
    }
    denominator = max(pool.market_players, 1)
    result["mapped_percentage"] = 100.0 * pool.mapped_players / denominator
    result["current_roster_percentage"] = 100.0 * pool.roster_matched_players / denominator
    return result


def _prediction_coverage(
    pool: PlayerPoolResult, predictions: pd.DataFrame, slate: pd.DataFrame
) -> dict[str, Any]:
    first_scenario = predictions[
        predictions.scenario_id.eq(predictions.scenario_id.iloc[0])
    ] if not predictions.empty else predictions
    scored = int(first_scenario.prediction_status.eq("SCORED").sum()) if not first_scenario.empty else 0
    cold = int(first_scenario.cold_start_flag.sum()) if not first_scenario.empty else 0
    out = int(first_scenario.prediction_status.eq("OUT").sum()) if not first_scenario.empty else 0
    unscorable = pool.market_players - scored - out
    stale = int(first_scenario.freshness_status.str.startswith("STALE").sum()) if not first_scenario.empty else 0
    denominator = max(pool.market_players, 1)
    return {
        "players_scored": scored,
        "players_unscorable": max(unscorable, 0),
        "cold_start_players": cold,
        "players_out_in_first_scenario": out,
        "players_with_stale_data": stale,
        "scored_percentage": 100.0 * scored / denominator,
        "unscorable_percentage": 100.0 * max(unscorable, 0) / denominator,
        "cold_start_percentage": 100.0 * cold / denominator,
    }


def _availability_state(slate: pd.DataFrame) -> dict[str, Any]:
    columns = [
        column for column in (
            "player_id", "game_id", "team_id", "resolved_availability_status",
            "availability_source", "availability_timestamp", "manual_override_active",
            "availability_conflict",
        ) if column in slate
    ]
    return {"players": slate[columns].to_dict("records") if columns else []}


def _parse_decision_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("player decisions must be an object")
    output: dict[str, str] = {}
    for player, item in value.items():
        decision = item.get("decision") if isinstance(item, dict) else item
        if decision is None:
            continue
        normalized = str(decision).upper()
        if normalized not in {"PLAY", "OUT", "UNKNOWN", "LIMITED"}:
            raise ValueError(f"invalid decision for {player}: {decision}")
        output[str(player)] = normalized
    return output


def _parse_role_estimates(value: Any) -> dict[str, float]:
    output: dict[str, float] = {}
    if not isinstance(value, dict):
        return output
    for player, item in value.items():
        if not isinstance(item, dict) or "manual_expected_minutes_if_available" not in item:
            continue
        minutes = float(item["manual_expected_minutes_if_available"])
        if not 0 <= minutes <= 40:
            raise ValueError(f"manual role estimate for {player} must be 0-40")
        output[str(player)] = minutes
    return output


def _canonicalize_decisions(
    slate: pd.DataFrame, decisions: Mapping[str, str]
) -> dict[str, str]:
    fantasy_map = {
        str(row.fantasy_player_id): str(row.player_id)
        for row in slate.itertuples()
        if hasattr(row, "fantasy_player_id") and pd.notna(row.fantasy_player_id)
    }
    valid = set(slate.player_id.astype(str))
    result: dict[str, str] = {}
    for supplied, decision in decisions.items():
        canonical = supplied if supplied in valid else fantasy_map.get(supplied)
        if canonical is None:
            raise ValueError(f"decision player is not in the current slate: {supplied}")
        result[canonical] = decision
    return result


def _canonicalize_role_estimates(
    slate: pd.DataFrame, roles: Mapping[str, float]
) -> dict[str, float]:
    output: dict[str, float] = {}
    for supplied, value in roles.items():
        canonical = next(iter(_canonicalize_decisions(
            slate, {supplied: "PLAY"}
        )))
        output[canonical] = float(value)
    return output


def _broad_position(value: Any) -> str:
    text = str(value or "").upper()
    if any(token in text for token in ("GUARD", "PG", "SG")):
        return "GUARD"
    if any(token in text for token in ("CENTER", "C")):
        return "CENTER"
    if any(token in text for token in ("FORWARD", "SF", "PF", "F")):
        return "FORWARD"
    return "UNKNOWN"


def _slate_round(slate: pd.DataFrame) -> int | None:
    if slate.empty or "round_number" not in slate:
        return None
    values = pd.to_numeric(slate["round_number"], errors="coerce").dropna().unique()
    return int(values[0]) if len(values) == 1 else None


def _feature_row_fingerprint(row: pd.Series, columns: Sequence[str]) -> str:
    return _json_sha({column: _json_value(row.get(column)) for column in columns})


def _prediction_feature_columns() -> tuple[str, ...]:
    return tuple(dict.fromkeys([
        *frozen_required_features(), *USAGE_OFFENSIVE_FEATURES,
        "expected_usage_next_game", "uncalibrated_expected_minutes",
        "previous_season_games", "previous_season_minutes_avg",
        "previous_season_last5_minutes_avg", "previous_season_history_max_time",
        "minutes_calibration_weight", "minutes_calibration_version",
    ]))


def _feature_fingerprint() -> str:
    values = {
        "direct": feature_manifest("CORE_ROTATION", include_player_id=True)["sha256"],
        "minutes": phase4b_feature_manifest("MINUTES_ROLE")["sha256"],
        "production": phase4b_feature_manifest("PRODUCTION")["sha256"],
    }
    return _json_sha(values)


def _query_fingerprint(connection: Any, sql: str, parameters: Sequence[Any]) -> str:
    return _json_sha(connection.execute(sql, list(parameters)).fetchall())


def _run_exists(database_path: Path | str, run_id: str) -> bool:
    with connect_database(database_path, read_only=True) as connection:
        return bool(connection.execute(
            "SELECT count(*) FROM live_prediction_runs WHERE prediction_run_id=?",
            [run_id],
        ).fetchone()[0])


def _write_immutable(path: Path, content: bytes) -> bool:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"immutable prediction artifact conflict: {path}")
        return True
    path.write_bytes(content)
    return False


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return int(parsed) if parsed is not None else None


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
