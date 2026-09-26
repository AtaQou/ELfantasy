"""Pure normalization and audit helpers for observed EuroLeague responses."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from xml.etree import ElementTree


PLAYER_STAT_FIELDS = {
    "Points": "points",
    "FieldGoalsMade2": "two_pointers_made",
    "FieldGoalsAttempted2": "two_pointers_attempted",
    "FieldGoalsMade3": "three_pointers_made",
    "FieldGoalsAttempted3": "three_pointers_attempted",
    "FreeThrowsMade": "free_throws_made",
    "FreeThrowsAttempted": "free_throws_attempted",
    "OffensiveRebounds": "offensive_rebounds",
    "DefensiveRebounds": "defensive_rebounds",
    "TotalRebounds": "total_rebounds",
    "Assistances": "assists",
    "Steals": "steals",
    "Turnovers": "turnovers",
    "BlocksFavour": "blocks",
    "BlocksAgainst": "blocks_received",
    "FoulsCommited": "fouls_committed",
    "FoulsReceived": "fouls_drawn",
    "Valuation": "pir",
    "Plusminus": "plus_minus",
}


def strip_string(value: Any) -> Any:
    """Strip API padding while leaving non-string values unchanged."""

    return value.strip() if isinstance(value, str) else value


def display_person_name(value: Any) -> str | None:
    """Render official ``SURNAME, GIVEN`` names for the user interface."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.strip().split())
    if "," in text:
        surname, given = (part.strip() for part in text.split(",", 1))
        text = " ".join(part for part in (given, surname) if part)
    return text.title() if text.isupper() else text


def parse_minutes(value: Any) -> float | None:
    """Convert ``MM:SS`` to decimal minutes; DNP/blank values become None."""

    if not isinstance(value, str) or not value or value.upper() == "DNP":
        return None
    try:
        minutes, seconds = value.split(":", maxsplit=1)
        return round(int(minutes) + int(seconds) / 60, 4)
    except (ValueError, TypeError):
        return None


