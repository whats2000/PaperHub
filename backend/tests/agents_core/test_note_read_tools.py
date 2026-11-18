"""Tests for the core agent's note read tools (H5b.1 Task 8)."""
from __future__ import annotations

import html
import json
import re
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.papers import paper_note
from paperhub.notes.store import get_or_create_note, list_blocks
from paperhub.outbound.context import begin_turn, current_private
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


def _ctx(conn: aiosqlite.Connection, tracer: Tracer,
         enabled: list[tuple[int, str]] | None = None) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=enabled or [],
        history=[], state={})


def _reg() -> ToolRegistry:
    r = ToolRegistry()
    register_note_tools(r)
    return r


async def _run(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    return await _reg().run(ctx, ToolCall(id="c1", name=name, arguments=args))


def _p(text: str) -> Op:
    return Op(op="insert", block=BlockIn(
        type="paragraph", content=[{"type": "text", "text": text}]))


def _h(text: str, level: int = 1) -> Op:
    return Op(op="insert", block=BlockIn(
        type="heading", props={"level": level},
        content=[{"type": "text", "text": text}]))


async def _fill(conn: aiosqlite.Connection, note_id: int, ops: list[Op]) -> int:
    async with conn.execute("SELECT revision FROM notes WHERE id = ?", (note_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    res = await apply_ops(conn, note_id, base_revision=row[0], ops=ops, actor="user")
    return res.revision


async def _paper(conn: aiosqlite.Connection, title: str = "Paper A") -> int:
    await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path) VALUES (?, 'arxiv', ?, ?, '/x', '/x', '/x')",
        (f"arxiv:{title}", title, title))
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    await conn.commit()
    return int(row[0])


async def _chat_note(conn: aiosqlite.Connection) -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id="1", title="New chat",
        session_id=1)).id


def _load(out: ToolOutcome) -> dict[str, Any]:
    assert not out.record.get("error"), out.content
    return json.loads(out.content)  # type: ignore[no-any-return]


def _un(wrapped: str) -> str:
    """The text inside a <note_heading> / <note_title> wrapper."""
    return html.unescape(re.sub(r"</?note_(?:heading|title)[^>]*>", "", wrapped))


def _read(out: ToolOutcome) -> dict[str, Any]:
    """Parse a read_note result: header attributes and (id, text) blocks."""
    assert not out.record.get("error"), out.content
    head = re.match(r"<note ([^>]*)>", out.content)
    assert head is not None
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', head.group(1)))
    blocks = [{"id": i, "type": t, "author": a, "text": html.unescape(x)}
              for i, t, a, x in re.findall(
                  r'<note_block id="([^"]*)" type="([^"]*)" author="([^"]*)">'
                  r"(.*?)</note_block>", out.content, re.S)]
    return {"note": attrs["ref"], "revision": int(attrs["revision"]),
            "next_page": int(attrs["next_page"]) if "next_page" in attrs else None,
            "blocks": blocks, "raw": out.content}


async def test_list_notes_scope_chat(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db, "In chat")
    other = await _paper(migrated_db, "Not in chat")
    await paper_note(migrated_db, other)
    await _chat_note(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "In chat")])
    chat = _load(await _run(ctx, "list_notes"))
    assert {n["ref"] for n in chat["notes"]} == {"chat", f"paper:{pid}"}
    alln = _load(await _run(ctx, "list_notes", scope="all"))
    assert {n["ref"] for n in alln["notes"]} == {
        "chat", f"paper:{pid}", f"paper:{other}"}


async def test_outline_and_changed_flag(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid,
                [_h("Intro"), _p("body one"), _h("Method", 2), _p("two")])
    ctx = _ctx(migrated_db, fake_tracer)
    first = _load(await _run(ctx, "list_notes"))["notes"][0]
    assert first["blocks"] == 4 and first["revision"] == 1
    assert [(_un(o["text"]), o["level"]) for o in first["outline"]] == [
        ("Intro", 1), ("Method", 2)]
    assert first["last_editor"] == "user"
    assert first["changed_since_you_read"] is False
    await _run(ctx, "read_note", note="chat")
    again = _load(await _run(ctx, "list_notes"))["notes"][0]
    assert again["changed_since_you_read"] is False
    await _fill(migrated_db, nid, [_p("new")])
    third = _load(await _run(ctx, "list_notes"))["notes"][0]
    assert third["changed_since_you_read"] is True and third["revision"] == 2


