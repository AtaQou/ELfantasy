CREATE TABLE ingestion_runs (
    run_id VARCHAR PRIMARY KEY,
    source VARCHAR NOT NULL,
    command VARCHAR,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    competition_code VARCHAR,
    season_code VARCHAR,
    records_downloaded BIGINT NOT NULL DEFAULT 0,
    records_inserted BIGINT NOT NULL DEFAULT 0,
    records_updated BIGINT NOT NULL DEFAULT 0,
    error_count BIGINT NOT NULL DEFAULT 0,
    errors_json JSON,
    code_version VARCHAR
);

CREATE TABLE raw_artifacts (
    artifact_id VARCHAR PRIMARY KEY,
    source VARCHAR NOT NULL,
    source_endpoint VARCHAR NOT NULL,
    source_url VARCHAR,
    competition_code VARCHAR,
    season_code VARCHAR,
    game_code INTEGER,
    matchday_number INTEGER,
    fetched_at TIMESTAMPTZ,
    stored_at TIMESTAMPTZ NOT NULL,
    raw_path VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    byte_count BIGINT NOT NULL CHECK (byte_count >= 0),
    media_type VARCHAR,
    is_sanitized BOOLEAN NOT NULL DEFAULT false,
    auth_data_included BOOLEAN NOT NULL DEFAULT false CHECK (auth_data_included = false),
    ingestion_run_id VARCHAR REFERENCES ingestion_runs(run_id),
    UNIQUE (raw_path, content_sha256)
);

CREATE TABLE competitions (
    competition_id VARCHAR PRIMARY KEY,
    competition_code VARCHAR NOT NULL UNIQUE,
    competition_name VARCHAR NOT NULL
);

CREATE TABLE seasons (
    season_id VARCHAR PRIMARY KEY,
    competition_id VARCHAR NOT NULL REFERENCES competitions(competition_id),
    season_code VARCHAR NOT NULL UNIQUE,
    season_label VARCHAR NOT NULL,
    start_year INTEGER NOT NULL,
    start_date DATE,
    end_date DATE
);

CREATE TABLE teams (
    canonical_team_id VARCHAR PRIMARY KEY,
    official_team_code VARCHAR UNIQUE,
    canonical_name VARCHAR NOT NULL,
    country_code VARCHAR,
    created_from_source VARCHAR NOT NULL
);

CREATE TABLE team_aliases (
    team_alias_id VARCHAR PRIMARY KEY,
    canonical_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    source VARCHAR NOT NULL,
    alias_type VARCHAR NOT NULL CHECK (alias_type IN ('CODE', 'NAME', 'ABBREVIATION', 'PROVIDER_ID')),
    alias_value VARCHAR NOT NULL,
    normalized_alias VARCHAR NOT NULL,
    season_code VARCHAR,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    match_method VARCHAR NOT NULL,
    manually_reviewed BOOLEAN NOT NULL DEFAULT false,
    notes VARCHAR,
    UNIQUE (source, alias_type, normalized_alias, season_code)
);

CREATE TABLE players (
    canonical_player_id VARCHAR PRIMARY KEY,
    official_player_code VARCHAR UNIQUE,
    canonical_name VARCHAR NOT NULL,
    birth_date DATE,
    nationality_code VARCHAR,
    nationality VARCHAR,
    height_cm SMALLINT,
    weight_kg SMALLINT,
    created_from_source VARCHAR NOT NULL
);

CREATE TABLE player_aliases (
    player_alias_id VARCHAR PRIMARY KEY,
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    source VARCHAR NOT NULL,
    alias_value VARCHAR NOT NULL,
    normalized_alias VARCHAR NOT NULL,
    season_code VARCHAR,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    match_method VARCHAR NOT NULL,
    manually_reviewed BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (canonical_player_id, source, normalized_alias, season_code)
);

CREATE TABLE player_team_memberships (
    membership_id VARCHAR PRIMARY KEY,
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    canonical_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    season_id VARCHAR NOT NULL REFERENCES seasons(season_id),
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    jersey_number VARCHAR,
    position_code VARCHAR,
    position_name VARCHAR,
    active BOOLEAN,
    source VARCHAR NOT NULL,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (canonical_player_id, canonical_team_id, season_id, source, valid_from)
);

