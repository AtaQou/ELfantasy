from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.data.fantasy_config import (
    FantasyConfigDiscoveryError,
    FantasyConfigValidationError,
    load_current_fantasy_config,
    parse_current_fantasy_scope,
)
from src.data.fantasy_market import (
    FantasyMarketValidationError,
    validate_current_market_payload,
)
from src.db.database import connect_database, initialize_database
from src.live.freshness import source_freshness
from src.live.fantasy import update_current_fantasy_market
from src.live.pipeline import update_live


class CurrentFantasyConfigTests(unittest.TestCase):
    def test_discovers_current_scope_from_official_bootstrap_shape(self) -> None:
        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.json.return_value = config_payload()
        session = Mock()
        session.get.return_value = response

        _, scope = load_current_fantasy_config(session=session)

        self.assertEqual(scope.competition_id, 49)
        self.assertEqual(scope.players_list_id, 49)
        self.assertEqual(scope.matchday_id, 1528)
        self.assertEqual(scope.matchday_number, 1)
        self.assertEqual(scope.matchday_catalog, ((1, 1528), (2, 1529)))
        self.assertEqual(len(scope.configured_team_ids), 10)
        self.assertTrue(scope.automatically_discovered)
        call = session.get.call_args
        self.assertTrue(call.args[0].endswith("/api/v1/leagues/10/config"))
        self.assertEqual(call.kwargs["headers"]["Accept"], "application/json")

    def test_config_endpoint_and_schema_failures_are_distinct(self) -> None:
        response = Mock(status_code=503, headers={"content-type": "application/json"})
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(FantasyConfigDiscoveryError):
            load_current_fantasy_config(session=session)

        malformed = config_payload()
        malformed["data"]["matchdays"] = []
        with self.assertRaises(FantasyConfigValidationError):
            parse_current_fantasy_scope(malformed)


class CurrentFantasyMarketValidationTests(unittest.TestCase):
    def test_complete_market_reports_supported_fields(self) -> None:
        result = validate_current_market_payload(
            market_payload(),
            configured_team_ids=range(100, 110),
        )
        self.assertEqual(result.entity_count, 110)
        self.assertEqual(result.player_count, 100)
        self.assertEqual(result.coach_count, 10)
        self.assertEqual(result.valid_credit_count, 110)
        self.assertEqual(result.turn_numbers, (1, 2))
        self.assertEqual(
            result.available_status_fields,
            ("is_injured", "probability_of_playing", "started_from_bench"),
        )

    def test_empty_duplicate_partial_and_malformed_markets_are_rejected(self) -> None:
        with self.assertRaises(FantasyMarketValidationError):
            validate_current_market_payload(
                {"data": []}, configured_team_ids=range(100, 110)
            )
        duplicate = market_payload()
        duplicate["data"][-1]["id"] = duplicate["data"][0]["id"]
        with self.assertRaisesRegex(FantasyMarketValidationError, "duplicate"):
            validate_current_market_payload(
                duplicate, configured_team_ids=range(100, 110)
            )
        partial = market_payload()
        partial["data"] = partial["data"][:20]
        with self.assertRaisesRegex(FantasyMarketValidationError, "small"):
            validate_current_market_payload(
                partial, configured_team_ids=range(100, 110)
            )
        malformed = market_payload()
        malformed["data"][0]["quotation"] = None
        with self.assertRaisesRegex(FantasyMarketValidationError, "credits"):
            validate_current_market_payload(
                malformed, configured_team_ids=range(100, 110)
            )


