from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.db.ingestion import ingest_retained_samples
from src.modeling.core_features import (
    FEATURE_PIPELINE_VERSION,
    build_phase3a_datasets,
    dataset_fingerprint,
    estimated_possessions,
    generate_core_features,
    pace_per_40,
)
from src.modeling.fantasy_scoring import (
    OFFICIAL_CURRENT_STATUS,
    OFFICIAL_HISTORICAL_STATUS,
    STANDARDIZED_STATUS,
    score_player_game,
    target_rule_status,
)


SCORING_INPUT = {
    "points": 20,
    "total_rebounds": 7,
    "assists": 4,
    "steals": 2,
    "blocks": 1,
    "fouls_drawn": 5,
    "turnovers": 3,
    "blocks_received": 1,
    "fouls_committed": 2,
    "two_points_made": 5,
    "two_points_attempted": 8,
    "three_points_made": 2,
    "three_points_attempted": 5,
    "free_throws_made": 4,
    "free_throws_attempted": 4,
}


class FantasyScoringTests(unittest.TestCase):
    def test_explicit_formula_and_win_bonus(self) -> None:
        losing = score_player_game(SCORING_INPUT, team_won=False)
        winning = score_player_game(SCORING_INPUT, team_won=True)
        self.assertEqual(losing.base_score, Decimal("27"))
        self.assertEqual(losing.total, Decimal("27"))
        self.assertEqual(winning.team_win_bonus, Decimal("2.7"))
        self.assertEqual(winning.total, Decimal("29.7"))

    def test_negative_winner_moves_toward_zero(self) -> None:
        values = dict(SCORING_INPUT)
        values.update(points=0, total_rebounds=0, assists=0, steals=0, blocks=0,
                      fouls_drawn=0, turnovers=2, blocks_received=0,
                      fouls_committed=0, two_points_made=0,
                      two_points_attempted=0, three_points_made=0,
                      three_points_attempted=0, free_throws_made=0,
                      free_throws_attempted=0)
        result = score_player_game(values, team_won=True)
        self.assertEqual(result.base_score, Decimal("-2"))
        self.assertEqual(result.total, Decimal("-1.8"))

    def test_invalid_shooting_line_is_rejected(self) -> None:
        values = dict(SCORING_INPUT, two_points_made=9)
        with self.assertRaises(ValueError):
            score_player_game(values, team_won=False)

    def test_rule_status_is_explicit_by_season(self) -> None:
        self.assertEqual(target_rule_status("E2021"), STANDARDIZED_STATUS)
        self.assertEqual(target_rule_status("E2022"), OFFICIAL_HISTORICAL_STATUS)
        self.assertEqual(target_rule_status("E2025"), OFFICIAL_CURRENT_STATUS)

    def test_overtime_pace_is_normalized_to_40_minutes(self) -> None:
        possessions = estimated_possessions(70, 10, 12, 20)
        self.assertAlmostEqual(possessions, 80.8)
        self.assertAlmostEqual(pace_per_40(90, 45), 80.0)
        self.assertAlmostEqual(pace_per_40(90, 50), 72.0)
        with self.assertRaises(ValueError):
            pace_per_40(80, 39)


class Phase3FeaturePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.database = cls.root / "canonical.duckdb"
        ingest_retained_samples(cls.database, raw_root=cls.root / "raw")
        cls.summary = build_phase3a_datasets(
            cls.database,
            derived_root=cls.root / "derived",
            sample_root=cls.root / "samples",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def scalar(self, query: str):
        with connect_database(self.database, read_only=True) as connection:
            return connection.execute(query).fetchone()[0]

    def test_active_player_semantics_and_audit_columns(self) -> None:
        self.assertGreater(self.summary.modelling_rows, 0)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE target_game_time IS NULL OR feature_cutoff_time <> target_game_time "
                f"OR feature_pipeline_version <> '{FEATURE_PIPELINE_VERSION}'"
            ),
            0,
        )

    def test_target_and_future_games_are_excluded_from_player_history(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE player_history_max_game_time >= feature_cutoff_time"
            ),
            0,
        )

    def test_target_game_is_excluded_from_team_and_opponent_history(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE team_history_max_game_time >= feature_cutoff_time "
                "OR opp_history_max_game_time >= feature_cutoff_time"
            ),
            0,
        )

    def test_last_five_sample_count_is_bounded(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE last_5_fp_n < 0 OR last_5_fp_n > 5"
            ),
            0,
        )

    def test_cold_start_is_retained_with_null_statistics(self) -> None:
        self.assertGreater(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE career_el_games_before = 0 AND last_5_fp_n = 0 "
                "AND last_5_fp_avg IS NULL"
            ),
            0,
        )

    def test_standard_deviation_requires_two_observations(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE last_5_fp_n < 2 AND last_5_fp_std IS NOT NULL"
            ),
            0,
        )

    def test_rate_features_use_guarded_ratio_of_sums(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE last_5_fp_per_min IS NOT NULL AND last_5_fp_n = 0"
            ),
            0,
        )

    def test_home_and_away_orientation_is_consistent(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE home_game = away_game OR "
                "(home_game AND home_away <> 'home') OR "
                "(away_game AND home_away <> 'away')"
            ),
            0,
        )

    def test_time_features_cannot_be_negative(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_v1 "
                "WHERE days_since_previous_el_game < 0 "
                "OR team_days_since_previous_el_game < 0"
            ),
            0,
        )

    def test_quarantined_legacy_credits_cannot_enter_extension(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_with_fantasy_v1 "
                "WHERE season IN ('E2022','E2023','E2024') "
                "AND fantasy_credits_pre_matchday IS NOT NULL"
            ),
            0,
        )

    def test_walk_forward_order_uses_actual_game_time(self) -> None:
        self.assertEqual(
            self.scalar(
                """
                SELECT count(*) FROM (
                    SELECT prediction_time,
                           lag(prediction_time) OVER (
                             ORDER BY chronological_game_index
                           ) AS previous_time
                    FROM ml_walk_forward_splits_v1
                ) WHERE prediction_time < previous_time
                """
            ),
            0,
        )

    def test_generation_is_reproducible_and_duplicate_free(self) -> None:
        with connect_database(self.database, read_only=True) as connection:
            before = dataset_fingerprint(connection)
        generate_core_features(self.database)
        with connect_database(self.database, read_only=True) as connection:
            after = dataset_fingerprint(connection)
        self.assertEqual(before, after)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) - count(DISTINCT player_game_id) "
                "FROM ml_player_game_core_features_v1"
            ),
            0,
        )