CREATE TABLE games (
    canonical_game_id VARCHAR PRIMARY KEY,
    official_game_id VARCHAR,
    season_id VARCHAR NOT NULL REFERENCES seasons(season_id),
    competition_code VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    game_code INTEGER NOT NULL,
    game_identifier VARCHAR,
    phase_code VARCHAR,
    round_number INTEGER,
    game_date TIMESTAMPTZ,
    local_game_date TIMESTAMP,
    home_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    away_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    home_score INTEGER,
    away_score INTEGER,
    overtime_count SMALLINT,
    game_status VARCHAR,
    played BOOLEAN,
    venue_code VARCHAR,
    venue_name VARCHAR,
    venue_capacity INTEGER,
    neutral_venue BOOLEAN,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (home_team_id <> away_team_id),
    CHECK (home_score IS NULL OR home_score >= 0),
    CHECK (away_score IS NULL OR away_score >= 0),
    CHECK (overtime_count IS NULL OR overtime_count >= 0),
    UNIQUE (season_id, game_code)
);

CREATE VIEW games_with_derived_winner AS
SELECT
    games.*,
    CASE
        WHEN home_score > away_score THEN home_team_id
        WHEN away_score > home_score THEN away_team_id
        ELSE NULL
    END AS derived_winner_team_id
FROM games;

CREATE TABLE player_game_stats (
    player_game_id VARCHAR PRIMARY KEY,
    canonical_game_id VARCHAR NOT NULL REFERENCES games(canonical_game_id),
    canonical_player_id VARCHAR NOT NULL REFERENCES players(canonical_player_id),
    canonical_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    opponent_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    raw_player_code VARCHAR,
    home_away VARCHAR NOT NULL CHECK (home_away IN ('home', 'away')),
    jersey_number VARCHAR,
    starter BOOLEAN,
    source_is_playing BOOLEAN,
    minutes_raw VARCHAR,
    minutes DOUBLE,
    did_not_play BOOLEAN NOT NULL,
    points INTEGER,
    two_points_made INTEGER,
    two_points_attempted INTEGER,
    three_points_made INTEGER,
    three_points_attempted INTEGER,
    free_throws_made INTEGER,
    free_throws_attempted INTEGER,
    offensive_rebounds INTEGER,
    defensive_rebounds INTEGER,
    total_rebounds INTEGER,
    assists INTEGER,
    steals INTEGER,
    turnovers INTEGER,
    blocks INTEGER,
    blocks_received INTEGER,
    fouls_committed INTEGER,
    fouls_drawn INTEGER,
    pir INTEGER,
    plus_minus INTEGER,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (minutes IS NULL OR minutes >= 0),
    CHECK (points IS NULL OR points >= 0),
    CHECK (two_points_made IS NULL OR two_points_made >= 0),
    CHECK (two_points_attempted IS NULL OR two_points_attempted >= 0),
    CHECK (three_points_made IS NULL OR three_points_made >= 0),
    CHECK (three_points_attempted IS NULL OR three_points_attempted >= 0),
    CHECK (free_throws_made IS NULL OR free_throws_made >= 0),
    CHECK (free_throws_attempted IS NULL OR free_throws_attempted >= 0),
    UNIQUE (canonical_game_id, canonical_player_id, canonical_team_id)
);

CREATE TABLE team_game_stats (
    team_game_id VARCHAR PRIMARY KEY,
    canonical_game_id VARCHAR NOT NULL REFERENCES games(canonical_game_id),
    canonical_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    opponent_team_id VARCHAR NOT NULL REFERENCES teams(canonical_team_id),
    home_away VARCHAR NOT NULL CHECK (home_away IN ('home', 'away')),
    team_name_raw VARCHAR,
    coach_name VARCHAR,
    minutes_raw VARCHAR,
    team_rebounds_offensive INTEGER,
    team_rebounds_defensive INTEGER,
    team_rebounds_total INTEGER,
    points INTEGER,
    two_points_made INTEGER,
    two_points_attempted INTEGER,
    three_points_made INTEGER,
    three_points_attempted INTEGER,
    free_throws_made INTEGER,
    free_throws_attempted INTEGER,
    offensive_rebounds INTEGER,
    defensive_rebounds INTEGER,
    total_rebounds INTEGER,
    assists INTEGER,
    steals INTEGER,
    turnovers INTEGER,
    blocks INTEGER,
    blocks_received INTEGER,
    fouls_committed INTEGER,
    fouls_drawn INTEGER,
    pir INTEGER,
    plus_minus INTEGER,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (points IS NULL OR points >= 0),
    UNIQUE (canonical_game_id, canonical_team_id)
);

