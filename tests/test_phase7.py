from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import unittest
import duckdb

from src.db.database import connect_database, initialize_database
from src.strategy.artifact import validate_phase7_artifact
from src.strategy.coach import build_coach_feature_history, train_coach_model
from src.strategy.engine import apply_availability_scenario, optimize_stochastic
from src.strategy.historical import load_historical_market
from src.strategy.optimizer import optimize_deterministic
from src.strategy.rules import (
    Lineup,
    RuleViolation,
    coach_fantasy_score,
    load_rules,
    postponed_average_score,
    score_lineup,
    validate_lineup,
    validate_turn_transition,
)
from src.strategy.simulation import (
    build_marginal,
    recommend_next_turn,
    simulate_player_outcomes,
)
from scripts.optimize_fantasy import _write_snapshot


def _pools() -> tuple[pd.DataFrame, pd.DataFrame]:
    players = []
    for position, count in (("GUARD", 8), ("FORWARD", 8), ("CENTER", 4)):
        for index in range(count):
            mean = 8.0 + index
            players.append({
                "player_id": f"{position}_{index}", "entity_id": f"E_{position}_{index}",
                "name": f"{position} {index}", "team_id": f"TEAM_{index % 10}",
                "game_id": f"GAME_{index % 10}", "position": position,
                "credits": 5.0 + 0.1 * index, "turn": 1 + index % 2,
                "expected_fp": mean, "p10_fp": mean - 7, "p25_fp": mean - 4,
                "p50_fp": mean, "p75_fp": mean + 4, "p90_fp": mean + 7,
                "p95_fp": mean + 9, "prob_fp_le_5": 0.1,
                "prob_fp_le_10": 0.25, "prob_fp_le_15": 0.5,
                "prob_fp_ge_20": 0.2, "prob_fp_ge_25": 0.1,
                "prob_fp_ge_30": 0.04, "prob_fp_ge_35": 0.02,
                "prob_fp_ge_40": 0.01, "actual_fp": mean,
            })
    coaches = pd.DataFrame([{
        "coach_id": f"COACH_{index}", "entity_id": f"EC_{index}",
        "name": f"Coach {index}", "team_id": f"TEAM_{index}",
        "credits": 5.0, "expected_score": float(index), "actual_score": float(index),
        "turn": 1,
        **{f"prob_score_{score}": float(score == -5) for score in (-20, -10, -5, 10, 20, 25)},
    } for index in range(5)])
    return pd.DataFrame(players), coaches


def test_phase7_rules_manifest_is_versioned_and_sourced() -> None:
    rules = load_rules()
    assert rules.ruleset_version == "elfc_classic_e2025_v1"
    assert rules.season_codes == ("E2025",)
    assert len(rules.fingerprint) == 64
    assert len(rules.manifest["sources"]) >= 5


def test_phase7_e2026_production_gate_fails_closed() -> None:
    with unittest.TestCase().assertRaisesRegex(RuleViolation, "E2026"):
        load_rules().assert_production_allowed("E2026")


def test_phase7_exact_roster_counts_and_budget() -> None:
    players, coaches = _pools(); rules = load_rules()
    result = optimize_deterministic(players, coaches, rules)
    selected = players[players.player_id.isin(result.lineup.player_ids)]
    assert len(selected) == 10
    assert selected.position.value_counts().to_dict() == {"GUARD": 4, "FORWARD": 4, "CENTER": 2}
    assert result.total_credits <= 100
    assert result.optimal and result.solver_gap == 0


def test_phase7_starting_formation_captain_and_sixth_are_legal() -> None:
    players, coaches = _pools(); rules = load_rules()
    result = optimize_deterministic(players, coaches, rules)
    formation = tuple(
        sum(players.set_index("player_id").loc[player, "position"] == position
            for player in result.lineup.starters)
        for position in ("GUARD", "FORWARD", "CENTER")
    )
    assert formation in rules.formations
    assert result.lineup.captain in result.lineup.starters
    assert result.lineup.sixth_man not in result.lineup.starters


def test_phase7_bench_captain_and_sixth_scoring() -> None:
    rules = load_rules()
    lineup = Lineup(
        tuple(f"P{index}" for index in range(10)), "C",
        frozenset(f"P{index}" for index in range(5)), "P5", "P0",
    )
    scores = {f"P{index}": 10.0 for index in range(10)}
    assert score_lineup(lineup, scores, 10.0, rules) == 100.0


def test_phase7_coach_scoring_buckets() -> None:
    cases = [
        (81, 80, False, 10), (91, 80, False, 20), (101, 80, False, 25),
        (80, 81, False, -5), (80, 91, False, -10), (80, 101, False, -20),
        (101, 99, True, 10),
    ]
    for home_score, away_score, overtime, expected in cases:
        assert coach_fantasy_score(
            home_score, away_score, home=True, overtime=overtime
        ) == expected


