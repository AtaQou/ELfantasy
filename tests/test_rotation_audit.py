from __future__ import annotations

import json
import unittest

from scripts.audit_rotation_reconstruction import (
    DEFAULT_OUTPUT,
    DEFAULT_SAMPLE_DIRECTORY,
    RotationAuditError,
    audit_game,
    audit_sample,
    parse_clock_seconds,
)


def _player(
    player_id: str,
    team_id: str,
    minutes: str,
    *,
    starter: bool,
) -> dict[str, object]:
    return {
        "Player_ID": player_id,
        "Player": player_id,
        "Team": team_id,
        "Minutes": minutes,
        "IsStarter": int(starter),
    }


def _synthetic_boxscore() -> dict[str, object]:
    team_a = [
        _player("A1", "AAA", "05:00", starter=True),
        *[
            _player(f"A{number}", "AAA", "10:00", starter=True)
            for number in range(2, 6)
        ],
        _player("A6", "AAA", "05:00", starter=False),
    ]
    team_b = [
        _player(f"B{number}", "BBB", "10:00", starter=True)
        for number in range(1, 6)
    ]
    return {
        "Stats": [
            {"Team": "Team A", "PlayersStats": team_a},
            {"Team": "Team B", "PlayersStats": team_b},
        ]
    }


def _event(
    number: int,
    play_type: str,
    *,
    clock: str = "",
    team_id: str = "",
    player_id: str = "",
) -> dict[str, object]:
    return {
        "NUMBEROFPLAY": number,
        "PLAYTYPE": play_type,
        "MARKERTIME": clock,
        "CODETEAM": team_id,
        "PLAYER_ID": player_id,
    }


def _synthetic_play_by_play(*, balanced: bool = True) -> dict[str, object]:
    events = [
        _event(10, "BP"),
        _event(11, "2FGM", clock="09:00", team_id="AAA", player_id="A1"),
        # Deliberately non-monotonic event numbers: source array order still wins.
        _event(8, "OUT", clock="05:00", team_id="AAA", player_id="A1"),
    ]
    if balanced:
        events.append(
            _event(9, "IN", clock="05:00", team_id="AAA", player_id="A6")
        )
    events.append(_event(12, "EG"))
    return {"FirstQuarter": events, "ExtraTime": []}


class RotationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retained_summary = audit_sample(DEFAULT_SAMPLE_DIRECTORY)

    def test_regulation_fixtures_reconcile_every_player_to_the_second(self) -> None:
        summary = self.retained_summary
        totals = summary["totals"]
        regulation_games = [
            game for game in summary["games"] if game["overtime_periods"] == 0
        ]

        self.assertEqual(summary["verdict"], "POSSIBLE WITH CAVEATS")
        self.assertEqual(summary["retained_sample_assessment"], "POSSIBLE WITH CAVEATS")
        self.assertFalse(summary["core_invariants_passed"])
        self.assertTrue(summary["structural_invariants_passed"])
        self.assertEqual(totals["games"], 4)
        self.assertEqual(len(regulation_games), 3)
        self.assertEqual(sum(game["boxscore_player_rows"] for game in regulation_games), 72)
        self.assertEqual(sum(game["exact_player_minute_rows"] for game in regulation_games), 72)
        self.assertTrue(all(not game["minute_discrepancies"] for game in regulation_games))

    def test_retained_substitution_transactions_preserve_five_players(self) -> None:
        totals = self.retained_summary["totals"]

        self.assertEqual(totals["substitution_events"], 504)
        self.assertEqual(totals["in_events"], totals["out_events"])
        self.assertEqual(totals["transaction_groups"], 173)
        self.assertEqual(
            totals["pair_size_distribution"],
            {"1": 112, "2": 46, "3": 12, "4": 3},
        )
        self.assertEqual(totals["non_substitution_events_during_transient_non_five"], 0)
        for game in self.retained_summary["games"]:
            self.assertEqual(game["invalid_out_events"], 0)
            self.assertEqual(game["invalid_in_events"], 0)
            self.assertEqual(game["settled_non_five_transactions"], 0)
            self.assertEqual(game["unsettled_period_ends"], 0)

    def test_four_overtime_fixture_exposes_bounded_minute_disagreement(self) -> None:
        summary = self.retained_summary
        overtime = next(
            game
            for game in summary["games"]
            if game["season_code"] == "E2023" and game["game_code"] == 170
        )

        self.assertEqual(overtime["period_count"], 8)
        self.assertEqual(overtime["overtime_periods"], 4)
        self.assertEqual(overtime["header_checks"]["header_game_seconds"], 3_600)
        self.assertTrue(overtime["header_checks"]["game_time_matches_pbp_periods"])
        self.assertTrue(
            overtime["header_checks"]["boxscore_overtime_periods_match_pbp"]
        )
        self.assertEqual(overtime["official_total_player_seconds"], 36_000)
        self.assertEqual(overtime["reconstructed_total_player_seconds"], 36_000)
        self.assertEqual(overtime["exact_player_minute_rows"], 20)
        self.assertEqual(overtime["maximum_absolute_player_minute_error_seconds"], 60)
        self.assertEqual(overtime["total_absolute_player_minute_error_seconds"], 240)
        self.assertEqual(
            {
                row["player_id"]: row["difference_seconds"]
                for row in overtime["minute_discrepancies"]
            },
            {"P001392": 60, "P005928": -60, "P012788": 60, "PLHK": -60},
        )
        self.assertFalse(overtime["invariants"]["all_player_minutes_match_exactly"])
        self.assertTrue(
            all(
                passed
                for name, passed in overtime["invariants"].items()
                if name != "all_player_minutes_match_exactly"
            )
        )

    def test_overtime_raw_file_provenance_is_stable(self) -> None:
        files = {
            row["relative_path"]: (row["bytes"], row["sha256"])
            for row in self.retained_summary["provenance"]["overtime_probe"]["files"]
        }
        self.assertEqual(
            files,
            {
                "data/samples/rotation_overtime_probe/game_170_boxscore.json": (
                    13_862,
                    "0bd2d930e045c35f3c035972a9230950cb976a1796d65081a2f7c14c90d70a79",
                ),
                "data/samples/rotation_overtime_probe/game_170_header.json": (
                    913,
                    "a34261ef3170c8ef65edbd48ccd67b7758d935079db19081483ac3b8de77d879",
                ),
                "data/samples/rotation_overtime_probe/game_170_play_by_play.json": (
                    198_765,
                    "7aa375371ea628c5ce7392eda98fcddcdf756becf99d2bd6317523706814d2ff",
                ),
            },
        )

    def test_non_monotonic_event_numbers_do_not_break_rotation_order(self) -> None:
        result = audit_game(999, _synthetic_boxscore(), _synthetic_play_by_play())

        self.assertTrue(all(result["invariants"].values()))
        self.assertEqual(result["ordering"]["event_number_inversions"], 1)
        self.assertEqual(result["exact_player_minute_rows"], 11)
        self.assertEqual(result["minute_discrepancies"], [])
        self.assertEqual(result["transactions"]["transaction_groups"], 1)

    def test_unbalanced_transaction_fails_core_invariants(self) -> None:
        result = audit_game(
            999,
            _synthetic_boxscore(),
            _synthetic_play_by_play(balanced=False),
        )

        self.assertFalse(result["invariants"]["balanced_substitution_transactions"])
        self.assertFalse(result["invariants"]["settled_lineups_have_five_players"])
        self.assertFalse(result["invariants"]["all_player_minutes_match_exactly"])
        self.assertGreater(result["non_substitution_events_during_transient_non_five"], 0)

    def test_committed_json_summary_is_reproducible(self) -> None:
        committed = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(committed, self.retained_summary)

    def test_invalid_clock_is_rejected(self) -> None:
        self.assertEqual(parse_clock_seconds("05:07"), 307)
        with self.assertRaises(RotationAuditError):
            parse_clock_seconds("05:99")


if __name__ == "__main__":
    unittest.main()
