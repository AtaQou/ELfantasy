# Phase 9A — Stat-Line & Game-Environment Challenger

Status: **PHASE6C_RETAINED**. Predictive winner: `phase6c_predictive_uplift_frozen_v1`. Phase 7 was not retrained.

## Decision

The architecture selected exclusively on chronological inner validation was `E_phase6c_environment_statline_ensemble`. The strict outer freeze gate did not pass.

| Architecture | MAE | RMSE | Pearson | Spearman | CRPS |
|---|---:|---:|---:|---:|---:|
| A_phase6c_frozen | 5.864892 | 7.508017 | 0.511996 | 0.503992 | 4.135178 |
| B_statline | 5.956109 | 7.649578 | 0.484593 | 0.482601 | 4.180572 |
| C_environment_statline | 5.946729 | 7.636558 | 0.487026 | 0.485273 | 4.172820 |
| D_phase6c_statline_ensemble | 5.894435 | 7.547106 | 0.504852 | 0.499461 | 4.126587 |
| E_phase6c_environment_statline_ensemble | 5.891498 | 7.545679 | 0.505064 | 0.499787 | 4.124620 |

## Game environment

Predictable versus the strictly trailing team/opponent baseline: game_possessions, opponent_points, team_assists, team_fga, team_fta, team_points, team_three_pa, team_turnovers, team_two_pa, three_point_attempt_rate.

Not predictably improved: assisted_field_goal_rate, creation_concentration, team_defensive_rebounds_available, team_fouls_drawn, team_offensive_rebounds.

The evaluated PBP families were transition/after-turnover flags, second-chance flags, and validated shot-zone mix. True early-clock timing, possession length, named tactics, and defender assignments were not invented.

## Minutes and stat components

Existing Phase 4B minutes MAE: 4.567869. Challenger minutes MAE: 4.558146.

Largest Fantasy-error bottlenecks: two_points_made, three_points_made, free_throws_made, two_points_attempted, free_throws_attempted.

## Safety and compatibility

Phase 7 interface compatibility: True. Frozen Phase 6C/7 artifacts untouched: True. No planner work was started.

## Gate detail

```json
{
  "checks": {
    "all_outer_fold_mae_wins": false,
    "bias_guard": true,
    "challenger_selected": true,
    "pooled_crps_not_degraded": true,
    "pooled_mae_improved": false,
    "pooled_rmse_not_degraded": false
  },
  "fold_mae_wins": {
    "outer_e2023": false,
    "outer_e2024": false,
    "outer_e2025": false
  },
  "passed": false,
  "rule": "architecture selected only on chronological inner predictions; freeze requires pooled MAE improvement, no RMSE/CRPS degradation, |bias|<=0.25, and an MAE win in every outer season",
  "selected_architecture": "E_phase6c_environment_statline_ensemble"
}
```

Selected-versus-baseline pooled central metrics:

```json
{
  "inner_selected": {
    "bias_actual_minus_prediction": 0.14832272891406992,
    "mae": 5.891497819865333,
    "pearson": 0.5050644443939449,
    "rmse": 7.545678990212851,
    "rows": 22399,
    "spearman": 0.49978688329893767
  },
  "phase6c": {
    "bias_actual_minus_prediction": 0.1311763178064753,
    "mae": 5.864892157734531,
    "pearson": 0.5119957350530728,
    "rmse": 7.508017249328457,
    "rows": 22399,
    "spearman": 0.5039916659894864
  }
}
```
