"""Append-only availability observations, normalization, and resolution.

This module stores what a source said and when it was knowable. It deliberately
does not estimate participation probability or modify the frozen performance
model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import normalized_text, stable_id


AVAILABILITY_STATUSES = (
    "AVAILABLE",
    "PROBABLE",
    "QUESTIONABLE",
    "DOUBTFUL",
    "GAME_TIME_DECISION",
    "OUT",
    "SUSPENDED",
    "NOT_REGISTERED",
    "LIMITED",
    "UNKNOWN",
)
REASON_CATEGORIES = (
    "INJURY",
    "ILLNESS",
    "SUSPENSION",
    "PERSONAL",
    "REST",
    "NOT_REGISTERED",
    "OTHER",
    "UNKNOWN",
)
SOURCE_PRIORITIES = {
    "MANUAL_OVERRIDE": 500,
    "OFFICIAL_CLUB": 400,
    "OFFICIAL_EUROLEAGUE": 300,
    "OFFICIAL_FANTASY": 200,
    "OTHER_VERIFIED": 100,
    "UNKNOWN": 0,
}


@dataclass(frozen=True, slots=True)
class AvailabilityObservation:
    season_code: str
    captured_at: datetime
    source_type: str
    source_identifier: str
    raw_status: str | None = None
    raw_text: str | None = None
    canonical_player_id: str | None = None
    canonical_team_id: str | None = None
    raw_player_name: str | None = None
    raw_team_name: str | None = None
    canonical_game_id: str | None = None
    matchday_number: int | None = None
    published_at: datetime | None = None
    effective_game_time: datetime | None = None
    normalized_status: str | None = None
    reason_category: str | None = None
    source_summary: str | None = None
    report_id: str | None = None
    source_artifact_id: str | None = None
    timestamp_reliable: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedAvailability:
    player_id: str
    game_id: str | None
    status: str
    source: str | None
    captured_at: datetime | None
    manual_override_active: bool
    conflict: bool
    conflicting_statuses: tuple[str, ...]
    snapshot_id: str | None
    override_event_id: str | None


def normalize_availability_status(raw_status: str | None, raw_text: str | None = None) -> str:
    """Conservatively map explicit source language to the canonical vocabulary."""

    value = " ".join(part for part in (raw_status, raw_text) if part).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return "UNKNOWN"
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("SUSPENDED", ("suspended", "suspension")),
        ("NOT_REGISTERED", (
            "not registered", "deregistered", "deactivated", "not on the roster",
        )),
        ("LIMITED", (
            "minutes restriction", "minute restriction", "limited role",
            "will be limited", "restricted role",
        )),
        ("GAME_TIME_DECISION", (
            "game-time decision", "game time decision", "decision at game time",
        )),
        ("DOUBTFUL", ("doubtful", "unlikely to play")),
        ("QUESTIONABLE", (
            "questionable", "status is uncertain", "uncertain for",
            "decision before the game",
        )),
        ("PROBABLE", ("probable", "expected to be available")),
        ("OUT", (
            "ruled out", "will not play", "won't play", "will miss",
            "not expected to play", "unavailable", "remains out", "remain out",
            "is out", "sidelined", "did not travel", "has not travelled",
        )),
        ("AVAILABLE", (
            "available to play", "listed as available", "is available",
            "ready to play", "full roster available", "cleared to play",
            "will play", "returned to action",
        )),
    )
    for status, phrases in patterns:
        if any(phrase in value for phrase in phrases):
            return status
    exact = value.upper().replace(" ", "_")
    return exact if exact in AVAILABILITY_STATUSES else "UNKNOWN"


def normalize_reason(raw_text: str | None, status: str) -> str:
    value = (raw_text or "").casefold()
    if status == "SUSPENDED" or "suspend" in value:
        return "SUSPENSION"
    if status == "NOT_REGISTERED" or any(
        term in value for term in ("registered", "deregistered", "deactivated", "roster")
    ):
        return "NOT_REGISTERED"
    if any(term in value for term in ("illness", "virus", "viral", "flu", "fever", "sick")):
        return "ILLNESS"
    if any(term in value for term in ("personal", "family", "bereavement")):
        return "PERSONAL"
    if "rest" in value:
        return "REST"
    if any(term in value for term in (
        "injur", "pain", "soreness", "sprain", "strain", "fracture", "broken",
        "surgery", "knee", "ankle", "hamstring", "shoulder", "back", "calf",
        "achilles", "acl", "meniscus", "wrist", "foot", "hip", "groin",
    )):
        return "INJURY"
    return "UNKNOWN"


def ingest_availability_observations(
    observations: Iterable[AvailabilityObservation],
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> dict[str, int]:
    """Append observations; exact repeat inputs converge to the same snapshot ID."""

    initialize_database(database_path)
    inserted = 0
    unmatched = 0
    with connect_database(database_path) as connection:
        for observation in observations:
            player_id, team_id, identity = _resolve_identity(connection, observation)
            unmatched += identity != "MATCHED"
            status = observation.normalized_status or normalize_availability_status(
                observation.raw_status, observation.raw_text
            )
            if status not in AVAILABILITY_STATUSES:
                raise ValueError(f"Unsupported normalized availability status: {status}")
            reason = observation.reason_category or normalize_reason(
                observation.raw_text, status
            )
            if reason not in REASON_CATEGORIES:
                raise ValueError(f"Unsupported availability reason: {reason}")
            known_at = observation.published_at or observation.captured_at
            effective = observation.effective_game_time
            if observation.timestamp_reliable and effective is not None:
                hours_before_tip = (effective - known_at).total_seconds() / 3600.0
                timing = "PRE_GAME" if hours_before_tip >= 0 else "POST_GAME"
            else:
                hours_before_tip = None
                timing = "UNKNOWN"
            priority = SOURCE_PRIORITIES.get(observation.source_type)
            if priority is None:
                raise ValueError(f"Unknown availability source type: {observation.source_type}")
            fingerprint_payload = {
                "season": observation.season_code,
                "game": observation.canonical_game_id,
                "matchday": observation.matchday_number,
                "player": player_id,
                "raw_player": observation.raw_player_name,
                "team": team_id,
                "captured": observation.captured_at.astimezone(UTC).isoformat(),
                "published": _iso(observation.published_at),
                "status": status,
                "raw_status": observation.raw_status,
                "raw_text": observation.raw_text,
                "source": observation.source_identifier,
            }
            fingerprint = hashlib.sha256(
                json.dumps(fingerprint_payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            snapshot_id = stable_id("availability_snapshot", fingerprint)
            result = connection.execute(
                """
                INSERT INTO availability_snapshots (
                    snapshot_id, report_id, season_code, canonical_game_id,
                    matchday_number, canonical_player_id, canonical_team_id,
                    raw_player_name, raw_team_name, captured_at, published_at,
                    effective_game_time, hours_before_tip, timing_status,
                    raw_status, normalized_status, reason_category, raw_text,
                    source_summary, source_type, source_identifier, source_priority,
                    identity_match_status, is_manual_override, source_artifact_id,
                    snapshot_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, false, ?, ?)
                ON CONFLICT DO NOTHING RETURNING 1
                """,
                [
                    snapshot_id, observation.report_id, observation.season_code,
                    observation.canonical_game_id, observation.matchday_number,
                    player_id, team_id, observation.raw_player_name,
                    observation.raw_team_name, observation.captured_at,
                    observation.published_at, effective, hours_before_tip, timing,
                    observation.raw_status, status, reason, observation.raw_text,
                    observation.source_summary, observation.source_type,
                    observation.source_identifier, priority, identity,
                    observation.source_artifact_id, fingerprint,
                ],
            ).fetchall()
            inserted += bool(result)
    return {"inserted": inserted, "unmatched": unmatched}


def fantasy_status_observations(
    season_code: str,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> list[AvailabilityObservation]:
    """Translate only the latest genuinely current Fantasy overlay into snapshots."""

    with connect_database(database_path, read_only=True) as connection:
        rows = connection.execute(
            """
            WITH latest_batch AS (
                SELECT snapshot_batch_id, max(observed_at) AS observed_at
                FROM fantasy_market_snapshots
                WHERE season_code = ? AND status_semantics = 'OBSERVED_AT_COLLECTION_ONLY'
                GROUP BY snapshot_batch_id
                ORDER BY observed_at DESC LIMIT 1
            ), crosswalk AS (
                SELECT * EXCLUDE (rank_value) FROM (
                  SELECT mapping.*,
                         row_number() OVER (
                           PARTITION BY fantasy_entity_id, season_code
                           ORDER BY CASE WHEN mapping_status='MATCHED' THEN 0 ELSE 1 END,
                                    valid_from DESC NULLS LAST, crosswalk_id DESC
                         ) AS rank_value
                  FROM fantasy_player_crosswalk AS mapping
                  WHERE season_code = ?
                ) WHERE rank_value=1
            )
            SELECT market.observed_at, market.matchday_number,
                   crosswalk.canonical_player_id, market.canonical_team_id,
                   entity.display_name, market.overlay_is_injured,
                   market.overlay_probability_of_playing,
                   market.snapshot_record_id
            FROM fantasy_market_snapshots AS market
            JOIN latest_batch USING (snapshot_batch_id)
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN crosswalk USING (fantasy_entity_id, season_code)
            WHERE entity.entity_type='PLAYER'
            """,
            [season_code, season_code],
        ).fetchall()
    observations: list[AvailabilityObservation] = []
    for observed, matchday, player_id, team_id, name, injured, probability, record_id in rows:
        status = _fantasy_status(injured, probability)
        observations.append(AvailabilityObservation(
            season_code=season_code,
            captured_at=observed,
            source_type="OFFICIAL_FANTASY",
            source_identifier=f"fantasy_market:{record_id}",
            canonical_player_id=player_id,
            canonical_team_id=team_id,
            raw_player_name=name,
            matchday_number=matchday,
            raw_status=f"is_injured={injured}; probability_of_playing={probability}",
            normalized_status=status,
            source_summary="Official Fantasy current market signal",
            timestamp_reliable=True,
        ))
    return observations


def set_availability_override(
    player: str,
    status: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str | None = None,
    game_id: str | None = None,
    fantasy_matchday: int | None = None,
    expires_at: datetime | None = None,
    reason_category: str = "UNKNOWN",
    note: str | None = None,
    created_at: datetime | None = None,
) -> str:
    status = status.upper()
    reason_category = reason_category.upper()
    if status not in AVAILABILITY_STATUSES:
        raise ValueError(f"Unsupported override status: {status}")
    if reason_category not in REASON_CATEGORIES:
        raise ValueError(f"Unsupported override reason: {reason_category}")
    if game_id is None and fantasy_matchday is None and expires_at is None:
        raise ValueError("Override requires a game, Fantasy matchday, or expiration")
    initialize_database(database_path)
    now = created_at or datetime.now(UTC)
    with connect_database(database_path) as connection:
        player_id = _resolve_player_argument(connection, player)
        key = _override_key(player_id, season_code, game_id, fantasy_matchday)
        event_id = stable_id("availability_override", key, "SET", now.isoformat())
        connection.execute(
            """
            INSERT INTO availability_override_events (
              override_event_id, override_key, action, canonical_player_id,
              normalized_status, reason_category, season_code, canonical_game_id,
              fantasy_matchday, expires_at, created_at, note, created_by
            ) VALUES (?, ?, 'SET', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'LOCAL_CLI')
            """,
            [event_id, key, player_id, status, reason_category, season_code,
             game_id, fantasy_matchday, expires_at, now, note],
        )
    return event_id


def clear_availability_override(
    player: str,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    season_code: str | None = None,
    game_id: str | None = None,
    fantasy_matchday: int | None = None,
    created_at: datetime | None = None,
) -> str:
    initialize_database(database_path)
    now = created_at or datetime.now(UTC)
    with connect_database(database_path) as connection:
        player_id = _resolve_player_argument(connection, player)
        key = _override_key(player_id, season_code, game_id, fantasy_matchday)
        event_id = stable_id("availability_override", key, "CLEAR", now.isoformat())
        connection.execute(
            """
            INSERT INTO availability_override_events (
              override_event_id, override_key, action, canonical_player_id,
              normalized_status, reason_category, season_code, canonical_game_id,
              fantasy_matchday, expires_at, created_at, note, created_by
            ) VALUES (?, ?, 'CLEAR', ?, NULL, NULL, ?, ?, ?, NULL, ?, NULL, 'LOCAL_CLI')
            """,
            [event_id, key, player_id, season_code, game_id, fantasy_matchday, now],
        )
    return event_id


class AvailabilityResolver:
    """Deterministically resolve manual scope, then source priority, then recency."""

    def __init__(self, database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)

    def resolve(
        self,
        player_id: str,
        *,
        season_code: str,
        game_id: str | None,
        matchday_number: int | None,
        tip_time: datetime | None,
        as_of: datetime | None = None,
    ) -> ResolvedAvailability:
        now = as_of or datetime.now(UTC)
        cutoff = min(now, tip_time) if tip_time is not None else now
        with connect_database(self.database_path, read_only=True) as connection:
            override = connection.execute(
                """
                WITH latest_as_of AS (
                  SELECT event.*,
                         row_number() OVER (
                           PARTITION BY override_key
                           ORDER BY created_at DESC, override_event_id DESC
                         ) AS event_rank
                  FROM availability_override_events AS event
                  WHERE created_at <= ?
                )
                SELECT override_event_id, normalized_status, created_at
                FROM latest_as_of
                WHERE event_rank=1 AND action='SET'
                  AND canonical_player_id = ?
                  AND (season_code IS NULL OR season_code = ?)
                  AND (canonical_game_id IS NULL OR canonical_game_id = ?)
                  AND (fantasy_matchday IS NULL OR fantasy_matchday = ?)
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY
                  (canonical_game_id IS NOT NULL) DESC,
                  (fantasy_matchday IS NOT NULL) DESC,
                  created_at DESC, override_event_id DESC
                LIMIT 1
                """,
                [cutoff, player_id, season_code, game_id, matchday_number, cutoff],
            ).fetchone()
            if override:
                return ResolvedAvailability(
                    player_id, game_id, str(override[1]), "MANUAL_OVERRIDE",
                    override[2], True, False, (), None, str(override[0])
                )
            rows = connection.execute(
                """
                SELECT snapshot_id, normalized_status, source_type,
                       source_identifier, captured_at, source_priority
                FROM availability_snapshots
                WHERE canonical_player_id = ? AND season_code = ?
                  AND captured_at <= ? AND timing_status <> 'POST_GAME'
                  AND (canonical_game_id IS NULL OR canonical_game_id = ?)
                  AND (matchday_number IS NULL OR matchday_number = ?)
                ORDER BY source_priority DESC, captured_at DESC, snapshot_id DESC
                """,
                [player_id, season_code, cutoff, game_id, matchday_number],
            ).fetchall()
        if not rows:
            return ResolvedAvailability(
                player_id, game_id, "UNKNOWN", None, None, False, False, (), None, None
            )
        statuses = tuple(sorted({str(row[1]) for row in rows}))
        selected = rows[0]
        return ResolvedAvailability(
            player_id, game_id, str(selected[1]),
            f"{selected[2]}:{selected[3]}", selected[4], False,
            len(statuses) > 1, statuses, str(selected[0]), None,
        )


def _resolve_identity(connection: Any, observation: AvailabilityObservation) -> tuple[str | None, str | None, str]:
    if observation.canonical_player_id:
        exists = connection.execute(
            "SELECT 1 FROM players WHERE canonical_player_id=?",
            [observation.canonical_player_id],
        ).fetchone()
        return (
            observation.canonical_player_id if exists else None,
            observation.canonical_team_id,
            "MATCHED" if exists else "UNMATCHED",
        )
    if not observation.raw_player_name:
        return None, observation.canonical_team_id, "UNMATCHED"
    normalized = normalized_text(observation.raw_player_name)
    rows = connection.execute(
        """
        SELECT DISTINCT alias.canonical_player_id
        FROM player_aliases AS alias
        LEFT JOIN player_team_memberships AS membership
          ON membership.canonical_player_id=alias.canonical_player_id
        LEFT JOIN seasons AS season USING (season_id)
        WHERE alias.normalized_alias=?
          AND (? IS NULL OR membership.canonical_team_id=?)
          AND (? IS NULL OR season.season_code=?)
        """,
        [normalized, observation.canonical_team_id, observation.canonical_team_id,
         observation.season_code, observation.season_code],
    ).fetchall()
    ids = sorted({str(row[0]) for row in rows})
    if len(ids) == 1:
        return ids[0], observation.canonical_team_id, "MATCHED"
    return None, observation.canonical_team_id, "AMBIGUOUS" if ids else "UNMATCHED"


def _resolve_player_argument(connection: Any, player: str) -> str:
    if connection.execute(
        "SELECT 1 FROM players WHERE canonical_player_id=?", [player]
    ).fetchone():
        return player
    rows = connection.execute(
        """
        SELECT DISTINCT canonical_player_id FROM player_aliases
        WHERE normalized_alias=?
        """,
        [normalized_text(player)],
    ).fetchall()
    ids = sorted({str(row[0]) for row in rows})
    if len(ids) != 1:
        raise ValueError(f"Player argument must resolve uniquely; candidates={len(ids)}")
    return ids[0]


def _override_key(
    player_id: str,
    season_code: str | None,
    game_id: str | None,
    matchday: int | None,
) -> str:
    return stable_id("availability_override_scope", player_id, season_code, game_id, matchday)


def _fantasy_status(injured: Any, probability: Any) -> str:
    if probability is not None:
        value = float(probability)
        value = value / 100.0 if value > 1 else value
        if value <= 0:
            return "OUT"
        if value < 0.5:
            return "DOUBTFUL"
        if value < 0.8:
            return "QUESTIONABLE"
        if value < 1:
            return "PROBABLE"
        if injured is False:
            return "AVAILABLE"
    if injured is True:
        return "QUESTIONABLE"
    if injured is False:
        return "AVAILABLE"
    return "UNKNOWN"


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
