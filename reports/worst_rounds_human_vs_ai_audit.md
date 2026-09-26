# Why the AI lost its five worst rounds versus strong human managers

## Executive conclusion

The dominant failure was not a broken optimizer or chronic lack of credits. It was a compounding pre-lock forecasting problem:

1. the replay forecasts Fantasy Points **conditional on playing**, while eventual DNPs remain eligible until replay;
2. several selected players and coaches then produced large negative forecast errors;
3. the AI omitted most of each round's extreme upside outcomes; and
4. from MD5 onward, the four-transfer cap made it impossible to repair the whole roster at once, even though every highlighted missed player was individually selectable.

Across the 30 largest missed scorers inspected, 27 finished above the model's pre-round 90th percentile. That makes realized hindsight-oracle gaps look enormous and shows that outcome variance was important. It does not make the losses purely bad luck: the repeat DNP exposure, poor coach outcomes, and several bad uses of scarce transfers—most clearly buying Kendrick Nunn for his MD23 DNP while selling McKinley Wright before 30 FP—were actionable weaknesses in the decision pipeline.

The partial BasketStories evidence also shows a consistent human edge: the round winners captured more mid-price/value upside, used productive bench slots, stacked favorable teams/coaches, and in MD38 put the captain multiplier on the round's top scorer. Exact full human rosters were not published, so this report does not invent the missing selections.

## Scope and method

- Season: E2025 only.
- Rounds: MD23, MD1, MD12, MD5, and MD38, the five largest negative gaps versus the BasketStories Top-10 average.
- Inputs: frozen historical market snapshots, frozen predictions, strategy ledger, current scoring/legality rules, and the prior human benchmark.
- Point in time: all stored feature timestamps are strictly before each round lock. Actual minutes/FP are used only after selection to score and diagnose the replay.
- Human evidence: one BasketStories Private League article per round. These articles identify the winner and sometimes a few players, roles, or a coach, but do not expose complete roster cards.
- Candidate legality: for each of the six largest omitted scorers per round, the existing deterministic optimizer was rerun diagnostically with that player forced into some legal roster. This changes no optimizer code or model.

Role shorthand in roster tables: `C` captain, `S` starter, `6` sixth man, `B` bench. `I→F` means initial selection to the legal between-turn replay result.

## Cross-round attribution

| MD | AI FP | Top-10 avg | Gap | Initial forecast residual¹ | Same-roster role loss² | Selection ceiling³ | Transfer-cap ceiling⁴ | Budget ceiling⁵ | Unused cr. | Missed top-six above P90 |
|---:|------:|-----------:|----:|--------------------------:|-----------------------:|-------------------:|---------------------:|----------------:|-----------:|--------------------------:|
| 23 | 125.25 | 238.51 | -113.26 | -45.87 | 25.50 | 132.85 | 35.50 | 3.30 | 0.1 | 6/6 |
| 1 | 112.25 | 221.06 | -108.80 | -38.25 | 5.05 | 143.75 | 0.00 | 23.60 | 0.0 | 6/6 |
| 12 | 122.25 | 210.04 | -87.79 | -38.75 | 33.55 | 91.75 | 51.30 | 4.45 | 0.9 | 4/6 |
| 5 | 143.20 | 226.22 | -83.02 | -11.62 | 11.50 | 83.10 | 42.45 | 17.75 | 0.0 | 5/6 |
| 38 | 168.45 | 249.53 | -81.08 | -38.01 | 4.95 | 116.30 | 46.45 | 0.00 | 0.0 | 6/6 |

1. Actual minus predicted score for the initial lineup, including coach and initial multipliers. It is the cleanest selected-forecast miss signal, not an additive causal loss after substitutions.
2. Best realized role assignment on the same roster minus the actual legal replay score. This is an exact hindsight upper bound for captain/starter/bench decisions.
3. Best realized legal roster under the actual budget and transfer cap minus the best same-roster role score. It mixes prediction misses and irreducible outcome variance.
4. Marginal hindsight gain from removing only the transfer cap at the actual budget.
5. Marginal hindsight gain from removing the budget after removing the transfer cap.

