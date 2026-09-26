# Canonical Database Quality Report

Generated: `2026-08-26T11:55:27.991788+00:00`

This is the selective Phase 2.5 canonical database: core E2018-E2025 and rich event data E2021-E2025. Raw artifacts remain the source of truth.

## Engine and integrity

- Engine: DuckDB `1.5.5`.
- Integrity suite: **PASS** (86 checks, 0 failures).
- Winner is derived only from validated final scores; the known-bad v2 `winner` field is not stored as truth.
- Shot records carry an explicit note that the source omits missed free throws.

## Table row counts

| Table | Rows |
| --- | ---: |
| `absence_scenario_player_outputs` | 0 |
| `absence_scenarios` | 0 |
| `availability_events` | 0 |
| `availability_override_events` | 0 |
| `availability_snapshots` | 0 |
| `competitions` | 1 |
| `current_upcoming_player_slate_v1` | 0 |
| `data_anomalies` | 18 |
| `fantasy_control_center_runs` | 0 |
| `fantasy_control_center_state_events` | 8 |
| `fantasy_entities` | 813 |
| `fantasy_identity_classifications` | 1,172 |
| `fantasy_market_snapshot_semantics` | 34,173 |
| `fantasy_market_snapshots` | 34,173 |
| `fantasy_matchday_round_mapping` | 165 |
| `fantasy_player_crosswalk` | 1,201 |
| `fantasy_price_model_manifests` | 1 |
| `fantasy_price_prediction_outcomes` | 0 |
| `fantasy_price_prediction_snapshots` | 0 |
| `fantasy_rules_manifests` | 1 |
| `fantasy_shadow_matchday_evaluations` | 0 |
| `fantasy_shadow_prelock_snapshots` | 0 |
| `fantasy_shadow_turn_outcomes` | 0 |
| `fantasy_strategy_replay_results` | 228 |
| `fantasy_strategy_runs` | 2 |
| `fantasy_turn_advisor_runs` | 0 |
| `games` | 2,658 |
| `ingestion_checkpoints` | 6,255 |
| `ingestion_failures` | 4 |
| `ingestion_runs` | 135 |
| `live_player_predictions` | 0 |
| `live_prediction_outcomes` | 0 |
| `live_prediction_runs` | 3 |
| `live_prediction_scenarios` | 0 |
| `live_roster_snapshots` | 0 |
| `live_schedule_snapshots` | 3,040 |
| `live_slate_runs` | 10 |
| `live_source_refresh_events` | 35 |
| `live_update_runs` | 7 |
| `ml_baseline_predictions_v1` | 163,926 |
| `ml_e2025_fantasy_evaluation_v1` | 7,921 |
| `ml_phase3b_primary_evaluation_v1` | 29,232 |
| `ml_phase4a_experiments_v1` | 21 |
| `ml_phase4a_feature_importance_v1` | 20,230 |
| `ml_phase4a_inner_trials_v1` | 36 |
| `ml_phase4a_outer_predictions_v1` | 470,379 |
| `ml_phase4a_preprocessing_audit_v1` | 63 |
| `ml_phase4b_calibration_predictions_v1` | 179,192 |
| `ml_phase4b_experiments_v1` | 60 |
| `ml_phase4b_feature_importance_v1` | 4,718 |
| `ml_phase4b_inner_trials_v1` | 84 |
| `ml_phase4b_leakage_audit_v1` | 57 |
| `ml_phase4b_minutes_predictions_v1` | 111,995 |
| `ml_phase4b_outer_predictions_v1` | 156,793 |
| `ml_phase4b_prediction_intervals_v1` | 22,399 |
| `ml_phase4b_production_predictions_v1` | 179,192 |
| `ml_phase5b_absence_recipient_rows_v1` | 36,631 |
| `ml_phase5b_candidate_features_v1` | 60,077 |
| `ml_phase5b_fantasy_impact_v1` | 15,019 |
| `ml_phase5b_inner_trials_v1` | 30 |
| `ml_phase5b_normalized_team_rotations_v1` | 60,077 |
| `ml_phase5b_redistribution_predictions_v1` | 137,988 |
| `ml_player_game_core_features_v1` | 54,642 |
| `ml_player_game_core_features_with_fantasy_v1` | 54,642 |
| `ml_player_game_core_plus_rich_v1` | 54,642 |
| `ml_player_game_lineup_features_v1` | 54,642 |
| `ml_player_game_onoff_features_v1` | 54,642 |
| `ml_player_game_opponent_shot_features_v1` | 54,642 |
| `ml_player_game_rich_features_v1` | 54,642 |
| `ml_player_game_rotation_features_v1` | 54,642 |
| `ml_player_game_shot_features_v1` | 54,642 |
| `ml_player_game_targets_v1` | 60,101 |
| `ml_walk_forward_splits_v1` | 2,528 |
| `official_availability_reports` | 8 |
| `pbp_lineup_possession_components_v1` | 106,090 |
| `pbp_lineup_possession_estimates_v1` | 106,090 |
| `pbp_lineup_stints_v1` | 106,090 |
| `pbp_player_stints_v1` | 106,597 |
| `pbp_possessions_v1` | 3,160 |
| `pbp_rotation_game_audit_v1` | 1,690 |
| `pbp_rotation_player_minute_audit_v1` | 35,452 |
| `play_by_play_events` | 895,360 |
| `player_aliases` | 8,876 |
| `player_game_lineup_observations_v1` | 34,102 |
| `player_game_onoff_observations_v1` | 34,102 |
| `player_game_rotation_observations_v1` | 34,102 |
| `player_game_shot_profile_observations_v1` | 34,214 |
| `player_game_stats` | 60,101 |
| `player_team_memberships` | 2,352 |
| `players` | 1,035 |
| `prospective_prediction_runs` | 0 |
| `raw_artifact_request_provenance` | 554 |
| `raw_artifacts` | 6,900 |
| `role_limit_override_events` | 0 |
| `schema_migrations` | 12 |
| `seasons` | 9 |
| `shot_spatial_attempts_v1` | 258,453 |
| `shots` | 258,453 |
| `team_aliases` | 537 |
| `team_game_shot_profile_observations_v1` | 3,380 |
| `team_game_stats` | 5,058 |
| `teams` | 29 |

