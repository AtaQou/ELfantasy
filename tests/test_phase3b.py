from __future__ import annotations

from datetime import UTC, datetime
import unittest

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.modeling.rich_features import (
    MIN_ONOFF_POSSESSIONS,
    RICH_FEATURE_PIPELINE_VERSION,
    assert_source_precedes_cutoff,
    assign_shot_zone,
    rich_dataset_fingerprint,
    validate_rich_cutoffs,
)
from src.modeling.rich_reconstruction import (
    GameContext,
    MINUTE_NEAR_EXACT_TOLERANCE_SECONDS,
    _event_counts,
    parse_game_clock,
    period_length_seconds,
    period_offset_seconds,
    reconstruct_game,
)


def _context(*, overtime: int = 0, a1_seconds: int | None = None) -> GameContext:
    total = 2400 + overtime * 300
    official = {f"A{number}": total for number in range(1, 6)}
    official.update({f"B{number}": total for number in range(1, 6)})
    if a1_seconds is not None:
        official["A1"] = a1_seconds
    return GameContext(
        game_id="GAME_SYNTHETIC",
        season="E2025",
        game_code=999,
        game_time_us=int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000_000),
        overtime_count=overtime,
        home_team_id="AAA",
        away_team_id="BBB",
        starters={
            "AAA": frozenset(f"A{number}" for number in range(1, 6)),
            "BBB": frozenset(f"B{number}" for number in range(1, 6)),
        },
        official_seconds=official,
        invalid_clock=False,
        rotation_discrepancy=False,
    )


def _event(
    period: int,
    sequence: int,
    clock: str,
    play_type: str,
    team_id: str | None = None,
    player_id: str | None = None,
) -> dict[str, object]:
    return {
        "period": period,
        "sequence": sequence,
        "clock": clock,
        "team_id": team_id,
        "player_id": player_id,
        "play_type": play_type,
        "home_score": None,
        "away_score": None,
    }


def _no_sub_events(*, periods: int = 4) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for period in range(1, periods + 1):
        start = "10:00" if period <= 4 else "05:00"
        rows.extend(
            [
                _event(period, period * 10, start, "BP"),
                _event(period, period * 10 + 1, "00:00", "EG"),
            ]
        )
    return rows


def _canonical_has_rich() -> bool:
    if not DEFAULT_DATABASE_PATH.exists():
        return False
    with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
        return bool(
            connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name='ml_player_game_rich_features_v1'"
            ).fetchone()[0]
        )


