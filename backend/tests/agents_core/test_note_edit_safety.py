"""The agent's note read/write path, audit 2026-11-21: per-op validation,
lossless reads of inline nodes and math, the inline-node record."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from paperhub.agents.core.note_tools import register_note_tools
from paperhub.agents.core.tools import ToolContext, ToolOutcome, ToolRegistry
from paperhub.llm.adapter import ToolCall
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_note, get_or_create_note, list_blocks
from paperhub.tracing.tracer import Tracer

pytestmark = pytest.mark.asyncio


def _ctx(conn: aiosqlite.Connection, tracer: Tracer) -> ToolContext:
    async def emit(_n: str, _p: dict[str, Any]) -> None:
        return None
    return ToolContext(
        conn=conn, tracer=tracer, session_id=1, run_id=1, settings=MagicMock(),
        adapter=MagicMock(), emit=emit, enabled_papers=[], history=[], state={})


async def _run(ctx: ToolContext, name: str, **args: Any) -> ToolOutcome:
    reg = ToolRegistry()
    register_note_tools(reg)
    return await reg.run(ctx, ToolCall(id="c1", name=name, arguments=args))


async def _note(conn: aiosqlite.Connection, blocks: list[tuple[str, BlockIn]],
                actor: str = "user") -> int:
    nid = (await get_or_create_note(conn, target_kind="session", target_id="1",
                                    title="New chat", session_id=1)).id
    ops, prev = [], None
    for bid, blk in blocks:
        ops.append(Op(op="insert", block_id=bid, block=blk, after=prev))
        prev = bid
    note = await get_note(conn, nid)
    assert note is not None
    await apply_ops(conn, nid, base_revision=note.revision, ops=ops,
                    actor=actor)  # type: ignore[arg-type]
    return nid


async def _mark(conn: aiosqlite.Connection, quote: str) -> None:
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
        "source_path, source_dir_path, html_path) VALUES "
        "(7, 'k7', 'pdf_upload', 'abc', 'P', 's', 'd', 'h')")
    await conn.execute(
        "INSERT INTO note_marks (id, paper_content_id, block_id, kind, view, "
        "pieces_json, created_at) VALUES ('mk_1', 7, 'q1', 'text', 'pdf', ?, 'now')",
        (json.dumps([{"quote": quote, "page": 0}]),))
    await conn.commit()


# --- 5. reads -------------------------------------------------------------------

async def test_read_fills_inline_nodes_and_math(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    await _mark(migrated_db, "the error rate rose to sixty two percent overall")
    await _note(migrated_db, [
        ("p1", BlockIn(type="paragraph", content=[
            {"type": "text", "text": "see ", "styles": {}},
            {"type": "markCite", "props": {"markId": "mk_1", "text": ""}},
            {"type": "text", "text": " with ", "styles": {}},
            {"type": "inlineMath", "props": {"latex": "x^2"}}])),
        ("p2", BlockIn(type="paragraph", content=[
            {"type": "text", "text": "by ", "styles": {}},
            {"type": "mention", "props": {"kind": "paper", "paperId": 7,
                                          "label": "ResNet"}},
            {"type": "inlineMath", "props": {"latex": "y"}}])),
        ("m1", BlockIn(type="math", props={"latex": "E=mc^2"})),
    ])
    out = await _run(_ctx(migrated_db, fake_tracer), "read_note", note="chat")
    raw = out.content
    assert '<mark_cite mark="mk_1">the error rate rose to s…</mark_cite>' in raw
    assert " with $x^2$" in raw
    assert ">by ResNet$y$</note_block>" in raw
    assert ">$$E=mc^2$$</note_block>" in raw


# --- 4. per-op validation -----------------------------------------------------------

async def test_one_invalid_op_does_not_abort_the_rest(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db, [
        ("p1", BlockIn(type="paragraph", content="one"))], actor="agent")
    note = await get_note(migrated_db, nid)
    assert note is not None
    out = await _run(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                     base_revision=note.revision, ops=[
        {"op": "insert", "block_id": "x1", "after": "p1",
         "block": {"type": "nonsense", "content": "bad"}},
        {"op": "insert", "block_id": "x2", "after": "x1",
         "block": {"type": "paragraph", "content": "hangs on x1"}},
        {"op": "insert", "block_id": "x3", "after": "p1",
         "block": {"type": "paragraph", "content": "fine"}},
    ])
    assert not out.record.get("error"), out.content
    data = json.loads(out.content)
    assert len(data["applied"]) == 1
    rej = {r["op"]: r for r in data["rejected"]}
    assert rej[0]["reason"] == "invalid" and rej[0]["block_id"] == "x1"
    assert rej[0]["hint"]
    assert rej[1]["reason"] == "depends_on_failed"
    texts = [b.text for b in await list_blocks(migrated_db, nid)]
    assert texts == ["one", "fine"]


# --- 2. the record names the inline nodes kept / dropped --------------------------

async def test_edit_records_inline_nodes_kept(
    migrated_db: aiosqlite.Connection, fake_tracer: Tracer,
) -> None:
    nid = await _note(migrated_db, [
        ("p1", BlockIn(type="paragraph", content=[
            {"type": "text", "text": "loss ", "styles": {}},
            {"type": "inlineMath", "props": {"latex": "x"}}]))], actor="agent")
    note = await get_note(migrated_db, nid)
    assert note is not None
    out = await _run(_ctx(migrated_db, fake_tracer), "edit_note", note="chat",
                     base_revision=note.revision, ops=[
        {"op": "update", "block_id": "p1",
         "block": {"type": "paragraph", "content": "low loss $x$"}}])
    data = json.loads(out.content)
    assert data["inline"][0]["kept"][0]["type"] == "inlineMath"
    assert out.record["inline"] == data["inline"]
