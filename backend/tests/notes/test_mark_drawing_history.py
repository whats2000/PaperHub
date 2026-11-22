"""Task 16 fix round 1 (3, 6): undo / redo / restore and the mark row."""
import json

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


async def _history_after(conn, block_id, revision):
    async with conn.execute(
        "SELECT after_json FROM note_block_history WHERE block_id = ? "
        "AND revision = ? ORDER BY id DESC LIMIT 1", (block_id, revision),
    ) as cur:
        return json.loads((await cur.fetchone())[0])


async def test_the_undo_history_records_the_colour_written(
        conn: aiosqlite.Connection):
    from paperhub.notes.marks import add_color, delete_color
    custom = (await add_color(conn, hex="#2f6fd8", name="Mine"))["color"]
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    await _update(conn, n, "q1", {"color": custom})
    res = await _update(conn, n, "q1", {"color": "pink"})
    await delete_color(conn, custom)
    undo = await undo_change_set(conn, res.change_set_id)
    after = await _history_after(conn, "q1", undo.revision)
    assert after["props"]["color"] == "pink"


async def test_undoing_a_move_of_a_lagging_block_leaves_the_mark(
        conn: aiosqlite.Connection):
    await _mark(conn)
    # q1 lags its row (pink, the row is green): an agent recolour before
    # the fix. Moving it, then undoing the move, never recolours the mark.
    n = await _note(conn, blocks=[("q1", _props(color="pink")),
                                  ("q2", _props())])
    note = await get_note(conn, n)
    moved = await apply_ops(conn, n, base_revision=note.revision, actor="user",
                            ops=[Op(op="move", block_id="q1", after="q2")])
    await undo_change_set(conn, moved.change_set_id)
    assert await _row(conn) == ("green", "highlight")
    assert (await _props_of(conn, n, "q2"))["color"] == "green"
    assert (await _props_of(conn, n, "q1"))["color"] == "green"
