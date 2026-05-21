"""Tests for the /sessions REST surface — creation, listing, history, delete."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.api.chat import _derive_title, _ensure_session, _record_user_message
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessions_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """ASGI test client with DB bootstrapped and model pre-warm disabled."""
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


async def test_post_sessions_creates_empty_session_row(
    sessions_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """POST /sessions returns 201 + {session_id: <int>} and creates a row
    in chat_sessions."""
    resp = await sessions_client.post("/sessions")
    assert resp.status_code == 201
    data = resp.json()
    assert "session_id" in data
    session_id = data["session_id"]
    assert isinstance(session_id, int)
    assert session_id >= 1

    # Verify the row actually exists in the DB.
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, f"chat_sessions row {session_id} not found"


async def test_post_sessions_returns_incrementing_ids(
    sessions_client: AsyncClient,
) -> None:
    """Multiple POST /sessions calls return different session_ids."""
    resp1 = await sessions_client.post("/sessions")
    resp2 = await sessions_client.post("/sessions")
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    id1 = resp1.json()["session_id"]
    id2 = resp2.json()["session_id"]
    assert id1 != id2


# ---------------------------------------------------------------------------
# _ensure_session — robustness against stale / unknown client session ids
# ---------------------------------------------------------------------------


async def test_ensure_session_creates_row_for_unknown_id(tmp_path: Path) -> None:
    """A client may hold a backend_session_id in localStorage that no longer
    exists in the DB (DB reset, deleted session). _ensure_session must
    materialise the row so the subsequent runs/messages FK inserts can't raise
    `FOREIGN KEY constraint failed`."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute("PRAGMA foreign_keys = ON")

        # Id 4242 was never created — the bug repro.
        returned = await _ensure_session(conn, 4242)
        assert returned == 4242

        # The row now exists, so an FK insert into runs succeeds.
        async with conn.execute(
            "SELECT id FROM chat_sessions WHERE id = 4242",
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "stale id must be materialised"
        await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (4242, 'running')",
        )
        await conn.commit()


async def test_ensure_session_preserves_existing_row(tmp_path: Path) -> None:
    """Ensuring an id that already exists must NOT clobber its title/created_at."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (7, 'Existing title')",
        )
        await conn.commit()

        returned = await _ensure_session(conn, 7)
        assert returned == 7
        async with conn.execute(
            "SELECT title FROM chat_sessions WHERE id = 7",
        ) as cur:
            row = await cur.fetchone()
        assert row is not None and row[0] == "Existing title"


# ---------------------------------------------------------------------------
# Title persistence — derived from the first user message, backend-side
# ---------------------------------------------------------------------------


async def test_derive_title_caps_length() -> None:
    short = "Explain flow matching"
    assert _derive_title(short) == short
    long = "word " * 30
    out = _derive_title(long)
    assert len(out) <= 41  # 40 chars + ellipsis
    assert out.endswith("…")


async def test_first_user_message_sets_session_title(tmp_path: Path) -> None:
    """Recording the first user message promotes the default 'New chat' title
    to one derived from the message — so GET /sessions lists meaningful titles
    across devices."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
        await conn.execute("INSERT INTO runs (session_id, status) VALUES (1, 'ok')")
        await conn.commit()

        await _record_user_message(conn, 1, "What is retrieval augmented generation?", 1)
        async with conn.execute("SELECT title FROM chat_sessions WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "What is retrieval augmented generation?"

        # A second user message must NOT overwrite the established title.
        await _record_user_message(conn, 1, "Now compare it to fine-tuning", 1)
        async with conn.execute("SELECT title FROM chat_sessions WHERE id = 1") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "What is retrieval augmented generation?"


# ---------------------------------------------------------------------------
# GET /sessions — cross-device session list
# ---------------------------------------------------------------------------


async def test_list_sessions_returns_meaningful_sessions(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """Meaningful = has messages OR a non-default title. Untouched 'New chat'
    empties are excluded as clutter; a named-but-empty session is included
    because a name signals intent."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        # Session 1: has messages.
        await conn.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (1, 'Flow matching')",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) "
            "VALUES (1, 'user', 'hi'), (1, 'assistant', 'hello')",
        )
        # Session 2: untouched empty — excluded.
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (2)")
        # Session 3: named but no messages — included (a name means something).
        await conn.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (3, 'manual mcp smoke')",
        )
        await conn.commit()

    resp = await sessions_client.get("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    ids = {s["id"] for s in data}
    assert ids == {1, 3}, "include messaged + named; exclude the 'New chat' empty"
    s1 = next(s for s in data if s["id"] == 1)
    assert s1["title"] == "Flow matching"
    assert s1["message_count"] == 2
    assert "created_at" in s1 and "updated_at" in s1
    s3 = next(s for s in data if s["id"] == 3)
    assert s3["message_count"] == 0


async def test_list_sessions_ordered_by_recent_activity(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """Most recently active session comes first."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1), (2)")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (1, 'user', 'old', '2024-01-01 00:00:00')",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (2, 'user', 'new', '2025-01-01 00:00:00')",
        )
        await conn.commit()

    resp = await sessions_client.get("/sessions")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [2, 1], "newest activity first"


