from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from src.data.fantasy_security import (
    FantasySecurityError,
    atomic_write_private_json,
    authentication_mechanism_summary,
    contains_sensitive_artifact_value,
    is_protected_fantasy_request,
    market_route_parts,
    safe_endpoint_metadata,
    sanitize_for_artifact,
    storage_state_has_material,
    storage_state_has_fantasy_token,
    verify_private_state_file,
)


MARKET_URL = (
    "https://fantaking-api.dunkest.com/api/v1/players-lists/30/"
    "matchdays/1000/players?page=1&per_page=25"
)


class FantasySecurityTests(unittest.TestCase):
    def test_market_route_is_strictly_scoped(self) -> None:
        self.assertEqual(
            market_route_parts(MARKET_URL),
            {"players_list_id": 30, "matchday_id": 1000},
        )
        self.assertTrue(is_protected_fantasy_request(MARKET_URL))
        self.assertIsNone(market_route_parts("https://example.com/api/v1/players"))

    def test_endpoint_metadata_keeps_only_known_query_values(self) -> None:
        metadata = safe_endpoint_metadata(MARKET_URL + "&unknown=private-value")
        self.assertEqual(metadata["query"]["page"], ["1"])
        self.assertEqual(metadata["query"]["unknown"], "[REDACTED]")
        with self.assertRaises(FantasySecurityError):
            safe_endpoint_metadata("https://example.com/api")

    def test_sensitive_body_values_are_redacted_recursively(self) -> None:
        payload = {
            "data": [{"id": 1, "name": "Player", "quotation": 12.5}],
            "authorization": "credential material",
            "nested": {"refreshToken": "credential material", "status": "active"},
        }
        sanitized = sanitize_for_artifact(payload)
        self.assertEqual(sanitized["authorization"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["refreshToken"], "[REDACTED]")
        self.assertEqual(sanitized["data"][0]["quotation"], 12.5)
        self.assertFalse(contains_sensitive_artifact_value(sanitized))

    def test_token_query_values_and_embedded_jwt_shapes_are_not_saved(self) -> None:
        callback = "https://example.com/auth?token=unit-test-only&safe=yes"
        sanitized = sanitize_for_artifact({"callback": callback})
        self.assertNotIn("unit-test-only", sanitized["callback"])
        self.assertIn("safe=yes", sanitized["callback"])
        self.assertTrue(contains_sensitive_artifact_value({"callback": callback}))
        self.assertFalse(contains_sensitive_artifact_value(sanitized))

        jwt_shaped = "prefix eyJUNIT.TEST_ONLY.suffix"
        self.assertEqual(sanitize_for_artifact(jwt_shaped), "[REDACTED]")

    def test_auth_summary_never_returns_header_values(self) -> None:
        summary = authentication_mechanism_summary(
            {"Authorization": "Bearer credential-material", "Cookie": "private"}
        )
        self.assertEqual(summary["authorization_scheme"], "Bearer")
        self.assertTrue(summary["cookie_header_present"])
        self.assertNotIn("credential-material", json.dumps(summary))

    def test_private_json_writer_and_state_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".auth" / "euroleague.json"
            state = {
                "cookies": [{"name": "opaque", "value": "not-a-real-session"}],
                "origins": [],
            }
            atomic_write_private_json(path, state)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertTrue(storage_state_has_material(state))
            verify_private_state_file(path)

            path.chmod(0o644)
            with self.assertRaises(FantasySecurityError):
                verify_private_state_file(path)

    def test_empty_storage_state_is_not_authentication(self) -> None:
        self.assertFalse(storage_state_has_material({"cookies": [], "origins": []}))

    def test_fantasy_token_check_uses_only_the_expected_origin_and_key(self) -> None:
        state = {
            "cookies": [],
            "origins": [
                {
                    "origin": "https://euroleaguefantasy.euroleaguebasketball.net",
                    "localStorage": [
                        {"name": "theme", "value": "dark"},
                        {"name": "flutter.authToken", "value": "unit-test-only"},
                    ],
                }
            ],
        }
        self.assertTrue(storage_state_has_fantasy_token(state))
        state["origins"][0]["origin"] = "https://example.com"
        self.assertFalse(storage_state_has_fantasy_token(state))


if __name__ == "__main__":
    unittest.main()