## Retained season coverage

| Season | Games | Games with player stats |
| --- | ---: | ---: |
| E2018 | 260 | 259 |
| E2019 | 252 | 252 |
| E2020 | 328 | 328 |
| E2021 | 327 | 299 |
| E2022 | 328 | 328 |
| E2023 | 331 | 331 |
| E2024 | 330 | 330 |
| E2025 | 402 | 402 |
| E2026 | 100 | 0 |

Detailed coverage and requested-versus-policy distinctions are reported in `historical_coverage.md`. E2018-E2020 PBP/shots are absent by policy, not endpoint failure; E2007-E2017 bulk data are not retained.

## Missingness and reconciliation

| Check | Rows |
| --- | ---: |
| games missing UTC tip time | 1 |
| player-games missing canonical player | 0 |
| player-games with DNP/blank minutes | 5,440 |
| play-by-play events missing team (often period/timeout events) | 18,758 |
| play-by-play events missing player (often system/team events) | 48,655 |
| shots missing canonical player | 0 |
| Fantasy snapshots missing canonical team | 2,999 |
| unresolved active Fantasy player mappings | 52 |

All games with team box scores have exactly two team rows, and team points reconcile to player points under the integrity suite.

## Fantasy identity and history

- Confident player mappings: **1120/1172 (95.6%)**.
- Unresolved Fantasy team snapshot rows: 2999.
- The Baskonia provider ID/name/code discrepancy is resolved through reviewed rows in `team_aliases`, not a scattered string replacement.
- Coaches remain separate `COACH` fantasy entities and are never joined to official players.

