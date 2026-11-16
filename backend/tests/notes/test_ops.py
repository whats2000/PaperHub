import json

import aiosqlite
import pytest

from paperhub.notes.ops import (
    BlockIn,
    Op,
    apply_ops,
    reapply_change_set,
    resolve_suggestion,
    restore_blocks,
    undo_change_set,
)
from paperhub.notes.store import get_note, get_or_create_note, list_blocks


def _para(text: str) -> BlockIn:
    return BlockIn(type="paragraph", content=[{"type": "text", "text": text}])


def _heading(text: str, level: int) -> BlockIn:
    return BlockIn(type="heading", props={"level": level},
                   content=[{"type": "text", "text": text}])


async def _note(conn):
    return await get_or_create_note(conn, target_kind="paper", target_id="1", title="")


async def _texts(conn, note_id):
    return [b.text for b in await list_blocks(conn, note_id)]


async def _seed(conn, note_id, blocks, actor="user"):
    """Insert blocks in order with client ids; returns the result."""
    ops = []
    prev = None
    for bid, blk in blocks:
        ops.append(Op(op="insert", block_id=bid, block=blk, after=prev))
        prev = bid
    note = await get_note(conn, note_id)
    return await apply_ops(conn, note_id, base_revision=note.revision,
                           ops=ops, actor=actor)


async def test_insert_under_heading_lands_at_section_end(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [
        ("h1", _heading("Intro", 1)),
        ("p1", _para("intro text")),
        ("h2", _heading("Sub", 2)),
        ("p2", _para("sub text")),
        ("h3", _heading("Method", 1)),
        ("p3", _para("method text")),
    ])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="insert", block_id="x1", block=_para("end of intro"),
           under_heading="h1"),
        Op(op="insert", block_id="x2", block=_para("end of sub"),
           under_heading="h2"),
        Op(op="insert", block_id="x3", block=_para("end of method"),
           under_heading="h3", divider_before=True),
    ])
    assert res.applied[:2] == ["x1", "x2"] and "x3" in res.applied
    blocks = await list_blocks(conn, n.id)
    assert [b.id for b in blocks][:7] == ["h1", "p1", "h2", "p2", "x1", "x2", "h3"]
    assert [b.type for b in blocks][-2:] == ["divider", "paragraph"]
    assert blocks[-1].id == "x3" and blocks[-1].author == "ai"


async def test_insert_under_section_ends_at_next_section(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [
        ("s1", BlockIn(type="section", props={"title": "A"})),
        ("h", _heading("inside", 1)),
        ("s2", BlockIn(type="section", props={"title": "B"})),
    ])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="x", block=_para("x"), under_heading="s1"),
    ])
    assert [b.id for b in await list_blocks(conn, n.id)] == ["s1", "h", "x", "s2"]


async def test_client_block_id_kept_when_unused(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="a", block=_para("b"), after="a"),
    ])
    assert res.applied and res.applied[0] != "a"
    assert await _texts(conn, n.id) == ["a", "b"]


async def test_one_revision_per_apply(conn):
    n = await _note(conn)
    res = await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b"))])
    assert res.revision == 1
    res2 = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("a2")),
        Op(op="update", block_id="b", block=_para("b2")),
    ])
    assert res2.revision == 2
    assert (await get_note(conn, n.id)).revision == 2
    async with conn.execute(
        "SELECT revision, change_set_id FROM note_revisions WHERE note_id = ? "
        "ORDER BY revision", (n.id,)) as cur:
        rows = await cur.fetchall()
    assert rows == [(1, res.change_set_id), (2, res2.change_set_id)]
    assert {b.changed_rev for b in await list_blocks(conn, n.id)} == {2}


