"""Top-level Phase 5A live update orchestration with graceful degradation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database
from src.db.ids import stable_id
from src.modeling.rich_features import _create_rotation_observations
from src.modeling.rich_reconstruction import generate_rotation_intermediates

from .current_features import build_current_slate
from .current_season import current_season_code, update_current_season
from .fantasy import update_current_fantasy_market
from .freshness import record_source_refresh, source_freshness
from .official_availability import (
    collect_official_report,
    load_source_manifest,
    register_discovered_sources,
)


@dataclass(frozen=True, slots=True)
class LiveUpdateResult:
    live_run_id: str
    season_code: str
    status: str
    stages: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    elapsed_seconds: float


def update_live(
    season_code: str | None = None,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    fantasy_config: Path | None = None,
    collect_official_availability: bool = True,
    include_historical_availability: bool = False,
    include_rich: bool = True,
    max_round_metadata_requests: int = 2,
    strict: bool = False,
    dry_run: bool = False,
    as_of: datetime | None = None,
) -> LiveUpdateResult:
    observed = as_of or datetime.now(UTC)
    season_code = (season_code or current_season_code(observed)).upper()
    initialize_database(database_path)
    started = time.monotonic()
    run_id = stable_id("live_update_run", season_code, observed.isoformat(), dry_run)
    with connect_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO live_update_runs (
              live_run_id, season_code, command, started_at, status, dry_run
            ) VALUES (?, ?, 'python -m scripts.update_live', ?, 'RUNNING', ?)
            ON CONFLICT DO NOTHING
            """,
            [run_id, season_code, observed, dry_run],
        )
    stages: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    def stage(name: str, function: Callable[[], Any], *, required: bool = False) -> Any | None:
        try:
            result = function()
            stages[name] = {"status": "SUCCEEDED", "result": _jsonable(result)}
            return result
        except Exception as error:
            message = f"{name} failed: {type(error).__name__}: {error}"
            stages[name] = {"status": "FAILED", "error": message}
            warnings.append(message)
            if strict or required:
                raise
            return None

    try:
        basketball = stage(
            "current_season",
            lambda: update_current_season(
                season_code, database_path=database_path, include_rich=include_rich,
                dry_run=dry_run, captured_at=observed,
                max_round_metadata_requests=max_round_metadata_requests,
            ),
        )
        if basketball is not None:
            record_source_refresh(
                "schedule", "OFFICIAL_EUROLEAGUE_API", True,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed, row_count=basketball.schedule_games,
                source_identifier=f"official_schedule:{season_code}",
                snapshot_batch_id=basketball.snapshot_batch_id,
                message=("dry run" if dry_run else "schedule snapshot retained"),
            )
            record_source_refresh(
                "basketball_stats", "OFFICIAL_EUROLEAGUE_API", True,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed, row_count=basketball.completed_games_ingested,
                source_identifier=f"official_game_feeds:{season_code}",
                message=f"box={basketball.boxscores_ingested}; pbp={basketball.pbp_ingested}; shots={basketball.shots_ingested}",
            )
            warnings.extend(basketball.warnings)
            if not dry_run and basketball.pbp_ingested:
                stage(
                    "rotation_refresh",
                    lambda: _refresh_rotation(database_path, season_code),
                )
        else:
            record_source_refresh(
                "schedule", "OFFICIAL_EUROLEAGUE_API", False,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed, message="schedule update failed; last valid state preserved",
            )
            record_source_refresh(
                "basketball_stats", "OFFICIAL_EUROLEAGUE_API", False,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed, message="basketball update failed; last valid facts preserved",
            )

        if not dry_run:
            fantasy = stage(
                "fantasy_market",
                lambda: update_current_fantasy_market(
                    season_code,
                    fantasy_config,
                    database_path=database_path,
                    captured_at=observed,
                ),
            )
            fantasy_stage = stages.get("fantasy_market", {})
            record_source_refresh(
                "fantasy_market", "OFFICIAL_FANTASY", fantasy is not None,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed,
                row_count=fantasy.entity_rows if fantasy else None,
                source_identifier=(
                    fantasy.config_source if fantasy else
                    str(fantasy_config) if fantasy_config is not None else
                    "official_current_league_bootstrap"
                ),
                message=(
                    fantasy.freshness_message if fantasy else
                    fantasy_stage.get("error") or
                    "Fantasy market update failed; last valid snapshot preserved"
                ),
            )

            sources = load_source_manifest()
            register_discovered_sources(sources, database_path, captured_at=observed)
            selected = [
                source for source in sources
                if include_historical_availability or source.season_code == season_code
            ]
            availability_results = []
            if collect_official_availability:
                for source in selected:
                    availability_results.append(stage(
                        f"availability:{source.season_code}:R{source.matchday_number}",
                        lambda source=source: collect_official_report(
                            source, database_path=database_path, captured_at=observed
                        ),
                    ))
            success_count = sum(
                result is not None and result.parse_status in {"PARSED", "PARTIAL"}
                for result in availability_results
            )
            availability_success = success_count > 0
            record_source_refresh(
                "availability", "OFFICIAL_EUROLEAGUE", availability_success,
                database_path=database_path, live_run_id=run_id,
                captured_at=observed,
                row_count=sum(result.observation_count for result in availability_results if result),
                source_identifier="official_euroleague_injury_reports",
                message=(
                    "no current-season report URL discovered"
                    if not selected else f"{success_count}/{len(selected)} reports parsed"
                ),
            )

            slate = stage(
                "current_slate",
                lambda: build_current_slate(
                    season_code, database_path=database_path, as_of=observed,
                ),
            )
            if slate is not None:
                warnings.extend(slate.warnings)
                record_source_refresh(
                    "current_features", "DERIVED", slate.schema_compatible,
                    database_path=database_path, live_run_id=run_id,
                    captured_at=observed, row_count=slate.row_count,
                    source_identifier=slate.slate_run_id,
                    message="frozen feature schema compatible" if slate.schema_compatible
                    else f"missing features: {slate.missing_features}",
                    content_fingerprint=slate.output_fingerprint,
                )
            else:
                record_source_refresh(
                    "current_features", "DERIVED", False,
                    database_path=database_path, live_run_id=run_id,
                    captured_at=observed,
                    message="current feature/slate generation failed; last valid slate preserved",
                )
        else:
            stages["mutating_live_sources"] = {
                "status": "SKIPPED", "reason": "dry run performs schedule diff only"
            }
        elapsed = time.monotonic() - started
        failures = sum(item["status"] == "FAILED" for item in stages.values())
        status = "PARTIAL" if failures or warnings else "SUCCEEDED"
        output = hashlib.sha256(
            json.dumps(stages, default=str, sort_keys=True).encode()
        ).hexdigest()
        with connect_database(database_path) as connection:
            connection.execute(
                """
                UPDATE live_update_runs SET completed_at=?, status=?,
                  stage_results_json=?, warning_count=?, elapsed_seconds=?,
                  output_fingerprint=? WHERE live_run_id=?
                """,
                [datetime.now(UTC), status, json.dumps(stages, default=str),
                 len(warnings), elapsed, output, run_id],
            )
        return LiveUpdateResult(run_id, season_code, status, stages, tuple(warnings), elapsed)
    except BaseException as error:
        with connect_database(database_path) as connection:
            connection.execute(
                """
                UPDATE live_update_runs SET completed_at=?, status='FAILED',
                  stage_results_json=?, warning_count=?, elapsed_seconds=?
                WHERE live_run_id=?
                """,
                [datetime.now(UTC), json.dumps(stages, default=str),
                 len(warnings) + 1, time.monotonic() - started, run_id],
            )
        raise


def _refresh_rotation(database_path: Path | str, season_code: str) -> dict[str, Any]:
    summary = generate_rotation_intermediates(
        database_path, season_end=season_code
    )
    with connect_database(database_path) as connection:
        _create_rotation_observations(connection)
    return asdict(summary)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
