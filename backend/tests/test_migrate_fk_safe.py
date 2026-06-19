"""FK-safe table-rebuild regression tests (FR-15, A4 fix).

These tests guard against the CRITICAL data-loss bug where the SQLite
table-rebuilds in ``apply_schema`` ran ``DROP TABLE <parent>`` while the
connection had ``PRAGMA foreign_keys = ON``. On SQLite, dropping a parent
table performs an implicit DELETE of its rows, which FIRES the foreign-key
actions on child tables — so the ``runs`` rebuild cascade-DELETED every
``tool_calls`` row (ON DELETE CASCADE) and NULLed ``messages.run_id`` /
``decks.run_id`` (ON DELETE SET NULL), and the ``decks`` rebuild would
cascade-delete ``deck_slides`` (ON DELETE CASCADE).

The fix routes all four rebuilds through ``_fk_safe_rebuild``, which toggles
``PRAGMA foreign_keys = OFF`` (outside the transaction — it is a silent no-op
inside one) around the BEGIN/DROP/RENAME/COMMIT and runs
``PRAGMA foreign_key_check`` afterwards. Without the fix, assertions on the
survival of ``tool_calls`` / ``messages.run_id`` / ``decks`` / ``deck_slides``
fail — that RED is the point of this test.
"""
from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.migrate import apply_schema