These columns overlap and must not be added. The selection, transfer, and budget values use realized outcomes and are ceilings, not achievable pre-lock forecasts. The six omitted upside surprises had aggregate actual-minus-predicted FP of 115.89 (MD23), 112.27 (MD1), 104.82 (MD12), 95.47 (MD5), and 125.37 (MD38), but a legal roster could not necessarily capture all six or all their raw FP.

The stochastic strategy was not systematically worse than the deterministic expected-FP alternative on these rounds: its realized score was +15.20, +8.20, 0.00, -14.75, and +16.25 FP different on MD1, MD5, MD12, MD23, and MD38 respectively—an average advantage of 4.98 FP.

## MD23 — 125.25 vs 238.51 (-113.26)

Budget was 131.8 credits, with 0.1 unused. The AI used all four transfers: in came Kendrick Nunn, Dan Oturu, Edgaras Ulanovas, and coach Svetislav Pesic; out went McKinley Wright, Jaylen Hoard, Mbaye Ndiaye, and coach Giuseppe Poeta.

### AI roster

| Player | Pos. | Role I→F | Cr. | Pred FP | Actual FP |
|---|---|---|---:|---:|---:|
| Nikola Milutinov | C | C→C | 17.5 | 20.44 | 24.2 |
| Dan Oturu | C | S→S | 14.0 | 18.22 | 15.4 |
| Sasha Vezenkov | F | S→S | 20.3 | 23.53 | 35.2 |
| Edgaras Ulanovas | F | B→B | 8.1 | 9.62 | 5.5 |
| Edwin Jackson | F | B→B | 4.0 | 6.19 | 12.0 |
| Shaquille Harrison | F | B→B | 6.9 | 8.37 | 2.0 |
| Kendrick Nunn | G | S→S | 17.0 | 20.36 | 0.0 |
| Mike James | G | S→S | 17.1 | 20.28 | 13.0 |
| Wade Baldwin | G | 6→6 | 14.6 | 17.55 | 0.0 |
| Iffe Lundberg | G | B→B | 7.9 | 11.14 | 17.0 |
| **Coach: Svetislav Pesic** | — | — | 4.3 | 12.64 | -5.0 |

The two guard DNPs were decisive. Nunn was projected for 28.68 minutes and 20.36 FP; Baldwin for 25.45 minutes and 17.55 FP. Both scored zero. Mike James added another -7.28 FP forecast residual, and Pesic missed his coach forecast by -17.64. Nunn and Baldwin alone created -37.91 raw selected-player forecast error.

The biggest omitted scorers were all in the locked market and all individually legal under the actual budget and transfer cap:

| Missed player | Pos. rank by prediction | Cr. | Pred FP | Actual FP | Pred min → actual min | Legal? |
|---|---:|---:|---:|---:|---:|---|
| Ebuka Izundu | C37 | 7.6 | 6.58 | 39.6 | 14.18 → 21.20 | Yes |
| Kameron Taylor | F8 | 11.6 | 15.40 | 36.3 | 23.29 → 21.68 | Yes |
| Moses Wright | C5 | 13.0 | 14.70 | 30.8 | 23.17 → 25.35 | Yes |
| Talen Horton-Tucker | G15 | 11.9 | 14.71 | 30.8 | 23.36 → 23.62 | Yes |
| McKinley Wright | G10 | 14.3 | 16.46 | 30.0 | 28.90 → 28.88 | Yes |

BasketStories says the winning Melbourniakos 13 roster used Kameron Taylor, coach Pedro Martínez, and Horton-Tucker on the bench. Taylor and Horton-Tucker both landed above the model's P90. Pedro Martínez scored 20 coach FP after a 12-point prediction; the AI coach scored -5. The most concrete human edge was therefore a successful Valencia player/coach angle plus productive bench upside. The article does not publish the remaining roster or captain. [BasketStories MD23 source](https://www.basketstories.net/article.php?p=private_league_post_5854)

