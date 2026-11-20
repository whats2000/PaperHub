"""Agent-edit data-loss fixes (audit 2026-11-21): props merge, rich runs kept
through a plain-string update, stale accepts, dependent ops, math blocks."""
import json

import aiosqlite

from paperhub.notes.ops import BlockIn, Op, apply_ops, resolve_suggestion
from paperhub.notes.store import get_note, get_or_create_note, list_blocks


async def _note(conn):
    return await get_or_create_note(conn, target_kind="session", target_id="1",
                                    title="")


async def _put(conn, note_id, bid, blk, actor="user"):
    note = await get_note(conn, note_id)
    prev = [b.id for b in await list_blocks(conn, note_id)]
    return await apply_ops(conn, note_id, base_revision=note.revision, actor=actor,
                           ops=[Op(op="insert", block_id=bid, block=blk,
                                   after=prev[-1] if prev else None)])


async def _update(conn, note_id, bid, blk, actor="agent"):
    note = await get_note(conn, note_id)
    return await apply_ops(conn, note_id, base_revision=note.revision, actor=actor,
                           ops=[Op(op="update", block_id=bid, block=blk)])


async def _block(conn, note_id, bid):
    return next(b for b in await list_blocks(conn, note_id) if b.id == bid)


# --- 1. update merges props ---------------------------------------------------

async def test_agent_update_keeps_props_it_omits(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "h1", BlockIn(
        type="heading", props={"level": 2, "textColor": "red"},
        content="Old"), actor="agent")
    await _update(conn, n.id, "h1", BlockIn(type="heading", content="New"))
    b = await _block(conn, n.id, "h1")
    assert b.props == {"level": 2, "textColor": "red"}
    assert b.text == "New"


async def test_agent_cannot_change_mark_identity_props(conn: aiosqlite.Connection):
    n = await _note(conn)
    props = {"markId": "mk_1", "quote": "rose to 62%", "kind": "text",
             "pieces": "[{}]", "source": "{\"page\":1}", "color": "yellow"}
    await _put(conn, n.id, "q1", BlockIn(type="paperQuote", props=props),
               actor="agent")
    await _update(conn, n.id, "q1", BlockIn(
        type="paperQuote", props={"markId": "mk_X", "quote": "", "color": "blue"}))
    b = await _block(conn, n.id, "q1")
    assert b.props["markId"] == "mk_1" and b.props["quote"] == "rose to 62%"
    assert b.props["kind"] == "text" and b.props["source"] == "{\"page\":1}"
    assert b.props["pieces"] == "[{}]"
    assert b.props["color"] == "blue"


async def test_accepted_suggestion_merges_props(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "h1", BlockIn(type="heading", props={"level": 3},
                                         content="Mine"))
    res = await _update(conn, n.id, "h1", BlockIn(type="heading", content="Better"))
    sid = res.suggested[0]["suggestion_id"]
    acc = await resolve_suggestion(conn, sid, accept=True)
    assert "h1" in acc.applied
    b = await _block(conn, n.id, "h1")
    assert b.props == {"level": 3} and b.text == "Better"


# --- 2. a plain-string update keeps rich runs ---------------------------------

RICH = [
    {"type": "text", "text": "Loss ", "styles": {}},
    {"type": "text", "text": "drops", "styles": {"bold": True}},
    {"type": "text", "text": " by ", "styles": {}},
    {"type": "inlineMath", "props": {"latex": "x^2"}},
    {"type": "text", "text": " see ", "styles": {}},
    {"type": "markCite", "props": {"markId": "mk_1", "text": "62%"}},
    {"type": "text", "text": " and ", "styles": {}},
    {"type": "link", "href": "https://a.b",
     "content": [{"type": "text", "text": "site", "styles": {}}]},
    {"type": "text", "text": " end", "styles": {"comment": "c1"}},
]
# What the agent reads: "Loss drops by $x^2$ see 62% and site end"


async def test_plain_update_keeps_runs_on_unchanged_text(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=RICH),
               actor="agent")
    res = await _update(conn, n.id, "p1", BlockIn(
        type="paragraph", content="Loss drops sharply by $x^2$ see 62% and site end"))
    b = await _block(conn, n.id, "p1")
    content = b.content
    assert isinstance(content, list)
    assert {"type": "inlineMath", "props": {"latex": "x^2"}} in content
    assert any(r.get("type") == "markCite" and r["props"]["markId"] == "mk_1"
               for r in content)
    link = next(r for r in content if r.get("type") == "link")
    assert link["href"] == "https://a.b"
    bold = [r for r in content if r.get("styles", {}).get("bold")]
    # "sharply " is inserted inside "drops by"? no: between "drops" and " by",
    # a boundary -> plain; the bold run is unchanged.
    assert [r["text"] for r in bold] == ["drops"]
    assert content[-1]["styles"] == {"comment": "c1"}
    assert b.text.startswith("Loss drops sharply by ")
    report = res.inline[0]
    assert report["block_id"] == "p1"
    assert {k["type"] for k in report["kept"]} == {"inlineMath", "markCite"}
    assert report["dropped"] == []


async def test_inserted_text_takes_the_style_it_lands_in(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=[
        {"type": "text", "text": "big cat", "styles": {"italic": True}},
        {"type": "inlineMath", "props": {"latex": "y"}}]), actor="agent")
    await _update(conn, n.id, "p1", BlockIn(type="paragraph",
                                            content="big fat cat$y$"))
    b = await _block(conn, n.id, "p1")
    assert b.content[0] == {"type": "text", "text": "big fat cat",
                            "styles": {"italic": True}}
    assert b.content[1]["type"] == "inlineMath"