async def test_history_rows_per_op(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b")),
                             ("c", _para("c"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("a2")),
        Op(op="move", block_id="c", before="a"),
        Op(op="delete", block_id="b"),
    ])
    async with conn.execute(
        "SELECT block_id, revision, actor, op FROM note_block_history "
        "WHERE note_id = ? ORDER BY id", (n.id,)) as cur:
        rows = await cur.fetchall()
    assert rows == [
        ("a", 1, "user", "insert"), ("b", 1, "user", "insert"),
        ("c", 1, "user", "insert"),
        ("a", 2, "user", "update"), ("c", 2, "user", "move"),
        ("b", 2, "user", "delete"),
    ]
    assert await _texts(conn, n.id) == ["c", "a2"]


async def test_delete_soft_deletes_subtree(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="k", block=_para("kid"), parent_id="a"),
    ])
    await apply_ops(conn, n.id, base_revision=2, actor="user", ops=[
        Op(op="delete", block_id="a"),
    ])
    assert await list_blocks(conn, n.id) == []
    async with conn.execute(
        "SELECT COUNT(*) FROM note_blocks WHERE deleted_at IS NOT NULL") as cur:
        assert (await cur.fetchone())[0] == 2


async def test_agent_update_on_user_block_becomes_suggestion(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("mine"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="update", block_id="a", block=_para("agent's")),
    ])
    assert res.applied == [] and len(res.suggested) == 1
    assert res.suggested[0]["block_id"] == "a"
    assert await _texts(conn, n.id) == ["mine"]
    async with conn.execute(
        "SELECT block_id, kind, status, change_set_id FROM note_suggestions") as cur:
        assert await cur.fetchall() == [("a", "update", "open", res.change_set_id)]


async def test_agent_delete_on_user_block_becomes_suggestion(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("mine"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="delete", block_id="a"),
    ])
    assert res.applied == [] and res.suggested[0]["block_id"] == "a"
    assert await _texts(conn, n.id) == ["mine"]
    async with conn.execute("SELECT kind FROM note_suggestions") as cur:
        assert (await cur.fetchone())[0] == "delete"


async def test_agent_op_on_block_changed_since_base_rejected(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("ai a")), ("b", _para("ai b"))],
                actor="agent")
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("user a")),
    ])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="update", block_id="a", block=_para("agent a")),
        Op(op="update", block_id="b", block=_para("agent b")),
        Op(op="delete", block_id="nope"),
        Op(op="update", block_id="b"),
    ])
    assert res.applied == ["b"]
    reasons = {(r["block_id"], r["reason"]) for r in res.rejected}
    assert reasons == {("a", "changed_since_base"), ("nope", "missing"),
                       ("b", "invalid")}
    conflict = next(r for r in res.rejected if r["block_id"] == "a")
    assert conflict["current"].text == "user a"
    assert await _texts(conn, n.id) == ["user a", "agent b"]


async def test_user_conflict_returns_current_block(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    first = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("from pane"))])
    assert first.applied == ["a"]
    second = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("from card"))])
    assert second.applied == []
    [rej] = second.rejected
    assert rej["reason"] == "changed_since_base"
    assert rej["current"].text == "from pane"
    # A client that advances its base from the ApplyResult is not blocked.
    third = await apply_ops(conn, n.id, base_revision=first.revision,
                            actor="user", ops=[
        Op(op="update", block_id="a", block=_para("again"))])
    assert third.applied == ["a"]


async def test_user_edit_makes_ai_block_users(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("ai"))], actor="agent")
    assert (await list_blocks(conn, n.id))[0].author == "ai"
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("edited"))])
    assert (await list_blocks(conn, n.id))[0].author == "user"
    res = await apply_ops(conn, n.id, base_revision=2, actor="agent", ops=[
        Op(op="update", block_id="a", block=_para("agent again"))])
    assert res.applied == [] and len(res.suggested) == 1


