# Fantasy identity resolution

Resolved from the complete retained official dataset at `2026-08-12T01:42:12.576131+00:00`. The population is the E2025 Matchday 38 player market; coaches are excluded.

- Confidently matched: **324/345 (93.9%)**
- Ambiguous: **3**
- No official candidate: **16**
- Name/team conflict: **2**
- Other: **0**

No edit-distance score or unconstrained fuzzy match was accepted. New matches require a unique explicit official-name variant, or a unique same-season team+jersey candidate with strong token/prefix compatibility. Previous mappings are version-closed; new mappings are append-versioned.

## Unresolved players

| Classification | Fantasy ID | Player | Team | Jersey | Official candidate(s) |
| --- | ---: | --- | --- | ---: | --- |
| NO_OFFICIAL_CANDIDATE | 8692 | Alex Blanco | Valencia Basket | 35 | — |
| NO_OFFICIAL_CANDIDATE | 9006 | Caspar Vossenberg | FC Bayern Munich | 37 | — |
| NAME_TEAM_CONFLICT | 8679 | Chiek Diallo | Baskonia Vitoria-Gasteiz | 17 | RADZEVICIUS, GYTIS (014190) |
| NO_OFFICIAL_CANDIDATE | 8685 | Daniel Gonzalez | FC Barcelona | 47 | — |
| NO_OFFICIAL_CANDIDATE | 8683 | Diego Ferreras | FC Barcelona | 45 | — |
| NO_OFFICIAL_CANDIDATE | 8688 | Egor Amosov | Real Madrid | 17 | — |
| AMBIGUOUS | 7241 | Gur Lavy | Maccabi Rapyd Tel Aviv | 4 | LAVI, GUR (014120) |
| NO_OFFICIAL_CANDIDATE | 8695 | Ignas Stombergas | Zalgiris Kaunas | 16 | — |
| AMBIGUOUS | 7200 | Jeff Dowtin Jr. | Maccabi Rapyd Tel Aviv | 21 | DOWTIN JR, JEFFREY (014123) |
| NO_OFFICIAL_CANDIDATE | 8684 | Joaquim Boumtje-Boumtje | FC Barcelona | 46 | — |
| NO_OFFICIAL_CANDIDATE | 8693 | Jorge Carot | Valencia Basket | 23 | — |
| NO_OFFICIAL_CANDIDATE | 8686 | Mohamed Dabone | FC Barcelona | 40 | — |
| NO_OFFICIAL_CANDIDATE | 7213 | Nate Mason | Dubai Basketball | 1 | — |
| NO_OFFICIAL_CANDIDATE | 9007 | Nicolas Kodjoe | FC Bayern Munich | 35 | — |
| NO_OFFICIAL_CANDIDATE | 8925 | Nikolas Sermpezis | FC Bayern Munich | 30 | — |
| NO_OFFICIAL_CANDIDATE | 8805 | Ognjen Simjanovski | Crvena Zvezda Meridianbet Belgrade | 19 | — |
| NAME_TEAM_CONFLICT | 8677 | Pedro Souza | Baskonia Vitoria-Gasteiz | 2 | SIMMONS, KOBI (014152) |
| AMBIGUOUS | 4758 | Perry Dozier | Anadolu Efes Istanbul | 15 | DOZIER, PJ (012742) |
| NO_OFFICIAL_CANDIDATE | 8806 | Sava Djuric | Crvena Zvezda Meridianbet Belgrade | 16 | — |
| NO_OFFICIAL_CANDIDATE | 8690 | Tomas Talcis | Valencia Basket | 21 | — |
| NO_OFFICIAL_CANDIDATE | 8769 | Uros Danilovic | Partizan Mozzart Bet Belgrade | 44 | — |

## Mapping policy

- Preserve Fantasy IDs and official EuroLeague IDs in separate namespaces.
- Exact provider-format reversal, suffix removal (`Jr.`, `IV`), and a trailing middle initial are explicit variants, not fuzzy matches.
- A same-team/same-jersey candidate must be unique and still pass strong name compatibility.
- `AMBIGUOUS`, `NAME_TEAM_CONFLICT`, and `NO_OFFICIAL_CANDIDATE` remain unmapped; a missing link is safer than a wrong player history.
- Mid-season transfers are represented by memberships/game teams and never encoded into permanent player identity.