| Season | Matchday | Rows | First observed | Last observed | Price semantics | Any status valid as-of matchday? |
| --- | ---: | ---: | --- | --- | --- | --- |
| E2022 | 1 | 182 | 2026-08-12 12:32:39.893887+03 | 2026-08-12 12:32:39.893887+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 2 | 188 | 2026-08-12 12:32:41.01302+03 | 2026-08-12 12:32:41.01302+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 3 | 192 | 2026-08-12 12:32:41.913745+03 | 2026-08-12 12:32:41.913745+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 4 | 193 | 2026-08-12 12:32:43.729266+03 | 2026-08-12 12:32:43.729266+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 5 | 197 | 2026-08-12 12:32:44.865304+03 | 2026-08-12 12:32:44.865304+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 6 | 193 | 2026-08-12 12:32:45.947017+03 | 2026-08-12 12:32:45.947017+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 7 | 185 | 2026-08-12 12:32:46.952948+03 | 2026-08-12 12:32:46.952948+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 8 | 195 | 2026-08-12 12:32:48.320409+03 | 2026-08-12 12:32:48.320409+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 9 | 188 | 2026-08-12 12:32:49.616791+03 | 2026-08-12 12:32:49.616791+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 10 | 192 | 2026-08-12 12:32:50.576119+03 | 2026-08-12 12:32:50.576119+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 11 | 190 | 2026-08-12 12:32:51.806141+03 | 2026-08-12 12:32:51.806141+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 12 | 194 | 2026-08-12 12:32:52.823804+03 | 2026-08-12 12:32:52.823804+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 13 | 189 | 2026-08-12 12:32:53.872064+03 | 2026-08-12 12:32:53.872064+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 14 | 189 | 2026-08-12 12:32:55.185822+03 | 2026-08-12 12:32:55.185822+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 15 | 189 | 2026-08-12 12:32:56.330669+03 | 2026-08-12 12:32:56.330669+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 16 | 184 | 2026-08-12 12:32:57.370836+03 | 2026-08-12 12:32:57.370836+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 17 | 191 | 2026-08-12 12:32:58.388729+03 | 2026-08-12 12:32:58.388729+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 18 | 188 | 2026-08-12 12:32:59.504368+03 | 2026-08-12 12:32:59.504368+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 19 | 187 | 2026-08-12 12:33:00.632196+03 | 2026-08-12 12:33:00.632196+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 20 | 197 | 2026-08-12 12:33:02.094837+03 | 2026-08-12 12:33:02.094837+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 21 | 194 | 2026-08-12 12:33:03.500384+03 | 2026-08-12 12:33:03.500384+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 22 | 195 | 2026-08-12 12:33:04.519078+03 | 2026-08-12 12:33:04.519078+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 23 | 188 | 2026-08-12 12:33:05.550496+03 | 2026-08-12 12:33:05.550496+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 24 | 195 | 2026-08-12 12:33:07.276667+03 | 2026-08-12 12:33:07.966994+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 25 | 195 | 2026-08-12 12:33:08.918763+03 | 2026-08-12 12:33:08.918763+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 26 | 202 | 2026-08-12 12:33:10.038509+03 | 2026-08-12 12:33:10.038509+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 27 | 193 | 2026-08-12 12:33:11.193071+03 | 2026-08-12 12:33:11.193071+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 28 | 199 | 2026-08-12 12:33:12.070964+03 | 2026-08-12 12:33:12.070964+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 29 | 186 | 2026-08-12 12:33:13.431713+03 | 2026-08-12 12:33:13.431713+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 30 | 193 | 2026-08-12 12:33:14.44595+03 | 2026-08-12 12:33:14.44595+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 31 | 198 | 2026-08-12 12:33:15.474725+03 | 2026-08-12 12:33:15.474725+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 32 | 194 | 2026-08-12 12:33:16.40135+03 | 2026-08-12 12:33:16.40135+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 33 | 190 | 2026-08-12 12:33:17.412737+03 | 2026-08-12 12:33:17.412737+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 34 | 191 | 2026-08-12 12:33:18.646739+03 | 2026-08-12 12:33:18.646739+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 35 | 88 | 2026-08-12 12:33:19.639819+03 | 2026-08-12 12:33:19.639819+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 36 | 88 | 2026-08-12 12:33:20.819296+03 | 2026-08-12 12:33:20.819296+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 37 | 83 | 2026-08-12 12:33:21.947185+03 | 2026-08-12 12:33:21.947185+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 38 | 63 | 2026-08-12 12:33:22.761009+03 | 2026-08-12 12:33:22.761009+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 39 | 61 | 2026-08-12 12:33:23.595263+03 | 2026-08-12 12:33:23.595263+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 40 | 43 | 2026-08-12 12:33:25.014037+03 | 2026-08-12 12:33:25.014037+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2022 | 41 | 45 | 2026-08-12 12:33:25.880725+03 | 2026-08-12 12:33:25.880725+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 1 | 193 | 2026-08-12 12:33:27.552261+03 | 2026-08-12 12:33:27.552261+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 2 | 191 | 2026-08-12 12:33:28.506429+03 | 2026-08-12 12:33:28.839439+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 3 | 194 | 2026-08-12 12:33:29.934788+03 | 2026-08-12 12:33:29.934788+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 4 | 191 | 2026-08-12 12:33:31.242483+03 | 2026-08-12 12:33:31.242483+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 5 | 193 | 2026-08-12 12:33:32.469936+03 | 2026-08-12 12:33:32.469936+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 6 | 188 | 2026-08-12 12:33:33.492767+03 | 2026-08-12 12:33:33.890356+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 7 | 189 | 2026-08-12 12:33:34.897849+03 | 2026-08-12 12:33:34.897849+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 8 | 194 | 2026-08-12 12:33:36.005555+03 | 2026-08-12 12:33:36.005555+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 9 | 187 | 2026-08-12 12:33:37.178984+03 | 2026-08-12 12:33:37.178984+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 10 | 188 | 2026-08-12 12:33:38.379789+03 | 2026-08-12 12:33:38.379789+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 11 | 192 | 2026-08-12 12:33:39.277013+03 | 2026-08-12 12:33:39.277013+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 12 | 186 | 2026-08-12 12:33:40.277356+03 | 2026-08-12 12:33:40.277356+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 13 | 185 | 2026-08-12 12:33:41.371812+03 | 2026-08-12 12:33:41.371812+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 14 | 186 | 2026-08-12 12:33:42.840919+03 | 2026-08-12 12:33:42.840919+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 15 | 191 | 2026-08-12 12:33:43.728087+03 | 2026-08-12 12:33:43.728087+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 16 | 192 | 2026-08-12 12:33:44.633215+03 | 2026-08-12 12:33:44.633215+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 17 | 191 | 2026-08-12 12:33:45.542846+03 | 2026-08-12 12:33:45.542846+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 18 | 187 | 2026-08-12 12:33:46.552388+03 | 2026-08-12 12:33:46.552388+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 19 | 190 | 2026-08-12 12:33:47.531363+03 | 2026-08-12 12:33:47.531363+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 20 | 190 | 2026-08-12 12:33:48.934064+03 | 2026-08-12 12:33:48.934064+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 21 | 197 | 2026-08-12 12:33:49.892149+03 | 2026-08-12 12:33:49.892149+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 22 | 194 | 2026-08-12 12:33:50.811539+03 | 2026-08-12 12:33:50.811539+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 23 | 186 | 2026-08-12 12:33:51.738235+03 | 2026-08-12 12:33:51.738235+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 24 | 198 | 2026-08-12 12:33:52.691447+03 | 2026-08-12 12:33:52.691447+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 25 | 197 | 2026-08-12 12:33:54.401224+03 | 2026-08-12 12:33:54.401224+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 26 | 196 | 2026-08-12 12:33:55.342815+03 | 2026-08-12 12:33:55.342815+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 27 | 195 | 2026-08-12 12:33:56.3367+03 | 2026-08-12 12:33:56.3367+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 28 | 196 | 2026-08-12 12:33:57.251716+03 | 2026-08-12 12:33:57.251716+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 29 | 194 | 2026-08-12 12:33:58.280738+03 | 2026-08-12 12:33:58.280738+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 30 | 200 | 2026-08-12 12:33:59.477406+03 | 2026-08-12 12:33:59.477406+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 31 | 190 | 2026-08-12 12:34:01.079389+03 | 2026-08-12 12:34:01.079389+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 32 | 199 | 2026-08-12 12:34:02.176357+03 | 2026-08-12 12:34:02.176357+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 33 | 194 | 2026-08-12 12:34:03.299457+03 | 2026-08-12 12:34:03.299457+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 34 | 207 | 2026-08-12 12:34:04.319828+03 | 2026-08-12 12:34:04.319828+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 35 | 44 | 2026-08-12 12:34:06.441106+03 | 2026-08-12 12:34:06.441106+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 36 | 22 | 2026-08-12 12:34:07.997979+03 | 2026-08-12 12:34:07.997979+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 37 | 88 | 2026-08-12 12:34:09.010291+03 | 2026-08-12 12:34:09.010291+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 38 | 84 | 2026-08-12 12:34:09.880593+03 | 2026-08-12 12:34:09.880593+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 39 | 83 | 2026-08-12 12:34:10.964762+03 | 2026-08-12 12:34:10.964762+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 40 | 62 | 2026-08-12 12:34:12.210575+03 | 2026-08-12 12:34:12.210575+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 41 | 60 | 2026-08-12 12:34:13.086516+03 | 2026-08-12 12:34:13.086516+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 42 | 43 | 2026-08-12 12:34:13.971839+03 | 2026-08-12 12:34:13.971839+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2023 | 43 | 46 | 2026-08-12 12:34:14.716811+03 | 2026-08-12 12:34:14.716811+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 1 | 187 | 2026-08-12 12:34:16.214774+03 | 2026-08-12 12:34:16.214774+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 2 | 194 | 2026-08-12 12:34:17.934989+03 | 2026-08-12 12:34:17.934989+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 3 | 191 | 2026-08-12 12:34:18.859985+03 | 2026-08-12 12:34:18.859985+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 4 | 186 | 2026-08-12 12:34:19.772751+03 | 2026-08-12 12:34:19.772751+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 5 | 177 | 2026-08-12 12:34:20.697886+03 | 2026-08-12 12:34:20.697886+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 6 | 182 | 2026-08-12 12:34:21.72949+03 | 2026-08-12 12:34:21.72949+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 7 | 186 | 2026-08-12 12:34:22.919627+03 | 2026-08-12 12:34:22.919627+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 8 | 189 | 2026-08-12 12:34:23.972385+03 | 2026-08-12 12:34:23.972385+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 9 | 185 | 2026-08-12 12:34:25.037404+03 | 2026-08-12 12:34:25.037404+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 10 | 190 | 2026-08-12 12:34:26.040332+03 | 2026-08-12 12:34:26.040332+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 11 | 186 | 2026-08-12 12:34:26.934686+03 | 2026-08-12 12:34:26.934686+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 12 | 189 | 2026-08-12 12:34:27.890838+03 | 2026-08-12 12:34:27.890838+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 13 | 182 | 2026-08-12 12:34:29.307264+03 | 2026-08-12 12:34:29.307264+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 14 | 188 | 2026-08-12 12:34:30.334309+03 | 2026-08-12 12:34:30.334309+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 15 | 185 | 2026-08-12 12:34:31.453681+03 | 2026-08-12 12:34:31.453681+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 16 | 191 | 2026-08-12 12:34:32.360014+03 | 2026-08-12 12:34:32.360014+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 17 | 187 | 2026-08-12 12:34:33.398763+03 | 2026-08-12 12:34:33.712569+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 18 | 184 | 2026-08-12 12:34:34.836685+03 | 2026-08-12 12:34:34.836685+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 19 | 188 | 2026-08-12 12:34:35.85625+03 | 2026-08-12 12:34:35.85625+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 20 | 189 | 2026-08-12 12:34:36.797927+03 | 2026-08-12 12:34:36.797927+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 21 | 192 | 2026-08-12 12:34:37.903446+03 | 2026-08-12 12:34:37.903446+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 22 | 189 | 2026-08-12 12:34:38.931519+03 | 2026-08-12 12:34:38.931519+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 23 | 188 | 2026-08-12 12:34:39.891703+03 | 2026-08-12 12:34:39.891703+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 24 | 187 | 2026-08-12 12:34:41.179692+03 | 2026-08-12 12:34:41.179692+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 25 | 192 | 2026-08-12 12:34:42.103515+03 | 2026-08-12 12:34:42.103515+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 26 | 189 | 2026-08-12 12:34:43.125561+03 | 2026-08-12 12:34:43.125561+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 27 | 193 | 2026-08-12 12:34:44.038197+03 | 2026-08-12 12:34:44.038197+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 28 | 197 | 2026-08-12 12:34:44.980854+03 | 2026-08-12 12:34:44.980854+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 29 | 189 | 2026-08-12 12:34:46.366614+03 | 2026-08-12 12:34:46.366614+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 30 | 199 | 2026-08-12 12:34:47.399361+03 | 2026-08-12 12:34:47.399361+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 31 | 192 | 2026-08-12 12:34:48.261803+03 | 2026-08-12 12:34:48.261803+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 32 | 191 | 2026-08-12 12:34:49.146122+03 | 2026-08-12 12:34:49.146122+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 33 | 188 | 2026-08-12 12:34:50.096603+03 | 2026-08-12 12:34:50.096603+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 34 | 191 | 2026-08-12 12:34:51.012188+03 | 2026-08-12 12:34:51.012188+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 35 | 42 | 2026-08-12 12:34:51.995272+03 | 2026-08-12 12:34:51.995272+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 36 | 22 | 2026-08-12 12:34:53.07577+03 | 2026-08-12 12:34:53.07577+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 37 | 87 | 2026-08-12 12:34:53.965107+03 | 2026-08-12 12:34:53.965107+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 38 | 90 | 2026-08-12 12:34:54.811869+03 | 2026-08-12 12:34:54.811869+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 39 | 82 | 2026-08-12 12:34:55.921558+03 | 2026-08-12 12:34:55.921558+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 40 | 63 | 2026-08-12 12:34:56.732606+03 | 2026-08-12 12:34:56.732606+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 41 | 41 | 2026-08-12 12:34:57.480606+03 | 2026-08-12 12:34:57.480606+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 42 | 43 | 2026-08-12 12:34:58.678838+03 | 2026-08-12 12:34:58.678838+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2024 | 43 | 39 | 2026-08-12 12:34:59.541696+03 | 2026-08-12 12:34:59.541696+03 | SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME | False |
| E2025 | 1 | 307 | 2026-08-11 14:34:15.37633+03 | 2026-08-11 14:34:15.37633+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 2 | 307 | 2026-08-12 04:30:18.091581+03 | 2026-08-12 04:30:18.091581+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 3 | 328 | 2026-08-12 04:30:24.368582+03 | 2026-08-12 04:30:24.368582+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 4 | 330 | 2026-08-12 04:30:33.035455+03 | 2026-08-12 04:30:33.035455+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 5 | 330 | 2026-08-12 04:30:39.606596+03 | 2026-08-12 04:30:39.606596+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 6 | 331 | 2026-08-12 04:30:46.024053+03 | 2026-08-12 04:30:46.024053+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 7 | 331 | 2026-08-12 04:30:54.293862+03 | 2026-08-12 04:30:54.293862+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 8 | 332 | 2026-08-12 04:31:00.641895+03 | 2026-08-12 04:31:00.641895+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 9 | 334 | 2026-08-12 04:31:07.357797+03 | 2026-08-12 04:31:07.357797+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 10 | 336 | 2026-08-12 04:31:14.813712+03 | 2026-08-12 04:31:14.813712+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 11 | 336 | 2026-08-12 04:31:22.053325+03 | 2026-08-12 04:31:22.053325+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 12 | 337 | 2026-08-12 04:31:28.725252+03 | 2026-08-12 04:31:28.725252+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 13 | 339 | 2026-08-12 04:31:35.750501+03 | 2026-08-12 04:31:35.750501+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 14 | 340 | 2026-08-12 04:31:42.944538+03 | 2026-08-12 04:31:42.944538+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 15 | 342 | 2026-08-12 04:31:50.293565+03 | 2026-08-12 04:31:50.293565+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 16 | 346 | 2026-08-12 04:31:57.026937+03 | 2026-08-12 04:31:57.026937+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 17 | 346 | 2026-08-12 04:32:04.138207+03 | 2026-08-12 04:32:04.138207+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 18 | 348 | 2026-08-12 04:32:11.038619+03 | 2026-08-12 04:32:11.038619+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 19 | 348 | 2026-08-11 14:34:29.973144+03 | 2026-08-11 14:34:29.973144+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 20 | 352 | 2026-08-12 04:32:20.203119+03 | 2026-08-12 04:32:20.203119+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 21 | 353 | 2026-08-12 04:32:27.32705+03 | 2026-08-12 04:32:27.32705+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 22 | 353 | 2026-08-12 04:32:34.181647+03 | 2026-08-12 04:32:34.181647+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 23 | 354 | 2026-08-12 04:32:42.634798+03 | 2026-08-12 04:32:42.634798+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 24 | 354 | 2026-08-12 04:32:49.959948+03 | 2026-08-12 04:32:49.959948+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 25 | 355 | 2026-08-12 04:32:57.502049+03 | 2026-08-12 04:32:57.502049+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 26 | 359 | 2026-08-12 04:33:04.585312+03 | 2026-08-12 04:33:04.585312+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 27 | 359 | 2026-08-12 04:33:12.710356+03 | 2026-08-12 04:33:12.710356+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 28 | 359 | 2026-08-12 04:33:19.98615+03 | 2026-08-12 04:33:19.98615+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 29 | 360 | 2026-08-12 04:33:27.376963+03 | 2026-08-12 04:33:27.376963+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 30 | 365 | 2026-08-12 04:33:37.097096+03 | 2026-08-12 04:33:37.097096+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 31 | 365 | 2026-08-12 04:33:45.154448+03 | 2026-08-12 04:33:45.154448+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 32 | 365 | 2026-08-12 04:33:53.09223+03 | 2026-08-12 04:33:53.09223+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 33 | 365 | 2026-08-12 04:34:00.53636+03 | 2026-08-12 04:34:00.53636+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 34 | 365 | 2026-08-12 04:34:08.71877+03 | 2026-08-12 04:34:08.71877+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 35 | 365 | 2026-08-12 04:34:16.165727+03 | 2026-08-12 04:34:16.165727+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 36 | 365 | 2026-08-12 04:34:24.235615+03 | 2026-08-12 04:34:24.235615+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 37 | 365 | 2026-08-11 14:34:49.083649+03 | 2026-08-11 14:34:49.083649+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |
| E2025 | 38 | 365 | 2026-08-11 00:49:26.880487+03 | 2026-08-11 00:49:26.880487+03 | PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS | False |