@unittest.skipUnless(
    DEFAULT_DATABASE_PATH.exists(), "canonical Phase 3A database is not present"
)
class CanonicalPhase3SafetyTests(unittest.TestCase):
    def scalar(self, query: str):
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            return connection.execute(query).fetchone()[0]

    def test_e2025_prices_are_positive_and_legacy_prices_are_absent(self) -> None:
        self.assertGreater(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_with_fantasy_v1 "
                "WHERE season='E2025' AND fantasy_credits_pre_matchday IS NOT NULL"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_features_with_fantasy_v1 "
                "WHERE season IN ('E2022','E2023','E2024') "
                "AND fantasy_credits_pre_matchday IS NOT NULL"
            ),
            0,
        )

    def test_historical_position_attachment_has_same_season_crosswalk(self) -> None:
        self.assertEqual(
            self.scalar(
                """
                SELECT count(*)
                FROM ml_player_game_core_features_with_fantasy_v1 AS core
                WHERE core.fantasy_position IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM fantasy_entities AS entity
                    JOIN fantasy_player_crosswalk AS crosswalk
                      USING (fantasy_entity_id)
                    WHERE entity.fantasy_id = core.fantasy_id
                      AND crosswalk.canonical_player_id = core.player_id
                      AND crosswalk.season_code = core.season
                      AND crosswalk.valid_to IS NULL
                      AND crosswalk.mapping_status = 'MATCHED'
                  )
                """
            ),
            0,
        )

    def test_rescheduled_legacy_attachment_obeys_source_date_window(self) -> None:
        self.assertEqual(
            self.scalar(
                """
                SELECT count(*)
                FROM ml_player_game_core_features_with_fantasy_v1 AS core
                WHERE core.season IN ('E2022','E2023','E2024')
                  AND core.fantasy_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM fantasy_market_snapshots AS snapshot
                    JOIN fantasy_entities AS entity USING (fantasy_entity_id)
                    JOIN fantasy_market_snapshot_semantics AS semantics
                      USING (snapshot_record_id)
                    JOIN fantasy_player_crosswalk AS crosswalk
                      ON crosswalk.fantasy_entity_id = snapshot.fantasy_entity_id
                     AND crosswalk.season_code = snapshot.season_code
                    WHERE snapshot.season_code = core.season
                      AND entity.fantasy_id = core.fantasy_id
                      AND crosswalk.canonical_player_id = core.player_id
                      AND snapshot.canonical_team_id = core.team_id
                      AND semantics.competition_round = core.round_number
                      AND semantics.position_valid_as_of_matchday
                      AND cast(core.local_game_date AS DATE) BETWEEN
                          cast(json_extract_string(
                            semantics.source_request_parameters_json,
                            '$.date_from[0]') AS DATE)
                          AND cast(json_extract_string(
                            semantics.source_request_parameters_json,
                            '$.date_to[0]') AS DATE)
                  )
                """
            ),
            0,
        )

    def test_last_five_is_exactly_five_most_recent_prior_appearances(self) -> None:
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            row = connection.execute(
                """
                SELECT player_id, epoch_us(target_game_time), last_5_fp_avg,
                       last_5_fp_n
                FROM ml_player_game_core_features_v1
                WHERE last_5_fp_n = 5 LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(row)
            expected = connection.execute(
                """
                SELECT avg(standardized_fantasy_points), count(*) FROM (
                    SELECT standardized_fantasy_points
                    FROM ml_player_game_targets_v1
                    WHERE player_id = ? AND eligibility_status = 'PLAYED'
                      AND epoch_us(target_game_time) < ?
                    ORDER BY target_game_time DESC, game_id DESC LIMIT 5
                )
                """,
                [row[0], row[1]],
            ).fetchone()
        self.assertAlmostEqual(row[2], expected[0])
        self.assertEqual(row[3], expected[1])


if __name__ == "__main__":
    unittest.main()
