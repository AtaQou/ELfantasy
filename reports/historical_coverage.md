# Selective Historical Coverage

Generated: `2026-08-12T01:53:15.632097+00:00`

The retained policy is complete core data for E2018-E2025 and rich event data only for E2021-E2025. `NOT REQUESTED BY POLICY` is intentional and is not missing coverage.

| Season | Expected completed | Stored games | Player stats | Player rows | Team stats | Team rows | PBP | PBP events | Shots | Shot rows | Roster teams | Memberships | Quarantined | Open errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| E2018 | 260 | 260 | 259/260 (99.62%) | 6,177 | 259/260 (99.62%) | 518 | NOT REQUESTED BY POLICY | 0 | NOT REQUESTED BY POLICY | 0 | 16/16 (100.00%) | 250 | 1 | 1 |
| E2019 | 252 | 252 | 252/252 (100.00%) | 6,001 | 252/252 (100.00%) | 504 | NOT REQUESTED BY POLICY | 0 | NOT REQUESTED BY POLICY | 0 | 18/18 (100.00%) | 298 | 0 | 0 |
| E2020 | 328 | 328 | 328/328 (100.00%) | 7,780 | 328/328 (100.00%) | 656 | NOT REQUESTED BY POLICY | 0 | NOT REQUESTED BY POLICY | 0 | 18/18 (100.00%) | 298 | 0 | 0 |
| E2021 | 299 | 327 | 299/299 (100.00%) | 7,072 | 299/299 (100.00%) | 598 | 299/299 (100.00%) | 151,766 | 299/299 (100.00%) | 43,555 | 18/18 (100.00%) | 293 | 4 | 0 |
| E2022 | 328 | 328 | 328/328 (100.00%) | 7,785 | 328/328 (100.00%) | 656 | 328/328 (100.00%) | 171,870 | 328/328 (100.00%) | 49,409 | 18/18 (100.00%) | 295 | 5 | 0 |
| E2023 | 331 | 331 | 331/331 (100.00%) | 7,883 | 331/331 (100.00%) | 662 | 331/331 (100.00%) | 172,265 | 331/331 (100.00%) | 50,159 | 18/18 (100.00%) | 287 | 4 | 0 |
| E2024 | 330 | 330 | 330/330 (100.00%) | 7,863 | 330/330 (100.00%) | 660 | 330/330 (100.00%) | 176,483 | 330/330 (100.00%) | 51,193 | 18/18 (100.00%) | 296 | 2 | 0 |
| E2025 | 402 | 402 | 402/402 (100.00%) | 9,540 | 402/402 (100.00%) | 804 | 402/402 (100.00%) | 222,976 | 402/402 (100.00%) | 64,137 | 20/20 (100.00%) | 335 | 0 | 0 |

Notes:

- E2021 stores 327 scheduled game records, including 28 source-marked cancelled/unplayed fixtures; the expected-completed denominator is 299.
- E2018 game 21 is the only requested core miss: the result is complete, but the official legacy box endpoint returns an empty/N-D payload. Raw evidence is retained and the game is quarantined.
- Every requested rich-season completed game has both PBP and shot data. Shot coverage does not imply complete free-throw attempts: the source omits missed free throws.
- Fourteen games contain a literal provider clock sentinel (`00:-1` or `-1:00`) and are quarantined for strict clock-dependent work while their raw events remain available.
- E2007-E2017 bulk data are absent by policy. Only small Phase 1 audit samples outside this window remain under `data/samples/`, not in the canonical historical layer.

## Cross-season identity continuity

- Players observed in multiple retained seasons: 528.
- Players observed for multiple teams across the retained window: 281.
- Player-season pairs observed for multiple teams (transfer/reassignment candidates): 27.
- Duplicate canonical rows sharing one official player ID: 0.

## Open endpoint failures

| Season | Game | Component | Class | Attempts |
| --- | ---: | --- | --- | ---: |
| E2018 | 21 | BOXSCORE | `EMPTY_SUPPORTED_ENDPOINT` | 2 |