**Cause labels:** `AVAILABILITY/DNP`, `PREDICTION_ERROR`, `TRANSFER_CONSTRAINT`, `HUMAN_BETTER_TRANSFER_TIMING`, `HUMAN_BETTER_PLAYER_IDENTIFICATION`, `CAPTAIN_STARTER_DECISION`, `OUTCOME_VARIANCE`.

**Judgment:** mixed, but mostly preventable exposure amplified by variance. Buying a DNP and selling a 30-FP guard was a major transfer-timing error; six of the six highlighted alternatives were also upside-tail outcomes.

## MD1 — 112.25 vs 221.06 (-108.80)

This was the initial 100-credit build, so no carry-over transfer restriction applied and no credits were unused.

### AI roster

| Player | Pos. | Role I→F | Cr. | Pred FP | Actual FP |
|---|---|---|---:|---:|---:|
| Mbaye Ndiaye | C | S→S | 6.5 | 8.54 | 10.0 |
| Laurynas Birutis | C | B→B | 6.5 | 7.80 | 12.1 |
| Nikola Mirotic | F | 6→6 | 15.5 | 16.46 | 23.0 |
| Dejan Davidovac | F | S→B | 5.0 | 6.30 | 1.0 |
| David Lighty | F | B→S | 5.0 | 6.18 | 6.0 |
| Panagiotis Kalaitzakis | F | S→B | 4.1 | 4.12 | 0.0 |
| Theo Maledon | G | C→B | 13.3 | 14.91 | 0.0 |
| Mike James | G | B→C | 15.2 | 15.94 | 21.0 |
| Shane Larkin | G | S→S | 13.6 | 14.93 | 13.2 |
| Wade Baldwin | G | B→S | 10.3 | 12.53 | 16.5 |
| **Coach: Pierric Poupet** | — | — | 5.0 | 6.85 | -5.0 |

The legal turn replay rescued the initial lineup from 70.00 to 112.25 by benching the DNP captain Maledon, promoting Mike James to captain, and making three substitutions. That is why the same-roster role ceiling is only 5.05 FP despite the disastrous initial captain. The larger issue was roster construction: low-output forwards, two DNPs, and a losing coach left no ceiling. The AI captured none of the round's actual top ten.

| Missed player | Pos. rank by prediction | Cr. | Pred FP | Actual FP | Pred min → actual min | Legal? |
|---|---:|---:|---:|---:|---:|---|
| Kendrick Nunn | G2 | 15.6 | 15.64 | 31.9 | 25.42 → 33.87 | Yes |
| Moses Wright | C22 | 10.5 | 7.99 | 30.8 | 16.85 → 23.43 | Yes |
| Will Clyburn | F4 | 13.0 | 14.55 | 30.0 | 23.60 → 24.83 | Yes |
| Nikola Milutinov | C6 | 12.4 | 11.97 | 29.7 | 19.70 → 24.70 | Yes |
| Sasha Vezenkov | F2 | 16.0 | 15.49 | 29.7 | 27.19 → 28.55 | Yes |
| Davis Bertans | F81 | 9.4 | 2.79 | 28.6 | 17.55 → 26.25 | Yes |

Several strong options were already highly ranked—Nunn G2, Clyburn F4, Vezenkov F2—so this was not only unknowable variance. Yet all six highlighted misses exceeded P90, and Bertans was a true low-probability eruption. The winning King of basketball article does not name players; it only states that none of the winner's starters scored below 25 FP. That supports a ceiling contrast but not a roster-by-roster comparison. [BasketStories MD1 source](https://www.basketstories.net/article.php?p=private_league_post_5564)

**Cause labels:** `AVAILABILITY/DNP`, `PREDICTION_ERROR`, `OPTIMIZER_SELECTION`, `OUTCOME_VARIANCE`.

**Judgment:** roughly half structural and half variance. The DNP exposure and omission of already-high-ranked stars were avoidable signals; the fact that every highlighted miss went above P90 was not normally predictable.

## MD12 — 122.25 vs 210.04 (-87.79)

