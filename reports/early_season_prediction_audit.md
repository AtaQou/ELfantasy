# Early-season prediction accuracy audit

## Executive conclusion

**Yes, the frozen system has a measurable cold-start problem, but it is concentrated in minutes at the season opener rather than being a broad, persistent Fantasy-points failure.** Pooled Matchday 1 minutes MAE is **5.64**, versus **4.69** through Matchdays 1–5 and **4.57** over the frozen minutes model's full outer evaluation. Rows with zero current-season games have **5.66 minutes MAE**, about 1.04 minutes (23%) worse than rows with 6+ games.

For returning players on MD1, the model (5.65 MAE) does not beat previous-season MPG (5.59). It does beat all simple priors by MD1–3 and MD1–5. The MD1 predictions are visibly compressed: their standard deviation is 4.25 minutes versus 5.86 for previous-season MPG and 8.21 for actual MD1 minutes. Prior 28+ MPG players are pulled down 4.77 minutes on average and underpredicted by 1.37; prior sub-14 MPG players are lifted 2.37 and overpredicted by 0.88. Some shrinkage is justified because star minutes actually fell 3.41 on average, but the model shrinks them about 1.37 minutes too far.

FP error is related to minutes error, but minutes are not the sole or primary explanation. Across MD1–5, the two signed errors have Pearson correlation **0.528** (simple bivariate R² **27.8%**). FP MAE rises from **5.39** when minutes are within ±3 to **8.49** when minutes miss by more than 10, yet substantial FP error remains even with accurate minutes. High-minute stars have good early minutes MAE (4.28) but poor FP MAE (7.77), reinforcing that per-minute production/outcome volatility also matters.

## Scope and leakage controls

- Predictions are existing, stored outer-fold forecasts; nothing was fitted or retrained. Minutes use frozen `p4b__minutes__catboost__role_older_history`; FP uses frozen `phase6c_predictive_uplift_frozen_v1` output `phase6c_expected_fp`.
- Eligible held-out seasons are E2023, E2024, and E2025: E2023 was trained from E2022, E2024 from E2022–E2023, and E2025 from E2022–E2024.
- The audit contains 22,399 outer rows and 2,772 rows in MD1–5. It is conditional on participation: every evaluated row has actual minutes >0. It does not assess DNP/participation prediction, which the project deliberately does not model.
- `round_number` is used as Matchday. In all 19,476 rows with a stored Fantasy Matchday mapping, Matchday equals round; round retains eligible players without a market mapping.
- All stored feature cutoffs are at or before game time (zero violations). Previous-season MPG/team/last-five context uses only season *t−1* games. Positive bias below means overprediction; negative bias means underprediction.
- The stored outer population starts at one prior EuroLeague game, so a true first-career-game test is not available. Low-history players with 1–4 prior EL games are reported as the closest available diagnostic.

## 1. Early-season minutes accuracy

| Season | Window | N | MAE | RMSE | Bias | Median error | Within ±3 | Within ±5 | Error >10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E2023 | MD1 | 155 | 6.41 | 7.90 | -1.74 | -1.75 | 29.0% | 43.9% | 23.2% |
| E2023 | MD1–3 | 530 | 5.14 | 6.46 | -0.69 | -0.94 | 36.6% | 55.7% | 11.9% |
| E2023 | MD1–5 | 910 | 4.98 | 6.31 | -0.65 | -0.87 | 37.7% | 58.4% | 10.9% |
| E2024 | MD1 | 139 | 5.08 | 6.56 | -0.42 | -1.16 | 40.3% | 61.2% | 11.5% |
| E2024 | MD1–3 | 506 | 4.73 | 6.08 | +0.23 | +0.13 | 41.7% | 61.5% | 9.5% |
| E2024 | MD1–5 | 873 | 4.50 | 5.80 | -0.09 | -0.20 | 43.6% | 63.1% | 8.7% |
| E2025 | MD1 | 151 | 5.38 | 6.71 | +0.29 | -0.39 | 32.5% | 55.0% | 12.6% |
| E2025 | MD1–3 | 563 | 4.63 | 5.83 | -0.04 | -0.38 | 39.6% | 62.3% | 8.9% |
| E2025 | MD1–5 | 989 | 4.59 | 5.80 | -0.34 | -0.59 | 39.8% | 62.3% | 8.7% |
| **Pooled** | **MD1** | **445** | **5.64** | **7.10** | **-0.64** | **-1.11** | **33.7%** | **53.0%** | **16.0%** |
| **Pooled** | **MD1–3** | **1,599** | **4.83** | **6.12** | **-0.17** | **-0.41** | **39.3%** | **59.8%** | **10.1%** |
| **Pooled** | **MD1–5** | **2,772** | **4.69** | **5.97** | **-0.36** | **-0.56** | **40.3%** | **61.3%** | **9.4%** |