async def test_changed_inline_node_is_dropped_and_reported(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=RICH),
               actor="agent")
    res = await _update(conn, n.id, "p1", BlockIn(
        type="paragraph", content="Loss drops by $y^3$ see and site end"))
    b = await _block(conn, n.id, "p1")
    assert not any(r.get("type") in ("inlineMath", "markCite") for r in b.content)
    dropped = {d["type"] for d in res.inline[0]["dropped"]}
    assert dropped == {"inlineMath", "markCite"}
    assert "$y^3$" in b.text


async def test_mark_cite_tags_echoed_back_are_read_as_their_text(
        conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=[
        {"type": "text", "text": "a < b ", "styles": {}},
        {"type": "markCite", "props": {"markId": "mk_1", "text": "62%"}}]),
        actor="agent")
    await _update(conn, n.id, "p1", BlockIn(
        type="paragraph",
        content='a &lt; b, so <mark_cite mark="mk_1">62%</mark_cite>'))
    b = await _block(conn, n.id, "p1")
    assert b.content[-1]["type"] == "markCite"
    assert b.text == "a < b, so 62%"


async def test_structured_content_is_used_as_is(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=RICH),
               actor="agent")
    runs = [{"type": "text", "text": "fresh", "styles": {}}]
    await _update(conn, n.id, "p1", BlockIn(type="paragraph", content=runs))
    assert (await _block(conn, n.id, "p1")).content == runs


async def test_rich_runs_kept_when_a_suggestion_is_accepted(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content=RICH))
    res = await _update(conn, n.id, "p1", BlockIn(
        type="paragraph", content="Loss drops by $x^2$ see 62% and site end!"))
    acc = await resolve_suggestion(conn, res.suggested[0]["suggestion_id"], True)
    assert "p1" in acc.applied
    b = await _block(conn, n.id, "p1")
    assert any(r.get("type") == "markCite" for r in b.content)
    assert b.text.endswith("end!")


# --- 3. a stale accept never overwrites newer user words ------------------------

async def test_accept_after_the_user_changed_the_block_is_stale(
        conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content="one"))
    res = await _update(conn, n.id, "p1", BlockIn(type="paragraph", content="two"))
    sid = res.suggested[0]["suggestion_id"]
    await _update(conn, n.id, "p1", BlockIn(type="paragraph", content="one more"),
                  actor="user")
    acc = await resolve_suggestion(conn, sid, accept=True)
    assert "p1" not in acc.applied
    assert acc.rejected[0]["reason"] == "stale"
    assert (await _block(conn, n.id, "p1")).text == "one more"
    async with conn.execute("SELECT status FROM note_suggestions WHERE id = ?",
                            (sid,)) as cur:
        assert (await cur.fetchone())[0] == "open"
    # Shown again against the current text, a second accept applies.
    again = await resolve_suggestion(conn, sid, accept=True)
    assert "p1" in again.applied
    assert (await _block(conn, n.id, "p1")).text == "two"


async def test_accept_after_a_change_elsewhere_applies(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content="one"))
    await _put(conn, n.id, "p2", BlockIn(type="paragraph", content="other"))
    res = await _update(conn, n.id, "p1", BlockIn(type="paragraph", content="two"))
    await _update(conn, n.id, "p2", BlockIn(type="paragraph", content="else"),
                  actor="user")
    # The block itself rewritten with the same words and props: not stale.
    await _update(conn, n.id, "p1", BlockIn(type="paragraph", content="one"),
                  actor="user")
    acc = await resolve_suggestion(conn, res.suggested[0]["suggestion_id"], True)
    assert "p1" in acc.applied
    assert (await _block(conn, n.id, "p1")).text == "two"


# --- 4. a failed insert fails the ops that hang on it -----------------------------

async def test_op_after_a_failed_insert_fails_depends_on_failed(
        conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "p1", BlockIn(type="paragraph", content="one"),
               actor="agent")
    note = await get_note(conn, n.id)
    res = await apply_ops(conn, n.id, base_revision=note.revision, actor="agent",
                          ops=[
        Op(op="insert", block_id="x1", after="nope",
           block=BlockIn(type="paragraph", content="lost")),
        Op(op="insert", block_id="x2", after="x1",
           block=BlockIn(type="paragraph", content="child of lost")),
        Op(op="insert", block_id="x3", after="p1",
           block=BlockIn(type="paragraph", content="fine")),
    ])
    reasons = {r["block_id"]: r["reason"] for r in res.rejected}
    assert reasons == {"x1": "missing", "x2": "depends_on_failed"}
    assert res.applied == ["x3"]
    assert [r["op"] for r in res.rejected] == [0, 1]
    outcome = None
    async with conn.execute("SELECT outcome_json FROM note_change_sets "
                            "WHERE id = ?", (res.change_set_id,)) as cur:
        outcome = json.loads((await cur.fetchone())[0])
    assert outcome["applied"] == ["x3"]


# --- 6. math blocks take their LaTeX from a string -------------------------------

async def test_math_insert_from_string_sets_latex(conn: aiosqlite.Connection):
    n = await _note(conn)
    await _put(conn, n.id, "m1", BlockIn(type="math", content="$$E = mc^2$$"),
               actor="agent")
    b = await _block(conn, n.id, "m1")
    assert b.props["latex"] == "E = mc^2"
    await _update(conn, n.id, "m1", BlockIn(type="math", content="  a+b  "))
    assert (await _block(conn, n.id, "m1")).props["latex"] == "a+b"


def test_math_block_with_latex_prop_is_left_alone():
    blk = BlockIn(type="math", props={"latex": "x"}, content=None)
    assert blk.props == {"latex": "x"}
