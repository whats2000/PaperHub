"""F4.5 schema migration tests.

These tests verify the v2.25 / F4.5 migration:
  - decks.theme dropped, decks.current_version_id added (table rebuild)
  - slide_style_overrides created with CHECK(source IN (...)) enum constraint

The migration entry point in this codebase is ``apply_schema`` (it both
creates fresh tables via schema.sql AND runs idempotent ALTER/rebuild
blocks on pre-existing DBs).
"""

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema


@pytest.mark.asyncio
async def test_decks_theme_dropped_and_current_version_id_added(tmp_path):
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        # Simulate a pre-F4.5 DB: create decks with the old `theme` column.
        await conn.execute(
            "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, "
            "created_at TEXT, title TEXT)"
        )
        await conn.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, session_id INTEGER, "
            "routing_decision_json TEXT, started_at TEXT, finished_at TEXT, "
            "status TEXT)"
        )
        await conn.execute(
            """CREATE TABLE decks (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
                run_id INTEGER,
                tex_path TEXT NOT NULL,
                pdf_path TEXT,
                speaker_notes_json TEXT,
                plan_json TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                theme TEXT NOT NULL DEFAULT 'metropolis',
                contributing_paper_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'ok',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (session_id)
            )"""
        )
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (1, datetime('now'), 't')"
        )
        await conn.execute(
            "INSERT INTO decks (session_id, tex_path, theme) "
            "VALUES (1, '/tmp/d.tex', 'metropolis')"
        )
        await conn.commit()

        await apply_schema(conn)

        # After F4.5 migration: theme column gone; current_version_id present (NULL).
        async with conn.execute("PRAGMA table_info(decks)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        assert "theme" not in cols, "decks.theme must be dropped by F4.5 migration"
        assert (
            "current_version_id" in cols
        ), "decks.current_version_id must be added"

        # Existing row survives.
        async with conn.execute(
            "SELECT session_id, current_version_id FROM decks WHERE session_id = 1"
        ) as cur:
            row = await cur.fetchone()
        assert row == (1, None)


@pytest.mark.asyncio
async def test_slide_style_overrides_table_created(tmp_path):
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")

        await apply_schema(conn)

        async with conn.execute(
            "PRAGMA table_info(slide_style_overrides)"
        ) as cur:
            cols = [row[1] for row in await cur.fetchall()]
        assert cols == [
            "session_id",
            "preamble_tex",
            "source",
            "created_at",
            "updated_at",
        ]

        # Seed chat_sessions rows OUTSIDE the raises block so only the bogus
        # INSERT is exercised inside it.
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (1, datetime('now'), 't')"
        )
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (2, datetime('now'), 't2')"
        )
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (3, datetime('now'), 't3')"
        )
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (4, datetime('now'), 't4')"
        )
        # Positive acceptance — every allowed enum value succeeds.
        await conn.execute(
            "INSERT INTO slide_style_overrides (session_id, preamble_tex, source) "
            "VALUES (1, '\\\\usetheme{Madrid}', 'user_request')"
        )
        await conn.execute(
            "INSERT INTO slide_style_overrides (session_id, preamble_tex, source) "
            "VALUES (2, '\\\\usetheme{Madrid}', 'agent_inferred')"
        )
        await conn.execute(
            "INSERT INTO slide_style_overrides (session_id, preamble_tex, source) "
            "VALUES (3, '\\\\usetheme{Madrid}', 'global_memory_projection')"
        )
        await conn.commit()

        # CHECK constraint on source rejects any value outside the enum.
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO slide_style_overrides "
                "(session_id, preamble_tex, source) VALUES (4, 'x', 'bogus')"
            )
            await conn.commit()

        # Confirm the three accepted rows actually landed.
        async with conn.execute(
            "SELECT session_id, source FROM slide_style_overrides ORDER BY session_id"
        ) as cur:
            rows = await cur.fetchall()
        assert rows == [
            (1, "user_request"),
            (2, "agent_inferred"),
            (3, "global_memory_projection"),
        ]


@pytest.mark.asyncio
async def test_f4_5_migration_idempotent(tmp_path):
    """Running apply_schema twice on a freshly migrated DB must not raise,
    must leave the schema unchanged, AND must preserve existing data."""
    db = tmp_path / "test.db"
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)

        # Seed a deck row between the two migrations to verify data preservation.
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, title) "
            "VALUES (1, datetime('now'), 't')"
        )
        await conn.execute(
            "INSERT INTO decks (session_id, tex_path, current_version_id) "
            "VALUES (1, '/tmp/d.tex', 'v-abc123')"
        )
        await conn.commit()

        # Second run must be a no-op (no `theme` to drop, table already there).
        await apply_schema(conn)

        async with conn.execute("PRAGMA table_info(decks)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        assert "theme" not in cols
        assert "current_version_id" in cols

        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='slide_style_overrides'"
        ) as cur:
            assert await cur.fetchone() is not None

        # The seeded deck row must survive the second migration unchanged.
        async with conn.execute(
            "SELECT session_id, tex_path, current_version_id "
            "FROM decks WHERE session_id = 1"
        ) as cur:
            row = await cur.fetchone()
        assert row == (1, "/tmp/d.tex", "v-abc123")