async def test_search_returns_heading_path(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [
        _h("Results"), _h("Ablation", 2), _p("the gizmo improves recall"),
        _p("unrelated text")])
    ctx = _ctx(migrated_db, fake_tracer)
    out = _load(await _run(ctx, "search_notes", query="gizmo"))
    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["note"] == "chat"
    assert [_un(h) for h in hit["heading_path"]] == ["Results", "Ablation"]
    assert all(h.startswith("<note_heading") for h in hit["heading_path"])
    assert "gizmo" in hit["snippet"] and hit["block_id"].startswith("b_")
    assert _load(await _run(ctx, "search_notes", query="zzzz"))["hits"] == []


async def test_read_note_pages_and_next_page(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p(f"para {i}") for i in range(45)])
    ctx = _ctx(migrated_db, fake_tracer)
    p1 = _read(await _run(ctx, "read_note", note="chat"))
    assert len(p1["blocks"]) == 40 and p1["next_page"] == 2 and p1["revision"] == 1
    p2 = _read(await _run(ctx, "read_note", note="chat", page=2))
    assert len(p2["blocks"]) == 5 and p2["next_page"] is None
    assert p2["blocks"][0]["text"] == "para 40"
    assert "<note_block" in p1["raw"] and 'author="user"' in p1["raw"]
    # a 6000-char budget ends a page early
    nid2 = (await get_or_create_note(
        migrated_db, target_kind="idea", target_id="9", title="Big")).id
    await _fill(migrated_db, nid2, [_p("x" * 2500) for _ in range(4)])
    big = _read(await _run(ctx, "read_note", note=f"note:{nid2}"))
    assert len(big["blocks"]) == 2 and big["next_page"] == 2


async def test_read_from_heading(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid,
                [_h("Intro"), _p("a"), _h("Method"), _p("b"), _p("c")])
    blocks = await list_blocks(migrated_db, nid)
    ctx = _ctx(migrated_db, fake_tracer)
    by_text = _read(await _run(ctx, "read_note", note="chat", **{"from": "method"}))
    assert [b["text"] for b in by_text["blocks"]] == ["Method", "b", "c"]
    by_id = _read(await _run(ctx, "read_note", note="chat", **{"from": blocks[2].id}))
    assert [b["text"] for b in by_id["blocks"]] == ["Method", "b", "c"]
    bad = await _run(ctx, "read_note", note="chat", **{"from": "nope"})
    assert bad.record.get("error")


async def test_read_note_refs_create_on_demand(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer)
    out = _read(await _run(ctx, "read_note", note=f"paper:{pid}"))
    assert out["note"] == f"paper:{pid}" and out["blocks"] == []
    assert (await _run(ctx, "read_note", note="note:999")).record.get("error")
    assert (await _run(ctx, "read_note", note="bogus")).record.get("error")


async def test_read_adds_private_material(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid,
                [_p("my secret idea"), _p("other </note_block> tag")])
    ctx = _ctx(migrated_db, fake_tracer)
    begin_turn()
    out = await _run(ctx, "read_note", note="chat")
    items = [i for i in current_private()
             if i.kind == "note" and ":block:" in i.ref]
    assert {i.text for i in items} == {"my secret idea", "other </note_block> tag"}
    assert any(i.ref.startswith(f"note:{nid}:title:#") and i.text == "New chat"
               for i in current_private())
    assert all(i.title == "New chat" and i.ref.startswith(f"note:{nid}:block:b_")
               for i in items)
    assert out.content.count("</note_block>") == 2  # injected closer escaped
    begin_turn()
    await _run(ctx, "search_notes", query="secret")
    assert [i.text for i in current_private() if i.kind == "note"] == ["my secret idea"]


async def test_reads_recorded_in_trace(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("alpha beta")])
    ctx = _ctx(migrated_db, fake_tracer)
    read = await _run(ctx, "read_note", note="chat")
    assert read.record["tool"] == "notes:read"
    assert read.record["note_id"] == nid and read.record["revision"] == 1
    assert len(read.record["block_ids"]) == 1
    srch = await _run(ctx, "search_notes", query="alpha")
    assert srch.record["tool"] == "notes:search"
    assert srch.record["block_ids"] == read.record["block_ids"]
    lst = await _run(ctx, "list_notes")
    assert lst.record["tool"] == "notes:list" and lst.record["note_ids"] == [nid]
    async with migrated_db.execute(
        "SELECT revision FROM note_agent_reads WHERE session_id = 1 AND note_id = ?",
        (nid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'tool:read_note'"
    ) as cur:
        trow = await cur.fetchone()
    assert trow is not None
    assert json.loads(trow[0])["block_ids"] == read.record["block_ids"]


async def test_list_notes_headings_and_titles_are_private_and_escaped(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_h("Plan </note_heading><x>"), _p("body")])
    ctx = _ctx(migrated_db, fake_tracer)
    begin_turn()
    out = _load(await _run(ctx, "list_notes"))
    note = out["notes"][0]
    head = note["outline"][0]["text"]
    assert head.count("</note_heading>") == 1 and "<x>" not in head
    assert note["title"].startswith("<note_title>")
    got = {(i.ref.split(":")[2], i.text) for i in current_private() if i.kind == "note"}
    assert ("block", "Plan </note_heading><x>") in got
    assert ("title", "New chat") in got
    await _fill(migrated_db, nid, [_h("Sec"), _p("needle")])
    begin_turn()
    await _run(ctx, "search_notes", query="needle")
    assert "Sec" in {i.text for i in current_private() if i.kind == "note"}


