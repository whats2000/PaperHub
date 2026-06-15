from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from paperhub.db.fork import fork_session
from paperhub.db.migrate import apply_schema, purge_deleted_sessions


async def _seed_paper_content(conn: aiosqlite.Connection, *, title: str) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, source_path, source_dir_path, html_path) "
        "VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x/h.html')",
        (f"arxiv:{title}", title, title),
    )
    await conn.commit()
    return int(cur.lastrowid)


async def _turn(conn: aiosqlite.Connection, sid: int, user: str, asst: str,
                *, routing: str | None = None, cards: str | None = None) -> int:
    """Create one run + a user message + an assistant message. Returns run_id."""
    cur = await conn.execute(
        "INSERT INTO runs (session_id, routing_decision_json, search_results_json, "
        "status) VALUES (?, ?, ?, 'ok')",
        (sid, routing, cards),
    )
    run_id = int(cur.lastrowid)
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'user', ?, ?)", (sid, user, run_id))
    await conn.execute(
        "INSERT INTO messages (session_id, role, content, run_id) "
        "VALUES (?, 'assistant', ?, ?)", (sid, asst, run_id))
    await conn.commit()
    return run_id


async def test_fork_copies_only_slice_above_point(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "first q", "first a", routing='{"intent":"chitchat"}')
        r2 = await _turn(conn, 1, "second q", "second a")  # <- fork point
        await _turn(conn, 1, "third q", "third a")

        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2,
            workspace_dir=tmp_path,
        )

        assert res.new_session_id != 1
        assert res.forked_message == "second q"
        async with conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?", (res.new_session_id,)
        ) as cur:
            assert (await cur.fetchone())[0] == "Fork of Orig"

        async with conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (res.new_session_id,),
        ) as cur:
            rows = await cur.fetchall()
        assert rows == [("user", "first q"), ("assistant", "first a")]

        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 1") as cur:
            assert (await cur.fetchone())[0] == 6

        async with conn.execute(
            "SELECT DISTINCT run_id FROM messages WHERE session_id = ?",
            (res.new_session_id,),
        ) as cur:
            new_run_ids = {r[0] for r in await cur.fetchall()}
        assert new_run_ids and r1 not in new_run_ids
        async with conn.execute(
            "SELECT routing_decision_json FROM runs WHERE id = ?",
            (next(iter(new_run_ids)),),
        ) as cur:
            assert (await cur.fetchone())[0] == '{"intent":"chitchat"}'


async def test_fork_records_lineage_to_source(tmp_path: Path) -> None:
    """The fork row records forked_from_session_id = the source session, so the
    sidebar can group it under its parent (the title is unreliable post-send)."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "q", "a")
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r1, workspace_dir=tmp_path)
        async with conn.execute(
            "SELECT forked_from_session_id FROM chat_sessions WHERE id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 1


async def test_fork_lineage_nulled_when_source_purged(tmp_path: Path) -> None:
    """ON DELETE SET NULL: hard-deleting the parent orphans the fork's lineage
    pointer (it falls back to a top-level row) instead of cascading."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        await _turn(conn, 1, "q", "a")
        r2 = await _turn(conn, 1, "q2", "a2")
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)
        await conn.execute("DELETE FROM chat_sessions WHERE id = 1")
        await conn.commit()
        async with conn.execute(
            "SELECT forked_from_session_id FROM chat_sessions WHERE id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] is None


async def test_fork_copies_papers_and_session_memories(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        pc1 = await _seed_paper_content(conn, title="P1")
        pc2 = await _seed_paper_content(conn, title="P2")
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, ?, 1)",
            (pc1,))
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, ?, 0)",
            (pc2,))
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content) "
            "VALUES ('session', 1, 'reply in Japanese')")
        await conn.execute(
            "INSERT INTO memories (scope, session_id, content, status) "
            "VALUES ('session', 1, 'stale', 'superseded')")
        r1 = await _turn(conn, 1, "q", "a")
        await conn.commit()

        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r1, workspace_dir=tmp_path)

        async with conn.execute(
            "SELECT paper_content_id, enabled FROM papers WHERE session_id = ? "
            "ORDER BY paper_content_id", (res.new_session_id,)) as cur:
            assert await cur.fetchall() == [(pc1, 1), (pc2, 0)]

        async with conn.execute(
            "SELECT content, scope, session_id, supersedes, superseded_by "
            "FROM memories WHERE session_id = ?", (res.new_session_id,)) as cur:
            mem = await cur.fetchall()
        assert mem == [("reply in Japanese", "session", res.new_session_id, None, None)]


async def test_fork_first_message_yields_empty_history(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "only q", "only a")

        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r1, workspace_dir=tmp_path)

        assert res.forked_message == "only q"
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 0


