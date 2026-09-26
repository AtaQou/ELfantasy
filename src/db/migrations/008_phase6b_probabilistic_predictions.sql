ALTER TABLE live_prediction_runs
ADD COLUMN probabilistic_model_version VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN calibration_version VARCHAR;

ALTER TABLE live_prediction_runs
ADD COLUMN probabilistic_artifact_fingerprint VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN phase4b_central_fp_before_adjustment DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN phase4b_central_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN median_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p10_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p25_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p50_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p75_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p90_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p95_fp DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_ge_20 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_ge_25 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_ge_30 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_ge_35 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN prob_fp_ge_40 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN distribution_width DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p90_minus_p50 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p50_minus_p10 DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN p95_minus_expected DOUBLE;

ALTER TABLE live_player_predictions
ADD COLUMN history_sample_count INTEGER;

ALTER TABLE live_player_predictions
ADD COLUMN distribution_confidence VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN probabilistic_model_version VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN calibration_version VARCHAR;

ALTER TABLE live_player_predictions
ADD COLUMN probabilistic_artifact_fingerprint VARCHAR;