async def _build_legacy_db(db_path: Path) -> None:
    """Create a populated PRE-A4 legacy DB.

    The legacy ``runs.status`` CHECK lacks ``'interrupted'`` so the A4
    rebuild fires; the child tables (``tool_calls`` CASCADE, ``messages`` /
    ``decks`` SET NULL, ``deck_slides`` CASCADE) carry the same FK actions
    as the live schema. Seeds enough rows in every child that a cascade /
    SET-NULL would be observable.
    """
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")

        await conn.execute(
            "CREATE TABLE chat_sessions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " title TEXT NOT NULL DEFAULT 'New chat',"
            " deleted_at TEXT,"
            " forked_from_session_id INTEGER"
            "   REFERENCES chat_sessions(id) ON DELETE SET NULL)"
        )
        # Legacy runs: CHECK WITHOUT 'interrupted' (the pre-A4 constraint).
        await conn.execute(
            "CREATE TABLE runs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id INTEGER NOT NULL"
            "   REFERENCES chat_sessions(id) ON DELETE CASCADE,"
            " routing_decision_json TEXT,"
            " search_results_json TEXT,"
            " deck_version_id TEXT,"
            " started_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " finished_at TEXT,"
            " status TEXT NOT NULL DEFAULT 'running'"
            "   CHECK (status IN ('running','ok','error','cancelled')))"
        )
        await conn.execute(
            "CREATE TABLE messages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id INTEGER NOT NULL"
            "   REFERENCES chat_sessions(id) ON DELETE CASCADE,"
            " role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),"
            " content TEXT NOT NULL,"
            " run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        await conn.execute(
            "CREATE TABLE tool_calls ("
            " run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,"
            " branch TEXT NOT NULL DEFAULT '',"
            " step_index INTEGER NOT NULL,"
            " parent_step INTEGER,"
            " agent TEXT NOT NULL,"
            " tool TEXT NOT NULL,"
            " model TEXT,"
            " args_redacted_json TEXT,"
            " result_summary_json TEXT,"
            " latency_ms INTEGER NOT NULL,"
            " token_in INTEGER,"
            " token_out INTEGER,"
            " status TEXT NOT NULL CHECK (status IN ('ok','error','rejected')),"
            " error TEXT,"
            " PRIMARY KEY (run_id, branch, step_index))"
        )
        # Legacy decks: still has the old `theme` column so the decks rebuild
        # ALSO fires (theme present OR current_version_id missing). deck_slides
        # is its ON DELETE CASCADE child.
        await conn.execute(
            "CREATE TABLE decks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " session_id INTEGER NOT NULL"
            "   REFERENCES chat_sessions(id) ON DELETE CASCADE,"
            " run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,"
            " tex_path TEXT NOT NULL,"
            " pdf_path TEXT,"
            " speaker_notes_json TEXT,"
            " plan_json TEXT,"
            " page_count INTEGER NOT NULL DEFAULT 0,"
            " theme TEXT NOT NULL DEFAULT 'metropolis',"
            " contributing_paper_ids_json TEXT NOT NULL DEFAULT '[]',"
            " status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
            " UNIQUE (session_id))"
        )
        await conn.execute(
            "CREATE TABLE deck_slides ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,"
            " slide_index INTEGER NOT NULL,"
            " frame_tex TEXT NOT NULL,"
            " note_text TEXT,"
            " note_language TEXT,"
            " page_start INTEGER NOT NULL,"
            " page_end INTEGER NOT NULL,"
            " UNIQUE (deck_id, slide_index))"
        )

        # Seed: one session, one run, several tool_calls, messages w/ run_id,
        # a deck with run_id, and a deck_slide.
        await conn.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (1, 's')"
        )
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) VALUES (10, 1, 'ok')"
        )
        for i in range(3):
            await conn.execute(
                "INSERT INTO tool_calls "
                "(run_id, branch, step_index, agent, tool, latency_ms, status) "
                "VALUES (10, '', ?, 'research', 'paper_qa', 5, 'ok')",
                (i,),
            )
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, run_id) "
            "VALUES (100, 1, 'assistant', 'hi', 10)"
        )
        await conn.execute(
            "INSERT INTO decks (id, session_id, run_id, tex_path, theme) "
            "VALUES (50, 1, 10, '/tmp/d.tex', 'metropolis')"
        )
        await conn.execute(
            "INSERT INTO deck_slides "
            "(id, deck_id, slide_index, frame_tex, page_start, page_end) "
            "VALUES (500, 50, 0, '\\begin{frame}\\end{frame}', 1, 1)"
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_runs_rebuild_does_not_cascade_delete_children(
    tmp_path: Path,
) -> None:
    """apply_schema on a populated legacy DB must widen the runs CHECK to
    include 'interrupted' WITHOUT cascade-deleting tool_calls or NULLing
    messages.run_id / decks.run_id, and without dropping deck_slides.

    Without the FK-safe fix the parent DROPs fire cascades and the survival
    assertions below fail — that is the regression this test catches.
    """
    db_path = tmp_path / "legacy.db"
    await _build_legacy_db(db_path)

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")  # as the app opens it
        await apply_schema(conn)

        # 1. runs CHECK now allows 'interrupted' — an INSERT of it succeeds.
        await conn.execute(
            "INSERT INTO runs (id, session_id, status) "
            "VALUES (11, 1, 'interrupted')"
        )
        await conn.commit()
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
        ) as cur:
            runs_ddl = (await cur.fetchone())[0]
        assert "interrupted" in runs_ddl

        # 2. tool_calls rows SURVIVE (the CASCADE child of runs).
        async with conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = 10"
        ) as cur:
            assert (await cur.fetchone())[0] == 3

        # 3. messages.run_id PRESERVED (the SET NULL child of runs).
        async with conn.execute(
            "SELECT run_id FROM messages WHERE id = 100"
        ) as cur:
            assert (await cur.fetchone())[0] == 10

        # 4. decks + deck_slides survive with links intact.
        async with conn.execute(
            "SELECT run_id FROM decks WHERE id = 50"
        ) as cur:
            assert (await cur.fetchone())[0] == 10
        async with conn.execute(
            "SELECT COUNT(*) FROM deck_slides WHERE deck_id = 50"
        ) as cur:
            assert (await cur.fetchone())[0] == 1

        # 5. The DB has no dangling foreign-key references.
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []

        # FK enforcement is restored (it was OFF only inside the helper).
        async with conn.execute("PRAGMA foreign_keys") as cur:
            assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_fk_safe_rebuild_is_idempotent_on_migrated_db(
    tmp_path: Path,
) -> None:
    """A second apply_schema on the already-migrated DB is a no-op (guards
    false → helper not called) and preserves the seeded children."""
    db_path = tmp_path / "legacy.db"
    await _build_legacy_db(db_path)

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await apply_schema(conn)  # idempotent — must not raise or wipe data

        async with conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = 10"
        ) as cur:
            assert (await cur.fetchone())[0] == 3
        async with conn.execute(
            "SELECT run_id FROM messages WHERE id = 100"
        ) as cur:
            assert (await cur.fetchone())[0] == 10
        async with conn.execute(
            "SELECT COUNT(*) FROM deck_slides WHERE deck_id = 50"
        ) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