The effect is largest in E2023 MD1, but all three openers are worse than their MD1–5 aggregates. Errors improve sharply after MD1 and then flatten.

## 2. Returning-player role test

These are pooled MD1 means across every player with at least one appearance in the immediately preceding season.

| Previous-season role | N | Previous MPG | Predicted MD1 | Actual MD1 | Predicted − previous | Bias | MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Star / heavy role (28+) | 28 | 29.83 | 25.06 | 26.42 | -4.77 | -1.37 | 4.61 |
| Starter (22–<28) | 134 | 24.60 | 22.01 | 23.09 | -2.59 | -1.08 | 5.14 |
| Rotation (14–<22) | 187 | 17.81 | 17.57 | 18.27 | -0.23 | -0.70 | 5.77 |
| Bench (<14) | 70 | 10.73 | 13.10 | 12.22 | +2.37 | +0.88 | 6.71 |

The regression slope of predicted MD1 minutes on previous-season MPG is **0.637**, compared with **0.750** for actual MD1 minutes on previous MPG. Predictions preserve ordering well (correlation 0.880) but compress the scale too aggressively. This is systematic role regression, not merely a few star outliers.

## 3. Frozen model versus simple minutes priors

Returning players only; all comparator inputs are pregame. The existing minutes EWMA is the project's leakage-safe career-recency feature and updates only after completed games.

| Window | N | Frozen model MAE | Previous-season MPG MAE | Previous-season last5 MAE | Pregame EWMA MAE |
| --- | ---: | ---: | ---: | ---: | ---: |
| MD1 | 419 | 5.65 | **5.59** | 5.91 | 6.13 |
| MD1–3 | 1,309 | **4.86** | 5.37 | 5.80 | 5.22 |
| MD1–5 | 2,185 | **4.70** | 5.33 | 5.77 | 4.93 |

| MD1 season | N | Frozen model | Previous MPG | Previous last5 | Pregame EWMA |
| --- | ---: | ---: | ---: | ---: | ---: |
| E2023 | 152 | 6.32 | **5.99** | 6.25 | 6.53 |
| E2024 | 136 | **5.11** | 5.53 | 5.77 | 5.82 |
| E2025 | 131 | 5.44 | **5.18** | 5.67 | 6.00 |

The model is not better than the simplest previous-season average at the opener overall (it trails by 0.06 minutes and loses in two of three seasons). Once one or two current-season observations are available it becomes clearly useful: by MD1–3 it beats previous MPG by 0.50 and EWMA by 0.35; by MD1–5 the gains are 0.64 and 0.23.

## 4. Early-season Fantasy Points accuracy