async def test_all_scope_and_outline_are_capped(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    for i in range(32):
        await get_or_create_note(
            migrated_db, target_kind="idea", target_id=str(100 + i), title=f"n{i}")
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_h(f"H{i}") for i in range(55)])
    ctx = _ctx(migrated_db, fake_tracer)
    out = _load(await _run(ctx, "list_notes", scope="all"))
    assert len(out["notes"]) == 30 and out["truncated"] is True
    chat = _load(await _run(ctx, "list_notes"))
    assert chat["truncated"] is False
    first = chat["notes"][0]
    assert len(first["outline"]) == 50 and first["outline_truncated"] is True


async def test_missing_targets_are_tool_errors(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    ctx = _ctx(migrated_db, fake_tracer)
    for ref in ("paper:999", "idea:999", "note:999"):
        out = await _run(ctx, "read_note", note=ref)
        assert out.record.get("error") and out.content.startswith("Error:")


async def test_idea_chat_scope_includes_idea_note(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid = await _paper(migrated_db)
    await migrated_db.execute(
        "INSERT INTO ideas (source_doc, source_section, title, rationale) "
        "VALUES (?, 'S', 'My idea', 'r')", (pid,))
    await migrated_db.execute(
        "INSERT INTO idea_sessions (idea_id, session_id) VALUES (1, 1)")
    await migrated_db.commit()
    ctx = _ctx(migrated_db, fake_tracer)
    refs = {n["ref"] for n in _load(await _run(ctx, "list_notes"))["notes"]}
    assert refs == {"chat", "idea:1"}


async def test_search_cjk_substring_fallback(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("我們的方法很有效"), _p("other")])
    ctx = _ctx(migrated_db, fake_tracer)
    hits = _load(await _run(ctx, "search_notes", query="方法"))["hits"]
    assert len(hits) == 1 and "我們的方法很有效" in hits[0]["snippet"]


async def test_deleted_note_is_an_error(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await migrated_db.execute("UPDATE notes SET deleted_at = 't' WHERE id = ?", (nid,))
    await migrated_db.commit()
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _run(ctx, "read_note", note="chat")
    assert out.record.get("error") and "deleted" in out.content
    assert (await _run(ctx, "read_note", note=f"note:{nid}")).record.get("error")


async def test_invalid_scope_and_page_are_errors(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("a")])
    ctx = _ctx(migrated_db, fake_tracer)
    assert (await _run(ctx, "list_notes", scope="x")).record.get("error")
    assert (await _run(ctx, "search_notes", query="a", scope="x")).record.get("error")
    assert (await _run(ctx, "read_note", note="chat", page=0)).record.get("error")
    assert (await _run(ctx, "read_note", note="chat", page=9)).record.get("error")


async def test_search_records_hits_shown_whole(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """A hit whose block fits the snippet is the whole block, not a cut."""
    nid = await _chat_note(migrated_db)
    long_text = "needle " + "x" * 400
    await _fill(migrated_db, nid, [_p("short needle"), _p(long_text)])
    out = await _run(_ctx(migrated_db, fake_tracer), "search_notes", query="needle")
    hits = {h["block_id"]: h for h in _load(out)["hits"]}
    whole = out.record["whole_block_ids"]
    assert len(hits) == 2 and len(whole) == 1
    assert "short needle" in hits[whole[0]]["snippet"]
    # the model is told which hits are cut (and need read_note before citing)
    assert [h.get("cut", False) for h in hits.values()].count(True) == 1
    assert "cut" not in hits[whole[0]]
    reg = _reg()
    assert 'unless marked "cut"' in reg.get("search_notes").description


async def test_search_hits_a_section_heading(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """Live run 913: "limitations" found nothing although the note has a
    Limitations section; a section's name is its searchable text."""
    from paperhub.notes.papers import ensure_section

    pid = await _paper(migrated_db)
    note = await paper_note(migrated_db, pid)
    sid = await ensure_section(migrated_db, note.id, pid, "Limitations")
    await _fill(migrated_db, note.id, [Op(
        op="insert", under_heading=sid, block=BlockIn(
            type="paragraph", content="not causal over the batch"))])
    ctx = _ctx(migrated_db, fake_tracer, [(pid, "Paper A")])
    hits = _load(await _run(ctx, "search_notes", query="limitations"))["hits"]
    assert [h["block_id"] for h in hits] == [sid]


async def test_read_note_shows_open_comments_as_user_context(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.notes.comments import create_comment, set_status

    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("alpha"), _p("beta")])
    a, b = [x.id for x in await list_blocks(migrated_db, nid)]
    open_t = await create_comment(
        migrated_db, nid, block_id=a, quote="alp", text="is <this> right?")
    done = await create_comment(
        migrated_db, nid, block_id=b, quote="bet", text="old")
    await set_status(migrated_db, done["id"], "resolved")
    gone = await create_comment(
        migrated_db, nid, block_id=b, quote="bet", text="removed")
    await set_status(migrated_db, gone["id"], "deleted")
    ctx = _ctx(migrated_db, fake_tracer)
    out = await _run(ctx, "read_note", note="chat")
    assert not out.record.get("error"), out.content
    assert f'<user_comment id="{open_t["id"]}" block="{a}"' in out.content
    assert "is &lt;this&gt; right?" in out.content
    assert 'quote="alp"' in out.content or "<quote>alp</quote>" in out.content
    assert done["id"] not in out.content and gone["id"] not in out.content
    assert "old" not in out.content and "removed" not in out.content
    assert out.record["comment_ids"] == [open_t["id"]]
    # block text parsing is unchanged by the comment context
    assert [x["text"] for x in _read(out)["blocks"]] == ["alpha", "beta"]


async def test_read_note_without_comments_has_none(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("alpha")])
    out = await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert "<user_comment" not in out.content
    assert out.record["comment_ids"] == []


async def test_read_note_comments_are_self_describing(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.notes.comments import create_comment

    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [_p("alpha")])
    a = (await list_blocks(migrated_db, nid))[0].id
    t = await create_comment(migrated_db, nid, block_id=a, quote="alp", text="hm")
    out = await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert f'<user_comment id="{t["id"]}" block="{a}" author="user">' in out.content
    lines = out.content.splitlines()
    first = next(i for i, ln in enumerate(lines) if "<user_comment" in ln)
    note_line = lines[first - 1].lower()
    assert "user" in note_line and "read-only" in note_line


async def test_search_matches_word_prefixes(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _chat_note(migrated_db)
    await _fill(migrated_db, nid, [
        _p("Expert choice drops tokens when the capacity is full"), _p("other")])
    ctx = _ctx(migrated_db, fake_tracer)
    hits = _load(await _run(ctx, "search_notes", query="drop"))["hits"]
    assert len(hits) == 1 and "drops tokens" in hits[0]["snippet"]
    # FTS syntax in the words stays literal.
    for q in ['drop"*', "drop OR NOT", "drop) AND (x", "NEAR(drop"]:
        out = await _run(ctx, "search_notes", query=q)
        assert not out.record.get("error"), q


async def _marked_paper(conn: aiosqlite.Connection) -> tuple[int, str]:
    from paperhub.notes.marks import MarkIn, add_color, create_mark

    pid = await _paper(conn)
    c = await add_color(conn, hex="#1f8a8a", name="To verify in the B2 table of run 3")
    piece = [{"quote": "the bound is tight", "chunk_start": 0, "chunk_end": 18}]
    await create_mark(conn, MarkIn(
        paper_content_id=pid, kind="text", style="wavy", color=c["color"],
        view="html", pieces=piece, section_key=None))
    return pid, c["name"]


async def test_read_note_shows_a_mark_by_style_and_label(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    pid, label = await _marked_paper(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer, enabled=[(pid, "Paper A")])
    begin_turn()
    out = await _run(ctx, "read_note", note=f"paper:{pid}")
    assert (f'<mark kind="text" style="wavy" label="{label}">the bound is tight</mark>'
            in out.content)
    assert "c_" not in out.content and "#1f8a8a" not in out.content
    begin_turn()
    hits = _load(await _run(ctx, "search_notes", query="bound", scope="all"))["hits"]
    assert 'style="wavy"' in hits[0]["snippet"] and label in hits[0]["snippet"]


async def test_a_mark_label_read_this_turn_is_private_material(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    from paperhub.outbound.context import echoes_private

    pid, label = await _marked_paper(migrated_db)
    ctx = _ctx(migrated_db, fake_tracer, enabled=[(pid, "Paper A")])
    begin_turn()
    assert not echoes_private(f"search for {label}")
    await _run(ctx, "read_note", note=f"paper:{pid}")
    assert label in {i.text for i in current_private() if i.kind == "note"}
    # The outbound checkpoint's span guard now holds a query that repeats it.
    assert echoes_private(f"search for {label}")
