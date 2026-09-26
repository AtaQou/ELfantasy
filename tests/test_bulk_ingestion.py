from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.ingest_history import CORE_POLICY_RANGE, RICH_POLICY_RANGE, _require_policy
from src.db.bulk_ingestion import _directory_size, _valid_boxscore
from src.db.remote_ingestion import _boxscore_has_players


class HistoricalPolicyTests(unittest.TestCase):
    def test_revised_core_and_rich_windows_are_accepted(self) -> None:
        _require_policy(list(range(2018, 2026)), CORE_POLICY_RANGE, "core", False)
        _require_policy(list(range(2021, 2026)), RICH_POLICY_RANGE, "events", False)

    def test_old_bulk_history_fails_closed_without_explicit_override(self) -> None:
        with self.assertRaisesRegex(SystemExit, "outside revised policy"):
            _require_policy([2007, 2018], CORE_POLICY_RANGE, "core", False)
        with self.assertRaisesRegex(SystemExit, "outside revised policy"):
            _require_policy([2018, 2021], RICH_POLICY_RANGE, "events", False)

    def test_explicit_future_policy_override_is_possible(self) -> None:
        _require_policy([2017], CORE_POLICY_RANGE, "core", True)

    def test_directory_size_counts_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "a.bin").write_bytes(b"abc")
            (root / "nested" / "b.bin").write_bytes(b"12345")
            self.assertEqual(_directory_size(root), 8)

    def test_empty_or_one_sided_boxscore_is_not_normalized(self) -> None:
        empty = {"Stats": [{"PlayersStats": []}, {"PlayersStats": []}]}
        one_sided = {
            "Stats": [
                {"PlayersStats": [{"Player_ID": "P1"}]},
                {"PlayersStats": []},
            ]
        }
        complete = {
            "Stats": [
                {"PlayersStats": [{"Player_ID": "P1"}]},
                {"PlayersStats": [{"Player_ID": "P2"}]},
            ]
        }
        self.assertFalse(_boxscore_has_players(empty))
        self.assertFalse(_valid_boxscore(empty)[0])
        self.assertFalse(_boxscore_has_players(one_sided))
        self.assertFalse(_valid_boxscore(one_sided)[0])
        self.assertTrue(_boxscore_has_players(complete))
        self.assertTrue(_valid_boxscore(complete)[0])


if __name__ == "__main__":
    unittest.main()
