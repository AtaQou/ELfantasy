CREATE TABLE live_update_runs (
    live_run_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    command VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
    dry_run BOOLEAN NOT NULL DEFAULT false,
    stage_results_json JSON,
    warning_count INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds DOUBLE,
    input_fingerprint VARCHAR,
    output_fingerprint VARCHAR
);

CREATE TABLE live_source_refresh_events (
    refresh_event_id VARCHAR PRIMARY KEY,
    live_run_id VARCHAR REFERENCES live_update_runs(live_run_id),
    source_name VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    succeeded BOOLEAN NOT NULL,
    row_count INTEGER,
    source_identifier VARCHAR,
    snapshot_batch_id VARCHAR,
    message VARCHAR,
    content_fingerprint VARCHAR
);

CREATE TABLE live_schedule_snapshots (
    schedule_snapshot_id VARCHAR PRIMARY KEY,
    snapshot_batch_id VARCHAR NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    season_code VARCHAR NOT NULL,
    canonical_game_id VARCHAR NOT NULL,
    official_game_id VARCHAR,
    game_code INTEGER NOT NULL,
    phase_code VARCHAR,
    round_number INTEGER,
    scheduled_tip_time TIMESTAMPTZ,
    local_game_date TIMESTAMP,
    home_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    away_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    home_score INTEGER,
    away_score INTEGER,
    game_status VARCHAR,
    played BOOLEAN NOT NULL,
    schedule_state VARCHAR NOT NULL CHECK (
        schedule_state IN ('NEW_GAME', 'UPDATED_GAME', 'UNCHANGED_GAME',
                           'POSTPONED_RESCHEDULED_GAME')
    ),
    change_summary_json JSON NOT NULL,
    source_identifier VARCHAR NOT NULL,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    row_fingerprint VARCHAR NOT NULL,
    UNIQUE (snapshot_batch_id, canonical_game_id)
);

CREATE TABLE live_roster_snapshots (
    roster_snapshot_id VARCHAR PRIMARY KEY,
    snapshot_batch_id VARCHAR NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    season_code VARCHAR NOT NULL,
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    canonical_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    roster_status VARCHAR NOT NULL CHECK (
        roster_status IN ('CURRENT_ROSTER', 'NOT_REGISTERED', 'LEFT_TEAM', 'UNKNOWN')
    ),
    source_active BOOLEAN,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    jersey_number VARCHAR,
    position_code VARCHAR,
    position_name VARCHAR,
    source_identifier VARCHAR NOT NULL,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    row_fingerprint VARCHAR NOT NULL,
    UNIQUE (snapshot_batch_id, canonical_player_id, canonical_team_id)
);

CREATE TABLE official_availability_reports (
    report_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    matchday_number INTEGER,
    source_url VARCHAR NOT NULL,
    title VARCHAR,
    published_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL,
    publication_time_reliable BOOLEAN NOT NULL DEFAULT false,
    source_type VARCHAR NOT NULL,
    parse_status VARCHAR NOT NULL CHECK (
        parse_status IN ('DISCOVERED', 'PARSED', 'PARTIAL', 'BLOCKED', 'REJECTED')
    ),
    observation_count INTEGER NOT NULL DEFAULT 0,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    content_fingerprint VARCHAR,
    notes VARCHAR,
    UNIQUE (source_url, captured_at)
);

CREATE TABLE availability_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    report_id VARCHAR REFERENCES official_availability_reports(report_id),
    season_code VARCHAR NOT NULL,
    canonical_game_id VARCHAR REFERENCES games(canonical_game_id),
    matchday_number INTEGER,
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    raw_player_name VARCHAR,
    raw_team_name VARCHAR,
    captured_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    effective_game_time TIMESTAMPTZ,
    hours_before_tip DOUBLE,
    timing_status VARCHAR NOT NULL CHECK (
        timing_status IN ('PRE_GAME', 'POST_GAME', 'UNKNOWN')
    ),
    raw_status VARCHAR,
    normalized_status VARCHAR NOT NULL CHECK (
        normalized_status IN ('AVAILABLE', 'PROBABLE', 'QUESTIONABLE',
          'DOUBTFUL', 'GAME_TIME_DECISION', 'OUT', 'SUSPENDED',
          'NOT_REGISTERED', 'LIMITED', 'UNKNOWN')
    ),
    reason_category VARCHAR NOT NULL CHECK (
        reason_category IN ('INJURY', 'ILLNESS', 'SUSPENSION', 'PERSONAL',
          'REST', 'NOT_REGISTERED', 'OTHER', 'UNKNOWN')
    ),
    raw_text VARCHAR,
    source_summary VARCHAR,
    source_type VARCHAR NOT NULL,
    source_identifier VARCHAR NOT NULL,
    source_priority INTEGER NOT NULL CHECK (source_priority >= 0),
    identity_match_status VARCHAR NOT NULL CHECK (
        identity_match_status IN ('MATCHED', 'AMBIGUOUS', 'UNMATCHED')
    ),
    is_manual_override BOOLEAN NOT NULL DEFAULT false,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    snapshot_fingerprint VARCHAR NOT NULL
);

