#!/usr/bin/env python3
"""Recover, validate, and ingest official historical Fantasy Stats observations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from src.data.fantasy_client import FantasyClient
from src.data.fantasy_market import write_sanitized_json
from src.data.historical_fantasy_stats import (
    FetchedResponse,
    HISTORICAL_ROUNDS_API,
    HISTORICAL_STATS_API,
    HISTORICAL_STATS_PAGE,
    HistoricalFantasyStatsClient,
    HistoricalStatsPage,
    legacy_stats_records,
    modern_stats_records,
    parse_historical_stats_page,
)
from src.data.fantasy_identity import normalize_person_name
from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.historical_fantasy_ingestion import (
    HistoricalFantasyMarketWriter,
    LegacyStatsSnapshotSpec,
    mark_e2025_stats_validation,
)
from src.db.identity_resolution import (
    resolve_current_fantasy_players,
    resolve_historical_fantasy_players,
)
from src.db.ingestion import IngestionSummary
from src.db.raw_store import DEFAULT_RAW_ROOT, archive_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "data"
    / "samples"
    / "fantasy_current"
    / "league_config.sanitized.json"
)
DEFAULT_SUMMARY = (
    PROJECT_ROOT
    / "data"
    / "samples"
    / "historical_fantasy_market"
    / "validation_summary.json"
)
EXPECTED_SEASONS = ("E2022", "E2023", "E2024", "E2025")


@dataclass(slots=True)
class LegacySeasonDownload:
    season_code: str
    page_response: FetchedResponse
    page: HistoricalStatsPage
    active_responses: dict[int, FetchedResponse]
    active_rows: dict[int, list[dict[str, Any]]]
    round_responses: dict[int, FetchedResponse]
    date_responses: dict[int, list[tuple[date, date, FetchedResponse]]]
    date_rows: dict[int, list[dict[str, Any]]]
    date_windows: dict[int, list[tuple[date, date]]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--pace-seconds", type=float, default=0.15)
    return parser


def season_slug_from_database(database: Path, season_code: str) -> str:
    with connect_database(database, read_only=True) as connection:
        row = connection.execute(
            "SELECT season_label, start_year FROM seasons WHERE season_code = ?",
            [season_code],
        ).fetchone()
    if row is None:
        raise ValueError(f"Canonical season {season_code} is missing")
    label, start_year = str(row[0]), int(row[1])
    expected_label = f"{start_year}-{str(start_year + 1)[-2:]}"
    if label != expected_label or season_code != f"E{start_year}":
        raise ValueError(f"Canonical season mapping is inconsistent for {season_code}")
    return f"season/{start_year}-{start_year + 1}"


def canonical_round_date_windows(
    database: Path, season_code: str
) -> dict[int, list[tuple[date, date]]]:
    with connect_database(database, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT round_number, cast(local_game_date AS DATE)
            FROM games
            WHERE season_code = ? AND round_number IS NOT NULL
              AND local_game_date IS NOT NULL AND played
            GROUP BY ALL ORDER BY round_number, cast(local_game_date AS DATE)
            """,
            [season_code],
        ).fetchall()
    by_round: dict[int, list[date]] = defaultdict(list)
    for round_number, game_date in rows:
        by_round[int(round_number)].append(game_date)
    result: dict[int, list[tuple[date, date]]] = {}
    for round_number, dates in by_round.items():
        clusters: list[list[date]] = []
        for value in sorted(set(dates)):
            if not clusters or (value - clusters[-1][-1]).days > 3:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        result[round_number] = [(values[0], values[-1]) for values in clusters]
    return result


