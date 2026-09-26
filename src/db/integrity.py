"""Canonical database integrity checks used by tests and quality reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from duckdb import DuckDBPyConnection

from .database import DEFAULT_DATABASE_PATH, connect_database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class IntegrityCheck:
    name: str
    passed: bool
    observed: int | float | str
    expectation: str


class DatabaseIntegrityError(RuntimeError):
    """Raised when one or more core canonical invariants fail."""


def validate_database(
        database_path: Path | str = DEFAULT_DATABASE_PATH,
        *,
        raise_on_failure: bool = False,
) -> dict[str, Any]:
    with connect_database(database_path, read_only=True) as connection:
        checks = _checks(connection)
        artifacts = connection.execute(
            "SELECT raw_path, content_sha256, auth_data_included FROM raw_artifacts"
        ).fetchall()
    missing_files = 0
    hash_mismatches = 0
    unsafe_paths = 0
    for raw_path, expected_hash, auth_included in artifacts:
        path = Path(str(raw_path))
        path = path if path.is_absolute() else PROJECT_ROOT / path
        if not path.is_file():
            missing_files += 1
            continue
        actual_hash = sha256(path.read_bytes()).hexdigest()
        hash_mismatches += actual_hash != str(expected_hash)
        unsafe_paths += bool(auth_included) or ".auth" in path.parts
    checks.extend(
        [
            IntegrityCheck(
                "raw_artifact_files_exist",
                missing_files == 0,
                missing_files,
                "0 missing raw files",
            ),
            IntegrityCheck(
                "raw_artifact_hashes_match",
                hash_mismatches == 0,
                hash_mismatches,
                "0 content-hash mismatches",
            ),
            IntegrityCheck(
                "raw_artifacts_exclude_auth_state",
                unsafe_paths == 0,
                unsafe_paths,
                "0 auth-bearing/path artifacts",
            ),
        ]
    )
    failures = [check for check in checks if not check.passed]
    result = {
        "passed": not failures,
        "check_count": len(checks),
        "failure_count": len(failures),
        "checks": [asdict(check) for check in checks],
    }
    if failures and raise_on_failure:
        names = ", ".join(check.name for check in failures)
        raise DatabaseIntegrityError(f"Canonical integrity checks failed: {names}")
    return result


def _checks(connection: DuckDBPyConnection) -> list[IntegrityCheck]:
    checks: list[IntegrityCheck] = []

    def zero(name: str, sql: str, expectation: str = "0 violating rows") -> None:
        value = int(connection.execute(sql).fetchone()[0])
        checks.append(IntegrityCheck(name, value == 0, value, expectation))

    zero(
        "games_unique_season_code",
        """
        SELECT count(*)
        FROM (SELECT season_id, game_code
              FROM games
              GROUP BY season_id, game_code
              HAVING count(*) > 1)
        """,
    )
    zero("games_distinct_teams", "SELECT count(*) FROM games WHERE home_team_id = away_team_id")
    zero(
        "completed_games_not_tied",
        """
        SELECT count(*)
        FROM games
        WHERE played
          AND home_score IS NOT NULL
          AND away_score IS NOT NULL
          AND home_score = away_score
        """,
    )
    zero(
        "game_scores_nonnegative",
        "SELECT count(*) FROM games WHERE home_score < 0 OR away_score < 0",
    )
    zero(
        "derived_winner_is_participant",
        """
        SELECT count(*)
        FROM games_with_derived_winner
        WHERE derived_winner_team_id IS NOT NULL
          AND derived_winner_team_id NOT IN (home_team_id, away_team_id)
        """,
    )

    zero(
        "player_games_unique",
        """
        SELECT count(*)
        FROM (SELECT canonical_game_id, canonical_player_id, canonical_team_id
              FROM player_game_stats
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "player_game_team_membership",
        """
        SELECT count(*)
        FROM player_game_stats AS stats
                 JOIN games USING (canonical_game_id)
        WHERE stats.canonical_team_id NOT IN (games.home_team_id, games.away_team_id)
           OR stats.opponent_team_id = stats.canonical_team_id
           OR stats.opponent_team_id NOT IN (games.home_team_id, games.away_team_id)
        """,
    )
    zero(
        "player_minutes_nonnegative",
        "SELECT count(*) FROM player_game_stats WHERE minutes < 0",
    )
    zero(
        "player_counting_stats_nonnegative",
        """
        SELECT count(*)
        FROM player_game_stats
        WHERE points < 0
           OR two_points_made < 0
           OR two_points_attempted < 0
           OR three_points_made < 0
           OR three_points_attempted < 0
           OR free_throws_made < 0
           OR free_throws_attempted < 0
           OR offensive_rebounds < 0
           OR defensive_rebounds < 0
           OR total_rebounds < 0
           OR assists < 0
           OR steals < 0
           OR turnovers < 0
           OR blocks < 0
           OR blocks_received < 0
           OR fouls_committed < 0
           OR fouls_drawn < 0
        """,
    )
    zero(
        "team_games_two_rows_where_present",
        """
        SELECT count(*)
        FROM (SELECT canonical_game_id
              FROM team_game_stats
              GROUP BY canonical_game_id
              HAVING count(*) <> 2)
        """,
    )
    zero(
        "team_game_points_reconcile",
        """
        SELECT count(*)
        FROM (SELECT team.canonical_game_id,
                     team.canonical_team_id,
                     team.points,
                     sum(player.points) AS player_points
              FROM team_game_stats AS team
                       JOIN player_game_stats AS player
                            ON player.canonical_game_id = team.canonical_game_id
                                AND player.canonical_team_id = team.canonical_team_id
              GROUP BY team.canonical_game_id, team.canonical_team_id, team.points)
        WHERE points <> player_points
        """,
    )

    zero(
        "pbp_valid_periods",
        "SELECT count(*) FROM play_by_play_events WHERE period < 1 OR period > 20",
    )
    zero(
        "pbp_clock_parseable",
        """
        SELECT count(*)
        FROM play_by_play_events AS event
        WHERE event.game_clock IS NOT NULL
          AND event.game_clock <> ''
          AND NOT regexp_matches(event.game_clock, '^[0-9]{2}:[0-9]{2}$')
          AND NOT EXISTS (SELECT 1
                          FROM data_anomalies AS anomaly
                          WHERE anomaly.entity_type = 'GAME'
                            AND anomaly.entity_id = event.canonical_game_id
                            AND anomaly.anomaly_code = 'INVALID_PBP_CLOCK'
                            AND anomaly.quarantined
                            AND anomaly.resolved_at IS NULL)
        """,
        "0 unquarantined invalid clock rows",
    )
    zero(
        "pbp_source_order_unique",
        """
        SELECT count(*)
        FROM (SELECT canonical_game_id, source_sequence
              FROM play_by_play_events
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "shots_unique_source_order",
        """
        SELECT count(*)
        FROM (SELECT canonical_game_id, source_sequence
              FROM shots
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "shots_team_is_game_participant",
        """
        SELECT count(*)
        FROM shots
                 JOIN games USING (canonical_game_id)
        WHERE shots.canonical_team_id IS NOT NULL
          AND shots.canonical_team_id NOT IN (games.home_team_id, games.away_team_id)
        """,
    )

    zero(
        "fantasy_ids_unique_per_snapshot",
        """
        SELECT count(*)
        FROM (SELECT snapshot_batch_id, fantasy_entity_id
              FROM fantasy_market_snapshots
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "fantasy_prices_valid",
        "SELECT count(*) FROM fantasy_market_snapshots WHERE credits IS NULL OR credits < 0",
    )
    zero(
        "fantasy_snapshot_provenance_present",
        """
        SELECT count(*)
        FROM fantasy_market_snapshots
        WHERE observed_at IS NULL
           OR season_code IS NULL
           OR matchday_id IS NULL
           OR matchday_number IS NULL
           OR source_artifact_id IS NULL
        """,
    )
    zero(
        "historical_status_overlay_not_claimed_asof",
        """
        SELECT count(*)
        FROM fantasy_market_snapshots
        WHERE status_semantics = 'CURRENT_OVERLAY_AT_COLLECTION_NOT_HISTORICAL'
          AND status_valid_as_of_matchday
        """,
    )
    zero(
        "leakage_safe_view_blocks_unsafe_status",
        """
        SELECT count(*)
        FROM leakage_safe_fantasy_market
        WHERE NOT status_valid_as_of_matchday
          AND (is_injured IS NOT NULL OR probability_of_playing IS NOT NULL
            OR started_from_bench IS NOT NULL OR is_on_fire IS NOT NULL
            OR average_points IS NOT NULL)
        """,
    )
    zero(
        "confirmed_crosswalk_has_player",
        """
        SELECT count(*)
        FROM fantasy_player_crosswalk
        WHERE mapping_status = 'MATCHED'
          AND canonical_player_id IS NULL
        """,
    )
    zero(
        "weak_crosswalk_not_silent",
        """
        SELECT count(*)
        FROM fantasy_player_crosswalk
        WHERE mapping_status <> 'MATCHED'
          AND canonical_player_id IS NOT NULL
        """,
    )
    zero(
        "crosswalk_one_active_mapping",
        """
        SELECT count(*)
        FROM (SELECT fantasy_entity_id, season_code
              FROM fantasy_player_crosswalk
              WHERE valid_to IS NULL
                AND mapping_status = 'MATCHED'
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "availability_has_observation_time",
        "SELECT count(*) FROM availability_events WHERE observed_at IS NULL",
    )
    zero(
        "availability_snapshot_timing_consistent",
        """
        SELECT count(*)
        FROM availability_snapshots
        WHERE (timing_status = 'PRE_GAME' AND coalesce(hours_before_tip, -1) < 0)
           OR (timing_status = 'POST_GAME' AND coalesce(hours_before_tip, 1) >= 0)
           OR (timing_status = 'UNKNOWN' AND hours_before_tip IS NOT NULL)
        """,
    )
    zero(
        "availability_snapshot_source_priority_valid",
        """
        SELECT count(*)
        FROM availability_snapshots
        WHERE source_priority <> CASE source_type
                                     WHEN 'MANUAL_OVERRIDE' THEN 500
                                     WHEN 'OFFICIAL_CLUB' THEN 400
                                     WHEN 'OFFICIAL_EUROLEAGUE' THEN 300
                                     WHEN 'OFFICIAL_FANTASY' THEN 200
                                     WHEN 'OTHER_VERIFIED' THEN 100
                                     WHEN 'UNKNOWN' THEN 0
                                     ELSE -1 END
        """,
    )
    zero(
        "availability_override_scope_valid",
        """
        SELECT count(*)
        FROM availability_override_events
        WHERE action ='SET'
          AND canonical_game_id IS NULL
          AND fantasy_matchday IS NULL
          AND expires_at IS NULL
        """,
    )
    zero(
        "live_schedule_batch_game_unique",
        """
        SELECT count(*)
        FROM (SELECT snapshot_batch_id, canonical_game_id
              FROM live_schedule_snapshots
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "live_roster_batch_player_team_unique",
        """
        SELECT count(*)
        FROM (SELECT snapshot_batch_id, canonical_player_id, canonical_team_id
              FROM live_roster_snapshots
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "prospective_runs_claim_prospective",
        "SELECT count(*) FROM prospective_prediction_runs WHERE NOT created_before_outcomes",
    )
    slate_exists = bool(connection.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='current_upcoming_player_slate_v1'"
    ).fetchone()[0])
    if slate_exists:
        zero(
            "current_slate_player_game_unique",
            """
            SELECT count(*)
            FROM (SELECT game_id, player_id
                  FROM current_upcoming_player_slate_v1
                  GROUP BY ALL
                  HAVING count(*) > 1)
            """,
        )
    else:
        checks.append(IntegrityCheck(
            "current_slate_player_game_unique", True, 0,
            "0 duplicate current-slate player-games (table not generated yet)",
        ))

    zero(
        "role_limit_override_scope_valid",
        """
        SELECT count(*)
        FROM role_limit_override_events
        WHERE action ='SET'
          AND canonical_game_id IS NULL
          AND fantasy_matchday IS NULL
          AND expires_at IS NULL
        """,
    )
    zero(
        "role_limit_override_value_valid",
        """
        SELECT count(*)
        FROM role_limit_override_events
        WHERE action ='SET'
          AND (
            limit_type IS NULL
           OR limit_value IS NULL
           OR (limit_type IN ('MAX_MINUTES'
            , 'EXPECTED_MINUTES')
          AND (limit_value
            < 0
           OR limit_value
            > 40))
           OR (limit_type='PERCENT_REDUCTION'
          AND (limit_value
            < 0
           OR limit_value
            > 1))
            )
        """,
    )
    zero(
        "absence_scenario_outputs_unique",
        """
        SELECT count(*)
        FROM (SELECT absence_scenario_id, canonical_player_id
              FROM absence_scenario_player_outputs
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "absence_scenario_minutes_possible",
        """
        SELECT count(*)
        FROM absence_scenario_player_outputs
        WHERE baseline_expected_minutes < 0
           OR baseline_expected_minutes > 40
           OR adjusted_expected_minutes < 0
           OR adjusted_expected_minutes > 40
        """,
    )
    zero(
        "generated_scenarios_conserve_200_minutes",
        """
        SELECT count(*)
        FROM (SELECT output.absence_scenario_id,
                     sum(output.adjusted_expected_minutes) AS total_minutes
              FROM absence_scenario_player_outputs AS output
          JOIN absence_scenarios AS scenario USING (absence_scenario_id)
              WHERE scenario.scenario_status='GENERATED'
              GROUP BY output.absence_scenario_id)
        WHERE abs(total_minutes - 200.0) > 0.000001
        """,
    )
    zero(
        "live_predictions_precede_game_cutoff",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_generated_at >= scheduled_tip_time
        """,
    )
    zero(
        "live_prediction_feature_cutoff_is_not_future",
        """
        SELECT count(*)
        FROM live_prediction_runs
        WHERE feature_cutoff_time <> prediction_generated_at
           OR feature_cutoff_time > current_timestamp
        """,
    )
    zero(
        "live_prediction_rows_unique",
        """
        SELECT count(*)
        FROM (SELECT prediction_run_id, scenario_id, fantasy_entity_id
              FROM live_player_predictions
              GROUP BY ALL
              HAVING count(*) > 1)
        """,
    )
    zero(
        "live_prediction_scenario_membership_valid",
        """
        SELECT count(*)
        FROM live_player_predictions AS prediction
                 LEFT JOIN live_prediction_scenarios AS scenario
                           ON scenario.prediction_scenario_id = prediction.prediction_scenario_id
                               AND scenario.prediction_run_id = prediction.prediction_run_id
                               AND scenario.scenario_id = prediction.scenario_id
        WHERE scenario.prediction_scenario_id IS NULL
        """,
    )
    zero(
        "live_prediction_player_team_membership_valid",
        """
        SELECT count(*)
        FROM live_player_predictions AS prediction
                 JOIN games AS game ON game.canonical_game_id = prediction.canonical_game_id
        WHERE prediction.canonical_team_id NOT IN (game.home_team_id, game.away_team_id)
           OR prediction.opponent_team_id NOT IN (game.home_team_id, game.away_team_id)
           OR prediction.canonical_team_id = prediction.opponent_team_id
        """,
    )
    zero(
        "live_prediction_team_summaries_conserve_200",
        """
        SELECT count(*)
        FROM live_prediction_scenarios AS scenario,
             json_each(scenario.team_summary_json) AS team
        WHERE json_extract_string(team.value, '$.redistribution_error') IS NULL
          AND json_extract_string(team.value, '$.missing_role_status') IS NULL
          AND abs(cast(json_extract(team.value, '$.adjusted_team_minutes') AS DOUBLE)
            - 200.0) > 0.000001
        """,
    )
    zero(
        "live_prediction_model_versions_consistent",
        """
        SELECT count(*)
        FROM live_player_predictions AS prediction
                 JOIN live_prediction_runs AS run USING (prediction_run_id)
        WHERE prediction.performance_model_version <> run.performance_model_version
           OR prediction.redistribution_model_version <> run.redistribution_model_version
           OR prediction.probabilistic_model_version <> run.probabilistic_model_version
           OR prediction.calibration_version <> run.calibration_version
           OR prediction.probabilistic_artifact_fingerprint
            <> run.probabilistic_artifact_fingerprint
           OR prediction.feature_manifest_fingerprint <> run.feature_manifest_fingerprint
        """,
    )
    zero(
        "live_probabilistic_quantiles_monotonic",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND (
                  p10_fp <= p25_fp AND p25_fp <= p50_fp AND p50_fp <= p75_fp
                      AND p75_fp <= p90_fp AND p90_fp <= p95_fp
                  ) IS
            DISTINCT
        FROM true
        """,
    )
    zero(
        "live_probabilistic_probabilities_in_unit_interval",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND (
                  prob_fp_ge_20 BETWEEN 0 AND 1
                      AND prob_fp_ge_25 BETWEEN 0 AND 1
                      AND prob_fp_ge_30 BETWEEN 0 AND 1
                      AND prob_fp_ge_35 BETWEEN 0 AND 1
                      AND prob_fp_ge_40 BETWEEN 0 AND 1
                  ) IS
            DISTINCT
        FROM true
        """,
    )
    zero(
        "live_probabilistic_threshold_probabilities_monotonic",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND (
                  prob_fp_ge_20 >= prob_fp_ge_25
                      AND prob_fp_ge_25 >= prob_fp_ge_30
                      AND prob_fp_ge_30 >= prob_fp_ge_35
                      AND prob_fp_ge_35 >= prob_fp_ge_40
                  ) IS
            DISTINCT
        FROM true
        """,
    )
    zero(
        "live_probabilistic_versions_valid",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND (
            probabilistic_model_version IS DISTINCT FROM
                'phase6b_probabilistic_player_outcome_frozen_v1'
                OR calibration_version IS DISTINCT FROM
                'phase6b_chronological_calibration_v1'
                OR probabilistic_artifact_fingerprint IS NULL
            )
        """,
    )
    zero(
        "live_predictive_probabilities_in_unit_interval",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND predictive_model_version IS NOT NULL
          AND (
                  prob_fp_le_5 BETWEEN 0 AND 1
                      AND prob_fp_le_10 BETWEEN 0 AND 1
                      AND prob_fp_le_15 BETWEEN 0 AND 1
                  ) IS
            DISTINCT
        FROM true
        """,
    )
    zero(
        "live_predictive_downside_probabilities_monotonic",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND predictive_model_version IS NOT NULL
          AND (
                  prob_fp_le_5 <= prob_fp_le_10
                      AND prob_fp_le_10 <= prob_fp_le_15
                  ) IS
            DISTINCT
        FROM true
        """,
    )
    zero(
        "live_predictive_model_versions_consistent",
        """
        SELECT count(*)
        FROM live_player_predictions AS prediction
                 JOIN live_prediction_runs AS run USING (prediction_run_id)
        WHERE prediction.predictive_model_version
                  IS DISTINCT
        FROM run.predictive_model_version
            OR prediction.predictive_calibration_version
            IS DISTINCT
        FROM run.predictive_calibration_version
            OR prediction.predictive_artifact_fingerprint
            IS DISTINCT
        FROM run.predictive_artifact_fingerprint
            OR prediction.predictive_feature_manifest_fingerprint
            IS DISTINCT
        FROM run.predictive_feature_manifest_fingerprint
        """,
    )
    zero(
        "live_predictive_versions_valid",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status = 'SCORED'
          AND predictive_model_version IS NOT NULL
          AND (
            predictive_model_version IS DISTINCT FROM
                'phase6c_predictive_uplift_frozen_v1'
                OR predictive_calibration_version IS DISTINCT FROM
                'phase6c_chronological_calibration_v1'
                OR predictive_artifact_fingerprint IS NULL
                OR predictive_feature_manifest_fingerprint IS NULL
                OR phase6c_expected_fp IS NULL
                OR expected_usage_next_game IS NULL
            )
        """,
    )
    zero(
        "live_nonparticipants_have_no_active_distribution",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE prediction_status <> 'SCORED'
          AND (expected_fp IS NOT NULL OR median_fp IS NOT NULL OR p90_fp IS NOT NULL
            OR prob_fp_ge_20 IS NOT NULL)
        """,
    )
    zero(
        "live_prediction_snapshots_immutable_complete",
        """
        SELECT count(*)
        FROM live_prediction_runs AS run
                 LEFT JOIN (SELECT prediction_run_id,
                                   count(*) AS rows,
                 count(DISTINCT row_fingerprint) AS fingerprints
                            FROM live_player_predictions
                            GROUP BY prediction_run_id) AS prediction USING (prediction_run_id)
        WHERE NOT run.created_before_outcomes
           OR (run.status IN ('SUCCEEDED', 'PARTIAL')
            AND coalesce(prediction.rows, 0) <> coalesce(prediction.fingerprints, 0))
        """,
    )
    zero(
        "live_prediction_minutes_within_bounds",
        """
        SELECT count(*)
        FROM live_player_predictions
        WHERE baseline_expected_minutes < 0
           OR baseline_expected_minutes > 40
           OR adjusted_expected_minutes < 0
           OR adjusted_expected_minutes > 40
        """,
    )
    zero(
        "live_outcomes_do_not_mutate_or_precede_predictions",
        """
        SELECT count(*)
        FROM live_prediction_outcomes AS outcome
                 JOIN live_player_predictions AS prediction USING (live_prediction_id)
        WHERE outcome.attached_at <= prediction.prediction_generated_at
        """,
    )
    zero(
        "live_prediction_scoring_states_valid",
        """
        SELECT count(*)
        FROM live_prediction_runs
        WHERE scoring_rules_compatibility IS NOT NULL
          AND scoring_rules_compatibility NOT IN (
                                                  'SCORING_RULES_COMPATIBLE',
                                                  'SCORING_RULES_MISMATCH',
                                                  'SCORING_RULES_UNVERIFIED'
            )
        """,
    )
    zero(
        "live_prediction_production_readiness_requires_compatible_scoring",
        """
        SELECT count(*)
        FROM live_prediction_runs
        WHERE production_ready
          AND scoring_rules_compatibility <> 'SCORING_RULES_COMPATIBLE'
        """,
    )
    zero(
        "phase7_strategy_budget_legal",
        """
        SELECT count(*)
        FROM fantasy_strategy_runs
        WHERE status = 'SUCCEEDED'
          AND (total_credits IS NULL OR total_credits > budget + 0.00000001)
        """,
    )
    zero(
        "phase7_strategy_frozen_predictive_version",
        """
        SELECT count(*)
        FROM fantasy_strategy_runs
        WHERE status = 'SUCCEEDED'
          AND (predictive_model_version <> 'phase6c_predictive_uplift_frozen_v1'
            OR predictive_artifact_fingerprint IS NULL)
        """,
    )
    zero(
        "phase7_strategy_rules_manifest_matches",
        """
        SELECT count(*)
        FROM fantasy_strategy_runs AS run
                 LEFT JOIN fantasy_rules_manifests AS rules USING (ruleset_version)
        WHERE rules.ruleset_version IS NULL
           OR run.rules_fingerprint IS DISTINCT
        FROM rules.rules_fingerprint
        """,
    )
    zero(
        "phase7_oracle_is_evaluation_only",
        """
        SELECT count(*)
        FROM fantasy_strategy_replay_results
        WHERE strategy_name = 'ORACLE'
          AND NOT oracle_is_evaluation_only
        """,
    )
    zero(
        "phase7_strategy_solver_gap_nonnegative",
        """
        SELECT count(*)
        FROM fantasy_strategy_runs
        WHERE solver_gap < 0
        """,
    )
    zero(
        "phase8a_state_values_valid",
        """
        SELECT count(*)
        FROM fantasy_control_center_state_events
        WHERE bank_credits < 0
           OR transfers_available < 0
           OR state_fingerprint IS NULL
           OR length(state_fingerprint) <> 64
        """,
    )
    zero(
        "phase8a_state_constraints_valid",
        """
        SELECT count(*)
        FROM fantasy_control_center_state_events AS event, json_each(event.player_constraints_json) AS item
        WHERE trim (both '"' from cast (item.value AS VARCHAR))
            NOT IN ('NORMAL'
            , 'EXCLUDE'
            , 'FORCE_INCLUDE')
        """,
    )
    zero(
        "phase8a_run_rules_manifest_matches",
        """
        SELECT count(*)
        FROM fantasy_control_center_runs AS run
                 LEFT JOIN fantasy_rules_manifests AS rules USING (ruleset_version)
        WHERE rules.ruleset_version IS NULL
           OR run.rules_fingerprint IS DISTINCT
        FROM rules.rules_fingerprint
        """,
    )
    zero(
        "phase8a_success_has_snapshot_fingerprints",
        """
        SELECT count(*)
        FROM fantasy_control_center_runs
        WHERE status = 'SUCCEEDED'
          AND (
            market_snapshot_fingerprint IS NULL
                OR prediction_snapshot_fingerprint IS NULL
                OR manual_override_fingerprint IS NULL
                OR input_fingerprint IS NULL OR run_fingerprint IS NULL
            )
        """,
    )
    zero(
        "phase8a_run_transfer_limit_nonnegative",
        "SELECT count(*) FROM fantasy_control_center_runs WHERE transfer_limit < 0",
    )
    zero(
        "phase8b_prelock_cutoff_strict",
        """SELECT count(*) FROM fantasy_shadow_prelock_snapshots
           WHERE decision_created_at >= decision_cutoff_at""",
    )
    zero(
        "phase8b_shadow_fingerprints_valid",
        """SELECT count(*) FROM fantasy_shadow_prelock_snapshots
           WHERE length(snapshot_fingerprint) <> 64 OR length(input_fingerprint) <> 64
              OR length(market_snapshot_fingerprint) <> 64
              OR length(prediction_snapshot_fingerprint) <> 64""",
    )
    zero(
        "phase8b_turn_outcomes_after_lock",
        """SELECT count(*) FROM fantasy_shadow_turn_outcomes AS outcome
           JOIN fantasy_shadow_prelock_snapshots AS shadow USING(shadow_snapshot_id)
           WHERE outcome.attached_at < shadow.decision_cutoff_at""",
    )
    zero(
        "phase8b_advisor_outcome_scope_matches",
        """SELECT count(*) FROM fantasy_turn_advisor_runs AS advisor
           JOIN fantasy_shadow_turn_outcomes AS outcome USING(turn_outcome_id)
           WHERE advisor.shadow_snapshot_id IS DISTINCT FROM outcome.shadow_snapshot_id
              OR advisor.completed_turn IS DISTINCT FROM outcome.completed_turn""",
    )
    zero(
        "phase8b_price_horizon_is_next_matchday",
        """SELECT count(*) FROM fantasy_price_prediction_snapshots
           WHERE target_matchday <> fantasy_matchday + 1""",
    )
    zero(
        "phase8b_price_probabilities_valid",
        """SELECT count(*) FROM fantasy_price_prediction_snapshots
           WHERE probability_increase NOT BETWEEN 0 AND 1
              OR probability_decrease NOT BETWEEN 0 AND 1""",
    )
    zero(
        "phase8b_price_outcomes_are_later_market_snapshots",
        """SELECT count(*) FROM fantasy_price_prediction_outcomes AS outcome
           JOIN fantasy_price_prediction_snapshots AS prediction USING(price_prediction_id)
           JOIN fantasy_market_snapshots AS market
             ON market.snapshot_record_id=outcome.actual_market_snapshot_record_id
           WHERE market.matchday_number <> prediction.target_matchday
              OR market.observed_at <= prediction.predicted_at""",
    )
    zero(
        "phase8b_price_model_is_secondary_only",
        """SELECT count(*) FROM fantasy_price_model_manifests
           WHERE acceptance_status NOT IN (
             'ACCEPTED', 'ACCEPTED_SECONDARY_RMSE_ONLY', 'REJECTED'
           )""",
    )

    trace_tables = (
        "games",
        "player_game_stats",
        "team_game_stats",
        "play_by_play_events",
        "shots",
        "player_team_memberships",
        "fantasy_market_snapshots",
    )
    for table in trace_tables:
        zero(
            f"{table}_source_trace",
            f"""
            SELECT count(*) FROM {table} AS normalized
            LEFT JOIN raw_artifacts AS raw
              ON raw.artifact_id = normalized.source_artifact_id
            WHERE raw.artifact_id IS NULL
            """,
        )
    return checks
