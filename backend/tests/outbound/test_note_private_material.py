"""Note text the agent read (or wrote) this turn is private material for the
outbound checkpoint's span guard (H5b.1 Task 27, SRS §III-10.5 H5b)."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.notes.comments import create_comment
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note, list_blocks
from paperhub.outbound import checkpoint
from paperhub.outbound.context import begin_turn, current_private
from paperhub.outbound.models import Decision, OutboundRequest, ReviewVerdict
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio

_ENV = ("PAPERHUB_PROFILE", "PAPERHUB_OUTBOUND_UNSURE", "PAPERHUB_SENSITIVE_TERMS",
        "PAPERHUB_OUTBOUND_ALLOWLIST", "PAPERHUB_OUTBOUND_REVIEW_TIMEOUT",
        "PAPERHUB_TRUSTED_SEARCH_URL", "PAPERHUB_PUBLIC_WEB_SEARCH")

# Eight words and more: any 8-word window of it echoed in a query is a leak.
_SECRET = "wafer cracks start at the scribe edge during reflow"
_SPAN_REASON = "contains a passage from private material"


@pytest.fixture(autouse=True)
def _policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW", "1")
    monkeypatch.setenv("PAPERHUB_OUTBOUND_REVIEW_MODEL", "ollama/review")


class _Allow:
    """A review that allows every query (the span guard is the backstop)."""

    async def structured(self, **_kw: Any) -> ReviewVerdict:
        return ReviewVerdict(action="allow", reason="ok")


def _ctx(conn: aiosqlite.Connection, tracer: Tracer) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=[], history=[], state={})


async def _run(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_note_tools(reg)
    out = await reg.run(ctx, ToolCall(id="c1", name=name, arguments=args))
    assert not out.record.get("error"), out.content
    return out


def _p(text: str) -> Op:
    return Op(op="insert", block=BlockIn(
        type="paragraph", content=[{"type": "text", "text": text}]))


async def _note(conn: aiosqlite.Connection, session: int = 1) -> int:
    return (await get_or_create_note(
        conn, target_kind="session", target_id=str(session), title="New chat",
        session_id=session)).id


async def _fill(conn: aiosqlite.Connection, note_id: int, ops: list[Op],
                actor: str = "user") -> None:
    async with conn.execute("SELECT revision FROM notes WHERE id = ?", (note_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    await apply_ops(conn, note_id, base_revision=row[0], ops=ops, actor=actor)  # type: ignore[arg-type]


async def _check(conn: aiosqlite.Connection, text: str,
                 tracer: Tracer | None = None) -> Decision:
    req = OutboundRequest(caller="research.search", kind="s2_search",
                          destination="api.semanticscholar.org", text=text,
                          run_id=1, session_id=1)
    return await checkpoint.check(req, conn=conn, adapter=_Allow(),  # type: ignore[arg-type]
                                  tracer=tracer)


def _is_span(d: Decision) -> bool:
    return (d.action, d.stage, d.reason) == ("ask", "span", _SPAN_REASON)


async def _block_id(conn: aiosqlite.Connection, note_id: int, text: str) -> str:
    return next(b.id for b in await list_blocks(conn, note_id) if b.text == text)


async def test_query_echoing_a_note_block_read_this_turn_is_held_at_span(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(f"My hunch: {_SECRET}.")])
    bid = await _block_id(migrated_db, nid, f"My hunch: {_SECRET}.")
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    d = await _check(migrated_db, f"papers on {_SECRET.upper()}?", fake_tracer)
    assert _is_span(d)
    async with migrated_db.execute(
        "SELECT reason, original_text FROM outbound_log WHERE id = ?", (d.log_id,),
    ) as cur:
        log_row = await cur.fetchone()
    assert log_row is not None
    # The log names the source (note id and block id), never the passage.
    assert log_row[0] == f"{_SPAN_REASON} (note {nid}: block {bid})"
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'outbound:review' "
        "ORDER BY rowid DESC LIMIT 1",
    ) as cur:
        step = await cur.fetchone()
    assert step is not None
    result = json.loads(step[0])
    assert result["span_sources"] == [
        {"kind": "note", "note_id": nid, "block_ids": [bid]}]
    assert "scribe" not in json.dumps(result["span_sources"])


async def test_note_not_read_this_turn_adds_nothing(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(_SECRET)])
    begin_turn()
    d = await _check(migrated_db, _SECRET)
    assert (d.action, d.stage) == ("send", "review")
    assert current_private() == []


@pytest.mark.parametrize("kind", ["ai", "quote", "hidden"])
async def test_ai_written_quoted_and_styled_text_count(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, kind: str,
) -> None:
    nid = await _note(migrated_db)
    if kind == "ai":
        await _fill(migrated_db, nid, [_p(_SECRET)], actor="agent")
    elif kind == "quote":
        # Quoted paper text counts: the user chose to keep it in a private note.
        await _fill(migrated_db, nid, [Op(op="insert", block=BlockIn(
            type="paperQuote", props={"quote": _SECRET, "paper_id": 1}))])
    else:
        await _fill(migrated_db, nid, [Op(op="insert", block=BlockIn(
            type="paragraph", content=[{"type": "text", "text": _SECRET,
                                        "styles": {"textColor": "gray"}}]))])
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert _is_span(await _check(migrated_db, _SECRET))


async def test_comment_text_read_this_turn_counts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p("short block")])
    bid = await _block_id(migrated_db, nid, "short block")
    c = await create_comment(migrated_db, nid, block_id=bid, quote="short",
                             text=f"Remember: {_SECRET}")
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    d = await _check(migrated_db, _SECRET, fake_tracer)
    assert _is_span(d)
    async with migrated_db.execute(
        "SELECT reason FROM outbound_log WHERE id = ?", (d.log_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == f"{_SPAN_REASON} (note {nid}: comment {c['id']})"


async def test_search_snippet_text_counts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(_SECRET), _p("unrelated filler words here")])
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "search_notes", query="scribe")
    assert _is_span(await _check(migrated_db, _SECRET))


async def test_text_written_with_edit_note_counts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    begin_turn()
    out = await _run(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                     base_revision=0, ops=[{"op": "insert", "block": {
                         "type": "paragraph",
                         "content": [{"type": "text", "text": _SECRET}]}}])
    [bid] = out.record["applied"]
    d = await _check(migrated_db, _SECRET)
    assert _is_span(d)
    async with migrated_db.execute(
        "SELECT reason FROM outbound_log WHERE id = ?", (d.log_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == f"{_SPAN_REASON} (note {nid}: block {bid})"


async def test_suggested_edit_payload_counts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """An update of the user's block becomes a suggestion; its text still
    counts as the turn's private material."""
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p("the user's own words")])
    bid = await _block_id(migrated_db, nid, "the user's own words")
    begin_turn()
    out = await _run(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                     base_revision=1, ops=[{"op": "update", "block_id": bid, "block": {
                         "type": "paragraph",
                         "content": [{"type": "text", "text": _SECRET}]}}])
    assert out.record["suggested"] and not out.record["applied"]
    assert _is_span(await _check(migrated_db, _SECRET))


