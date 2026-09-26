from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from scripts.collect_historical_fantasy_market import analyze_credit_progression
from src.data.historical_fantasy_stats import (
    FetchedResponse,
    HistoricalFantasyStatsClient,
    modern_stats_records,
    parse_historical_stats_page,
)
from src.db.database import connect_database
from src.db.historical_fantasy_ingestion import (
    HistoricalFantasyMarketWriter,
    LegacyStatsSnapshotSpec,
)
from src.db.identity_resolution import resolve_historical_fantasy_players
from src.db.ingestion import ingest_retained_samples


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SUMMARY = (
    PROJECT_ROOT
    / "data"
    / "samples"
    / "historical_fantasy_market"
    / "validation_summary.json"
)


def _page(*, selected_season: int, matchdays: tuple[int, ...]) -> bytes:
    seasons = {
        11: ("season/2022-2023", "2022-23"),
        15: ("season/2023-2024", "2023-24"),
    }
    options = "".join(
        f'<option value="{season_id}" data-slug="{slug}"'
        f'{" selected" if season_id == selected_season else ""}>{label}</option>'
        for season_id, (slug, label) in seasons.items()
    )
    weeks = "".join(f'<option value="{value}">{value}</option>' for value in matchdays)
    return (
        f'<select id="seasonSelect">{options}</select>'
        f'<select id="weeksSelect">{weeks}</select>'
        '<select id="teamsSelect"><option value="101">Team</option></select>'
        '<select id="positionsSelect"><option value="1">Guard</option></select>'
    ).encode()


class HistoricalStatsUnitTests(unittest.TestCase):
    def test_committed_e2025_credits_match_existing_market_exactly(self) -> None:
        summary = json.loads(VALIDATION_SUMMARY.read_text(encoding="utf-8"))
        validation = summary["e2025_validation"]
        self.assertEqual(validation["exact_matches"], 12_431)
        self.assertEqual(validation["mismatches"], 0)
        self.assertEqual(validation["match_percentage"], 1.0)
        self.assertEqual(len(validation["per_matchday"]), 38)
        self.assertTrue(
            all(row["mismatches"] == 0 for row in validation["per_matchday"])
        )

    def test_season_and_matchday_selection_changes_structured_request(self) -> None:
        first = parse_historical_stats_page(
            _page(selected_season=11, matchdays=(1, 2, 3))
        )
        second = parse_historical_stats_page(
            _page(selected_season=15, matchdays=(1, 2, 3, 4))
        )
        self.assertEqual((first.season_id, first.matchdays), (11, (1, 2, 3)))
        self.assertEqual((second.season_id, second.matchdays), (15, (1, 2, 3, 4)))

        response = Mock(status_code=200, content=b"[]", url="https://example.test")
        response.headers = {"content-type": "application/json"}
        session = Mock()
        session.get.return_value = response
        client = HistoricalFantasyStatsClient(session=session)
        client.get_matchday_stats(first, 2, (21,))
        params = session.get.call_args.kwargs["params"]
        self.assertIn(("season_id", 11), params)
        self.assertIn(("weeks[]", 2), params)
        self.assertIn(("rounds[]", 21), params)

    def test_credit_progression_distinguishes_history_from_overlay(self) -> None:
        safe = {
            1: [{"id": 7, "cr": 10.0, "plus": 0.4}],
            2: [{"id": 7, "cr": 10.4, "plus": -0.2}],
            3: [{"id": 7, "cr": 10.2, "plus": 0.0}],
        }
        overlay = {
            1: [{"id": 7, "cr": 10.2, "plus": 0.4}],
            2: [{"id": 7, "cr": 10.2, "plus": -0.2}],
            3: [{"id": 7, "cr": 10.2, "plus": 0.0}],
        }
        self.assertTrue(analyze_credit_progression(safe)["credits_are_pre_matchday_safe"])
        self.assertFalse(
            analyze_credit_progression(overlay)["credits_are_pre_matchday_safe"]
        )

    def test_modern_columnar_response_is_deterministic(self) -> None:
        payload = {
            "data": {
                "columns": ["name", "position", "quotation"],
                "players": [{"id": 17, "row": ["Player", "G", 12.3]}],
            }
        }
        expected = [{"id": 17, "name": "Player", "position": "G", "quotation": 12.3}]
        self.assertEqual(modern_stats_records(payload), expected)
        self.assertEqual(modern_stats_records(json.loads(json.dumps(payload))), expected)


class HistoricalMarketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "canonical.duckdb"
        cls.raw_root = cls.root / "raw"
        ingest_retained_samples(cls.database, raw_root=cls.raw_root)
        with connect_database(cls.database, read_only=True) as connection:
            cls.player = connection.execute(
                """
                SELECT player.canonical_player_id, player.canonical_name,
                       team.canonical_name, team.official_team_code
                FROM player_game_stats AS stat
                JOIN games AS game USING (canonical_game_id)
                JOIN players AS player USING (canonical_player_id)
                JOIN teams AS team
                  ON team.canonical_team_id = stat.canonical_team_id
                WHERE game.season_code = 'E2023'
                ORDER BY player.canonical_player_id LIMIT 1
                """
            ).fetchone()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _response(self) -> FetchedResponse:
        _, official_name, team_name, team_code = self.player
        surname, given = (part.strip() for part in official_name.split(",", 1))
        payload = [
            {
                "id": 7001,
                "first_name": given.title(),
                "last_name": surname.title(),
                "team_id": 501,
                "team_code": team_code,
                "team_name": team_name,
                "position_id": 1,
                "position": "G",
                "cr": 9.5,
                "plus": 0.2,
            }
        ]
        return FetchedResponse(
            url=(
                "https://www.dunkest.com/api/stats/table?season_id=15&"
                "mode=nba&date_from=2023-10-05&date_to=2023-10-06"
            ),
            content=json.dumps(payload, sort_keys=True).encode(),
            fetched_at=datetime(2026, 8, 12, tzinfo=UTC),
            media_type="application/json",
        )

    def _ingest(self) -> int:
        writer = HistoricalFantasyMarketWriter(
            "E2023", self.database, raw_root=self.raw_root
        )
        _, inserted = writer.ingest_legacy_date_response(
            self._response(),
            LegacyStatsSnapshotSpec(
                season_code="E2023",
                source_season_id=15,
                fantasy_matchday=1,
                competition_round=1,
                date_from=date(2023, 10, 5),
                date_to=date(2023, 10, 6),
                credits_valid_pre_matchday=False,
                position_valid_as_of_matchday=False,
            ),
            window_number=1,
        )
        writer.finish()
        return inserted

    def test_mapping_identity_leakage_and_duplicate_protection(self) -> None:
        self.assertEqual(self._ingest(), 1)
        self.assertEqual(self._ingest(), 0)
        identity = resolve_historical_fantasy_players(
            self.database, season_code="E2023"
        )
        self.assertEqual(identity["counts"], {"MATCHED": 1})
        with connect_database(self.database, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT market.competition_round,
                       market.fantasy_credits_pre_matchday,
                       market.fantasy_position,
                       market.is_injured, market.average_points,
                       market.identity_match_status,
                       mapping.mapping_status
                FROM leakage_safe_fantasy_market AS market
                JOIN fantasy_matchday_round_mapping AS mapping
                  ON mapping.season_code = market.season_code
                 AND mapping.fantasy_matchday = market.matchday_number
                WHERE market.season_code = 'E2023'
                  AND market.fantasy_id = '7001'
                """
            ).fetchone()
            count = connection.execute(
                """
                SELECT count(*) FROM fantasy_market_snapshots AS snapshot
                JOIN fantasy_entities AS entity USING (fantasy_entity_id)
                WHERE snapshot.season_code = 'E2023'
                  AND entity.fantasy_id = '7001'
                """
            ).fetchone()[0]
        self.assertEqual(row, (1, None, None, None, None, "MATCHED", "VALIDATED_DIRECT"))
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