Budget was 115.1 credits with 0.9 unused. All four transfers were used: Trent Forrest, Mouhamet Diouf, Sasha Vezenkov, and coach Nenad Jakovljevic came in; Shane Larkin, Dwayne Bacon, Mam Jaiteh, and coach Sasa Obradovic went out. The three outgoing players also scored zero, so the realized problem was not simply that the sold assets exploded. It was the opportunity cost of a capped four-move rebuild and the roles assigned to the new roster.

### AI roster

| Player | Pos. | Role I→F | Cr. | Pred FP | Actual FP |
|---|---|---|---:|---:|---:|
| Nikola Milutinov | C | S→S | 15.9 | 20.30 | 19.8 |
| Mouhamet Diouf | C | B→B | 7.4 | 11.81 | 17.6 |
| Sasha Vezenkov | F | C→C | 17.5 | 21.00 | 6.6 |
| Trey Lyles | F | S→S | 13.6 | 18.81 | 24.2 |
| Ercan Osmani | F | B→B | 9.0 | 13.46 | 20.9 |
| Edwin Jackson | F | B→B | 4.1 | 7.01 | 0.0 |
| Tyler Dorsey | G | S→S | 11.5 | 17.67 | 13.2 |
| Wade Baldwin | G | S→S | 12.0 | 17.03 | 23.1 |
| Trent Forrest | G | 6→6 | 10.1 | 15.57 | 0.0 |
| Aleksa Avramovic | G | B→B | 8.3 | 10.79 | -1.0 |
| **Coach: Nenad Jakovljevic** | — | — | 4.8 | 8.07 | 10.0 |

This is the clearest captain/starter failure. The captain Vezenkov scored 6.6, Forrest DNP'd as sixth man, Avramovic scored -1, and the productive Osmani remained on the bench. Perfect hindsight roles on this exact roster add 33.55 FP—the largest role gap of the five rounds. The fresh Vezenkov/Forrest pair contributed only 6.6 raw FP against 36.57 predicted.

| Missed player | Pos. rank by prediction | Cr. | Pred FP | Actual FP | Pred min → actual min | Legal? |
|---|---:|---:|---:|---:|---:|---|
| Justin Robinson | G24 | 11.8 | 13.16 | 39.0 | 18.48 → 28.00 | Yes |
| Theo Maledon | G15 | 13.2 | 14.64 | 37.4 | 20.79 → 21.50 | Yes |
| Sylvain Francisco | G7 | 15.8 | 16.66 | 37.0 | 24.82 → 26.27 | Yes |
| Kendrick Nunn | G6 | 15.0 | 16.72 | 28.6 | 27.43 → 31.98 | Yes |
| Facundo Campazzo | G20 | 12.9 | 13.42 | 27.5 | 24.33 → 24.55 | Yes |