async def test_seven_word_overlap_does_not_trigger(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(_SECRET)])
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    seven = " ".join(_SECRET.split()[:7])
    d = await _check(migrated_db, f"{seven} papers")
    assert (d.action, d.stage) == ("send", "review")


@pytest.mark.parametrize("query", [
    "WAFER  Cracks, start at the scribe-edge; during REFLOW!",
    "ｗａｆｅｒ cracks start at the scribe edge during reflow",
])
async def test_normalisation_matches_h5a(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer, query: str,
) -> None:
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(_SECRET)])
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert _is_span(await _check(migrated_db, query))


async def test_cjk_note_text_uses_the_12_character_window(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db)
    secret = "晶圓裂紋從切割道邊緣開始並在回焊時擴展"
    await _fill(migrated_db, nid, [_p(f"筆記：{secret}。")])
    begin_turn()
    await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    assert _is_span(await _check(migrated_db, f"搜尋 {secret[:12]} 的論文"))
    d = await _check(migrated_db, f"搜尋 {secret[:11]} 的論文")
    assert (d.action, d.stage) == ("send", "review")


async def test_material_is_scoped_per_run(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """Two turns at once: one reads the note, the other's query is not held."""
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p(_SECRET)])
    read = asyncio.Event()

    async def reader() -> Decision:
        begin_turn()
        await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
        read.set()
        return await _check(migrated_db, _SECRET)

    async def other() -> Decision:
        begin_turn()
        await read.wait()
        return await _check(migrated_db, _SECRET)

    a, b = await asyncio.gather(asyncio.create_task(reader()),
                                asyncio.create_task(other()))
    assert _is_span(a)
    assert (b.action, b.stage) == ("send", "review")