async def test_undo_change_set_keeps_user_edited_blocks(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("u", _para("user text"))])
    agent = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="insert", block_id="x", block=_para("ai x"), after="u"),
        Op(op="insert", block_id="y", block=_para("ai y"), after="x"),
        Op(op="update", block_id="u", block=_para("suggested")),
    ])
    assert agent.applied == ["x", "y"] and len(agent.suggested) == 1
    # The user edits y after the agent's change set.
    await apply_ops(conn, n.id, base_revision=agent.revision, actor="user",
                    ops=[Op(op="update", block_id="y", block=_para("my y"))])
    res = await undo_change_set(conn, agent.change_set_id)
    assert res.applied == ["x"]
    assert [(r["block_id"], r["reason"]) for r in res.rejected] == [
        ("y", "changed_since_base")]
    assert res.rejected[0]["current"].text == "my y"
    assert await _texts(conn, n.id) == ["user text", "my y"]
    async with conn.execute("SELECT status FROM note_suggestions") as cur:
        assert (await cur.fetchone())[0] == "withdrawn"
    async with conn.execute(
        "SELECT status FROM note_change_sets WHERE id = ?",
        (agent.change_set_id,)) as cur:
        assert (await cur.fetchone())[0] == "undone"


async def test_undo_reverts_update_move_delete(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b")),
                             ("c", _para("c"))])
    cs = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("a2")),
        Op(op="move", block_id="c", before="a"),
        Op(op="delete", block_id="b"),
    ])
    assert await _texts(conn, n.id) == ["c", "a2"]
    res = await undo_change_set(conn, cs.change_set_id)
    assert sorted(res.applied) == ["a", "b", "c"] and res.rejected == []
    assert await _texts(conn, n.id) == ["a", "b", "c"]


async def test_reapply_after_undo(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("u", _para("u"))])
    cs = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="insert", block_id="x", block=_para("x"), after="u"),
        Op(op="insert", block_id="y", block=_para("y"), after="x"),
    ])
    await undo_change_set(conn, cs.change_set_id)
    assert await _texts(conn, n.id) == ["u"]
    again = await reapply_change_set(conn, cs.change_set_id)
    assert len(again.applied) == 2 and again.change_set_id != cs.change_set_id
    assert await _texts(conn, n.id) == ["u", "x", "y"]
    assert all(b.author == "ai" for b in (await list_blocks(conn, n.id))[1:])


async def test_restore_deleted_blocks(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="k", block=_para("kid"), parent_id="a")])
    await apply_ops(conn, n.id, base_revision=2, actor="user", ops=[
        Op(op="delete", block_id="a")])
    assert await _texts(conn, n.id) == ["b"]
    res = await restore_blocks(conn, n.id, ["a", "zzz"], actor="user")
    assert res.applied == ["a", "k"]
    assert [r["reason"] for r in res.rejected] == ["missing"]
    assert await _texts(conn, n.id) == ["a", "kid", "b"]
    async with conn.execute(
        "SELECT block_id FROM note_block_history WHERE op = 'restore' "
        "ORDER BY id") as cur:
        assert [r[0] for r in await cur.fetchall()] == ["a", "k"]


async def test_accept_and_reject_suggestion(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="update", block_id="a", block=_para("agent a")),
        Op(op="delete", block_id="b"),
    ])
    sug = {s["block_id"]: s["suggestion_id"] for s in res.suggested}
    acc = await resolve_suggestion(conn, sug["a"], accept=True)
    assert acc.applied == ["a"]
    blocks = await list_blocks(conn, n.id)
    assert blocks[0].text == "agent a" and blocks[0].author == "user"
    rej = await resolve_suggestion(conn, sug["b"], accept=False)
    assert rej.applied == [] and rej.revision == acc.revision
    assert await _texts(conn, n.id) == ["agent a", "b"]
    async with conn.execute(
        "SELECT block_id, status FROM note_suggestions ORDER BY block_id") as cur:
        assert await cur.fetchall() == [("a", "accepted"), ("b", "rejected")]
    async with conn.execute(
        "SELECT actor FROM note_block_history WHERE block_id = 'a' "
        "ORDER BY id DESC LIMIT 1") as cur:
        assert (await cur.fetchone())[0] == "user"


