CREATE TABLE live_prediction_runs (
    prediction_run_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    run_mode VARCHAR NOT NULL CHECK (
        run_mode IN ('LIVE', 'HISTORICAL_REHEARSAL')
    ),
    status VARCHAR NOT NULL CHECK (
        status IN (
            'RUNNING', 'SUCCEEDED', 'PARTIAL', 'NO_CURRENT_FANTASY_SLATE',
            'MATCHDAY_AMBIGUOUS', 'AVAILABILITY_UNRESOLVED',
            'MODEL_ARTIFACT_MISMATCH', 'FAILED'
        )
    ),
    prediction_generated_at TIMESTAMPTZ NOT NULL,
    feature_cutoff_time TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    season_matchday INTEGER,
    matchday_resolution_status VARCHAR NOT NULL CHECK (
        matchday_resolution_status IN (
            'MATCHDAY_RESOLVED', 'MATCHDAY_AMBIGUOUS', 'MARKET_NOT_AVAILABLE'
        )
    ),
    matchday_resolution_method VARCHAR,
    source_slate_run_id VARCHAR REFERENCES live_slate_runs(slate_run_id),
    source_live_run_id VARCHAR REFERENCES live_update_runs(live_run_id),
    refresh_requested BOOLEAN NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    run_fingerprint VARCHAR NOT NULL,
    performance_model_version VARCHAR,
    redistribution_model_version VARCHAR,
    performance_artifact_fingerprint VARCHAR,
    redistribution_artifact_fingerprint VARCHAR,
    feature_manifest_fingerprint VARCHAR NOT NULL,
    market_snapshot_batch_id VARCHAR,
    market_snapshot_fingerprint VARCHAR,
    availability_state_json JSON NOT NULL,
    scenario_definitions_json JSON NOT NULL,
    source_freshness_json JSON NOT NULL,
    coverage_json JSON NOT NULL,
    timing_json JSON NOT NULL,
    warnings_json JSON NOT NULL,
    error_code VARCHAR,
    error_message VARCHAR,
    output_json_path VARCHAR,
    output_tabular_path VARCHAR,
    created_before_outcomes BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (run_fingerprint)
);

CREATE TABLE live_prediction_scenarios (
    prediction_scenario_id VARCHAR PRIMARY KEY,
    prediction_run_id VARCHAR NOT NULL REFERENCES live_prediction_runs(prediction_run_id),
    scenario_id VARCHAR NOT NULL,
    scenario_status VARCHAR NOT NULL CHECK (
        scenario_status IN ('READY', 'GENERATED', 'UNRESOLVED', 'INCOMPLETE_ROLE', 'FAILED')
    ),
    decisions_json JSON NOT NULL,
    manual_role_estimates_json JSON NOT NULL,
    unresolved_players_json JSON NOT NULL,
    known_out_players_json JSON NOT NULL,
    team_summary_json JSON NOT NULL,
    scenario_fingerprint VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (prediction_run_id, scenario_id)
);

CREATE TABLE live_player_predictions (
    live_prediction_id VARCHAR PRIMARY KEY,
    prediction_run_id VARCHAR NOT NULL REFERENCES live_prediction_runs(prediction_run_id),
    prediction_scenario_id VARCHAR NOT NULL
        REFERENCES live_prediction_scenarios(prediction_scenario_id),
    scenario_id VARCHAR NOT NULL,
    prediction_generated_at TIMESTAMPTZ NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER,
    canonical_game_id VARCHAR NOT NULL REFERENCES games(canonical_game_id),
    game_date DATE,
    scheduled_tip_time TIMESTAMPTZ NOT NULL,
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    fantasy_entity_id VARCHAR REFERENCES fantasy_entities(fantasy_entity_id),
    fantasy_player_id VARCHAR,
    player_name VARCHAR NOT NULL,
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    opponent_team_id VARCHAR REFERENCES teams(canonical_team_id),
    home_away VARCHAR CHECK (home_away IN ('home', 'away')),
    fantasy_position VARCHAR,
    credits DOUBLE,
    source_availability_status VARCHAR,
    resolved_availability VARCHAR NOT NULL CHECK (
        resolved_availability IN ('PLAY', 'OUT', 'UNKNOWN', 'LIMITED')
    ),
    availability_decision_source VARCHAR NOT NULL,
    baseline_expected_minutes DOUBLE,
    adjusted_expected_minutes DOUBLE,
    minutes_delta_due_to_absences DOUBLE,
    direct_prediction DOUBLE,
    expected_fp_per_min DOUBLE,
    expected_fp_before_adjustment DOUBLE,
    expected_fp DOUBLE,
    fp_delta_due_to_absences DOUBLE,
    lower_prediction_interval DOUBLE,
    upper_prediction_interval DOUBLE,
    interval_calibration_status VARCHAR NOT NULL,
    career_el_games_before INTEGER,
    season_games_before INTEGER,
    cold_start_flag BOOLEAN NOT NULL,
    feature_completeness DOUBLE,
    freshness_status VARCHAR NOT NULL,
    prediction_status VARCHAR NOT NULL CHECK (
        prediction_status IN (
            'SCORED', 'OUT', 'UNSCORABLE', 'UNRESOLVED', 'MISSING_ROLE_UNKNOWN'
        )
    ),
    missing_role_status VARCHAR NOT NULL CHECK (
        missing_role_status IN ('ESTIMATED', 'MANUAL', 'UNKNOWN', 'NOT_APPLICABLE')
    ),
    manual_expected_minutes_if_available DOUBLE,
    performance_model_version VARCHAR,
    redistribution_model_version VARCHAR,
    feature_manifest_fingerprint VARCHAR NOT NULL,
    feature_snapshot_fingerprint VARCHAR NOT NULL,
    feature_snapshot_json JSON NOT NULL,
    provenance_json JSON NOT NULL,
    explanation_json JSON NOT NULL,
    row_fingerprint VARCHAR NOT NULL,
    UNIQUE (prediction_run_id, scenario_id, fantasy_entity_id)
);

CREATE TABLE live_prediction_outcomes (
    outcome_attachment_id VARCHAR PRIMARY KEY,
    live_prediction_id VARCHAR NOT NULL UNIQUE
        REFERENCES live_player_predictions(live_prediction_id),
    actual_fantasy_points DOUBLE NOT NULL,
    actual_minutes DOUBLE,
    outcome_source VARCHAR NOT NULL,
    source_player_game_id VARCHAR,
    attached_at TIMESTAMPTZ NOT NULL,
    outcome_fingerprint VARCHAR NOT NULL
);

CREATE INDEX idx_live_prediction_run_time
    ON live_prediction_runs(season_code, prediction_generated_at);
CREATE INDEX idx_live_prediction_player
    ON live_player_predictions(canonical_player_id, scheduled_tip_time);
CREATE INDEX idx_live_prediction_game
    ON live_player_predictions(canonical_game_id, scenario_id);
