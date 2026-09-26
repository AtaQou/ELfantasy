"""Versioned EuroLeague Fantasy Challenge rules and legality checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.db.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = (
    PROJECT_ROOT / "data" / "reference" / "fantasy_rules"
    / "elfc_classic_e2025_v1.json"
)
POSITIONS = ("GUARD", "FORWARD", "CENTER")


class RuleViolation(ValueError):
    """A roster, lineup, or between-Turn action is not legal."""


@dataclass(frozen=True, slots=True)
class FantasyRules:
    ruleset_version: str
    season_codes: tuple[str, ...]
    verification_status: str
    budget_credits: float
    player_count: int
    coach_count: int
    position_counts: Mapping[str, int]
    team_player_limit: int
    formations: tuple[tuple[int, int, int], ...]
    starter_count: int
    bench_multiplier: float
    sixth_man_multiplier: float
    captain_multiplier: float
    trade_limit: int
    unlimited_after_rounds: tuple[int, ...]
    production_gates: Mapping[str, Any]
    manifest: Mapping[str, Any]
    fingerprint: str

    def trade_limit_after(self, round_number: int) -> int | None:
        """Return None for an unlimited window, else the maximum changed entities."""

        if int(round_number) in self.unlimited_after_rounds:
            return None
        return self.trade_limit

    def assert_production_allowed(self, season_code: str) -> None:
        gate = self.production_gates.get(f"{season_code}_live")
        if gate is not True:
            reason = self.production_gates.get(
                f"{season_code}_block_reason",
                f"{season_code} has no verified production rules gate",
            )
            raise RuleViolation(str(reason))


@dataclass(frozen=True, slots=True)
class Lineup:
    player_ids: tuple[str, ...]
    coach_id: str
    starters: frozenset[str]
    sixth_man: str
    captain: str

    @property
    def bench(self) -> frozenset[str]:
        return frozenset(self.player_ids) - self.starters - {self.sixth_man}


def load_rules(path: Path | str = DEFAULT_RULES_PATH) -> FantasyRules:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    fingerprint = _json_fingerprint(payload)
    roster = payload["roster"]
    lineup = payload["lineup"]
    trades = payload["trades"]
    rules = FantasyRules(
        ruleset_version=str(payload["ruleset_version"]),
        season_codes=tuple(map(str, payload["season_codes"])),
        verification_status=str(payload["verification_status"]),
        budget_credits=float(roster["budget_credits"]),
        player_count=int(roster["players"]),
        coach_count=int(roster["coaches"]),
        position_counts={str(k): int(v) for k, v in roster["position_counts"].items()},
        team_player_limit=int(roster["team_player_limit"]),
        formations=tuple(tuple(map(int, value)) for value in lineup[
            "formations_guard_forward_center"
        ]),
        starter_count=int(lineup["starting_players"]),
        bench_multiplier=float(lineup["bench_multiplier"]),
        sixth_man_multiplier=float(lineup["sixth_man_multiplier"]),
        captain_multiplier=float(lineup["captain_multiplier"]),
        trade_limit=int(trades["regular_season_limit"]),
        unlimited_after_rounds=tuple(map(int, trades["unlimited_after_rounds"])),
        production_gates=dict(payload.get("production_gates", {})),
        manifest=payload,
        fingerprint=fingerprint,
    )
    _validate_manifest(rules)
    return rules


def register_rules_manifest(
    rules: FantasyRules,
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
) -> None:
    """Persist the immutable manifest, rejecting a conflicting version payload."""

    initialize_database(database_path)
    with connect_database(database_path) as connection:
        existing = connection.execute(
            "SELECT rules_fingerprint FROM fantasy_rules_manifests WHERE ruleset_version=?",
            [rules.ruleset_version],
        ).fetchone()
        if existing is not None and str(existing[0]) != rules.fingerprint:
            raise RuleViolation("ruleset version already exists with a different fingerprint")
        connection.execute(
            """
            INSERT INTO fantasy_rules_manifests (
                ruleset_version, verification_status, season_codes_json,
                rules_json, rules_fingerprint, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                rules.ruleset_version, rules.verification_status,
                json.dumps(rules.season_codes), json.dumps(rules.manifest),
                rules.fingerprint, rules.manifest["verified_at"],
            ],
        )


def normalize_position(value: Any) -> str:
    text = str(value or "").upper()
    if "GUARD" in text or text in {"G", "PG", "SG"}:
        return "GUARD"
    if "CENTER" in text or text in {"C"}:
        return "CENTER"
    if "FORWARD" in text or text in {"F", "SF", "PF"}:
        return "FORWARD"
    raise RuleViolation(f"unknown Fantasy position: {value!r}")


