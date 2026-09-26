# Historical Human Benchmark — EuroLeague Fantasy E2025

## Result

The frozen AI replay was season-long competitive with the strongest published BasketStories Private League managers, but it did not match the extreme weekly scores of the best ten teams in each individual round.

- AI final score: **6,416.35 FP**.
- BasketStories leader: **6,832.10 FP**; AI gap: **-415.75 FP** (**6.09%**).
- The AI score falls between the published 8th-place score (6,421.10) and 9th-place score (6,411.66), so its supported equivalent finish is **approximately 9th in the BasketStories Private League**.
- AI beat the published round Top-10 average in **4/38 rounds (10.5%)**, the Top-5 average in **2/38**, and the single best published round score in **0/38**.
- Average round gap: **-48.13 FP vs Top-10**, **-52.38 FP vs Top-5**, and **-60.14 FP vs the round winner**.

This apparent contrast is expected: each weekly Top-10 contains that round's ten best teams and is not a fixed cohort. It is a much harder benchmark than the cumulative leaderboard. The cumulative comparison is the appropriate measure of season-long competitiveness.

## Scope and method

`E2025` in this repository is the **2025–26** season. The benchmark uses all 38 regular-season matchdays shared by the frozen Phase 7 replay and the BasketStories archive.

- **AI:** the existing `FULL_DYNAMIC_STOCHASTIC` row from `e2025_strategy_backtest.parquet`, using `actual_final_score`. The replay carries the roster, bank, current prices, and transfer limits forward from one matchday to the next. It permits only the already-modelled legal between-Turn substitutions and captain switches.
- **Humans:** the published BasketStories Private League round Top-10 and cumulative Top-10. Rounds 1–35 have individual articles; the regular-season winners article supplies rounds 36–38 and the final cumulative table.
- **Alignment:** every AI matchday maps one-to-one to its BasketStories Fantasy Game number. Cumulative Top-10 tables exist for MD1–35 and MD38; BasketStories did not publish separate cumulative tables for MD36–37.
- **Separation:** human standings live only under `data/benchmarks/`; they are not ingested into the database or used by the prediction/training pipeline.

Reproducible analysis: [`notebooks/historical_human_fantasy_benchmark.ipynb`](../notebooks/historical_human_fantasy_benchmark.ipynb).

## Overall comparison

| Measure | AI / result | Human benchmark |
| --- | ---: | ---: |
| Mean round score | 168.85 | 216.98 Top-10 avg |
| Mean round Top-5 score | — | 221.23 |
| Mean round-winning score | — | 228.99 |
| Rounds above Top-10 average | 4 / 38 | — |
| Rounds above Top-5 average | 2 / 38 | — |
| Rounds above round winner | 0 / 38 | — |
| Final cumulative score | 6,416.35 | 6,832.10 leader |
| Final gap to leader | -415.75 | — |
| Supported equivalent final rank | approximately 9th | published Top 10 only |

At MD38 the nearest published teams were **Blue Vayeros** (8th, 6,421.10), **v.p. nupezę vilkai** (9th, 6,411.66), and **Btbuckets2** (10th, 6,393.92). The archive supports the 9th-place interpolation, but not a percentile or a rank outside the published Top 10.

## Matchday-by-matchday

`Gap` is AI round score minus the published round Top-10 average. Human cumulative leader values are unavailable for MD36–37.

