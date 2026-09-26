"""Leakage-aware ingestion of sanitized Fantaking market observations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.data.fantasy_identity import build_identity_audit, load_roster_rows
from src.data.fantasy_market import market_records
from src.data.fantasy_security import (
    contains_sensitive_artifact_value,
    sanitize_for_artifact,
)

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .ids import canonical_official_player_code, normalized_text, stable_id
from .ingestion import (
    CanonicalIngestor,
    IngestionSummary,
    PROJECT_ROOT,
    _bool_or_none,
    _float_or_none,
    _int_or_none,
    _text_or_none,
    seed_manual_team_aliases,
)
from .raw_store import DEFAULT_RAW_ROOT, archive_json


FANTASY_PROVIDER = "fantaking_euroleague"


@dataclass(frozen=True, slots=True)
class FantasySnapshotSpec:
    season_code: str
    competition_id: int
    players_list_id: int
    matchday_id: int
    matchday_number: int
    observed_at: datetime
    historical_request: bool
    status_valid_as_of_matchday: bool = False


def ingest_fantasy_market_payload(
    payload: Any,
    spec: FantasySnapshotSpec,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    roster_path: Path | None = None,
) -> IngestionSummary:
    """Archive one sanitized observation and load its append-only market rows.

    Historical endpoint status/average fields remain in explicitly named
    ``overlay_*`` columns and are excluded by the leakage-safe view unless a
    caller has independent evidence that they were valid for that matchday.
    """

    sanitized = sanitize_for_artifact(payload)
    if contains_sensitive_artifact_value(sanitized):
        raise ValueError("Fantasy payload still contains sensitive values after sanitizing")
    rows = market_records(sanitized)
    if not rows:
        raise ValueError("Fantasy market payload contains no records")
    if any(
        isinstance(row.get("fantasy_team"), Mapping)
        for row in rows
    ):
        raise ValueError("User-specific Fantasy ownership must not be archived")

    initialize_database(database_path)
    timestamp = spec.observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    relative_target = Path(
        "fantasy",
        spec.season_code,
        "market",
        f"matchday_{spec.matchday_number:02d}",
        f"observed_{timestamp}.sanitized.json",
    )
    artifact = archive_json(sanitized, relative_target, raw_root=raw_root)

    with connect_database(database_path) as connection:
        ingestor = CanonicalIngestor(
            connection,
            source="fantasy_market_snapshot",
            command="python -m scripts.ingest_fantasy_snapshot",
            competition_code=spec.season_code[0],
            season_code=spec.season_code,
        )
        try:
            ingestor.ensure_season(spec.season_code)
            artifact_id = ingestor.register_artifact(
                artifact,
                source="fantaking_api_official_app",
                endpoint=(
                    f"/api/v1/players-lists/{spec.players_list_id}/"
                    f"matchdays/{spec.matchday_id}/players"
                ),
                source_url=None,
                competition_code=spec.season_code[0],
                season_code=spec.season_code,
                matchday_number=spec.matchday_number,
                fetched_at=spec.observed_at,
                is_sanitized=True,
            )
            seed_manual_team_aliases(ingestor)
            _seed_exact_fantasy_team_aliases(ingestor, rows, spec.season_code)
            _ingest_entities_and_snapshot(
                ingestor, rows, spec, artifact_id, artifact.sha256
            )
            if roster_path is not None:
                _ingest_crosswalk(
                    ingestor,
                    sanitized,
                    roster_path,
                    spec.season_code,
                    artifact_id,
                )
            return ingestor.finish()
        except Exception as error:
            ingestor.fail(error)
            raise


def _seed_exact_fantasy_team_aliases(
    ingestor: CanonicalIngestor,
    rows: Sequence[Mapping[str, Any]],
    season_code: str,
) -> None:
    official_names: dict[str, set[str]] = {}
    for team_id, name in ingestor.connection.execute(
        """
        SELECT canonical_team_id, normalized_alias
        FROM team_aliases
        WHERE source = 'euroleague' AND alias_type = 'NAME'
          AND (season_code = ? OR season_code IS NULL)
        """,
        [season_code],
    ).fetchall():
        official_names.setdefault(str(name), set()).add(str(team_id))

    fantasy_teams: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        for key in ("team", "opponent"):
            team = row.get(key)
            if isinstance(team, Mapping) and team.get("id") is not None:
                fantasy_teams[str(team["id"])] = team

    for team in fantasy_teams.values():
        name = _text_or_none(team.get("name"))
        if not name:
            continue
        candidates = official_names.get(normalized_text(name), set())
        if len(candidates) != 1:
            continue
        canonical_team_id = next(iter(candidates))
        for alias_type, value in (
            ("PROVIDER_ID", team.get("id")),
            ("NAME", team.get("name")),
            ("ABBREVIATION", team.get("abbreviation")),
        ):
            ingestor.ensure_team_alias(
                canonical_team_id,
                source="fantasy",
                alias_type=alias_type,
                alias_value=value,
                season_code=season_code,
                match_method="EXACT_OFFICIAL_NAME",
                manually_reviewed=False,
            )


def _ingest_entities_and_snapshot(
    ingestor: CanonicalIngestor,
    rows: Sequence[Mapping[str, Any]],
    spec: FantasySnapshotSpec,
    artifact_id: str,
    artifact_sha256: str,
) -> None:
    snapshot_batch_id = stable_id(
        "fantasy_snapshot_batch", artifact_id, spec.observed_at.isoformat(), artifact_sha256
    )
    records: list[dict[str, Any]] = []
    for row in rows:
        fantasy_id = str(row.get("id") or "").strip()
        if not fantasy_id:
            raise ValueError("Fantasy market row is missing its stable provider ID")
        position = row.get("position") if isinstance(row.get("position"), Mapping) else {}
        entity_type = "COACH" if position.get("name") == "Head Coach" else "PLAYER"
        display_name = " ".join(
            part
            for part in (
                _text_or_none(row.get("first_name")),
                _text_or_none(row.get("last_name")),
            )
            if part
        )
        entity_id = stable_id("fantasy_entity", FANTASY_PROVIDER, fantasy_id)
        ingestor.insert_ignore(
            "fantasy_entities",
            {
                "fantasy_entity_id": entity_id,
                "fantasy_provider": FANTASY_PROVIDER,
                "fantasy_id": fantasy_id,
                "entity_type": entity_type,
                "display_name": display_name or fantasy_id,
                "first_observed_at": spec.observed_at,
                "last_observed_at": spec.observed_at,
            },
        )
        ingestor.connection.execute(
            """
            UPDATE fantasy_entities
            SET first_observed_at = least(first_observed_at, ?),
                last_observed_at = greatest(last_observed_at, ?)
            WHERE fantasy_entity_id = ?
            """,
            [spec.observed_at, spec.observed_at, entity_id],
        )
        team = row.get("team") if isinstance(row.get("team"), Mapping) else {}
        opponent = (
            row.get("opponent") if isinstance(row.get("opponent"), Mapping) else {}
        )
        round_data = row.get("round") if isinstance(row.get("round"), Mapping) else {}
        credits = _float_or_none(row.get("quotation"))
        if credits is None or credits < 0:
            raise ValueError(f"Invalid Fantasy credits for entity {fantasy_id}")
        historical = spec.historical_request
        records.append(
            {
                "snapshot_record_id": stable_id(
                    "fantasy_snapshot_record", snapshot_batch_id, entity_id
                ),
                "snapshot_batch_id": snapshot_batch_id,
                "observed_at": spec.observed_at,
                "season_code": spec.season_code,
                "competition_id": spec.competition_id,
                "players_list_id": spec.players_list_id,
                "matchday_id": spec.matchday_id,
                "matchday_number": spec.matchday_number,
                "turn_id": _int_or_none(round_data.get("id")),
                "turn_number": _int_or_none(round_data.get("number")),
                "fantasy_entity_id": entity_id,
                "fantasy_team_provider_id": _text_or_none(team.get("id")),
                "canonical_team_id": _resolve_fantasy_team(
                    ingestor, team, spec.season_code
                ),
                "fantasy_opponent_provider_id": _text_or_none(opponent.get("id")),
                "opponent_team_id": _resolve_fantasy_team(
                    ingestor, opponent, spec.season_code
                ),
                "team_name_raw": _text_or_none(team.get("name")),
                "opponent_name_raw": _text_or_none(opponent.get("name")),
                "position_id": _int_or_none(position.get("id")),
                "position_name": _text_or_none(position.get("name")),
                "credits": credits,
                "overlay_average_points": _float_or_none(row.get("avg_pts")),
                "overlay_popularity": _float_or_none(row.get("popularity")),
                "jersey_number": _text_or_none(row.get("jersey")),
                "overlay_is_injured": _bool_or_none(row.get("is_injured")),
                "overlay_probability_of_playing": _float_or_none(
                    row.get("probability_of_playing")
                ),
                "overlay_started_from_bench": _bool_or_none(
                    row.get("started_from_bench")
                ),
                "overlay_is_on_fire": _bool_or_none(row.get("is_on_fire")),
                "status_valid_as_of_matchday": spec.status_valid_as_of_matchday,
                "status_semantics": (
                    "CURRENT_OVERLAY_AT_COLLECTION_NOT_HISTORICAL"
                    if historical
                    else "OBSERVED_AT_COLLECTION_ONLY"
                ),
                "position_semantics": (
                    "RETURNED_FOR_MATCHDAY_HISTORICAL_SEMANTICS_UNPROVEN"
                    if historical
                    else "OBSERVED_CURRENT_MARKET_POSITION"
                ),
                "price_semantics": (
                    "HISTORICAL_CURRENT_SEASON_MATCHDAY_PRICE"
                    if historical
                    else "OBSERVED_MATCHDAY_PRICE"
                ),
                "market_available": True,
                "source_artifact_id": artifact_id,
                "ingestion_run_id": ingestor.run_id,
            }
        )
    ingestor.insert_many_ignore("fantasy_market_snapshots", records)


def _ingest_crosswalk(
    ingestor: CanonicalIngestor,
    market_payload: Any,
    roster_path: Path,
    season_code: str,
    artifact_id: str,
) -> None:
    audit = build_identity_audit(
        market_payload,
        load_roster_rows(roster_path),
        season_code=season_code,
    )
    accepted = {
        "matched_direct_official_id",
        "matched_exact_name_team",
        "matched_normalized_name_team",
    }
    for result in audit["records"]:
        if result["is_coach"]:
            continue
        fantasy_entity_id = stable_id(
            "fantasy_entity", FANTASY_PROVIDER, result["fantasy_player_id"]
        )
        official_code = result.get("official_player_id")
        status = result["status"]
        method = result.get("match_method") or "no_exact_match"
        manually_reviewed = False
        team_context_id = _resolve_fantasy_team_parts(
            ingestor,
            result.get("fantasy_team_id"),
            result.get("fantasy_team_name"),
            result.get("fantasy_team_abbreviation"),
            season_code,
        )
        mapping_status = "UNMATCHED"
        confidence = "NONE"
        canonical_player_id = None
        notes = list(result.get("review_notes") or [])
        if status in accepted and official_code:
            canonical_player_id = _canonical_player_from_official_code(
                ingestor, official_code
            )
            mapping_status = "MATCHED"
            confidence = "HIGH"
        elif status == "unique_name_only_review":
            candidates = result.get("candidate_official_player_ids") or []
            if len(candidates) == 1 and team_context_id:
                candidate = _canonical_player_from_official_code(
                    ingestor, candidates[0]
                )
                if candidate and _has_membership(
                    ingestor, candidate, team_context_id, season_code
                ):
                    canonical_player_id = candidate
                    mapping_status = "MATCHED"
                    confidence = "HIGH"
                    method = "normalized_name_team_via_versioned_team_alias"
                    notes.append(
                        "Accepted only after the reviewed Fantasy-to-official team alias supplied team context."
                    )
        elif status == "ambiguous":
            mapping_status = "AMBIGUOUS"
            confidence = "NONE"

        ingestor.insert_ignore(
            "fantasy_player_crosswalk",
            {
                "crosswalk_id": stable_id(
                    "fantasy_crosswalk", fantasy_entity_id, season_code
                ),
                "fantasy_entity_id": fantasy_entity_id,
                "canonical_player_id": canonical_player_id,
                "mapping_status": mapping_status,
                "confidence": confidence,
                "match_method": method,
                "team_context_id": team_context_id,
                "season_code": season_code,
                "valid_from": None,
                "valid_to": None,
                "manually_reviewed": manually_reviewed,
                "notes": " ".join(notes) or None,
                "source_artifact_id": artifact_id,
                "ingestion_run_id": ingestor.run_id,
            },
        )


def _resolve_fantasy_team(
    ingestor: CanonicalIngestor,
    team: Mapping[str, Any],
    season_code: str,
) -> str | None:
    return _resolve_fantasy_team_parts(
        ingestor,
        team.get("id"),
        team.get("name"),
        team.get("abbreviation"),
        season_code,
    )


def _resolve_fantasy_team_parts(
    ingestor: CanonicalIngestor,
    provider_id: Any,
    name: Any,
    abbreviation: Any,
    season_code: str,
) -> str | None:
    candidates: set[str] = set()
    for alias_type, value in (
        ("PROVIDER_ID", provider_id),
        ("NAME", name),
        ("ABBREVIATION", abbreviation),
    ):
        text = _text_or_none(value)
        if not text:
            continue
        rows = ingestor.connection.execute(
            """
            SELECT canonical_team_id
            FROM team_aliases
            WHERE source = 'fantasy' AND alias_type = ?
              AND normalized_alias = ?
              AND (season_code = ? OR season_code IS NULL)
            """,
            [alias_type, normalized_text(text), season_code],
        ).fetchall()
        candidates.update(str(row[0]) for row in rows)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _canonical_player_from_official_code(
    ingestor: CanonicalIngestor, official_code: Any
) -> str | None:
    code = canonical_official_player_code(official_code)
    if not code:
        return None
    row = ingestor.connection.execute(
        "SELECT canonical_player_id FROM players WHERE official_player_code = ?",
        [code],
    ).fetchone()
    return str(row[0]) if row else None


def _has_membership(
    ingestor: CanonicalIngestor,
    player_id: str,
    team_id: str,
    season_code: str,
) -> bool:
    return bool(
        ingestor.connection.execute(
            """
            SELECT 1
            FROM player_team_memberships
            JOIN seasons USING (season_id)
            WHERE canonical_player_id = ? AND canonical_team_id = ?
              AND season_code = ?
            LIMIT 1
            """,
            [player_id, team_id, season_code],
        ).fetchone()
    )
