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


async def _row_colour(conn):
    async with conn.execute(
            "SELECT color FROM note_marks WHERE id = 'mk_1'") as cur:
        return (await cur.fetchone())[0]


async def test_undo_of_a_redraw_set_is_refused(conn: aiosqlite.Connection):
    from paperhub.notes.ops import undo_change_set
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    other = await _note(conn, target_id="2", blocks=[("q9", _props())])
    res = await _update(conn, n, "q1", {"color": "blue"})
    [r] = res.redrawn
    before = (await get_note(conn, other)).revision
    out = await undo_change_set(conn, r["change_set_id"])
    [rej] = out.rejected
    assert rej["reason"] == "follows_mark"
    assert rej["hint"] == ("this change follows its mark; undo the recolour "
                           "that caused it")
    assert out.applied == [] and out.change_set_id is None
    assert await _row_colour(conn) == "blue"
    assert (await get_note(conn, other)).revision == before


async def test_undo_of_the_recolour_still_reverts_every_copy(
        conn: aiosqlite.Connection):
    from paperhub.notes.ops import undo_change_set
    from paperhub.notes.store import list_blocks
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    other = await _note(conn, target_id="2", blocks=[("q9", _props())])
    res = await _update(conn, n, "q1", {"color": "blue"})
    out = await undo_change_set(conn, res.change_set_id)
    assert out.rejected == []
    assert await _row_colour(conn) == "green"
    for note_id in (n, other):
        assert {b.props["color"] for b in await list_blocks(conn, note_id)} == {"green"}


async def _trash(conn, note_id):
    await conn.execute("UPDATE notes SET deleted_at = 'then' WHERE id = ?",
                       (note_id,))
    await conn.commit()


async def test_a_recolour_skips_copies_in_trashed_notes(
        conn: aiosqlite.Connection):
    """A note in the trash is not redrawn (no change set lands in it)."""
    from tests.notes.test_mark_drawing import _props_of
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    trashed = await _note(conn, target_id="2", blocks=[("q9", _props())])
    await _trash(conn, trashed)
    before = (await get_note(conn, trashed)).revision
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert res.redrawn == [] and res.marks[0]["blocks"] == []
    assert (await get_note(conn, trashed)).revision == before
    assert (await _props_of(conn, trashed, "q9"))["color"] == "green"


async def test_a_user_copy_in_the_trash_does_not_make_a_recolour_a_suggestion(
        conn: aiosqlite.Connection):
    from paperhub.notes.ops import BlockIn, Op, apply_ops
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    other = await _note(conn, target_id="2")
    note = await get_note(conn, other)
    await apply_ops(conn, other, base_revision=note.revision, actor="user",
                    ops=[Op(op="insert", block_id="q9", block=BlockIn(
                        type="paperQuote", props=_props()))])
    await _trash(conn, other)
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert "q1" in res.applied and res.suggested == []


async def test_a_restored_chat_note_is_redrawn_from_the_mark_row(
        conn: aiosqlite.Connection):
    """Restoring a chat (and its note) brings its mark blocks back drawn
    as the mark row is now; the row itself never moves."""
    from paperhub.notes.ops import redraw_from_rows
    from tests.notes.test_mark_drawing import _props_of, _row
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    trashed = await _note(conn, target_id="2", blocks=[("q9", _props())])
    await _trash(conn, trashed)
    await _update(conn, n, "q1", {"color": "blue"})
    await conn.execute("UPDATE notes SET deleted_at = NULL WHERE id = ?",
                       (trashed,))
    await conn.commit()
    res = await redraw_from_rows(conn, trashed)
    assert res is not None and res.applied == ["q9"]
    assert (await _props_of(conn, trashed, "q9"))["color"] == "blue"
    assert await _row(conn) == ("blue", "highlight")
