from __future__ import annotations

import unittest

from src.data.euroleague_client import EuroLeagueClient
from src.data.normalizers import (
    data_quality_summary,
    normalize_games,
    normalize_play_by_play,
    normalize_player_boxscore,
    normalize_shots,
    normalize_team_boxscore,
    parse_minutes,
)


GAME = {
    "id": "game-uuid",
    "identifier": "E2025_1",
    "gameCode": 1,
    "round": 1,
    "played": True,
    "gameStatus": "Confirmed",
    "utcDate": "2025-09-30T17:45:00Z",
    "season": {"year": 2025, "code": "E2025", "competitionCode": "E"},
    "phaseType": {"code": "RS"},
    "local": {
        "club": {"code": "AAA", "name": "Home"},
        "score": 80,
        "partials": {"extraPeriods": {}},
    },
    "road": {
        "club": {"code": "BBB", "name": "Away"},
        "score": 75,
        "partials": {"extraPeriods": {}},
    },
    "winner": {"code": "WRONG"},
    "venue": {"code": "V1", "name": "Arena", "capacity": 5000},
}

PLAYER = {
    "Player_ID": "P000001   ",
    "Player": "Example Player ",
    "Team": "AAA ",
    "Dorsal": "7 ",
    "IsStarter": 1,
    "IsPlaying": 0,
    "Minutes": "30:30",
    "Points": 12,
    "FieldGoalsMade2": 3,
    "FieldGoalsAttempted2": 5,
    "FieldGoalsMade3": 1,
    "FieldGoalsAttempted3": 4,
    "FreeThrowsMade": 3,
    "FreeThrowsAttempted": 4,
    "OffensiveRebounds": 1,
    "DefensiveRebounds": 2,
    "TotalRebounds": 3,
    "Assistances": 4,
    "Steals": 1,
    "Turnovers": 2,
    "BlocksFavour": 1,
    "BlocksAgainst": 0,
    "FoulsCommited": 2,
    "FoulsReceived": 3,
    "Valuation": 15,
    "Plusminus": 5,
}

TEAM_TOTAL = {
    "Minutes": "200:00",
    **{
        key: PLAYER[key]
        for key in (
            "Points",
            "FieldGoalsMade2",
            "FieldGoalsAttempted2",
            "FieldGoalsMade3",
            "FieldGoalsAttempted3",
            "FreeThrowsMade",
            "FreeThrowsAttempted",
            "OffensiveRebounds",
            "DefensiveRebounds",
            "TotalRebounds",
            "Assistances",
            "Steals",
            "Turnovers",
            "BlocksFavour",
            "BlocksAgainst",
            "FoulsCommited",
            "FoulsReceived",
            "Valuation",
            "Plusminus",
        )
    },
}


