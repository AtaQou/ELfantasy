"""Conservative Fantasy-to-official identity resolution from canonical data."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from src.data.fantasy_identity import normalize_person_name

from .database import DEFAULT_DATABASE_PATH, connect_database
from .ids import stable_id
from .ingestion import CanonicalIngestor


SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def official_name_variants(name: str) -> set[str]:
    """Return explicit provider-format variants; never use edit-distance matching."""

    variants = {normalize_person_name(name)}
    if "," not in name:
        return variants
    surname, given = (part.strip() for part in name.split(",", 1))
    surname_tokens = _tokens(surname)
    given_tokens = _tokens(given)
    variants.add(" ".join(given_tokens + surname_tokens))
    surname_tokens = [token for token in surname_tokens if token not in SUFFIXES]
    given_tokens = [token for token in given_tokens if token not in SUFFIXES]
    variants.add(" ".join(given_tokens + surname_tokens))
    while given_tokens and len(given_tokens[-1]) == 1:
        given_tokens = given_tokens[:-1]
    variants.add(" ".join(given_tokens + surname_tokens))
    return {variant for variant in variants if variant}


def resolve_current_fantasy_players(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    season_code: str = "E2025",
    matchday_number: int = 38,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    with connect_database(database_path) as connection:
        market = connection.execute(
            """
            SELECT e.fantasy_entity_id, e.fantasy_id, e.display_name,
                   s.canonical_team_id, s.team_name_raw, s.jersey_number,
                   s.source_artifact_id
            FROM fantasy_market_snapshots s
            JOIN fantasy_entities e USING (fantasy_entity_id)
            WHERE s.season_code = ? AND s.matchday_number = ?
              AND e.entity_type = 'PLAYER'
            QUALIFY row_number() OVER (
                PARTITION BY e.fantasy_entity_id ORDER BY s.observed_at DESC
            ) = 1
            ORDER BY e.display_name
            """,
            [season_code, matchday_number],
        ).fetchall()
        if not market:
            raise ValueError("No current Fantasy player market is available")

        players = connection.execute(
            "SELECT canonical_player_id, official_player_code, canonical_name FROM players"
        ).fetchall()
        exact_index: dict[str, set[str]] = defaultdict(set)
        player_data: dict[str, tuple[str, str]] = {}
        for player_id, official_code, name in players:
            player_data[str(player_id)] = (str(official_code), str(name))
            for variant in official_name_variants(str(name)):
                exact_index[variant].add(str(player_id))

        context: dict[tuple[str, str], set[str]] = defaultdict(set)
        context_rows = connection.execute(
            """
            SELECT DISTINCT canonical_player_id, canonical_team_id, jersey_number
            FROM player_game_stats JOIN games USING (canonical_game_id)
            WHERE season_code = ?
            UNION
            SELECT DISTINCT canonical_player_id, canonical_team_id, jersey_number
            FROM player_team_memberships JOIN seasons USING (season_id)
            WHERE season_code = ?
            """,
            [season_code, season_code],
        ).fetchall()
        for player_id, team_id, jersey in context_rows:
            context[(str(team_id), _jersey(jersey))].add(str(player_id))

        active = {
            str(row[0]): row
            for row in connection.execute(
                """
                SELECT fantasy_entity_id, crosswalk_id, mapping_status,
                       canonical_player_id
                FROM fantasy_player_crosswalk
                WHERE season_code = ? AND valid_to IS NULL
                """,
                [season_code],
            ).fetchall()
        }
        ingestor = CanonicalIngestor(
            connection,
            source="fantasy_identity_resolution",
            command="python -m scripts.resolve_fantasy_identity",
            competition_code="E",
            season_code=season_code,
        )
        records: list[dict[str, Any]] = []
        try:
            for (
                fantasy_entity_id,
                fantasy_id,
                display_name,
                team_id,
                team_name,
                jersey,
                artifact_id,
            ) in market:
                entity_id = str(fantasy_entity_id)
                current = active.get(entity_id)
                if current and str(current[2]) == "MATCHED":
                    player_id = str(current[3])
                    _set_resolution_classification(
                        connection, str(current[1]), "MATCHED", now
                    )
                    records.append(
                        _record(
                            fantasy_id,
                            display_name,
                            team_name,
                            jersey,
                            "MATCHED",
                            player_id,
                            player_data,
                            "existing_confirmed_crosswalk",
                        )
                    )
                    continue

                name_key = normalize_person_name(str(display_name))
                exact = exact_index.get(name_key, set())
                selected: str | None = None
                method: str | None = None
                if len(exact) == 1:
                    selected = next(iter(exact))
                    method = "unique_explicit_name_variant"
                team_jersey = context.get((str(team_id), _jersey(jersey)), set())
                if selected is None and len(team_jersey) == 1:
                    candidate = next(iter(team_jersey))
                    if _strong_name_compatibility(
                        str(display_name), player_data[candidate][1]
                    ):
                        selected = candidate
                        method = "unique_strong_name_team_jersey"

                if selected is not None:
                    if current:
                        connection.execute(
                            "UPDATE fantasy_player_crosswalk SET valid_to = ? "
                            "WHERE crosswalk_id = ? AND valid_to IS NULL",
                            [now, current[1]],
                        )
                    crosswalk_id = stable_id(
                        "fantasy_crosswalk",
                        entity_id,
                        season_code,
                        now.isoformat(),
                    )
                    ingestor.insert_ignore(
                        "fantasy_player_crosswalk",
                        {
                            "crosswalk_id": crosswalk_id,
                            "fantasy_entity_id": entity_id,
                            "canonical_player_id": selected,
                            "mapping_status": "MATCHED",
                            "confidence": "HIGH",
                            "match_method": method,
                            "team_context_id": str(team_id) if team_id else None,
                            "season_code": season_code,
                            "valid_from": now,
                            "valid_to": None,
                            "manually_reviewed": False,
                            "notes": (
                                "Resolved after complete retained modern ingestion; "
                                "no edit-distance/fuzzy score was used."
                            ),
                            "source_artifact_id": str(artifact_id),
                            "ingestion_run_id": ingestor.run_id,
                        },
                    )
                    _set_resolution_classification(
                        connection, crosswalk_id, "MATCHED", now
                    )
                    ingestor.ensure_player_alias(
                        selected,
                        str(display_name),
                        source="fantasy",
                        season_code=season_code,
                        match_method=method or "unknown",
                    )
                    records.append(
                        _record(
                            fantasy_id,
                            display_name,
                            team_name,
                            jersey,
                            "MATCHED",
                            selected,
                            player_data,
                            method,
                        )
                    )
                    continue

                status = _unresolved_status(
                    str(display_name), team_jersey, player_data
                )
                if current:
                    expected_status = (
                        "AMBIGUOUS" if status == "AMBIGUOUS" else "UNMATCHED"
                    )
                    if str(current[2]) != expected_status:
                        raise RuntimeError(
                            "Existing Fantasy crosswalk classification changed; "
                            "manual versioned crosswalk review is required"
                        )
                    _set_resolution_classification(
                        connection, str(current[1]), status, now
                    )
                records.append(
                    _record(
                        fantasy_id,
                        display_name,
                        team_name,
                        jersey,
                        status,
                        None,
                        player_data,
                        "no_conservative_match",
                        candidates=team_jersey,
                    )
                )
            summary = ingestor.finish()
        except BaseException as error:
            ingestor.fail(error)
            raise

    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record["classification"]] += 1
    return {
        "season_code": season_code,
        "matchday_number": matchday_number,
        "resolved_at": now.isoformat(),
        "run_id": summary.run_id,
        "counts": dict(sorted(counts.items())),
        "records": records,
    }


def resolve_historical_fantasy_players(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    season_code: str,
    fantasy_provider: str = "dunkest_euroleague_stats_legacy",
) -> dict[str, Any]:
    """Resolve a season's historical Stats identities without fuzzy matching."""

    now = datetime.now(UTC)
    with connect_database(database_path) as connection:
        market = connection.execute(
            """
            SELECT entity.fantasy_entity_id, entity.fantasy_id,
                   entity.display_name,
                   list(DISTINCT snapshot.canonical_team_id)
                       FILTER (WHERE snapshot.canonical_team_id IS NOT NULL) AS team_ids,
                   string_agg(DISTINCT snapshot.team_name_raw, ', ')
                       FILTER (WHERE snapshot.team_name_raw IS NOT NULL) AS team_names,
                   max(snapshot.source_artifact_id) AS source_artifact_id
            FROM fantasy_market_snapshots AS snapshot
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            WHERE snapshot.season_code = ?
              AND entity.fantasy_provider = ?
              AND entity.entity_type = 'PLAYER'
            GROUP BY ALL
            ORDER BY entity.display_name, entity.fantasy_id
            """,
            [season_code, fantasy_provider],
        ).fetchall()
        if not market:
            raise ValueError(f"No historical Fantasy Stats players for {season_code}")

        players = connection.execute(
            "SELECT canonical_player_id, official_player_code, canonical_name FROM players"
        ).fetchall()
        exact_index: dict[str, set[str]] = defaultdict(set)
        player_data: dict[str, tuple[str, str]] = {}
        for player_id, official_code, name in players:
            player_data[str(player_id)] = (str(official_code), str(name))
            for variant in official_name_variants(str(name)):
                exact_index[variant].add(str(player_id))

        season_teams: dict[str, set[str]] = defaultdict(set)
        for player_id, team_id in connection.execute(
            """
            SELECT DISTINCT canonical_player_id, canonical_team_id
            FROM player_team_memberships JOIN seasons USING (season_id)
            WHERE season_code = ?
            UNION
            SELECT DISTINCT canonical_player_id, canonical_team_id
            FROM player_game_stats JOIN games USING (canonical_game_id)
            WHERE season_code = ?
            """,
            [season_code, season_code],
        ).fetchall():
            season_teams[str(player_id)].add(str(team_id))

        active = {
            str(row[0]): row
            for row in connection.execute(
                """
                SELECT fantasy_entity_id, crosswalk_id, mapping_status,
                       canonical_player_id
                FROM fantasy_player_crosswalk
                WHERE season_code = ? AND valid_to IS NULL
                """,
                [season_code],
            ).fetchall()
        }
        ingestor = CanonicalIngestor(
            connection,
            source="historical_fantasy_identity_resolution",
            command="python -m scripts.resolve_historical_fantasy_identity",
            competition_code="E",
            season_code=season_code,
        )
        records: list[dict[str, Any]] = []
        try:
            for (
                entity_id,
                fantasy_id,
                display_name,
                team_ids,
                team_names,
                artifact_id,
            ) in market:
                entity_id = str(entity_id)
                team_set = {str(value) for value in (team_ids or []) if value}
                name_key = normalize_person_name(str(display_name))
                name_candidates = set(exact_index.get(name_key, set()))
                team_candidates = {
                    player_id
                    for player_id, memberships in season_teams.items()
                    if memberships & team_set
                }
                contextual_exact = name_candidates & team_candidates
                selected: str | None = None
                method = "no_conservative_match"
                classification = "UNKNOWN"

                if len(contextual_exact) == 1:
                    selected = next(iter(contextual_exact))
                    method = "unique_explicit_name_variant_and_season_team"
                elif not team_set and len(name_candidates) == 1:
                    selected = next(iter(name_candidates))
                    method = "unique_explicit_name_variant_team_unresolved"
                elif len(contextual_exact) > 1 or len(name_candidates) > 1:
                    classification = "AMBIGUOUS"
                elif len(name_candidates) == 1:
                    classification = "NAME_TEAM_CONFLICT"
                else:
                    compatible = {
                        player_id
                        for player_id in team_candidates
                        if _strong_name_compatibility(
                            str(display_name), player_data[player_id][1]
                        )
                    }
                    if len(compatible) == 1:
                        selected = next(iter(compatible))
                        method = "unique_strong_name_and_season_team"
                    elif len(compatible) > 1:
                        classification = "AMBIGUOUS"
                    else:
                        global_compatible = {
                            player_id
                            for player_id, (_, name) in player_data.items()
                            if _strong_name_compatibility(str(display_name), name)
                        }
                        classification = (
                            "NAME_TEAM_CONFLICT"
                            if global_compatible
                            else "NO_OFFICIAL_CANDIDATE"
                        )

                if selected is not None:
                    classification = "MATCHED"
                mapping_status = (
                    "MATCHED"
                    if classification == "MATCHED"
                    else "AMBIGUOUS"
                    if classification == "AMBIGUOUS"
                    else "UNMATCHED"
                )
                current = active.get(entity_id)
                canonical_player_id = selected if mapping_status == "MATCHED" else None
                if current:
                    if (
                        str(current[2]) != mapping_status
                        or (current[3] is None) != (canonical_player_id is None)
                        or (
                            current[3] is not None
                            and str(current[3]) != canonical_player_id
                        )
                    ):
                        raise RuntimeError(
                            "Existing historical Fantasy crosswalk changed; "
                            "manual versioned crosswalk review is required"
                        )
                    _set_resolution_classification(
                        connection, str(current[1]), classification, now
                    )
                else:
                    crosswalk_id = stable_id(
                        "fantasy_crosswalk", entity_id, season_code
                    )
                    ingestor.insert_ignore(
                        "fantasy_player_crosswalk",
                        {
                            "crosswalk_id": crosswalk_id,
                            "fantasy_entity_id": entity_id,
                            "canonical_player_id": canonical_player_id,
                            "mapping_status": mapping_status,
                            "confidence": "HIGH" if selected else "NONE",
                            "match_method": method,
                            "team_context_id": (
                                next(iter(team_set)) if len(team_set) == 1 else None
                            ),
                            "season_code": season_code,
                            "valid_from": None,
                            "valid_to": None,
                            "manually_reviewed": False,
                            "notes": (
                                "Season-aware deterministic historical Stats resolution."
                            ),
                            "source_artifact_id": str(artifact_id),
                            "ingestion_run_id": ingestor.run_id,
                        },
                    )
                    _set_resolution_classification(
                        connection, crosswalk_id, classification, now
                    )
                if selected:
                    ingestor.ensure_player_alias(
                        selected,
                        str(display_name),
                        source="fantasy_stats",
                        season_code=season_code,
                        match_method=method,
                    )
                records.append(
                    _record(
                        fantasy_id,
                        display_name,
                        team_names,
                        None,
                        classification,
                        selected,
                        player_data,
                        method,
                        candidates=(
                            contextual_exact
                            or name_candidates
                            or team_candidates
                        ),
                    )
                )
            summary = ingestor.finish()
        except BaseException as error:
            ingestor.fail(error)
            raise

    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record["classification"]] += 1
    return {
        "season_code": season_code,
        "resolved_at": now.isoformat(),
        "run_id": summary.run_id,
        "counts": dict(sorted(counts.items())),
        "records": records,
    }


