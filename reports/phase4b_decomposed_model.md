# Phase 4B decomposed Fantasy model

## FP/min target and stabilization

Raw production is `actual_fantasy_points / actual_minutes`; the denominator must be finite and strictly positive. Minute-weighted modelling uses `actual_minutes / training_mean_minutes` as the training weight. The stabilized alternative is `(FP + k × prior_rate) / (minutes + k)`, where `prior_rate = sum(training FP) / sum(training minutes)` and `k ∈ {5,10}` is selected on inner validation. Every prior is refitted inside the historical fold. No FP/min value is capped.

Actual target-game minutes define the historical production label only. They are not inference features. The production model does not consume minutes, so there is no actual-minutes/predicted-minutes train/inference mismatch; decomposition multiplies two independently out-of-sample outputs.

## Production candidates

| Candidate | Strategy | Inner decomp MAE | Rate MAE | Decomp MAE | Decomp RMSE |
|---|---|---|---|---|---|
| p4b__production__ridge__raw | raw | 5.7671 | 0.3032 | 5.8340 | 7.6084 |
| p4b__production__xgboost__raw | raw | 5.7434 | 0.3040 | 5.8416 | 7.6262 |
| p4b__production__catboost__raw | raw | 5.7459 | 0.3020 | 5.8120 | 7.5744 |
| p4b__production__catboost__weighted | weighted | 5.7454 | 0.3023 | 5.8168 | 7.5879 |
| p4b__production__catboost__stabilized | stabilized | 5.7857 | 0.3055 | 5.8534 | 7.5688 |
| p4b__production__catboost__weighted_shot | weighted | 5.7454 | 0.3023 | 5.8233 | 7.5908 |
| p4b__production__catboost__weighted_stage | weighted | 5.7413 | 0.3025 | 5.8230 | 7.5957 |
| p4b__production__catboost__weighted_older_history | weighted | 5.7281 | 0.3017 | 5.8130 | 7.5903 |

Selected from inner validation: `p4b__production__catboost__weighted_older_history`.

## Direct versus decomposed

| Architecture | MAE | RMSE | Spearman | Pearson | Bias | Max prediction |
|---|---|---|---|---|---|---|
| Frozen direct | 5.8272 | 7.5874 | 0.5021 | 0.5088 | 0.9907 | 22.0057 |
| Selected decomposed | 5.8130 | 7.5903 | 0.5057 | 0.5126 | 1.1505 | 23.3348 |

Direct/decomposed genuine outer residual Pearson correlation: **0.9931**.

## ORACLE / DIAGNOSTIC ONLY

These rows are not predictive models and are excluded from leaderboards.

| Diagnostic | MAE | RMSE | Meaning |
|---|---|---|---|
| Actual minutes × predicted rate | 4.9986 | 6.6344 | error remaining with perfect minutes |
| Predicted minutes × actual rate | 2.2460 | 6.1641 | error remaining with perfect production |

Minutes-error/FP-error Pearson: 0.5350; production-rate-error/FP-error Pearson: 0.5333.

## Targeted ablations

Shot profile: `p4b__production__catboost__weighted_shot` changed the measured metric from 5.8168 to 5.8233 (delta +0.0065).
Older standardized history for production: `p4b__production__catboost__weighted_older_history` changed the measured metric from 5.8168 to 5.8130 (delta -0.0038).
