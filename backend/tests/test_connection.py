"""Tests for DB connection configuration (WAL + busy_timeout).

A long-lived background Marker worker writes concurrently with request
handlers (each on its own connection). Without WAL + a busy_timeout,
concurrent writers raise `sqlite3.OperationalError: database is locked`.
These tests pin the centralized connection PRAGMAs.
"""
from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.connection import configure_connection, open_db


@pytest.mark.asyncio
async def test_open_db_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    """open_db must yield a WAL-mode connection with a non-zero busy_timeout."""
    db_path = tmp_path / "wal.db"
    async with open_db(db_path) as conn:
        async with conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"

        async with conn.execute("PRAGMA busy_timeout") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] > 0


@pytest.mark.asyncio
async def test_configure_connection_sets_pragmas(tmp_path: Path) -> None:
    """configure_connection applies foreign_keys + WAL + busy_timeout."""
    db_path = tmp_path / "cfg.db"
    async with aiosqlite.connect(db_path) as conn:
        await configure_connection(conn)

        async with conn.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1

        async with conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"

        async with conn.execute("PRAGMA busy_timeout") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] > 0
