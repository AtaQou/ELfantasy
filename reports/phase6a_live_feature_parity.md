# Phase 6A live feature parity

Phase 6A calls the Phase 3 core and Phase 3B rotation formulas; there is no
simplified live feature pipeline. The adapter now uses
`feature_cutoff_time = prediction_generated_at`, filters facts at or after that
cutoff for deterministic past-cutoff reruns, and retains scheduled tip separately.

Representative rows with at least five prior rotation games were reconstructed:

| Season | Rows | Mismatched features | Maximum absolute error |
|---|---:|---:|---:|
| E2023 | 1 | 0 | 0.0 |
| E2024 | 1 | 0 | 0.0 |
| E2025 | 1 | 0 | 0.0 |

Tolerance was `1e-9`; overall maximum absolute error was **0.0**. Evidence is in
`data/samples/phase6a/live_feature_parity.json`.

The combined feature fingerprint covers the exact frozen direct
`CORE_ROTATION` (with player identity), `MINUTES_ROLE`, `PRODUCTION`, and
75-feature redistribution manifests. Each run also stores each player's full
feature snapshot and fingerprint. The currently computed combined fingerprint is
`2b54a0aedd44aeacb852fb11efd4ce18fe433db8a05f5534ccf7b9fad4ec520b` for
the Phase 4B deployment-side manifests; a successful scored run additionally
binds the Phase 5B manifest into its validated run fingerprint. No mismatches
were observed.
