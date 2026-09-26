"""Current Fantasy market discovery, validation, and snapshot ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.data.fantasy_client import FantasyAPIError, FantasyClient
from src.data.fantasy_config import load_current_fantasy_config
from src.data.fantasy_market import (
    FantasyMarketValidation,
    FantasyMarketValidationError,
    market_records,
    validate_current_market_payload,
)
from src.db.database import (
    DEFAULT_DATABASE_PATH,
    connect_database,
    initialize_database,
)
from src.db.fantasy_ingestion import FantasySnapshotSpec, ingest_fantasy_market_payload
from src.db.identity_resolution import resolve_current_fantasy_players
from src.db.raw_store import DEFAULT_RAW_ROOT

from .availability import fantasy_status_observations, ingest_availability_observations


@dataclass(frozen=True, slots=True)
class FantasyUpdateResult:
    season_code: str
    competition_id: int
    players_list_id: int
    matchday_id: int
    matchday_number: int
    entity_rows: int
    player_rows: int
    coach_rows: int
    valid_credit_rows: int
    team_rows: int
    position_counts: dict[str, int]
    turn_numbers: tuple[int, ...]
    market_status_fields: tuple[str, ...]
    mapped_players: int
    identity_counts: dict[str, int]
    identity_error: str | None
    availability_rows: int
    snapshot_rows_inserted: int
    ingestion_run_id: str
    config_source: str
    config_automatically_discovered: bool
    captured_at: datetime

    @property
    def freshness_message(self) -> str:
        source = (
            "official config auto-discovered"
            if self.config_automatically_discovered
            else "explicit config validated"
        )
        return (
            f"{self.entity_rows} entities ({self.player_rows} players, "
            f"{self.coach_rows} coaches); Matchday {self.matchday_number}; {source}"
        )


def update_current_fantasy_market(
    season_code: str,
    config_path: Path | None = None,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    client: FantasyClient | None = None,
    config_session: Any | None = None,
    config_timeout: float = 30.0,
    captured_at: datetime | None = None,
    raw_root: Path = DEFAULT_RAW_ROOT,
    minimum_players: int = 100,
    minimum_teams: int = 10,
) -> FantasyUpdateResult:
    """Discover and append the official current market without changing history.

    The public league bootstrap supplies the current competition, players-list,
    matchday, and team identifiers. An explicit config path remains available for
    sanitized fixtures or controlled overrides. The market itself is authenticated
    with the existing saved Playwright state.
    """

    _config, scope = load_current_fantasy_config(
        config_path,
        session=config_session,
        timeout=config_timeout,
    )
    observed = captured_at or datetime.now(UTC)
    initialize_database(database_path)
    owned = client is None
    api = client or FantasyClient.from_storage_state()
    try:
        market = api.get_market(scope.players_list_id, scope.matchday_id)
        validation = validate_current_market_payload(
            market,
            configured_team_ids=scope.configured_team_ids,
            minimum_players=minimum_players,
            minimum_teams=minimum_teams,
        )
        if scope.automatically_discovered:
            scope, market, validation = _select_upcoming_market(
                scope,
                market,
                validation,
                api,
                season_code=season_code,
                database_path=database_path,
                observed_at=observed,
                minimum_players=minimum_players,
                minimum_teams=minimum_teams,
            )
    finally:
        if owned:
            api.close()
    before_rows = _snapshot_row_count(
        database_path,
        season_code,
        scope.players_list_id,
        scope.matchday_id,
        observed,
    )
    summary = ingest_fantasy_market_payload(
        market,
        FantasySnapshotSpec(
            season_code=season_code,
            competition_id=scope.competition_id,
            players_list_id=scope.players_list_id,
            matchday_id=scope.matchday_id,
            matchday_number=scope.matchday_number,
            observed_at=observed,
            historical_request=False,
            status_valid_as_of_matchday=False,
        ),
        database_path,
        raw_root=raw_root,
        roster_path=None,
    )
    after_rows = _snapshot_row_count(
        database_path,
        season_code,
        scope.players_list_id,
        scope.matchday_id,
        observed,
    )

    identity_counts: dict[str, int] = {}
    identity_error: str | None = None
    try:
        identity = resolve_current_fantasy_players(
            database_path,
            season_code=season_code,
            matchday_number=scope.matchday_number,
        )
        identity_counts = {
            str(key): int(value) for key, value in identity["counts"].items()
        }
    except (ValueError, RuntimeError) as error:
        # A not-yet-published official roster or a quarantined conflict must not
        # invalidate an otherwise complete official market observation.
        identity_error = str(error)
    mapped = int(identity_counts.get("MATCHED", 0))

    observations = fantasy_status_observations(season_code, database_path)
    availability = ingest_availability_observations(observations, database_path)
    return FantasyUpdateResult(
        season_code=season_code,
        competition_id=scope.competition_id,
        players_list_id=scope.players_list_id,
        matchday_id=scope.matchday_id,
        matchday_number=scope.matchday_number,
        entity_rows=validation.entity_count,
        player_rows=validation.player_count,
        coach_rows=validation.coach_count,
        valid_credit_rows=validation.valid_credit_count,
        team_rows=validation.team_count,
        position_counts=validation.position_counts,
        turn_numbers=validation.turn_numbers,
        market_status_fields=validation.available_status_fields,
        mapped_players=mapped,
        identity_counts=identity_counts,
        identity_error=identity_error,
        availability_rows=int(availability["inserted"]),
        snapshot_rows_inserted=max(0, after_rows - before_rows),
        ingestion_run_id=summary.run_id,
        config_source=scope.source_identifier,
        config_automatically_discovered=scope.automatically_discovered,
        captured_at=observed,
    )


def _select_upcoming_market(
    scope: Any,
    current_market: Any,
    current_validation: FantasyMarketValidation,
    client: FantasyClient,
    *,
    season_code: str,
    database_path: Path | str,
    observed_at: datetime,
    minimum_players: int,
    minimum_teams: int,
) -> tuple[Any, Any, FantasyMarketValidation]:
    """Advance past a lagging bootstrap only with unique schedule evidence."""

    expected_pairs = _upcoming_round_pairs(
        database_path, season_code, observed_at
    )
    if not expected_pairs:
        return scope, current_market, current_validation
    current_pairs = _market_canonical_pairs(
        database_path, season_code, current_market
    )
    if current_pairs == expected_pairs:
        return scope, current_market, current_validation

    matches: list[tuple[Any, Any, FantasyMarketValidation]] = []
    candidates = [
        (number, matchday_id)
        for number, matchday_id in scope.matchday_catalog
        if number > scope.matchday_number
    ][:3]
    for number, matchday_id in candidates:
        try:
            market = client.get_market(scope.players_list_id, matchday_id)
            validation = validate_current_market_payload(
                market,
                configured_team_ids=scope.configured_team_ids,
                minimum_players=minimum_players,
                minimum_teams=minimum_teams,
            )
        except (FantasyAPIError, FantasyMarketValidationError):
            break
        pairs = _market_canonical_pairs(database_path, season_code, market)
        if pairs == expected_pairs:
            matches.append((
                replace(
                    scope,
                    matchday_id=matchday_id,
                    matchday_number=number,
                ),
                market,
                validation,
            ))
    return matches[0] if len(matches) == 1 else (
        scope, current_market, current_validation
    )


def _upcoming_round_pairs(
    database_path: Path | str,
    season_code: str,
    observed_at: datetime,
) -> set[tuple[str, str]] | None:
    with connect_database(database_path, read_only=True) as connection:
        target = connection.execute(
            """
            SELECT round_number, phase_code
            FROM current_live_schedule
            WHERE season_code=? AND NOT played
              AND scheduled_tip_time IS NOT NULL
              AND scheduled_tip_time >= ?
              AND round_number IS NOT NULL
            ORDER BY scheduled_tip_time, game_code
            LIMIT 1
            """,
            [season_code, observed_at],
        ).fetchone()
        if target is None:
            return None
        rows = connection.execute(
            """
            SELECT home_team_id, away_team_id
            FROM current_live_schedule
            WHERE season_code=? AND round_number=? AND phase_code=?
              AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """,
            [season_code, target[0], target[1]],
        ).fetchall()
    if len(rows) < 2:
        return None
    return {
        pair
        for home, away in rows
        for pair in ((str(home), str(away)), (str(away), str(home)))
    }


def _market_canonical_pairs(
    database_path: Path | str,
    season_code: str,
    payload: Any,
) -> set[tuple[str, str]] | None:
    records = market_records(payload)
    if not records:
        return None
    with connect_database(database_path, read_only=True) as connection:
        alias_rows = connection.execute(
            """
            SELECT normalized_alias, canonical_team_id FROM (
              SELECT normalized_alias, canonical_team_id,
                     row_number() OVER (
                       PARTITION BY normalized_alias
                       ORDER BY season_code=? DESC
                     ) AS priority
              FROM team_aliases
              WHERE source='fantasy' AND alias_type='PROVIDER_ID'
                AND (season_code=? OR season_code IS NULL)
            ) WHERE priority=1
            """,
            [season_code, season_code],
        ).fetchall()
    provider_ids: dict[str, set[str]] = {}
    for alias, team_id in alias_rows:
        provider_ids.setdefault(str(alias), set()).add(str(team_id))

    pairs: set[tuple[str, str]] = set()
    for record in records:
        team = record.get("team")
        opponent = record.get("opponent")
        if not isinstance(team, Mapping) or not isinstance(opponent, Mapping):
            return None
        team_candidates = provider_ids.get(str(team.get("id") or ""), set())
        opponent_candidates = provider_ids.get(
            str(opponent.get("id") or ""), set()
        )
        if len(team_candidates) != 1 or len(opponent_candidates) != 1:
            return None
        pairs.add((next(iter(team_candidates)), next(iter(opponent_candidates))))
    return pairs


def _snapshot_row_count(
    database_path: Path | str,
    season_code: str,
    players_list_id: int,
    matchday_id: int,
    observed_at: datetime,
) -> int:
    with connect_database(database_path, read_only=True) as connection:
        return int(
            connection.execute(
                """
                SELECT count(*) FROM fantasy_market_snapshots
                WHERE season_code=? AND players_list_id=? AND matchday_id=?
                  AND observed_at=?
                """,
                [season_code, players_list_id, matchday_id, observed_at],
            ).fetchone()[0]
        )