Historical current-season prices are retained as matchday-addressed prices. Position semantics are explicitly unproven historically. Injury, probability, bench, on-fire, and average fields from old-matchday responses are marked current overlays and return `NULL` through `leakage_safe_fantasy_market`.

No rows were fabricated in `availability_events`; it remains ready for sources with real publication/observation timestamps.

### Unresolved Fantasy players

No weak fuzzy match is accepted merely to increase coverage.

| Fantasy ID | Player | Team context | Method | Review note |
| --- | --- | --- | --- | --- |
| 8692 | Alex Blanco | Valencia Basket | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 1444 | Antoine Diot | LDLC ASVEL Villeurbanne | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1416 | Branko Lazic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1416 | Branko Lazic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1416 | Branko Lazic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 9006 | Caspar Vossenberg | FC Bayern Munich | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 1447 | Charles Kahudi | LDLC ASVEL Villeurbanne | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1447 | Charles Kahudi | LDLC ASVEL Villeurbanne | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1447 | Charles Kahudi | LDLC ASVEL Villeurbanne | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 8679 | Chiek Diallo | Kosner Baskonia Vitoria-Gasteiz | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8685 | Daniel Gonzalez | FC Barcelona | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8683 | Diego Ferreras | FC Barcelona | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 1454 | Donatas Motiejunas | AS Monaco | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1454 | Donatas Motiejunas | AS Monaco | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1454 | Donatas Motiejunas | AS Monaco | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 8688 | Egor Amosov | Real Madrid | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 7241 | Gur Lavy | Maccabi Rapyd Tel Aviv | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8695 | Ignas Stombergas | Zalgiris Kaunas | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 7200 | Jeff Dowtin Jr. | Maccabi Rapyd Tel Aviv | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8684 | Joaquim Boumtje-Boumtje | FC Barcelona | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8693 | Jorge Carot | Valencia Basket | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 1392 | Luigi Datome | EA7 Emporio Armani Milan | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1291 | Marco Belinelli | Virtus Bologna | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1291 | Marco Belinelli | Virtus Bologna | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1291 | Marco Belinelli | Virtus Bologna | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1222 | Milos Teodosic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1222 | Milos Teodosic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1222 | Milos Teodosic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1250 | Miroslav Raduljica | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 8686 | Mohamed Dabone | FC Barcelona | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 7213 | Nate Mason | Dubai Basketball | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 9007 | Nicolas Kodjoe | FC Bayern Munich | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8925 | Nikolas Sermpezis | FC Bayern Munich | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8805 | Ognjen Simjanovski | Crvena Zvezda Meridianbet Belgrade | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8677 | Pedro Souza | Kosner Baskonia Vitoria-Gasteiz | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 4758 | Perry Dozier | Anadolu Efes Istanbul | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 2237 | Ricky Rubio | FC Barcelona | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1319 | Rudy Fernandez | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1319 | Rudy Fernandez | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1302 | Sam Van Rossom | Valencia Basket | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 8806 | Sava Djuric | Crvena Zvezda Meridianbet Belgrade | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 1314 | Sergio Llull | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1314 | Sergio Llull | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1314 | Sergio Llull | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1315 | Sergio Rodriguez | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1315 | Sergio Rodriguez | Real Madrid | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1411 | Stefan Markovic | Crvena Zvezda Meridianbet Belgrade | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1384 | Tibor Pleiss | Panathinaikos AKTOR Athens | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1384 | Tibor Pleiss | Anadolu Efes Istanbul | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 1384 | Tibor Pleiss | Anadolu Efes Istanbul | `no_conservative_match` | Season-aware deterministic historical Stats resolution. |
| 8690 | Tomas Talcis | Valencia Basket | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |
| 8769 | Uros Danilovic | Partizan Mozzart Bet Belgrade | `no_exact_match` | No explicit ID or exact conservative name+team+season match was found. |

