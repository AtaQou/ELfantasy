"""Sequential, cached, resumable E2007+ historical ingestion orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Literal

from src.data.euroleague_client import APIResponse, EuroLeagueAPIError, EuroLeagueClient
from src.data.normalizers import extract_xml_rows

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from .ids import stable_id
from .ingestion import CanonicalIngestor
from .raw_store import DEFAULT_RAW_ROOT, StoredRawArtifact, archive_bytes, describe_existing_file
from .remote_ingestion import ingest_archived_official_season


Stage = Literal["core", "events"]
PREFERRED_TOTAL_BYTES = 15_000_000_000
WARNING_TOTAL_BYTES = 20_000_000_000
HARD_STOP_TOTAL_BYTES = 25_000_000_000
FailureClass = Literal[
    "TRANSIENT_NETWORK",
    "RATE_LIMIT",
    "SERVER_ERROR",
    "EMPTY_SUPPORTED_ENDPOINT",
    "EXPECTED_LEGACY_EMPTY",
    "MALFORMED_RESPONSE",
    "SCHEMA_MISMATCH",
    "IDENTITY_ERROR",
    "DATA_INTEGRITY_ERROR",
]


@dataclass(frozen=True, slots=True)
class SeasonDownloadResult:
    season_code: str
    stage: Stage
    expected_games: int
    successful_components: int
    failed_components: int
    network_requests: int
    cached_components: int
    elapsed_seconds: float


class BulkHistoryRunner:
    """Download sequentially, checkpoint raw files, then normalize offline."""

    def __init__(
        self,
        *,
        database_path: Path | str = DEFAULT_DATABASE_PATH,
        raw_root: Path = DEFAULT_RAW_ROOT,
        pace_seconds: float = 0.25,
        timeout_seconds: float = 45.0,
        limit_games: int | None = None,
    ) -> None:
        if pace_seconds < 0:
            raise ValueError("pace_seconds must be non-negative")
        if limit_games is not None and limit_games <= 0:
            raise ValueError("limit_games must be positive")
        self.database_path = Path(database_path)
        self.raw_root = raw_root
        self.pace_seconds = pace_seconds
        self.timeout_seconds = timeout_seconds
        self.limit_games = limit_games
        initialize_database(self.database_path)

    def run(self, seasons: Sequence[int], stage: Stage) -> list[SeasonDownloadResult]:
        self._assert_storage_budget()
        results: list[SeasonDownloadResult] = []
        with EuroLeagueClient(timeout_seconds=self.timeout_seconds) as client:
            for season in seasons:
                if season < 2007:
                    raise ValueError("Detailed bulk history starts at E2007")
                result = self._run_season(client, season, stage)
                results.append(result)
                print(
                    f"{result.season_code} {stage}: expected={result.expected_games} "
                    f"ok={result.successful_components} failed={result.failed_components} "
                    f"network={result.network_requests} cached={result.cached_components} "
                    f"elapsed={result.elapsed_seconds:.1f}s",
                    flush=True,
                )
                self._assert_storage_budget()
        return results

    def _run_season(
        self, client: EuroLeagueClient, season: int, stage: Stage
    ) -> SeasonDownloadResult:
        started = time.monotonic()
        season_code = client.season_code(season)
        season_root = self.raw_root / "euroleague" / season_code
        with connect_database(self.database_path) as connection:
            run = CanonicalIngestor(
                connection,
                source=f"bulk_download_{stage}",
                command=f"python -m scripts.ingest_history --stage {stage}",
                competition_code="E",
                season_code=season_code,
            )
            state = {"network": 0, "cached": 0, "ok": 0, "failed": 0}
            try:
                results_path = season_root / "results" / "season.xml"
                results_text = self._xml_component(
                    run,
                    client,
                    season_code,
                    None,
                    "RESULTS",
                    results_path,
                    lambda: client.results(season),
                    "game",
                    state,
                )
                if results_text is None:
                    raise RuntimeError(f"{season_code} results listing is unavailable")
                result_rows = extract_xml_rows(results_text, "game")
                completed = [
                    row
                    for row in result_rows
                    if str(row.get("played", "")).casefold() == "true"
                    and str(row.get("gamenumber", "")).isdigit()
                ]
                completed.sort(key=lambda row: int(row["gamenumber"]))
                selected = completed[: self.limit_games] if self.limit_games else completed

                if stage == "core":
                    self._download_core(
                        run, client, season, season_code, season_root, selected, state
                    )
                else:
                    self._download_events(
                        run, client, season, season_code, season_root, selected, state
                    )
                run.artifact_count = state["network"]
                run.finish()
            except BaseException as error:
                run.fail(error)
                raise

        components = (
            frozenset({"core"})
            if stage == "core"
            else frozenset({"play_by_play", "shots"})
        )
        try:
            ingest_archived_official_season(
                season,
                database_path=self.database_path,
                raw_root=self.raw_root,
                components=components,
            )
        except Exception as error:
            with connect_database(self.database_path) as connection:
                self._record_failure(
                    connection,
                    None,
                    season_code,
                    None,
                    f"NORMALIZE_{stage.upper()}",
                    _classify_exception(error, normalization=True),
                    str(error),
                )
            raise
        with connect_database(self.database_path) as connection:
            _resolve_failure(
                connection,
                season_code,
                None,
                f"NORMALIZE_{stage.upper()}",
            )
        self._mark_normalized(season_code, stage)
        return SeasonDownloadResult(
            season_code=season_code,
            stage=stage,
            expected_games=len(completed),
            successful_components=state["ok"],
            failed_components=state["failed"],
            network_requests=state["network"],
            cached_components=state["cached"],
            elapsed_seconds=time.monotonic() - started,
        )

    def _assert_storage_budget(self) -> None:
        """Enforce the revised project-wide storage safety boundaries."""

        raw_bytes = _directory_size(self.raw_root)
        database_bytes = (
            self.database_path.stat().st_size if self.database_path.is_file() else 0
        )
        total_bytes = raw_bytes + database_bytes
        if total_bytes >= HARD_STOP_TOTAL_BYTES:
            raise RuntimeError(
                "Historical ingestion hard stop: local raw plus DuckDB storage "
                f"is {total_bytes / 1_000_000_000:.2f} GB, at or above the "
                "25 GB policy limit. Explicit user approval is required."
            )
        if total_bytes >= WARNING_TOTAL_BYTES:
            print(
                "WARNING: local raw plus DuckDB storage is "
                f"{total_bytes / 1_000_000_000:.2f} GB, above the 20 GB "
                "warning threshold.",
                flush=True,
            )

    def _download_core(
        self,
        run: CanonicalIngestor,
        client: EuroLeagueClient,
        season: int,
        season_code: str,
        season_root: Path,
        completed: Sequence[Mapping[str, Any]],
        state: dict[str, int],
    ) -> None:
        self._xml_component(
            run,
            client,
            season_code,
            None,
            "SCHEDULE",
            season_root / "schedules" / "season.xml",
            lambda: client.schedule(season),
            "item",
            state,
        )
        self._json_component(
            run,
            season_code,
            None,
            "ROSTER",
            season_root / "rosters" / "season_players.json",
            lambda: client.season_players(season),
            _valid_roster,
            state,
        )
        gamedays = sorted(
            {
                int(value)
                for row in completed
                if (value := str(row.get("gameday", ""))).isdigit()
            }
        )
        for gameday in gamedays:
            self._json_component(
                run,
                season_code,
                None,
                f"ROUND_{gameday}",
                season_root / "games" / f"round_{gameday}.json",
                lambda gameday=gameday: client.round_games(season, gameday),
                _valid_round,
                state,
            )
        total = len(completed)
        for index, row in enumerate(completed, start=1):
            game_code = int(row["gamenumber"])
            self._json_component(
                run,
                season_code,
                game_code,
                "BOXSCORE",
                season_root / "boxscores" / f"{game_code}.json",
                lambda game_code=game_code: client.game_boxscore(season, game_code),
                _valid_boxscore,
                state,
            )
            if index % 25 == 0 or index == total:
                print(
                    f"  {season_code} core {index}/{total} "
                    f"network={state['network']} failed={state['failed']}",
                    flush=True,
                )

    def _download_events(
        self,
        run: CanonicalIngestor,
        client: EuroLeagueClient,
        season: int,
        season_code: str,
        season_root: Path,
        completed: Sequence[Mapping[str, Any]],
        state: dict[str, int],
    ) -> None:
        total = len(completed)
        for index, row in enumerate(completed, start=1):
            game_code = int(row["gamenumber"])
            self._json_component(
                run,
                season_code,
                game_code,
                "PLAY_BY_PLAY",
                season_root / "play_by_play" / f"{game_code}.json",
                lambda game_code=game_code: client.game_play_by_play(season, game_code),
                _valid_pbp,
                state,
            )
            self._json_component(
                run,
                season_code,
                game_code,
                "SHOTS",
                season_root / "shots" / f"{game_code}.json",
                lambda game_code=game_code: client.game_shots(season, game_code),
                _valid_shots,
                state,
            )
            if index % 25 == 0 or index == total:
                print(
                    f"  {season_code} events {index}/{total} "
                    f"network={state['network']} failed={state['failed']}",
                    flush=True,
                )

    def _xml_component(
        self,
        run: CanonicalIngestor,
        client: EuroLeagueClient,
        season_code: str,
        game_code: int | None,
        component: str,
        path: Path,
        fetch: Callable[[], APIResponse],
        item_tag: str,
        state: dict[str, int],
    ) -> str | None:
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
                if extract_xml_rows(text, item_tag):
                    self._success(run, season_code, game_code, component, path, None)
                    state["cached"] += 1
                    state["ok"] += 1
                    return text
            except Exception:
                pass
        response = self._request(
            run, season_code, game_code, component, fetch, state
        )
        if response is None:
            return None
        artifact = archive_bytes(
            response.text.encode("utf-8"),
            path.relative_to(self.raw_root),
            raw_root=self.raw_root,
        )
        try:
            rows = extract_xml_rows(response.text, item_tag)
        except Exception as error:
            self._failure(
                run,
                season_code,
                game_code,
                component,
                "MALFORMED_RESPONSE",
                str(error),
                state,
            )
            return None
        if not rows:
            self._failure(
                run,
                season_code,
                game_code,
                component,
                "EMPTY_SUPPORTED_ENDPOINT",
                "XML response contained no expected rows",
                state,
            )
            return None
        self._success(run, season_code, game_code, component, artifact.path, artifact)
        state["ok"] += 1
        return response.text

    def _json_component(
        self,
        run: CanonicalIngestor,
        season_code: str,
        game_code: int | None,
        component: str,
        path: Path,
        fetch: Callable[[], APIResponse],
        validator: Callable[[Any], tuple[bool, int]],
        state: dict[str, int],
    ) -> Any | None:
        candidates = [path]
        if path.parent.is_dir():
            candidates.extend(
                candidate
                for candidate in sorted(
                    path.parent.glob(f"{path.stem}.*{path.suffix}")
                )
                if candidate != path
            )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                valid, count = validator(payload)
                if valid:
                    self._success(
                        run,
                        season_code,
                        game_code,
                        component,
                        candidate,
                        None,
                        count,
                    )
                    state["cached"] += 1
                    state["ok"] += 1
                    return payload
            except Exception:
                continue
        response = self._request(
            run, season_code, game_code, component, fetch, state
        )
        if response is None:
            return None
        artifact = archive_bytes(
            response.text.encode("utf-8"),
            path.relative_to(self.raw_root),
            raw_root=self.raw_root,
        )
        payload = response.payload
        valid, count = validator(payload)
        if not valid:
            self._failure(
                run,
                season_code,
                game_code,
                component,
                "EMPTY_SUPPORTED_ENDPOINT",
                f"Structured response failed validation; observed rows={count}",
                state,
            )
            return None
        self._success(
            run, season_code, game_code, component, artifact.path, artifact, count
        )
        state["ok"] += 1
        return payload

    def _request(
        self,
        run: CanonicalIngestor,
        season_code: str,
        game_code: int | None,
        component: str,
        fetch: Callable[[], APIResponse],
        state: dict[str, int],
    ) -> APIResponse | None:
        state["network"] += 1
        try:
            return fetch()
        except Exception as error:
            failure_class = _classify_exception(error)
            self._failure(
                run,
                season_code,
                game_code,
                component,
                failure_class,
                str(error),
                state,
            )
            return None
        finally:
            if self.pace_seconds:
                time.sleep(self.pace_seconds)

    def _success(
        self,
        run: CanonicalIngestor,
        season_code: str,
        game_code: int | None,
        component: str,
        path: Path,
        artifact: StoredRawArtifact | None,
        record_count: int | None = None,
    ) -> None:
        described = artifact or describe_existing_file(path)
        try:
            raw_path = path.relative_to(Path(__file__).resolve().parents[2]).as_posix()
        except ValueError:
            raw_path = str(path)
        _upsert_checkpoint(
            run.connection,
            run.run_id,
            season_code,
            game_code,
            component,
            "RAW_AVAILABLE",
            raw_path,
            described.sha256,
            record_count,
            None,
        )
        _resolve_failure(run.connection, season_code, game_code, component)

    def _failure(
        self,
        run: CanonicalIngestor,
        season_code: str,
        game_code: int | None,
        component: str,
        failure_class: FailureClass,
        message: str,
        state: dict[str, int],
    ) -> None:
        state["failed"] += 1
        self._record_failure(
            run.connection,
            run.run_id,
            season_code,
            game_code,
            component,
            failure_class,
            message,
        )
        _upsert_checkpoint(
            run.connection,
            run.run_id,
            season_code,
            game_code,
            component,
            "FAILED",
            None,
            None,
            None,
            failure_class,
        )

    def _record_failure(
        self,
        connection: Any,
        run_id: str | None,
        season_code: str,
        game_code: int | None,
        component: str,
        failure_class: FailureClass,
        message: str,
    ) -> None:
        failure_id = stable_id(
            "ingestion_failure", "official_euroleague_api", season_code, game_code, component
        )
        now = datetime.now(UTC)
        existing = connection.execute(
            "SELECT attempt_count FROM ingestion_failures WHERE failure_id = ?",
            [failure_id],
        ).fetchone()
        safe_message = " ".join(message.split())[:2000]
        if existing:
            connection.execute(
                """
                UPDATE ingestion_failures
                SET failure_class = ?, retryable = ?, last_occurred_at = ?,
                    attempt_count = ?, last_error = ?, resolved_at = NULL,
                    last_ingestion_run_id = ?
                WHERE failure_id = ?
                """,
                [
                    failure_class,
                    failure_class in {
                        "TRANSIENT_NETWORK",
                        "RATE_LIMIT",
                        "SERVER_ERROR",
                        "EMPTY_SUPPORTED_ENDPOINT",
                    },
                    now,
                    int(existing[0]) + 1,
                    safe_message,
                    run_id,
                    failure_id,
                ],
            )
        else:
            connection.execute(
                """
                INSERT INTO ingestion_failures (
                    failure_id, source, season_code, game_code, component,
                    failure_class, retryable, first_occurred_at, last_occurred_at,
                    attempt_count, last_error, resolved_at, last_ingestion_run_id
                ) VALUES (?, 'official_euroleague_api', ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?)
                """,
                [
                    failure_id,
                    season_code,
                    game_code,
                    component,
                    failure_class,
                    failure_class in {
                        "TRANSIENT_NETWORK",
                        "RATE_LIMIT",
                        "SERVER_ERROR",
                        "EMPTY_SUPPORTED_ENDPOINT",
                    },
                    now,
                    now,
                    safe_message,
                    run_id,
                ],
            )

    def _mark_normalized(self, season_code: str, stage: Stage) -> None:
        with connect_database(self.database_path) as connection:
            components = (
                ("BOXSCORE",)
                if stage == "core"
                else ("PLAY_BY_PLAY", "SHOTS")
            )
            for component in components:
                rows = connection.execute(
                    """
                    SELECT checkpoint_id FROM ingestion_checkpoints
                    WHERE season_code = ? AND component = ?
                      AND status = 'RAW_AVAILABLE'
                    """,
                    [season_code, component],
                ).fetchall()
                if rows:
                    connection.executemany(
                        """
                        UPDATE ingestion_checkpoints
                        SET status = 'NORMALIZED', updated_at = ?
                        WHERE checkpoint_id = ?
                        """,
                        [[datetime.now(UTC), row[0]] for row in rows],
                    )


def _valid_roster(payload: Any) -> tuple[bool, int]:
    rows = payload.get("data", []) if isinstance(payload, Mapping) else []
    players = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and (row.get("type") == "J" or row.get("typeName") == "Player")
    ]
    return bool(players), len(players)


def _valid_round(payload: Any) -> tuple[bool, int]:
    rows = payload.get("data", []) if isinstance(payload, Mapping) else []
    return bool(rows), len(rows)


def _valid_boxscore(payload: Any) -> tuple[bool, int]:
    sides = payload.get("Stats", []) if isinstance(payload, Mapping) else []
    players = sum(
        len(side.get("PlayersStats", []))
        for side in sides
        if isinstance(side, Mapping)
    )
    both_sides_have_players = len(sides) == 2 and all(
        isinstance(side, Mapping) and bool(side.get("PlayersStats")) for side in sides
    )
    return both_sides_have_players and players > 0, players


def _valid_pbp(payload: Any) -> tuple[bool, int]:
    if not isinstance(payload, Mapping):
        return False, 0
    count = sum(
        len(payload.get(key, []))
        for key in (
            "FirstQuarter",
            "SecondQuarter",
            "ThirdQuarter",
            "ForthQuarter",
            "ExtraTime",
        )
        if isinstance(payload.get(key, []), list)
    )
    return count > 0, count


def _valid_shots(payload: Any) -> tuple[bool, int]:
    rows = payload.get("Rows", []) if isinstance(payload, Mapping) else []
    return bool(rows), len(rows)


def _classify_exception(
    error: BaseException, *, normalization: bool = False
) -> FailureClass:
    text = str(error).casefold()
    if normalization:
        if "identity" in text or "player" in text and "missing" in text:
            return "IDENTITY_ERROR"
        return "DATA_INTEGRITY_ERROR"
    if "429" in text or "rate" in text and "limit" in text:
        return "RATE_LIMIT"
    if any(code in text for code in (" 500", " 502", " 503", " 504")):
        return "SERVER_ERROR"
    if "invalid json" in text or "not valid json" in text:
        return "MALFORMED_RESPONSE"
    if isinstance(error, EuroLeagueAPIError):
        return "TRANSIENT_NETWORK"
    return "SCHEMA_MISMATCH"


def _upsert_checkpoint(
    connection: Any,
    run_id: str | None,
    season_code: str,
    game_code: int | None,
    component: str,
    status: str,
    raw_path: str | None,
    content_hash: str | None,
    record_count: int | None,
    error_class: str | None,
) -> None:
    checkpoint_id = stable_id("checkpoint", season_code, game_code, component)
    values = [
        status,
        raw_path,
        content_hash,
        record_count,
        datetime.now(UTC),
        run_id,
        error_class,
        checkpoint_id,
    ]
    if connection.execute(
        "SELECT 1 FROM ingestion_checkpoints WHERE checkpoint_id = ?",
        [checkpoint_id],
    ).fetchone():
        connection.execute(
            """
            UPDATE ingestion_checkpoints
            SET status = ?, raw_path = ?, content_sha256 = ?, record_count = ?,
                updated_at = ?, ingestion_run_id = ?, error_class = ?
            WHERE checkpoint_id = ?
            """,
            values,
        )
    else:
        connection.execute(
            """
            INSERT INTO ingestion_checkpoints (
                status, raw_path, content_sha256, record_count, updated_at,
                ingestion_run_id, error_class, checkpoint_id,
                season_code, game_code, component
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values[:-1]
            + [checkpoint_id, season_code, game_code, component],
        )


def _resolve_failure(
    connection: Any,
    season_code: str,
    game_code: int | None,
    component: str,
) -> None:
    failure_id = stable_id(
        "ingestion_failure", "official_euroleague_api", season_code, game_code, component
    )
    connection.execute(
        """
        UPDATE ingestion_failures SET resolved_at = ?
        WHERE failure_id = ? AND resolved_at IS NULL
        """,
        [datetime.now(UTC), failure_id],
    )


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
