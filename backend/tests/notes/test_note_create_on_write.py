"""Nothing is created by looking (H5b.1 Task 14): a note is created by its
first write, and ``[[`` search lists no empty note nor the note of a chat
the sidebar hides (unless a link points at it)."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.executescript(
            "INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat');"
            "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
            "source_path, source_dir_path, html_path) "
            "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', 's', 'd', 'h');"
            "INSERT INTO ideas (id, source_doc, source_section, title) "
            "VALUES (5, 1, '1 Intro', 'An idea');")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


async def _note_count(tmp_path: Path) -> int:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn, conn.execute(
            "SELECT COUNT(*) FROM notes") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def test_get_target_creates_nothing(client: AsyncClient, tmp_path: Path) -> None:
    for kind, tid, default in [("paper", 1, "A Paper"), ("session", 1, "Chat"),
                               ("idea", 5, "An idea")]:
        r = await client.get("/notes/target", params={"kind": kind, "id": tid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["note"] is None and body["blocks"] == []
        assert body["default_title"] == default
    assert await _note_count(tmp_path) == 0
    # A missing target is still a 404.
    assert (await client.get("/notes/target",
                             params={"kind": "paper", "id": 99})).status_code == 404
    assert (await client.get("/notes/target",
                             params={"kind": "session", "id": 99})).status_code == 404


async def test_post_target_creates_once(client: AsyncClient, tmp_path: Path) -> None:
    a = (await client.post("/notes/target", params={"kind": "session", "id": 1})).json()
    b = (await client.post("/notes/target", params={"kind": "session", "id": 1})).json()
    assert a["note"]["id"] == b["note"]["id"] and a["default_title"] == "Chat"
    assert await _note_count(tmp_path) == 1
    got = (await client.get("/notes/target", params={"kind": "session", "id": 1})).json()
    assert got["note"]["id"] == a["note"]["id"]
    assert (await client.post("/notes/target",
                              params={"kind": "idea", "id": 99})).status_code == 404
    assert await _note_count(tmp_path) == 1


# --- [[ search ---------------------------------------------------------------

def _para(text: str) -> BlockIn:
    return BlockIn(type="paragraph", content=[{"type": "text", "text": text}])


async def _write(tmp_path: Path, note_id: int, *blocks: BlockIn) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_ops(conn, note_id, base_revision=0, actor="user",
                        ops=[Op(op="insert", block=b) for b in blocks])


async def _create(client: AsyncClient, kind: str, tid: int) -> int:
    r = await client.post("/notes/target", params={"kind": kind, "id": tid})
    return int(r.json()["note"]["id"])


async def test_search_skips_empty_and_hidden_chat_notes(
    client: AsyncClient, tmp_path: Path,
) -> None:
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.executescript(
            "INSERT INTO chat_sessions (id, title) VALUES (2, 'Empty chat');"
            "INSERT INTO chat_sessions (id, title, internal) VALUES (3, 'Internal', 1);"
            "INSERT INTO chat_sessions (id, title) VALUES (4, 'Linked empty');"
            "INSERT INTO chat_sessions (id, title, deleted_at) "
            "VALUES (6, 'Deleted', '2026-11-20');")
        await conn.commit()
    full = await _create(client, "session", 1)
    await _write(tmp_path, full, BlockIn(type="heading", props={"level": 1},
                                         content="Chat heading"))
    await _create(client, "session", 2)  # empty: left out
    whitespace = await _create(client, "paper", 1)
    await _write(tmp_path, whitespace, _para("   "))  # blank text: still empty
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        internal = (await get_or_create_note(
            conn, target_kind="session", target_id="3", title="")).id
        deleted = (await get_or_create_note(
            conn, target_kind="session", target_id="6", title="")).id
    await _write(tmp_path, deleted, BlockIn(type="heading", props={"level": 1},
                                            content="Deleted heading"))
    await _write(tmp_path, internal, BlockIn(type="heading", props={"level": 1},
                                             content="Internal heading"))
    linked = await _create(client, "session", 4)  # empty, but a link points here
    await _write(tmp_path, full, BlockIn(type="paragraph", content=[
        {"type": "noteLink", "props": {"targetKind": "note", "targetId": linked,
                                       "label": "", "name": "Linked empty"}}]))
    hits = (await client.get("/notes/search")).json()
    assert sorted(h["note_id"] for h in hits) == sorted([full, linked])
    heads = (await client.get("/notes/headings/search", params={"q": "heading"})).json()
    assert [h["note_id"] for h in heads] == [full]


async def test_search_lists_a_note_with_only_a_title(
    client: AsyncClient,
) -> None:
    nid = await _create(client, "idea", 5)
    assert (await client.get("/notes/search")).json() == []
    await client.patch(f"/notes/{nid}", json={"title": "Named page"})
    assert [h["note_id"] for h in (await client.get("/notes/search")).json()] == [nid]


async def test_a_title_copied_from_the_target_is_not_content(
    client: AsyncClient,
) -> None:
    """A note title auto-filled with its target's title (before
    paperhub-clear-auto-note-titles) does not make the note non-empty."""
    nid = await _create(client, "paper", 1)
    await client.patch(f"/notes/{nid}", json={"title": "A Paper"})
    assert (await client.get("/notes/search")).json() == []
