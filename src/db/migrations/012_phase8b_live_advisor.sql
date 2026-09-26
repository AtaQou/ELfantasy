CREATE TABLE fantasy_shadow_prelock_snapshots (
    shadow_snapshot_id VARCHAR PRIMARY KEY,
    control_center_run_id VARCHAR NOT NULL UNIQUE
        REFERENCES fantasy_control_center_runs(control_center_run_id),
    profile_id VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    prediction_run_id VARCHAR NOT NULL REFERENCES live_prediction_runs(prediction_run_id),
    scenario_id VARCHAR NOT NULL,
    decision_created_at TIMESTAMPTZ NOT NULL,
    decision_cutoff_at TIMESTAMPTZ NOT NULL,
    market_snapshot_fingerprint VARCHAR NOT NULL,
    prediction_snapshot_fingerprint VARCHAR NOT NULL,
    ruleset_version VARCHAR NOT NULL,
    rules_fingerprint VARCHAR NOT NULL,
    manual_override_fingerprint VARCHAR NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    knowledge_json JSON NOT NULL,
    recommendations_json JSON NOT NULL,
    snapshot_fingerprint VARCHAR NOT NULL UNIQUE,
    snapshot_path VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    CHECK (decision_created_at < decision_cutoff_at)
);

CREATE TABLE fantasy_shadow_turn_outcomes (
    turn_outcome_id VARCHAR PRIMARY KEY,
    shadow_snapshot_id VARCHAR NOT NULL
        REFERENCES fantasy_shadow_prelock_snapshots(shadow_snapshot_id),
    completed_turn INTEGER NOT NULL CHECK (completed_turn >= 1),
    source_prediction_run_id VARCHAR NOT NULL
        REFERENCES live_prediction_runs(prediction_run_id),
    source_live_run_id VARCHAR,
    observed_scores_json JSON NOT NULL,
    completed_games_json JSON NOT NULL,
    outcome_cutoff_at TIMESTAMPTZ NOT NULL,
    outcome_fingerprint VARCHAR NOT NULL UNIQUE,
    attached_at TIMESTAMPTZ NOT NULL,
    UNIQUE (shadow_snapshot_id, completed_turn)
);

CREATE TABLE fantasy_turn_advisor_runs (
    advisor_run_id VARCHAR PRIMARY KEY,
    shadow_snapshot_id VARCHAR NOT NULL
        REFERENCES fantasy_shadow_prelock_snapshots(shadow_snapshot_id),
    turn_outcome_id VARCHAR NOT NULL
        REFERENCES fantasy_shadow_turn_outcomes(turn_outcome_id),
    completed_turn INTEGER NOT NULL CHECK (completed_turn >= 1),
    status VARCHAR NOT NULL CHECK (status IN ('READY', 'NO_ACTION', 'BLOCKED')),
    current_lineup_json JSON NOT NULL,
    recommended_lineup_json JSON NOT NULL,
    actions_json JSON NOT NULL,
    evidence_json JSON NOT NULL,
    simulation_json JSON NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    advisor_fingerprint VARCHAR NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE fantasy_shadow_matchday_evaluations (
    shadow_evaluation_id VARCHAR PRIMARY KEY,
    shadow_snapshot_id VARCHAR NOT NULL UNIQUE
        REFERENCES fantasy_shadow_prelock_snapshots(shadow_snapshot_id),
    player_metrics_json JSON NOT NULL,
    strategy_metrics_json JSON NOT NULL,
    evaluation_fingerprint VARCHAR NOT NULL UNIQUE,
    evaluated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE fantasy_price_model_manifests (
    price_model_version VARCHAR PRIMARY KEY,
    training_season_code VARCHAR NOT NULL,
    training_cutoff_matchday INTEGER NOT NULL,
    training_rows INTEGER NOT NULL,
    feature_names_json JSON NOT NULL,
    chronological_folds_json JSON NOT NULL,
    validation_metrics_json JSON NOT NULL,
    model_parameters_json JSON NOT NULL,
    acceptance_status VARCHAR NOT NULL CHECK (
        acceptance_status IN ('ACCEPTED', 'ACCEPTED_SECONDARY_RMSE_ONLY', 'REJECTED')
    ),
    artifact_fingerprint VARCHAR NOT NULL UNIQUE,
    artifact_path VARCHAR,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE fantasy_price_prediction_snapshots (
    price_prediction_id VARCHAR PRIMARY KEY,
    price_model_version VARCHAR NOT NULL
        REFERENCES fantasy_price_model_manifests(price_model_version),
    prediction_run_id VARCHAR NOT NULL REFERENCES live_prediction_runs(prediction_run_id),
    scenario_id VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    target_matchday INTEGER NOT NULL,
    fantasy_entity_id VARCHAR NOT NULL REFERENCES fantasy_entities(fantasy_entity_id),
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    current_credits DOUBLE NOT NULL,
    expected_next_price DOUBLE NOT NULL,
    expected_credit_change DOUBLE NOT NULL,
    probability_increase DOUBLE NOT NULL CHECK (probability_increase BETWEEN 0 AND 1),
    probability_decrease DOUBLE NOT NULL CHECK (probability_decrease BETWEEN 0 AND 1),
    input_json JSON NOT NULL,
    prediction_fingerprint VARCHAR NOT NULL UNIQUE,
    predicted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (prediction_run_id, scenario_id, fantasy_entity_id, target_matchday)
);

CREATE TABLE fantasy_price_prediction_outcomes (
    price_outcome_id VARCHAR PRIMARY KEY,
    price_prediction_id VARCHAR NOT NULL UNIQUE
        REFERENCES fantasy_price_prediction_snapshots(price_prediction_id),
    actual_market_snapshot_record_id VARCHAR NOT NULL
        REFERENCES fantasy_market_snapshots(snapshot_record_id),
    actual_next_price DOUBLE NOT NULL,
    absolute_error DOUBLE NOT NULL,
    squared_error DOUBLE NOT NULL,
    direction_correct BOOLEAN NOT NULL,
    outcome_fingerprint VARCHAR NOT NULL UNIQUE,
    attached_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_shadow_prelock_matchday
    ON fantasy_shadow_prelock_snapshots(season_code, fantasy_matchday, created_at);
CREATE INDEX idx_shadow_turn_snapshot
    ON fantasy_shadow_turn_outcomes(shadow_snapshot_id, completed_turn);
CREATE INDEX idx_price_prediction_target
    ON fantasy_price_prediction_snapshots(season_code, target_matchday, fantasy_entity_id);