| Season | Window | N | MAE | RMSE | Bias | Pearson | Spearman |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E2023 | MD1 | 155 | 6.56 | 8.40 | -1.88 | 0.340 | 0.343 |
| E2023 | MD1–3 | 530 | 6.15 | 7.72 | -0.53 | 0.411 | 0.376 |
| E2023 | MD1–5 | 910 | 6.02 | 7.56 | -0.48 | 0.450 | 0.425 |
| E2024 | MD1 | 139 | 5.51 | 7.35 | -1.37 | 0.454 | 0.506 |
| E2024 | MD1–3 | 506 | 5.69 | 7.16 | -0.18 | 0.480 | 0.470 |
| E2024 | MD1–5 | 873 | 5.82 | 7.34 | -0.35 | 0.510 | 0.485 |
| E2025 | MD1 | 151 | 6.35 | 7.89 | -1.16 | 0.476 | 0.456 |
| E2025 | MD1–3 | 563 | 6.26 | 7.89 | -0.67 | 0.463 | 0.462 |
| E2025 | MD1–5 | 989 | 6.24 | 7.91 | -0.36 | 0.454 | 0.455 |
| **Pooled** | **MD1** | **445** | **6.16** | **7.91** | **-1.48** | **0.421** | **0.430** |
| **Pooled** | **MD1–3** | **1,599** | **6.04** | **7.61** | **-0.47** | **0.453** | **0.438** |
| **Pooled** | **MD1–5** | **2,772** | **6.04** | **7.62** | **-0.40** | **0.471** | **0.455** |

MD1 has a clear underprediction bias, but pooled FP MAE changes little through MD5. Rank correlation improves from 0.430 to 0.455. This is weaker evidence for an FP-specific cold-start problem than for minutes.

## 5. Error by current-season history

Exact, mutually exclusive history bins use all held-out player-games, so the 6+ group can be compared with true early-history rows.

| Current-season history | N | Minutes MAE | Minutes bias | FP MAE | FP bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 games | 581 | **5.66** | -0.60 | 5.80 | -0.96 |
| 1–2 games | 1,565 | 4.62 | -0.20 | 5.77 | +0.03 |
| 3–5 games | 2,476 | 4.62 | -0.61 | **5.63** | -0.14 |
| 6+ games | 17,777 | **4.52** | -0.42 | 5.91 | -0.12 |

The requested cumulative checks are: 0 games (N=581, minutes/FP MAE 5.66/5.80), ≤2 games (N=2,146, 4.90/5.77), and ≤5 games (N=4,622, 4.75/5.70). Minutes improve immediately after the first observation. FP error does not improve monotonically and the 6+ group is not best, so current-season sample count alone does not explain FP accuracy.

## 6. Lightweight cohort checks (MD1–5)

| Group | N | Minutes MAE | Minutes bias | FP MAE | FP bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prior-season stars (28+ MPG) | 147 | 4.28 | +0.20 | 7.77 | -1.10 |
| Low EL history (1–4 prior games) | 434 | 4.60 | -0.44 | 5.88 | -0.57 |
| Returning, changed team | 604 | 4.85 | -0.51 | 6.42 | -0.20 |
| Returning, same team | 1,581 | 4.64 | -0.28 | 5.98 | -0.39 |

Team changers are modestly harder than same-team returners (+0.22 minutes MAE and +0.44 FP MAE). Stars are not harder in minutes after the opener, but their FP outcomes are much harder. Exact new-to-EuroLeague debuts cannot be evaluated because the stored outer prediction population has no rows with zero prior career EL games.

## 7. How much FP error comes from minutes error?

Across 2,772 MD1–5 rows:

- Pearson correlation between signed minutes and FP errors: **0.528**; Spearman: **0.536**.
- A one-minute signed error is associated with **0.67 FP** signed error in a simple univariate regression.
- Simple bivariate R² is **27.8%**. This is shared variation, not a causal decomposition.
- FP MAE is **5.39** when minutes error is within ±3 and **8.49** when minutes error exceeds 10.

Minutes misses materially amplify FP misses, but about 72% of bivariate FP-error variance is not explained by minutes error alone. The separate minutes forecast is not itself an input to the final direct Phase 6C central forecast, so this relationship diagnoses shared outcome/role error rather than a mechanical error pass-through.

## 8. Largest early-season misses (MD1–5)

`Miss` is actual−predicted for underpredictions and predicted−actual for overpredictions. Prior EL games is the point-in-time career count.

### 15 largest minutes underpredictions

