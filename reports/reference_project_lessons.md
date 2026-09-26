# Reference Project Lessons — Phase 1.5

Review date: 2026-08-11  
Scope: architectural lessons only; no source code was copied and no prediction or optimization component was implemented.

## Executive conclusion

The most useful lesson is not a particular model. It is to preserve a strict, inspectable chain from the information available before a round to the decision produced for that round:

```text
immutable raw observations
        -> normalized, source-keyed records
        -> point-in-time/as-of view
        -> leakage-safe features
        -> versioned player projections
        -> legal roster/captain decision
        -> realized result and backtest record
```

[AIrsenal](https://github.com/alan-turing-institute/AIrsenal) is the strongest architectural reference: collection/update, prediction, and optimization are separate runnable stages, and its optimizer consumes projections rather than being the prediction model. The public [EuroLeague Fantasy Optimizer](https://fantasyteam.app/) is useful mainly as a product and constraint reference; its public page does not expose enough methodology to validate its forecasts or backtests. The archived [KengoA NBA fantasy-basketball project](https://github.com/KengoA/fantasy-basketball) demonstrates the full statistics-plus-salary-to-lineup workflow, but its notebook-heavy, scraped, name-joined design is a caution for a durable system. The open-access [Papageorgiou, Sarlis and Tjortjis NBA study](https://link.springer.com/article/10.1007/s41060-024-00523-y) supplies useful feature and linear-optimization examples, while also illustrating why outcome-conditioned filtering and non-temporal cross-validation must not be carried into our backtesting design.

The immediate recommendation is therefore to adopt data lineage, point-in-time snapshots, stable identity, modular stages, simple baselines, and walk-forward evaluation early. Complex models, simulations, and multi-round strategy remain later work.

## Evidence boundaries

This comparison distinguishes public evidence from inference:

- **Documented** means the behavior is described in a project's repository, paper, or public interface.
- **Inferred** means it is an architectural lesson suggested by that evidence, not a verified implementation detail.
- **Not demonstrated** means the reviewed material does not support a conclusion. It must not be treated as a feature the project has.

The EuroLeague optimizer appears to be a closed, unofficial web product. Its visible interface says that it uses live data from GivemeStats, exposes budget, position, price, minutes, index, last-three form, status, and a value mode, and records prior generated decisions. It also currently says injury data are unavailable. No public source repository, model card, data-version history, or reproducible backtest was identified. Labels such as “AI-powered” are therefore marketing claims, not evidence of a specific model.

AIrsenal is an actively developed open-source FPL package. Its [README](https://github.com/alan-turing-institute/AIrsenal#readme) documents independent commands to create/update a database, generate predictions, optimize transfers/squads, and optionally apply the resulting actions. It states that the initial database loads three prior seasons, that prediction combines a team-level match model with player-level involvement models and heuristics, and that optimization normally looks three gameweeks ahead while selecting captain and substitute order. Its current repository also separates `data`, `framework`, `scripts`, `api`, and `tests`.

The KengoA repository is an archived capstone, not a production reference. Its README documents nine notebooks covering Basketball-Reference and RotoGuru scraping, name-standardized merging, Fantasy-point calculation, weighted features, baselines, boosting/neural models, predictions, and genetic-algorithm lineup construction. It explicitly excludes opponent defensive ability and uses regular-season data starting in 2014–15. Its value is the end-to-end decomposition; its source choices, name joins, notebooks, and age make it unsuitable as a direct foundation.

The NBA paper collected official NBA player/team game data from 2011–12 through 2020–21, constructed lag, momentum, rest, team, and opponent features, then used external salary/position data and linear optimization for one DraftKings slate. It chronologically allocated the outer 70/20/10 train/test/unseen partitions, but describes its internal 10-fold cross-validation as non-temporal. It also selected players using 2020–21 participation/performance criteria and removed individual games with fewer than 10 Fantasy points due to injury, unexpected benching, or fewer than 12 minutes. Those choices make its reported experiment informative, but not a template for leakage-safe prediction of the full real-world player pool.

## Architecture comparison

| Concern | EuroLeague Fantasy Optimizer | AIrsenal | NBA projects/study | Decision for this project |
| --- | --- | --- | --- | --- |
| Historical collection | Public UI shows live inputs and a decision-history panel; raw snapshot history is not demonstrated | Database initialization/update stages are documented; three previous seasons plus current fixtures/results | KengoA scrapes historical games and salaries; the paper uses ten seasons of official game data | Store immutable source responses and timestamp every Fantasy market snapshot; never infer old prices from current values |
| Normalization | Not documented | Persistent data layer and independent update commands are documented | KengoA explicitly standardizes names while merging two scraped sources | Normalize after preservation; retain every original source ID and value alongside canonical fields |
| Player identity | Not documented | Not assessed here; FPL has its own identifiers | KengoA's need for name standardization exposes join fragility | Use Fantasy and EuroLeague IDs when present; keep a versioned crosswalk with evidence and confidence, not names as keys |
| Fantasy price/salary | Price and “index per credit” are core visible inputs | Price belongs to current roster/transfer state, separate from the scoring model | Both NBA examples join salary/position data separately from performance statistics | Treat price, position, eligibility, and availability as time-varying market observations, separate from realized player games |
| Rolling form/time weighting | Visible `Last 3`, minutes, and value columns; formula/model not documented | Historical description uses recent minutes and longer-history team/player models | KengoA compares averages/weighted features; the paper uses 1-game lags plus 3/5/7/10-game FP lags and momentum | Implement simple trailing baselines first, always shifted before the target game; compare windows/decay during walk-forward validation |
| Player prediction | Method not documented | Team scoreline model plus player contribution/heuristics | KengoA compares baselines, boosting and neural networks; paper compares 14 models and per-player models | Start with transparent baselines and pooled regularized/tree models; individual-player models are likely data-starved in EuroLeague |
| Minutes/availability | Minutes and status filters are visible; injury feed is currently marked unavailable | Minutes affect projections; old project description calls better minutes/injury replacement modeling an improvement area | KengoA notes injury-driven opportunity; the paper removes low-minute/injury outcomes instead of forecasting them | Model participation/expected minutes explicitly later; retain DNPs, benchings, injuries, and restrictions as real outcomes |
| Team/game environment | Not demonstrated | Explicit team-level scoring model feeds player projections | Paper joins team and future-opponent histories; KengoA acknowledges opponent defense is omitted | Keep a game-environment layer distinct from player rate/minutes projections; derive team/opponent features as of tip-off |
| Injury handling | UI says injury data unavailable and disables the filter | Current injury/suspension status is pulled during updates; point-in-time history is not established by README | KengoA discusses opportunity effects; the paper's outcome filtering is unsafe for our target | Snapshot timestamped status/news; never use a later “final” injury label for an earlier deadline |
| Lineup optimization | Budget/value/position constraints visible; `2-2-1` decisions shown | Separate multi-week transfer/squad optimizer, including captain and bench order | KengoA uses a genetic algorithm; paper uses linear optimization with salary and position constraints | Use deterministic mixed-integer/constraint optimization only after projections exist; preserve the entire candidate pool and ruleset for replay |
| Captain/bench | Product history shows formation, but public method is not documented | Explicit captain and substitute-order output | DraftKings examples have neither EuroLeague-style captain doubling nor 50% bench scoring | Encode captain, sixth man/bench, coach, Turn locks, and multipliers as versioned rules—not prediction features |
| Historical evaluation | “Historical Analytics” appears to summarize generated choices, not necessarily realized, reproducible backtests | Live operation and tests are evident; a strict walk-forward benchmark is not established by the reviewed README | KengoA evaluates March 2019 contests; paper evaluates models plus one real slate | Build round-by-round walk-forward replay; one slate or a decision log is not evidence of generalization |
| Multi-round planning | Not demonstrated | Usually optimizes transfers over the next three gameweeks | NBA daily-slate examples are single-period | Defer until single-round prices, deadlines, transfers, Turns, and scoring can be reproduced exactly |

## Lessons by subsystem

### Data collection and normalization

AIrsenal demonstrates the operational advantage of making data update a first-class command rather than an incidental side effect of prediction. KengoA demonstrates the opposite risk: when two sources are merged by standardized names in analysis notebooks, identity corrections and reproducibility become difficult.

For EuroLeague Fantasy, a collection run should eventually be identifiable by source, request parameters, retrieval time, effective season/round, schema version, and content hash. Raw responses should be immutable. A normalized observation should point back to the raw artifact and preserve source IDs. Market price, Fantasy position, eligibility, status, and official Fantasy points must remain separate time-varying facts; a later correction must not silently overwrite what a historical manager could see before the deadline.

### Rolling features and time weighting

All reviewed systems recognize recent form, but none removes the need to test it properly. The EuroLeague optimizer exposes last-three form; KengoA constructs simple and weighted averages; the NBA paper uses multiple lag windows and momentum. These are candidate baselines, not evidence that one window is optimal for EuroLeague.

Every trailing statistic for a target game at time `T` must use rows with event time before `T` and observations published by the applicable decision cutoff. The feature calculation should shift before rolling. Same-round games require tip-time or Turn-aware cutoffs rather than a blanket round aggregate. Window length or decay should be selected inside chronological training folds, then assessed on the next untouched period.

### Prediction, minutes, and availability

AIrsenal's separation of team context from player contribution is worth adopting conceptually. Basketball projections can likewise separate at least:

```text
probability active
    × expected minutes if active
    × expected production per minute under team/opponent context
    -> expected Fantasy points and uncertainty
```

That is a future modeling direction, not a Phase 1.5 implementation. It prevents a common error seen in the NBA study: filtering away injury, unexpected-bench, and short-minute games makes the target easier but removes exactly the downside events a Fantasy decision system must price. DNPs and role changes must remain in the evaluation population. Predictions should be issued for every legally selectable market player, including an explicit low-availability probability, rather than only for players retrospectively known to play well.

### Optimization and Fantasy rules

The common successful boundary is that optimization consumes a projection table; it does not own data collection or fit the prediction model. The NBA paper's linear formulation is a clearer future baseline than a genetic algorithm because roster legality and deterministic expected-value maximization can be tested exactly. Heuristic or stochastic optimization is unnecessary until the legal problem exceeds a solver's practical limits.

The EuroLeague rules are materially different from DraftKings and FPL. The future rules input must be versioned by season and cover credits, positions, team limits, head coach, starting five, sixth player/bench treatment, captain multiplier, Transfers, and Turn-specific lock/substitution behavior. A backtest must optimize using the price and eligibility snapshot available at that historical deadline, then apply the scoring rules effective in that season.

### Historical evaluation

The evaluation unit should be a historical decision, not a randomly sampled player-game row. For each round or Turn:

1. Reconstruct the exact information state and market snapshot at the historical cutoff.
2. Fit or update only on earlier observations.
3. Generate and store player projections before revealing outcomes.
4. Optimize under the then-current credits and rules.
5. Score the frozen decision against realized results.
6. Advance the cutoff and repeat.

This walk-forward design should report forecast metrics and decision metrics separately. Useful forecast measures include MAE, calibration/coverage for intervals, and error by minutes/status group. Useful decision measures include legal-roster rate, realized points, regret versus a hindsight-only upper bound, budget efficiency, captain contribution, and performance relative to transparent baselines. The hindsight optimum is a diagnostic ceiling, never a deployable benchmark.

Random cross-validation is inappropriate for the main result, even if the outer holdout is chronological. Model selection, feature selection, normalization, and hyperparameter tuning must all occur inside time-respecting folds. Likewise, selecting the eligible player population using end-of-season appearances or removing bad games based on their realized outcome creates survivorship/selection bias.

## What to adopt early

1. **Hard stage boundaries.** Keep ingestion, normalization/identity, as-of feature construction, prediction, optimization, and evaluation independently runnable and testable.
2. **Immutable and replayable inputs.** Preserve raw basketball responses and Fantasy market/status snapshots with retrieval and effective timestamps.
3. **A point-in-time contract.** Every feature and roster input must answer “was this knowable before this decision cutoff?”
4. **Stable identity with provenance.** Link Fantasy IDs to official player/team IDs using evidence, retaining source IDs and mapping confidence.
5. **Simple baselines first.** Season mean, last-N mean, exponentially weighted mean, FP/minute plus recent minutes, and price/value baselines should precede complex ML.
6. **Separate opportunity from rate.** Treat active probability and minutes as distinct from per-minute production and matchup context.
7. **Versioned projection artifacts.** Store predicted mean and later uncertainty with model/data cutoff, feature version, and target game; do not recompute history silently.
8. **Rules as data.** Keep salary/credits, positions, coach, captain/bench multipliers, formation, Transfers, and Turn locks out of hard-coded model logic.
9. **Walk-forward backtesting.** Evaluate the entire selection pipeline chronologically, including players who do not play or disappoint.
10. **Decision auditability.** Save the candidate pool, exclusions, prices, projections, constraints, selected roster, and solver status for each historical decision.

## What to defer

- Complex neural networks and per-player models: the EuroLeague sample size per player is modest and role/team changes reduce stationarity.
- Full Monte Carlo and covariance-aware tournament strategy: useful only after means, minutes, availability, and uncertainty are calibrated.
- Multi-round transfer planning: first establish historical prices, season-specific rules, deadlines, Turns, and single-round replay.
- Advanced tactical/tracking inputs: potentially valuable, but not required for a strong raw-box-score/PBP baseline and likely commercial.
- Automated live team changes: authentication, session security, irreversible actions, and deadline behavior add risk without helping model validation.
- Frontend, cloud deployment, and production database tuning: none resolves the remaining point-in-time data questions.

## Specific anti-patterns to avoid

- Treating a public “historical analytics” panel as proof of a leakage-safe backtest.
- Joining player sources solely by a normalized name.
- Using today's Fantasy price, position, injury flag, team, or roster membership for an old round.
- Computing rolling features without shifting past the target row.
- Applying full-season aggregates to early-season games.
- Randomly splitting chronological player-game rows or tuning on the final evaluation period.
- Selecting players using appearances, minutes, or mean Fantasy points measured after the historical decision date.
- Removing DNPs, injury-shortened games, benchings, or low scores because they are inconvenient outliers.
- Reporting only projection MAE while ignoring whether the chosen roster was legal, reproducible, and better than simple selection baselines.
- Letting the optimizer reach into live APIs, retrain models, or mutate source data.

## Phase 2 implication

These references support a modular Phase 2 design. The authenticated audit now
adds limited current-season price history, but no previous-season index and no
historical as-of availability: old-matchday responses carried unchanged current
status/average fields. Database architecture can therefore proceed only with
explicit source observations, effective/retrieval timestamps, stable
identifiers/crosswalk evidence, season-specific Fantasy rules, and immutable
decision cutoffs. Full historical roster backtests still require an archive or a
deliberately reduced scope; prospective snapshots should begin once permitted.

## Sources reviewed

- [EuroLeague Fantasy Optimizer public interface](https://fantasyteam.app/) — product-visible inputs, constraints, status limitation, and decision-history display; no public implementation was assumed.
- [AIrsenal repository and README](https://github.com/alan-turing-institute/AIrsenal) — staged database/update/prediction/optimization workflow, three-season initialization, captain/bench and multi-gameweek operation.
- [Alan Turing Institute: AIrsenal approach](https://www.turing.ac.uk/news/airsenal) — team/player decomposition, recent-minutes treatment, captaincy decision, and multi-week transfer framing. This 2019 article describes an earlier system state; current behavior is taken from the repository README where available.
- [KengoA/fantasy-basketball](https://github.com/KengoA/fantasy-basketball) — archived statistics/salary merge, name standardization, baselines/models, and genetic-algorithm lineup workflow.
- [Papageorgiou, Sarlis and Tjortjis, “An innovative method for accurate NBA player performance forecasting and line-up optimization in daily fantasy sports”](https://link.springer.com/article/10.1007/s41060-024-00523-y) — official NBA inputs, lag/rest/team/opponent features, evaluation design, player/game exclusions, external salary/position inputs, and linear optimization.