async def test_accept_delete_suggestion(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent",
                          ops=[Op(op="delete", block_id="a")])
    await resolve_suggestion(conn, res.suggested[0]["suggestion_id"], accept=True)
    assert await _texts(conn, n.id) == []


async def test_undo_and_reapply_restore(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user",
                    ops=[Op(op="delete", block_id="a")])
    res = await restore_blocks(conn, n.id, ["a"], actor="user")
    assert await _texts(conn, n.id) == ["a", "b"]
    await undo_change_set(conn, res.change_set_id)
    assert await _texts(conn, n.id) == ["b"]
    again = await reapply_change_set(conn, res.change_set_id)
    assert again.applied == ["a"]
    assert await _texts(conn, n.id) == ["a", "b"]


# --- fix round 1 -----------------------------------------------------------

async def _cs_row(conn, cs_id):
    async with conn.execute(
        "SELECT status, outcome_json FROM note_change_sets WHERE id = ?",
        (cs_id,)) as cur:
        status, outcome = await cur.fetchone()
    return status, json.loads(outcome)


async def test_agent_delete_with_user_descendant_becomes_suggestion(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("p", _para("ai parent"))], actor="agent")
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="k", block=_para("my kid"), parent_id="p")])
    res = await apply_ops(conn, n.id, base_revision=2, actor="agent",
                          ops=[Op(op="delete", block_id="p")])
    assert res.applied == []
    assert [s["block_id"] for s in res.suggested] == ["p"]
    assert await _texts(conn, n.id) == ["ai parent", "my kid"]
    await resolve_suggestion(conn, res.suggested[0]["suggestion_id"],
                             accept=True)
    assert await _texts(conn, n.id) == []


async def test_delete_rejected_when_descendant_changed_since_base(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("p", _para("p"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="k", block=_para("kid"), parent_id="p")])
    res = await apply_ops(conn, n.id, base_revision=1, actor="user",
                          ops=[Op(op="delete", block_id="p")])
    assert res.applied == []
    [rej] = res.rejected
    assert rej["block_id"] == "p" and rej["reason"] == "changed_since_base"
    assert rej["current"].id == "k"
    assert await _texts(conn, n.id) == ["p", "kid"]


async def test_reapply_after_partial_undo(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("u", _para("u"))])
    cs = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="insert", block_id="x", block=_para("x"), after="u"),
        Op(op="insert", block_id="y", block=_para("y"), after="x"),
    ])
    edit = await apply_ops(conn, n.id, base_revision=cs.revision,
                           actor="user",
                           ops=[Op(op="update", block_id="y",
                                   block=_para("my y"))])
    await undo_change_set(conn, cs.change_set_id)
    assert await _texts(conn, n.id) == ["u", "my y"]
    again = await reapply_change_set(conn, cs.change_set_id)
    assert again.applied == ["x"]
    assert [(r["block_id"], r["reason"]) for r in again.rejected] == [
        ("y", "changed_since_base")]
    assert again.change_set_id != cs.change_set_id
    blocks = await list_blocks(conn, n.id)
    assert [b.id for b in blocks] == ["u", "x", "y"]
    assert [b.text for b in blocks] == ["u", "x", "my y"]
    assert blocks[2].changed_rev == edit.revision
    status, outcome = await _cs_row(conn, cs.change_set_id)
    assert status == "undone" and outcome["reapplied_by"] == again.change_set_id
    # The reapply is itself an undoable change set.
    await undo_change_set(conn, again.change_set_id)
    assert await _texts(conn, n.id) == ["u", "my y"]


