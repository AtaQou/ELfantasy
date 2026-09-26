# Phase 7 — Dynamic EuroLeague Fantasy Strategy Engine

## Decision

Phase 7 is implemented as a separate strategy layer over the unchanged frozen
`phase6c_predictive_uplift_frozen_v1` player distributions. It does not retrain
Phase 4B, 5B, 6B, or 6C, ingest another dataset, or simulate possessions. The
strategy artifact is `phase7_dynamic_strategy_engine_frozen_v1`.

## Rules audit: `elfc_classic_e2025_v1`

The versioned source-of-truth is
`data/reference/fantasy_rules/elfc_classic_e2025_v1.json`.

- 100 credits; 10 players plus one head coach.
- Exactly 4 guards, 4 forwards, and 2 centers.
- Starting five formations in guard-forward-center order: 2-2-1, 1-2-2,
  2-1-2, 1-3-1, or 3-1-1.
- One designated sixth man scores 100%; the other four bench players score 50%.
- Captain must be a starter and scores 2x.
- At most six players from one EuroLeague club; the coach is not counted as a
  player for this limit.
- A Turn is the block of games on one local calendar day. Between Turns, field
  and bench may change, formation may change, and captain may change, but an
  incoming field player or new captain must not have played.
- Four entities may be traded between Regular Season rounds, including a coach
  trade. The verified unlimited windows are after R6, R13, R18, R23, R28, and
  R34; Play-in/Playoffs/Final Four have unlimited trades.
- Coach scoring: +10/+20/+25 for wins by 1-10/11-20/21+, and -5/-10/-20 for the
  corresponding losses; overtime uses the close-result bucket.
- A qualifying postponed game awards the player's prior average at the end of
  T1 and does not double that average for a captain. The manifest separately
  preserves the cancelled-during-open-round exception and organizer override.

The official English rules do not explicitly authorize changing the designated
sixth-man identity between Turns. Replay therefore uses the conservative rule
that this identity is frozen for the Round. This interpretation is explicit in
the manifest rather than silently assumed.

E2026 is blocked in the manifest. The production command additionally calls the
existing player-scoring compatibility gate. Flipping only one gate cannot make a
run production-ready.

## Strict E2025 replay

E2025 is the only safe full price-aware season. All 38 Matchdays have a distinct
validated pre-Matchday price/player-pool snapshot. E2022-E2024 prices remain
quarantined and cannot be requested through the replay loader.

For every E2025 Matchday, the replay:

1. resolves the full mapped market without looking at participation;
2. sets the decision cutoff to one microsecond before the first T1 tip;
3. removes all target/future player targets and target-game team facts from the
   temporary feature database;
4. rebuilds the frozen Phase 4B/6B/6C inputs at that cutoff;
5. retains eventual DNPs in the choice set and gives them their real zero only
   when replaying the outcome;
6. assigns Turns from local calendar dates;
7. carries the selected roster, bank, current market value, and legal trade
   constraint into the next Matchday.

The immutable cache contains 11,769 player-round predictions, 38 Matchdays, zero
duplicate target rows, zero target-time/cutoff violations, and 3,848 eventual
DNP rows retained. Historical availability overlays are not used because the
repository has no trustworthy timestamped E2025 injury history; this is a known
replay limitation, not a reason to leak actual participation.

## Player outcome simulation

Each player is sampled from a monotone, reconciled piecewise inverse CDF built
from Phase 6C expected FP, P10/P25/P50/P75/P90/P95, 20+/25+/30+/35+/40+ rates,
and <=5/<=10/<=15 rates. No normal player distribution is assumed. Conflicting
frozen anchors are reconciled with weighted pool-adjacent-violators and a bounded
tail/mean correction; no new FP model is fitted.

On a seeded 400-player held-out E2025 reservoir with 10,000 samples per player:

- mean MAE: 0.0819 FP;
- six-quantile MAE: 0.5856 FP;
- upside-probability MAE: 0.0040;
- downside-probability MAE: 0.0278.