BasketStories explicitly credits winner Lion City Greens with Nunn (28.6), Kenneth Faried (25.3), Osmani (20.9), and Nick Weiler-Babb (20.9). The AI owned only Osmani and left him at the 0.5 bench multiplier. The four named human players totaled 95.7 raw FP before their unpublished role multipliers. Captain, starters, full roster, and transfers are unavailable. [BasketStories MD12 source](https://www.basketstories.net/article.php?p=private_league_post_5713)

**Cause labels:** `AVAILABILITY/DNP`, `PREDICTION_ERROR`, `MINUTES_ROLE_ERROR`, `CAPTAIN_STARTER_DECISION`, `TRANSFER_CONSTRAINT`, `HUMAN_BETTER_PLAYER_IDENTIFICATION`, `OUTCOME_VARIANCE`.

**Judgment:** mostly predictable lineup/role damage, with a substantial tail component. Better use of the same roster was worth up to 33.55 FP; four of the six largest omitted scorers were above P90.

## MD5 — 143.20 vs 226.22 (-83.02)

Budget was 105.3 credits with none unused. Three of four transfers were used: Devon Hall, Edwin Jackson, and coach Tomas Masiulis came in; Vladimir Lucic, Yakuba Ouattara, and coach Julius Thomas went out.

### AI roster

| Player | Pos. | Role I→F | Cr. | Pred FP | Actual FP |
|---|---|---|---:|---:|---:|
| Nikola Milutinov | C | S→S | 13.4 | 18.06 | 33.0 |
| Mbaye Ndiaye | C | B→B | 6.6 | 8.89 | 11.0 |
| Sasha Vezenkov | F | C→C | 17.2 | 22.25 | 34.1 |
| Ercan Osmani | F | B→B | 7.8 | 13.56 | 6.0 |
| Edwin Jackson | F | B→B | 4.4 | 6.50 | 13.0 |
| David Lighty | F | B→B | 5.7 | 8.38 | 6.0 |
| Wade Baldwin | G | S→S | 11.3 | 17.25 | 17.6 |
| Devon Hall | G | S→S | 9.5 | 14.86 | 15.4 |
| Trent Forrest | G | S→S | 10.8 | 15.99 | 0.0 |
| Omari Moore | G | 6→6 | 11.7 | 15.56 | 1.0 |
| **Coach: Tomas Masiulis** | — | — | 6.9 | 9.94 | -10.0 |

The top end worked: Vezenkov was an excellent captain, and Milutinov also hit 33.0. The roster collapsed lower down: starter Forrest DNP'd, sixth man Moore scored 1.0, and the newly acquired coach scored -10. Those three positions prevented the AI from converting two captured top scorers into a competitive round.

| Missed player | Pos. rank by prediction | Cr. | Pred FP | Actual FP | Pred min → actual min | Legal? |
|---|---:|---:|---:|---:|---:|---|
| Cedi Osman | F17 | 12.1 | 13.77 | 33.0 | 27.51 → 33.58 | Yes |
| Dan Oturu | C4 | 11.3 | 15.12 | 31.9 | 22.83 → 29.70 | Yes |
| Filip Petrusev | F3 | 13.1 | 17.15 | 30.8 | 25.19 → 23.18 | Yes |
| Carsen Edwards | G10 | 13.9 | 15.77 | 30.8 | 25.30 → 26.72 | Yes |
| Arnas Butkevicius | F54 | 6.4 | 7.32 | 28.0 | 20.70 → 27.42 | Yes |

BasketStories says winner Avengers owned both Kamar and Wade Baldwin and kept Jordan Nwora and Dan Oturu on the bench, plus an unnamed Olympiacos trio. The AI shared Wade and clearly found the correct Olympiacos core in Vezenkov/Milutinov, but the human had much stronger depth: even at the 0.5 bench multiplier, Nwora and Oturu yielded 26.95 FP together. Kamar Baldwin was a 6.8-credit upside pick who scored 19.0 after a 6.82 prediction. No full roster, captain, coach, or transfer list is published. [BasketStories MD5 source](https://www.basketstories.net/article.php?p=private_league_post_5616)

**Cause labels:** `AVAILABILITY/DNP`, `PREDICTION_ERROR`, `TRANSFER_CONSTRAINT`, `HUMAN_BETTER_PLAYER_IDENTIFICATION`, `HUMAN_MORE_AGGRESSIVE_RISK`, `OUTCOME_VARIANCE`.

**Judgment:** the captain strategy was good; weak depth, one DNP, and a -10 coach did the damage. Human value identification was better, but five of six highlighted omissions were still P90-breaking outcomes.

## MD38 — 168.45 vs 249.53 (-81.08)

Budget was 149.1 credits with none unused. All four transfers were used: Sasha Vezenkov, Dzanan Musa, Eugene Omoruyi, and coach Oded Kattash came in; Chima Moneke, Jordan Nwora, Josh Nebo, and coach Aleksander Sekulic went out.

### AI roster

| Player | Pos. | Role I→F | Cr. | Pred FP | Actual FP |
|---|---|---|---:|---:|---:|
| Eugene Omoruyi | C | S→B | 10.1 | 13.54 | 8.0 |
| Mfiondu Kabengele | C | B→S | 15.8 | 17.70 | 12.0 |
| Sasha Vezenkov | F | C→B | 21.1 | 21.28 | 16.5 |
| Jaylen Hoard | F | S→B | 15.2 | 19.42 | 10.0 |
| Braian Angola | F | S→S | 12.3 | 16.64 | 24.0 |
| Dzanan Musa | F | B→S | 13.1 | 17.38 | 24.0 |
| Mike James | G | B→C | 17.0 | 20.60 | 30.8 |
| Carlik Jones | G | S→S | 13.4 | 19.73 | 22.0 |
| Jean Montero | G | 6→6 | 16.2 | 17.82 | 6.6 |
| Jordan Loyd | G | B→B | 10.7 | 12.62 | 12.0 |
| **Coach: Oded Kattash** | — | — | 4.2 | 12.15 | -5.0 |

The legal turn replay worked well: it changed captain from Vezenkov to Mike James, made three substitutions, and lifted the initial realized score from 138.00 to 168.45. Perfect hindsight roles on this roster add only another 4.95 FP. This was therefore not mainly a starter/bench execution failure. The coach missed by -17.15, but the larger hindsight gap came from omitted upside.

| Missed player | Pos. rank by prediction | Cr. | Pred FP | Actual FP | Pred min → actual min | Legal? |
|---|---:|---:|---:|---:|---:|---|
| Moses Wright | C1 | 15.8 | 18.85 | 47.3 | 26.72 → 27.38 | Yes |
| Will Clyburn | F8 | 12.3 | 16.37 | 36.3 | 26.75 → 26.02 | Yes |
| Shaquille Harrison | F31 | 7.5 | 11.26 | 33.0 | 21.96 → 31.23 | Yes |
| Nikola Milutinov | C3 | 17.1 | 18.75 | 30.8 | 20.91 → 24.33 | Yes |
| Jaron Blossomgame | F22 | 10.1 | 13.47 | 30.8 | 27.09 → 27.08 | Yes |
| Youssoupha Fall | C46 | 4.0 | 4.93 | 30.8 | 5.40 → 23.23 | Yes |

This is the strongest evidence of a human captain edge. BasketStories explicitly says winner Nigel Williams-Vagoss captained Moses Wright. Under the audited rules, Wright alone contributed 94.6 captain FP. The winner also had Matthew Strazel (23.1) and Jaron Blossomgame (30.8). The article says only “Edwards”; both Carsen and Kessler Edwards existed in the market, so this audit does not choose between them. Carsen scored 27.5 and Kessler 3.0. The full roster and coach remain unavailable. [BasketStories MD38 source](https://www.basketstories.net/article.php?p=private_league_post_6025)

**Cause labels:** `PREDICTION_ERROR`, `OPTIMIZER_SELECTION`, `CAPTAIN_STARTER_DECISION`, `TRANSFER_CONSTRAINT`, `HUMAN_BETTER_PLAYER_IDENTIFICATION`, `HUMAN_MORE_AGGRESSIVE_RISK`, `OUTCOME_VARIANCE`.

**Judgment:** mostly an omitted-upside/variance round, with a real selection signal. All six headline misses exceeded P90, but Wright was already the model's top-ranked center, was individually feasible, and became the human winner's captain. The AI's within-roster turn decisions were nearly optimal.

## What stronger humans consistently did better

Only partial lineups are recoverable, so “consistently” here refers to the published fragments, not unobserved roster slots:

- **Captured playable depth.** MD5's Nwora/Oturu bench produced 26.95 counted FP; the AI carried a starting DNP, a 1-FP sixth man, and a -10 coach.
- **Converted favorable team reads into multiple slots.** The clearest example is MD23's Taylor plus Pedro Martínez, with Horton-Tucker retained on the bench.
- **Accepted more upside in mid-price/value selections.** Kamar Baldwin at 6.8 credits and the MD23 Valencia choices outperformed their central forecasts.
- **Used the multiplier on the slate-winning result.** MD38 captain Moses Wright generated 94.6 FP. The AI did successfully switch its captain to Mike James, but it did not roster Wright despite his C1 model rank.
- **Avoided at least some of the AI's dead slots.** BasketStories explicitly reports that the MD1 winner had no starter below 25 FP, while the AI needed turn recourse to escape a DNP captain and still fielded a low-ceiling lineup.

What cannot be established from the archive: exact human-vs-AI transfer differences, full budget structure, all starter/bench assignments, or a complete count of top scorers captured. Those comparisons are intentionally not fabricated.

## Root-cause synthesis

| Cause | Evidence across the five rounds | Assessment |
|---|---|---|
| `PREDICTION_ERROR` | Initial selected-lineup residual totaled -172.50 FP; four coaches alone missed their combined forecasts by 66.58 FP. | Major, repeated |
| `AVAILABILITY/DNP` | Maledon/Kalaitzakis (MD1), Forrest (MD5/12), and Nunn/Baldwin (MD23) produced zero after positive conditional-on-playing forecasts. | Major, repeated |
| `OUTCOME_VARIANCE` | 27/30 largest omitted scorers exceeded pre-round P90. | Major amplifier |
| `TRANSFER_CONSTRAINT` | Removing the cap adds 35.50–51.30 hindsight FP in MD5/12/23/38; zero effect on the unrestricted MD1 build. | Important after MD1, but a hindsight ceiling |
| `HUMAN_BETTER_TRANSFER_TIMING` | Directly evidenced on the AI side most strongly by Nunn in / McKinley Wright out on MD23; exact human transfers are unpublished. | Clear in MD23, not provable generally |
| `CAPTAIN_STARTER_DECISION` | Same-roster hindsight loss totals 80.55 FP, concentrated in MD12 (33.55) and MD23 (25.50); only 4.95 on MD38 after legal recourse. | Secondary but material |
| `MINUTES_ROLE_ERROR` | Several zero/short-minute outcomes were projected for normal roles; other missed eruptions included large minute surprises such as Youssoupha Fall. | Material, intertwined with availability |
| `OPTIMIZER_SELECTION` | All highlighted players were individually feasible, including MD38 C1 Moses Wright, but the stochastic strategy beat the deterministic expected-FP roster by 4.98 FP per round on average. | Specific misses, no systematic optimizer failure shown |
| `BUDGET_CONSTRAINT` | Almost all credits were spent, but budget-removal ceilings were small in MD12/23/38 and no highlighted player was individually blocked. | Not dominant |

## Highest-payoff improvement areas

No fixes are implemented here. Based strictly on these five rounds, the likely payoff order is:

1. **Predictions** — especially integrating availability/DNP risk into the score used for selection and improving coach/player tail calibration. This directly addresses the repeated dead slots and the -172.50 selected forecast residual.
2. **Transfer strategy** — treat uncertain availability and downside risk as especially costly when spending the full four-transfer allowance; MD23 is the clearest case, and transfer constraints had large hindsight ceilings in every audited post-opener round.
3. **Captain/starter strategy** — worth a meaningful but smaller exact 80.55-FP same-roster ceiling, heavily concentrated in MD12 and MD23. The existing turn replay already handled MD1 and MD38 well.
4. **Minutes/role** — ordinary role errors matter, but many of the largest “minutes” misses were actually conditional-on-playing forecasts attached to DNPs, so fixing availability first has more direct leverage.
5. **Optimizer logic** — investigate individual selections such as MD38 Wright, but there is no evidence here of a general solver/objective defect; the stochastic roster outscored the deterministic expected-FP alternative on average.

**Bottom line:** large losses occur when the AI combines one or two dead/highly disappointing selected slots—often a DNP or losing coach—with a round in which omitted mid-price players realize extreme upside. Strong human managers appear to have identified those upside pockets and captain opportunities better, while the AI's scarce transfers and conditional-on-playing forecasts left it exposed to downside it was not pricing.

## Verification

The focused notebook checks passed for:

- exact alignment of MD1, MD5, MD12, MD23, and MD38;
- feature timestamps strictly before each lock;
- legality of every initial and replay-final roster;
- exact reproduction of frozen static and final scores;
- attribution identities;
- forced-candidate feasibility under each round's stated constraints; and
- one BasketStories provenance URL per audited round.

No model was retrained, no feature or optimizer logic was modified, and the full test suite was not run. Reproducible calculations are in `notebooks/worst_rounds_human_vs_ai_audit.ipynb`.
