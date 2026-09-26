"""Validate the Phase 3A scoring engine against already-retained official evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from src.db.database import DEFAULT_DATABASE_PATH, connect_database

from .core_features import DEFAULT_SAMPLE_ROOT
from .fantasy_scoring import TARGET_RULE_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_STATS_ROOT = PROJECT_ROOT / "data" / "raw" / "fantasy_stats"
FANTASY_SAMPLE_ROOT = PROJECT_ROOT / "data" / "samples" / "fantasy_current"


def validate_scoring_engine(
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> dict[str, Any]:
    with connect_database(database_path, read_only=True) as connection:
        pir = connection.execute(
            """
            SELECT count(*) AS rows,
                   count(*) FILTER (WHERE fantasy_base_score = pir) AS exact
            FROM (
                SELECT stat.pir,
                    stat.points + stat.total_rebounds + stat.assists
                    + stat.steals + stat.blocks + stat.fouls_drawn
                    - stat.turnovers - stat.blocks_received
                    - stat.fouls_committed
                    - (stat.two_points_attempted - stat.two_points_made)
                    - (stat.three_points_attempted - stat.three_points_made)
                    - (stat.free_throws_attempted - stat.free_throws_made)
                    AS fantasy_base_score
                FROM player_game_stats AS stat
                JOIN games AS game USING (canonical_game_id)
                WHERE game.season_code BETWEEN 'E2018' AND 'E2025'
            )
            """
        ).fetchone()
        legacy = _legacy_validation(connection)
        current_samples = _current_sample_validation(connection)
        cases = _representative_cases(connection)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target_rule_version": TARGET_RULE_VERSION,
        "canonical_base_formula_vs_pir": {
            "rows": int(pir[0]),
            "exact": int(pir[1]),
            "exact_rate": int(pir[1]) / int(pir[0]),
            "note": (
                "PIR equality is a validation result, not the target definition; "
                "the scoring engine calculates every component explicitly."
            ),
        },
        "official_legacy_pdk_validation": legacy,
        "official_e2025_detailed_samples": current_samples,
        "representative_canonical_cases": cases,
    }
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "scoring_validation.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _legacy_validation(connection: Any) -> dict[str, Any]:
    mappings = connection.execute(
        """
        SELECT crosswalk.season_code, entity.fantasy_id,
               crosswalk.canonical_player_id
        FROM fantasy_player_crosswalk AS crosswalk
        JOIN fantasy_entities AS entity USING (fantasy_entity_id)
        WHERE crosswalk.season_code IN ('E2022','E2023','E2024')
          AND crosswalk.valid_to IS NULL
          AND crosswalk.mapping_status = 'MATCHED'
          AND entity.fantasy_provider = 'dunkest_euroleague_stats_legacy'
        """
    ).fetchall()
    canonical_by_fantasy = {
        (str(season), str(fantasy_id)): str(player_id)
        for season, fantasy_id, player_id in mappings
    }
    games = connection.execute(
        """
        SELECT game.season_code, game.round_number, stat.canonical_player_id,
               stat.pir,
               CASE
                   WHEN stat.canonical_team_id = game.home_team_id
                       THEN game.home_score > game.away_score
                   ELSE game.away_score > game.home_score
               END AS won
        FROM player_game_stats AS stat
        JOIN games AS game USING (canonical_game_id)
        WHERE game.season_code IN ('E2022','E2023','E2024')
          AND game.played
        """
    ).fetchall()
    canonical: dict[tuple[str, int, str], list[tuple[int, bool]]] = defaultdict(list)
    for season, round_number, player_id, pir, won in games:
        canonical[(str(season), int(round_number), str(player_id))].append(
            (int(pir), bool(won))
        )
    result: dict[str, Any] = {}
    for season in ("E2022", "E2023", "E2024"):
        counts: Counter[str] = Counter()
        differences: Counter[str] = Counter()
        for path in sorted(
            (RAW_STATS_ROOT / season).glob(
                "matchday_*/all_players_window_*.json"
            )
        ):
            matchday = int(path.parent.name.split("_")[1])
            for row in json.loads(path.read_text(encoding="utf-8")):
                counts["official_rows"] += 1
                base = _legacy_base_score(row)
                official = Decimal(str(row["pdk"]))
                win_score = base + abs(base) * Decimal("0.1")
                if official in {base, win_score}:
                    counts["official_component_formula_exact"] += 1
                player_id = canonical_by_fantasy.get((season, str(row["id"])))
                candidates = canonical.get((season, matchday, player_id or ""), [])
                if len(candidates) != 1:
                    counts["not_uniquely_canonical_mapped"] += 1
                    continue
                pir, won = candidates[0]
                canonical_score = Decimal(pir) + (
                    abs(Decimal(pir)) * Decimal("0.1") if won else Decimal("0")
                )
                counts["canonical_compared"] += 1
                if canonical_score == official:
                    counts["canonical_exact"] += 1
                else:
                    differences[str(official - canonical_score)] += 1
        result[season] = {
            **dict(counts),
            "official_component_formula_exact_rate": (
                counts["official_component_formula_exact"]
                / counts["official_rows"]
            ),
            "canonical_exact_rate": (
                counts["canonical_exact"] / counts["canonical_compared"]
                if counts["canonical_compared"]
                else None
            ),
            "canonical_difference_counts": dict(sorted(differences.items())),
            "discrepancy_interpretation": (
                "The retained Fantasy response always obeys the formula using its "
                "own component values. Small cross-feed differences reflect official "
                "box-score corrections/provider snapshots and are not force-adjusted."
            ),
        }
    return result


def _current_sample_validation(connection: Any) -> dict[str, Any]:
    manifest = json.loads(
        (FANTASY_SAMPLE_ROOT / "discovery_manifest.json").read_text(encoding="utf-8")
    )
    fantasy_id = str(
        manifest["single_player_fantasy_points_probe"]["fantasy_player_id"]
    )
    player = connection.execute(
        """
        SELECT crosswalk.canonical_player_id
        FROM fantasy_entities AS entity
        JOIN fantasy_player_crosswalk AS crosswalk USING (fantasy_entity_id)
        WHERE entity.fantasy_provider = 'fantaking_euroleague'
          AND entity.fantasy_id = ? AND crosswalk.season_code = 'E2025'
          AND crosswalk.valid_to IS NULL AND crosswalk.mapping_status = 'MATCHED'
        """,
        [fantasy_id],
    ).fetchone()
    if player is None:
        raise ValueError("Retained E2025 scoring sample is not confidently mapped")
    files = {
        1: "player_fantasy_points_matchday_1.sanitized.json",
        38: "player_fantasy_points_sample.sanitized.json",
    }
    rows: list[dict[str, Any]] = []
    for matchday, filename in files.items():
        official = json.loads(
            (FANTASY_SAMPLE_ROOT / filename).read_text(encoding="utf-8")
        )["data"]["fantasy_pts"]
        canonical = connection.execute(
            """
            SELECT target.standardized_fantasy_points
            FROM ml_player_game_targets_v1 AS target
            WHERE target.season = 'E2025' AND target.round_number = ?
              AND target.player_id = ? AND target.eligibility_status = 'PLAYED'
            """,
            [matchday, str(player[0])],
        ).fetchone()
        if canonical is None:
            raise ValueError("Canonical E2025 scoring sample is missing")
        rows.append(
            {
                "matchday": matchday,
                "fantasy_id": fantasy_id,
                "official_fantasy_points": float(official),
                "calculated_fantasy_points": float(canonical[0]),
                "exact": Decimal(str(official)) == Decimal(str(canonical[0])),
            }
        )
    return {
        "rows": rows,
        "exact": sum(bool(row["exact"]) for row in rows),
        "compared": len(rows),
        "exact_rate": sum(bool(row["exact"]) for row in rows) / len(rows),
    }


def _representative_cases(connection: Any) -> list[dict[str, Any]]:
    criteria = {
        "high_score": "standardized_fantasy_points = (SELECT max(standardized_fantasy_points) FROM ml_player_game_targets_v1)",
        "low_score": "standardized_fantasy_points = (SELECT min(standardized_fantasy_points) FROM ml_player_game_targets_v1 WHERE eligibility_status='PLAYED')",
        "turnovers": "turnovers >= 7",
        "blocks": "blocks >= 5",
        "steals": "steals >= 5",
        "fouls": "fouls_committed >= 5 AND fouls_drawn >= 5",
        "overtime": "game_id IN (SELECT canonical_game_id FROM games WHERE overtime_count > 0)",
        "bench": "target_started = false",
    }
    rows: list[dict[str, Any]] = []
    for label, condition in criteria.items():
        row = connection.execute(
            f"""
            SELECT season, round_number, game_id, player_id,
                   fantasy_base_score, fantasy_team_win_bonus,
                   standardized_fantasy_points, target_started
            FROM ml_player_game_targets_v1
            WHERE eligibility_status = 'PLAYED' AND {condition}
            ORDER BY target_game_time, game_id, player_id LIMIT 1
            """
        ).fetchone()
        if row:
            rows.append(
                {
                    "case": label,
                    "season": row[0],
                    "round_number": int(row[1]),
                    "game_id": row[2],
                    "player_id": row[3],
                    "base_score": float(row[4]),
                    "win_bonus": float(row[5]),
                    "fantasy_points": float(row[6]),
                    "started": bool(row[7]),
                }
            )
    return rows


def _legacy_base_score(row: dict[str, Any]) -> Decimal:
    def value(key: str) -> Decimal:
        return Decimal(str(row.get(key) or 0))

    return (
        value("pts")
        + value("reb")
        + value("ast")
        + value("stl")
        + value("blk")
        + value("fouls_received")
        - value("tov")
        - value("blka")
        - value("pf")
        - (value("fga") - value("fgm"))
        - (value("fta") - value("ftm"))
    )

