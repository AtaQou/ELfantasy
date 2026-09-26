CREATE TABLE raw_artifact_request_provenance (
    artifact_id VARCHAR PRIMARY KEY REFERENCES raw_artifacts(artifact_id),
    request_parameters_json JSON NOT NULL,
    parser_schema_version VARCHAR NOT NULL
);

CREATE TABLE fantasy_identity_classifications (
    crosswalk_id VARCHAR PRIMARY KEY
        REFERENCES fantasy_player_crosswalk(crosswalk_id),
    resolution_classification VARCHAR NOT NULL CHECK (
        resolution_classification IN (
            'MATCHED',
            'AMBIGUOUS',
            'NAME_TEAM_CONFLICT',
            'NO_OFFICIAL_CANDIDATE',
            'UNKNOWN'
        )
    ),
    classified_at TIMESTAMPTZ NOT NULL,
    notes VARCHAR
);

CREATE TABLE fantasy_market_snapshot_semantics (
    snapshot_record_id VARCHAR PRIMARY KEY
        REFERENCES fantasy_market_snapshots(snapshot_record_id),
    source_system VARCHAR NOT NULL,
    source_season_id VARCHAR,
    matchday_identifier_semantics VARCHAR NOT NULL,
    competition_round INTEGER,
    credits_valid_pre_matchday BOOLEAN NOT NULL,
    position_valid_as_of_matchday BOOLEAN NOT NULL,
    market_population_semantics VARCHAR NOT NULL,
    source_request_parameters_json JSON NOT NULL
);

CREATE TABLE fantasy_matchday_round_mapping (
    mapping_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    competition_round INTEGER NOT NULL,
    mapping_status VARCHAR NOT NULL CHECK (mapping_status IN (
        'VALIDATED_DIRECT', 'PLAYER_DATE_CONTEXT_REQUIRED', 'UNRESOLVED'
    )),
    notes VARCHAR,
    source_artifact_id VARCHAR REFERENCES raw_artifacts(artifact_id),
    ingestion_run_id VARCHAR NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (season_code, fantasy_matchday, competition_round)
);

DROP VIEW leakage_safe_fantasy_market;

CREATE VIEW leakage_safe_fantasy_market AS
WITH current_crosswalk AS (
    SELECT * EXCLUDE (priority)
    FROM (
        SELECT crosswalk.*,
               row_number() OVER (
                   PARTITION BY fantasy_entity_id, season_code
                   ORDER BY
                       CASE WHEN valid_to IS NULL THEN 0 ELSE 1 END,
                       CASE WHEN mapping_status = 'MATCHED' THEN 0 ELSE 1 END,
                       valid_from DESC NULLS LAST,
                       crosswalk_id DESC
               ) AS priority
        FROM fantasy_player_crosswalk AS crosswalk
    )
    WHERE priority = 1
)
SELECT
    snapshot.snapshot_record_id,
    snapshot.snapshot_batch_id,
    snapshot.observed_at,
    snapshot.season_code,
    snapshot.matchday_id,
    snapshot.matchday_number,
    semantics.competition_round,
    snapshot.fantasy_entity_id,
    entity.fantasy_id,
    entity.display_name AS fantasy_name,
    crosswalk.canonical_player_id AS player_id,
    coalesce(
        classification.resolution_classification,
        CASE crosswalk.mapping_status
            WHEN 'MATCHED' THEN 'MATCHED'
            WHEN 'AMBIGUOUS' THEN 'AMBIGUOUS'
            ELSE 'UNKNOWN'
        END,
        'UNKNOWN'
    )
        AS identity_match_status,
    snapshot.canonical_team_id,
    snapshot.team_name_raw AS team,
    snapshot.opponent_team_id,
    CASE WHEN coalesce(semantics.position_valid_as_of_matchday, false)
         THEN snapshot.position_name ELSE NULL END AS position_name,
    CASE WHEN coalesce(semantics.position_valid_as_of_matchday, false)
         THEN snapshot.position_name ELSE NULL END AS fantasy_position,
    snapshot.position_semantics,
    coalesce(semantics.position_valid_as_of_matchday, false)
        AS position_valid_as_of_matchday,
    CASE WHEN coalesce(semantics.credits_valid_pre_matchday, true)
         THEN snapshot.credits ELSE NULL END AS credits,
    CASE WHEN coalesce(semantics.credits_valid_pre_matchday, true)
         THEN snapshot.credits ELSE NULL END AS fantasy_credits_pre_matchday,
    coalesce(semantics.credits_valid_pre_matchday, true)
        AS credits_valid_pre_matchday,
    snapshot.price_semantics,
    CASE WHEN snapshot.status_valid_as_of_matchday
         THEN snapshot.overlay_average_points ELSE NULL END AS average_points,
    snapshot.overlay_popularity AS popularity_observed_at_collection,
    CASE WHEN snapshot.status_valid_as_of_matchday
         THEN snapshot.overlay_is_injured ELSE NULL END AS is_injured,
    CASE WHEN snapshot.status_valid_as_of_matchday
         THEN snapshot.overlay_probability_of_playing ELSE NULL END
         AS probability_of_playing,
    CASE WHEN snapshot.status_valid_as_of_matchday
         THEN snapshot.overlay_started_from_bench ELSE NULL END
         AS started_from_bench,
    CASE WHEN snapshot.status_valid_as_of_matchday
         THEN snapshot.overlay_is_on_fire ELSE NULL END AS is_on_fire,
    snapshot.status_valid_as_of_matchday,
    snapshot.status_semantics,
    coalesce(semantics.source_system, 'FANTAKING_MARKET_API') AS source,
    coalesce(semantics.market_population_semantics, 'FULL_MARKET_RESPONSE')
        AS market_population_semantics,
    snapshot.source_artifact_id
FROM fantasy_market_snapshots AS snapshot
JOIN fantasy_entities AS entity USING (fantasy_entity_id)
LEFT JOIN fantasy_market_snapshot_semantics AS semantics
  USING (snapshot_record_id)
LEFT JOIN current_crosswalk AS crosswalk
  ON crosswalk.fantasy_entity_id = snapshot.fantasy_entity_id
 AND crosswalk.season_code = snapshot.season_code
LEFT JOIN fantasy_identity_classifications AS classification
  ON classification.crosswalk_id = crosswalk.crosswalk_id;

CREATE INDEX idx_fantasy_market_season_matchday
    ON fantasy_market_snapshots(season_code, matchday_number);
CREATE INDEX idx_fantasy_mapping_round
    ON fantasy_matchday_round_mapping(season_code, fantasy_matchday);
