"""A mark's colour and style have one source, its ``note_marks`` row: an
update of a mark block that changes them writes the row in the same
transaction, and every block showing the mark follows (Task 16a)."""
import json

import aiosqlite
import pytest

from paperhub.notes.ops import (
    BlockIn,
    Op,
    apply_ops,
    resolve_suggestion,
    undo_change_set,
)
from paperhub.notes.store import get_note, get_or_create_note, list_blocks


async def _mark(conn, mark_id="mk_1", kind="text", style="highlight",
                color="green", block_id="q1"):
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content (id, content_key, kind, sha256, "
        "title, source_path, source_dir_path, html_path) VALUES "
        "(7, 'k7', 'pdf_upload', 'abc', 'P', 's', 'd', 'h')")
    await conn.execute(
        "INSERT INTO note_marks (id, paper_content_id, block_id, kind, view, "
        "pieces_json, created_at, color, style) "
        "VALUES (?, 7, ?, ?, 'pdf', ?, 'now', ?, ?)",
        (mark_id, block_id, kind, json.dumps([{"quote": "q", "page": 0}]),
         color, style))
    await conn.commit()


def _props(mark_id="mk_1", kind="text", style="highlight", color="green"):
    p = {"markId": mark_id, "color": color, "kind": kind, "quote": "q",
         "pieces": [{"quote": "q", "page": 0}], "source": {"page": 0}}
    if style:
        p["style"] = style
    return p


_TYPE = {"text": "paperQuote", "figure": "paperFigure"}


async def _note(conn, target_id="1", blocks=(), actor="agent"):
    n = await get_or_create_note(conn, target_kind="session",
                                 target_id=target_id, title="")
    prev = None
    ops = []
    for bid, props in blocks:
        ops.append(Op(op="insert", block_id=bid, after=prev, block=BlockIn(
            type=_TYPE[props["kind"]], props=props)))
        prev = bid
    await apply_ops(conn, n.id, base_revision=n.revision, ops=ops, actor=actor)
    return n.id


async def _update(conn, note_id, bid, props, actor="agent", kind="text"):
    note = await get_note(conn, note_id)
    return await apply_ops(
        conn, note_id, base_revision=note.revision, actor=actor,
        ops=[Op(op="update", block_id=bid,
                block=BlockIn(type=_TYPE[kind], props=props))])


async def _row(conn, mark_id="mk_1"):
    async with conn.execute(
        "SELECT color, style FROM note_marks WHERE id = ?", (mark_id,)
    ) as cur:
        return tuple(await cur.fetchone())


async def _props_of(conn, note_id, bid):
    return next(b for b in await list_blocks(conn, note_id) if b.id == bid).props


async def test_agent_colour_change_writes_the_mark_and_every_block(
        conn: aiosqlite.Connection):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert "q1" in res.applied and not res.rejected
    assert await _row(conn) == ("blue", "highlight")
    assert (await _props_of(conn, n, "q1"))["color"] == "blue"
    # The second block showing the same mark reads blue too.
    assert (await _props_of(conn, n, "q2"))["color"] == "blue"
    assert res.marks == [{"mark_id": "mk_1", "paper_id": 7, "block_id": "q1",
                          "color": {"from": "green", "to": "blue"},
                          "blocks": ["q2"]}]


async def test_a_copy_in_another_note_follows(conn: aiosqlite.Connection):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props())])
    other = await _note(conn, target_id="2", blocks=[("q9", _props())])
    await _update(conn, n, "q1", {"style": "wavy"})
    assert await _row(conn) == ("green", "wavy")
    assert (await _props_of(conn, other, "q9"))["style"] == "wavy"


@pytest.mark.parametrize(("change", "reason"), [
    ({"color": "teal"}, "unknown colour 'teal'"),
    ({"style": "zigzag"}, "unknown mark style 'zigzag'"),
    ({"color": None}, "a highlight needs a colour"),
])
async def test_invalid_value_is_rejected_with_its_reason(
        conn: aiosqlite.Connection, change, reason):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    note = await get_note(conn, n)
    res = await _update(conn, n, "q1", change)
    assert res.applied == [] and res.change_set_id is None
    [rej] = res.rejected
    assert rej["reason"] == "invalid" and reason in rej["hint"]
    # Nothing written: the row, both blocks and the revision are as before.
    assert await _row(conn) == ("green", "highlight")
    assert (await _props_of(conn, n, "q1"))["color"] == "green"
    assert (await _props_of(conn, n, "q2"))["color"] == "green"
    assert (await get_note(conn, n)).revision == note.revision