async def test_reapply_rejects_block_edited_after_undo(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    cs = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="update", block_id="a", block=_para("a2"))])
    undo = await undo_change_set(conn, cs.change_set_id)
    await apply_ops(conn, n.id, base_revision=undo.revision, actor="user",
                    ops=[Op(op="update", block_id="a", block=_para("a3"))])
    again = await reapply_change_set(conn, cs.change_set_id)
    assert again.applied == []
    assert [(r["block_id"], r["reason"]) for r in again.rejected] == [
        ("a", "changed_since_base")]
    assert again.rejected[0]["current"].text == "a3"
    assert await _texts(conn, n.id) == ["a3"]


async def test_reapply_twice_raises(conn):
    n = await _note(conn)
    cs = await _seed(conn, n.id, [("a", _para("a"))])
    await undo_change_set(conn, cs.change_set_id)
    await reapply_change_set(conn, cs.change_set_id)
    with pytest.raises(ValueError):
        await reapply_change_set(conn, cs.change_set_id)
    assert await _texts(conn, n.id) == ["a"]


async def test_accept_suggestion_on_deleted_block_withdraws(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="agent", ops=[
        Op(op="update", block_id="a", block=_para("agent a"))])
    await apply_ops(conn, n.id, base_revision=2, actor="user",
                    ops=[Op(op="delete", block_id="a")])
    acc = await resolve_suggestion(conn, res.suggested[0]["suggestion_id"],
                                   accept=True)
    assert acc.applied == [] and acc.rejected[0]["reason"] == "missing"
    async with conn.execute("SELECT status FROM note_suggestions") as cur:
        assert (await cur.fetchone())[0] == "withdrawn"


async def test_under_heading_anchor_must_be_heading_or_section(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("p", _para("p"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="x", block=_para("x"), under_heading="p")])
    assert res.applied == [] and res.rejected[0]["reason"] == "invalid"


async def test_parent_id_conflicting_with_anchor_is_invalid(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("b", _para("b"))])
    res = await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="x", block=_para("x"), parent_id="a",
           after="b"),
        Op(op="insert", block_id="y", block=_para("y"), after="b"),
    ])
    assert res.applied == ["y"]
    assert [r["reason"] for r in res.rejected] == ["invalid"]


async def test_undo_reparents_when_old_parent_deleted(conn):
    n = await _note(conn)
    await _seed(conn, n.id, [("a", _para("a")), ("z", _para("z"))])
    await apply_ops(conn, n.id, base_revision=1, actor="user", ops=[
        Op(op="insert", block_id="k", block=_para("kid"), parent_id="a")])
    mv = await apply_ops(conn, n.id, base_revision=2, actor="user", ops=[
        Op(op="move", block_id="k", after="z")])
    await apply_ops(conn, n.id, base_revision=mv.revision, actor="user",
                    ops=[Op(op="delete", block_id="a")])
    res = await undo_change_set(conn, mv.change_set_id)
    assert res.applied == ["k"]
    assert res.reparented == [{"block_id": "k", "parent_id": None}]
    blocks = await list_blocks(conn, n.id)
    assert [b.id for b in blocks] == ["k", "z"]
    assert blocks[0].parent_id is None


async def test_future_base_revision_raises(conn):
    n = await _note(conn)
    with pytest.raises(ValueError):
        await apply_ops(conn, n.id, base_revision=5, actor="user", ops=[
            Op(op="insert", block=_para("x"))])
    assert (await get_note(conn, n.id)).revision == 0


async def test_change_set_schema_checks_and_indexes(conn):
    n = await _note(conn)
    with pytest.raises(aiosqlite.IntegrityError):
        await conn.execute(
            "INSERT INTO note_change_sets (id, note_id, actor, base_revision, "
            "result_revision, ops_json, outcome_json, status, created_at) "
            "VALUES ('c', ?, 'robot', 0, 1, '[]', '{}', 'applied', 't')",
            (n.id,))
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'") as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert {"idx_note_change_sets_note",
            "idx_note_suggestions_note_status"} <= names