## Play-by-play and schema observations

- Raw event-number inversions retained: 10344. `source_sequence` and `source_period` are the canonical ordering evidence.
- Retained player-box and roster row schemas are stable across E2018-E2025; PBP and shot row schemas are stable across E2021-E2025.
- Live numeric player IDs are deliberately canonicalized from forms such as `P007200` to roster code `007200`; raw codes remain on fact rows.

## Quarantine and anomalies

| Entity | Code | Severity | Quarantined | Details |
| --- | --- | --- | --- | --- |
| GAME:aa3e514d-7f65-5ebb-8c25-ddb0e02301d1 | `BETTER_SOURCE_METADATA_REQUIRES_REBUILD` | WARNING | False | `{"existing_utc_tip_time": null, "new_source_has_utc_tip_time": true, "new_source_artifact_id": "ed3e16eb-f2dd-55c2-90d8-a9776cb7fe25"}` |
| GAME:ff733ed5-25f4-552a-a3a1-438d656dd3cc | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2021", "game_code": 226, "event_count": 1, "values": ["00:-1"], "source_sequences": [361], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:de6cf445-22ea-538d-ab9b-723c11186460 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2021", "game_code": 238, "event_count": 1, "values": ["00:-1"], "source_sequences": [117], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:a045e9dd-dcad-569e-a56c-d9e8387ddbb7 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2021", "game_code": 306, "event_count": 1, "values": ["00:-1"], "source_sequences": [114], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:8aa58be0-5af3-527d-863f-e3d314c4d83c | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2021", "game_code": 52, "event_count": 1, "values": ["00:-1"], "source_sequences": [92], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:6dc2bb59-ff1b-5cba-8368-e1be54aa3c20 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2022", "game_code": 138, "event_count": 1, "values": ["00:-1"], "source_sequences": [245], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:595fb012-2443-5ca9-86a1-4a12f3c3b699 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2022", "game_code": 169, "event_count": 1, "values": ["00:-1"], "source_sequences": [114], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:8768e54f-9e32-5812-a4de-385a02649482 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2022", "game_code": 208, "event_count": 1, "values": ["00:-1"], "source_sequences": [255], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:0605ed18-9195-58af-8391-01f3f67cb888 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2022", "game_code": 214, "event_count": 1, "values": ["-1:00"], "source_sequences": [395], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:16d1d82c-9a85-5291-958f-fe516d3d9ebd | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2022", "game_code": 47, "event_count": 1, "values": ["00:-1"], "source_sequences": [560], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:0b5da6c9-1091-594f-a500-862f460d1317 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2023", "game_code": 186, "event_count": 1, "values": ["00:-1"], "source_sequences": [238], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:31844f84-dcda-5ff4-aec3-2773d1f0c94c | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2023", "game_code": 63, "event_count": 1, "values": ["00:-1"], "source_sequences": [113], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:67b98d6b-f4d0-5559-b642-5811d1794e7b | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2023", "game_code": 78, "event_count": 1, "values": ["00:-1"], "source_sequences": [236], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:f5568ac3-ac46-5c39-87fb-fe7585e64160 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2024", "game_code": 178, "event_count": 1, "values": ["00:-1"], "source_sequences": [411], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:a6ab8c1e-6d15-5b8e-babc-72c041bb88d5 | `INVALID_PBP_CLOCK` | WARNING | True | `{"season_code": "E2024", "game_code": 99, "event_count": 1, "values": ["00:-1"], "source_sequences": [362], "policy": "preserved raw; quarantine for clock/rotation features"}` |
| GAME:aa3e514d-7f65-5ebb-8c25-ddb0e02301d1 | `ROTATION_PLAYER_MINUTE_DISCREPANCY` | WARNING | True | `{"fixture": "E2023/170", "verdict": "POSSIBLE WITH CAVEATS", "player_rows_exact": 20, "player_rows_total": 24, "maximum_absolute_error_seconds": 60, "team_totals_reconcile": true}` |
| GAME:8e379b74-0475-5af4-90ed-f25ab4eaff7a | `EMPTY_SUPPORTED_BOXSCORE` | ERROR | True | `{"season_code": "E2018", "game_code": 21, "raw_path": "/Users/heliasantoniou/PycharmProjects/ELfantasy/data/raw/euroleague/E2018/boxscores/21.json", "policy": "preserved raw; excluded from player/team statistics"}` |
| GAME:aa3e514d-7f65-5ebb-8c25-ddb0e02301d1 | `MISSING_TRUSTWORTHY_UTC_TIP_TIME` | ERROR | True | `{"fixture": "E2023/170", "local_header_time": "2024-01-05 20:45", "timezone_offset_in_source": null, "affected_use": "strict chronological/leakage-safe calculations"}` |