| Player | Season | MD | Prev MPG | Pred MIN | Actual MIN | Pred FP | Actual FP | Prior EL games | Miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PETERS, ALEC | E2023 | 1 | 13.2 | 11.5 | 36.8 | 4.2 | 34.1 | 145 | 25.4 |
| ANDJUSIC, DANILO | E2023 | 4 | 13.1 | 11.1 | 34.7 | 3.8 | 10.0 | 59 | 23.6 |
| BOLOMBOY, JOEL | E2023 | 4 | 11.3 | 10.3 | 31.8 | 7.0 | 19.0 | 140 | 21.5 |
| PREPELIC, KLEMEN | E2025 | 4 | — | 11.9 | 31.6 | 4.6 | 20.9 | 83 | 19.7 |
| DORSEY, TYLER | E2025 | 1 | 10.1 | 12.8 | 32.2 | 4.7 | 23.1 | 171 | 19.4 |
| VOIGTMANN, JOHANNES | E2024 | 1 | 20.2 | 16.0 | 33.9 | 7.0 | 25.3 | 182 | 17.9 |
| GRANT, JERIAN | E2023 | 1 | — | 14.9 | 32.5 | 3.1 | 8.0 | 26 | 17.6 |
| WALKUP, THOMAS | E2023 | 1 | 25.1 | 21.3 | 38.8 | 8.2 | 17.6 | 174 | 17.4 |
| RICCI, GIAMPAOLO | E2025 | 5 | 15.1 | 10.6 | 28.0 | 2.5 | 13.2 | 123 | 17.4 |
| CALATHES, NICK | E2023 | 3 | 25.7 | 12.0 | 29.3 | 8.8 | 20.9 | 172 | 17.3 |
| NWORA, JORDAN | E2024 | 3 | — | 13.6 | 30.9 | 4.5 | 17.0 | 2 | 17.2 |
| JARAMAZ, OGNJEN | E2023 | 5 | 15.6 | 8.6 | 25.8 | 3.7 | 12.1 | 64 | 17.2 |
| GIEDRAITIS, DOVYDAS | E2023 | 3 | 9.1 | 4.4 | 21.6 | 1.6 | 9.0 | 18 | 17.2 |
| VOIGTMANN, JOHANNES | E2023 | 1 | 15.7 | 14.8 | 31.8 | 4.8 | 21.0 | 148 | 17.0 |
| SHIELDS, SHAVON | E2023 | 1 | 21.9 | 20.4 | 37.3 | 7.8 | 11.0 | 129 | 16.9 |

### 15 largest minutes overpredictions

| Player | Season | MD | Prev MPG | Pred MIN | Actual MIN | Pred FP | Actual FP | Prior EL games | Miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OSMANI, ERCAN | E2025 | 5 | 16.6 | 24.9 | 2.5 | 13.9 | 6.0 | 66 | 22.4 |
| WILBEKIN, SCOTTIE | E2024 | 1 | 24.7 | 23.9 | 2.1 | 9.9 | 0.0 | 190 | 21.8 |
| MCKISSIC, SHAQUIELLE | E2024 | 2 | 16.3 | 21.3 | 1.8 | 11.8 | 0.0 | 147 | 19.5 |
| BROWN, LORENZO | E2024 | 4 | 29.3 | 22.1 | 3.6 | 13.2 | -1.0 | 164 | 18.5 |
| DIBARTOLOMEO, JOHN | E2023 | 2 | 16.9 | 20.5 | 2.4 | 7.0 | -1.8 | 137 | 18.2 |
| PIERRE, DYSHAWN | E2023 | 2 | 22.8 | 31.0 | 14.5 | 10.4 | 0.0 | 94 | 16.6 |
| SAKO, NEAL | E2025 | 1 | 22.1 | 22.2 | 5.7 | 9.9 | -3.6 | 33 | 16.5 |
| HACKETT, DANIEL | E2024 | 2 | 24.1 | 21.8 | 5.6 | 7.2 | -1.0 | 174 | 16.2 |
| PONITKA, MATEUSZ | E2023 | 1 | 25.2 | 21.0 | 4.9 | 8.5 | 1.0 | 99 | 16.1 |
| TONUT, STEFANO | E2024 | 1 | 17.1 | 20.4 | 4.3 | 5.6 | 0.0 | 53 | 16.1 |
| FORREST, TRENT | E2025 | 4 | 24.9 | 27.0 | 11.2 | 17.3 | 13.0 | 37 | 15.8 |
| MATTISSECK, JONAS | E2024 | 2 | 12.2 | 18.1 | 2.5 | 3.0 | -1.0 | 128 | 15.6 |
| GIFFEY, NIELS | E2023 | 5 | 19.4 | 20.2 | 4.6 | 6.6 | 0.0 | 109 | 15.5 |
| OTURU, DAN | E2024 | 3 | 15.1 | 20.7 | 6.0 | 12.2 | 2.0 | 23 | 14.7 |
| CANAAN, ISAIAH | E2023 | 3 | 16.1 | 22.2 | 7.5 | 7.8 | -3.0 | 65 | 14.7 |

