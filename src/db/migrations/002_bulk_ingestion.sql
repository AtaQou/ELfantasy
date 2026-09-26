CREATE TABLE ingestion_failures (
    failure_id VARCHAR PRIMARY KEY,
    source VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    game_code INTEGER,
    component VARCHAR NOT NULL,
    failure_class VARCHAR NOT NULL CHECK (failure_class IN (
        'TRANSIENT_NETWORK',
        'RATE_LIMIT',
        'SERVER_ERROR',
        'EMPTY_SUPPORTED_ENDPOINT',
        'EXPECTED_LEGACY_EMPTY',
        'MALFORMED_RESPONSE',
        'SCHEMA_MISMATCH',
        'IDENTITY_ERROR',
        'DATA_INTEGRITY_ERROR'
    )),
    retryable BOOLEAN NOT NULL,
    first_occurred_at TIMESTAMPTZ NOT NULL,
    last_occurred_at TIMESTAMPTZ NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    last_error VARCHAR NOT NULL,
    resolved_at TIMESTAMPTZ,
    last_ingestion_run_id VARCHAR REFERENCES ingestion_runs(run_id),
    UNIQUE (source, season_code, game_code, component)
);

CREATE TABLE ingestion_checkpoints (
    checkpoint_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    game_code INTEGER,
    component VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN (
        'RAW_AVAILABLE', 'NORMALIZED', 'FAILED', 'EXPECTED_EMPTY'
    )),
    raw_path VARCHAR,
    content_sha256 VARCHAR,
    record_count BIGINT,
    updated_at TIMESTAMPTZ NOT NULL,
    ingestion_run_id VARCHAR REFERENCES ingestion_runs(run_id),
    error_class VARCHAR,
    UNIQUE (checkpoint_id)
);

CREATE INDEX idx_ingestion_failures_open
    ON ingestion_failures(season_code, component, resolved_at);
CREATE INDEX idx_ingestion_checkpoints_resume
    ON ingestion_checkpoints(season_code, component, status);
