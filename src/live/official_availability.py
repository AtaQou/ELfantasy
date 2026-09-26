"""Conservative collection of official EuroLeague availability reports.

Official articles are free-form editorial pages, not a documented injury API.
The collector honors HTTP failures (including 429) and never attempts to evade
access controls. HTML parsing is deterministic and only emits a row when one
known player and an explicit status phrase can be tied to a team section.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

import requests

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import normalized_text, stable_id
from src.db.ingestion import CanonicalIngestor
from src.db.raw_store import DEFAULT_RAW_ROOT, archive_bytes

from .availability import (
    AvailabilityObservation,
    ingest_availability_observations,
    normalize_availability_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_MANIFEST = PROJECT_ROOT / "data" / "reference" / "official_availability_sources.json"


@dataclass(frozen=True, slots=True)
class OfficialReportSource:
    season_code: str
    source_url: str
    matchday_number: int | None = None
    published_at: datetime | None = None
    publication_time_reliable: bool = False


@dataclass(frozen=True, slots=True)
class CollectionResult:
    source_url: str
    parse_status: str
    observation_count: int
    message: str


def load_source_manifest(path: Path = DEFAULT_SOURCE_MANIFEST) -> list[OfficialReportSource]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources: list[OfficialReportSource] = []
    for row in payload.get("sources", []):
        published = row.get("published_at")
        sources.append(OfficialReportSource(
            season_code=str(row["season_code"]),
            source_url=str(row["source_url"]),
            matchday_number=row.get("matchday_number"),
            published_at=datetime.fromisoformat(published) if published else None,
            publication_time_reliable=bool(row.get("publication_time_reliable", False)),
        ))
    return sources


def collect_official_report(
    source: OfficialReportSource,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    raw_root: Path = DEFAULT_RAW_ROOT,
    timeout_seconds: float = 20.0,
    session: requests.Session | None = None,
    captured_at: datetime | None = None,
) -> CollectionResult:
    """Fetch, retain, and conservatively parse one official report."""

    parsed_url = urlparse(source.source_url)
    if parsed_url.scheme != "https" or parsed_url.hostname not in {
        "www.euroleaguebasketball.net", "euroleaguebasketball.net"
    }:
        raise ValueError("Official availability collector only accepts EuroLeague HTTPS URLs")
    initialize_database(database_path)
    observed = captured_at or datetime.now(UTC)
    owned = session is None
    client = session or requests.Session()
    client.headers.setdefault("User-Agent", "euroleague-fantasy-ai-live-data/0.1")
    try:
        response = client.get(source.source_url, timeout=timeout_seconds)
    except requests.RequestException as error:
        return _record_unavailable_report(
            source, observed, "BLOCKED", str(error), database_path
        )
    finally:
        if owned:
            client.close()
    if response.status_code != 200:
        return _record_unavailable_report(
            source, observed, "BLOCKED",
            f"HTTP {response.status_code}; no bypass attempted", database_path,
        )
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.casefold():
        return _record_unavailable_report(
            source, observed, "REJECTED", f"unexpected content type {content_type}",
            database_path,
        )
    relative = Path(
        "availability", source.season_code,
        f"report_{source.matchday_number or 'unknown'}_{observed:%Y%m%dT%H%M%SZ}.html",
    )
    artifact = archive_bytes(response.content, relative, raw_root=raw_root)
    parser = _ReportHTMLParser()
    parser.feed(response.text)
    title = parser.title
    published_at = source.published_at or parser.published_at
    publication_reliable = source.publication_time_reliable or parser.published_at is not None
    matchday = source.matchday_number or _matchday_from_text(title)

    with connect_database(database_path) as connection:
        ingestor = CanonicalIngestor(
            connection,
            source="official_availability_report",
            command="python -m scripts.update_availability",
            competition_code="E",
            season_code=source.season_code,
        )
        artifact_id = ingestor.register_artifact(
            artifact,
            source="official_euroleague_news",
            endpoint="official_injury_report_article",
            source_url=source.source_url,
            competition_code="E",
            season_code=source.season_code,
            matchday_number=matchday,
            fetched_at=observed,
            media_type="text/html",
        )
        existing_report = connection.execute(
            "SELECT report_id FROM official_availability_reports "
            "WHERE source_url=? AND captured_at=?",
            [source.source_url, observed],
        ).fetchone()
        report_id = str(existing_report[0]) if existing_report else stable_id(
            "official_availability_report", source.source_url, observed.isoformat()
        )
        connection.execute(
            """
            INSERT INTO official_availability_reports (
              report_id, season_code, matchday_number, source_url, title,
              published_at, captured_at, publication_time_reliable, source_type,
              parse_status, observation_count, source_artifact_id,
              content_fingerprint, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OFFICIAL_EUROLEAGUE',
                      'DISCOVERED', 0, ?, ?, NULL)
            ON CONFLICT DO NOTHING
            """,
            [report_id, source.season_code, matchday, source.source_url, title,
             published_at, observed, publication_reliable, artifact_id, artifact.sha256],
        )
        ingestor.finish()

    observations = _extract_observations(
        database_path, source.season_code, matchday, parser.sections,
        observed, published_at, publication_reliable, source.source_url,
        report_id, artifact_id,
    )
    result = ingest_availability_observations(observations, database_path)
    status = "PARSED" if observations and result["unmatched"] == 0 else (
        "PARTIAL" if observations else "REJECTED"
    )
    with connect_database(database_path) as connection:
        connection.execute(
            """
            UPDATE official_availability_reports
            SET parse_status=?, observation_count=?, notes=?
            WHERE report_id=? AND captured_at=?
            """,
            [status, result["inserted"],
             "Deterministic explicit-status parser; retrospective-only statements excluded.",
             report_id, observed],
        )
    return CollectionResult(
        source.source_url, status, result["inserted"],
        f"parsed={len(observations)} unmatched={result['unmatched']}",
    )


def register_discovered_sources(
    sources: Iterable[OfficialReportSource],
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    captured_at: datetime | None = None,
) -> int:
    """Register source discovery without pretending content or timestamps were recovered."""

    initialize_database(database_path)
    observed = captured_at or datetime.now(UTC)
    inserted = 0
    with connect_database(database_path) as connection:
        for source in sources:
            report_id = stable_id(
                "official_availability_report", source.source_url, "DISCOVERED"
            )
            inserted += bool(connection.execute(
                """
                INSERT INTO official_availability_reports (
                  report_id, season_code, matchday_number, source_url, title,
                  published_at, captured_at, publication_time_reliable, source_type,
                  parse_status, observation_count, source_artifact_id,
                  content_fingerprint, notes
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'OFFICIAL_EUROLEAGUE',
                          'DISCOVERED', 0, NULL, NULL,
                          'Official URL discovered; content/timestamp not yet retained.')
                ON CONFLICT DO NOTHING RETURNING 1
                """,
                [report_id, source.season_code, source.matchday_number,
                 source.source_url, source.published_at, observed,
                 source.publication_time_reliable],
            ).fetchall())
    return inserted


def _record_unavailable_report(
    source: OfficialReportSource,
    captured_at: datetime,
    status: str,
    message: str,
    database_path: Path | str,
) -> CollectionResult:
    report_id = stable_id(
        "official_availability_report", source.source_url, captured_at.isoformat()
    )
    with connect_database(database_path) as connection:
        existing_report = connection.execute(
            "SELECT report_id FROM official_availability_reports "
            "WHERE source_url=? AND captured_at=?",
            [source.source_url, captured_at],
        ).fetchone()
        if existing_report:
            report_id = str(existing_report[0])
        connection.execute(
            """
            INSERT INTO official_availability_reports (
              report_id, season_code, matchday_number, source_url, title,
              published_at, captured_at, publication_time_reliable, source_type,
              parse_status, observation_count, source_artifact_id,
              content_fingerprint, notes
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'OFFICIAL_EUROLEAGUE',
                      ?, 0, NULL, NULL, ?)
            ON CONFLICT DO NOTHING
            """,
            [report_id, source.season_code, source.matchday_number, source.source_url,
             source.published_at, captured_at, source.publication_time_reliable,
             status, message[:1000]],
        )
        connection.execute(
            """
            UPDATE official_availability_reports
            SET parse_status=?, notes=?
            WHERE report_id=? AND captured_at=?
            """,
            [status, message[:1000], report_id, captured_at],
        )
    return CollectionResult(source.source_url, status, 0, message)


def _extract_observations(
    database_path: Path | str,
    season_code: str,
    matchday: int | None,
    sections: list[tuple[str, str]],
    captured_at: datetime,
    published_at: datetime | None,
    publication_reliable: bool,
    source_url: str,
    report_id: str,
    artifact_id: str,
) -> list[AvailabilityObservation]:
    if matchday is None:
        return []
    with connect_database(database_path, read_only=True) as connection:
        team_aliases = connection.execute(
            """
            SELECT alias.normalized_alias, alias.canonical_team_id
            FROM team_aliases AS alias
            WHERE alias.source='euroleague'
              AND (alias.season_code=? OR alias.season_code IS NULL)
            """,
            [season_code],
        ).fetchall()
        player_rows = connection.execute(
            """
            SELECT membership.canonical_team_id, alias.canonical_player_id,
                   alias.normalized_alias
            FROM player_team_memberships AS membership
            JOIN seasons AS season USING (season_id)
            JOIN player_aliases AS alias
              ON alias.canonical_player_id=membership.canonical_player_id
            WHERE season.season_code=?
            """,
            [season_code],
        ).fetchall()
        games = connection.execute(
            """
            SELECT canonical_game_id, home_team_id, away_team_id, game_date
            FROM games WHERE season_code=? AND round_number=?
            """,
            [season_code, matchday],
        ).fetchall()
    alias_to_teams: dict[str, set[str]] = {}
    for alias, team_id in team_aliases:
        alias_to_teams.setdefault(str(alias), set()).add(str(team_id))
    players_by_team: dict[str, list[tuple[str, str]]] = {}
    for team_id, player_id, alias in player_rows:
        players_by_team.setdefault(str(team_id), []).append((str(player_id), str(alias)))
    observations: list[AvailabilityObservation] = []
    for heading, statement in sections:
        team_ids = alias_to_teams.get(normalized_text(heading), set())
        if len(team_ids) != 1:
            continue
        team_id = next(iter(team_ids))
        normalized_statement = normalized_text(statement)
        matched_players = {
            player_id
            for player_id, alias in players_by_team.get(team_id, [])
            if len(alias) >= 5 and re.search(
                rf"(^|\s){re.escape(alias)}($|\s)", normalized_statement
            )
        }
        if not matched_players:
            continue
        status = normalize_availability_status(None, statement)
        if status == "UNKNOWN":
            continue
        game_matches = [row for row in games if team_id in {str(row[1]), str(row[2])}]
        game_id = str(game_matches[0][0]) if len(game_matches) == 1 else None
        tip = game_matches[0][3] if len(game_matches) == 1 else None
        for player_id in sorted(matched_players):
            observations.append(AvailabilityObservation(
                season_code=season_code,
                captured_at=captured_at,
                published_at=published_at,
                timestamp_reliable=publication_reliable,
                source_type="OFFICIAL_EUROLEAGUE",
                source_identifier=source_url,
                canonical_player_id=player_id,
                canonical_team_id=team_id,
                canonical_game_id=game_id,
                matchday_number=matchday,
                effective_game_time=tip,
                raw_status=status,
                normalized_status=status,
                raw_text=statement,
                source_summary=f"Official EuroLeague report team section: {heading}",
                report_id=report_id,
                source_artifact_id=artifact_id,
            ))
    return observations


class _ReportHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.published_at: datetime | None = None
        self.sections: list[tuple[str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._team_heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"h1", "h2", "h3", "li", "p"}:
            self._capture = tag
            self._buffer = []
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").casefold()
            if key in {"article:published_time", "datepublished", "date"}:
                self.published_at = _parse_datetime(attributes.get("content"))

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._capture:
            return
        text = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
        if text:
            if tag == "h1" and self.title is None:
                self.title = text
            elif tag in {"h2", "h3"} and len(text) <= 100:
                self._team_heading = text
            elif tag in {"li", "p"} and self._team_heading:
                self.sections.append((self._team_heading, text))
        self._capture = None
        self._buffer = []


def _matchday_from_text(value: str | None) -> int | None:
    match = re.search(r"round\s+(\d+)", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