def _strong_name_compatibility(fantasy_name: str, official_name: str) -> bool:
    fantasy = _tokens(fantasy_name)
    if "," not in official_name or len(fantasy) < 2:
        return False
    surname, given = (part.strip() for part in official_name.split(",", 1))
    official_surname = [token for token in _tokens(surname) if token not in SUFFIXES]
    official_given = [token for token in _tokens(given) if token not in SUFFIXES]
    while official_given and len(official_given[-1]) == 1:
        official_given.pop()
    official = official_given + official_surname
    if set(fantasy).issubset(set(official)):
        return True
    # Explicitly require the same surname plus a shared >=3-character given-name
    # prefix; exact team and jersey uniqueness is enforced by the caller.
    return (
        fantasy[-1] == official_surname[-1]
        and len(_common_prefix(fantasy[0], official_given[0])) >= 3
    ) if official_surname and official_given else False


def _unresolved_status(
    fantasy_name: str,
    team_jersey: set[str],
    player_data: dict[str, tuple[str, str]],
) -> str:
    if team_jersey:
        fantasy_tokens = _tokens(fantasy_name)
        fantasy_surname = fantasy_tokens[-1]
        candidate_surnames = {
            _tokens(name.split(",", 1)[0])[-1]
            for _, name in (player_data[player] for player in team_jersey)
        }
        if len(team_jersey) == 1:
            candidate = next(iter(team_jersey))
            official_tokens = _tokens(player_data[candidate][1])
            shared = set(fantasy_tokens) & set(official_tokens)
            differing_fantasy = [token for token in fantasy_tokens if token not in shared]
            differing_official = [token for token in official_tokens if token not in shared]
            # A unique same-team/jersey candidate with one exact name token and a
            # transliteration-like prefix is reviewable, but not safe to auto-map.
            if (
                shared
                and differing_fantasy
                and differing_official
                and any(
                    len(_common_prefix(left, right)) >= 3
                    for left in differing_fantasy
                    for right in differing_official
                )
            ):
                return "AMBIGUOUS"
        return (
            "AMBIGUOUS"
            if fantasy_surname in candidate_surnames
            else "NAME_TEAM_CONFLICT"
        )
    return "NO_OFFICIAL_CANDIDATE"


