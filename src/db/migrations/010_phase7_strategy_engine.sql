CREATE TABLE fantasy_rules_manifests (
    ruleset_version VARCHAR PRIMARY KEY,
    verification_status VARCHAR NOT NULL,
    season_codes_json JSON NOT NULL,
    rules_json JSON NOT NULL,
    rules_fingerprint VARCHAR NOT NULL UNIQUE,
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE fantasy_strategy_runs (
    strategy_run_id VARCHAR PRIMARY KEY,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    decision_stage VARCHAR NOT NULL CHECK (
        decision_stage IN ('PRE_T1', 'BETWEEN_TURNS', 'HISTORICAL_REPLAY')
    ),
    completed_turn INTEGER,
    status VARCHAR NOT NULL CHECK (
        status IN ('SUCCEEDED', 'BLOCKED_RULES', 'NO_MARKET', 'FAILED')
    ),
    ruleset_version VARCHAR NOT NULL REFERENCES fantasy_rules_manifests(ruleset_version),
    rules_fingerprint VARCHAR NOT NULL,
    predictive_model_version VARCHAR NOT NULL,
    predictive_artifact_fingerprint VARCHAR NOT NULL,
    optimizer_version VARCHAR NOT NULL,
    simulation_version VARCHAR NOT NULL,
    simulation_seed BIGINT NOT NULL,
    simulation_count INTEGER NOT NULL,
    budget DOUBLE NOT NULL,
    total_credits DOUBLE,
    solver_status VARCHAR,
    solver_gap DOUBLE,
    expected_final_score DOUBLE,
    p10_final_score DOUBLE,
    p50_final_score DOUBLE,
    p90_final_score DOUBLE,
    input_fingerprint VARCHAR NOT NULL,
    run_fingerprint VARCHAR NOT NULL UNIQUE,
    source_prediction_run_id VARCHAR REFERENCES live_prediction_runs(prediction_run_id),
    observed_results_json JSON NOT NULL,
    selected_roster_json JSON NOT NULL,
    recommended_actions_json JSON NOT NULL,
    simulation_diagnostics_json JSON NOT NULL,
    output_json_path VARCHAR,
    output_tabular_path VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE fantasy_strategy_replay_results (
    replay_result_id VARCHAR PRIMARY KEY,
    backtest_version VARCHAR NOT NULL,
    season_code VARCHAR NOT NULL,
    fantasy_matchday INTEGER NOT NULL,
    strategy_name VARCHAR NOT NULL,
    decision_cutoff TIMESTAMPTZ NOT NULL,
    available_budget DOUBLE NOT NULL,
    total_credits DOUBLE NOT NULL,
    trades_used INTEGER NOT NULL,
    trade_limit INTEGER,
    predicted_final_score DOUBLE,
    actual_final_score DOUBLE NOT NULL,
    oracle_final_score DOUBLE,
    captain_switches INTEGER NOT NULL,
    substitutions INTEGER NOT NULL,
    roster_fingerprint VARCHAR NOT NULL,
    rules_fingerprint VARCHAR NOT NULL,
    prediction_fingerprint VARCHAR NOT NULL,
    result_fingerprint VARCHAR NOT NULL,
    oracle_is_evaluation_only BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UNIQUE (backtest_version, season_code, fantasy_matchday, strategy_name)
);

CREATE INDEX idx_fantasy_strategy_season_matchday
    ON fantasy_strategy_runs(season_code, fantasy_matchday, created_at);
CREATE INDEX idx_fantasy_replay_strategy
    ON fantasy_strategy_replay_results(backtest_version, strategy_name, fantasy_matchday);