class RichReconstructionUnitTests(unittest.TestCase):
    def test_clock_and_overtime_offsets(self) -> None:
        self.assertEqual(parse_game_clock("05:07"), 307)
        self.assertEqual(period_length_seconds(4), 600)
        self.assertEqual(period_length_seconds(5), 300)
        self.assertEqual(period_offset_seconds(5), 2400)
        self.assertEqual(period_offset_seconds(8), 3300)
        with self.assertRaises(ValueError):
            parse_game_clock("05:99")

    def test_balanced_substitution_reconstructs_multiple_stints_exactly(self) -> None:
        context = _context(a1_seconds=2100)
        context.official_seconds["A6"] = 300
        events = _no_sub_events()
        events.extend(
            [
                _event(1, 3, "05:00", "OUT", "AAA", "A1"),
                _event(1, 4, "05:00", "IN", "AAA", "A6"),
                _event(2, 15, "10:00", "OUT", "AAA", "A6"),
                _event(2, 16, "10:00", "IN", "AAA", "A1"),
            ]
        )
        result = reconstruct_game(context, events)
        self.assertTrue(result["audit"]["rotation_usable"])
        self.assertEqual(result["audit"]["maximum_absolute_error_seconds"], 0)
        a1 = [row for row in result["players"] if row["player_id"] == "A1"]
        self.assertEqual(len(a1), 2)
        self.assertEqual(sum(row["duration_seconds"] for row in a1), 2100)

    def test_unbalanced_substitution_is_quarantined_without_repair(self) -> None:
        events = _no_sub_events()
        events.append(_event(1, 3, "05:00", "OUT", "AAA", "A1"))
        result = reconstruct_game(_context(), events)
        self.assertFalse(result["audit"]["rotation_usable"])
        self.assertIn("UNBALANCED_SUBSTITUTION", result["audit"]["quarantine_reason"])
        self.assertEqual(result["players"], [])

    def test_same_clock_out_in_for_same_player_is_a_valid_noop(self) -> None:
        events = _no_sub_events()
        events.extend(
            [
                _event(1, 3, "05:00", "OUT", "AAA", "A1"),
                _event(1, 4, "05:00", "IN", "AAA", "A1"),
            ]
        )
        result = reconstruct_game(_context(), events)
        self.assertTrue(result["audit"]["rotation_usable"])
        self.assertEqual(result["audit"]["exact_player_rows"], 10)

    def test_minute_disagreement_exceeding_tolerance_is_quarantined(self) -> None:
        result = reconstruct_game(_context(a1_seconds=2460), _no_sub_events())
        self.assertFalse(result["audit"]["rotation_usable"])
        self.assertEqual(result["audit"]["outside_tolerance_player_rows"], 1)
        self.assertGreater(
            result["audit"]["maximum_absolute_error_seconds"],
            MINUTE_NEAR_EXACT_TOLERANCE_SECONDS,
        )

    def test_overtime_duration_is_reconstructed_explicitly(self) -> None:
        result = reconstruct_game(_context(overtime=1), _no_sub_events(periods=5))
        self.assertTrue(result["audit"]["rotation_usable"])
        self.assertEqual(result["audit"]["overtime_count"], 1)
        self.assertEqual(max(row["end_second"] for row in result["lineups"]), 2700)

    def test_possession_proxy_components_cover_turnover_orebound_and_fts(self) -> None:
        events = [
            _event(1, 1, "09:00", "2FGA", "AAA", "A1"),
            _event(1, 2, "08:59", "O", "AAA", "A2"),
            _event(1, 3, "08:00", "TO", "AAA", "A3"),
            _event(1, 4, "07:00", "FTM", "AAA", "A1"),
            _event(1, 5, "07:00", "FTA", "AAA", "A1"),
            _event(1, 6, "00:00", "EG"),
        ]
        counts = _event_counts(events, "AAA")
        self.assertEqual(counts["fga"], 1)
        self.assertEqual(counts["oreb"], 1)
        self.assertEqual(counts["turnovers"], 1)
        self.assertEqual(counts["fta"], 2)
        self.assertAlmostEqual(counts["estimated_possessions"], 1.88)


class ShotGeometryUnitTests(unittest.TestCase):
    def test_all_five_documented_zones(self) -> None:
        self.assertEqual(assign_shot_zone(2, 0, 125), "RIM")
        self.assertEqual(assign_shot_zone(2, 200, 300), "PAINT_NONRIM")
        self.assertEqual(assign_shot_zone(2, 300, 300), "MIDRANGE")
        self.assertEqual(assign_shot_zone(3, 680, 100), "CORNER3")
        self.assertEqual(assign_shot_zone(3, 0, 675), "ABOVE_BREAK3")

    def test_impossible_and_conflicting_coordinates_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            assign_shot_zone(2, 751, 0)
        with self.assertRaises(ValueError):
            assign_shot_zone(3, 0, 100)
        with self.assertRaises(ValueError):
            assign_shot_zone(2, -1, -1)


class SyntheticRichLeakageTests(unittest.TestCase):
    def _assert_future_fails(self) -> None:
        with self.assertRaises(ValueError):
            assert_source_precedes_cutoff(2_000_000, 2_000_000)
        with self.assertRaises(ValueError):
            assert_source_precedes_cutoff(2_000_001, 2_000_000)
        assert_source_precedes_cutoff(1_999_999, 2_000_000)

    def test_target_rotation_observation_fails(self) -> None:
        self._assert_future_fails()

    def test_target_shot_observation_fails(self) -> None:
        self._assert_future_fails()

    def test_target_opponent_observation_fails(self) -> None:
        self._assert_future_fails()

    def test_target_onoff_observation_fails(self) -> None:
        self._assert_future_fails()


