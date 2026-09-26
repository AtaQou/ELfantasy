"""Focused coverage of the combined L5 query, including partial boxes."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import duckdb

from src.control_center.repository import ControlCenterRepository, _PREPARATION_FP_INPUTS


class RecentFormPreparationTests(unittest.TestCase):
    def test_latest_five_include_partial_preparation_without_inventing_stats(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "recent.duckdb"
            with duckdb.connect(str(path)) as connection:
                connection.execute("""
                    CREATE TABLE ml_player_game_targets_v1 AS
                    SELECT 'P1' AS player_id, 'EL' AS game_id,
                           TIMESTAMPTZ '2026-05-20 12:00:00+00' AS target_game_time,
                           100.0 AS actual_fantasy_points, 40.0 AS target_minutes,
                           'E2025' AS season, 2025 AS season_start_year,
                           'PLAYED' AS eligibility_status;
                    CREATE TABLE preparation_games (
                        preparation_game_id VARCHAR, game_date TIMESTAMPTZ,
                        game_type VARCHAR, home_team_name VARCHAR, away_team_name VARCHAR
                    );
                """)
                fields = ', '.join(f'{name} DOUBLE' for name in _PREPARATION_FP_INPUTS)
                connection.execute(f"""
                    CREATE TABLE preparation_player_stats (
                        preparation_game_id VARCHAR, canonical_player_id VARCHAR,
                        team_name_raw VARCHAR, minutes DOUBLE, starter BOOLEAN,
                        pir DOUBLE, efficiency DOUBLE, {fields}
                    )
                """)
                # All four preparation competition types belong in the shared L5.
                games = [
                    ('G1', 'FRIENDLY', 10, 2),
                    ('G2', 'PRESEASON_TOURNAMENT', 20, 4),
                    ('G3', 'SUPERCUP', 30, 6),
                    ('G4', 'DOMESTIC_OFFICIAL', None, 8),
                    ('G5', 'FRIENDLY', None, None),
                    ('DNP', 'FRIENDLY', 0, 0),
                    ('EMPTY', 'FRIENDLY', None, None),
                ]
                for day, (game_id, kind, minutes, pir) in enumerate(games, 10):
                    connection.execute(
                        'INSERT INTO preparation_games VALUES (?, ?, ?, ?, ?)',
                        [game_id, f'2026-09-{day} 12:00:00+00', kind, 'Home', 'Away'],
                    )
                    connection.execute("""
                        INSERT INTO preparation_player_stats
                        (preparation_game_id, canonical_player_id, team_name_raw,
                         minutes, pir, points)
                        VALUES (?, 'P1', 'Home', ?, ?, ?)
                    """, [game_id, minutes, pir, 5 if game_id not in ('DNP', 'EMPTY') else 0])
            repository = ControlCenterRepository.__new__(ControlCenterRepository)
            repository.database_path = path
            form = repository.player_recent_form(['P1'])['P1']
            self.assertEqual([g['game_id'] for g in form['last5_games']],
                             ['G5', 'G4', 'G3', 'G2', 'G1'])
            self.assertEqual(form['last5_minutes_median'], 20)
            self.assertEqual(form['last5_fp_average'], 5)
            self.assertIsNone(form['last_game_minutes'])
            self.assertIsNone(form['last_game_fp'])
            self.assertEqual(form['last5_games'][1]['fantasy_points'], 8)
            self.assertEqual(form['last5_games'][1]['fantasy_points_kind'], 'PIR_BASE_ONLY')
            self.assertEqual(form['season_minutes_average'], 40)
            self.assertEqual(form['season_fp_average'], 100)


if __name__ == '__main__':
    unittest.main()
