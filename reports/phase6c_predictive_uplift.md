# Phase 6C predictive feature uplift

## Decision

Phase 6C is frozen as `phase6c_predictive_uplift_frozen_v1`. The only retained
central family is **usage/offensive involvement**. It improves the frozen Phase
6B mean MAE from **5.874369** to **5.864892** (0.1613%) and RMSE from 7.515070 to
7.508017 on 22,399 untouched chronological outer rows. Phase 6B artifacts were
not modified, and Phase 7 was not started.

All models remain conditional on playing. Rows with zero/non-positive actual
minutes are excluded; no full-game Fantasy result is fabricated from FP/min.
Only repository data are used, and no individual-defender assignments are
created.

## Protocol and expected usage

The outer folds are E2022 → E2023, E2022–E2023 → E2024, and E2022–E2024 →
E2025. Each model selects iterations inside the available training history and
is then refit on the complete outer-training period. Outer rows are not used for
candidate selection. Families are screened independently; only inner winners
are eligible for combination.

Historical usage is the bounded traditional formula

`100 × (FGA + 0.44×FTA + TOV) × (team player-minutes / 5) /
(MIN × (team FGA + 0.44×team FTA + team TOV))`.

The unbounded value is retained only for audit (9 values exceeded 100 because
of source denominator anomalies); the model target is bounded to [0,100]. For
target season Y, expected usage is trained before Y−1, iteration-selected on
Y−1, refit through Y−1, and predicted for Y. Actual target-game usage is never
a downstream input.

| Expected-usage result | MAE | RMSE | Pearson | Spearman | Rows |
|---|---:|---:|---:|---:|---:|
| Walk-forward CatBoost | 5.9612 | 8.1174 | 0.5117 | 0.5496 | 22,993 |
| Leakage-safe last-five mean | 6.2296 | 8.5675 | 0.4667 | 0.5086 | 22,720 |

Seasonal usage MAE is 6.1757 (E2022), 6.1302 (E2023), 5.9248 (E2024), and
5.8526 (E2025). Despite that forecast accuracy, the expected-next-game-usage
family did not add central FP value beyond the frozen model's minute/role,
quality, form, and opponent inputs (inner MAE 5.8157 versus 5.8148).

## Independent family ablations

The Phase 6C reproduction row is the same Core+Rotation specification retrained
under the Phase 6C nested protocol. Frozen Phase 6B is the immutable comparison.

| Candidate | Inner MAE | Outer MAE | Outer RMSE | Decision |
|---|---:|---:|---:|---|
| Frozen Phase 6B | — | 5.874369 | 7.515070 | Reference |
| Core+Rotation reproduction | 5.814805 | 5.875001 | 7.515387 | Reference only |
| Usage / offensive involvement | **5.803995** | **5.864892** | **7.508017** | Retained |
| Creation | 5.817284 | 5.873282 | 7.511989 | Failed inner gate |
| Expected next-game usage | 5.815680 | 5.879296 | 7.516259 | Failed inner gate |
| Absence role/usage redistribution history | 5.814942 | 5.872350 | 7.514666 | Failed inner gate |
| Pace | 5.817576 | 5.884557 | 7.513168 | Failed inner gate |
| General opponent allowances | 5.815502 | 5.871124 | 7.509094 | Failed inner gate |
| Role-specific opponent matchup | 5.818618 | 5.878568 | 7.513456 | Failed inner gate |
| Recent role changes | 5.816286 | 5.878541 | 7.515961 | Failed inner gate |
| Targeted PBP / coach / rotation | 5.811081 | 5.874075 | 7.512451 | Inner winner, not final |
| Shot/profile | 5.820894 | 5.879360 | 7.513931 | Failed inner gate |
| Usage + targeted PBP/coach/rotation | 5.811927 | 5.875147 | 7.514013 | Worse than usage alone |

The PBP/coach/rotation family was the only other independent inner winner. Its
combination with usage lost the usage-only gain, so no PBP, on/off, lineup,
coach, or rotation-depth addition is deployed. Previously calculated but unused
`role_change_score`, on/off, teammate-lineup, player-shot, opponent-shot, and
shot-interaction fields were audited; none survived the final selection.

## Retained signal

The strongest new retained features by full-fit CatBoost importance were:

| Feature | Importance |
|---|---:|
| `p6c_2pa_share_last5` | 3.1568 |
| `p6c_fga_share_season_before` | 1.6587 |
| `p6c_fta_share_last5` | 1.6217 |
| `p6c_turnover_share_last5` | 1.5181 |
| `p6c_fouls_drawn_share_last5` | 1.4278 |
| `p6c_dreb_share_last5` | 0.9226 |

Thus the improvement is not attributable to USG% alone. The retained family
also contains leakage-safe rolling USG%, 2PA/3PA/FGA/FTA shares, points and
rebound shares, offensive/defensive rebound involvement, turnover involvement,
fouls drawn, and scoring-composition shares. CatBoost learns nonlinear
interactions with the existing player, position, production-rate, minutes, and
form profile.

