# Phase 5A historical availability investigation

Generated at `2026-08-18T14:12:10.522640+00:00`. This is a coverage report, not an availability model.

## Measured recovery

| Season | Official URLs found | Successful/partial fetches | Parsed observations | Reliable article timestamps |
| --- | ---: | ---: | ---: | ---: |
| E2022 | 2 | 0 | 0 | 0 |
| E2023 | 0 | 0 | 0 | 0 |
| E2024 | 1 | 0 | 0 | 0 |
| E2025 | 1 | 0 | 0 | 0 |

Canonical observations recovered: **0**; reliable pre-game timing: **0/0**; identity-matched: **0/0**.

Official report pages were confirmed for E2022, E2024, and E2025. No reliable E2023 report index was found. Direct automated retrieval returned HTTP 429 for every bounded attempt; the collector stopped and did not bypass the control. Publication timestamps were not inferred from round numbers or article contents.

Examples of the official source family: [2022-23 Round 26](https://www.euroleaguebasketball.net/news/euroleague-injury-report-round-26/), [2024-25 Round 26](https://www.euroleaguebasketball.net/en/euroleague/news/injury-report-round-26-tae2425/), and [2025-26 Round 21](https://www.euroleaguebasketball.net/euroleague/news/euroleague-injury-report-round-21/).

## Safety decision

The locally recovered historical data are **not sufficient for Phase 5B supervised training**. The pages contain useful official prose, but an URL discovery is not a point-in-time player observation. Until access and publication timestamps can be retained legitimately, Phase 5B can train only on any future timestamped live snapshots accumulated by this system; historical availability should initially use deterministic status rules and explicit UNKNOWN values.

Retrospective phrases such as “missed Round 25” or “last played in Round 22” are not normalized as current pre-game OUT unless the same statement explicitly declares the player unavailable for the target game. Rows captured after tip are marked POST_GAME; missing publication times remain UNKNOWN timing.
