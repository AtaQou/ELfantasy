# Phase 6A historical rehearsal

The bounded technical rehearsal used E2025 Fantasy matchdays **1 and 2** and is
labelled `HISTORICAL_REHEARSAL` / `CONDITIONAL_ON_KNOWN_ABSENCE_SET`. Actual
zero-minute players are an external condition; this does not claim historical
availability knowledge.

Results:

- 189 player rows, 16 games, and 21 game/team absence contexts;
- unadjusted/adjusted MAE: 6.39043 / 6.38907;
- unadjusted/adjusted RMSE: 8.44095 / 8.43944;
- zero player, team, opponent, or rotation cutoff violations;
- serialized Phase 4B validation models were used for inference;
- maximum direct-component difference: `7.105427357601002e-15`;
- maximum decomposed-only adjustment identity error: `3.1363800445660672e-15`;
- maximum 200-minute reconciliation error: `5.684341886080802e-14`;
- identical repeated fingerprint:
  `146c3bca97c6365b11be101aad62551ea1f541511e69250d08f225bb82db7e7b`.

Target-game teammate minutes, player FP, box score, PBP, and shots were not
features. Actual outcomes were joined only after prediction for evaluation.
Evidence is in `data/samples/phase6a/historical_rehearsal.json`.
