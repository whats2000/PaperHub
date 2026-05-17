"""SQLite connection helper + forward-only migration runner.

Migrations live as NNNN_*.sql files under paperhub/data/migrations/.
Each file runs via executescript(); the runner records its version in
schema_migrations so subsequent runs are idempotent. Invoked at FastAPI
startup (Task 3) but deliberately decoupled from FastAPI so it's
callable from tests and CLI tools.

Design notes
------------
* ``isolation_level=None`` puts sqlite3 in autocommit mode. We manage
  transactions manually with explicit DDL statements where needed.
* ``executescript()`` always issues an implicit COMMIT before running and
  executes in autocommit mode — it cannot participate in an outer
  transaction. Migration SQL files are therefore self-contained scripts.
* The ``schema_migrations`` table is created by migration 0001. On a
  fresh DB the applied-versions query is protected by a try/except so
  we don't pre-create the table and conflict with the migration SQL.
* After each successful executescript() we record the version with
  INSERT OR IGNORE — "OR IGNORE" handles the edge case where the
  migration SQL itself already inserted the row.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

_MIGRATION_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with foreign-key enforcement on."""
    conn = sqlite3.connect(db_path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _list_migrations() -> list[tuple[int, str, str]]:
    """Return [(version, filename, sql_text), ...] sorted by version."""
    out: list[tuple[int, str, str]] = []
    pkg = resources.files("paperhub.data.migrations")
    for entry in pkg.iterdir():
        name = entry.name
        m = _MIGRATION_NAME_RE.match(name)
        if not m:
            continue
        out.append((int(m.group(1)), name, entry.read_text(encoding="utf-8")))
    out.sort(key=lambda t: t[0])
    return out


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of already-applied migration versions.

    On a fresh database where schema_migrations doesn't exist yet, returns
    an empty set rather than raising an error.
    """
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        # Table doesn't exist yet — no migrations have been applied.
        return set()


def apply_migrations(db_path: Path) -> None:
    """Apply every unapplied migration in order. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        already_applied = _applied_versions(conn)

        for version, _name, sql in _list_migrations():
            if version in already_applied:
                continue
            # executescript() issues an implicit COMMIT before running and
            # executes in autocommit mode. The migration SQL file is
            # responsible for all DDL; we only record the version after success.
            conn.executescript(sql)
            # Record that this version is applied. INSERT OR IGNORE is safe
            # if the migration SQL itself inserted the row (it doesn't, but
            # defensive coding is free here).
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
