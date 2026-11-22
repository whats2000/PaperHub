"""Task 16 fix round 1 (3, 6): undo / redo / restore and the mark row."""
import aiosqlite

from paperhub.notes.ops import Op, apply_ops, undo_change_set
from paperhub.notes.store import get_note
from tests.notes.test_mark_drawing import _mark, _note, _props, _props_of, _row, _update


async def test_undo_to_a_deleted_colour_leaves_the_mark(
        conn: aiosqlite.Connection):
    from paperhub.notes.marks import add_color, delete_color
    custom = (await add_color(conn, hex="#2f6fd8", name="Mine"))["color"]
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    await _update(conn, n, "q1", {"color": custom})
    res = await _update(conn, n, "q1", {"color": "pink"})
    await delete_color(conn, custom)
    await undo_change_set(conn, res.change_set_id)
    # The colour is gone: the mark keeps pink, and the block draws it.
    assert await _row(conn) == ("pink", "highlight")
    assert (await _props_of(conn, n, "q1"))["color"] == "pink"


async def test_undo_of_an_older_recolour_is_refused_once_redrawn(
        conn: aiosqlite.Connection):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    first = await _update(conn, n, "q1", {"color": "blue"})
    await _update(conn, n, "q2", {"color": "pink"})  # redraws q1
    res = await undo_change_set(conn, first.change_set_id)
    assert {r["block_id"] for r in res.rejected} >= {"q1"}
    assert all(r["reason"] == "changed_since_base" for r in res.rejected)
    assert await _row(conn) == ("pink", "highlight")
    assert (await _props_of(conn, n, "q1"))["color"] == "pink"


async def test_redo_of_an_older_recolour_is_refused_once_redrawn(
        conn: aiosqlite.Connection):
    from paperhub.notes.ops import reapply_change_set
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    first = await _update(conn, n, "q1", {"color": "blue"})
    await undo_change_set(conn, first.change_set_id)
    await _update(conn, n, "q2", {"color": "pink"})  # redraws q1
    res = await reapply_change_set(conn, first.change_set_id)
    assert res.applied == []
    assert {r["reason"] for r in res.rejected} == {"changed_since_base"}
    assert await _row(conn) == ("pink", "highlight")


async def test_restore_draws_the_block_as_its_mark_is_now(
        conn: aiosqlite.Connection):
    from paperhub.notes.ops import restore_blocks
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    note = await get_note(conn, n)
    await apply_ops(conn, n, base_revision=note.revision, actor="agent",
                    ops=[Op(op="delete", block_id="q2")])
    await _update(conn, n, "q1", {"color": "pink"})
    await restore_blocks(conn, n, ["q2"], actor="user")
    assert await _row(conn) == ("pink", "highlight")
    assert (await _props_of(conn, n, "q2"))["color"] == "pink"
