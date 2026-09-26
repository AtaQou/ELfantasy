CREATE TABLE fantasy_control_center_state_events (
    state_event_id VARCHAR PRIMARY KEY,
    profile_id VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER,
    roster_entity_ids_json JSON NOT NULL,
    bank_credits DOUBLE NOT NULL CHECK (bank_credits >= 0),
    transfers_available INTEGER NOT NULL CHECK (transfers_available >= 0),
    scenario_id VARCHAR,
    player_constraints_json JSON NOT NULL,
    state_fingerprint VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE fantasy_control_center_runs (
    control_center_run_id VARCHAR PRIMARY KEY,
    profile_id VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    status VARCHAR NOT NULL CHECK (
        status IN ('SUCCEEDED', 'BLOCKED', 'FAILED')
    ),
    market_snapshot_fingerprint VARCHAR,
    prediction_snapshot_fingerprint VARCHAR,
    prediction_run_id VARCHAR REFERENCES live_prediction_runs(prediction_run_id),
    ruleset_version VARCHAR NOT NULL,
    rules_fingerprint VARCHAR NOT NULL,
    manual_override_fingerprint VARCHAR NOT NULL,
    current_roster_json JSON NOT NULL,
    bank_credits DOUBLE NOT NULL,
    transfer_limit INTEGER NOT NULL,
    optimization_constraints_json JSON NOT NULL,
    recommendations_json JSON NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    run_fingerprint VARCHAR NOT NULL UNIQUE,
    snapshot_path VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE OR REPLACE VIEW current_fantasy_control_center_state AS
SELECT * EXCLUDE (state_rank)
FROM (
    SELECT event.*,
           row_number() OVER (
             PARTITION BY profile_id
             ORDER BY created_at DESC, state_event_id DESC
           ) AS state_rank
    FROM fantasy_control_center_state_events AS event
)
WHERE state_rank = 1;

CREATE INDEX idx_control_center_state_profile
    ON fantasy_control_center_state_events(profile_id, created_at);
CREATE INDEX idx_control_center_runs_matchday
    ON fantasy_control_center_runs(season_code, fantasy_matchday, created_at);
