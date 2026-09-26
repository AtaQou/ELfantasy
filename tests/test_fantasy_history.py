from __future__ import annotations

import unittest

from scripts.collect_fantasy_history import config_market_scope


class FantasyHistoryTests(unittest.TestCase):
    def test_matchdays_come_only_from_config(self) -> None:
        payload = {
            "data": {
                "current_competition_id": 30,
                "current_players_list_id": 30,
                "current_matchday": {"id": 1000, "number": 38},
                "matchdays": [
                    {"id": 964, "number": 2},
                    {"id": 963, "number": 1},
                ],
            }
        }
        self.assertEqual(config_market_scope(payload), (30, 30, 38, [(963, 1), (964, 2)]))

    def test_duplicate_or_missing_matchday_metadata_is_rejected(self) -> None:
        duplicate = {
            "data": {
                "current_competition_id": 30,
                "current_players_list_id": 30,
                "current_matchday": {"number": 38},
                "matchdays": [
                    {"id": 963, "number": 1},
                    {"id": 963, "number": 2},
                ],
            }
        }
        with self.assertRaisesRegex(ValueError, "duplicate"):
            config_market_scope(duplicate)
        with self.assertRaisesRegex(ValueError, "no legitimate matchdays"):
            config_market_scope(
                {
                    "data": {
                        "current_competition_id": 30,
                        "current_players_list_id": 30,
                        "current_matchday": {"number": 1},
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