The largest individual probability error is 0.216 where frozen quantile and tail
anchors are mutually incompatible; the aggregate calibration gate passes.

Independence overstated same-game residual aggregation. Standardized Phase 6C
same-game residual pair dependence was -0.0191 in E2023, -0.0188 in E2024, and
-0.0200 in held-out E2025. A bounded game Gaussian copula with rho=-0.015 reduces
the held-out same-game residual-sum second-moment error from 8.62 to 2.15. The
dependence is deliberately small; no unsupported player-pair parameters are used.

## Optimizers and dynamic policy

The deterministic baseline is a SciPy/HiGHS binary MILP. It jointly chooses
roster, coach, formation, starting five, sixth man, and captain subject to budget,
positions, team limit, trade retention, and Turn eligibility. Every reported
deterministic solve has a zero solver gap.

The stochastic optimizer generates a bounded set of legal MILP rosters under
common random numbers, ranks them on simulations separate from candidate
generation, and selects the roster/initial layout with the highest simulated
expected **final** Fantasy score. It never adds a variance or upside coefficient.

After each Turn, the policy enumerates legal next states. Completed-Turn outcomes
are replaced by their realized scores; unplayed outcomes remain at frozen
expectations. A completed starter is kept, benched, or loses the captain bonus
only when the alternative has greater expected final value. This produces
state-specific substitution and captain thresholds without a manually selected
FP cutoff and without observing the next Turn's outcome.

## Separate coach model

No validated coach distribution existed, so Phase 7 adds a small multinomial
logistic model over pre-tip home status, Elo difference, rolling margins, season
win rates, and games played. It is trained on E2018-E2024 and validated on all
804 E2025 team-games. It improves log loss from 1.5867 to 1.5210, multiclass
Brier from 0.7670 to 0.7498, and expected-score MAE from 11.175 to 9.802 versus
the historical-prior baseline. It remains isolated from the player model.

## E2025 backtest results

The policy and simulation settings were fixed before reading E2025 strategy
outcomes. E2025 outcomes select no thresholds or hyperparameters. The hindsight
oracle uses future outcomes only in its separate evaluation branch and obeys the
same roster, budget, formation, captain, coach, and sequential trade constraints.

All 38 eligible Matchdays replayed successfully. Scores are final realized
Fantasy points per Matchday:

| Strategy | Mean | Median | P10 | P90 | <=120 | >=180 |
|---|---:|---:|---:|---:|---:|---:|
| Highest expected-FP legal roster | 153.838 | 150.450 | 113.250 | 191.275 | 21.1% | 21.1% |
| Expected-FP/credit heuristic | 109.214 | 110.075 | 65.115 | 154.160 | 63.2% | 2.6% |
| Deterministic expected-FP optimizer | 158.271 | 159.825 | 118.755 | 203.940 | 13.2% | 28.9% |
| Static stochastic optimizer | 159.525 | 156.575 | 121.240 | 212.465 | 10.5% | 23.7% |
| Full dynamic stochastic optimizer | **168.851** | **171.725** | **129.870** | 202.945 | **5.3%** | **36.8%** |
| Hindsight oracle (evaluation only) | 280.176 | 280.475 | 238.535 | 316.585 | 0.0% | 100.0% |

The static stochastic layer gains 1.254 FP/Matchday over deterministic expected
FP. The complete dynamic policy gains 10.580 FP/Matchday (6.68%). On its own
selected rosters, legal between-Turn adaptation is worth 14.243 FP/Matchday and
captain switching is worth 6.634. The full dynamic versus static stochastic gap,
which captures the value of constructing and managing a Turn-diversified roster,
is 9.326 FP/Matchday. These comparisons are paired over all 38 Matchdays and are
not additive decompositions.

Average regret to the sequential hindsight-perfect legal oracle is 111.325 FP.
The dynamic engine improves the deterministic P10 by 11.115 FP and cuts <=120
frequency from 13.2% to 5.3%. Its P90 is 0.995 FP lower, but >=180 frequency rises
from 28.9% to 36.8%; strategy value came from choosing *when* to retain or replace
outcomes, not merely widening the team-score distribution.