Raw records are never deleted when an anomaly is quarantined. E2018/21 lacks a usable official box score. Fourteen games carry invalid provider clock sentinels and are excluded from strict clock-dependent work. E2023/170 is additionally excluded from rotation-derived calculations until its four ±60-second player discrepancies are resolved and from strict chronology until a trustworthy UTC tip time is sourced. Their other valid facts remain queryable.

## Ingestion run audit

| Source | Status | Runs | Inserted records | Errors |
| --- | --- | ---: | ---: | ---: |
| archived_official_season | FAILED | 6 | 0 | 6 |
| archived_official_season | SUCCEEDED | 30 | 1,059,850 | 0 |
| bulk_download_core | FAILED | 2 | 0 | 2 |
| bulk_download_core | SUCCEEDED | 14 | 0 | 0 |
| bulk_download_events | SUCCEEDED | 10 | 0 | 0 |
| fantasy_identity_resolution | SUCCEEDED | 4 | 58 | 0 |
| fantasy_market_snapshot | SUCCEEDED | 38 | 14,000 | 0 |
| historical_fantasy_identity_resolution | FAILED | 1 | 0 | 1 |
| historical_fantasy_identity_resolution | SUCCEEDED | 6 | 1,623 | 0 |
| historical_fantasy_stats | FAILED | 1 | 0 | 1 |
| historical_fantasy_stats | SUCCEEDED | 12 | 43,785 | 0 |
| live_current_season_update | SUCCEEDED | 8 | 183 | 0 |
| retained_phase1_samples | SUCCEEDED | 3 | 5,025 | 0 |

Failed runs remain visible in metadata; they never silently become successful. Cached reruns compare per-game normalized counts, skip valid immutable artifacts, and converge to zero new records.

## Three largest remaining risks

1. Previous-season Fantasy prices are still unavailable, so multi-season price backtests remain incomplete.
2. Twenty-one current Fantasy players still lack a high-confidence official identity mapping; three are ambiguous, two conflict with team/jersey context, and sixteen have no official candidate.
3. E2018/21 has no usable official box, while provider clock sentinels and the E2023/170 minute conflict require quarantine-aware feature generation.