class CurrentFantasyDatabaseTests(unittest.TestCase):
    def test_auto_discovery_advances_when_upcoming_market_uniquely_matches_schedule(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "fantasy.duckdb"
            seed_required_alias_target(database)
            config = config_payload()
            scope = parse_current_fantasy_scope(config)
            current = market_payload()
            upcoming = market_payload()
            current["scope"] = "current"
            upcoming["scope"] = "upcoming"
            client = MatchdayMarketClient({1528: current, 1529: upcoming})
            expected_pairs = {("team-a", "team-b"), ("team-b", "team-a")}

            with (
                patch(
                    "src.live.fantasy.load_current_fantasy_config",
                    return_value=(config, scope),
                ),
                patch(
                    "src.live.fantasy._upcoming_round_pairs",
                    return_value=expected_pairs,
                ),
                patch(
                    "src.live.fantasy._market_canonical_pairs",
                    side_effect=lambda _db, _season, payload: (
                        expected_pairs if payload.get("scope") == "upcoming"
                        else {("old-a", "old-b"), ("old-b", "old-a")}
                    ),
                ),
            ):
                result = update_current_fantasy_market(
                    "E2026",
                    database_path=database,
                    client=client,
                    captured_at=datetime(2026, 9, 26, tzinfo=UTC),
                    raw_root=root / "raw",
                )

            self.assertEqual(client.calls, [(49, 1528), (49, 1529)])
            self.assertEqual((result.matchday_number, result.matchday_id), (2, 1529))
            with connect_database(database, read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT DISTINCT matchday_number FROM fantasy_market_snapshots"
                    ).fetchall(),
                    [(2,)],
                )

    def test_authenticated_snapshot_is_append_only_idempotent_and_quarantines_unmatched(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "fantasy.duckdb"
            raw_root = root / "raw"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config_payload()), encoding="utf-8")
            seed_required_alias_target(database)
            client = FakeMarketClient(market_payload())
            observed = datetime(2026, 9, 2, 13, 30, tzinfo=UTC)

            first = update_current_fantasy_market(
                "E2026",
                config_path,
                database_path=database,
                client=client,
                captured_at=observed,
                raw_root=raw_root,
            )
            second = update_current_fantasy_market(
                "E2026",
                config_path,
                database_path=database,
                client=client,
                captured_at=observed,
                raw_root=raw_root,
            )

            self.assertEqual(client.calls, [(49, 1528), (49, 1528)])
            self.assertEqual(first.snapshot_rows_inserted, 110)
            self.assertEqual(second.snapshot_rows_inserted, 0)
            self.assertEqual(first.identity_counts, {"NO_OFFICIAL_CANDIDATE": 100})
            self.assertEqual(first.mapped_players, 0)
            with connect_database(database, read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM fantasy_market_snapshots"
                    ).fetchone()[0],
                    110,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(DISTINCT fantasy_id) FROM fantasy_entities"
                    ).fetchone()[0],
                    110,
                )

    def test_validation_failure_preserves_last_valid_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "fantasy.duckdb"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config_payload()), encoding="utf-8")
            seed_required_alias_target(database)
            first_observed = datetime(2026, 9, 2, 13, 30, tzinfo=UTC)
            update_current_fantasy_market(
                "E2026",
                config_path,
                database_path=database,
                client=FakeMarketClient(market_payload()),
                captured_at=first_observed,
                raw_root=root / "raw",
            )

            malformed = market_payload()
            malformed["data"] = []
            with self.assertRaises(FantasyMarketValidationError):
                update_current_fantasy_market(
                    "E2026",
                    config_path,
                    database_path=database,
                    client=FakeMarketClient(malformed),
                    captured_at=datetime(2026, 9, 2, 14, 30, tzinfo=UTC),
                    raw_root=root / "raw",
                )

            with connect_database(database, read_only=True) as connection:
                count, latest = connection.execute(
                    "SELECT count(*), max(observed_at) FROM fantasy_market_snapshots"
                ).fetchone()
            self.assertEqual(count, 110)
            self.assertEqual(latest, first_observed)

    def test_live_pipeline_auto_discovers_and_marks_market_fresh(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "fantasy.duckdb"
            observed = datetime(2026, 9, 2, 13, 30, tzinfo=UTC)
            fantasy_result = SimpleNamespace(
                entity_rows=110,
                player_rows=100,
                config_source="https://fantaking-api.dunkest.com/api/v1/leagues/10/config",
                freshness_message=(
                    "110 entities (100 players, 10 coaches); Matchday 1; "
                    "official config auto-discovered"
                ),
            )
            with (
                patch(
                    "src.live.pipeline.update_current_season",
                    side_effect=RuntimeError("offline in sanitized test"),
                ),
                patch(
                    "src.live.pipeline.update_current_fantasy_market",
                    return_value=fantasy_result,
                ) as update_market,
                patch("src.live.pipeline.load_source_manifest", return_value=[]),
                patch(
                    "src.live.pipeline.build_current_slate",
                    side_effect=RuntimeError("no canonical fixture"),
                ),
            ):
                update_live(
                    "E2026",
                    database_path=database,
                    collect_official_availability=False,
                    include_rich=False,
                    as_of=observed,
                )

            self.assertIsNone(update_market.call_args.args[1])
            state = source_freshness(database, as_of=observed)["fantasy_market"]
            self.assertFalse(state.is_stale)
            self.assertTrue(state.last_attempt_succeeded)
            self.assertIn("Matchday 1", state.message or "")


class FakeMarketClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[int, int]] = []

    def get_market(self, players_list_id: int, matchday_id: int) -> dict:
        self.calls.append((players_list_id, matchday_id))
        return self.payload


