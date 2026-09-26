"""Offline identity checks between Fantasy market and official roster data.

The matcher is intentionally conservative.  It accepts an explicit official
identifier, or an exact/cautiously-normalized name within a uniquely mapped
team and season.  It never performs fuzzy matching and never treats an
accidental overlap between the two systems' native IDs as proof of identity.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .fantasy_market import market_records


DIRECT_PLAYER_ID_FIELDS = frozenset(
    {
        "euroleague_id",
        "euroleague_player_id",
        "official_id",
        "official_player_id",
        "person_code",
    }
)
GENERIC_PLAYER_ID_FIELDS = frozenset(
    {"external_id", "player_code", "player_id", "person_id"}
)
REQUIRED_ROSTER_FIELDS = frozenset(
    {"season_code", "player_id", "external_id", "player_name", "team_id", "team_name"}
)


class FantasyIdentityAuditError(ValueError):
    """Raised when an offline audit input is missing or malformed."""


def normalize_person_name(value: Any) -> str:
    """Return a cautious comparison key; this is normalization, not fuzzing.

    Accents and case are folded, apostrophes/periods are removed, and other
    punctuation becomes whitespace.  Token order and suffixes are retained.
    Consequently, reordered, misspelled, abbreviated, or transliterated names
    do not become automatic matches.
    """

    text = unicodedata.normalize("NFKD", _text(value)).casefold()
    output: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category == "Mn":
            continue
        if character in {"'", "\u2019", "\u2018", "."}:
            continue
        if character.isalnum():
            output.append(character)
        else:
            output.append(" ")
    return " ".join("".join(output).split())


def basic_person_name(value: Any) -> str:
    """Case/whitespace-fold a name while preserving accents and punctuation."""

    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def load_roster_rows(path: Path) -> list[dict[str, str]]:
    """Load an official roster CSV without coercing identifier strings."""

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            missing = sorted(REQUIRED_ROSTER_FIELDS - fields)
            if missing:
                raise FantasyIdentityAuditError(
                    f"Official roster is missing required fields: {', '.join(missing)}"
                )
            rows = [
                {str(key): "" if value is None else str(value) for key, value in row.items()}
                for row in reader
            ]
    except OSError as exc:
        raise FantasyIdentityAuditError(f"Could not read official roster: {path}") from exc
    if not rows:
        raise FantasyIdentityAuditError("Official roster contains no rows")
    return rows


def build_identity_audit(
    market_payload: Any,
    roster_rows: Sequence[Mapping[str, Any]],
    *,
    season_code: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, public-data-only Fantasy identity audit."""

    fantasy_rows = market_records(market_payload)
    if not fantasy_rows:
        raise FantasyIdentityAuditError("Fantasy market payload contains no player records")
    roster = [{str(key): _text(value) for key, value in row.items()} for row in roster_rows]
    if not roster:
        raise FantasyIdentityAuditError("Official roster contains no rows")
    missing = sorted(
        REQUIRED_ROSTER_FIELDS
        - {key for row in roster for key in row}
    )
    if missing:
        raise FantasyIdentityAuditError(
            f"Official roster is missing required fields: {', '.join(missing)}"
        )

    available_seasons = sorted({row["season_code"] for row in roster if row["season_code"]})
    selected_season = season_code
    if selected_season is None and len(available_seasons) == 1:
        selected_season = available_seasons[0]
    scoped_roster = [
        row
        for row in roster
        if selected_season is None or row["season_code"] == selected_season
    ]
    if not scoped_roster:
        raise FantasyIdentityAuditError(
            f"No official roster rows found for season {selected_season!r}"
        )

    official_by_player = _official_people(scoped_roster)
    team_context = _build_team_context(fantasy_rows, scoped_roster)
    player_namespace = _player_namespace_audit(fantasy_rows, scoped_roster)
    official_indexes = _official_name_indexes(scoped_roster)
    official_transfer_ids = _official_transfer_ids(scoped_roster)

    results: list[dict[str, Any]] = []
    for record in fantasy_rows:
        result = _match_record(
            record,
            scoped_roster,
            official_by_player,
            official_indexes,
            team_context["mapping_by_fantasy_key"],
            official_transfer_ids,
            selected_season,
        )
        results.append(result)

    status_counts = Counter(result["status"] for result in results)
    method_counts = Counter(
        result["match_method"]
        for result in results
        if result.get("match_method") is not None
    )
    accepted_statuses = {
        "matched_direct_official_id",
        "matched_exact_name_team",
        "matched_normalized_name_team",
    }
    flags = _duplicate_and_transfer_flags(fantasy_rows, scoped_roster)

    return {
        "audit_version": 1,
        "policy": {
            "automatic_match_rules": [
                "unique explicit official-ID candidate",
                "unique exact name within an exact team mapping and season",
                "unique cautiously-normalized name within an exact team mapping and season",
            ],
            "fuzzy_matching": False,
            "name_only_candidates_are_automatic_matches": False,
            "fantasy_id_overlap_is_identity_evidence": False,
            "normalization_notes": (
                "Unicode accents/case and conservative punctuation are normalized; "
                "token order, suffixes, spelling, abbreviations, and script are retained."
            ),
        },
        "season_scope": {
            "selected": selected_season,
            "available_in_roster": available_seasons,
            "season_was_inferred": season_code is None and len(available_seasons) == 1,
        },
        "summary": {
            "fantasy_records": len(fantasy_rows),
            "fantasy_player_records": sum(not row["is_coach"] for row in results),
            "fantasy_coach_records": status_counts["coach_excluded"],
            "accepted_unique_matches": sum(
                count for status, count in status_counts.items() if status in accepted_statuses
            ),
            "exact_direct_id_unique": status_counts["matched_direct_official_id"],
            "exact_name_team_unique": status_counts["matched_exact_name_team"],
            "normalized_name_team_unique": status_counts[
                "matched_normalized_name_team"
            ],
            "unique_name_only_review": status_counts["unique_name_only_review"],
            "ambiguous": status_counts["ambiguous"],
            "unmatched": status_counts["unmatched"],
            "status_counts": dict(sorted(status_counts.items())),
            "match_method_counts": dict(sorted(method_counts.items())),
        },
        "namespace_comparison": {
            "players": player_namespace,
            "teams": team_context["report"],
        },
        "quality_flags": flags,
        "records": results,
    }


