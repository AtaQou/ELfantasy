"""DuckDB connection and forward-only schema migration helpers."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "db" / "euroleague.duckdb"
MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
_DATABASE_ANCHORS: dict[str, DuckDBPyConnection] = {}
_DATABASE_ANCHORS_LOCK = threading.Lock()


def _database_key(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve())


def hold_database_open(
    path: Path | str = DEFAULT_DATABASE_PATH,
) -> DuckDBPyConnection:
    """Keep one process-wide instance loaded and return its owning connection.

    DuckDB reloads the complete catalog after the last connection to a database
    closes.  The control center performs many short repository operations, so a
    retained owner avoids paying that catalog-loading cost for every operation.
    Callers still receive independent cursors from :func:`connect_database`.
    """

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    key = _database_key(database_path)
    with _DATABASE_ANCHORS_LOCK:
        existing = _DATABASE_ANCHORS.get(key)
        if existing is not None:
            return existing
        connection = duckdb.connect(key)
        _DATABASE_ANCHORS[key] = connection
        return connection


def release_database_anchor(path: Path | str = DEFAULT_DATABASE_PATH) -> None:
    """Close the retained process-wide database owner, if one exists."""

    with _DATABASE_ANCHORS_LOCK:
        connection = _DATABASE_ANCHORS.pop(_database_key(path), None)
    if connection is not None:
        connection.close()


def connect_database(
    path: Path | str = DEFAULT_DATABASE_PATH,
    *,
    read_only: bool = False,
) -> DuckDBPyConnection:
    """Open an explicit connection; never use DuckDB's shared global handle."""

    database_path = Path(path)
    if not read_only:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    key = _database_key(database_path)
    with _DATABASE_ANCHORS_LOCK:
        anchor = _DATABASE_ANCHORS.get(key)
        if anchor is not None:
            # A cursor is an independent DuckDB connection backed by the same
            # loaded database instance. The anchor itself remains open.
            return anchor.cursor()
    return duckdb.connect(key, read_only=read_only)


def initialize_database(
    path: Path | str = DEFAULT_DATABASE_PATH,
) -> list[str]:
    """Apply every unapplied numbered SQL migration transactionally."""

    applied_now: list[str] = []
    with connect_database(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                migration_name VARCHAR NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
            )
            """
        )
        applied = {
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        for migration in sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", maxsplit=1)[0])
            if version in applied:
                continue
            sql = migration.read_text(encoding="utf-8")
            connection.begin()
            try:
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations (version, migration_name) VALUES (?, ?)",
                    [version, migration.name],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            applied_now.append(migration.name)
    return applied_now


def database_version(path: Path | str = DEFAULT_DATABASE_PATH) -> str:
    """Return the engine version recorded by the installed DuckDB client."""

    with connect_database(path, read_only=True) as connection:
        return str(connection.execute("SELECT version()").fetchone()[0])