# ---------------------------------------------------------------------------
# GET /sessions/{id}/messages — replay history
# ---------------------------------------------------------------------------


async def test_get_session_messages_returns_history(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, routing_decision_json) "
            "VALUES (1, 1, 'ok', "
            "'{\"intent\":\"chitchat\",\"model_tier\":\"small\","
            "\"confidence\":0.9,\"reasoning\":\"hi\"}')",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id, created_at) "
            "VALUES (1, 'user', 'hi there', 1, '2024-01-01 00:00:01'), "
            "(1, 'assistant', 'hello!', 1, '2024-01-01 00:00:02')",
        )
        await conn.commit()

    resp = await sessions_client.get("/sessions/1/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi there"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["run_id"] == 1
    # Assistant message carries the run's routing decision.
    assert msgs[1]["routing_decision"]["intent"] == "chitchat"


async def test_get_session_messages_replays_search_result_cards(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """Inline paper-search cards are persisted per run and replayed, so they
    render identically on every device (not just the browser that ran the
    search)."""
    db_path = tmp_path / "paperhub.db"
    candidates = (
        '[{"paper_id":"arxiv:1","title":"Flow matching","authors":["A"],'
        '"year":2024,"abstract":"x","arxiv_id":"1","has_open_pdf":true,'
        '"reason":"relevant","finalize":true,"auto_added":true,"papers_id":3,'
        '"error":null,"already_in_session":false}]'
    )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO runs (id, session_id, status, search_results_json) "
            "VALUES (1, 1, 'ok', ?)",
            (candidates,),
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'find flow matching', 1), "
            "(1, 'assistant', 'Here are some papers [chunk:3]', 1)",
        )
        await conn.commit()

    resp = await sessions_client.get("/sessions/1/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    # User turn carries no cards; assistant turn replays them.
    assert msgs[0]["search_results"] is None
    cards = msgs[1]["search_results"]
    assert cards is not None and len(cards) == 1
    assert cards[0]["title"] == "Flow matching"
    assert cards[0]["papers_id"] == 3
    assert cards[0]["auto_added"] is True


async def test_get_session_messages_404_for_missing(
    sessions_client: AsyncClient,
) -> None:
    resp = await sessions_client.get("/sessions/9999/messages")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /sessions/{id} — empty → hard delete, has-content → soft delete
# ---------------------------------------------------------------------------


async def test_delete_session_returns_404_for_missing(
    sessions_client: AsyncClient,
) -> None:
    resp = await sessions_client.delete("/sessions/9999")
    assert resp.status_code == 404
    assert "9999" in resp.json()["detail"]


async def test_delete_empty_session_hard_deletes(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """An empty session (no messages) has nothing to undo — it's removed
    outright, cascading any attached rows but leaving shared paper_content."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
        await conn.execute(
            "INSERT INTO paper_content "
            "(content_key, kind, arxiv_id, title, authors_json, year, abstract, "
            " source_path, source_dir_path, html_path) "
            "VALUES ('arxiv:t', 'arxiv', 't', 't', '[]', 2024, 'a', '/x', '/x', '/x.html')",
        )
        await conn.execute(
            "INSERT INTO papers (session_id, paper_content_id) VALUES (1, 1)",
        )
        await conn.commit()

    resp = await sessions_client.delete("/sessions/1")
    assert resp.status_code == 204

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE id = 1",
        ) as cur:
            sess = await cur.fetchone()
        async with conn.execute(
            "SELECT COUNT(*) FROM papers WHERE session_id = 1",
        ) as cur:
            papers = await cur.fetchone()
        async with conn.execute(
            "SELECT COUNT(*) FROM paper_content WHERE id = 1",
        ) as cur:
            pc = await cur.fetchone()
    assert sess is not None and sess[0] == 0, "empty session row should be gone"
    assert papers is not None and papers[0] == 0, "membership should cascade-delete"
    assert pc is not None and pc[0] == 1, "shared paper_content must survive"


async def test_delete_named_empty_session_soft_deletes(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """A named session with no messages is still meaningful → soft-deleted
    (tombstoned), not removed, so it can be restored."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, title) VALUES (1, 'manual mcp smoke')",
        )
        await conn.commit()

    resp = await sessions_client.delete("/sessions/1")
    assert resp.status_code == 204
    async with aiosqlite.connect(db_path) as conn, conn.execute(
        "SELECT deleted_at FROM chat_sessions WHERE id = 1",
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] is not None, "named session must be tombstoned"


async def test_delete_session_with_messages_soft_deletes(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """A session with content is tombstoned, not removed: its row + messages
    survive (so Undo can restore them) but it disappears from GET /sessions."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Keep me')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (1, 'user', 'hi')",
        )
        await conn.commit()

    resp = await sessions_client.delete("/sessions/1")
    assert resp.status_code == 204

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT deleted_at FROM chat_sessions WHERE id = 1",
        ) as cur:
            row = await cur.fetchone()
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 1",
        ) as cur:
            msgs = await cur.fetchone()
    assert row is not None and row[0] is not None, "deleted_at tombstone must be set"
    assert msgs is not None and msgs[0] == 1, "messages must survive for undo"

    # Hidden from the cross-device list.
    listed = await sessions_client.get("/sessions")
    assert all(s["id"] != 1 for s in listed.json())


async def test_restore_session_unhides(
    sessions_client: AsyncClient, tmp_path: Path,
) -> None:
    """POST /sessions/{id}/restore clears the tombstone → session is live and
    listed again."""
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Keep me')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (1, 'user', 'hi')",
        )
        await conn.commit()

    await sessions_client.delete("/sessions/1")
    resp = await sessions_client.post("/sessions/1/restore")
    assert resp.status_code == 204

    listed = await sessions_client.get("/sessions")
    assert any(s["id"] == 1 for s in listed.json()), "restored session must reappear"


async def test_restore_missing_session_404(sessions_client: AsyncClient) -> None:
    resp = await sessions_client.post("/sessions/9999/restore")
    assert resp.status_code == 404


async def test_purge_deleted_sessions_removes_old_tombstones(tmp_path: Path) -> None:
    """purge_deleted_sessions hard-deletes (with cascade) sessions tombstoned
    longer ago than the retention window, and leaves recent/live ones."""
    from paperhub.db.migrate import purge_deleted_sessions

    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute("PRAGMA foreign_keys = ON")
        # 1: tombstoned 40 days ago (purge). 2: tombstoned now (keep).
        # 3: live (keep).
        await conn.execute(
            "INSERT INTO chat_sessions (id, deleted_at) "
            "VALUES (1, datetime('now', '-40 days'))",
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (1, 'user', 'old')",
        )
        await conn.execute(
            "INSERT INTO chat_sessions (id, deleted_at) VALUES (2, datetime('now'))",
        )
        await conn.execute("INSERT INTO chat_sessions (id) VALUES (3)")
        await conn.commit()

        purged = await purge_deleted_sessions(conn, retention_days=30)
        assert purged == 1

        async with conn.execute("SELECT id FROM chat_sessions ORDER BY id") as cur:
            ids = {r[0] for r in await cur.fetchall()}
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 1",
        ) as cur:
            old_msgs = await cur.fetchone()
    assert ids == {2, 3}, "only the old tombstone should be purged"
    assert old_msgs is not None and old_msgs[0] == 0, "purge must cascade messages"
