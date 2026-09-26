from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.data.fantasy_identity import (
    FantasyIdentityAuditError,
    audit_files,
    build_identity_audit,
    normalize_person_name,
)


def roster_row(
    player_id: str,
    external_id: str,
    name: str,
    team_id: str,
    team_name: str,
    *,
    passport_name: str = "",
    passport_surname: str = "",
) -> dict[str, str]:
    return {
        "season_code": "E2025",
        "player_id": player_id,
        "external_id": external_id,
        "player_name": name,
        "alias": "",
        "passport_name": passport_name,
        "passport_surname": passport_surname,
        "team_id": team_id,
        "team_name": team_name,
    }


class FantasyIdentityTests(unittest.TestCase):
    def test_name_normalization_is_cautious_not_fuzzy(self) -> None:
        self.assertEqual(normalize_person_name("Nikóla O’Neal Jr."), "nikola oneal jr")
        self.assertNotEqual(
            normalize_person_name("Nikola Oneal Jr"),
            normalize_person_name("Oneal Nikola Jr"),
        )
        self.assertNotEqual(
            normalize_person_name("Aleksandar Vezenkov"),
            normalize_person_name("Alexander Vezenkov"),
        )

    def test_conservative_matching_and_coach_separation(self) -> None:
        roster = [
            roster_row("003733", "53103", "MIROTIĆ, NIKOLA", "MIL", "Milano"),
            roster_row("P2", "902", "JAMES, MIKE", "MCO", "Monaco"),
            roster_row("P3", "903", "SMITH, ALEX", "AAA", "Alpha"),
            roster_row("P4", "904", "SMITH, ALEX", "AAA", "Alpha"),
            roster_row("P5", "905", "ONLY, NAME", "ZZZ", "Zeta"),
        ]
        market = {
            "data": [
                {
                    "id": 700,
                    "first_name": "Nikola",
                    "last_name": "Mirotic",
                    "team": {"id": 10, "name": "Milano", "abbreviation": "MIL"},
                    "position": {"id": 30, "name": "Center"},
                },
                {
                    "id": 701,
                    "first_name": "Unrelated",
                    "last_name": "Label",
                    "official_player_id": "P2",
                    "team": {"id": 11, "name": "Monaco", "abbreviation": "MCO"},
                    "position": {"id": 28, "name": "Guard"},
                },
                {
                    "id": 702,
                    "first_name": "Alex",
                    "last_name": "Smith",
                    "team": {"id": 12, "name": "Alpha", "abbreviation": "AAA"},
                    "position": {"id": 28, "name": "Guard"},
                },
                {
                    "id": 703,
                    "first_name": "Name",
                    "last_name": "Only",
                    "team": {"id": 99, "name": "Unknown", "abbreviation": "UNK"},
                    "position": {"id": 29, "name": "Forward"},
                },
                {
                    "id": "003733",
                    "player_id": "003733",
                    "first_name": "No",
                    "last_name": "Match",
                    "team": {"id": 98, "name": "Unknown Two", "abbreviation": "UN2"},
                    "position": {"id": 28, "name": "Guard"},
                },
                {
                    "id": 705,
                    "first_name": "Coach",
                    "last_name": "Person",
                    "team": {"id": 10, "name": "Milano", "abbreviation": "MIL"},
                    "position": {"id": 31, "name": "Head Coach"},
                },
            ]
        }

        report = build_identity_audit(market, roster)
        by_id = {row["fantasy_player_id"]: row for row in report["records"]}
        self.assertEqual(by_id["700"]["status"], "matched_normalized_name_team")
        self.assertEqual(by_id["700"]["official_player_id"], "003733")
        self.assertEqual(by_id["701"]["status"], "matched_direct_official_id")
        self.assertEqual(by_id["702"]["status"], "ambiguous")
        self.assertEqual(by_id["703"]["status"], "unique_name_only_review")
        # Native Fantasy ID overlap is reported but is never accepted as identity.
        self.assertEqual(by_id["003733"]["status"], "unmatched")
        self.assertFalse(
            by_id["003733"]["explicit_official_id_candidates"][0][
                "automatic_evidence"
            ]
        )
        self.assertEqual(by_id["705"]["status"], "coach_excluded")
        self.assertEqual(
            report["namespace_comparison"]["players"][
                "fantasy_id_exact_official_player_id_overlap_count"
            ],
            1,
        )
        self.assertEqual(report["summary"]["accepted_unique_matches"], 2)

    def test_duplicate_and_transfer_flags(self) -> None:
        roster = [
            roster_row("MOVE", "100", "MOVE, JOHN", "AAA", "Alpha"),
            roster_row("MOVE", "100", "MOVE, JOHN", "BBB", "Beta"),
        ]
        market = {
            "data": [
                {
                    "id": 1,
                    "first_name": "John",
                    "last_name": "Move",
                    "team": {"id": 7, "name": "Alpha", "abbreviation": "AAA"},
                    "position": {"name": "Guard"},
                },
                {
                    "id": 1,
                    "first_name": "John",
                    "last_name": "Move",
                    "team": {"id": 8, "name": "Beta", "abbreviation": "BBB"},
                    "position": {"name": "Guard"},
                },
            ]
        }
        report = build_identity_audit(market, roster)
        flags = report["quality_flags"]
        self.assertEqual(flags["duplicate_fantasy_ids"][0]["count"], 2)
        self.assertEqual(flags["fantasy_ids_on_multiple_teams"][0]["fantasy_team_ids"], ["7", "8"])
        self.assertEqual(flags["official_multi_team_memberships"][0]["official_player_id"], "MOVE")
        self.assertTrue(all(row["official_transfer_flag"] for row in report["records"]))

    def test_file_audit_uses_only_supplied_synthetic_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_path = root / "market_response.sanitized.json"
            roster_path = root / "roster_players.csv"
            market_path.write_text(
                json.dumps(
                    {
                        "data": [
                            {
                                "id": 1,
                                "first_name": "Mike",
                                "last_name": "James",
                                "team": {"id": 2, "name": "Monaco", "abbreviation": "MCO"},
                                "position": {"name": "Guard"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            roster_path.write_text(
                "season_code,player_id,external_id,player_name,team_id,team_name\n"
                'E2025,P2,902,"JAMES, MIKE",MCO,Monaco\n',
                encoding="utf-8",
            )
            output, report = audit_files(market_path, roster_path)
            self.assertEqual(output.parent, market_path.parent)
            self.assertEqual(output.name, "identity_mapping_audit.json")
            self.assertEqual(report["summary"]["accepted_unique_matches"], 1)
            self.assertTrue(output.exists())

    def test_empty_market_is_rejected(self) -> None:
        with self.assertRaises(FantasyIdentityAuditError):
            build_identity_audit({"data": []}, [roster_row("P1", "1", "A, B", "T", "Team")])


if __name__ == "__main__":
    unittest.main()