### 15 largest FP underpredictions

| Player | Season | MD | Prev MPG | Pred MIN | Actual MIN | Pred FP | Actual FP | Prior EL games | Miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BLATT, TAMIR | E2024 | 1 | 18.9 | 21.1 | 28.9 | 7.6 | 39.6 | 98 | 32.0 |
| HOLMES, RICHAUN | E2025 | 4 | — | 14.8 | 28.8 | 6.0 | 36.3 | 3 | 30.3 |
| PETERS, ALEC | E2023 | 1 | 13.2 | 11.5 | 36.8 | 4.2 | 34.1 | 145 | 29.9 |
| HERNANGOMEZ, JUANCHO | E2025 | 2 | 27.3 | 21.0 | 35.1 | 9.6 | 39.0 | 72 | 29.4 |
| MIROTIC, NIKOLA | E2023 | 1 | 25.0 | 21.2 | 35.7 | 10.0 | 38.0 | 128 | 28.0 |
| MONEKE, CHIMA | E2023 | 4 | 12.6 | 18.1 | 27.6 | 9.7 | 37.0 | 19 | 27.3 |
| YABUSELE, GUERSCHON | E2023 | 2 | 23.8 | 28.9 | 26.3 | 16.2 | 42.9 | 98 | 26.7 |
| OBST, ANDREAS | E2025 | 2 | 23.9 | 20.0 | 26.2 | 6.5 | 33.0 | 115 | 26.5 |
| SHORTS, TJ | E2024 | 5 | — | 26.9 | 29.1 | 16.1 | 41.8 | 4 | 25.7 |
| MANEK, BRADY | E2023 | 4 | — | 16.4 | 28.0 | 5.0 | 29.7 | 3 | 24.7 |
| MALEDON, THEO | E2024 | 4 | — | 25.4 | 27.7 | 16.4 | 39.6 | 25 | 23.2 |
| NWORA, JORDAN | E2025 | 4 | 17.0 | 23.4 | 32.6 | 14.3 | 37.4 | 40 | 23.1 |
| RATHAN-MAYES, XAVIER | E2025 | 1 | 11.9 | 12.7 | 24.0 | 3.2 | 26.0 | 23 | 22.8 |
| WRIGHT, MOSES | E2025 | 1 | 12.8 | 15.5 | 23.4 | 8.1 | 30.8 | 35 | 22.7 |
| SAKO, NEAL | E2024 | 2 | — | 17.6 | 26.1 | 10.6 | 33.0 | 1 | 22.4 |

### 15 largest FP overpredictions