async def _seed_deck(conn, *, session_id, run_id, slides_dir: Path) -> int:
    slides_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    (slides_dir / "deck.tex").write_text("\\documentclass{beamer}", encoding="utf-8")
    (slides_dir / "deck.pdf").write_bytes(b"%PDF-1.5 fake")
    (slides_dir / "edit_history").mkdir(exist_ok=True)
    (slides_dir / "edit_history" / "version_x.json").write_text("{}", encoding="utf-8")
    cur = await conn.execute(
        "INSERT INTO decks (session_id, run_id, tex_path, pdf_path, page_count, "
        "current_version_id, status) VALUES (?, ?, ?, ?, 2, 'version_x', 'ok')",
        (session_id, run_id, str(slides_dir / "deck.tex"), str(slides_dir / "deck.pdf")),
    )
    deck_id = int(cur.lastrowid)
    for i in range(2):
        await conn.execute(
            "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, page_start, "
            "page_end) VALUES (?, ?, ?, ?, ?)",
            (deck_id, i, f"\\begin{{frame}}{{S{i}}}\\end{{frame}}", i + 1, i + 1))
    # Stamp the deck-producing run so the fork guard sees a deck existed AS OF
    # this run (the guard only carries a deck if some run < fork_run_id did so).
    await conn.execute(
        "UPDATE runs SET deck_version_id = 'version_x' WHERE id = ?", (run_id,))
    await conn.commit()
    return deck_id


async def test_fork_copies_deck_with_rewritten_paths(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "make slides", "done", routing='{"intent":"slides"}')
        src_slides = tmp_path / "chat_session" / "1" / "slides"
        await _seed_deck(conn, session_id=1, run_id=r1, slides_dir=src_slides)
        r2 = await _turn(conn, 1, "next", "ok")

        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)

        async with conn.execute(
            "SELECT tex_path, pdf_path, page_count, current_version_id "
            "FROM decks WHERE session_id = ?", (res.new_session_id,)) as cur:
            drow = await cur.fetchone()
        assert drow is not None
        fork_slides = tmp_path / "chat_session" / str(res.new_session_id) / "slides"
        assert drow[0] == str(fork_slides / "deck.tex")
        assert drow[1] == str(fork_slides / "deck.pdf")
        assert drow[2] == 2 and drow[3] == "version_x"
        async with conn.execute(
            "SELECT COUNT(*) FROM deck_slides d JOIN decks k ON k.id = d.deck_id "
            "WHERE k.session_id = ?", (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 2
        assert (fork_slides / "deck.tex").exists()
        assert (fork_slides / "edit_history" / "version_x.json").exists()


async def test_fork_deck_artifact_failure_yields_deckless_fork(
    tmp_path: Path, monkeypatch
) -> None:
    """If copying the slides dir fails, the fork still succeeds WITHOUT a deck."""
    import paperhub.db.fork as fork_mod

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fork_mod.shutil, "copytree", _boom)

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "make slides", "done")
        src_slides = tmp_path / "chat_session" / "1" / "slides"
        await _seed_deck(conn, session_id=1, run_id=r1, slides_dir=src_slides)
        r2 = await _turn(conn, 1, "next", "ok")

        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)

        async with conn.execute(
            "SELECT COUNT(*) FROM decks WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 0
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 2


async def test_fork_above_deck_run_is_deckless(tmp_path: Path) -> None:
    """Forking AT/ABOVE the turn that generated the deck must NOT carry it.

    The deck is a "future" artifact relative to the fork point — the branch
    never had it. Guard: copy only when a run STRICTLY BEFORE fork_run_id
    stamped runs.deck_version_id. Here the deck is produced by r2 and we fork
    at r2, so the fork is deckless even though a decks row exists for the source.
    """
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
        await conn.commit()
        r1 = await _turn(conn, 1, "hello", "hi")  # a plain turn, no deck
        r2 = await _turn(conn, 1, "make slides", "done",
                         routing='{"intent":"slides"}')
        src_slides = tmp_path / "chat_session" / "1" / "slides"
        # The deck is produced BY r2 (the fork point) — not before it.
        await _seed_deck(conn, session_id=1, run_id=r2, slides_dir=src_slides)

        # Fork AT r2 (the deck-producing turn): the branch is above the deck.
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)

        async with conn.execute(
            "SELECT COUNT(*) FROM decks WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 0, "fork must not carry future deck"
        # The fork still copies the slice strictly above r2 (i.e. r1's messages).
        assert r1 is not None


# ---------------------------------------------------------------------------
# Deletion side-effects: the fork is deliberately INDEPENDENT of the original.
# Everything except paper_content is copied (its own messages/runs/decks/
# deck_slides rows + its own slides/ artifact folder); paper_content is shared
# but RESTRICT-protected, so it survives as long as either session references
# it. Deleting one session must never break the other.
# ---------------------------------------------------------------------------


async def _seed_source_with_paper_and_deck(
    conn: aiosqlite.Connection, tmp_path: Path
) -> tuple[int, int, Path]:
    """Source session 1 with a shared paper + a deck (run r1) + a later turn r2.
    Returns (pc1, r2, src_slides)."""
    await conn.execute("INSERT INTO chat_sessions (title) VALUES ('Orig')")
    await conn.commit()
    pc1 = await _seed_paper_content(conn, title="P1")
    await conn.execute(
        "INSERT INTO papers (session_id, paper_content_id, enabled) VALUES (1, ?, 1)",
        (pc1,))
    r1 = await _turn(conn, 1, "make slides", "done")
    src_slides = tmp_path / "chat_session" / "1" / "slides"
    await _seed_deck(conn, session_id=1, run_id=r1, slides_dir=src_slides)
    r2 = await _turn(conn, 1, "next", "ok")
    return pc1, r2, src_slides


async def test_fork_survives_source_hard_delete(tmp_path: Path) -> None:
    """Hard-deleting the ORIGINAL session (FK cascade) leaves the fork's copied
    rows, its slides dir, and the shared paper_content untouched."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        pc1, r2, _ = await _seed_source_with_paper_and_deck(conn, tmp_path)
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)
        fork_slides = tmp_path / "chat_session" / str(res.new_session_id) / "slides"
        assert (fork_slides / "deck.tex").exists()

        # Hard-delete the original (cascade papers/messages/runs/decks/deck_slides).
        await conn.execute("DELETE FROM chat_sessions WHERE id = 1")
        await conn.commit()

        # Fork session + every copied row survives.
        async with conn.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 2  # r1 user+assistant
        async with conn.execute(
            "SELECT paper_content_id FROM papers WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == pc1
        async with conn.execute(
            "SELECT COUNT(*) FROM deck_slides d JOIN decks k ON k.id = d.deck_id "
            "WHERE k.session_id = ?", (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 2
        # Shared paper_content survives (the fork still references it).
        async with conn.execute(
            "SELECT COUNT(*) FROM paper_content WHERE id = ?", (pc1,)) as cur:
            assert (await cur.fetchone())[0] == 1
        # The fork's OWN deck artifacts on disk are untouched.
        assert (fork_slides / "deck.tex").exists()


async def test_fork_artifacts_survive_source_purge(tmp_path: Path) -> None:
    """purge_deleted_sessions removes the ORIGINAL's chat_session/<id>/ folder;
    the fork's own folder + deck rows must survive (this is exactly why the deck
    artifacts are COPIED into the fork's dir, not shared)."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        pc1, r2, src_slides = await _seed_source_with_paper_and_deck(conn, tmp_path)
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)
        fork_slides = tmp_path / "chat_session" / str(res.new_session_id) / "slides"

        # Tombstone the original in the past (older than the retention window),
        # then purge — the folder-removal path runs for the original only.
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now', '-2 days') "
            "WHERE id = 1")
        await conn.commit()
        n = await purge_deleted_sessions(conn, 1, workspace_dir=tmp_path)
        assert n == 1

        # Original folder gone; fork folder + deck rows intact.
        assert not src_slides.exists()
        assert (fork_slides / "deck.tex").exists()
        async with conn.execute(
            "SELECT COUNT(*) FROM decks WHERE session_id = ?",
            (res.new_session_id,)) as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM paper_content WHERE id = ?", (pc1,)) as cur:
            assert (await cur.fetchone())[0] == 1


