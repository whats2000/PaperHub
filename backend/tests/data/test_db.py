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
    assert versions == [1, 2, 3]


def test_migration_0002_adds_extraction_tier_and_notes_md(tmp_path: Path) -> None:
    """Migration 0002 must add extraction_tier and notes_md columns to papers."""
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    assert "extraction_tier" in columns, (
        "papers.extraction_tier column missing after migration 0002"
    )
    assert "notes_md" in columns, "papers.notes_md column missing after migration 0002"


def test_migration_0002_backfill_sets_raw_for_existing_rows(tmp_path: Path) -> None:
    """Migration 0002 backfills pre-existing papers rows with extraction_tier='raw'."""
    import uuid
    from datetime import UTC, datetime

    db_path = tmp_path / "paperhub.db"

    # Apply only migration 0001 (no extraction_tier column yet)
    from importlib import resources

    pkg = resources.files("paperhub.data.migrations")
    migration_0001 = (pkg / "0001_initial.sql").read_text(encoding="utf-8")

    with connect(db_path) as conn:
        conn.executescript(migration_0001)
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (1)")

    # Insert a pre-migration paper row
    paper_id = str(uuid.uuid4())
    added_at = datetime.now(UTC).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, arxiv_id, doi, title, authors_json, year, abstract,"
            " pdf_path, sha256, added_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
            (
                paper_id,
                "old-arxiv-001",
                "Old Paper",
                "[]",
                None,
                None,
                "papers/old.md",
                "a" * 64,
                added_at,
            ),
        )

    # Apply migration 0002
    migration_0002 = (pkg / "0002_papers_extraction_tier.sql").read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(migration_0002)
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (2)")

    # Verify backfill
    with connect(db_path) as conn:
        row = conn.execute("SELECT extraction_tier FROM papers WHERE id=?", (paper_id,)).fetchone()
    assert row is not None
    assert row[0] == "raw", f"Expected 'raw' backfill, got: {row[0]}"


def test_migration_0003_adds_source_dir_path(tmp_path: Path) -> None:
    """Migration 0003 must add source_dir_path column to papers."""
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    assert "source_dir_path" in columns, (
        "papers.source_dir_path column missing after migration 0003"
    )


def test_apply_migrations_is_idempotent_three_migrations(tmp_path: Path) -> None:
    """Three migrations applied twice must be idempotent (versions = [1, 2, 3])."""
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    apply_migrations(db_path)
    with connect(db_path) as conn:
        versions = [
            r[0]
            for r in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == [1, 2, 3]


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
