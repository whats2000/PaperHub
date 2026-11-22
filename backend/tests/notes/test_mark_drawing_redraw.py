"""Task 16 fix round 1 (1, 2): redraws in other notes are returned."""
import aiosqlite

from paperhub.notes.store import get_note
from tests.notes.test_mark_drawing import _mark, _note, _props, _update


async def test_cross_note_redraw_is_returned(conn: aiosqlite.Connection):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    other = await _note(conn, target_id="2", blocks=[("q9", _props())])
    before = (await get_note(conn, other)).revision
    res = await _update(conn, n, "q1", {"color": "blue"})
    [r] = res.redrawn
    assert r["note_id"] == other and r["revision"] == before + 1
    assert r["change_set_id"]
    assert res.marks[0]["paper_id"] == 7


async def test_a_copy_the_nested_change_did_not_apply_is_not_listed(
        conn: aiosqlite.Connection, monkeypatch):
    from paperhub.notes import ops as ops_mod
    real = ops_mod._apply_in_tx
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    await _note(conn, target_id="2", blocks=[("q9", _props())])

    async def fake(conn_, note_id, **kw):
        if note_id != n:  # the redraw in the other note lands nothing
            note = await get_note(conn_, note_id)
            return ops_mod.ApplyResult(
                note_id=note_id, revision=note.revision, change_set_id=None,
                applied=[], suggested=[], rejected=[])
        return await real(conn_, note_id, **kw)
    monkeypatch.setattr(ops_mod, "_apply_in_tx", fake)
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert res.marks[0]["blocks"] == [] and res.redrawn == []


async def test_the_other_notes_history_records_the_old_colour(
        conn: aiosqlite.Connection):
    import json
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    other = await _note(conn, target_id="2", blocks=[("q9", _props())])
    res = await _update(conn, n, "q1", {"color": "blue"})
    [r] = res.redrawn
    async with conn.execute(
        "SELECT before_json, after_json FROM note_block_history "
        "WHERE note_id = ? AND block_id = 'q9' AND revision = ?",
        (other, r["revision"]),
    ) as cur:
        before, after = (json.loads(x) for x in await cur.fetchone())
    assert before["props"]["color"] == "green"
    assert after["props"]["color"] == "blue"