def _record(
    fantasy_id: Any,
    display_name: Any,
    team_name: Any,
    jersey: Any,
    classification: str,
    player_id: str | None,
    player_data: dict[str, tuple[str, str]],
    method: str | None,
    *,
    candidates: set[str] | None = None,
) -> dict[str, Any]:
    official = player_data.get(player_id) if player_id else None
    return {
        "fantasy_id": str(fantasy_id),
        "fantasy_name": str(display_name),
        "fantasy_team": str(team_name),
        "jersey": None if jersey is None else str(jersey),
        "classification": classification,
        "canonical_player_id": player_id,
        "official_player_code": official[0] if official else None,
        "official_name": official[1] if official else None,
        "match_method": method,
        "candidate_official_players": [
            {"official_player_code": player_data[item][0], "name": player_data[item][1]}
            for item in sorted(candidates or set())
        ],
    }


def _tokens(value: str) -> list[str]:
    return [token for token in normalize_person_name(value).split() if token]


def _jersey(value: Any) -> str:
    text = str(value or "").strip()
    return text.lstrip("0") or "0"


def _common_prefix(left: str, right: str) -> str:
    size = 0
    while size < min(len(left), len(right)) and left[size] == right[size]:
        size += 1
    return left[:size]


def _set_resolution_classification(
    connection: Any,
    crosswalk_id: str,
    classification: str,
    classified_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO fantasy_identity_classifications (
            crosswalk_id, resolution_classification, classified_at, notes
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT (crosswalk_id) DO UPDATE SET
            resolution_classification = excluded.resolution_classification,
            classified_at = excluded.classified_at,
            notes = excluded.notes
        """,
        [
            crosswalk_id,
            classification,
            classified_at,
            "Deterministic season-aware identity classification.",
        ],
    )
