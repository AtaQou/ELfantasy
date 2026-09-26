ALTER TABLE live_prediction_runs
ADD COLUMN scoring_rule_version VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN scoring_rules_compatibility VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN scoring_rules_evidence_fingerprint VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN production_ready BOOLEAN DEFAULT false;
