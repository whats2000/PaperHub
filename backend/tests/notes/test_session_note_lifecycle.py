from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.fork import fork_session
from paperhub.db.migrate import apply_schema, purge_deleted_sessions
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_note, get_or_create_note, list_blocks

pytestmark = pytest.mark.asyncio


def _p(text: str) -> BlockIn:
    return BlockIn(type="paragraph", content=[{"type": "text", "text": text}])


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (1,'user','hi')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


async def test_session_note_lifecycle(client: AsyncClient, tmp_path: Path) -> None:
    nid = (await client.get("/notes/target", params={"kind": "session", "id": 1})
           ).json()["note"]["id"]
    db = tmp_path / "paperhub.db"

    async def deleted_at() -> str | None:
        async with aiosqlite.connect(db) as conn, conn.execute(
                "SELECT deleted_at FROM notes WHERE id = ?", (nid,)) as cur:
            return (await cur.fetchone())[0]

    assert await deleted_at() is None
    assert (await client.delete("/sessions/1")).status_code == 204
    assert await deleted_at() is not None
    assert (await client.post("/sessions/1/restore")).status_code == 204
    assert await deleted_at() is None

    # Purge cascades through notes.session_id.
    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_ops(conn, nid, base_revision=0, actor="user",
                        ops=[Op(op="insert", block=_p("x"))])
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at = datetime('now', '-40 days') "
            "WHERE id = 1")
        await conn.commit()
        assert await purge_deleted_sessions(conn, retention_days=30) == 1
        assert await get_note(conn, nid) is None
        async with conn.execute("SELECT COUNT(*) FROM note_blocks") as cur:
            assert (await cur.fetchone())[0] == 0


async def test_fork_copies_session_note(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Orig')")
        cur = await conn.execute(
            "INSERT INTO runs (session_id, status) VALUES (1, 'ok')")
        run_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (1, 'user', 'q', ?)", (run_id,))
        await conn.commit()
        note = await get_or_create_note(
            conn, target_kind="session", target_id="1", title="Orig", session_id=1)
        res = await apply_ops(conn, note.id, base_revision=0, actor="user", ops=[
            Op(op="insert", block=BlockIn(type="heading", props={"level": 2},
                                          content=[{"type": "text", "text": "H"}]))])
        head = res.applied[0]
        res = await apply_ops(conn, note.id, base_revision=1, actor="agent", ops=[
            Op(op="insert", block=_p("child"), parent_id=head),
            Op(op="insert", block=_p("gone"))])
        await apply_ops(conn, note.id, base_revision=2, actor="user", ops=[
            Op(op="delete", block_id=res.applied[1])])

        fk = await fork_session(conn, source_session_id=1, fork_run_id=run_id,
                                workspace_dir=tmp_path)
        async with conn.execute(
            "SELECT id, revision, title FROM notes WHERE target_kind = 'session' "
            "AND target_id = ?", (str(fk.new_session_id),)) as cur:
            new = await cur.fetchone()
        assert new is not None and new[1] == 1 and new[2] == "Fork of Orig"
        src = await list_blocks(conn, note.id)
        dst = await list_blocks(conn, new[0])
        assert [b.text for b in dst] == [b.text for b in src] == ["H", "child"]
        assert [b.author for b in dst] == [b.author for b in src] == ["user", "ai"]
        assert {b.id for b in dst}.isdisjoint({b.id for b in src})
        assert dst[1].parent_id == dst[0].id
        async with conn.execute(
            "SELECT actor, summary FROM note_revisions WHERE note_id = ?",
            (new[0],)) as cur:
            assert await cur.fetchall() == [("system", "forked")]
        # The source note is untouched.
        assert (await get_note(conn, note.id)).revision == 3  # type: ignore[union-attr]


async def test_restore_keeps_an_independently_deleted_note(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid = (await client.get("/notes/target", params={"kind": "session", "id": 1})
           ).json()["note"]["id"]
    db = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "UPDATE notes SET deleted_at = '2020-01-01 00:00:00' WHERE id = ?",
            (nid,))
        await conn.commit()
    assert (await client.delete("/sessions/1")).status_code == 204
    assert (await client.post("/sessions/1/restore")).status_code == 204
    async with aiosqlite.connect(db) as conn, conn.execute(
            "SELECT deleted_at FROM notes WHERE id = ?", (nid,)) as cur:
        assert (await cur.fetchone())[0] == "2020-01-01 00:00:00"
