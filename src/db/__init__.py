"""Canonical DuckDB layer for EuroLeague source data."""

from .database import DEFAULT_DATABASE_PATH, connect_database, initialize_database

__all__ = ["DEFAULT_DATABASE_PATH", "connect_database", "initialize_database"]
