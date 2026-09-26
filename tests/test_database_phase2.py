from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.db.database import (
    connect_database,
    hold_database_open,
    initialize_database,
    release_database_anchor,
)
from src.db.fantasy_ingestion import FantasySnapshotSpec, ingest_fantasy_market_payload
from src.db.ids import canonical_official_player_code, stable_id
from src.db.ingestion import PROJECT_ROOT, ingest_retained_samples
from src.db.integrity import validate_database
from src.db.raw_store import archive_bytes
from src.db.remote_ingestion import _representative_rows


class DatabasePhase2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "canonical.duckdb"
        cls.raw_root = cls.root / "raw"
        cls.first_official = ingest_retained_samples(
            cls.database, raw_root=cls.raw_root
        )
        market_path = (
            PROJECT_ROOT
            / "data"
            / "samples"
            / "fantasy_current"
            / "market_response.sanitized.json"
        )
        cls.market_payload = json.loads(market_path.read_text(encoding="utf-8"))
        cls.snapshot_spec = FantasySnapshotSpec(
            season_code="E2025",
            competition_id=30,
            players_list_id=30,
            matchday_id=1000,
            matchday_number=38,
            observed_at=datetime(2026, 8, 10, 21, 49, 26, tzinfo=UTC),
            historical_request=False,
            status_valid_as_of_matchday=False,
        )
        cls.first_fantasy = ingest_fantasy_market_payload(
            cls.market_payload,
            cls.snapshot_spec,
            cls.database,
            raw_root=cls.raw_root,
            roster_path=(
                PROJECT_ROOT
                / "data"
                / "samples"
                / "e2025_rounds_1_2"
                / "roster_players.csv"
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def scalar(self, query: str) -> int:
        with connect_database(self.database, read_only=True) as connection:
            return int(connection.execute(query).fetchone()[0])

    def test_database_anchor_survives_short_lived_cursor_close(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "warm.duckdb"
            owner = hold_database_open(database)
            try:
                with connect_database(database) as connection:
                    connection.execute("CREATE TABLE warm_check(value INTEGER)")
                    connection.execute("INSERT INTO warm_check VALUES (7)")
                self.assertEqual(
                    owner.execute("SELECT value FROM warm_check").fetchone(), (7,)
                )
                with connect_database(database, read_only=True) as connection:
                    self.assertEqual(
                        connection.execute("SELECT count(*) FROM warm_check").fetchone(), (1,)
                    )
            finally:
                release_database_anchor(database)

    def test_migrations_are_forward_only_and_idempotent(self) -> None:
        self.assertEqual(initialize_database(self.database), [])
        self.assertEqual(self.scalar("SELECT count(*) FROM schema_migrations"), 14)

    def test_retained_fixture_counts_and_quarantine(self) -> None:
        expected = {
            "games": 21,
            "player_game_stats": 96,
            "team_game_stats": 8,
            "play_by_play_events": 2386,
            "shots": 497,
            "player_team_memberships": 335,
            "data_anomalies": 2,
        }
        for table, count in expected.items():
            with self.subTest(table=table):
                self.assertEqual(self.scalar(f"SELECT count(*) FROM {table}"), count)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM data_anomalies WHERE quarantined "
                "AND anomaly_code = 'ROTATION_PLAYER_MINUTE_DISCREPANCY'"
            ),
            1,
        )

    def test_winner_is_derived_from_score(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT winner.official_team_code
                FROM games_with_derived_winner AS game
                JOIN teams AS winner
                  ON winner.canonical_team_id = game.derived_winner_team_id
                WHERE game.season_code = 'E2025' AND game.game_code = 1
                """
            ).fetchone()
        self.assertEqual(row, ("IST",))

    def test_event_order_and_shot_limitations_are_preserved(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            duplicate_sequences = connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT canonical_game_id, source_sequence
                    FROM play_by_play_events GROUP BY ALL HAVING count(*) > 1
                )
                """
            ).fetchone()[0]
            inversions = connection.execute(
                """
                WITH events AS (
                    SELECT canonical_game_id, raw_event_number,
                           lag(raw_event_number) OVER (
                             PARTITION BY canonical_game_id ORDER BY source_sequence
                           ) AS previous
                    FROM play_by_play_events
                )
                SELECT count(*) FROM events WHERE raw_event_number < previous
                """
            ).fetchone()[0]
            shot_notes = connection.execute(
                "SELECT count(DISTINCT feed_completeness_note) FROM shots"
            ).fetchone()[0]
        self.assertEqual(duplicate_sequences, 0)
        self.assertGreater(inversions, 0)
        self.assertEqual(shot_notes, 1)

    def test_fantasy_entities_crosswalk_and_team_alias(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            types = dict(
                connection.execute(
                    "SELECT entity_type, count(*) FROM fantasy_entities GROUP BY entity_type"
                ).fetchall()
            )
            mappings = dict(
                connection.execute(
                    "SELECT mapping_status, count(*) FROM fantasy_player_crosswalk GROUP BY mapping_status"
                ).fetchall()
            )
            baskonia = connection.execute(
                """
                SELECT team.official_team_code, alias.manually_reviewed
                FROM team_aliases AS alias
                JOIN teams AS team USING (canonical_team_id)
                WHERE alias.source = 'fantasy' AND alias.alias_type = 'CODE'
                  AND alias.alias_value = 'KBA' AND alias.season_code = 'E2025'
                """
            ).fetchone()
        self.assertEqual(types, {"PLAYER": 345, "COACH": 20})
        self.assertEqual(mappings, {"MATCHED": 295, "UNMATCHED": 50})
        self.assertEqual(baskonia, ("BAS", True))

    def test_unsafe_fantasy_overlay_is_blocked(self) -> None:
        self.assertEqual(self.scalar("SELECT count(*) FROM fantasy_market_snapshots"), 365)
        self.assertEqual(
            self.scalar(
                """
                SELECT count(*) FROM leakage_safe_fantasy_market
                WHERE is_injured IS NOT NULL OR probability_of_playing IS NOT NULL
                   OR started_from_bench IS NOT NULL OR is_on_fire IS NOT NULL
                   OR average_points IS NOT NULL
                """
            ),
            0,
        )
        self.assertEqual(self.scalar("SELECT count(*) FROM availability_events"), 0)

    def test_all_fact_rows_have_raw_provenance(self) -> None:
        for table in (
            "games",
            "player_game_stats",
            "team_game_stats",
            "play_by_play_events",
            "shots",
            "player_team_memberships",
            "fantasy_market_snapshots",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    self.scalar(
                        f"""
                        SELECT count(*) FROM {table} AS fact
                        LEFT JOIN raw_artifacts AS raw
                          ON raw.artifact_id = fact.source_artifact_id
                        WHERE raw.artifact_id IS NULL
                        """
                    ),
                    0,
                )

    def test_full_integrity_suite_passes(self) -> None:
        result = validate_database(self.database)
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["check_count"], 30)

    def test_reingestion_is_idempotent(self) -> None:
        second_official = ingest_retained_samples(
            self.database, raw_root=self.raw_root
        )
        second_fantasy = ingest_fantasy_market_payload(
            self.market_payload,
            self.snapshot_spec,
            self.database,
            raw_root=self.raw_root,
            roster_path=(
                PROJECT_ROOT
                / "data"
                / "samples"
                / "e2025_rounds_1_2"
                / "roster_players.csv"
            ),
        )
        self.assertEqual(second_official.inserted_records, 0)
        self.assertEqual(second_fantasy.inserted_records, 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM fantasy_market_snapshots"), 365)

    def test_player_code_canonicalization_is_conservative(self) -> None:
        self.assertEqual(canonical_official_player_code("P007200"), "007200")
        self.assertEqual(canonical_official_player_code("007200"), "007200")
        self.assertEqual(canonical_official_player_code("PLHK"), "PLHK")

    def test_representative_selection_is_bounded_and_deterministic(self) -> None:
        rows = [{"gamenumber": str(index)} for index in range(1, 11)]
        self.assertEqual(
            [row["gamenumber"] for row in _representative_rows(rows, 3)],
            ["1", "5", "10"],
        )


class RawStoreTests(unittest.TestCase):
    def test_raw_bytes_are_immutable_and_conflicts_are_sidecars(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = archive_bytes(b"one", Path("fixture.json"), raw_root=root)
            duplicate = archive_bytes(b"one", Path("fixture.json"), raw_root=root)
            changed = archive_bytes(b"two", Path("fixture.json"), raw_root=root)
            self.assertEqual(first.path, duplicate.path)
            self.assertEqual(first.sha256, duplicate.sha256)
            self.assertNotEqual(first.path, changed.path)
            self.assertEqual(first.path.read_bytes(), b"one")
            self.assertEqual(changed.path.read_bytes(), b"two")


if __name__ == "__main__":
    unittest.main()
