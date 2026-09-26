CREATE TABLE role_limit_override_events (
    role_limit_event_id VARCHAR PRIMARY KEY,
    role_limit_key VARCHAR NOT NULL,
    action VARCHAR NOT NULL CHECK (action IN ('SET', 'CLEAR')),
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    season_code VARCHAR,
    canonical_game_id VARCHAR REFERENCES games(canonical_game_id),
    fantasy_matchday INTEGER,
    limit_type VARCHAR CHECK (
        limit_type IN ('MAX_MINUTES', 'EXPECTED_MINUTES', 'PERCENT_REDUCTION')
    ),
    limit_value DOUBLE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    note VARCHAR,
    created_by VARCHAR NOT NULL DEFAULT 'LOCAL_CLI',
    CHECK (
        canonical_game_id IS NOT NULL
        OR fantasy_matchday IS NOT NULL
        OR expires_at IS NOT NULL
    ),
    CHECK (
        (action = 'SET' AND limit_type IS NOT NULL AND limit_value IS NOT NULL)
        OR (action = 'CLEAR' AND limit_type IS NULL AND limit_value IS NULL)
    ),
    CHECK (
        action = 'CLEAR'
        OR (limit_type IN ('MAX_MINUTES', 'EXPECTED_MINUTES')
            AND limit_value >= 0 AND limit_value <= 40)
        OR (limit_type = 'PERCENT_REDUCTION'
            AND limit_value >= 0 AND limit_value <= 1)
    )
);

CREATE TABLE absence_scenarios (
    absence_scenario_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    canonical_game_id VARCHAR REFERENCES games(canonical_game_id),
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    scenario_name VARCHAR NOT NULL,
    scenario_status VARCHAR NOT NULL CHECK (
        scenario_status IN ('READY', 'UNRESOLVED', 'GENERATED', 'FAILED')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    feature_cutoff_time TIMESTAMPTZ NOT NULL,
    decisions_json JSON NOT NULL,
    unresolved_players_json JSON NOT NULL,
    source_slate_run_id VARCHAR REFERENCES live_slate_runs(slate_run_id),
    redistribution_model_version VARCHAR,
    reconciliation_method VARCHAR NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    output_fingerprint VARCHAR,
    warning_text VARCHAR
);

CREATE TABLE absence_scenario_player_outputs (
    scenario_player_output_id VARCHAR PRIMARY KEY,
    absence_scenario_id VARCHAR NOT NULL REFERENCES absence_scenarios(absence_scenario_id),
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    resolved_availability VARCHAR NOT NULL CHECK (
        resolved_availability IN ('PLAY', 'OUT', 'UNKNOWN', 'LIMITED')
    ),
    baseline_expected_minutes DOUBLE,
    adjusted_expected_minutes DOUBLE,
    minutes_delta_due_to_absences DOUBLE,
    team_total_missing_minutes DOUBLE,
    number_teammates_out INTEGER,
    expected_fp_before_absence_adjustment DOUBLE,
    expected_fp_after_absence_adjustment DOUBLE,
    floor_fp DOUBLE,
    median_fp DOUBLE,
    ceiling_fp DOUBLE,
    uncertainty_status VARCHAR NOT NULL,
    explanation_json JSON NOT NULL,
    model_version VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (absence_scenario_id, canonical_player_id)
);

CREATE OR REPLACE VIEW active_role_limit_overrides AS
WITH latest AS (
    SELECT event.*,
           row_number() OVER (
             PARTITION BY role_limit_key
             ORDER BY created_at DESC, role_limit_event_id DESC
           ) AS event_rank
    FROM role_limit_override_events AS event
)
SELECT * EXCLUDE (event_rank)
FROM latest
WHERE event_rank = 1
  AND action = 'SET'
  AND (expires_at IS NULL OR expires_at > current_timestamp);

CREATE INDEX idx_role_limit_player_time
    ON role_limit_override_events(canonical_player_id, created_at);
CREATE INDEX idx_absence_scenario_game
    ON absence_scenarios(season_code, canonical_game_id, created_at);
CREATE INDEX idx_absence_output_scenario
    ON absence_scenario_player_outputs(absence_scenario_id, canonical_player_id);
