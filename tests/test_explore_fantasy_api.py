from __future__ import annotations

import unittest

from scripts.explore_fantasy_api import (
    _classify_historical_prices,
    _current_market_route,
    _market_url,
    _representative_player_record,
    _summarize_player_points,
)
from src.data.fantasy_security import market_route_parts


class ExploreFantasyAPITests(unittest.TestCase):
    def test_current_route_comes_only_from_public_config_fields(self) -> None:
        config = {
            "data": {
                "current_players_list_id": 30,
                "current_matchday": {"id": 1000, "number": 38},
            }
        }
        route = _current_market_route(config)
        self.assertEqual(route, {"players_list_id": 30, "matchday_id": 1000})
        self.assertEqual(market_route_parts(_market_url(**route)), route)
        self.assertIsNone(_current_market_route({"data": {}}))

    def test_historical_classification_requires_observed_price_change(self) -> None:
        successful = [
            {
                "status": 200,
                "summary": {"record_count": 10},
                "comparison_with_current": {"players_with_different_price": 2},
            }
        ]
        self.assertTrue(
            _classify_historical_prices(successful, True).startswith("B.")
        )
        self.assertTrue(
            _classify_historical_prices([{"status": 404}], True).startswith("C.")
        )
        self.assertTrue(_classify_historical_prices([], True).startswith("D."))

    def test_points_probe_selects_a_player_and_summarizes_fields(self) -> None:
        records = [
            {"id": 2, "position": {"id": 28, "name": "Guard"}},
            {"id": 1, "position": {"id": 31, "name": "Head Coach"}},
            {"id": 3, "position": {"id": 29, "name": "Forward"}},
        ]
        self.assertEqual(_representative_player_record(records)["id"], 2)
        summary = _summarize_player_points(
            {
                "data": {
                    "fantasy_pts": 12.5,
                    "stats_items": [
                        {"id": 1, "name": "Points", "value": 10},
                        {"id": 2, "name": "Rebounds", "value": 4},
                    ],
                }
            }
        )
        self.assertEqual(summary["stats_item_names"], ["Points", "Rebounds"])
        self.assertIn("data.fantasy_pts", summary["schema_inventory"])


if __name__ == "__main__":
    unittest.main()
