# Phase 5B live availability and redistribution workflow

## Status semantics

| Source status | Default action |
|---|---|
| AVAILABLE, PROBABLE | PLAY |
| OUT, SUSPENDED, NOT_REGISTERED | OUT |
| LIMITED | PLAY, with warning unless an explicit limit is supplied |
| QUESTIONABLE, DOUBTFUL, GAME_TIME_DECISION, UNKNOWN | User decision |

QUESTIONABLE never reduces `expected_minutes_if_playing`. The review command accepts
PLAY, OUT, UNKNOWN, and LIMITED through the existing append-only, scoped override
backend. Manual overrides retain Phase 5A's highest source priority and do not alter
historical basketball facts.

```bash
python -m scripts.review_availability E2026
python -m scripts.review_availability E2026   --decision PLAYER_ID=OUT --game-id GAME_ID
python -m scripts.set_role_limit PLAYER_ID MAX_MINUTES 20   --season E2026 --game-id GAME_ID
python -m scripts.clear_role_limit PLAYER_ID   --season E2026 --game-id GAME_ID
```

One unresolved player creates explicit `PLAYER_PLAYS` and `PLAYER_OUT` scenarios.
Multiple unresolved players require user-supplied scenario sets, avoiding an
uncontrolled exponential expansion. No scenario receives a probability.

## Prediction flow

Each resolved scenario normalizes the full pre-game baseline to 200, removes OUT
roles, derives cumulative missing-role and remaining-roster context, applies the
frozen method `p5b__xgboost__core`, reconciles available
players to exactly 200 in [0,40], and propagates adjusted minutes only through the
frozen 75% decomposed component. Outputs retain scenario decisions, missing minutes,
minutes delta, before/after FP, model version, cutoff, fingerprints, and context—not
causal claims.

LIMITED alone supplies no numeric restriction. Supported explicit scopes are maximum
minutes, expected minutes, and percentage role reduction. Adjusted uncertainty
intervals are deliberately null/flagged, not fabricated.
