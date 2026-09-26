# Phase 6B final distribution

## Frozen architecture

`phase6b_probabilistic_player_outcome_frozen_v1` is a conditional-on-playing,
Core+Rotation CatBoost system:

1. RMSE CatBoost estimates the conditional mean `expected_fp`.
2. Six direct pinball CatBoost models estimate P10/P25/P50/P75/P90/P95.
3. Inner-chronological finite-sample role calibration adjusts each quantile.
4. Four CatBoost classifiers plus inner-chronological Platt calibration estimate
   20+/25+/30+/35+; 40+ is derived because direct support is insufficient.
5. Increasing quantile rearrangement, decreasing probability PAVA, and
   quantile-implied survival bounds create one coherent public distribution.

The chronological outer weighted interval score is 12.97164. The frozen Phase
4B output remains `phase4b_central_fp`; its historical interval remains a legacy
uncertainty field. User-facing `ceiling` is defined, if that alias is used at
all, as calibrated P90. P95 remains an explicitly rarer upside diagnostic.

At live inference, Phase 5B's frozen availability-driven FP location delta is
added to the new mean and quantiles and shifts the calibrated probability curve.
It does not alter Phase 4B's direct 25% component, retrain Phase 5B, or use
target-game teammate outcomes. Team minutes remain reconciled to 200 before the
location shift is calculated.

## Phase 7 output contract

The immutable player row exposes:

```text
phase4b_central_fp
expected_fp
median_fp
p10_fp p25_fp p50_fp p75_fp p90_fp p95_fp
prob_fp_ge_20 prob_fp_ge_25 prob_fp_ge_30 prob_fp_ge_35 prob_fp_ge_40
distribution_width p90_minus_p50 p50_minus_p10 p95_minus_expected
history_sample_count distribution_confidence cold_start_flag
probabilistic_model_version calibration_version
probabilistic_artifact_fingerprint
```

OUT and otherwise unscorable rows have no active conditional performance
distribution. Cold starts use the tree models' missing-value handling and are
labelled LOW confidence for fewer than five prior EuroLeague games; no personal
calibration claim is made from sparse history.

## Reproducibility and deployment

The verified data fingerprint is
`c7039b0a7768303880e38e3d191106206c2a0e1479fc6229c0555090e7f7ad41`;
the bundle fingerprint is
`0a3754ccabd080065c38ac17215c0f3e44573d66e1efc33e96542297b456450f`;
the code/protocol fingerprint is
`de543ad6da06e7bf94cae166cea201d3907dfea8f2b320a203c60cfdd6094503`.
The loader validates every model hash, scoring target, feature order, protocol,
calibration, DNP-exclusion flag, and outer gate and fails closed on mismatch.

`python -m scripts.predict_live` loads this bundle after the unchanged Phase 4B
and Phase 5B bundles. JSON, CSV, and database snapshots store the complete
pre-game distribution immutably; later outcomes attach separately. The absent
genuine E2026 market/roster and `SCORING_RULES_UNVERIFIED` state remain live
production gates.

## Known limitations

1. High-expected-FP rows still under-cover at P90/P95 (86.69%/92.80%).
2. Only 65 primary 40+ cases exist, so 40+ is derived rather than directly fit.
3. Several rare explosions remain far above P95.
4. Very low positive-minute outcomes are not historically labelled by cause.
5. E2026 scoring, market, roster, and prospective calibration are not yet verified.

No Fantasy roster, captain, substitution, credit, simulation, or strategy logic
is part of Phase 6B.
