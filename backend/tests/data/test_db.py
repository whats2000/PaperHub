"""Tests for the SQLite connection helper and migration runner."""

from __future__ import annotations

from pathlib import Path

from paperhub.data.db import apply_migrations, connect


def test_apply_migrations_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    table_names = {row[0] for row in rows}
    assert {
        "papers",
        "chunks",
        "projects",
        "project_papers",
        "tags",
        "notes",
        "citations",
        "chat_sessions",
        "messages",
        "runs",
        "tool_calls",
        "schema_migrations",
    }.issubset(table_names)


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    apply_migrations(db_path)  # second call must be a no-op
    with connect(db_path) as conn:
        versions = [
            r[0]
            for r in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == [1]


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