class NormalizerTests(unittest.TestCase):
    def test_minutes_and_season_code_validation(self) -> None:
        self.assertEqual(parse_minutes("30:30"), 30.5)
        self.assertIsNone(parse_minutes("DNP"))
        self.assertIsNone(parse_minutes("bad"))
        self.assertEqual(EuroLeagueClient().season_code(2025), "E2025")

    def test_game_winner_is_derived_from_score(self) -> None:
        row = normalize_games([GAME])[0]
        self.assertEqual(row["api_winner_code"], "WRONG")
        self.assertEqual(row["derived_winner_code"], "AAA")

    def test_player_and_team_boxscores_preserve_source_semantics(self) -> None:
        other_player = {**PLAYER, "Player_ID": "P000002", "Team": "BBB"}
        payload = {
            "Stats": [
                {
                    "Team": "Home",
                    "Coach": "Coach A",
                    "PlayersStats": [PLAYER],
                    "totr": TEAM_TOTAL,
                    "tmr": {},
                },
                {
                    "Team": "Away",
                    "Coach": "Coach B",
                    "PlayersStats": [other_player],
                    "totr": TEAM_TOTAL,
                    "tmr": {},
                },
            ]
        }
        players = normalize_player_boxscore(payload, GAME)
        teams = normalize_team_boxscore(payload, GAME)
        self.assertEqual(players[0]["player_id"], "P000001")
        self.assertEqual(players[0]["minutes"], 30.5)
        self.assertFalse(players[0]["is_playing_flag"])
        self.assertEqual(players[0]["opponent_id"], "BBB")
        self.assertEqual(teams[1]["opponent_id"], "AAA")

    def test_play_by_play_keeps_response_order_and_substitutions(self) -> None:
        payload = {
            "FirstQuarter": [
                {"NUMBEROFPLAY": 10, "PLAYTYPE": "BP"},
                {
                    "NUMBEROFPLAY": 8,
                    "PLAYTYPE": "IN",
                    "PLAYER_ID": "P1 ",
                    "CODETEAM": "AAA ",
                },
            ],
            "SecondQuarter": [],
            "ThirdQuarter": [],
            "ForthQuarter": [],
            "ExtraTime": [],
        }
        rows = normalize_play_by_play(payload, season_code="E2025", game_code=1)
        self.assertEqual([row["source_sequence"] for row in rows], [1, 2])
        self.assertEqual(rows[1]["source_period"], "FirstQuarter")
        self.assertEqual(rows[1]["player_id"], "P1")

    def test_nested_overtime_periods_keep_their_period_number(self) -> None:
        rows = normalize_play_by_play(
            {
                "ExtraTime": [
                    [{"NUMBEROFPLAY": 100, "PLAYTYPE": "2FGM"}],
                    [{"NUMBEROFPLAY": 200, "PLAYTYPE": "3FGM"}],
                ]
            },
            season_code="E2023",
            game_code=170,
        )
        self.assertEqual([row["quarter"] for row in rows], [5, 6])
        self.assertEqual(
            [row["source_period"] for row in rows], ["ExtraTime1", "ExtraTime2"]
        )

    def test_flat_overtime_closing_markers_stay_in_the_period_they_close(self) -> None:
        events = [
            {"NUMBEROFPLAY": 1, "MINUTE": 41, "PLAYTYPE": "BP"},
            {"NUMBEROFPLAY": 2, "MINUTE": 46, "PLAYTYPE": "EP"},
            {
                "NUMBEROFPLAY": 3,
                "MINUTE": 46,
                "MARKERTIME": "05:00",
                "PLAYTYPE": "IN",
            },
            {"NUMBEROFPLAY": 4, "MINUTE": 46, "PLAYTYPE": "BP"},
            {"NUMBEROFPLAY": 5, "MINUTE": 51, "PLAYTYPE": "EP"},
            {"NUMBEROFPLAY": 6, "MINUTE": 51, "PLAYTYPE": "BP"},
            {"NUMBEROFPLAY": 7, "MINUTE": 56, "PLAYTYPE": "EP"},
            {"NUMBEROFPLAY": 8, "MINUTE": 56, "PLAYTYPE": "BP"},
            {"NUMBEROFPLAY": 9, "MINUTE": 61, "PLAYTYPE": "EP"},
            {"NUMBEROFPLAY": 10, "MINUTE": 61, "PLAYTYPE": "EG"},
        ]

        rows = normalize_play_by_play(
            {"ExtraTime": events}, season_code="E2023", game_code=170
        )

        self.assertEqual(
            [row["quarter"] for row in rows],
            [5, 5, 6, 6, 6, 7, 7, 8, 8, 8],
        )
        self.assertEqual([row["source_sequence"] for row in rows], list(range(1, 11)))
        self.assertTrue(all(row["source_period"] == "ExtraTime" for row in rows))
        self.assertNotIn(9, {row["quarter"] for row in rows})

    def test_quality_summary_exposes_known_source_traps(self) -> None:
        games = normalize_games([GAME])
        player = {
            "game_code": 1,
            "team_id": "AAA",
            "player_id": "P1",
            "player_name": "Player",
            "minutes": 20.0,
            "did_not_play": False,
            "is_playing_flag": False,
        }
        pbp = normalize_play_by_play(
            {
                "FirstQuarter": [
                    {"NUMBEROFPLAY": 2, "PLAYTYPE": "IN"},
                    {"NUMBEROFPLAY": 1, "PLAYTYPE": "FTA"},
                ]
            },
            season_code="E2025",
            game_code=1,
        )
        shots = normalize_shots(
            {
                "Rows": [
                    {
                        "NUM_ANOT": 3,
                        "ID_PLAYER": "P1",
                        "ACTION": "Two Pointer",
                    }
                ]
            },
            season_code="E2025",
            game_code=1,
        )
        summary = data_quality_summary(games, [player], [], pbp, shots, [])
        self.assertEqual(summary["winner_field_mismatch_game_codes"], [1])
        self.assertEqual(summary["players_with_minutes_but_is_playing_false"], 1)
        self.assertEqual(summary["play_by_play_missed_free_throw_events"], 1)
        self.assertEqual(summary["shot_rows_describing_missed_free_throws"], 0)
        self.assertEqual(summary["play_by_play_event_number_inversions"][0]["inversions"], 1)


if __name__ == "__main__":
    unittest.main()
