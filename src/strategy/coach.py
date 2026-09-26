"""Small leakage-safe coach distribution model, isolated from player prediction."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

from src.db.database import DEFAULT_DATABASE_PATH, connect_database
from src.strategy.rules import coach_fantasy_score


COACH_SCORE_CLASSES = np.asarray([-20.0, -10.0, -5.0, 10.0, 20.0, 25.0])
COACH_FEATURES = (
    "is_home", "elo_difference", "team_last5_margin", "opponent_last5_margin",
    "team_season_win_rate", "opponent_season_win_rate", "team_games_before",
    "opponent_games_before",
)


@dataclass(frozen=True, slots=True)
class CoachValidation:
    rows: int
    log_loss: float
    prior_log_loss: float
    multiclass_brier: float
    prior_multiclass_brier: float
    expected_score_mae: float
    prior_expected_score_mae: float
    improves_prior: bool


@dataclass(slots=True)
class CoachDistributionModel:
    model: LogisticRegression
    classes: np.ndarray
    feature_names: tuple[str, ...]
    training_seasons: tuple[str, ...]
    validation: CoachValidation

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(frame[list(self.feature_names)].fillna(0.0))
        output = np.zeros((len(frame), len(COACH_SCORE_CLASSES)), dtype=float)
        class_index = {float(value): index for index, value in enumerate(COACH_SCORE_CLASSES)}
        for source_index, value in enumerate(self.model.classes_):
            output[:, class_index[float(value)]] = raw[:, source_index]
        return output


def build_coach_feature_history(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Create pre-tip Elo/rolling features and observed coach score for each team-game."""

    with connect_database(database_path, read_only=True) as connection:
        games = connection.execute(
            """
            SELECT season_code, round_number, canonical_game_id AS game_id,
                   game_date, local_game_date, home_team_id, away_team_id,
                   home_score, away_score, coalesce(overtime_count, 0) AS overtime_count
            FROM games
            WHERE played AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND competition_code='E'
            ORDER BY game_date, canonical_game_id
            """
        ).df()
    ratings: dict[tuple[str, str], float] = defaultdict(lambda: 1500.0)
    margins: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=5))
    wins: dict[tuple[str, str], list[int]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for game in games.itertuples(index=False):
        season = str(game.season_code)
        home = str(game.home_team_id); away = str(game.away_team_id)
        home_key = (season, home); away_key = (season, away)
        home_margin = int(game.home_score) - int(game.away_score)
        for team, opponent, team_key, opponent_key, is_home, margin in (
            (home, away, home_key, away_key, 1.0, home_margin),
            (away, home, away_key, home_key, 0.0, -home_margin),
        ):
            rows.append({
                "season": season,
                "round_number": int(game.round_number),
                "game_id": str(game.game_id),
                "game_time": game.game_date,
                "local_game_date": game.local_game_date,
                "team_id": team,
                "opponent_team_id": opponent,
                "is_home": is_home,
                "elo_difference": ratings[team_key] - ratings[opponent_key],
                "team_last5_margin": float(np.mean(margins[team_key])) if margins[team_key] else 0.0,
                "opponent_last5_margin": (
                    float(np.mean(margins[opponent_key])) if margins[opponent_key] else 0.0
                ),
                "team_season_win_rate": float(np.mean(wins[team_key])) if wins[team_key] else 0.5,
                "opponent_season_win_rate": (
                    float(np.mean(wins[opponent_key])) if wins[opponent_key] else 0.5
                ),
                "team_games_before": len(wins[team_key]),
                "opponent_games_before": len(wins[opponent_key]),
                "actual_coach_score": coach_fantasy_score(
                    int(game.home_score), int(game.away_score), home=bool(is_home),
                    overtime=bool(game.overtime_count),
                ),
            })
        expected_home = 1.0 / (1.0 + 10.0 ** ((ratings[away_key] - ratings[home_key] - 70.0) / 400.0))
        actual_home = 1.0 if home_margin > 0 else 0.0
        change = 20.0 * (actual_home - expected_home)
        ratings[home_key] += change; ratings[away_key] -= change
        margins[home_key].append(float(home_margin)); margins[away_key].append(float(-home_margin))
        wins[home_key].append(int(home_margin > 0)); wins[away_key].append(int(home_margin < 0))
    return pd.DataFrame(rows)


def train_coach_model(
    history: pd.DataFrame,
    *,
    validation_season: str = "E2025",
) -> CoachDistributionModel:
    training = history[history["season"].astype(str) < validation_season].copy()
    validation = history[history["season"].astype(str).eq(validation_season)].copy()
    if training.empty or validation.empty:
        raise ValueError("coach model needs pre-E2025 history and E2025 validation rows")
    model = LogisticRegression(C=0.25, max_iter=2000, random_state=202507)
    model.fit(training[list(COACH_FEATURES)], training["actual_coach_score"])
    wrapper = CoachDistributionModel(
        model=model, classes=COACH_SCORE_CLASSES, feature_names=COACH_FEATURES,
        training_seasons=tuple(sorted(training["season"].astype(str).unique())),
        validation=CoachValidation(0, 0, 0, 0, 0, 0, 0, False),
    )
    probabilities = wrapper.predict_proba(validation)
    actual = validation["actual_coach_score"].to_numpy(float)
    labels = np.searchsorted(COACH_SCORE_CLASSES, actual)
    one_hot = np.eye(len(COACH_SCORE_CLASSES))[labels]
    priors = np.asarray([
        np.mean(training["actual_coach_score"].to_numpy(float) == value)
        for value in COACH_SCORE_CLASSES
    ])
    prior_matrix = np.tile(priors, (len(validation), 1))
    expected = probabilities @ COACH_SCORE_CLASSES
    prior_expected = prior_matrix @ COACH_SCORE_CLASSES
    metrics = CoachValidation(
        rows=len(validation),
        log_loss=float(log_loss(actual, probabilities, labels=COACH_SCORE_CLASSES)),
        prior_log_loss=float(log_loss(actual, prior_matrix, labels=COACH_SCORE_CLASSES)),
        multiclass_brier=float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        prior_multiclass_brier=float(np.mean(np.sum((prior_matrix - one_hot) ** 2, axis=1))),
        expected_score_mae=float(np.mean(np.abs(expected - actual))),
        prior_expected_score_mae=float(np.mean(np.abs(prior_expected - actual))),
        improves_prior=False,
    )
    wrapper.validation = CoachValidation(
        **{**asdict(metrics), "improves_prior": (
            metrics.log_loss < metrics.prior_log_loss
            and metrics.multiclass_brier < metrics.prior_multiclass_brier
        )}
    )
    return wrapper


def coach_market_for_matchday(
    history: pd.DataFrame,
    model: CoachDistributionModel,
    season: str,
    matchday: int,
    market: pd.DataFrame,
) -> pd.DataFrame:
    features = history[
        history["season"].astype(str).eq(str(season))
        & history["round_number"].eq(int(matchday))
    ].copy()
    coaches = market[market["entity_type"].astype(str).str.upper().eq("COACH")].copy()
    coaches = coaches.merge(
        features, left_on="team_id", right_on="team_id", how="inner", validate="one_to_one",
    )
    probabilities = model.predict_proba(coaches)
    for index, value in enumerate(COACH_SCORE_CLASSES):
        coaches[f"prob_score_{int(value)}"] = probabilities[:, index]
    coaches["expected_score"] = probabilities @ COACH_SCORE_CLASSES
    coaches["actual_score"] = coaches["actual_coach_score"]
    coaches["turn"] = (
        pd.to_datetime(coaches["local_game_date"]).dt.date
        .map({value: index + 1 for index, value in enumerate(sorted(
            pd.to_datetime(coaches["local_game_date"]).dt.date.unique()
        ))})
    )
    return coaches


def coach_market_for_upcoming(
    model: CoachDistributionModel,
    season: str,
    matchday: int,
    market: pd.DataFrame,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Build the same leakage-safe features for a future live coach slate."""

    with connect_database(database_path, read_only=True) as connection:
        games = connection.execute(
            """
            SELECT season_code, round_number, canonical_game_id AS game_id,
                   game_date, local_game_date, home_team_id, away_team_id,
                   home_score, away_score, coalesce(overtime_count,0) overtime_count,
                   played
            FROM games WHERE season_code=?
            ORDER BY game_date, canonical_game_id
            """,
            [season],
        ).df()
    upcoming = games[games["round_number"].eq(int(matchday))].copy()
    if upcoming.empty:
        raise ValueError(f"no scheduled games for {season} Matchday {matchday}")
    cutoff = pd.to_datetime(upcoming["game_date"], utc=True).min()
    prior = games[
        games["played"].fillna(False)
        & (pd.to_datetime(games["game_date"], utc=True) < cutoff)
        & games["home_score"].notna() & games["away_score"].notna()
    ]
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    margins: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=5))
    wins: dict[str, list[int]] = defaultdict(list)
    for game in prior.itertuples(index=False):
        home = str(game.home_team_id); away = str(game.away_team_id)
        margin = int(game.home_score) - int(game.away_score)
        expected_home = 1.0 / (1.0 + 10.0 ** ((ratings[away] - ratings[home] - 70.0) / 400.0))
        change = 20.0 * ((1.0 if margin > 0 else 0.0) - expected_home)
        ratings[home] += change; ratings[away] -= change
        margins[home].append(float(margin)); margins[away].append(float(-margin))
        wins[home].append(int(margin > 0)); wins[away].append(int(margin < 0))
    rows: list[dict[str, Any]] = []
    for game in upcoming.itertuples(index=False):
        home = str(game.home_team_id); away = str(game.away_team_id)
        for team, opponent, is_home in ((home, away, 1.0), (away, home, 0.0)):
            rows.append({
                "team_id": team, "opponent_team_id": opponent,
                "game_id": str(game.game_id), "game_time": game.game_date,
                "local_game_date": game.local_game_date, "is_home": is_home,
                "elo_difference": ratings[team] - ratings[opponent],
                "team_last5_margin": float(np.mean(margins[team])) if margins[team] else 0.0,
                "opponent_last5_margin": (
                    float(np.mean(margins[opponent])) if margins[opponent] else 0.0
                ),
                "team_season_win_rate": float(np.mean(wins[team])) if wins[team] else 0.5,
                "opponent_season_win_rate": (
                    float(np.mean(wins[opponent])) if wins[opponent] else 0.5
                ),
                "team_games_before": len(wins[team]),
                "opponent_games_before": len(wins[opponent]),
            })
    features = pd.DataFrame(rows)
    coaches = market[market["entity_type"].astype(str).str.upper().eq("COACH")].copy()
    coaches = coaches.merge(features, on="team_id", how="inner", validate="one_to_one")
    probabilities = model.predict_proba(coaches)
    for index, value in enumerate(COACH_SCORE_CLASSES):
        coaches[f"prob_score_{int(value)}"] = probabilities[:, index]
    coaches["expected_score"] = probabilities @ COACH_SCORE_CLASSES
    coaches["actual_score"] = np.nan
    dates = sorted(pd.to_datetime(coaches["local_game_date"]).dt.date.unique())
    coaches["turn"] = pd.to_datetime(coaches["local_game_date"]).dt.date.map(
        {value: index + 1 for index, value in enumerate(dates)}
    )
    return coaches


def simulate_coach_outcomes(
    coaches: pd.DataFrame, simulations: int, *, seed: int,
) -> np.ndarray:
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    uniforms = rng.random((simulations, len(coaches)))
    output = np.empty_like(uniforms)
    for column, row in enumerate(coaches.to_dict("records")):
        probabilities = np.asarray([
            float(row[f"prob_score_{int(value)}"]) for value in COACH_SCORE_CLASSES
        ])
        output[:, column] = COACH_SCORE_CLASSES[
            np.searchsorted(np.cumsum(probabilities), uniforms[:, column], side="right")
            .clip(0, len(COACH_SCORE_CLASSES) - 1)
        ]
    return output