def audit_files(
    market_path: Path,
    roster_path: Path,
    *,
    season_code: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Read offline inputs and write the report beside the sanitized market."""

    try:
        payload = json.loads(market_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FantasyIdentityAuditError(
            f"Sanitized Fantasy market sample not found: {market_path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FantasyIdentityAuditError(
            f"Could not parse sanitized Fantasy market sample: {market_path}"
        ) from exc
    report = build_identity_audit(
        payload,
        load_roster_rows(roster_path),
        season_code=season_code,
    )
    output = market_path.with_name("identity_mapping_audit.json")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return output, report


def _match_record(
    record: Mapping[str, Any],
    roster: Sequence[Mapping[str, str]],
    official_by_player: Mapping[str, Sequence[Mapping[str, str]]],
    indexes: Mapping[str, Mapping[tuple[str, str] | str, set[str]]],
    fantasy_team_mapping: Mapping[str, Sequence[str]],
    official_transfer_ids: set[str],
    season_code: str | None,
) -> dict[str, Any]:
    fantasy_id = _identifier(record.get("id"))
    name = _fantasy_name(record)
    position = _position(record)
    team = _fantasy_team(record)
    team_key = _fantasy_team_key(team)
    mapped_team_ids = sorted(set(fantasy_team_mapping.get(team_key, ())))
    coach = _is_coach(record, position)
    base: dict[str, Any] = {
        "fantasy_player_id": fantasy_id or None,
        "fantasy_name": name or None,
        "fantasy_team_id": team["id"] or None,
        "fantasy_team_name": team["name"] or None,
        "fantasy_team_abbreviation": team["abbreviation"] or None,
        "fantasy_position": position["name"] or None,
        "season_code": season_code,
        "is_coach": coach,
        "mapped_official_team_ids": mapped_team_ids,
        "status": "coach_excluded" if coach else "unmatched",
        "match_method": None,
        "official_player_id": None,
        "official_external_id": None,
        "official_player_name": None,
        "official_team_ids": [],
        "team_consistent": None,
        "official_transfer_flag": False,
        "candidate_official_player_ids": [],
        "explicit_official_id_candidates": _direct_candidates(record),
        "review_notes": [],
    }
    if coach:
        base["review_notes"].append("Head coaches are outside the official player roster audit.")
        return base

    direct_people, direct_details = _resolve_direct_candidates(
        base["explicit_official_id_candidates"], roster
    )
    base["explicit_official_id_candidates"] = direct_details
    if len(direct_people) == 1:
        player_id = next(iter(direct_people))
        return _accept_match(
            base,
            official_by_player[player_id],
            "matched_direct_official_id",
            "explicit_official_id",
            mapped_team_ids,
            official_transfer_ids,
        )
    if len(direct_people) > 1:
        base["status"] = "ambiguous"
        base["match_method"] = "conflicting_explicit_official_ids"
        base["candidate_official_player_ids"] = sorted(direct_people)
        base["review_notes"].append(
            "Explicit ID candidates resolve to more than one official player."
        )
        return base

    basic_names, normalized_names = _fantasy_name_keys(record)
    if not basic_names and not normalized_names:
        base["review_notes"].append("No sufficiently complete Fantasy player name was present.")
        return base

    exact_team_people = _people_for_team_keys(
        basic_names, mapped_team_ids, indexes["basic_team"]
    )
    if len(exact_team_people) == 1:
        player_id = next(iter(exact_team_people))
        return _accept_match(
            base,
            official_by_player[player_id],
            "matched_exact_name_team",
            "exact_name_team_season",
            mapped_team_ids,
            official_transfer_ids,
        )
    if len(exact_team_people) > 1:
        return _mark_ambiguous(base, exact_team_people, "exact_name_team_season")

    normalized_team_people = _people_for_team_keys(
        normalized_names, mapped_team_ids, indexes["normalized_team"]
    )
    if len(normalized_team_people) == 1:
        player_id = next(iter(normalized_team_people))
        return _accept_match(
            base,
            official_by_player[player_id],
            "matched_normalized_name_team",
            "normalized_name_team_season",
            mapped_team_ids,
            official_transfer_ids,
        )
    if len(normalized_team_people) > 1:
        return _mark_ambiguous(
            base, normalized_team_people, "normalized_name_team_season"
        )

    name_only_people: set[str] = set()
    for key in normalized_names:
        name_only_people.update(indexes["normalized"].get(key, set()))
    if len(name_only_people) == 1:
        base["status"] = "unique_name_only_review"
        base["match_method"] = "normalized_name_only_not_automatic"
        base["candidate_official_player_ids"] = sorted(name_only_people)
        base["review_notes"].append(
            "Name is unique in season scope, but no exact unique team mapping confirmed it."
        )
    elif len(name_only_people) > 1:
        return _mark_ambiguous(base, name_only_people, "normalized_name_only")
    else:
        base["review_notes"].append(
            "No explicit ID or exact conservative name+team+season match was found."
        )
    return base


def _accept_match(
    base: dict[str, Any],
    memberships: Sequence[Mapping[str, str]],
    status: str,
    method: str,
    mapped_team_ids: Sequence[str],
    official_transfer_ids: set[str],
) -> dict[str, Any]:
    row = memberships[0]
    official_teams = sorted({member["team_id"] for member in memberships if member["team_id"]})
    team_consistent = bool(set(mapped_team_ids) & set(official_teams)) if mapped_team_ids else None
    base.update(
        {
            "status": status,
            "match_method": method,
            "official_player_id": row["player_id"],
            "official_external_id": row["external_id"] or None,
            "official_player_name": row["player_name"] or None,
            "official_team_ids": official_teams,
            "team_consistent": team_consistent,
            "official_transfer_flag": row["player_id"] in official_transfer_ids,
            "candidate_official_player_ids": [row["player_id"]],
        }
    )
    if method == "explicit_official_id" and team_consistent is False:
        base["review_notes"].append(
            "Explicit ID matched, but the exact team crosswalk does not agree; review transfer timing."
        )
    return base


def _mark_ambiguous(
    base: dict[str, Any], people: Iterable[str], method: str
) -> dict[str, Any]:
    base["status"] = "ambiguous"
    base["match_method"] = method
    base["candidate_official_player_ids"] = sorted(set(people))
    base["review_notes"].append("More than one official player satisfies the exact rule.")
    return base


def _official_people(
    roster: Sequence[Mapping[str, str]],
) -> dict[str, list[Mapping[str, str]]]:
    result: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in roster:
        if row["player_id"]:
            result[row["player_id"]].append(row)
    return dict(result)


def _official_name_indexes(
    roster: Sequence[Mapping[str, str]],
) -> dict[str, dict[Any, set[str]]]:
    basic: dict[str, set[str]] = defaultdict(set)
    normalized: dict[str, set[str]] = defaultdict(set)
    basic_team: dict[tuple[str, str], set[str]] = defaultdict(set)
    normalized_team: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in roster:
        player_id = row["player_id"]
        team_id = row["team_id"]
        for variant in _official_name_variants(row):
            basic_key = basic_person_name(variant)
            normalized_key = normalize_person_name(variant)
            if basic_key:
                basic[basic_key].add(player_id)
                basic_team[(basic_key, team_id)].add(player_id)
            if normalized_key:
                normalized[normalized_key].add(player_id)
                normalized_team[(normalized_key, team_id)].add(player_id)
    return {
        "basic": dict(basic),
        "normalized": dict(normalized),
        "basic_team": dict(basic_team),
        "normalized_team": dict(normalized_team),
    }


def _official_name_variants(row: Mapping[str, str]) -> set[str]:
    variants: set[str] = set()
    display = _text(row.get("player_name"))
    if display:
        variants.add(display)
        if "," in display:
            surname, given = display.split(",", maxsplit=1)
            variants.add(f"{given.strip()} {surname.strip()}")
    passport_given = _text(row.get("passport_name"))
    passport_surname = _text(row.get("passport_surname"))
    if passport_given and passport_surname:
        variants.add(f"{passport_given} {passport_surname}")
    alias = _text(row.get("alias"))
    if len(normalize_person_name(alias).split()) >= 2:
        variants.add(alias)
        if "," in alias:
            surname, given = alias.split(",", maxsplit=1)
            variants.add(f"{given.strip()} {surname.strip()}")
    return {variant for variant in variants if variant.strip()}


def _fantasy_name(record: Mapping[str, Any]) -> str:
    first = _text(record.get("first_name"))
    last = _text(record.get("last_name"))
    if first and last:
        return f"{first} {last}"
    name = _text(record.get("name"))
    if name:
        if "," in name:
            surname, given = name.split(",", maxsplit=1)
            return f"{given.strip()} {surname.strip()}"
        return name
    return ""


def _fantasy_name_keys(record: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    name = _fantasy_name(record)
    if len(normalize_person_name(name).split()) < 2:
        return set(), set()
    return {basic_person_name(name)}, {normalize_person_name(name)}


def _position(record: Mapping[str, Any]) -> dict[str, str]:
    value = record.get("position")
    if isinstance(value, Mapping):
        return {"id": _identifier(value.get("id")), "name": _text(value.get("name"))}
    return {
        "id": _identifier(record.get("position_id")),
        "name": _text(value),
    }


def _is_coach(record: Mapping[str, Any], position: Mapping[str, str]) -> bool:
    if record.get("is_coach") is True:
        return True
    return position.get("id") == "31" or "coach" in normalize_person_name(
        position.get("name")
    )


def _fantasy_team(record: Mapping[str, Any]) -> dict[str, str]:
    value = record.get("team")
    if isinstance(value, Mapping):
        return {
            "id": _identifier(value.get("id")),
            "name": _text(value.get("name")),
            "abbreviation": _text(value.get("abbreviation")),
        }
    return {
        "id": _identifier(record.get("team_id")),
        "name": _text(value) or _text(record.get("team_name")),
        "abbreviation": _text(record.get("team_abbreviation")),
    }


def _fantasy_team_key(team: Mapping[str, str]) -> str:
    return "|".join((team.get("id", ""), team.get("name", ""), team.get("abbreviation", "")))


def _build_team_context(
    fantasy_rows: Sequence[Mapping[str, Any]], roster: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    official_ids = sorted({row["team_id"] for row in roster if row["team_id"]})
    official_names: dict[str, set[str]] = defaultdict(set)
    for row in roster:
        name_key = normalize_person_name(row["team_name"])
        if name_key:
            official_names[name_key].add(row["team_id"])

    fantasy_teams: dict[str, dict[str, str]] = {}
    for record in fantasy_rows:
        team = _fantasy_team(record)
        fantasy_teams[_fantasy_team_key(team)] = team

    mapping: dict[str, list[str]] = {}
    details: list[dict[str, Any]] = []
    for key, team in sorted(fantasy_teams.items()):
        candidates: set[str] = set()
        methods: list[str] = []
        if team["id"] and team["id"] in official_ids:
            candidates.add(team["id"])
            methods.append("native_id_exact")
        abbreviation = team["abbreviation"].upper()
        if abbreviation and abbreviation in official_ids:
            candidates.add(abbreviation)
            methods.append("abbreviation_to_official_team_id")
        name_candidates = official_names.get(normalize_person_name(team["name"]), set())
        if len(name_candidates) == 1:
            candidates.update(name_candidates)
            methods.append("normalized_team_name_exact")
        elif len(name_candidates) > 1:
            candidates.update(name_candidates)
            methods.append("normalized_team_name_ambiguous")
        mapping[key] = sorted(candidates)
        details.append(
            {
                "fantasy_team_id": team["id"] or None,
                "fantasy_team_name": team["name"] or None,
                "fantasy_team_abbreviation": team["abbreviation"] or None,
                "official_team_id_candidates": sorted(candidates),
                "mapping_methods": methods,
                "is_unique_exact_mapping": len(candidates) == 1,
            }
        )

    fantasy_native_ids = sorted({team["id"] for team in fantasy_teams.values() if team["id"]})
    exact_overlap = sorted(set(fantasy_native_ids) & set(official_ids))
    report = {
        "fantasy_native_id_count": len(fantasy_native_ids),
        "official_team_id_count": len(official_ids),
        "native_id_exact_overlap_count": len(exact_overlap),
        "native_id_exact_overlap": exact_overlap,
        "unique_exact_crosswalk_count": sum(
            len(candidate_ids) == 1 for candidate_ids in mapping.values()
        ),
        "ambiguous_crosswalk_count": sum(
            len(candidate_ids) > 1 for candidate_ids in mapping.values()
        ),
        "unmapped_count": sum(not candidate_ids for candidate_ids in mapping.values()),
        "teams": details,
    }
    return {"mapping_by_fantasy_key": mapping, "report": report}


def _player_namespace_audit(
    fantasy_rows: Sequence[Mapping[str, Any]], roster: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    fantasy_ids = {_identifier(row.get("id")) for row in fantasy_rows} - {""}
    official_player_ids = {row["player_id"] for row in roster if row["player_id"]}
    official_external_ids = {row["external_id"] for row in roster if row["external_id"]}
    exact_player_overlap = sorted(fantasy_ids & official_player_ids)
    exact_external_overlap = sorted(fantasy_ids & official_external_ids)
    numeric_player_overlap = _numeric_overlap(fantasy_ids, official_player_ids)
    numeric_external_overlap = _numeric_overlap(fantasy_ids, official_external_ids)
    return {
        "fantasy_native_id_count": len(fantasy_ids),
        "official_player_id_count": len(official_player_ids),
        "official_external_id_count": len(official_external_ids),
        "fantasy_id_exact_official_player_id_overlap_count": len(exact_player_overlap),
        "fantasy_id_exact_official_player_id_overlap": exact_player_overlap,
        "fantasy_id_exact_official_external_id_overlap_count": len(exact_external_overlap),
        "fantasy_id_exact_official_external_id_overlap": exact_external_overlap,
        "fantasy_id_numeric_official_player_id_overlap_count": len(numeric_player_overlap),
        "fantasy_id_numeric_official_external_id_overlap_count": len(numeric_external_overlap),
        "overlap_interpretation": (
            "Namespace overlap is diagnostic only and is never used as an automatic match."
        ),
    }


def _direct_candidates(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path, key, value in _walk_fields(record):
        if key not in DIRECT_PLAYER_ID_FIELDS | GENERIC_PLAYER_ID_FIELDS:
            continue
        path_parts = path.casefold().split(".")
        if "team" in path_parts or "opponent" in path_parts:
            continue
        identifier = _identifier(value)
        if identifier:
            candidates.append(
                {
                    "path": path,
                    "value": identifier,
                    "automatic_evidence": key in DIRECT_PLAYER_ID_FIELDS,
                }
            )
    return candidates


def _resolve_direct_candidates(
    candidates: Sequence[Mapping[str, Any]], roster: Sequence[Mapping[str, str]]
) -> tuple[set[str], list[dict[str, Any]]]:
    people: set[str] = set()
    details: list[dict[str, Any]] = []
    for candidate in candidates:
        value = _identifier(candidate.get("value"))
        matched_namespaces: set[str] = set()
        matched_people: set[str] = set()
        for row in roster:
            if _identifier_equal(value, row["player_id"]):
                matched_namespaces.add("official_player_id")
                matched_people.add(row["player_id"])
            if row["external_id"] and _identifier_equal(value, row["external_id"]):
                matched_namespaces.add("official_external_id")
                matched_people.add(row["player_id"])
        automatic_evidence = candidate.get("automatic_evidence") is True
        if automatic_evidence:
            people.update(matched_people)
        details.append(
            {
                "path": candidate["path"],
                "value": value,
                "automatic_evidence": automatic_evidence,
                "matched_namespaces": sorted(matched_namespaces),
                "candidate_official_player_ids": sorted(matched_people),
            }
        )
    return people, details


def _walk_fields(
    value: Any, path: str = ""
) -> Iterable[tuple[str, str, Any]]:
    if not isinstance(value, Mapping):
        return
    for raw_key, nested in value.items():
        key = str(raw_key).casefold()
        child = f"{path}.{key}" if path else key
        if isinstance(nested, Mapping):
            yield from _walk_fields(nested, child)
        elif not isinstance(nested, list):
            yield child, key, nested


def _people_for_team_keys(
    names: Iterable[str],
    teams: Iterable[str],
    index: Mapping[tuple[str, str], set[str]],
) -> set[str]:
    result: set[str] = set()
    for name in names:
        for team in teams:
            result.update(index.get((name, team), set()))
    return result


def _official_transfer_ids(roster: Sequence[Mapping[str, str]]) -> set[str]:
    teams: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in roster:
        teams[(row["season_code"], row["player_id"])].add(row["team_id"])
    return {player_id for (_, player_id), values in teams.items() if len(values - {""}) > 1}


def _duplicate_and_transfer_flags(
    fantasy_rows: Sequence[Mapping[str, Any]], roster: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    fantasy_id_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    fantasy_name_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in fantasy_rows:
        identifier = _identifier(row.get("id"))
        if identifier:
            fantasy_id_rows[identifier].append(row)
        name_key = normalize_person_name(_fantasy_name(row))
        if name_key:
            fantasy_name_rows[name_key].append(row)

    official_exact_rows = Counter(
        (row["season_code"], row["player_id"], row["team_id"]) for row in roster
    )
    official_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    transfer_teams: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in roster:
        for name in _official_name_variants(row):
            official_names[(row["season_code"], normalize_person_name(name))].add(
                row["player_id"]
            )
        transfer_teams[(row["season_code"], row["player_id"])].add(row["team_id"])

    return {
        "duplicate_fantasy_ids": [
            {"fantasy_player_id": identifier, "count": len(rows)}
            for identifier, rows in sorted(fantasy_id_rows.items())
            if len(rows) > 1
        ],
        "fantasy_ids_on_multiple_teams": [
            {
                "fantasy_player_id": identifier,
                "fantasy_team_ids": sorted(
                    {_fantasy_team(row)["id"] for row in rows} - {""}
                ),
            }
            for identifier, rows in sorted(fantasy_id_rows.items())
            if len({_fantasy_team(row)["id"] for row in rows} - {""}) > 1
        ],
        "duplicate_normalized_fantasy_names": [
            {
                "normalized_name": name,
                "fantasy_player_ids": sorted(
                    {_identifier(row.get("id")) for row in rows} - {""}
                ),
            }
            for name, rows in sorted(fantasy_name_rows.items())
            if len({_identifier(row.get("id")) for row in rows} - {""}) > 1
        ],
        "duplicate_official_membership_rows": [
            {
                "season_code": key[0],
                "official_player_id": key[1],
                "official_team_id": key[2],
                "count": count,
            }
            for key, count in sorted(official_exact_rows.items())
            if count > 1
        ],
        "official_normalized_name_collisions": [
            {
                "season_code": key[0],
                "normalized_name": key[1],
                "official_player_ids": sorted(player_ids),
            }
            for key, player_ids in sorted(official_names.items())
            if len(player_ids) > 1
        ],
        "official_multi_team_memberships": [
            {
                "season_code": key[0],
                "official_player_id": key[1],
                "official_team_ids": sorted(team_ids - {""}),
            }
            for key, team_ids in sorted(transfer_teams.items())
            if len(team_ids - {""}) > 1
        ],
    }


def _numeric_overlap(left: set[str], right: set[str]) -> list[str]:
    right_numbers = {_numeric_identifier(value) for value in right}
    right_numbers.discard(None)
    return sorted(value for value in left if _numeric_identifier(value) in right_numbers)


def _identifier_equal(left: Any, right: Any) -> bool:
    left_text = _identifier(left)
    right_text = _identifier(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    left_number = _numeric_identifier(left_text)
    right_number = _numeric_identifier(right_text)
    return left_number is not None and left_number == right_number


def _numeric_identifier(value: Any) -> int | None:
    text = _identifier(value)
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def _identifier(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return _text(value)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
