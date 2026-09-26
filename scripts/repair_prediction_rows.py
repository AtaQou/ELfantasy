"""Restore missing rows from a run's saved prediction artifact without inference."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.live.prediction import _insert_prediction_row


def repair(run_id, database=DEFAULT_DATABASE_PATH, *, apply=False):
    with connect_database(database, read_only=not apply) as connection:
        run = connection.execute(
            "SELECT output_json_path, season_code, prediction_generated_at, "
            "feature_manifest_fingerprint FROM live_prediction_runs WHERE prediction_run_id=?",
            [run_id],
        ).fetchone()
        if run is None:
            raise ValueError("Unknown prediction run")
        path = Path(run[0])
        raw = path.read_bytes()
        artifact = json.loads(raw)
        if artifact['prediction_run_id'] != run_id or artifact['season'] != run[1]:
            raise ValueError("Artifact does not belong to this run")
        if datetime.fromisoformat(artifact['prediction_generated_at']) != run[2]:
            raise ValueError("Artifact generation time differs from the stored run")
        existing = {
            (row[0], row[1]): row[2:]
            for row in connection.execute(
                "SELECT scenario_id, fantasy_player_id, canonical_player_id, "
                "canonical_game_id, expected_fp, adjusted_expected_minutes "
                "FROM live_player_predictions WHERE prediction_run_id=?", [run_id],
            ).fetchall()
        }
        scenarios = dict(connection.execute(
            "SELECT scenario_id,prediction_scenario_id FROM live_prediction_scenarios "
            "WHERE prediction_run_id=?", [run_id],
        ).fetchall())
        result = SimpleNamespace(prediction_run_id=run_id, season_code=run[1],
                                 prediction_generated_at=run[2])
        pending = []
        seen = set()
        for scenario in artifact['scenarios']:
            for source in scenario['players']:
                key = (source['scenario_id'], str(source['fantasy_player_id']))
                if key in seen:
                    raise ValueError("Duplicate artifact player")
                seen.add(key)
                identity = (source['player_id'], source['game_id'], source['expected_fp'],
                            source['adjusted_expected_minutes'])
                if key in existing:
                    if existing[key] != identity:
                        raise ValueError("Artifact differs from an existing prediction")
                    continue
                entities = connection.execute(
                    "SELECT DISTINCT m.fantasy_entity_id FROM fantasy_market_snapshots m "
                    "JOIN fantasy_entities e USING(fantasy_entity_id) "
                    "JOIN live_prediction_runs r ON r.market_snapshot_batch_id=m.snapshot_batch_id "
                    "WHERE r.prediction_run_id=? AND e.fantasy_id=? AND e.entity_type='PLAYER'",
                    [run_id, key[1]],
                ).fetchall()
                if len(entities) != 1 or key[0] not in scenarios:
                    raise ValueError("Artifact player/scenario cannot be mapped unambiguously")
                row = dict(source)
                row.update(fantasy_id=entities[0][0], team_id=source['team'],
                           opponent_team_id=source['opponent'], scheduled_tip_time=source['tip_time'])
                pending.append((scenarios[key[0]], SimpleNamespace(**row)))
        if set(existing) - seen:
            raise ValueError("Stored predictions are absent from the artifact")
        if apply and pending:
            connection.execute('BEGIN TRANSACTION')
            try:
                for scenario_id, row in pending:
                    _insert_prediction_row(connection, result, scenario_id, row, run[3])
                    # The exported artifact preserves predictions, but not all model inputs.
                    # Record that limitation explicitly instead of inventing feature values.
                    connection.execute(
                        "UPDATE live_player_predictions SET feature_snapshot_json='{}', "
                        "provenance_json=? WHERE prediction_run_id=? AND scenario_id=? "
                        "AND fantasy_entity_id=?",
                        [json.dumps({'recovered_from': str(path),
                                     'artifact_sha256': hashlib.sha256(raw).hexdigest(),
                                     'feature_snapshot_unavailable': True}),
                         run_id, row.scenario_id, row.fantasy_id],
                    )
                connection.execute('COMMIT')
            except BaseException:
                connection.execute('ROLLBACK')
                raise
        return {'artifact_rows': len(seen), 'existing_rows': len(existing),
                'missing_rows': len(pending), 'added_rows': len(pending) if apply else 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(repair(args.run_id, apply=args.apply)))
