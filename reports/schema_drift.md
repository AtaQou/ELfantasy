# Retained-History Schema Drift

Generated: `2026-08-12T01:53:15.632097+00:00`

## Field-set result

The immutable retained responses were inspected season by season. Within the selected window, the row-level field sets are stable:

| Source | Seasons compared | Row signatures | Stable fields |
| --- | --- | ---: | --- |
| Legacy player box score | E2018-E2025 | 1 | `Player_ID`, player/team, starter/playing flags, minutes, shooting, rebounds, assists, steals, blocks/blocks received, turnovers, fouls, points, PIR, plus-minus |
| v1 roster membership | E2018-E2025 | 1 | person/club/season, active/start/end, jersey, position/type, external ID and images |
| Play-by-play event | E2021-E2025 | 1 | team/player, period container, minute/clock, play number/type/info, score and comment |
| Shot event | E2021-E2025 | 1 | player/team, action/event IDs, minute/console clock, coordinates/zone, points and context flags |

The canonical normalizers therefore did not have to merge renamed fields or incompatible datatypes across this retained window. Optional/null values remain null rather than being synthesized.

## Meaningful structural differences and source anomalies

- **E2018/21:** the supported box endpoint returns an empty/N-D structure even though results show Khimki 84-85 Istanbul. It is a requested-but-missing core record and remains quarantined.
- **E2019:** the source schedule contains more fixtures than the 252 played results because the season was curtailed; expected coverage uses played results, not schedule row count.
- **E2021:** 327 schedule/game records versus 299 completed results. The 28 cancelled records are retained explicitly instead of being mislabelled as missing stats.
- **E2021-E2024 PBP:** fourteen games include literal invalid clock sentinels (`00:-1`/`-1:00`). The values are preserved and the affected games quarantined for clock-sensitive calculations.
- Raw PBP uses the provider spelling `ForthQuarter`; overtime is a flat `ExtraTime` array. Canonical normalization handles period boundaries but retains source period and source sequence.
- Provider event numbers are not globally chronological. The canonical table retains both `raw_event_number` and source response order.
- The shot feed consistently omits missed free throws. Absence of a shot row must never be interpreted as a make.
- The v2 winner field remains excluded from canonical truth; future winner queries derive it from validated scores.

## Identity/code drift

- Numeric live player IDs may contain a leading `P` while roster/v3 IDs do not. Only this demonstrated representation difference is canonicalized; raw IDs are preserved.
- Team display names and codes vary by provider/season. Versioned `team_aliases`, including the reviewed Baskonia/Fantaking aliases, resolve them centrally.
- No duplicate canonical player records share an official player ID. Name collisions are not merged because names are not identity keys.

There is no evidence in the retained window that any season is broadly unsuitable for player-game modelling because of schema drift. E2018 has one quarantined missing box score; individual quarantined PBP clocks affect only clock-dependent rich calculations, not the corresponding box-score facts.