CREATE TABLE availability_override_events (
    override_event_id VARCHAR PRIMARY KEY,
    override_key VARCHAR NOT NULL,
    action VARCHAR NOT NULL CHECK (action IN ('SET', 'CLEAR')),
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    normalized_status VARCHAR CHECK (
        normalized_status IN ('AVAILABLE', 'PROBABLE', 'QUESTIONABLE',
          'DOUBTFUL', 'GAME_TIME_DECISION', 'OUT', 'SUSPENDED',
          'NOT_REGISTERED', 'LIMITED', 'UNKNOWN')
    ),
    reason_category VARCHAR CHECK (
        reason_category IN ('INJURY', 'ILLNESS', 'SUSPENSION', 'PERSONAL',
          'REST', 'NOT_REGISTERED', 'OTHER', 'UNKNOWN')
    ),
    season_code VARCHAR,
    canonical_game_id VARCHAR REFERENCES games(canonical_game_id),
    fantasy_matchday INTEGER,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    note VARCHAR,
    created_by VARCHAR NOT NULL DEFAULT 'LOCAL_CLI',
    CHECK (
        action = 'CLEAR'
        OR canonical_game_id IS NOT NULL
        OR fantasy_matchday IS NOT NULL
        OR expires_at IS NOT NULL
    ),
    CHECK (
        (action = 'SET' AND normalized_status IS NOT NULL)
        OR (action = 'CLEAR' AND normalized_status IS NULL)
    )
);

CREATE TABLE live_slate_runs (
    slate_run_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    feature_cutoff_time TIMESTAMPTZ NOT NULL,
    row_count INTEGER NOT NULL,
    required_feature_count INTEGER NOT NULL,
    schema_compatible BOOLEAN NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    output_fingerprint VARCHAR NOT NULL,
    source_freshness_json JSON NOT NULL,
    warnings_json JSON NOT NULL,
    elapsed_seconds DOUBLE NOT NULL
);

CREATE TABLE prospective_prediction_runs (
    prediction_run_id VARCHAR PRIMARY KEY,
    slate_run_id VARCHAR REFERENCES live_slate_runs(slate_run_id),
    prediction_timestamp TIMESTAMPTZ NOT NULL,
    feature_cutoff_time TIMESTAMPTZ NOT NULL,
    model_version VARCHAR NOT NULL,
    feature_version VARCHAR NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    prediction_protocol VARCHAR NOT NULL,
    created_before_outcomes BOOLEAN NOT NULL DEFAULT true
);

CREATE OR REPLACE VIEW current_live_schedule AS
SELECT * EXCLUDE (snapshot_rank)
FROM (
    SELECT snapshot.*,
           row_number() OVER (
             PARTITION BY season_code, canonical_game_id
             ORDER BY captured_at DESC, schedule_snapshot_id DESC
           ) AS snapshot_rank
    FROM live_schedule_snapshots AS snapshot
)
WHERE snapshot_rank = 1;

CREATE OR REPLACE VIEW current_fantasy_market AS
SELECT * EXCLUDE (snapshot_rank)
FROM (
    SELECT market.*,
           row_number() OVER (
             PARTITION BY season_code, fantasy_entity_id
             ORDER BY observed_at DESC, snapshot_record_id DESC
           ) AS snapshot_rank
    FROM fantasy_market_snapshots AS market
)
WHERE snapshot_rank = 1;

CREATE OR REPLACE VIEW current_live_roster AS
SELECT * EXCLUDE (snapshot_rank)
FROM (
    SELECT snapshot.*,
           row_number() OVER (
             PARTITION BY season_code, canonical_player_id
             ORDER BY captured_at DESC, roster_snapshot_id DESC
           ) AS snapshot_rank
    FROM live_roster_snapshots AS snapshot
)
WHERE snapshot_rank = 1;

CREATE OR REPLACE VIEW availability_status_trajectories AS
SELECT snapshot.*,
       lag(normalized_status) OVER (
         PARTITION BY season_code, canonical_player_id,
                      coalesce(canonical_game_id, '')
         ORDER BY captured_at, snapshot_id
       ) AS previous_normalized_status,
       lag(captured_at) OVER (
         PARTITION BY season_code, canonical_player_id,
                      coalesce(canonical_game_id, '')
         ORDER BY captured_at, snapshot_id
       ) AS previous_status_captured_at
FROM availability_snapshots AS snapshot;

CREATE OR REPLACE VIEW active_availability_overrides AS
WITH latest AS (
    SELECT event.*,
           row_number() OVER (
             PARTITION BY override_key
             ORDER BY created_at DESC, override_event_id DESC
           ) AS event_rank
    FROM availability_override_events AS event
)
SELECT * EXCLUDE (event_rank)
FROM latest
WHERE event_rank = 1
  AND action = 'SET'
  AND (expires_at IS NULL OR expires_at > current_timestamp);

CREATE INDEX idx_live_schedule_current
    ON live_schedule_snapshots(season_code, canonical_game_id, captured_at);
CREATE INDEX idx_live_refresh_source
    ON live_source_refresh_events(source_name, captured_at);
CREATE INDEX idx_live_roster_current
    ON live_roster_snapshots(season_code, canonical_player_id, captured_at);
CREATE INDEX idx_availability_player_time
    ON availability_snapshots(canonical_player_id, captured_at);
CREATE INDEX idx_availability_game_time
    ON availability_snapshots(canonical_game_id, captured_at);
CREATE INDEX idx_override_player_time
    ON availability_override_events(canonical_player_id, created_at);