async def test_source_survives_fork_hard_delete(tmp_path: Path) -> None:
    """Deleting the FORK leaves the original session + the shared paper_content
    fully intact (the reverse direction)."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        pc1, r2, src_slides = await _seed_source_with_paper_and_deck(conn, tmp_path)
        res = await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)

        await conn.execute(
            "DELETE FROM chat_sessions WHERE id = ?", (res.new_session_id,))
        await conn.commit()

        # Original is untouched: messages (4), papers row, deck, shared paper.
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 1") as cur:
            assert (await cur.fetchone())[0] == 4
        async with conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = 1") as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM decks WHERE session_id = 1") as cur:
            assert (await cur.fetchone())[0] == 1
        async with conn.execute(
            "SELECT COUNT(*) FROM paper_content WHERE id = ?", (pc1,)) as cur:
            assert (await cur.fetchone())[0] == 1
        assert (src_slides / "deck.tex").exists()


async def test_shared_paper_content_restrict_blocks_delete_while_forked(
    tmp_path: Path,
) -> None:
    """The shared paper_content is RESTRICT-protected: forking adds a second
    reference, so paper_content cannot be deleted while EITHER session keeps it
    — the fork can never orphan or destroy the original's paper data."""
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        pc1, r2, _ = await _seed_source_with_paper_and_deck(conn, tmp_path)
        await fork_session(
            conn, source_session_id=1, fork_run_id=r2, workspace_dir=tmp_path)

        # Two papers rows (original + fork) now reference pc1; RESTRICT blocks
        # a direct paper_content delete.
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute("DELETE FROM paper_content WHERE id = ?", (pc1,))
        await conn.rollback()