def analyze_credit_progression(
    active_rows: Mapping[int, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    by_matchday = {
        matchday: {str(row["id"]): row for row in rows}
        for matchday, rows in active_rows.items()
    }
    compared = exact = changed_plus = changed_credit = replicated_despite_change = 0
    examples: list[dict[str, Any]] = []
    matchdays = sorted(by_matchday)
    for left, right in zip(matchdays, matchdays[1:]):
        if right != left + 1:
            continue
        for player_id in sorted(by_matchday[left].keys() & by_matchday[right].keys()):
            current, following = by_matchday[left][player_id], by_matchday[right][player_id]
            credit = _decimal(current.get("cr"))
            movement = _decimal(current.get("plus"))
            next_credit = _decimal(following.get("cr"))
            if credit is None or movement is None or next_credit is None:
                continue
            compared += 1
            changed_credit += int(next_credit != credit)
            expected = max(Decimal("4.0"), credit + movement).quantize(Decimal("0.1"))
            is_exact = expected == next_credit.quantize(Decimal("0.1"))
            exact += int(is_exact)
            if movement != 0:
                changed_plus += 1
                replicated_despite_change += int(next_credit == credit)
            if not is_exact and len(examples) < 8:
                examples.append(
                    {
                        "matchday": left,
                        "player_id": player_id,
                        "player": " ".join(
                            str(current.get(key) or "")
                            for key in ("first_name", "last_name")
                        ).strip(),
                        "credit": str(credit),
                        "plus": str(movement),
                        "next_credit": str(next_credit),
                    }
                )
    exact_rate = exact / compared if compared else 0.0
    replication_rate = (
        replicated_despite_change / changed_plus if changed_plus else 0.0
    )
    return {
        "consecutive_player_pairs": compared,
        "credit_plus_equals_next_credit": exact,
        "credit_progression_exact_rate": exact_rate,
        "nonzero_plus_pairs": changed_plus,
        "changed_credit_pairs": changed_credit,
        "unchanged_credit_despite_nonzero_plus": replicated_despite_change,
        "replication_rate_when_plus_nonzero": replication_rate,
        "mismatch_examples": examples,
        "credits_are_pre_matchday_safe": (
            compared > 0
            and changed_plus > 0
            and changed_credit > 0
            and exact_rate >= 0.98
            and replication_rate <= 0.02
        ),
    }


def compare_legacy_modes(download: LegacySeasonDownload) -> dict[str, Any]:
    shared = credits_exact = position_exact = team_exact = 0
    for matchday in download.page.matchdays:
        active = {str(row["id"]): row for row in download.active_rows[matchday]}
        all_players = {str(row["id"]): row for row in download.date_rows[matchday]}
        for player_id in active.keys() & all_players.keys():
            left, right = active[player_id], all_players[player_id]
            shared += 1
            credits_exact += _decimal(left.get("cr")) == _decimal(right.get("cr"))
            position_exact += str(left.get("position")) == str(right.get("position"))
            team_exact += str(left.get("team_code")) == str(right.get("team_code"))
    return {
        "shared_active_all_player_rows": shared,
        "credit_exact": credits_exact,
        "position_exact": position_exact,
        "team_exact": team_exact,
        "credit_exact_rate": credits_exact / shared if shared else 0.0,
        "position_exact_rate": position_exact / shared if shared else 0.0,
        "team_exact_rate": team_exact / shared if shared else 0.0,
    }


def _load_cached_response(
    database: Path,
    raw_root: Path,
    relative_target: Path,
) -> FetchedResponse | None:
    path = raw_root / relative_target
    if not path.is_file():
        return None
    source_url = None
    media_type = None
    if database.is_file():
        # Use the default connection configuration so this cache lookup remains
        # compatible when a writer already has the same DuckDB file open.
        with connect_database(database) as connection:
            row = connection.execute(
                """
                SELECT source_url, media_type FROM raw_artifacts
                WHERE raw_path = ? OR raw_path = ?
                ORDER BY stored_at DESC LIMIT 1
                """,
                [
                    path.relative_to(PROJECT_ROOT).as_posix()
                    if path.is_relative_to(PROJECT_ROOT)
                    else str(path),
                    str(path),
                ],
            ).fetchone()
        if row:
            source_url, media_type = row
    if not source_url:
        return None
    return FetchedResponse(
        url=str(source_url),
        content=path.read_bytes(),
        fetched_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
        media_type=str(media_type) if media_type else None,
    )


def _obtain(
    database: Path,
    raw_root: Path,
    relative_target: Path,
    fetch: Callable[[], FetchedResponse],
    pace_seconds: float,
) -> FetchedResponse:
    cached = _load_cached_response(database, raw_root, relative_target)
    if cached is not None:
        return cached
    response = fetch()
    archive_bytes(response.content, relative_target, raw_root=raw_root)
    if pace_seconds:
        time.sleep(pace_seconds)
    return response


def _download_legacy_season(
    client: HistoricalFantasyStatsClient,
    database: Path,
    raw_root: Path,
    season_code: str,
    pace_seconds: float,
) -> LegacySeasonDownload:
    slug = season_slug_from_database(database, season_code)
    page_target = Path("fantasy_stats", season_code, "discovery", "stats_page.html")
    page_response = _obtain(
        database,
        raw_root,
        page_target,
        lambda: client.get_season_page(slug),
        pace_seconds,
    )
    page = parse_historical_stats_page(page_response.content)
    if page.season_slug != slug:
        raise ValueError(f"Official Stats season selection disagrees for {season_code}")
    windows = canonical_round_date_windows(database, season_code)
    if set(page.matchdays) != set(windows):
        raise ValueError(
            f"Stats matchdays and canonical rounds differ for {season_code}: "
            f"stats={page.matchdays}, canonical={tuple(sorted(windows))}"
        )
    active_responses: dict[int, FetchedResponse] = {}
    active_rows: dict[int, list[dict[str, Any]]] = {}
    round_responses: dict[int, FetchedResponse] = {}
    date_responses: dict[int, list[tuple[date, date, FetchedResponse]]] = {}
    date_rows: dict[int, list[dict[str, Any]]] = {}
    for index, matchday in enumerate(page.matchdays, start=1):
        prefix = Path("fantasy_stats", season_code, f"matchday_{matchday:02d}")
        rounds_response = _obtain(
            database,
            raw_root,
            prefix / "rounds.json",
            lambda matchday=matchday: client.get_rounds(page.season_id, matchday),
            pace_seconds,
        )
        round_values = rounds_response.json()
        if not isinstance(round_values, list) or not round_values:
            raise ValueError(f"No official Fantasy rounds for {season_code} {matchday}")
        rounds = [int(value) for value in round_values]
        active_response = _obtain(
            database,
            raw_root,
            prefix / "active_players.json",
            lambda matchday=matchday, rounds=rounds: client.get_matchday_stats(
                page, matchday, rounds
            ),
            pace_seconds,
        )
        round_responses[matchday] = rounds_response
        active_responses[matchday] = active_response
        active_rows[matchday] = legacy_stats_records(active_response.json())
        date_responses[matchday] = []
        combined: dict[str, dict[str, Any]] = {}
        for window_number, (date_from, date_to) in enumerate(
            windows[matchday], start=1
        ):
            target = prefix / (
                f"all_players_window_{window_number:02d}_"
                f"{date_from.isoformat()}_{date_to.isoformat()}.json"
            )
            response = _obtain(
                database,
                raw_root,
                target,
                lambda date_from=date_from, date_to=date_to: client.get_date_stats(
                    page, date_from, date_to
                ),
                pace_seconds,
            )
            date_responses[matchday].append((date_from, date_to, response))
            for row in legacy_stats_records(response.json()):
                player_id = str(row["id"])
                if player_id in combined:
                    raise ValueError(
                        f"Player {player_id} occurs in multiple date windows for "
                        f"{season_code} matchday {matchday}"
                    )
                combined[player_id] = row
        date_rows[matchday] = list(combined.values())
        print(
            f"  {season_code} matchday {matchday:02d} "
            f"({index}/{len(page.matchdays)}): active={len(active_rows[matchday])} "
            f"all_players={len(date_rows[matchday])}",
            flush=True,
        )
    return LegacySeasonDownload(
        season_code=season_code,
        page_response=page_response,
        page=page,
        active_responses=active_responses,
        active_rows=active_rows,
        round_responses=round_responses,
        date_responses=date_responses,
        date_rows=date_rows,
        date_windows=windows,
    )


def _historical_position_evidence(
    downloads: Sequence[LegacySeasonDownload],
) -> dict[str, Any]:
    positions: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for download in downloads:
        for rows in download.date_rows.values():
            for row in rows:
                name = normalize_person_name(
                    f"{row.get('first_name', '')} {row.get('last_name', '')}"
                )
                positions[name][download.season_code].add(str(row.get("position")))
    changed: list[dict[str, Any]] = []
    for name, seasons in positions.items():
        single = {
            season: next(iter(values))
            for season, values in seasons.items()
            if len(values) == 1
        }
        if len(set(single.values())) > 1:
            changed.append({"normalized_name": name, "positions": single})
    return {
        "players_with_cross_season_position_change": len(changed),
        "examples": changed[:12],
        "season_selection_changes_position": bool(changed),
    }


def _register_and_ingest_legacy(
    database: Path,
    raw_root: Path,
    download: LegacySeasonDownload,
    *,
    credits_safe: bool,
    position_safe: bool,
) -> IngestionSummary:
    expected_rows = sum(len(rows) for rows in download.date_rows.values())
    with connect_database(database) as connection:
        existing_rows = int(
            connection.execute(
                """
                SELECT count(*)
                FROM fantasy_market_snapshots AS snapshot
                JOIN fantasy_entities AS entity USING (fantasy_entity_id)
                WHERE snapshot.season_code = ?
                  AND entity.fantasy_provider =
                      'dunkest_euroleague_stats_legacy'
                """,
                [download.season_code],
            ).fetchone()[0]
        )
        if existing_rows == expected_rows:
            connection.execute(
                """
                UPDATE fantasy_market_snapshot_semantics AS semantics
                SET credits_valid_pre_matchday = ?,
                    position_valid_as_of_matchday = ?
                FROM fantasy_market_snapshots AS snapshot
                JOIN fantasy_entities AS entity USING (fantasy_entity_id)
                WHERE semantics.snapshot_record_id = snapshot.snapshot_record_id
                  AND snapshot.season_code = ?
                  AND entity.fantasy_provider =
                      'dunkest_euroleague_stats_legacy'
                """,
                [credits_safe, position_safe, download.season_code],
            )
            connection.execute(
                """
                UPDATE fantasy_market_snapshots AS snapshot
                SET price_semantics = ?, position_semantics = ?
                FROM fantasy_entities AS entity
                WHERE entity.fantasy_entity_id = snapshot.fantasy_entity_id
                  AND snapshot.season_code = ?
                  AND entity.fantasy_provider =
                      'dunkest_euroleague_stats_legacy'
                """,
                [
                    (
                        "PRE_MATCHDAY_PRICE_VALIDATED_BY_CREDIT_MOVEMENT"
                        if credits_safe
                        else "SEASON_LEVEL_CREDIT_OVERLAY_NOT_POINT_IN_TIME"
                    ),
                    (
                        "HISTORICAL_SEASON_FANTASY_POSITION"
                        if position_safe
                        else "RETURNED_POSITION_HISTORICAL_SEMANTICS_UNPROVEN"
                    ),
                    download.season_code,
                ],
            )
            return IngestionSummary(
                run_id="REUSED_CANONICAL_ROWS",
                inserted_records=0,
                raw_artifacts=0,
                status="SUCCEEDED",
            )
    with HistoricalFantasyMarketWriter(
        download.season_code, database, raw_root=raw_root
    ) as writer:
        writer.register_response(
            download.page_response,
            relative_target=Path(
                "fantasy_stats", download.season_code, "discovery", "stats_page.html"
            ),
            endpoint="/en/euroleague/stats/players/table/season/{season}",
            matchday_number=None,
        )
        for matchday in download.page.matchdays:
            prefix = Path(
                "fantasy_stats", download.season_code, f"matchday_{matchday:02d}"
            )
            writer.register_response(
                download.round_responses[matchday],
                relative_target=prefix / "rounds.json",
                endpoint="/api/stats/dunkest/week/rounds",
                matchday_number=matchday,
            )
            writer.register_response(
                download.active_responses[matchday],
                relative_target=prefix / "active_players.json",
                endpoint="/api/stats/table",
                matchday_number=matchday,
            )
            for window_number, (date_from, date_to, response) in enumerate(
                download.date_responses[matchday], start=1
            ):
                writer.ingest_legacy_date_response(
                    response,
                    LegacyStatsSnapshotSpec(
                        season_code=download.season_code,
                        source_season_id=download.page.season_id,
                        fantasy_matchday=matchday,
                        competition_round=matchday,
                        date_from=date_from,
                        date_to=date_to,
                        credits_valid_pre_matchday=credits_safe,
                        position_valid_as_of_matchday=position_safe,
                    ),
                    window_number=window_number,
                )
        return writer.finish()


def _e2025_validation(
    database: Path,
    raw_root: Path,
    config_path: Path,
    pace_seconds: float,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    data = config.get("data") if isinstance(config, Mapping) else None
    if not isinstance(data, Mapping):
        raise ValueError("E2025 Fantasy config is malformed")
    competition_id = int(data["current_competition_id"])
    matchdays = sorted(
        (int(row["number"]), int(row["id"])) for row in data["matchdays"]
    )
    totals = Counter()
    position_exact = 0
    position_compared = 0
    per_matchday: list[dict[str, Any]] = []
    validated: list[int] = []
    with HistoricalFantasyMarketWriter("E2025", database, raw_root=raw_root) as writer:
        with FantasyClient.from_storage_state(timeout=30.0) as client:
            for index, (number, matchday_id) in enumerate(matchdays, start=1):
                records: dict[str, dict[str, Any]] = {}
                page_number = 1
                while True:
                    target = Path(
                        "fantasy_stats",
                        "E2025",
                        f"matchday_{number:02d}",
                        f"competition_stats_page_{page_number:02d}.json",
                    )
                    cached = _load_cached_response(database, raw_root, target)
                    if cached is None:
                        raw = client.get_competition_player_stats_response(
                            competition_id,
                            matchday_ids=(matchday_id,),
                            page=page_number,
                            per_page=100,
                        )
                        response = FetchedResponse(
                            url=raw.url,
                            content=raw.content,
                            fetched_at=raw.fetched_at,
                            media_type=raw.media_type,
                        )
                        archive_bytes(response.content, target, raw_root=raw_root)
                        if pace_seconds:
                            time.sleep(pace_seconds)
                    else:
                        response = cached
                    writer.register_response(
                        response,
                        relative_target=target,
                        endpoint=(
                            f"/api/v1/competitions/{competition_id}/"
                            "stats/players/table"
                        ),
                        matchday_number=number,
                        source="official_fantaking_stats",
                    )
                    payload = response.json()
                    for row in modern_stats_records(payload):
                        player_id = str(row["id"])
                        if player_id in records:
                            raise ValueError(
                                f"Duplicate E2025 Stats player {player_id} on matchday {number}"
                            )
                        records[player_id] = row
                    meta = payload.get("meta", {})
                    if int(meta.get("current_page", page_number)) >= int(
                        meta.get("last_page", page_number)
                    ):
                        break
                    page_number += 1

                existing_rows = writer.connection.execute(
                    """
                    SELECT entity.fantasy_id, snapshot.credits,
                           snapshot.position_name
                    FROM fantasy_market_snapshots AS snapshot
                    JOIN fantasy_entities AS entity USING (fantasy_entity_id)
                    WHERE snapshot.season_code = 'E2025'
                      AND snapshot.matchday_number = ?
                      AND entity.entity_type = 'PLAYER'
                    QUALIFY row_number() OVER (
                        PARTITION BY entity.fantasy_id
                        ORDER BY snapshot.observed_at DESC
                    ) = 1
                    """,
                    [number],
                ).fetchall()
                existing = {
                    str(player_id): (Decimal(str(credit)), position)
                    for player_id, credit, position in existing_rows
                }
                shared = records.keys() & existing.keys()
                exact = mismatch = 0
                for player_id in shared:
                    stats_credit = _decimal(records[player_id].get("quotation"))
                    if stats_credit == existing[player_id][0]:
                        exact += 1
                    else:
                        mismatch += 1
                    stats_position = str(records[player_id].get("position") or "")
                    old_position = str(existing[player_id][1] or "")
                    position_compared += 1
                    position_exact += stats_position == old_position
                missing_new = existing.keys() - records.keys()
                missing_existing = records.keys() - existing.keys()
                totals.update(
                    {
                        "exact_matches": exact,
                        "mismatches": mismatch,
                        "missing_on_new_source": len(missing_new),
                        "missing_on_existing_source": len(missing_existing),
                        "new_source_rows": len(records),
                        "existing_player_rows": len(existing),
                    }
                )
                per_matchday.append(
                    {
                        "matchday": number,
                        "matchday_id": matchday_id,
                        "new_source_rows": len(records),
                        "existing_player_rows": len(existing),
                        "exact_matches": exact,
                        "mismatches": mismatch,
                        "missing_on_new_source": len(missing_new),
                        "missing_on_existing_source": len(missing_existing),
                    }
                )
                if mismatch == 0 and exact > 0:
                    validated.append(number)
                print(
                    f"  E2025 matchday {number:02d} ({index}/{len(matchdays)}): "
                    f"exact={exact} mismatch={mismatch} "
                    f"missing_new={len(missing_new)} missing_existing={len(missing_existing)}",
                    flush=True,
                )
        writer.finish()

    positions_validated = position_compared > 0 and position_exact == position_compared
    if len(validated) != len(matchdays) or totals["mismatches"]:
        raise ValueError("E2025 official Stats credits did not validate exactly")
    mark_e2025_stats_validation(
        database,
        season_code="E2025",
        exact_matches=totals["exact_matches"],
        mismatches=totals["mismatches"],
        validated_matchdays=validated,
        positions_validated=positions_validated,
    )
    return {
        **dict(totals),
        "match_percentage": (
            totals["exact_matches"]
            / (totals["exact_matches"] + totals["mismatches"])
            if totals["exact_matches"] + totals["mismatches"]
            else 0.0
        ),
        "position_exact": position_exact,
        "position_compared": position_compared,
        "position_exact_rate": (
            position_exact / position_compared if position_compared else 0.0
        ),
        "per_matchday": per_matchday,
    }


def _database_coverage(database: Path) -> dict[str, Any]:
    with connect_database(database, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT snapshot.season_code,
                   min(snapshot.matchday_number), max(snapshot.matchday_number),
                   count(DISTINCT snapshot.matchday_number), count(*),
                   count(DISTINCT CASE WHEN entity.entity_type = 'PLAYER'
                                       THEN snapshot.fantasy_entity_id END),
                   count(*) FILTER (
                       WHERE coalesce(semantics.credits_valid_pre_matchday, true)
                   ),
                   count(*) FILTER (
                       WHERE coalesce(
                           semantics.position_valid_as_of_matchday, false
                       )
                   )
            FROM fantasy_market_snapshots AS snapshot
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN fantasy_market_snapshot_semantics AS semantics
              USING (snapshot_record_id)
            WHERE snapshot.season_code IN ('E2022','E2023','E2024','E2025')
            GROUP BY snapshot.season_code ORDER BY snapshot.season_code
            """
        ).fetchall()
        mappings = connection.execute(
            """
            SELECT crosswalk.season_code,
                   coalesce(classification.resolution_classification,
                       CASE crosswalk.mapping_status
                           WHEN 'MATCHED' THEN 'MATCHED'
                           WHEN 'AMBIGUOUS' THEN 'AMBIGUOUS'
                           ELSE 'UNKNOWN' END),
                   count(DISTINCT crosswalk.fantasy_entity_id)
            FROM fantasy_player_crosswalk AS crosswalk
            JOIN fantasy_entities AS entity USING (fantasy_entity_id)
            LEFT JOIN fantasy_identity_classifications AS classification
              USING (crosswalk_id)
            WHERE crosswalk.season_code IN ('E2022','E2023','E2024','E2025')
              AND crosswalk.valid_to IS NULL AND entity.entity_type = 'PLAYER'
            GROUP BY ALL ORDER BY 1, 2
            """
        ).fetchall()
    coverage = {
        str(row[0]): {
            "first_matchday": int(row[1]),
            "last_matchday": int(row[2]),
            "matchday_count": int(row[3]),
            "canonical_observation_rows": int(row[4]),
            "unique_fantasy_players": int(row[5]),
            "safe_pre_matchday_credit_rows": int(row[6]),
            "safe_position_rows": int(row[7]),
        }
        for row in rows
    }
    for season, classification, count in mappings:
        coverage[str(season)].setdefault("identity_counts", {})[
            str(classification)
        ] = int(count)
    for values in coverage.values():
        identity = values.setdefault("identity_counts", {})
        total = sum(identity.values())
        values["identity_matched_percentage"] = (
            identity.get("MATCHED", 0) / total if total else 0.0
        )
    return coverage


def main() -> int:
    args = build_parser().parse_args()
    if args.pace_seconds < 0:
        raise SystemExit("ERROR: --pace-seconds must be non-negative")
    initialize_database(args.database)
    slugs = {
        season: season_slug_from_database(args.database, season)
        for season in EXPECTED_SEASONS
    }
    print("Canonical season mapping:", slugs, flush=True)

    downloads: list[LegacySeasonDownload] = []
    with HistoricalFantasyStatsClient(timeout=30.0) as client:
        for season_code in EXPECTED_SEASONS[:3]:
            print(f"Downloading official historical Stats for {season_code}", flush=True)
            downloads.append(
                _download_legacy_season(
                    client,
                    args.database,
                    args.raw_root,
                    season_code,
                    args.pace_seconds,
                )
            )

    position_evidence = _historical_position_evidence(downloads)
    legacy_summary: dict[str, Any] = {}
    for download in downloads:
        progression = analyze_credit_progression(download.active_rows)
        mode_comparison = compare_legacy_modes(download)
        position_safe = bool(
            position_evidence["season_selection_changes_position"]
            and mode_comparison["position_exact_rate"] == 1.0
        )
        result = _register_and_ingest_legacy(
            args.database,
            args.raw_root,
            download,
            credits_safe=bool(progression["credits_are_pre_matchday_safe"]),
            position_safe=position_safe,
        )
        player_counts = [len(download.date_rows[value]) for value in download.page.matchdays]
        response_hashes = {
            __import__("hashlib").sha256(response.content).hexdigest()
            for response in download.active_responses.values()
        }
        legacy_summary[download.season_code] = {
            "source_season_id": download.page.season_id,
            "season_slug": download.page.season_slug,
            "available_matchdays": list(download.page.matchdays),
            "rounds_per_matchday": {
                str(matchday): [int(value) for value in response.json()]
                for matchday, response in download.round_responses.items()
            },
            "player_rows_by_matchday": {
                str(matchday): len(download.date_rows[matchday])
                for matchday in download.page.matchdays
            },
            "active_rows_by_matchday": {
                str(matchday): len(download.active_rows[matchday])
                for matchday in download.page.matchdays
            },
            "date_windows_by_matchday": {
                str(matchday): [
                    {"date_from": left.isoformat(), "date_to": right.isoformat()}
                    for left, right in download.date_windows[matchday]
                ]
                for matchday in download.page.matchdays
            },
            "total_downloaded_player_rows": sum(player_counts),
            "min_players_per_matchday": min(player_counts),
            "max_players_per_matchday": max(player_counts),
            "unique_active_response_hashes": len(response_hashes),
            "credit_progression": progression,
            "active_vs_all_players": mode_comparison,
            "position_point_in_time_safe": position_safe,
            "ingestion": {
                "status": result.status,
                "inserted_records": result.inserted_records,
                "raw_artifacts": result.raw_artifacts,
            },
        }

    print("Validating E2025 against the existing market archive", flush=True)
    e2025 = _e2025_validation(
        args.database, args.raw_root, args.config, args.pace_seconds
    )

    identities: dict[str, Any] = {}
    for season_code in EXPECTED_SEASONS[:3]:
        result = resolve_historical_fantasy_players(
            args.database, season_code=season_code
        )
        identities[season_code] = {
            "counts": result["counts"],
            "records": result["records"],
        }
        print(f"Identity {season_code}: {result['counts']}", flush=True)
    current = resolve_current_fantasy_players(
        args.database, season_code="E2025", matchday_number=38
    )
    identities["E2025"] = {
        "counts": current["counts"],
        "records": current["records"],
    }

    summary = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "canonical_season_mapping": slugs,
        "historical_stats_page": HISTORICAL_STATS_PAGE,
        "historical_stats_endpoint": HISTORICAL_STATS_API,
        "historical_rounds_endpoint": HISTORICAL_ROUNDS_API,
        "legacy_seasons": legacy_summary,
        "position_evidence": position_evidence,
        "e2025_validation": e2025,
        "identity": identities,
        "database_coverage": _database_coverage(args.database),
    }
    write_sanitized_json(args.summary, summary)
    print(f"Wrote {args.summary}", flush=True)
    return 0


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace("+", "").strip())
    except (InvalidOperation, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
