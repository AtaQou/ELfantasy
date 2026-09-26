ALTER TABLE live_prediction_runs
ADD COLUMN predictive_model_version VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN predictive_calibration_version VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN predictive_artifact_fingerprint VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN predictive_feature_manifest_fingerprint VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN phase6b_expected_fp_before_adjustment DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN phase6b_expected_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN phase6c_expected_fp_before_adjustment DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN phase6c_expected_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN phase6c_location_delta DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN expected_usage_next_game DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_le_5 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_le_10 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_le_15 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN predictive_model_version VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN predictive_calibration_version VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN predictive_artifact_fingerprint VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN predictive_feature_manifest_fingerprint VARCHAR;