def normalize_games(games: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in games:
        local = _mapping(game.get("local"))
        road = _mapping(game.get("road"))
        local_club = _mapping(local.get("club"))
        road_club = _mapping(road.get("club"))
        season = _mapping(game.get("season"))
        phase = _mapping(game.get("phaseType"))
        venue = _mapping(game.get("venue"))
        local_score = local.get("score")
        road_score = road.get("score")
        derived_winner = None
        if isinstance(local_score, (int, float)) and isinstance(road_score, (int, float)):
            if local_score > road_score:
                derived_winner = local_club.get("code")
            elif road_score > local_score:
                derived_winner = road_club.get("code")

        winner = _mapping(game.get("winner"))
        local_partials = _mapping(local.get("partials"))
        road_partials = _mapping(road.get("partials"))
        rows.append(
            {
                "season": season.get("year"),
                "season_code": season.get("code"),
                "competition_code": season.get("competitionCode"),
                "phase_code": phase.get("code"),
                "round": game.get("round"),
                "game_id": game.get("id"),
                "game_identifier": game.get("identifier"),
                "game_code": game.get("gameCode"),
                "played": game.get("played"),
                "game_status": game.get("gameStatus"),
                "date": game.get("date"),
                "local_date": game.get("localDate"),
                "utc_date": game.get("utcDate"),
                "home_team_id": local_club.get("code"),
                "home_team_name": local_club.get("name"),
                "away_team_id": road_club.get("code"),
                "away_team_name": road_club.get("name"),
                "home_score": local_score,
                "away_score": road_score,
                "venue_code": venue.get("code"),
                "venue_name": venue.get("name"),
                "venue_capacity": venue.get("capacity"),
                "neutral_venue": game.get("isNeutralVenue"),
                "overtime_periods": max(
                    _extra_period_count(local_partials.get("extraPeriods")),
                    _extra_period_count(road_partials.get("extraPeriods")),
                ),
                "api_winner_code": winner.get("code"),
                "derived_winner_code": derived_winner,
            }
        )
    return rows


def normalize_player_boxscore(
    payload: Mapping[str, Any],
    game: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    home_code = _mapping(_mapping(game.get("local")).get("club")).get("code")
    away_code = _mapping(_mapping(game.get("road")).get("club")).get("code")
    for side_index, side in enumerate(_list(payload.get("Stats"))):
        side_data = _mapping(side)
        home_away = "home" if side_index == 0 else "away"
        opponent = away_code if home_away == "home" else home_code
        for player in _list(side_data.get("PlayersStats")):
            item = _mapping(player)
            minutes_raw = strip_string(item.get("Minutes"))
            row: dict[str, Any] = {
                "season_code": _mapping(game.get("season")).get("code"),
                "round": game.get("round"),
                "game_id": game.get("id"),
                "game_code": game.get("gameCode"),
                "game_utc_date": game.get("utcDate"),
                "player_id": strip_string(item.get("Player_ID")),
                "player_name": strip_string(item.get("Player")),
                "team_id": strip_string(item.get("Team")),
                "opponent_id": opponent,
                "home_away": home_away,
                "jersey_number": strip_string(item.get("Dorsal")),
                "is_starter": _as_bool(item.get("IsStarter")),
                "is_playing_flag": _as_bool(item.get("IsPlaying")),
                "minutes_raw": minutes_raw,
                "minutes": parse_minutes(minutes_raw),
                "did_not_play": isinstance(minutes_raw, str)
                and minutes_raw.upper() == "DNP",
            }
            for source, target in PLAYER_STAT_FIELDS.items():
                row[target] = item.get(source)
            rows.append(row)
    return rows


def normalize_team_boxscore(
    payload: Mapping[str, Any],
    game: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    home_code = _mapping(_mapping(game.get("local")).get("club")).get("code")
    away_code = _mapping(_mapping(game.get("road")).get("club")).get("code")
    for side_index, side in enumerate(_list(payload.get("Stats"))):
        side_data = _mapping(side)
        players = _list(side_data.get("PlayersStats"))
        team_code = strip_string(_mapping(players[0]).get("Team")) if players else None
        home_away = "home" if side_index == 0 else "away"
        opponent = away_code if home_away == "home" else home_code
        total = _mapping(side_data.get("totr"))
        team_misc = _mapping(side_data.get("tmr"))
        row: dict[str, Any] = {
            "season_code": _mapping(game.get("season")).get("code"),
            "round": game.get("round"),
            "game_id": game.get("id"),
            "game_code": game.get("gameCode"),
            "team_id": team_code,
            "team_name": strip_string(side_data.get("Team")),
            "opponent_id": opponent,
            "home_away": home_away,
            "coach": strip_string(side_data.get("Coach")),
            "minutes_raw": strip_string(total.get("Minutes")),
            "team_rebounds_offensive": team_misc.get("OffensiveRebounds"),
            "team_rebounds_defensive": team_misc.get("DefensiveRebounds"),
            "team_rebounds_total": team_misc.get("TotalRebounds"),
        }
        for source, target in PLAYER_STAT_FIELDS.items():
            row[target] = total.get(source)
        rows.append(row)
    return rows


def normalize_play_by_play(
    payload: Mapping[str, Any],
    *,
    season_code: str,
    game_code: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def append_event(event: Any, quarter: int, source_period: str) -> None:
        # NUMBEROFPLAY is not reliably monotonic (period-start rows can be
        # numbered after substitutions).  Keep an explicit response-order key.
        row = _normalize_pbp_event(event, season_code, game_code, quarter)
        row["source_sequence"] = len(rows) + 1
        row["source_period"] = source_period
        rows.append(row)

    quarter_keys = [
        ("FirstQuarter", 1),
        ("SecondQuarter", 2),
        ("ThirdQuarter", 3),
        ("ForthQuarter", 4),
    ]
    for key, quarter in quarter_keys:
        for event in _list(payload.get(key)):
            append_event(event, quarter, key)

    extra_time = _list(payload.get("ExtraTime"))
    if extra_time and all(isinstance(period, list) for period in extra_time):
        for overtime_offset, period in enumerate(extra_time, start=1):
            for event in period:
                append_event(event, 4 + overtime_offset, f"ExtraTime{overtime_offset}")
    else:
        for event in extra_time:
            event_data = _mapping(event)
            minute = event_data.get("MINUTE")
            try:
                elapsed_minute = int(float(minute))
            except (TypeError, ValueError):
                elapsed_minute = 41
            overtime_offset = max(0, (elapsed_minute - 41) // 5)
            # The flat legacy feed numbers an OT closing marker with the first
            # elapsed minute of the next period: EP at 46 closes OT1, while a
            # timed 05:00 row or BP at 46 starts OT2.  The final EP/EG pair at
            # minute 61 therefore belongs to OT4 rather than a phantom OT5.
            if (
                strip_string(event_data.get("PLAYTYPE")) in {"EP", "EG"}
                and elapsed_minute > 41
                and (elapsed_minute - 41) % 5 == 0
            ):
                overtime_offset = max(0, overtime_offset - 1)
            quarter = 5 + overtime_offset
            append_event(event, quarter, "ExtraTime")
    return rows


def normalize_shots(
    payload: Mapping[str, Any],
    *,
    season_code: str,
    game_code: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shot in _list(payload.get("Rows")):
        item = _mapping(shot)
        rows.append(
            {
                "season_code": season_code,
                "game_code": game_code,
                "event_number": item.get("NUM_ANOT"),
                "team_id": strip_string(item.get("TEAM")),
                "player_id": strip_string(item.get("ID_PLAYER")),
                "player_name": strip_string(item.get("PLAYER")),
                "action_id": strip_string(item.get("ID_ACTION")),
                "action": item.get("ACTION"),
                "points": item.get("POINTS"),
                "coordinate_x": item.get("COORD_X"),
                "coordinate_y": item.get("COORD_Y"),
                "zone": item.get("ZONE"),
                "fast_break": item.get("FASTBREAK"),
                "second_chance": item.get("SECOND_CHANCE"),
                "points_off_turnover": item.get("POINTS_OFF_TURNOVER"),
                "elapsed_minute": item.get("MINUTE"),
                "game_clock": item.get("CONSOLE"),
                "home_score": item.get("POINTS_A"),
                "away_score": item.get("POINTS_B"),
                "utc_timestamp_raw": item.get("UTC"),
            }
        )
    return rows


def normalize_roster_players(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for membership in _list(payload.get("data")):
        item = _mapping(membership)
        if item.get("type") != "J" and item.get("typeName") != "Player":
            continue
        person = _mapping(item.get("person"))
        club = _mapping(item.get("club"))
        season = _mapping(item.get("season"))
        country = _mapping(person.get("country"))
        birth_country = _mapping(person.get("birthCountry"))
        rows.append(
            {
                "season_code": season.get("code"),
                "player_id": person.get("code"),
                "external_id": item.get("externalId"),
                "player_name": person.get("name"),
                "alias": person.get("alias"),
                "passport_name": person.get("passportName"),
                "passport_surname": person.get("passportSurname"),
                "team_id": club.get("code"),
                "team_name": club.get("name"),
                "position_code": item.get("position"),
                "position": item.get("positionName"),
                "jersey_number": item.get("dorsal"),
                "height_cm": person.get("height"),
                "weight_kg": person.get("weight"),
                "birth_date": person.get("birthDate"),
                "nationality_code": country.get("code"),
                "nationality": country.get("name"),
                "birth_country_code": birth_country.get("code"),
                "active": item.get("active"),
                "membership_start": item.get("startDate"),
                "membership_end": item.get("endDate"),
            }
        )
    return rows


def extract_xml_rows(xml_text: str, item_tag: str) -> list[dict[str, Any]]:
    """Convert flat XML elements such as schedule items/results games to dicts."""

    root = ElementTree.fromstring(xml_text)
    rows: list[dict[str, Any]] = []
    for element in root.findall(f".//{item_tag}"):
        row = dict(element.attrib)
        row.update({child.tag: child.text for child in element})
        rows.append(row)
    return rows


def xml_roster_summary(xml_text: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_text)
    clubs = root.findall(".//club")
    players = root.findall(".//roster/player")
    return {
        "club_count": len(clubs),
        "player_membership_count": len(players),
        "player_fields": sorted({key for player in players for key in player.attrib}),
    }


def schema_inventory(payload: Any) -> dict[str, list[str]]:
    """Return observed JSON paths mapped to the value types seen there."""

    observed: dict[str, set[str]] = defaultdict(set)

    def visit(value: Any, path: str) -> None:
        observed[path or "$"].add(_type_name(value))
        if isinstance(value, Mapping):
            for key, nested in value.items():
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            child_path = f"{path}[]" if path else "[]"
            if not value:
                observed[child_path].add("empty")
            for nested in value:
                visit(nested, child_path)

    visit(payload, "")
    return {path: sorted(types) for path, types in sorted(observed.items())}


def data_quality_summary(
    games: list[dict[str, Any]],
    players: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    play_by_play: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
) -> dict[str, Any]:
    game_keys = [row.get("game_identifier") or row.get("game_code") for row in games]
    player_keys = [
        (row.get("game_code"), row.get("team_id"), row.get("player_id"))
        for row in players
    ]
    roster_clubs: dict[Any, set[Any]] = defaultdict(set)
    roster_names: dict[Any, set[Any]] = defaultdict(set)
    for row in rosters:
        roster_clubs[row.get("player_id")].add(row.get("team_id"))
        roster_names[row.get("player_id")].add(row.get("player_name"))

    winner_mismatches = [
        row.get("game_code")
        for row in games
        if row.get("api_winner_code")
        and row.get("derived_winner_code")
        and row.get("api_winner_code") != row.get("derived_winner_code")
    ]
    expected_player_minutes: dict[Any, float] = {}
    for row in games:
        expected_player_minutes[row.get("game_code")] = 200.0 + 25.0 * int(
            row.get("overtime_periods") or 0
        )
    minute_totals: dict[tuple[Any, Any], float] = defaultdict(float)
    for row in players:
        minutes = row.get("minutes")
        if isinstance(minutes, (int, float)):
            minute_totals[(row.get("game_code"), row.get("team_id"))] += minutes
    minute_checks = [
        {
            "game_code": game_code,
            "team_id": team_id,
            "observed_minutes": round(total, 2),
            "expected_minutes": expected_player_minutes.get(game_code),
            "difference": round(total - expected_player_minutes.get(game_code, total), 2),
        }
        for (game_code, team_id), total in sorted(minute_totals.items())
    ]

    pbp_event_numbers: dict[Any, list[int]] = defaultdict(list)
    for row in play_by_play:
        value = row.get("event_number")
        if isinstance(value, int):
            pbp_event_numbers[row.get("game_code")].append(value)
    event_number_inversions = [
        {
            "game_code": game_code,
            "inversions": sum(
                following < current
                for current, following in zip(numbers, numbers[1:])
            ),
        }
        for game_code, numbers in sorted(pbp_event_numbers.items())
    ]

    substitution_counts = Counter(
        row.get("play_type")
        for row in play_by_play
        if row.get("play_type") in {"IN", "OUT"}
    )
    played_statuses = Counter(
        str(row.get("game_status")) for row in games if row.get("played") is True
    )

    return {
        "row_counts": {
            "games": len(games),
            "player_boxscores": len(players),
            "team_boxscores": len(teams),
            "play_by_play_events": len(play_by_play),
            "shots": len(shots),
            "roster_memberships": len(rosters),
        },
        "duplicate_game_keys": _duplicates(game_keys),
        "duplicate_player_game_keys": [list(key) for key in _duplicates(player_keys)],
        "missing_player_ids": sum(not row.get("player_id") for row in players),
        "missing_player_names": sum(not row.get("player_name") for row in players),
        "missing_or_unparsed_minutes": sum(
            row.get("minutes") is None and not row.get("did_not_play") for row in players
        ),
        "did_not_play_rows": sum(bool(row.get("did_not_play")) for row in players),
        "players_with_minutes_but_is_playing_false": sum(
            isinstance(row.get("minutes"), (int, float))
            and row.get("is_playing_flag") is False
            for row in players
        ),
        "played_game_statuses": dict(sorted(played_statuses.items())),
        "winner_field_mismatch_game_codes": winner_mismatches,
        "team_player_minute_checks": minute_checks,
        "substitution_event_counts": dict(sorted(substitution_counts.items())),
        "play_by_play_event_number_inversions": event_number_inversions,
        "play_by_play_duplicate_event_keys": [
            list(key)
            for key in _duplicates(
                (row.get("game_code"), row.get("event_number"))
                for row in play_by_play
            )
        ],
        "play_by_play_events_missing_player_id": sum(
            not row.get("player_id") for row in play_by_play
        ),
        "play_by_play_missed_free_throw_events": sum(
            row.get("play_type") == "FTA" for row in play_by_play
        ),
        "shots_missing_player_id": sum(not row.get("player_id") for row in shots),
        "shot_rows_describing_missed_free_throws": sum(
            "missed free" in str(row.get("action", "")).lower() for row in shots
        ),
        "roster_players_with_multiple_clubs_in_sample_season": sorted(
            str(player_id)
            for player_id, clubs in roster_clubs.items()
            if len({club for club in clubs if club}) > 1
        ),
        "roster_player_ids_with_multiple_names": sorted(
            str(player_id)
            for player_id, names in roster_names.items()
            if len({name for name in names if name}) > 1
        ),
    }


def _normalize_pbp_event(
    event: Any,
    season_code: str,
    game_code: int,
    quarter: int,
) -> dict[str, Any]:
    item = _mapping(event)
    return {
        "season_code": season_code,
        "game_code": game_code,
        "quarter": quarter,
        "event_type": item.get("TYPE"),
        "event_number": item.get("NUMBEROFPLAY"),
        "team_id": strip_string(item.get("CODETEAM")),
        "player_id": strip_string(item.get("PLAYER_ID")),
        "play_type": strip_string(item.get("PLAYTYPE")),
        "player_name": strip_string(item.get("PLAYER")),
        "team_name": strip_string(item.get("TEAM")),
        "jersey_number": strip_string(item.get("DORSAL")),
        "elapsed_minute": item.get("MINUTE"),
        "game_clock": item.get("MARKERTIME"),
        "home_score": item.get("POINTS_A"),
        "away_score": item.get("POINTS_B"),
        "comment": item.get("COMMENT"),
        "play_info": item.get("PLAYINFO"),
    }


def _duplicates(values: Iterable[Any]) -> list[Any]:
    counts = Counter(values)
    return [value for value, count in counts.items() if value is not None and count > 1]


def _extra_period_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


def _as_bool(value: Any) -> bool | None:
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__