## Risk and option-value findings

Two predeclared tail ablations reran the complete 38-Matchday sequential strategy
with identical seeds and legal constraints:

- removing explicit <=5/<=10/<=15 anchors reduced the mean from 168.851 to
  165.555 (-3.296 FP/Matchday);
- removing explicit 20+/25+/30+/35+/40+ anchors reduced it to 161.408
  (-7.443 FP/Matchday).

Thus calibrated tail rates add value beyond expected FP and the six quantiles.
Downside information changed *which* roster/initial roles made later actions
valuable rather than simply increasing action volume: full/no-downside policies
made 56/57 substitutions. Upside information changed portfolio and captain
option quality; full/no-upside policies made 16/15 captain switches. The fixed-
captain replay separately attributes 6.634 FP/Matchday to legal captain switching.

A later replacement is the clearest option-value moderator. Matchdays above the
median later-alternative strength gained 21.855 FP from adaptation, versus 6.632
for weaker-later-alternative rounds: a 15.224 FP difference. Of high-width T1
starters, 68.2% had an unplayed same-position alternative. This is the condition
under which the optimizer accepts an early gamble: a weak result can be benched
while a strong result is retained.

The engine did **not** learn a blanket preference for volatile T1 players. Mean
P90-P10 width among selected T1 starters was 23.774 FP versus 24.453 in the final
Turn (-0.678). It used some risky T1 players when recourse existed, but preferred
safer choices when it did not. This is the intended contextual result: volatility
has option value, not an unconditional bonus.

## Production command

```bash
python -m scripts.optimize_fantasy \
  --season E2026 \
  --matchday N \
  --budget 100
```

The output contains roster, credits, formation, starters, sixth man, bench,
coach, captain, player Turn, expected FP, P10/P50/P90, upside/downside rates,
strategic reason, simulated final-score distribution, solver gap, rules/model
fingerprints, and an immutable snapshot. After T1:

```bash
python -m scripts.optimize_fantasy \
  --season E2026 --matchday N --budget 100 \
  --snapshot SNAPSHOT.json \
  --after-turn-results T1_RESULTS.json
```

The rerun returns only legal field/bench/formation/captain actions. E2026 still
returns `BLOCKED_RULES` until real market plus scoring and game-rules gates pass.

An E2025 Matchday-1 command smoke test returned `SUCCEEDED`, a 100.0-credit legal
dynamic roster, zero solver gap, Theo Maledon as the T1 captain, Nikola Mirotic
as sixth man, and this seeded 32-draw distribution excerpt:

```json
{
  "method": "DYNAMIC_STOCHASTIC",
  "simulated_final_score": {
    "mean": 124.542,
    "p10": 85.730,
    "p50": 124.619,
    "p90": 148.179
  },
  "snapshot_fingerprint": "14f5e392f2ab..."
}
```

The small draw count is only a command-path smoke test; research results use
separate fixed 48-draw selection and 256-draw evaluation reservoirs. A supplied
T1 result payload then produced one legal field/bench swap and an immutable
between-Turn snapshot. Missing completed-Turn player scores now fail closed.

## Validation

All 272 tests and all 73 database integrity checks pass. The frozen bundle at
`data/derived/phase7/frozen_dynamic_strategy_engine/manifest.json` validates its
eight evidence files, eleven code files, rules fingerprint, frozen Phase 6C
identity, and bundle fingerprint.

## Five largest remaining limitations

1. Historical E2025 replay has no trustworthy point-in-time injury feed, so it
   cannot reproduce users' historical known-absence information.
2. Only E2025 has validated point-in-time prices; strategy generalization cannot
   yet be tested on another price-safe season.
3. The official rule text is not explicit about changing the designated sixth
   man between Turns; replay uses the documented conservative interpretation.
4. Player dependence is a small game-level residual copula, not a detailed
   player-pair or possession simulator.
5. Coach expected-score error remains material even though it beats the simple
   baseline, and postponed/special-event organizer decisions remain exogenous.

Phase 7 stops here. It does not begin another phase.
