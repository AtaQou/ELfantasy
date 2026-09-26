# Phase 4A feature ablations

All controlled ablations use the same 22,399 genuine E2023-E2025 outer rows,
identical target semantics, chronological folds, evaluation code, seed 17, and
CatBoost fold settings. Only the manifest changes.

## CatBoost family ablations

| Feature set | Features | MAE | Delta MAE vs CORE | RMSE | Spearman | Top10/20 | Top20/30 |
|---|---:|---:|---:|---:|---:|---:|---:|
| CORE | 106 | 5.8303 | — | 7.5881 | 0.5016 | 48.06% | 54.13% |
| CORE + ROTATION | 132 | **5.8272** | **-0.0032** | 7.5874 | 0.5021 | **48.55%** | 53.77% |
| CORE + ON/OFF | 124 | 5.8345 | +0.0041 | 7.5938 | 0.5001 | 48.31% | 53.44% |
| CORE + LINEUP/TEAMMATE | 118 | 5.8310 | +0.0006 | **7.5858** | 0.5012 | 47.66% | 53.93% |
| CORE + PLAYER SHOT | 139 | 5.8289 | -0.0015 | 7.5877 | **0.5022** | 47.58% | **54.21%** |
| CORE + OPPONENT SHOT | 130 | 5.8347 | +0.0044 | 7.5960 | 0.5009 | 47.58% | 53.48% |
| CORE + ROTATION + ON/OFF | 150 | 5.8360 | +0.0056 | 7.5956 | 0.5009 | 47.42% | 53.16% |
| CORE + SHOT ALL | 166 | 5.8332 | +0.0028 | 7.5885 | 0.5014 | 48.23% | 53.60% |
| CORE + ALL RICH | 222 | 5.8368 | +0.0065 | 7.6002 | 0.5013 | 47.98% | 53.48% |

Rotation is the largest MAE improvement, only 0.054% versus CatBoost CORE.
Player-shot profile is second at 0.025%. These effects are negligible in
practical magnitude even though rotation helps consistently enough to win the
predeclared MAE hierarchy. Lineup/teammate is neutral on MAE but has the best
ablation RMSE. On/off, opponent shots, combined shots, Rotation + On/off, and All
Rich are negative on MAE.

## Core versus All Rich by model

| Model | CORE MAE | ALL RICH MAE | Delta | Conclusion |
|---|---:|---:|---:|---|
| Ridge | 5.8935 | 5.9095 | +0.0161 | worse |
| CatBoost | 5.8303 | 5.8368 | +0.0065 | worse |
| XGBoost | 5.8848 | 5.8859 | +0.0011 | neutral/worse |
| LightGBM | 5.8504 | 5.8508 | +0.0003 | neutral/worse |

All-Rich fails the main incremental-value test for every model. Missing-value
support is not enough to make every rich field useful simultaneously, and Phase
4A does not retrospectively alter Phase 3 features to improve this outcome.

## Identity

CatBoost ALL RICH with `player_id` has MAE 5.8368; removing only `player_id`
gives 5.8378. Established-player MAE changes from 5.8966 to 5.8952 (slightly
better without identity); low-history MAE changes from 5.1800 to 5.2071 (slightly
worse). Identity adds negligible aggregate value and does not materially harm
low-history generalization in this sample. There are no true zero-history outer
rows, so a strict unseen-player conclusion is not available.

## Training-derived importance for the winner

Normalized CatBoost importance is 81.55% core, 14.26% rotation, 2.68% identity
context, and 1.50% structural categoricals. The leading raw fields are:

1. `season_fp_avg_before` (0.0905)
2. `career_el_fp_avg_before_3s` (0.0533)
3. `last_10_fp_avg` (0.0446)
4. `season_minutes_avg_before` (0.0228)
5. `career_el_fp_avg_before_8s` (0.0218)
6. `career_el_fp_avg_before_5s` (0.0214)
7. `fta_per_min` (0.0209)
8. `career_el_fp_avg_before` (0.0183)
9. `rot_season_minutes_share_before` (0.0182)
10. `rot_minutes_ewma` (0.0180)

Importance rank stability is moderate rather than high: fold-pair Spearman is
0.533-0.674. Importance is associative, training-derived, and not causal.

## Phase 4B recommendation

Test the following first, in order:

1. minutes/role decomposition, because rotation is the only MAE-positive rich
   family and high-minute errors remain largest;
2. player shot profile as the only other individually MAE-positive family;
3. lineup/teammate stability as a targeted RMSE/variance signal, without the
   noisy full on/off bundle.

Do not carry all rich families forward by default. Preserve them as controlled
ablation manifests.