class MatchdayMarketClient:
    def __init__(self, payloads: dict[int, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[int, int]] = []

    def get_market(self, players_list_id: int, matchday_id: int) -> dict:
        self.calls.append((players_list_id, matchday_id))
        return self.payloads[matchday_id]


def config_payload() -> dict:
    return {
        "data": {
            "current_competition_id": 49,
            "current_players_list_id": 49,
            "current_matchday": {"id": 1528, "number": 1},
            "matchdays": [
                {"id": 1528, "number": 1},
                {"id": 1529, "number": 2},
            ],
            "teams": [
                {"id": team_id, "name": f"Sanitized Team {team_id}"}
                for team_id in range(100, 110)
            ],
        }
    }


def market_payload() -> dict:
    rows = []
    positions = ("Guard", "Forward", "Center")
    for index in range(100):
        team_id = 100 + index % 10
        rows.append(
            market_row(
                fantasy_id=5_000 + index,
                first_name=f"Player{index}",
                last_name="Sanitized",
                team_id=team_id,
                position=positions[index % len(positions)],
                turn=1 + index % 2,
            )
        )
    for index, team_id in enumerate(range(100, 110)):
        rows.append(
            market_row(
                fantasy_id=6_000 + index,
                first_name=f"Coach{index}",
                last_name="Sanitized",
                team_id=team_id,
                position="Head Coach",
                turn=1 + index % 2,
            )
        )
    return {"data": rows}


def market_row(
    *,
    fantasy_id: int,
    first_name: str,
    last_name: str,
    team_id: int,
    position: str,
    turn: int,
) -> dict:
    position_id = {
        "Guard": 28,
        "Forward": 29,
        "Center": 30,
        "Head Coach": 31,
    }[position]
    opponent_id = 100 + ((team_id - 99) % 10)
    return {
        "id": fantasy_id,
        "first_name": first_name,
        "last_name": last_name,
        "quotation": 10.0,
        "avg_pts": 0.0,
        "popularity": 0.0,
        "jersey": "1",
        "position": {"id": position_id, "name": position},
        "team": {"id": team_id, "name": f"Sanitized Team {team_id}"},
        "opponent": {
            "id": opponent_id,
            "name": f"Sanitized Team {opponent_id}",
        },
        "round": {"id": 2_500 + turn, "number": turn},
        "is_injured": False,
        "probability_of_playing": 1,
        "started_from_bench": True,
        "is_on_fire": False,
        "fantasy_team": None,
    }


def seed_required_alias_target(database: Path) -> None:
    initialize_database(database)
    with connect_database(database) as connection:
        connection.execute(
            "INSERT INTO teams VALUES "
            "('sanitized-baskonia', 'BAS', 'Sanitized Baskonia', NULL, 'test')"
        )


if __name__ == "__main__":
    unittest.main()
