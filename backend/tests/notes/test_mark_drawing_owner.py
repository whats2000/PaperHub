"""Task 16 fix round 1 (4): the mark row belongs to whoever made the
mark; an agent recolour applies directly only on a mark the AI made and
only the AI shows."""
import aiosqlite

from paperhub.notes.ops import BlockIn, Op, apply_ops, resolve_suggestion
from paperhub.notes.store import get_note
from tests.notes.test_mark_drawing import _mark, _note, _props, _props_of, _row, _update


async def test_user_copy_makes_the_agent_change_a_suggestion(
        conn: aiosqlite.Connection):
    await _mark(conn)
    # The AI wrote the mark's block, but the user's copy shows it too.
    n = await _note(conn, blocks=[("q1", _props())])
    note = await get_note(conn, n)
    await apply_ops(conn, n, base_revision=note.revision, actor="user", ops=[
        Op(op="insert", block_id="q2", after="q1",
           block=BlockIn(type="paperQuote", props=_props()))])
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert res.applied == [] and res.suggested[0]["block_id"] == "q1"
    assert await _row(conn) == ("green", "highlight")
    await resolve_suggestion(conn, res.suggested[0]["suggestion_id"],
                             accept=True)
    assert await _row(conn) == ("blue", "highlight")
    assert (await _props_of(conn, n, "q2"))["color"] == "blue"


async def test_user_made_mark_on_an_ai_copy_is_a_suggestion(
        conn: aiosqlite.Connection):
    await _mark(conn, block_id="u1")
    user = await _note(conn, target_id="2", blocks=[("u1", _props())],
                       actor="user")
    n = await _note(conn, blocks=[("q1", _props())])  # the AI's copy
    res = await _update(conn, n, "q1", {"style": "wavy"})
    assert res.applied == [] and res.suggested[0]["block_id"] == "q1"
    assert await _row(conn) == ("green", "highlight")
    assert (await _props_of(conn, user, "u1")).get("style") == "highlight"