@unittest.skipUnless(_canonical_has_rich(), "canonical Phase 3B artifacts are absent")
class CanonicalRichFeatureTests(unittest.TestCase):
    def scalar(self, query: str):
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            return connection.execute(query).fetchone()[0]

    def test_core_row_identity_is_preserved_by_left_extension(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT (SELECT count(*) FROM ml_player_game_core_features_v1) - "
                "(SELECT count(*) FROM ml_player_game_rich_features_v1)"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*)-count(DISTINCT model_row_id) "
                "FROM ml_player_game_rich_features_v1"
            ),
            0,
        )

    def test_all_source_families_precede_cutoff(self) -> None:
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            self.assertEqual(validate_rich_cutoffs(connection), {
                "rotation": 0,
                "onoff": 0,
                "lineup": 0,
                "shot": 0,
                "opponent_shot": 0,
            })

    def test_reconstructable_lineups_have_exactly_five_distinct_players(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pbp_lineup_stints_v1 WHERE "
                "list_unique([player_1_id,player_2_id,player_3_id,player_4_id,player_5_id])<>5"
            ),
            0,
        )

    def test_usable_rotation_minutes_are_within_tolerance(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pbp_rotation_player_minute_audit_v1 "
                "WHERE rotation_usable AND NOT within_tolerance"
            ),
            0,
        )

    def test_rotation_and_possession_values_are_not_impossible(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pbp_player_stints_v1 "
                "WHERE duration_seconds<=0 OR start_second<0 OR end_second<=start_second"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pbp_possessions_v1 WHERE estimated_possessions<0"
            ),
            0,
        )

    def test_rates_and_flags_remain_in_valid_ranges(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_rich_features_v1 WHERE "
                "rot_last5_closing_lineup_rate NOT BETWEEN 0 AND 1 OR "
                "lineup_last5_top_lineup_share_avg NOT BETWEEN 0 AND 1 OR "
                "shot_season_rim_attempt_rate NOT BETWEEN 0 AND 1 OR "
                "opp_shot_season_rim_attempt_rate_allowed NOT BETWEEN 0 AND 1"
            ),
            0,
        )

    def test_onoff_availability_obeys_possession_guard(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_rich_features_v1 "
                f"WHERE has_onoff_features AND onoff_last5_on_possessions_n < {MIN_ONOFF_POSSESSIONS}"
            ),
            0,
        )

    def test_invalid_clock_quarantine_is_family_specific(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM pbp_player_stints_v1 WHERE game_id IN "
                "(SELECT entity_id FROM data_anomalies WHERE quarantined "
                "AND anomaly_code='INVALID_PBP_CLOCK')"
            ),
            0,
        )
        self.assertGreater(
            self.scalar(
                "SELECT count(*) FROM shots WHERE canonical_game_id IN "
                "(SELECT entity_id FROM data_anomalies WHERE quarantined "
                "AND anomaly_code='INVALID_PBP_CLOCK')"
            ),
            0,
        )

    def test_primary_population_has_verified_target_and_all_major_families(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_phase3b_primary_evaluation_v1 WHERE "
                "season NOT IN ('E2022','E2023','E2024','E2025') OR "
                "actual_fantasy_points IS NULL OR NOT rich_all_major_families_available"
            ),
            0,
        )

    def test_no_target_game_outcomes_appear_in_rich_only_schema(self) -> None:
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info('ml_player_game_rich_features_v1')"
                ).fetchall()
            }
        prohibited = {
            "target_minutes", "target_starter", "target_stint_count",
            "target_lineup", "target_shots", "target_onoff",
        }
        self.assertFalse(columns & prohibited)
        self.assertIn("rich_feature_pipeline_version", columns)

    def test_generation_fingerprint_is_deterministic(self) -> None:
        with connect_database(DEFAULT_DATABASE_PATH, read_only=True) as connection:
            first = rich_dataset_fingerprint(connection)
            second = rich_dataset_fingerprint(connection)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rescheduled_rows_are_ordered_by_actual_datetime(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_core_plus_rich_v1 WHERE "
                "season IN ('E2022','E2023','E2024') AND round_number IN (2,6,17,24) "
                "AND (rot_source_max_game_time_us>=epoch_us(feature_cutoff_time) OR "
                "shot_source_max_game_time_us>=epoch_us(feature_cutoff_time) OR "
                "opp_shot_source_max_game_time_us>=epoch_us(feature_cutoff_time))"
            ),
            0,
        )

    def test_feature_pipeline_version_is_constant(self) -> None:
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM ml_player_game_rich_features_v1 "
                f"WHERE rich_feature_pipeline_version<>'{RICH_FEATURE_PIPELINE_VERSION}'"
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