CREATE TABLE play_by_play_events (
    pbp_event_id VARCHAR PRIMARY KEY,
    canonical_game_id VARCHAR NOT NULL REFERENCES games(canonical_game_id),
    period SMALLINT NOT NULL,
    game_clock VARCHAR,
    elapsed_minute INTEGER,
    source_sequence INTEGER NOT NULL,
    source_period VARCHAR NOT NULL,
    raw_event_number INTEGER,
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    raw_team_code VARCHAR,
    raw_player_code VARCHAR,
    event_type VARCHAR,
    play_type VARCHAR,
    player_name_raw VARCHAR,
    team_name_raw VARCHAR,
    jersey_number VARCHAR,
    home_score INTEGER,
    away_score INTEGER,
    comment VARCHAR,
    play_info VARCHAR,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (period >= 1),
    UNIQUE (canonical_game_id, source_sequence)
);

CREATE TABLE shots (
    shot_id VARCHAR PRIMARY KEY,
    canonical_game_id VARCHAR NOT NULL REFERENCES games(canonical_game_id),
    source_sequence INTEGER NOT NULL,
    raw_event_number INTEGER,
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    raw_team_code VARCHAR,
    raw_player_code VARCHAR,
    player_name_raw VARCHAR,
    action_id VARCHAR,
    action VARCHAR,
    points INTEGER,
    made BOOLEAN,
    points_value SMALLINT,
    coordinate_x DOUBLE,
    coordinate_y DOUBLE,
    zone VARCHAR,
    fast_break BOOLEAN,
    second_chance BOOLEAN,
    points_off_turnover BOOLEAN,
    period SMALLINT,
    elapsed_minute INTEGER,
    game_clock VARCHAR,
    home_score INTEGER,
    away_score INTEGER,
    utc_timestamp_raw VARCHAR,
    feed_completeness_note VARCHAR NOT NULL DEFAULT 'Field goals and made free throws; missed free throws are omitted by this source.',
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (canonical_game_id, source_sequence)
);

CREATE TABLE fantasy_entities (
    fantasy_entity_id VARCHAR PRIMARY KEY,
    fantasy_provider VARCHAR NOT NULL,
    fantasy_id VARCHAR NOT NULL,
    entity_type VARCHAR NOT NULL CHECK (entity_type IN ('PLAYER', 'COACH')),
    display_name VARCHAR NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (fantasy_provider, fantasy_id)
);

CREATE TABLE fantasy_player_crosswalk (
    crosswalk_id VARCHAR PRIMARY KEY,
    fantasy_entity_id VARCHAR NOT NULL REFERENCES fantasy_entities(fantasy_entity_id),
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    mapping_status VARCHAR NOT NULL CHECK (mapping_status IN ('MATCHED', 'AMBIGUOUS', 'UNMATCHED')),
    confidence VARCHAR NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW', 'NONE')),
    match_method VARCHAR NOT NULL,
    team_context_id VARCHAR REFERENCES teams(canonical_team_id),
    season_code VARCHAR,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    manually_reviewed BOOLEAN NOT NULL DEFAULT false,
    notes VARCHAR,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (
        (mapping_status = 'MATCHED' AND canonical_player_id IS NOT NULL)
        OR (mapping_status <> 'MATCHED' AND canonical_player_id IS NULL)
    ),
    UNIQUE (fantasy_entity_id, season_code, valid_from)
);

