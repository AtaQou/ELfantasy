from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.data.fantasy_market import (
    compare_market_payloads,
    extract_config_matchdays,
    market_records,
    representative_historical_matchdays,
    summarize_market_payload,
    write_sanitized_json,
)


CURRENT = {
    "data": [
        {
            "id": 1,
            "first_name": "A",
            "last_name": "Player",
            "quotation": 12.5,
            "position": {"id": 28, "name": "Guard"},
            "is_injured": False,
            "probability_of_playing": 100,
        },
        {
            "id": 2,
            "first_name": "B",
            "last_name": "Player",
            "quotation": 9.0,
            "position": {"id": 29, "name": "Forward"},
            "is_injured": True,
            "probability_of_playing": 25,
        },
    ],
    "meta": {"current_page": 1},
}


class FantasyMarketTests(unittest.TestCase):
    def test_records_and_field_summary(self) -> None:
        self.assertEqual(len(market_records(CURRENT)), 2)
        summary = summarize_market_payload(CURRENT)
        self.assertEqual(summary["record_count"], 2)
        self.assertIn("quotation", summary["record_fields"])
        self.assertTrue(summary["useful_field_paths"]["current_price"])
        self.assertEqual(summary["value_summary"]["unique_fantasy_ids"], 2)
        self.assertEqual(
            summary["value_summary"]["position_counts"],
            {"Forward": 1, "Guard": 1},
        )
        self.assertEqual(
            summary["status_value_summary"]["is_injured"],
            {"false": 1, "true": 1},
        )

    def test_snapshot_comparison(self) -> None:
        historical = {
            "data": [
                {**CURRENT["data"][0], "quotation": 11.0},
                {**CURRENT["data"][1], "is_injured": False},
            ]
        }
        comparison = compare_market_payloads(CURRENT, historical)
        self.assertEqual(comparison["shared_player_ids"], 2)
        self.assertEqual(comparison["players_with_different_price"], 1)
        self.assertEqual(comparison["players_with_different_injury_or_status"], 1)
        self.assertEqual(comparison["players_with_different_is_injured"], 1)
        self.assertEqual(
            comparison["players_with_different_probability_of_playing"], 0
        )

    def test_representative_matchdays_are_config_driven(self) -> None:
        config = {
            "data": {
                "matchdays": [
                    {"id": 100, "number": 1},
                    {"id": 101, "number": 2},
                    {"id": 102, "number": 3},
                    {"id": 103, "number": 4},
                ]
            }
        }
        matchdays = extract_config_matchdays(config)
        selected = representative_historical_matchdays(matchdays, 103, limit=3)
        self.assertEqual([row["id"] for row in selected], [102, 101, 100])

    def test_writer_rejects_unsanitized_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            write_sanitized_json(path, CURRENT)
            self.assertTrue(path.exists())
            with self.assertRaises(ValueError):
                write_sanitized_json(path, {"access_token": "unsafe"})


if __name__ == "__main__":
    unittest.main()
