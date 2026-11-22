"""Task 16 fix round 1 (2): a copy is listed only when its redraw landed."""
import aiosqlite

from paperhub.notes.store import get_note
from tests.notes.test_mark_drawing import _mark, _note, _props, _update


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
    assert res.marks[0]["blocks"] == []