By pre-game archetype, MAE improvement was +0.0224 for cold-start players
(n=335), +0.0202 for creators (n=713), +0.0093 for balanced players
(n=16,698), +0.0089 for scorers (n=4,542), and −0.0516 for rebounders (n=111).
The small rebounder sample is negative; the data do not support an assumption
that increased usage helps every archetype or mainly helps guards.

The separate Phase 5B known-absence diagnostic has 15,019 rows. Missing-minute
and missing-creation correlations with usage surprise are −0.0086 and −0.0139,
respectively. Current known-absence context therefore remains handled by the
frozen Phase 5B minute/location adjustment; lagged absence-response features
were not retained in the Phase 6C central model.

## Central and ranking evaluation

| Fold | Phase 6B MAE | Phase 6C MAE | Phase 6B RMSE | Phase 6C RMSE |
|---|---:|---:|---:|---:|
| E2022 → E2023 | 5.781863 | 5.781729 | 7.401628 | 7.396177 |
| E2022–E2023 → E2024 | 5.860119 | 5.840247 | 7.472169 | 7.453651 |
| E2022–E2024 → E2025 | 5.961778 | 5.953060 | 7.641004 | 7.641748 |
| Pooled | 5.874369 | **5.864892** | 7.515070 | **7.508017** |

Pooled actual-minus-prediction bias changes from +0.0831 to +0.1312, Pearson
from 0.5105 to 0.5120, and Spearman from 0.5030 to 0.5040. MAE improves in all
three folds; RMSE improves in two and worsens by 0.00074 in E2025.

Across 1,061 games, mean within-game Spearman improves from 0.4906 to 0.4916,
top-one hit rate from 23.09% to 23.37%, and top-three recall from 39.87% to
40.40%.

## Distribution and game-specific event timing

The retained central delta shifts the frozen Phase 6B distribution; Phase 6B's
models and original predictions remain preserved.

| Quantile | Phase 6B pinball | Phase 6C pinball | Phase 6B coverage | Phase 6C coverage |
|---|---:|---:|---:|---:|
| P10 | 1.087705 | 1.092768 | 0.09960 | 0.09974 |
| P25 | 2.142625 | 2.146447 | 0.24849 | 0.24635 |
| P50 | 2.929096 | 2.932440 | 0.49560 | 0.49252 |
| P75 | 2.507503 | 2.507731 | 0.74646 | 0.74302 |
| P90 | 1.468471 | **1.467212** | 0.89598 | 0.89526 |
| P95 | 0.891762 | **0.891231** | 0.94759 | 0.94665 |

P90/P95 pinball improves slightly, while their coverage errors become slightly
larger. Lower quantile pinball does not improve.

Upside timing is mixed rather than a uniform calibration win. PR-AUC improves
for 20+ (0.3127→0.3140), 25+ (0.1913→0.1936), 30+ (0.0945→0.1010), and 35+
(0.0390→0.0459), but not 40+ (0.0113→0.0110). Brier/log-loss changes are tiny
and mixed across thresholds. The model is modestly better at identifying when
20–35 FP upside occurs, but there is no supported improvement for the rare 40+
tail.

Direct, inner-Platt-calibrated downside classifiers improve every requested
threshold over probabilities derived from the frozen Phase 6B quantiles:

| Event | Phase 6B Brier / log loss | Phase 6C Brier / log loss | ROC-AUC change | PR-AUC change |
|---|---|---|---:|---:|
| FP ≤ 5 | 0.19869 / 0.58196 | **0.19819 / 0.58079** | 0.7446→0.7451 | 0.6511→0.6519 |
| FP ≤ 10 | 0.19109 / 0.56314 | **0.18945 / 0.55883** | 0.7575→0.7605 | 0.8303→0.8325 |
| FP ≤ 15 | 0.14647 / 0.45243 | **0.14505 / 0.44782** | 0.7706→0.7763 | 0.9140→0.9162 |

Rare-explosion misses did not improve: among 51 outcomes of 40+, mean P95
shortfall changes from 12.9479 to 12.9529; P95 pinball on the 180 outcomes of
35+ changes from 8.6216 to 8.6445.

## Frozen and live artifacts

The new bundle is under `data/derived/phase6c/frozen_predictive_uplift/` with
bundle fingerprint
`bb054ab561a54430113bf53f8c38aff6dbc0f5ffc73269b1ab35a819a5cca031`.
It contains the expected-usage regressor, retained central model, direct
FP≤5/10/15 classifiers, calibration parameters, exact feature manifests,
protocol, training/evaluation fingerprints, and code/file hashes.

`predict_live` fails closed on any Phase 6C artifact/hash mismatch. It preserves
Phase 4B and Phase 6B central provenance, publishes Phase 6C `expected_fp`,
P10–P95, 20+/25+/30+/35+/40+, FP≤5/10/15, and expected next-game usage, and
stores separate Phase 6C version/calibration/artifact/feature-manifest fields.

Final verification is **245/245 tests**, **68/68 integrity checks**, and a clean
full-tree bytecode compilation. Thirteen Phase 6C tests cover formula/manifest
semantics, target and source cutoffs, chronological fold audits, expected-usage
OOF reporting, deterministic inference, probability order/range, corruption and
missing-artifact failure, and `predict_live` preflight behavior.