| MD | AI | Best human | Top-5 avg | Top-10 avg | Gap | AI cumulative | Human leader cumulative |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 112.25 | 226.85 | 222.90 | 221.06 | -108.80 | 112.25 | 226.85 |
| 2 | 203.40 | 204.25 | 201.50 | 199.32 | +4.08 | 315.65 | 389.90 |
| 3 | 140.20 | 223.95 | 222.57 | 221.09 | -80.89 | 455.85 | 585.90 |
| 4 | 151.65 | 217.80 | 210.21 | 204.91 | -53.26 | 607.50 | 723.20 |
| 5 | 143.20 | 233.15 | 229.79 | 226.22 | -83.02 | 750.70 | 934.45 |
| 6 | 179.50 | 218.85 | 215.24 | 214.02 | -34.53 | 930.20 | 1,117.45 |
| 7 | 169.65 | 248.40 | 235.30 | 231.83 | -62.18 | 1,099.85 | 1,312.90 |
| 8 | 132.60 | 217.35 | 208.09 | 204.82 | -72.22 | 1,232.45 | 1,509.30 |
| 9 | 147.00 | 214.40 | 206.34 | 202.03 | -55.03 | 1,379.45 | 1,672.35 |
| 10 | 220.75 | 241.50 | 231.59 | 225.22 | -4.47 | 1,600.20 | 1,841.85 |
| 11 | 134.80 | 201.00 | 195.77 | 190.40 | -55.60 | 1,735.00 | 1,945.10 |
| 12 | 122.25 | 214.30 | 212.40 | 210.04 | -87.79 | 1,857.25 | 2,137.50 |
| 13 | 150.20 | 225.20 | 221.54 | 218.14 | -67.94 | 2,007.45 | 2,316.00 |
| 14 | 96.70 | 191.51 | 177.80 | 172.56 | -75.86 | 2,104.15 | 2,448.55 |
| 15 | 191.20 | 241.95 | 233.23 | 228.78 | -37.58 | 2,295.35 | 2,629.20 |
| 16 | 175.30 | 230.50 | 221.69 | 215.08 | -39.78 | 2,470.65 | 2,787.45 |
| 17 | 243.00 | 253.00 | 239.46 | 233.28 | +9.73 | 2,713.65 | 2,994.75 |
| 18 | 172.65 | 223.80 | 221.24 | 217.45 | -44.80 | 2,886.30 | 3,197.35 |
| 19 | 183.90 | 240.95 | 229.76 | 222.06 | -38.15 | 3,070.20 | 3,406.35 |
| 20 | 183.60 | 220.80 | 210.51 | 205.05 | -21.45 | 3,253.80 | 3,566.85 |
| 21 | 229.60 | 248.66 | 231.96 | 227.55 | +2.05 | 3,483.40 | 3,793.48 |
| 22 | 179.60 | 231.95 | 222.80 | 219.46 | -39.87 | 3,663.00 | 3,986.78 |
| 23 | 125.25 | 246.90 | 241.92 | 238.51 | -113.26 | 3,788.25 | 4,210.98 |
| 24 | 189.80 | 217.80 | 206.60 | 202.73 | -12.93 | 3,978.05 | 4,408.03 |
| 25 | 202.75 | 227.55 | 221.99 | 218.33 | -15.58 | 4,180.80 | 4,594.88 |
| 26 | 131.85 | 217.10 | 211.30 | 206.82 | -74.97 | 4,312.65 | 4,732.68 |
| 27 | 152.40 | 240.35 | 227.19 | 223.42 | -71.02 | 4,465.05 | 4,917.68 |
| 28 | 170.80 | 242.70 | 229.31 | 222.70 | -51.90 | 4,635.85 | 5,102.68 |
| 29 | 182.70 | 280.85 | 259.45 | 252.12 | -69.42 | 4,818.55 | 5,321.53 |
| 30 | 196.70 | 199.45 | 197.29 | 193.20 | +3.50 | 5,015.25 | 5,471.78 |
| 31 | 180.05 | 216.80 | 213.02 | 209.55 | -29.50 | 5,195.30 | 5,663.48 |
| 32 | 177.65 | 216.05 | 208.46 | 203.00 | -25.35 | 5,372.95 | 5,828.23 |
| 33 | 165.20 | 212.98 | 210.92 | 208.60 | -43.40 | 5,538.15 | 5,991.31 |
| 34 | 168.55 | 221.13 | 219.21 | 217.19 | -48.64 | 5,706.70 | 6,181.90 |
| 35 | 189.40 | 266.55 | 245.41 | 238.86 | -49.46 | 5,896.10 | 6,353.10 |
| 36 | 200.75 | 263.60 | 258.01 | 255.30 | -54.54 | 6,096.85 | — |
| 37 | 151.05 | 203.25 | 200.88 | 194.93 | -43.88 | 6,247.90 | — |
| 38 | 168.45 | 258.35 | 253.95 | 249.52 | -81.08 | 6,416.35 | 6,832.10 |

Full manager/team names, published ranks, round scores, and cumulative values are preserved in the benchmark CSVs listed below.

## Strongest and weakest AI rounds

Measured by gap to the published round Top-10 average:

