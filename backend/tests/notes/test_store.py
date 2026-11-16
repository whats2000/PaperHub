import aiosqlite
import pytest

from paperhub.notes.store import (
    block_text,
    bump_revision,
    get_note,
    get_or_create_note,
    list_blocks,
    new_block_id,
)


async def _insert(conn, note_id, bid, parent, key, text="", deleted=False):
    await conn.execute(
        "INSERT INTO note_blocks (id, note_id, parent_id, order_key, type, "
        "payload_json, text, author, changed_rev, changed_by, created_at, "
        "updated_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bid, note_id, parent, key, "paragraph",
         '{"props": {}, "content": []}', text, "user", 1, "user", "t", "t",
         "t" if deleted else None),
    )
    await conn.commit()


async def test_get_or_create_is_idempotent(conn):
    a = await get_or_create_note(conn, target_kind="paper", target_id="7", title="T")
    b = await get_or_create_note(conn, target_kind="paper", target_id="7", title="Other")
    assert a.id == b.id and b.title == "T" and a.revision == 0
    assert (await get_note(conn, a.id)) == a
    assert await get_note(conn, 999) is None


async def test_unique_per_target(conn):
    a = await get_or_create_note(conn, target_kind="paper", target_id="7", title="")
    b = await get_or_create_note(conn, target_kind="idea", target_id="7", title="")
    assert a.id != b.id
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO notes (target_kind, target_id, created_at, updated_at) "
            "VALUES ('paper','7','t','t')")


async def test_list_blocks_document_order(conn):
    n = await get_or_create_note(conn, target_kind="paper", target_id="1", title="")
    await _insert(conn, n.id, "b_2", None, "a1", "second")
    await _insert(conn, n.id, "b_1", None, "a0", "first")
    await _insert(conn, n.id, "b_1b", "b_1", "a1", "child b")
    await _insert(conn, n.id, "b_1a", "b_1", "a0", "child a")
    await _insert(conn, n.id, "b_x", None, "a2", "gone", deleted=True)
    ids = [b.id for b in await list_blocks(conn, n.id)]
    assert ids == ["b_1", "b_1a", "b_1b", "b_2"]
    ids_all = [b.id for b in await list_blocks(conn, n.id, include_deleted=True)]
    assert ids_all == ["b_1", "b_1a", "b_1b", "b_2", "b_x"]


async def test_bump_revision(conn):
    n = await get_or_create_note(conn, target_kind="paper", target_id="1", title="")
    assert await bump_revision(conn, n.id, actor="user", summary="x") == 1
    assert await bump_revision(
        conn, n.id, actor="agent", summary="y", change_set_id="cs", run_id=None) == 2
    assert (await get_note(conn, n.id)).revision == 2
    async with conn.execute(
        "SELECT revision, actor, change_set_id FROM note_revisions "
        "WHERE note_id=? ORDER BY revision", (n.id,)) as cur:
        assert [tuple(r) for r in await cur.fetchall()] == [
            (1, "user", None), (2, "agent", "cs")]


def test_new_block_id_shape():
    a, b = new_block_id(), new_block_id()
    assert a != b and a.startswith("b_") and len(a) == 12
    assert a[2:].isalnum()


def test_block_text_extracts_plain():
    runs = [{"type": "text", "text": "Hello "},
            {"type": "link", "href": "u", "content": [{"type": "text", "text": "world"}]}]
    assert block_text("paragraph", {}, runs) == "Hello world"
    assert block_text("paragraph", {}, "plain") == "plain"
    assert block_text("divider", {}, None) == ""
    assert block_text("paperQuote", {"quote": "q text"}, None) == "q text"
    assert block_text("math", {"latex": "x^2"}, None) == "x^2"
    assert block_text("codeBlock", {}, [{"type": "text", "text": "print(1)"}]) == "print(1)"


async def _fts(conn, q):
    async with conn.execute(
        "SELECT b.id FROM note_blocks_fts f JOIN note_blocks b ON b.rowid=f.rowid "
        "WHERE note_blocks_fts MATCH ?", (q,)) as cur:
        return sorted(r[0] for r in await cur.fetchall())


async def test_fts_tracks_insert_update_delete(conn):
    n = await get_or_create_note(conn, target_kind="paper", target_id="1", title="")
    await _insert(conn, n.id, "b_1", None, "a0", "alpha beta")
    assert await _fts(conn, "alpha") == ["b_1"]
    await conn.execute("UPDATE note_blocks SET text='gamma' WHERE id='b_1'")
    assert await _fts(conn, "alpha") == []
    assert await _fts(conn, "gamma") == ["b_1"]
    await conn.execute("UPDATE note_blocks SET deleted_at='t' WHERE id='b_1'")
    assert await _fts(conn, "gamma") == []
    await conn.execute("UPDATE note_blocks SET deleted_at=NULL WHERE id='b_1'")
    assert await _fts(conn, "gamma") == ["b_1"]
    await conn.execute("DELETE FROM note_blocks WHERE id='b_1'")
    assert await _fts(conn, "gamma") == []
    await _insert(conn, n.id, "b_2", None, "a1", "hidden", deleted=True)
    assert await _fts(conn, "hidden") == []