| Player | Season | MD | Prev MPG | Pred MIN | Actual MIN | Pred FP | Actual FP | Prior EL games | Miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NUNN, KENDRICK | E2025 | 5 | 30.8 | 30.2 | 16.1 | 18.3 | -3.6 | 79 | 21.9 |
| MONEKE, CHIMA | E2024 | 5 | 24.7 | 28.7 | 21.3 | 18.5 | -2.7 | 54 | 21.2 |
| PUNTER, KEVIN | E2025 | 5 | 27.1 | 23.7 | 21.8 | 15.8 | -5.0 | 165 | 20.8 |
| SLOUKAS, KOSTAS | E2025 | 3 | 21.7 | 20.2 | 11.5 | 15.6 | -4.5 | 240 | 20.1 |
| PUNTER, KEVIN | E2023 | 5 | 27.8 | 28.4 | 27.9 | 16.1 | -2.7 | 100 | 18.8 |
| SORKIN, ROMAN | E2025 | 2 | 23.4 | 26.2 | 13.1 | 15.4 | -3.0 | 124 | 18.4 |
| OKOBO, ELIE | E2023 | 2 | 26.1 | 25.6 | 27.6 | 13.2 | -5.0 | 69 | 18.2 |
| HAYES-DAVIS, NIGEL | E2024 | 5 | 31.5 | 27.9 | 23.9 | 14.1 | -4.0 | 178 | 18.1 |
| BALDWIN IV, WADE | E2024 | 3 | 27.6 | 27.7 | 29.3 | 17.7 | 0.0 | 159 | 17.7 |
| EDWARDS, CARSEN | E2023 | 2 | 15.0 | 15.9 | 16.1 | 6.4 | -11.0 | 35 | 17.4 |
| SHIELDS, SHAVON | E2024 | 5 | 30.4 | 28.9 | 32.9 | 16.1 | -1.0 | 162 | 17.1 |
| SHORTS, TJ | E2025 | 1 | 27.3 | 24.7 | 10.6 | 14.6 | -1.8 | 37 | 16.4 |
| GUDURIC, MARKO | E2025 | 4 | 22.0 | 21.1 | 19.9 | 10.4 | -6.0 | 199 | 16.4 |
| MILLER-MCINTYRE, CODI | E2025 | 4 | 25.3 | 29.2 | 30.9 | 14.8 | -0.9 | 77 | 15.7 |
| THOMPSON, DARIUS | E2023 | 1 | 27.0 | 25.7 | 26.3 | 11.7 | -4.0 | 34 | 15.7 |

## 9. Optimizer sanity check

The current Control Center's **Best Overall** choice maximizes the mean simulated final Fantasy score among legal candidate rosters/layouts. The underlying deterministic solver defaults to player `expected_fp` plus coach `expected_score`, with the rules' starter, bench, sixth-man, and captain multipliers. Credits are a hard budget constraint, not part of the score. **It does not maximize FP/credit.** `FP_PER_CREDIT` exists only as an explicit historical comparison strategy.

As a cheap budget diagnostic, the first five E2025 `DETERMINISTIC_EXPECTED` historical optimizations left 0.1, 0.0, 1.6, 0.6, and 0.5 credits unused: **0.56 credits on average**. Available capital varied from 100.0 to 105.4 because the replay carries roster value and bank forward.

## 10. Main conclusions

1. **The main cold-start weakness is minutes at zero current-season history, especially MD1.** MAE drops by about one minute after initial current-season evidence appears.
2. **The minutes model over-compresses established roles on MD1.** It pulls stars down and bench players up more than actual opener roles warrant.
3. **Previous-season MPG is the better MD1 default on this sample, but the trained model becomes better quickly.** This is an opener-specific diagnosis, not a general rejection of the minutes model.
4. **FP has opener underprediction bias but little MAE recovery through MD5.** Current-season history depth is not a monotonic FP-error driver.
5. **Minutes error is important, not dominant.** Large minutes misses sharply worsen FP error, but only 27.8% of signed FP-error variance is shared with signed minutes error.
6. **Team changes add modest difficulty; stars' main early issue is FP rather than minutes.** Exact first-career EL debuts remain outside the stored evaluation population.

No model, feature, optimizer logic, frozen artifact, or database content was changed.
