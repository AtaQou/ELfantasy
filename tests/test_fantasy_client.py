from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.data.fantasy_client import (
    FantasyAuthenticationError,
    FantasyClient,
    extract_bearer_token,
)
from src.data.fantasy_security import atomic_write_private_json


TEST_ONLY_TOKEN = "unit-test-opaque-value"


class FantasyClientTests(unittest.TestCase):
    def test_extracts_json_encoded_flutter_shared_preference(self) -> None:
        state = {
            "cookies": [],
            "origins": [
                {
                    "origin": "https://euroleaguefantasy.euroleaguebasketball.net",
                    "localStorage": [
                        {
                            "name": "flutter.authToken",
                            "value": json.dumps(TEST_ONLY_TOKEN),
                        }
                    ],
                }
            ],
        }
        self.assertEqual(extract_bearer_token(state), TEST_ONLY_TOKEN)

    def test_missing_token_fails_without_dumping_state(self) -> None:
        with self.assertRaisesRegex(FantasyAuthenticationError, "rerun"):
            extract_bearer_token({"cookies": [], "origins": []})

    def test_client_can_load_mock_state_and_make_scoped_get(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"id": 1, "quotation": 10.0}]}
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / ".auth" / "euroleague.json"
            atomic_write_private_json(
                state_path,
                {
                    "cookies": [],
                    "origins": [
                        {
                            "origin": "https://euroleaguefantasy.euroleaguebasketball.net",
                            "localStorage": [
                                {"name": "flutter.authToken", "value": TEST_ONLY_TOKEN}
                            ],
                        }
                    ],
                },
            )
            client = FantasyClient.from_storage_state(state_path, session=session)
            payload = client.get_market(30, 1000)

        self.assertEqual(payload["data"][0]["quotation"], 10.0)
        call = session.get.call_args
        self.assertEqual(call.kwargs["params"], {"page": 1, "per_page": -1})
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            f"Bearer {TEST_ONLY_TOKEN}",
        )
        self.assertNotIn(TEST_ONLY_TOKEN, repr(client))

    def test_rejected_session_has_safe_error(self) -> None:
        response = Mock(status_code=401)
        session = Mock()
        session.get.return_value = response
        client = FantasyClient(TEST_ONLY_TOKEN, session=session)
        with self.assertRaisesRegex(FantasyAuthenticationError, "rerun") as raised:
            client.get_market(30, 1000)
        self.assertNotIn(TEST_ONLY_TOKEN, str(raised.exception))

    def test_single_player_points_request_is_narrow_and_parameterized(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"fantasy_pts": 18.5}]}
        session = Mock()
        session.get.return_value = response
        client = FantasyClient(TEST_ONLY_TOKEN, session=session)
        payload = client.get_player_fantasy_points(123, 1000)
        self.assertEqual(payload["data"][0]["fantasy_pts"], 18.5)
        call = session.get.call_args
        self.assertTrue(call.args[0].endswith("/players/123/fantasy-pts"))
        self.assertEqual(
            call.kwargs["params"],
            {"league": 10, "matchday": 1000, "lang": "en"},
        )
        with self.assertRaises(ValueError):
            client.get_player_fantasy_points(123, 1000, language="english")


if __name__ == "__main__":
    unittest.main()