CREATE TABLE fantasy_market_snapshots (
    snapshot_record_id VARCHAR PRIMARY KEY,
    snapshot_batch_id VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    season_code VARCHAR NOT NULL,
    competition_id INTEGER,
    players_list_id INTEGER,
    matchday_id INTEGER NOT NULL,
    matchday_number INTEGER NOT NULL,
    turn_id INTEGER,
    turn_number INTEGER,
    fantasy_entity_id VARCHAR NOT NULL REFERENCES fantasy_entities(fantasy_entity_id),
    fantasy_team_provider_id VARCHAR,
    canonical_team_id VARCHAR REFERENCES teams(canonical_team_id),
    fantasy_opponent_provider_id VARCHAR,
    opponent_team_id VARCHAR REFERENCES teams(canonical_team_id),
    team_name_raw VARCHAR,
    opponent_name_raw VARCHAR,
    position_id INTEGER,
    position_name VARCHAR,
    credits DOUBLE NOT NULL CHECK (credits >= 0),
    overlay_average_points DOUBLE,
    overlay_popularity DOUBLE,
    jersey_number VARCHAR,
    overlay_is_injured BOOLEAN,
    overlay_probability_of_playing DOUBLE,
    overlay_started_from_bench BOOLEAN,
    overlay_is_on_fire BOOLEAN,
    status_valid_as_of_matchday BOOLEAN NOT NULL,
    status_semantics VARCHAR NOT NULL,
    position_semantics VARCHAR NOT NULL,
    price_semantics VARCHAR NOT NULL,
    market_available BOOLEAN,
    source_artifact_id VARCHAR NOT NULL REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (snapshot_batch_id, fantasy_entity_id)
);

CREATE VIEW leakage_safe_fantasy_market AS
SELECT
    snapshot_record_id,
    snapshot_batch_id,
    observed_at,
    season_code,
    matchday_id,
    matchday_number,
    fantasy_entity_id,
    canonical_team_id,
    opponent_team_id,
    position_name,
    position_semantics,
    credits,
    price_semantics,
    CASE WHEN status_valid_as_of_matchday THEN overlay_average_points ELSE NULL END AS average_points,
    overlay_popularity AS popularity_observed_at_collection,
    CASE WHEN status_valid_as_of_matchday THEN overlay_is_injured ELSE NULL END AS is_injured,
    CASE WHEN status_valid_as_of_matchday THEN overlay_probability_of_playing ELSE NULL END AS probability_of_playing,
    CASE WHEN status_valid_as_of_matchday THEN overlay_started_from_bench ELSE NULL END AS started_from_bench,
    CASE WHEN status_valid_as_of_matchday THEN overlay_is_on_fire ELSE NULL END AS is_on_fire,
    status_valid_as_of_matchday,
    status_semantics,
    source_artifact_id
FROM fantasy_market_snapshots;

CREATE TABLE availability_events (
    availability_event_id VARCHAR PRIMARY KEY,
    canonical_player_id VARCHAR REFERENCES players(canonical_player_id),
    source VARCHAR NOT NULL,
    source_player_id VARCHAR,
    status VARCHAR,
    probability DOUBLE,
    injury_type VARCHAR,
    description VARCHAR,
    published_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    source_reference VARCHAR,
    confidence VARCHAR,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (probability IS NULL OR (probability >= 0 AND probability <= 1))
);

CREATE TABLE data_anomalies (
    anomaly_id VARCHAR PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    anomaly_code VARCHAR NOT NULL,
    severity VARCHAR NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR')),
    details_json JSON,
    detected_at TIMESTAMPTZ NOT NULL,
    quarantined BOOLEAN NOT NULL DEFAULT false,
    resolved_at TIMESTAMPTZ,
    resolution_notes VARCHAR,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (entity_type, entity_id, anomaly_code)
);

CREATE VIEW quarantined_entities AS
SELECT entity_type, entity_id, anomaly_code, severity, details_json, detected_at
FROM data_anomalies
WHERE quarantined AND resolved_at IS NULL;

CREATE INDEX idx_games_tip_time ON games(game_date);
CREATE INDEX idx_player_games_player_time ON player_game_stats(canonical_player_id, canonical_game_id);
CREATE INDEX idx_pbp_game_sequence ON play_by_play_events(canonical_game_id, source_sequence);
CREATE INDEX idx_fantasy_market_entity_time ON fantasy_market_snapshots(fantasy_entity_id, observed_at);
CREATE INDEX idx_availability_known_time ON availability_events(canonical_player_id, published_at, observed_at);