def test_phase7_played_bench_player_cannot_be_promoted() -> None:
    rules = load_rules()
    before = Lineup(tuple(f"P{i}" for i in range(10)), "C", frozenset(f"P{i}" for i in range(5)), "P5", "P0")
    after = replace(before, starters=frozenset({"P0", "P1", "P2", "P3", "P6"}))
    with unittest.TestCase().assertRaisesRegex(RuleViolation, "already played"):
        validate_turn_transition(before, after, {"P6"}, rules)


def test_phase7_new_captain_must_not_have_played() -> None:
    rules = load_rules()
    before = Lineup(tuple(f"P{i}" for i in range(10)), "C", frozenset(f"P{i}" for i in range(5)), "P5", "P0")
    after = replace(before, captain="P1")
    with unittest.TestCase().assertRaisesRegex(RuleViolation, "new captain"):
        validate_turn_transition(before, after, {"P0", "P1"}, rules)


def test_phase7_captain_can_switch_to_an_unplayed_starter() -> None:
    rules = load_rules()
    before = Lineup(tuple(f"P{i}" for i in range(10)), "C", frozenset(f"P{i}" for i in range(5)), "P5", "P0")
    after = replace(before, captain="P1")
    validate_turn_transition(before, after, {"P0"}, rules)


def test_phase7_marginal_sampler_is_reproducible() -> None:
    players, _ = _pools()
    first, _ = simulate_player_outcomes(players.iloc[:3], 500, seed=99)
    second, _ = simulate_player_outcomes(players.iloc[:3], 500, seed=99)
    assert np.array_equal(first, second)


def test_phase7_marginal_sampler_preserves_monotone_quantiles_and_mean() -> None:
    players, _ = _pools(); row = players.iloc[0]
    marginal = build_marginal(row)
    assert np.all(np.diff(marginal.values) >= 0)
    assert abs(marginal.implied_mean - row.expected_fp) < 1e-8


def test_phase7_dynamic_substitution_uses_expected_value_not_fixed_cutoff() -> None:
    players, _ = _pools(); rules = load_rules()
    roster = players.iloc[[0, 1, 2, 3, 8, 9, 10, 11, 16, 17]].copy()
    roster.loc[:, "turn"] = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
    ids = tuple(roster.player_id)
    initial = Lineup(ids, "C", frozenset([ids[0], ids[2], ids[4], ids[6], ids[8]]), ids[1], ids[0])
    observed = {ids[0]: -5, ids[2]: 20, ids[4]: 20, ids[6]: 20, ids[8]: 20}
    after, actions = recommend_next_turn(initial, roster, observed, 1, rules)
    assert ids[0] not in after.starters
    assert any(action["action"] == "MOVE_TO_FIELD" for action in actions)


def test_phase7_between_turn_decision_rejects_missing_realized_scores() -> None:
    players, _ = _pools(); rules = load_rules()
    roster = players.iloc[[0, 1, 2, 3, 8, 9, 10, 11, 16, 17]].copy()
    roster.loc[:, "turn"] = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
    ids = tuple(roster.player_id)
    initial = Lineup(ids, "C", frozenset([ids[0], ids[2], ids[4], ids[6], ids[8]]), ids[1], ids[0])
    with unittest.TestCase().assertRaisesRegex(ValueError, "every completed-Turn"):
        recommend_next_turn(initial, roster, {ids[0]: 10.0}, 1, rules)


def test_phase7_stochastic_optimizer_is_reproducible() -> None:
    players, coaches = _pools(); rules = load_rules()
    kwargs = dict(
        budget=100.0, seed=71, training_simulations=10, evaluation_simulations=20,
        scenario_candidates=1, alternative_mean_rosters=0, max_layouts_per_roster=2,
    )
    first = optimize_stochastic(players, coaches, rules, **kwargs)
    second = optimize_stochastic(players, coaches, rules, **kwargs)
    assert first.strategy_fingerprint == second.strategy_fingerprint
    assert np.array_equal(first.simulation.scores, second.simulation.scores)


def test_phase7_e2025_market_is_price_safe_and_round_specific() -> None:
    first = load_historical_market("E2025", 1)
    second = load_historical_market("E2025", 2)
    assert first.price_semantics.eq(
        "PRE_MATCHDAY_PRICE_VALIDATED_AGAINST_OFFICIAL_STATS"
    ).all()
    assert first.snapshot_batch_id.nunique() == 1
    assert second.snapshot_batch_id.nunique() == 1
    assert first.snapshot_batch_id.iloc[0] != second.snapshot_batch_id.iloc[0]


def test_phase7_unsafe_price_season_is_rejected() -> None:
    with unittest.TestCase().assertRaisesRegex(ValueError, "not validated"):
        load_historical_market("E2024", 1)


def test_phase7_coach_model_improves_simple_prior() -> None:
    model = train_coach_model(build_coach_feature_history())
    assert model.validation.rows == 804
    assert model.validation.improves_prior
    assert model.validation.log_loss < model.validation.prior_log_loss