async def test_block_read_then_rewritten_keeps_both_texts(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """Items dedup by ref: a block read and then rewritten in the same turn
    must keep its new text too."""
    nid = await _note(migrated_db)
    await _fill(migrated_db, nid, [_p("an early draft by the agent")], actor="agent")
    bid = await _block_id(migrated_db, nid, "an early draft by the agent")
    begin_turn()
    ctx = _ctx(migrated_db, fake_tracer)
    await _run(ctx, "read_note", note="chat")
    out = await _run(ctx, "edit_note", note="chat", base_revision=1, ops=[
        {"op": "update", "block_id": bid, "block": {
            "type": "paragraph", "content": [{"type": "text", "text": _SECRET}]}}])
    assert out.record["applied"] == [bid]
    assert _is_span(await _check(migrated_db, _SECRET))


async def test_note_text_past_the_review_cap_stays_guarded(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    """Full read_note pages of about 6,000 characters each, past the 12k
    review cap: the first block, trimmed out of the review's material,
    still holds the query."""
    nid = await _note(migrated_db)
    filler = [_p(f"filler {i} " + "lorem ipsum " * 495) for i in range(3)]
    await _fill(migrated_db, nid, [_p(_SECRET), *filler])
    bid = await _block_id(migrated_db, nid, _SECRET)
    begin_turn()
    ctx = _ctx(migrated_db, fake_tracer)
    first = await _run(ctx, "read_note", note="chat")
    assert first.record["next_page"] == 2
    await _run(ctx, "read_note", note="chat", page=2)
    await _run(ctx, "read_note", note="chat", page=3)
    items = current_private()
    assert sum(len(i.text) for i in items) == 12_000  # the review's cap
    assert next(i for i in items if ":block:" + bid in i.ref).text == ""
    d = await _check(migrated_db, _SECRET, fake_tracer)
    assert _is_span(d)
    async with migrated_db.execute(
        "SELECT result_summary_json FROM tool_calls WHERE tool = 'outbound:review' "
        "ORDER BY rowid DESC LIMIT 1",
    ) as cur:
        step = await cur.fetchone()
    assert step is not None
    assert {"kind": "note", "note_id": nid, "block_ids": [bid]} in json.loads(
        step[0])["span_sources"]


async def test_private_document_past_the_review_cap_stays_guarded(
    migrated_db: aiosqlite.Connection,
) -> None:
    from paperhub.outbound.context import PrivateItem, add_private
    begin_turn()
    add_private(PrivateItem("document", "doc:41", "Yield review", f"Intro. {_SECRET}."))
    add_private(PrivateItem("chunk", "chunk:7", "Yield review", "y " * 7000))
    add_private(PrivateItem("document", "doc:42", "Other review", "z " * 7000))
    assert current_private()[0].text == ""
    assert _is_span(await _check(migrated_db, _SECRET))


async def test_review_model_input_stays_capped(
    migrated_db: aiosqlite.Connection,
) -> None:
    """The guard keeps every window, but the review sees at most 12k chars."""
    import html
    import re

    from paperhub.outbound.context import PrivateItem, add_private
    seen: list[str] = []

    class _Capture(_Allow):
        async def structured(self, **kw: Any) -> ReviewVerdict:
            seen.append(kw["variables"]["private_material"])
            return await super().structured(**kw)

    begin_turn()
    for i in range(4):
        add_private(PrivateItem("note", f"note:1:block:b{i}", "N", f"w{i} " * 2000))
    req = OutboundRequest(caller="research.search", kind="s2_search",
                          destination="api.semanticscholar.org", text="robot grasping")
    d = await checkpoint.check(req, conn=migrated_db, adapter=_Capture(),  # type: ignore[arg-type]
                               tracer=None)
    assert d.action == "send"
    [material] = seen
    texts = re.findall(r"<private_item [^>]*>(.*?)</private_item>", material, re.S)
    assert sum(len(html.unescape(t)) for t in texts) == 12_000
