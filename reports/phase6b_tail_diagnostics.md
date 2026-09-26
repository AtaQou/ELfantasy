# Phase 6B tail diagnostics

The 22,399 chronological outer rows contain 1,281 actual 25+ games, 484 30+,
180 35+, and 51 40+. On those subsets respectively, central MAE is
16.029/19.926/23.914/27.704 FP; P90 coverage is
0.2186/0.0310/0/0 and P95 coverage is 0.4754/0.2025/0.0222/0. This conditional
tail diagnostic is intentionally different from global quantile coverage: most
tail realizations should exceed a P90, and calibration is assessed across the
whole comparable population.

Mean probabilities assigned to the realized tail subsets are 13.78% for 25+,
6.24% for 30+, 2.74% for 35+, and 1.68% for 40+. The probability losses and
reliability results, rather than an aggressive point loss, penalize missed and
fake upside.

The worst missed-upside outer case was 48.4 actual FP with mean 9.61, P90 18.06,
P95 21.44, and P(40+) 0.033%. Other severe misses include 41.8 actual versus
P95 18.19, and the 57.2 maximum versus P95 34.86. These prove that Phase 6B
materially improves distribution semantics but does not eliminate rare-shock
underestimation.

The worst false-upside case was −8.0 actual FP with P90 29.42/P95 34.51;
another was −10.0 actual with P90 26.84/P95 30.86. These low positive-minute
games remain in the primary target as genuine downside. Full programmatic
out-of-sample archetype cases—including successes and failures—are stored in
`data/derived/phase6b/research/results.json` under `case_studies`; selection is
rule-based, not hand-picked.

In the separately labelled 15,019-row
`CONDITIONAL_ON_KNOWN_ABSENCE_SET` diagnostic, the frozen Phase 5B location shift
improves P90 pinball 1.49440→1.48691 and P95 pinball 0.90638→0.90174. For 828
major opportunities, mean P90 rises 0.663 FP and coverage improves
0.8430→0.8599. No target-game recipient minutes are features. This supports live
compatibility, while the supplied OUT-set caveat remains essential.