def test_phase7_migration_has_oracle_isolation() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "phase7.duckdb"
        initialize_database(database)
        with connect_database(database, read_only=True) as connection:
            columns = connection.execute(
                "DESCRIBE fantasy_strategy_replay_results"
            ).df().column_name.tolist()
        assert "oracle_is_evaluation_only" in columns


def test_phase7_rules_manifest_round_trip_json() -> None:
    rules = load_rules()
    encoded = json.dumps(rules.manifest, sort_keys=True)
    assert json.loads(encoded)["ruleset_version"] == rules.ruleset_version


def test_phase7_trade_limit_retains_seven_of_eleven_entities() -> None:
    players, coaches = _pools(); rules = load_rules()
    first = optimize_deterministic(players, coaches, rules)
    selected = set(players[players.player_id.isin(first.lineup.player_ids)].entity_id)
    selected.add(coaches.loc[coaches.coach_id.eq(first.lineup.coach_id), "entity_id"].iloc[0])
    second = optimize_deterministic(
        players, coaches, rules, previous_entity_ids=selected, trade_limit=4
    )
    second_entities = set(
        players[players.player_id.isin(second.lineup.player_ids)].entity_id
    )
    second_entities.add(
        coaches.loc[coaches.coach_id.eq(second.lineup.coach_id), "entity_id"].iloc[0]
    )
    assert len(selected & second_entities) >= 7


def test_phase7_postponed_average_does_not_invent_current_outcome() -> None:
    assert postponed_average_score([10.0, 20.0, 30.0]) == 20.0
    assert postponed_average_score([]) == 0.0


def test_phase7_known_availability_scenario_excludes_only_explicit_out() -> None:
    players, _ = _pools()
    output = apply_availability_scenario(
        players.iloc[:3], {players.player_id.iloc[0]: "OUT", players.player_id.iloc[1]: "PLAY"}
    )
    assert players.player_id.iloc[0] not in set(output.player_id)
    assert players.player_id.iloc[2] in set(output.player_id)


def test_phase7_cached_replay_has_all_matchdays_and_strict_cutoffs() -> None:
    path = Path("data/derived/phase7/research/e2025_full_market_predictions.parquet")
    with duckdb.connect() as connection:
        rows, matchdays, duplicates, bad_cutoffs, dnps = connection.execute(
            """
            SELECT count(*), count(DISTINCT round_number),
                   count(*)-count(DISTINCT player_game_id),
                   count(*) FILTER(WHERE target_game_time<=prediction_cutoff),
                   count(*) FILTER(WHERE NOT played)
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
    assert (rows, matchdays, duplicates, bad_cutoffs) == (11769, 38, 0, 0)
    assert dnps == 3848


def test_phase7_oracle_is_not_a_player_prediction_feature() -> None:
    path = Path("data/derived/phase7/research/e2025_full_market_predictions.parquet")
    with duckdb.connect() as connection:
        columns = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).df().column_name.tolist()
    assert not any("oracle" in column.lower() for column in columns)


def test_phase7_historical_replay_contains_every_strategy_and_matchday() -> None:
    path = Path("data/derived/phase7/research/e2025_strategy_backtest.parquet")
    with duckdb.connect() as connection:
        rows, matchdays, strategies, nonzero_gaps = connection.execute(
            """
            SELECT count(*), count(DISTINCT matchday), count(DISTINCT strategy),
                   count(*) FILTER(WHERE coalesce(solver_gap, 0)>1e-9)
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
    assert (rows, matchdays, strategies, nonzero_gaps) == (228, 38, 6, 0)


def test_phase7_frozen_strategy_artifact_validates() -> None:
    payload = validate_phase7_artifact()
    assert payload["strategy_model_version"] == "phase7_dynamic_strategy_engine_frozen_v1"
    assert payload["predictive_layer"] == "phase6c_predictive_uplift_frozen_v1"


def test_phase7_snapshot_identity_ignores_wall_clock_time() -> None:
    with TemporaryDirectory() as directory:
        first = {"season": "E2025", "matchday": 1, "generated_at": "first"}
        second = {"season": "E2025", "matchday": 1, "generated_at": "second"}
        first_path = _write_snapshot(first, Path(directory))
        second_path = _write_snapshot(second, Path(directory))
    assert first_path == second_path
    assert first["snapshot_fingerprint"] == second["snapshot_fingerprint"]
    assert second["generated_at"] == "first"


class Phase7Tests(unittest.TestCase):
    """Expose the concise function tests to the repository's unittest runner."""


_PHASE7_TEST_FUNCTIONS = {
    name: value for name, value in tuple(globals().items())
    if name.startswith("test_phase7_") and callable(value)
}
for _test_name, _test_function in _PHASE7_TEST_FUNCTIONS.items():
    setattr(Phase7Tests, _test_name, staticmethod(_test_function))
del _test_name, _test_function, _PHASE7_TEST_FUNCTIONS
