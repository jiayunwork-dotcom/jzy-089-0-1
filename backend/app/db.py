"""Postgres connection management (psycopg 3, synchronous)."""
from __future__ import annotations

import atexit

import psycopg
from psycopg.rows import dict_row

from .config import settings

_pool: psycopg.Connection | None = None


def get_connection() -> psycopg.Connection:
    """Return a single shared connection.

    The workload is read-only analytical queries from one API process; a
    shared connection with its own lock is sufficient and keeps the
    deployment simple. The database role itself has no write privileges.
    """
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg.connect(
            settings.database_url,
            row_factory=dict_row,
            autocommit=True,
            connect_timeout=5,
        )
        _pool.read_only = True
    return _pool


@atexit.register
def _close() -> None:
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.close()