def validate_lineup(
    lineup: Lineup,
    players: Mapping[str, Mapping[str, Any]],
    coach: Mapping[str, Any],
    rules: FantasyRules,
    *,
    budget: float | None = None,
) -> None:
    ids = tuple(map(str, lineup.player_ids))
    if len(ids) != rules.player_count or len(set(ids)) != rules.player_count:
        raise RuleViolation(f"roster must contain {rules.player_count} distinct players")
    if set(ids) != set(map(str, players)):
        raise RuleViolation("lineup player ids and supplied player records differ")
    if len(lineup.starters) != rules.starter_count:
        raise RuleViolation(f"starting lineup must contain {rules.starter_count} players")
    if not lineup.starters.issubset(ids):
        raise RuleViolation("starter is not on the roster")
    if lineup.sixth_man not in ids or lineup.sixth_man in lineup.starters:
        raise RuleViolation("sixth man must be a rostered bench player")
    if lineup.captain not in lineup.starters:
        raise RuleViolation("captain must be in the starting five")
    position_totals = {position: 0 for position in POSITIONS}
    starting_totals = {position: 0 for position in POSITIONS}
    team_totals: dict[str, int] = {}
    credits = float(coach.get("credits", 0.0))
    for player_id in ids:
        row = players[player_id]
        position = normalize_position(row.get("position"))
        position_totals[position] += 1
        starting_totals[position] += int(player_id in lineup.starters)
        team_id = str(row.get("team_id"))
        team_totals[team_id] = team_totals.get(team_id, 0) + 1
        credits += float(row.get("credits", 0.0))
    if position_totals != dict(rules.position_counts):
        raise RuleViolation(
            f"position counts {position_totals} do not match {dict(rules.position_counts)}"
        )
    formation = tuple(starting_totals[position] for position in POSITIONS)
    if formation not in rules.formations:
        raise RuleViolation(f"illegal starting formation {formation}")
    if team_totals and max(team_totals.values()) > rules.team_player_limit:
        raise RuleViolation(f"more than {rules.team_player_limit} players from one club")
    ceiling = rules.budget_credits if budget is None else float(budget)
    if credits > ceiling + 1e-8:
        raise RuleViolation(f"roster costs {credits:.2f}, above budget {ceiling:.2f}")


def validate_turn_transition(
    before: Lineup,
    after: Lineup,
    played_player_ids: Iterable[str],
    rules: FantasyRules,
) -> None:
    if before.player_ids != after.player_ids or before.coach_id != after.coach_id:
        raise RuleViolation("trades are not permitted between Turns")
    if before.sixth_man != after.sixth_man:
        raise RuleViolation("sixth-man designation is frozen during the Round")
    played = set(map(str, played_player_ids))
    promoted = after.starters - before.starters
    if promoted & played:
        raise RuleViolation("a player who already played cannot move from bench to field")
    if after.captain != before.captain and after.captain in played:
        raise RuleViolation("new captain must not have played")
    if after.captain not in after.starters:
        raise RuleViolation("captain must remain in the starting five")


def score_lineup(
    lineup: Lineup,
    player_scores: Mapping[str, float],
    coach_score: float,
    rules: FantasyRules,
    *,
    captain_multiplier_allowed: bool = True,
) -> float:
    score = float(coach_score)
    for player_id in lineup.player_ids:
        actual = float(player_scores.get(player_id, 0.0))
        if player_id in lineup.starters:
            multiplier = 1.0
        elif player_id == lineup.sixth_man:
            multiplier = rules.sixth_man_multiplier
        else:
            multiplier = rules.bench_multiplier
        if captain_multiplier_allowed and player_id == lineup.captain:
            multiplier += rules.captain_multiplier - 1.0
        score += multiplier * actual
    return float(score)


def coach_fantasy_score(home_score: int, away_score: int, *, home: bool, overtime: bool) -> float:
    margin = int(home_score) - int(away_score)
    margin = margin if home else -margin
    won = margin > 0
    absolute = abs(margin)
    if overtime or absolute <= 10:
        return 10.0 if won else -5.0
    if absolute <= 20:
        return 20.0 if won else -10.0
    return 25.0 if won else -20.0


def postponed_average_score(prior_fantasy_scores: Iterable[float]) -> float:
    """Official average-score award, based only on scores before the postponed game."""

    values = [float(value) for value in prior_fantasy_scores]
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def lineup_formation(lineup: Lineup, positions: Mapping[str, str]) -> tuple[int, int, int]:
    return tuple(
        sum(normalize_position(positions[player]) == position for player in lineup.starters)
        for position in POSITIONS
    )


def _validate_manifest(rules: FantasyRules) -> None:
    if sum(rules.position_counts.values()) != rules.player_count:
        raise RuleViolation("manifest position counts do not equal player roster size")
    if any(sum(formation) != rules.starter_count for formation in rules.formations):
        raise RuleViolation("manifest contains a formation with the wrong size")
    if rules.captain_multiplier < 1 or not 0 <= rules.bench_multiplier <= 1:
        raise RuleViolation("manifest scoring multipliers are invalid")
    if not rules.manifest.get("sources"):
        raise RuleViolation("rules manifest has no source audit")


def _json_fingerprint(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
