# Phase 6A.1 frozen Phase 4B deployment bundle

## Outcome

No original fitted model objects remained. The accepted models were
deterministically reconstructed from the frozen chronological datasets,
manifests, seed 17, selected configurations, selected iteration counts, and
target rules. No search, retuning, or model-selection decision was repeated.

Bundle identifier: `phase4b_conditional_performance_frozen_v1`.

The live deployment files are `direct.json`, `minutes.json`, and
`production.json`. Nine serialized chronological validation models under
`validation_models/outer_e2023`, `outer_e2024`, and `outer_e2025` make the
historical reproduction claim independently executable. Canonical CatBoost JSON
removes only volatile serializer GUID/timestamp metadata.

## Reproduction gate

All 22,399 retained outer rows were predicted again from the serialized models.

| Measurement | Reconstructed |
|---|---:|
| Maximum absolute row difference | 1.0658141036401503e-14 |
| Mean absolute row difference | 1.2001888711055981e-15 |
| MAE | 5.808649555104744 |
| RMSE | 7.579663446775682 |
| Spearman | 0.5063550600370463 |
| Pearson | 0.5134851605153571 |

The strict tolerance is `1e-12`; the gate passes. Aggregate similarity alone is
not accepted. Component-level maxima are `1.0658e-14` direct, `1.4211e-14`
minutes, and `5.5511e-16` FP/min.

## Frozen live refit

The live-facing models use the latest frozen `outer_e2025` selections and all
eligible earlier rows. Direct uses 191 trees, minutes 337 trees, and production
353 trees. The architecture remains exactly 25% direct plus 75% adjusted
minutes times expected FP/min.

The bundle manifest records every model hash, feature order/manifest,
preprocessing policy, per-component and combined training-data fingerprints,
Phase 4A/4B protocol versions, target scoring-rule fingerprint, source-code
hashes, reproduction evidence, and the combined bundle fingerprint. The loader
verifies all of them and fails closed.

## Current scoring compatibility

The training target is `ELFC_PLAYER_V1_STANDARDIZED_2025`. The official current
player-scoring page still describes the same component formula, but it does not
explicitly establish an effective E2026/2026-27 rules version and no genuine
E2026 market snapshot is available. The current gate is therefore
`SCORING_RULES_UNVERIFIED`, not compatible-by-assumption.

Player-game scoring is isolated from captain, bench, substitutions, transfers,
and price mechanics. Those later strategy rules were not implemented here.
