# Phase 8B — Live Decision Advisor, Shadow Validation & Price Intelligence

Phase 8B extends the Phase 8A Control Center without changing or retraining the
frozen Phase 4B/5B/6B/6C/7 artifacts. It does not implement multi-Matchday
optimization.

## Live/shadow decision path

- A successful pre-lock optimization now writes one immutable, fully self-contained
  snapshot: market/prediction/rules fingerprints, current team, transfers, overrides,
  constraints, prediction/coach pools, recommendations, alternatives, roles, captain,
  Turn allocation, and Monte Carlo configuration.
- Refresh attaches outcomes in a separate append-only table. A Turn becomes observable
  only when every game represented in that roster Turn is marked played. Missing player
  target rows in a completed game are explicitly represented as a Fantasy DNP zero; no
  future game row is attached.
- The after-Turn advisor calls the frozen Phase 7 legal recourse policy. It freezes the
  roster, sixth man, and played-player eligibility, while re-enumerating all legal
  formations. Therefore cross-position effects such as played Guard out / future
  Forward in are legal when the resulting five is legal.
- KEEP and SWITCH share the same Phase 6C inverse-CDF Monte Carlo samples. Evidence
  includes marginal P(new player > realized score), paired final-team KEEP/SWITCH
  distributions, captain keep/switch comparisons, bench effects, and remaining Turn
  optionality. No 50% cutoff or risk bonus is introduced.

## Prospective validation and monitoring

Completed Matchdays accumulate conditional-on-playing player MAE/RMSE, expected-minutes
MAE, P10/P50/P90/P95 coverage, upside/downside Brier calibration, expected-vs-realized
team score, dynamic recourse score, captain/switch actions, transfers, and evaluation-only
hindsight regret. Original recommendations are never rewritten.

Monitoring is read-only. It reports transfer use/saving, credits and concentration,
FP/credit, Turn allocation, captain and roster width, switch/bench/sixth-man behavior,
manual constraints/overrides, recommendation reversals, and price shadow error. Five-round
behavior alerts compare only against earlier stored behavior and have no optimizer action.

## E2025 price audit

Only validated E2025 point-in-time market history was used: 11,445 consecutive-player
rows from Matchdays 1–37. Three chronological folds compare the one-step model with
no-change.

| Model | MAE | RMSE | RMSE fold wins vs no-change |
|---|---:|---:|---:|
| No price change | 0.1638 | 0.2455 | — |
| Ridge pregame model | 0.1654 | 0.2407 | 3/3 |

The model is frozen separately as `phase8b_e2025_next_price_ridge_v1` with status
`ACCEPTED_SECONDARY_RMSE_ONLY` and low confidence. It improves RMSE by 1.99%, but does
not improve aggregate MAE and wins MAE in only 1/3 folds. Increase/decrease Brier scores
are 0.1619 and 0.2307. Current credits, pregame expected FP, expected minutes, prior
realized FP, and the latest credit delta are the strongest inputs.

The UI therefore exposes expected next price, delta, and direction probabilities only
as a separate Value/Credit Growth diagnostic. A player must also have at least median
current FP/credit to qualify for that list. Price information is not passed into the
Phase 7 objective. No two- or three-Matchday price extrapolation is released.

## UI/API additions

- Strategy: `Update Turn Results / Re-evaluate Strategy`, action evidence, captain
  comparison, switch probabilities, and Turn portfolio.
- Players: Value/Credit Growth view and a Performance / Value / Strategy detail panel.
- Monitoring: read-only accumulated validation, behavior metrics, diagnostics, and
  price-shadow accuracy.
- APIs: `/api/reevaluate`, `/api/advisor/latest`, `/api/shadow/latest`,
  `/api/shadow/evaluate`, `/api/monitoring`, and `/api/player/{player_id}`.

The existing launch command remains:

```bash
python -m scripts.control_center
```

Current E2026 optimization still fails closed until market, scoring, and rules gates pass.

