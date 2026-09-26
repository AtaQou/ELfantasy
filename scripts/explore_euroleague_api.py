#!/usr/bin/env python3
"""Explore real EuroLeague responses, save a small sample, and probe coverage.

Examples:
    python -m scripts.explore_euroleague_api sample
    python -m scripts.explore_euroleague_api coverage
    python -m scripts.explore_euroleague_api all
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.euroleague_client import (  # noqa: E402
    APIResponse,
    EuroLeagueAPIError,
    EuroLeagueClient,
)
from src.data.normalizers import (  # noqa: E402
    data_quality_summary,
    extract_xml_rows,
    normalize_games,
    normalize_play_by_play,
    normalize_player_boxscore,
    normalize_roster_players,
    normalize_shots,
    normalize_team_boxscore,
    schema_inventory,
    xml_roster_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples"
DEFAULT_COVERAGE_SEASONS = [
    2025,
    2024,
    2023,
    2022,
    2021,
    2020,
    2018,
    2015,
    2010,
    2009,
    2008,
    2007,
    2006,
    2005,
    2000,
]
LOGGER = logging.getLogger("euroleague_api_explorer")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit EuroLeague public API data without downloading full history."
    )
    parser.add_argument("--competition", default="E", choices=("E", "U"))
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample", help="Download and normalize a small real sample")
    sample.add_argument("--season", type=int, default=2025, help="Season starting year")
    sample.add_argument("--rounds", type=int, nargs="+", default=[1, 2])
    sample.add_argument("--max-games", type=int, default=3)
    sample.add_argument("--output", type=Path, help="Output directory")

    coverage = subparsers.add_parser(
        "coverage", help="Probe representative games across historical seasons"
    )
    coverage.add_argument(
        "--seasons", type=int, nargs="+", default=DEFAULT_COVERAGE_SEASONS
    )
    coverage.add_argument(
        "--probe-games",
        type=int,
        default=2,
        choices=(1, 2, 3),
        help="Representative played games tested per season",
    )
    coverage.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SAMPLE_ROOT / "historical_coverage.json",
    )

    all_command = subparsers.add_parser("all", help="Run the default sample and coverage audit")
    all_command.add_argument("--season", type=int, default=2025)
    all_command.add_argument("--rounds", type=int, nargs="+", default=[1, 2])
    all_command.add_argument("--max-games", type=int, default=3)
    all_command.add_argument(
        "--seasons", type=int, nargs="+", default=DEFAULT_COVERAGE_SEASONS
    )
    all_command.add_argument("--probe-games", type=int, default=2, choices=(1, 2, 3))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        with EuroLeagueClient(
            args.competition,
            timeout_seconds=args.timeout,
        ) as client:
            if args.command == "sample":
                output = args.output or default_sample_directory(
                    args.competition, args.season, args.rounds
                )
                create_sample(client, args.season, args.rounds, args.max_games, output)
            elif args.command == "coverage":
                run_coverage(client, args.seasons, args.probe_games, args.output)
            else:
                output = default_sample_directory(args.competition, args.season, args.rounds)
                create_sample(client, args.season, args.rounds, args.max_games, output)
                run_coverage(
                    client,
                    args.seasons,
                    args.probe_games,
                    DEFAULT_SAMPLE_ROOT / "historical_coverage.json",
                )
    except (EuroLeagueAPIError, OSError, ParseError, ValueError) as exc:
        LOGGER.error("Audit failed: %s", exc)
        return 1
    return 0


def default_sample_directory(
    competition: str, season: int, rounds: Sequence[int]
) -> Path:
    round_label = "_".join(str(value) for value in rounds)
    return DEFAULT_SAMPLE_ROOT / f"{competition.lower()}{season}_rounds_{round_label}"


def create_sample(
    client: EuroLeagueClient,
    season: int,
    rounds: Sequence[int],
    max_games: int,
    output_directory: Path,
) -> None:
    """Download several games from a few rounds and produce normalized CSVs."""

    if not rounds or any(round_number <= 0 for round_number in rounds):
        raise ValueError("rounds must contain positive integers")
    if max_games <= 0:
        raise ValueError("max_games must be positive")

    raw_directory = output_directory / "raw"
    raw_directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "description": "Small EuroLeague data-discovery sample; not a full-season download.",
        "competition": client.competition,
        "season": season,
        "season_code": client.season_code(season),
        "rounds": list(rounds),
        "requested_max_games": max_games,
        "responses": [],
        "errors": [],
    }
    schemas: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    games_by_round: dict[int, list[dict[str, Any]]] = {}
    for round_number in dict.fromkeys(rounds):
        LOGGER.info("Fetching %s round %s", client.season_code(season), round_number)
        response = client.round_games(season, round_number)
        save_response(response, raw_directory / f"round_{round_number}_games.json", manifest)
        merge_schema(schemas[response.endpoint], schema_inventory(response.payload))
        payload = _mapping(response.payload)
        games_by_round[round_number] = [
            dict(item) for item in _list(payload.get("data")) if isinstance(item, Mapping)
        ]

    all_games = deduplicate_games(
        game for round_games in games_by_round.values() for game in round_games
    )
    selected_games = select_games_across_rounds(games_by_round, max_games)
    if not selected_games:
        raise ValueError("No played games were returned for the requested rounds")
    manifest["selected_game_codes"] = [game.get("gameCode") for game in selected_games]

    normalized_games = normalize_games(all_games)
    player_rows: list[dict[str, Any]] = []
    team_rows: list[dict[str, Any]] = []
    pbp_rows: list[dict[str, Any]] = []
    shot_rows: list[dict[str, Any]] = []
    roster_rows: list[dict[str, Any]] = []

    try:
        roster_response = client.season_players(season)
        save_response(roster_response, raw_directory / "season_players.json", manifest)
        merge_schema(schemas[roster_response.endpoint], schema_inventory(roster_response.payload))
        roster_rows = normalize_roster_players(_mapping(roster_response.payload))
    except EuroLeagueAPIError as exc:
        record_error(manifest, "v2_season_players", None, exc)

    endpoint_calls: list[tuple[str, Callable[[int, int], APIResponse]]] = [
        ("boxscore", client.game_boxscore),
        ("play_by_play", client.game_play_by_play),
        ("shots", client.game_shots),
        ("header", client.game_header),
        ("v3_report", client.game_report),
        ("v3_stats", client.game_stats),
        ("v3_teams_comparison", client.game_team_comparison),
    ]

    game_lookup = {int(game["gameCode"]): game for game in selected_games}
    for game_code, game in game_lookup.items():
        LOGGER.info("Fetching %s game %s", client.season_code(season), game_code)
        responses: dict[str, APIResponse] = {}
        for label, call in endpoint_calls:
            try:
                response = call(season, game_code)
                responses[label] = response
                save_response(
                    response,
                    raw_directory / f"game_{game_code}_{label}.json",
                    manifest,
                )
                merge_schema(schemas[response.endpoint], schema_inventory(response.payload))
            except EuroLeagueAPIError as exc:
                record_error(manifest, label, game_code, exc)

        if "boxscore" in responses:
            payload = _mapping(responses["boxscore"].payload)
            player_rows.extend(normalize_player_boxscore(payload, game))
            team_rows.extend(normalize_team_boxscore(payload, game))
        if "play_by_play" in responses:
            pbp_rows.extend(
                normalize_play_by_play(
                    _mapping(responses["play_by_play"].payload),
                    season_code=client.season_code(season),
                    game_code=game_code,
                )
            )
        if "shots" in responses:
            shot_rows.extend(
                normalize_shots(
                    _mapping(responses["shots"].payload),
                    season_code=client.season_code(season),
                    game_code=game_code,
                )
            )

    write_csv(output_directory / "games.csv", normalized_games)
    write_csv(output_directory / "player_boxscores.csv", player_rows)
    write_csv(output_directory / "team_boxscores.csv", team_rows)
    write_csv(output_directory / "play_by_play.csv", pbp_rows)
    write_csv(output_directory / "shots.csv", shot_rows)
    write_csv(output_directory / "roster_players.csv", roster_rows)

    quality = data_quality_summary(
        normalized_games,
        player_rows,
        team_rows,
        pbp_rows,
        shot_rows,
        roster_rows,
    )
    write_json(output_directory / "data_quality_summary.json", quality)
    write_json(output_directory / "schema_inventory.json", finalize_schemas(schemas))
    manifest["normalized_row_counts"] = quality["row_counts"]
    write_json(output_directory / "manifest.json", manifest)
    LOGGER.info("Sample saved to %s", output_directory)


def run_coverage(
    client: EuroLeagueClient,
    seasons: Sequence[int],
    probe_games: int,
    output_file: Path,
) -> None:
    """Probe a few representative games per season rather than full history."""

    if not seasons:
        raise ValueError("at least one season is required")
    summaries: list[dict[str, Any]] = []
    for season in dict.fromkeys(seasons):
        LOGGER.info("Coverage probe for %s", client.season_code(season))
        summaries.append(probe_season(client, season, probe_games))

    document = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "method": (
            "Availability probe only: full v1 schedule/results and roster metadata, "
            f"plus {probe_games} representative played game(s) per season. "
            "It is not an exhaustive integrity scan of every historical game."
        ),
        "competition": client.competition,
        "probe_games_per_season": probe_games,
        "seasons": summaries,
    }
    write_json(output_file, document)
    write_coverage_csv(output_file.with_suffix(".csv"), summaries)
    LOGGER.info("Coverage summary saved to %s", output_file)


def probe_season(
    client: EuroLeagueClient,
    season: int,
    probe_games: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "season": season,
        "season_code": client.season_code(season),
        "schedule": unavailable_probe(),
        "results": unavailable_probe(),
        "rosters": unavailable_probe(),
        "boxscore": unavailable_probe(),
        "player_stats": unavailable_probe(),
        "team_stats": unavailable_probe(),
        "play_by_play": unavailable_probe(),
        "shots": unavailable_probe(),
        "v3_game_stats": unavailable_probe(),
        "representative_game_codes": [],
    }

    result_rows: list[dict[str, Any]] = []
    try:
        response = client.schedule(season)
        rows = extract_xml_rows(response.text, "item")
        summary["schedule"] = success_probe(
            len(rows), union_fields(rows), response.url, tested=1, successes=int(bool(rows))
        )
    except (EuroLeagueAPIError, ParseError, ValueError) as exc:
        summary["schedule"]["errors"].append(str(exc))

    try:
        response = client.results(season)
        result_rows = extract_xml_rows(response.text, "game")
        summary["results"] = success_probe(
            len(result_rows),
            union_fields(result_rows),
            response.url,
            tested=1,
            successes=int(bool(result_rows)),
        )
    except (EuroLeagueAPIError, ParseError, ValueError) as exc:
        summary["results"]["errors"].append(str(exc))

    try:
        response = client.season_teams_xml(season)
        roster = xml_roster_summary(response.text)
        summary["rosters"] = success_probe(
            roster["player_membership_count"],
            roster["player_fields"],
            response.url,
            tested=1,
            successes=int(roster["player_membership_count"] > 0),
        )
        summary["rosters"]["club_count"] = roster["club_count"]
    except (EuroLeagueAPIError, ParseError, ValueError) as exc:
        summary["rosters"]["errors"].append(str(exc))

    played_games = sorted(
        (
            row
            for row in result_rows
            if str(row.get("played", "")).lower() == "true"
            and str(row.get("gamenumber", "")).isdigit()
        ),
        key=lambda row: int(row["gamenumber"]),
    )
    selected = representative_items(played_games, probe_games)
    game_codes = [int(row["gamenumber"]) for row in selected]
    summary["representative_game_codes"] = game_codes
    if not game_codes:
        no_games_error = "No played game code could be selected from v1 results"
        for key in (
            "boxscore",
            "player_stats",
            "team_stats",
            "play_by_play",
            "shots",
            "v3_game_stats",
        ):
            summary[key]["errors"].append(no_games_error)
        return summary

    probes: dict[str, dict[str, Any]] = {
        key: {"counts": [], "fields": set(), "urls": [], "errors": [], "successes": 0}
        for key in (
            "boxscore",
            "player_stats",
            "team_stats",
            "play_by_play",
            "shots",
            "v3_game_stats",
        )
    }
    for game_code in game_codes:
        probe_game_endpoints(client, season, game_code, probes)

    for key, probe in probes.items():
        counts = probe["counts"]
        summary[key] = {
            "available": probe["successes"] == len(game_codes),
            "partially_available": 0 < probe["successes"] < len(game_codes),
            "tested": len(game_codes),
            "successes": probe["successes"],
            "record_counts": counts,
            "record_count_min": min(counts) if counts else 0,
            "observed_fields": sorted(probe["fields"]),
            "source_urls": probe["urls"],
            "errors": probe["errors"],
        }
    return summary


def probe_game_endpoints(
    client: EuroLeagueClient,
    season: int,
    game_code: int,
    probes: dict[str, dict[str, Any]],
) -> None:
    try:
        response = client.game_boxscore(season, game_code)
        payload = _mapping(response.payload)
        sides = [_mapping(item) for item in _list(payload.get("Stats"))]
        players = [
            _mapping(player)
            for side in sides
            for player in _list(side.get("PlayersStats"))
        ]
        team_totals = [_mapping(side.get("totr")) for side in sides]
        update_probe(probes["boxscore"], len(sides), payload.keys(), response.url, bool(sides))
        update_probe(
            probes["player_stats"],
            len(players),
            union_fields(players),
            response.url,
            bool(players),
        )
        update_probe(
            probes["team_stats"],
            len(team_totals),
            union_fields(team_totals),
            response.url,
            bool(team_totals),
        )
    except EuroLeagueAPIError as exc:
        for key in ("boxscore", "player_stats", "team_stats"):
            probes[key]["errors"].append(f"game {game_code}: {exc}")

    try:
        response = client.game_play_by_play(season, game_code)
        payload = _mapping(response.payload)
        events = [
            _mapping(event)
            for key in ("FirstQuarter", "SecondQuarter", "ThirdQuarter", "ForthQuarter", "ExtraTime")
            for event in _flatten_one_level(_list(payload.get(key)))
        ]
        update_probe(
            probes["play_by_play"],
            len(events),
            union_fields(events),
            response.url,
            bool(events),
        )
    except EuroLeagueAPIError as exc:
        probes["play_by_play"]["errors"].append(f"game {game_code}: {exc}")

    try:
        response = client.game_shots(season, game_code)
        rows = [_mapping(item) for item in _list(_mapping(response.payload).get("Rows"))]
        update_probe(
            probes["shots"], len(rows), union_fields(rows), response.url, bool(rows)
        )
    except EuroLeagueAPIError as exc:
        probes["shots"]["errors"].append(f"game {game_code}: {exc}")

    try:
        response = client.game_stats(season, game_code)
        payload = _mapping(response.payload)
        player_count = sum(
            len(_list(_mapping(payload.get(side)).get("players")))
            for side in ("local", "road")
        )
        fields = schema_inventory(payload).keys()
        update_probe(
            probes["v3_game_stats"],
            player_count,
            fields,
            response.url,
            player_count > 0,
        )
    except EuroLeagueAPIError as exc:
        probes["v3_game_stats"]["errors"].append(f"game {game_code}: {exc}")


def save_response(response: APIResponse, path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    manifest["responses"].append(
        {
            "endpoint": response.endpoint,
            "url": response.url,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "fetched_at_utc": response.fetched_at_utc,
            "relative_path": str(path.relative_to(path.parents[2])),
            "bytes": response.byte_count,
            "sha256": response.sha256,
        }
    )


def record_error(
    manifest: dict[str, Any], endpoint: str, game_code: int | None, exc: Exception
) -> None:
    LOGGER.warning("%s failed for game %s: %s", endpoint, game_code, exc)
    manifest["errors"].append(
        {"endpoint": endpoint, "game_code": game_code, "error": str(exc)}
    )


def select_games_across_rounds(
    games_by_round: Mapping[int, list[dict[str, Any]]], max_games: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_codes: set[Any] = set()
    playable = {
        round_number: sorted(
            (game for game in games if game.get("played") is True),
            key=lambda game: int(game.get("gameCode") or 0),
        )
        for round_number, games in games_by_round.items()
    }
    for games in playable.values():
        if games and len(selected) < max_games:
            selected.append(games[0])
            selected_codes.add(games[0].get("gameCode"))
    for games in playable.values():
        for game in games:
            if len(selected) >= max_games:
                return selected
            if game.get("gameCode") not in selected_codes:
                selected.append(game)
                selected_codes.add(game.get("gameCode"))
    return selected


def deduplicate_games(games: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[Any, dict[str, Any]] = {}
    for game in games:
        key = game.get("id") or game.get("identifier") or game.get("gameCode")
        unique.setdefault(key, game)
    return sorted(unique.values(), key=lambda game: int(game.get("gameCode") or 0))


def representative_items(items: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if len(items) <= count:
        return list(items)
    if count == 1:
        indices = [0]
    elif count == 2:
        indices = [0, len(items) - 1]
    else:
        indices = [0, len(items) // 2, len(items) - 1]
    return [items[index] for index in dict.fromkeys(indices)]


def merge_schema(
    target: dict[str, set[str]], observed: Mapping[str, Sequence[str]]
) -> None:
    for path, types in observed.items():
        target[path].update(types)


def finalize_schemas(
    schemas: Mapping[str, Mapping[str, set[str]]]
) -> dict[str, dict[str, list[str]]]:
    return {
        endpoint: {path: sorted(types) for path, types in sorted(paths.items())}
        for endpoint, paths in sorted(schemas.items())
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fieldnames.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fieldnames:
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def write_coverage_csv(path: Path, summaries: Sequence[Mapping[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for season in summaries:
        for endpoint in (
            "schedule",
            "results",
            "rosters",
            "boxscore",
            "player_stats",
            "team_stats",
            "play_by_play",
            "shots",
            "v3_game_stats",
        ):
            probe = _mapping(season.get(endpoint))
            rows.append(
                {
                    "season": season.get("season"),
                    "season_code": season.get("season_code"),
                    "data": endpoint,
                    "available": probe.get("available"),
                    "partially_available": probe.get("partially_available", False),
                    "tested": probe.get("tested"),
                    "successes": probe.get("successes"),
                    "record_count": probe.get("record_count"),
                    "record_count_min": probe.get("record_count_min"),
                    "observed_fields": "|".join(probe.get("observed_fields", [])),
                    "errors": " | ".join(probe.get("errors", [])),
                }
            )
    write_csv(path, rows)


def unavailable_probe() -> dict[str, Any]:
    return {
        "available": False,
        "partially_available": False,
        "tested": 0,
        "successes": 0,
        "record_count": 0,
        "observed_fields": [],
        "source_urls": [],
        "errors": [],
    }


def success_probe(
    record_count: int,
    fields: Iterable[str],
    url: str,
    *,
    tested: int,
    successes: int,
) -> dict[str, Any]:
    return {
        "available": successes == tested and successes > 0,
        "partially_available": 0 < successes < tested,
        "tested": tested,
        "successes": successes,
        "record_count": record_count,
        "observed_fields": sorted(fields),
        "source_urls": [url],
        "errors": [],
    }


def update_probe(
    probe: dict[str, Any],
    count: int,
    fields: Iterable[str],
    url: str,
    success: bool,
) -> None:
    probe["counts"].append(count)
    probe["fields"].update(fields)
    probe["urls"].append(url)
    probe["successes"] += int(success)
    if not success:
        probe["errors"].append(f"HTTP 200 but no usable records: {url}")


def union_fields(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(key) for row in rows for key in row}


def _flatten_one_level(values: list[Any]) -> Iterable[Any]:
    for value in values:
        if isinstance(value, list):
            yield from value
        else:
            yield value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