async def test_a_figure_mark_has_no_style(conn: aiosqlite.Connection):
    await _mark(conn, kind="figure", style=None)
    n = await _note(conn, blocks=[("q1", _props(kind="figure", style=None))])
    res = await _update(conn, n, "q1", {"style": "wavy"}, kind="figure")
    assert res.rejected[0]["hint"] == "a figure mark has no style"
    assert await _row(conn) == ("green", None)
    # Its colour still changes.
    res = await _update(conn, n, "q1", {"color": "pink"}, kind="figure")
    assert not res.rejected and await _row(conn) == ("pink", None)
    assert "style" not in await _props_of(conn, n, "q1")


async def test_a_suggestion_is_checked_and_its_accept_writes_the_mark(
        conn: aiosqlite.Connection):
    await _mark(conn)
    # The user's own mark blocks: the agent's change is a suggestion.
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())],
                    actor="user")
    bad = await _update(conn, n, "q1", {"color": "teal"})
    assert bad.suggested == [] and "unknown colour" in bad.rejected[0]["hint"]
    res = await _update(conn, n, "q1", {"color": "purple"})
    assert await _row(conn) == ("green", "highlight")  # not until accepted
    acc = await resolve_suggestion(conn, res.suggested[0]["suggestion_id"],
                                   accept=True)
    assert "q1" in acc.applied
    assert await _row(conn) == ("purple", "highlight")
    assert (await _props_of(conn, n, "q2"))["color"] == "purple"


async def test_undo_puts_the_mark_back(conn: aiosqlite.Connection):
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())])
    res = await _update(conn, n, "q1", {"color": "blue"})
    await undo_change_set(conn, res.change_set_id)
    assert await _row(conn) == ("green", "highlight")
    assert (await _props_of(conn, n, "q1"))["color"] == "green"
    assert (await _props_of(conn, n, "q2"))["color"] == "green"


async def test_a_move_never_rewrites_the_mark(conn: aiosqlite.Connection):
    await _mark(conn)
    # A block whose props lag the row (no style key): moving it keeps the row.
    stale = _props(style=None)
    n = await _note(conn, blocks=[("q1", stale), ("q2", _props())])
    note = await get_note(conn, n)
    await apply_ops(conn, n, base_revision=note.revision, actor="user",
                    ops=[Op(op="move", block_id="q1", after="q2")])
    assert await _row(conn) == ("green", "highlight")


async def test_a_block_without_a_mark_row_keeps_the_old_merge(
        conn: aiosqlite.Connection):
    n = await _note(conn, blocks=[("q1", _props(mark_id="mk_gone"))])
    res = await _update(conn, n, "q1", {"color": "blue"})
    assert "q1" in res.applied and res.marks == []
    assert (await _props_of(conn, n, "q1"))["color"] == "blue"


async def test_a_colour_the_editor_only_carried_does_not_recolour_the_mark(
        conn: aiosqlite.Connection):
    """The editor sends a block's full props on every edit. A colour it
    carried unchanged from the stored block (one behind its mark row, e.g.
    a copy not redrawn yet) is no change: the mark keeps its colour and
    the block is drawn from it."""
    await _mark(conn)
    n = await _note(conn, blocks=[("q1", _props()), ("q2", _props())],
                    actor="user")
    await conn.execute("UPDATE note_marks SET color = 'blue' WHERE id = 'mk_1'")
    await conn.commit()
    res = await _update(conn, n, "q1", {**_props(), "quote": "q, edited"},
                        actor="user")
    assert res.applied == ["q1"] and res.marks == []
    assert await _row(conn) == ("blue", "highlight")
    props = await _props_of(conn, n, "q1")
    assert props["color"] == "blue" and props["quote"] == "q, edited"