| Strongest MD | AI | Top-10 avg | Gap |
| ---: | ---: | ---: | ---: |
| 17 | 243.00 | 233.28 | +9.73 |
| 2 | 203.40 | 199.32 | +4.08 |
| 30 | 196.70 | 193.20 | +3.50 |
| 21 | 229.60 | 227.55 | +2.05 |
| 10 | 220.75 | 225.22 | -4.47 |

| Weakest MD | AI | Top-10 avg | Gap |
| ---: | ---: | ---: | ---: |
| 23 | 125.25 | 238.51 | -113.26 |
| 1 | 112.25 | 221.06 | -108.80 |
| 12 | 122.25 | 210.04 | -87.79 |
| 5 | 143.20 | 226.22 | -83.02 |
| 38 | 168.45 | 249.52 | -81.08 |

The cumulative deficit was smallest after MD2 (-74.25) and largest after MD29 (-502.98), before recovering to -415.75 at the regular-season finish.

## Budget, transfers, and roster record

- Mean available budget: **125.37 credits**; mean credits used: **125.03**; mean unused: **0.34**.
- Available budget rose from **100.00** to **149.10 credits**; MD38 used all 149.10.
- The replay records **161 post-MD1 transfers**, averaging **4.35** (median 4) per later round. The MD1 value of 11 is initial acquisition of ten players plus one coach and is not counted as a transfer round.
- The full per-round roster, starters, captain, sixth man, coach, budget, bank, transfers, substitutions, and cutoff are in [`e2025_ai_replay_ledger.csv`](../data/benchmarks/e2025_ai_replay_ledger.csv).

BasketStories does not publish comparable budget or transfer histories in these standings articles, so no human budget/transfer comparison is claimed.

## Leakage and integrity checks

The notebook completed these bounded checks:

1. Parsed exactly **380** round rows: ten published teams for each of 38 matchdays.
2. Parsed exactly **360** cumulative rows: ten teams for MD1–35 and MD38.
3. Confirmed one-to-one matchday alignment between the 38 AI replay rows and 38 provenance rows.
4. Confirmed all stored cutoff-audit flags report `max_feature_time_strictly_before_cutoff=true`.
5. Confirmed predictions are conditional on playing and actual DNP zeroes are applied only during replay.
6. Recomputed AI cumulative FP as the exact cumulative sum of round FP.
7. Confirmed every parsed round score is numeric and between 0 and 400 FP, catching malformed HTML cell shifts.

No models were retrained, no features or optimizer logic changed, and no repository-wide test suite was run.

## Human data and provenance

- [`basketstories_private_league_e2025_round_standings.csv`](../data/benchmarks/basketstories_private_league_e2025_round_standings.csv): all 380 published round Top-10 rows.
- [`basketstories_private_league_e2025_cumulative_standings.csv`](../data/benchmarks/basketstories_private_league_e2025_cumulative_standings.csv): all 360 available cumulative Top-10 rows.
- [`basketstories_private_league_e2025_provenance.csv`](../data/benchmarks/basketstories_private_league_e2025_provenance.csv): source URL, post ID, publication date, retrieval date, and availability by matchday.
- [`e2025_ai_human_round_comparison.csv`](../data/benchmarks/e2025_ai_human_round_comparison.csv): the aligned comparison used above.

Primary source examples: [BasketStories Game 1](https://www.basketstories.net/article.php?p=private_league_post_5564), [Game 35](https://www.basketstories.net/article.php?p=private_league_post_5994), and the [regular-season winners article with Games 36–38 and final standings](https://www.basketstories.net/article.php?p=private_league_post_6025). All remaining article URLs are stored per matchday in the provenance CSV.

Source quirks are retained transparently: MD12 numbers its ten round rows 2–11; the benchmark preserves those published ranks and still treats them as ten entries. Two articles contain the malformed literal name `Kostas <Kechris`; the parser normalizes only that stray angle bracket to recover the manager-name cell, without changing scores or ranks.

## Conclusion

Against the strongest real managers available in the archive, the system looks **genuinely competitive over a full season**: 6,416.35 FP would land inside the published final Top 10, approximately 9th, only 415.75 FP behind the winner. It is **not competitive with the weekly extreme tail**: it beats the round Top-10 average only four times and trails it by 48.13 FP on average.

The most defensible interpretation is that the AI produced a strong, persistent season-long result but lacked the upside of the best one-off human rounds. The archive supports an approximate BasketStories Private League rank; it does not support a global percentile or an exact rank beyond the published standings.
