"""The ``toc`` block: stored, exported as a linked list, read by the agent."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.agents.core.note_tools import _block_xml
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.export import note_to_markdown, pandoc_slug
from paperhub.notes.models import Block, Note

NOTE = Note(id=1, target_kind="session", target_id="1", title="Notes",
            revision=1, updated_at="")


def _blk(bid: str, type_: str, props: dict[str, Any] | None = None,
         text: str = "", parent: str | None = None) -> Block:
    content = [{"type": "text", "text": text, "styles": {}}] if text else []
    return Block(id=bid, note_id=1, parent_id=parent, type=type_,  # type: ignore[arg-type]
                 props=props or {}, content=content or None, text=text,
                 author="user", section_key=None, changed_rev=1)


def _doc() -> list[Block]:
    return [
        _blk("t", "toc"),
        _blk("a", "heading", {"level": 1}, "Intro"),
        _blk("b", "heading", {"level": 2}, "實驗 Setup 結果 (v2)"),
        _blk("c", "heading", {"level": 3}, "Intro"),
        _blk("d", "heading", {"level": 2}, "1. 2024 Results"),
        _blk("e", "heading", {"level": 1}, "  "),
        _blk("f", "paragraph", text="body"),
    ]


def test_slug_follows_pandoc() -> None:
    assert pandoc_slug("實驗 Setup 結果 (v2)") == "實驗-setup-結果-v2"
    assert pandoc_slug("1. 2024 Results") == "results"
    assert pandoc_slug("!!!") == "section"
    assert pandoc_slug("A_b-c.d  e") == "a_b-c.d-e"


def test_export_writes_a_nested_linked_list() -> None:
    md = note_to_markdown(NOTE, _doc(), {})[0]
    want = [
        "- [Intro](#intro)",
        "  - [實驗 Setup 結果 (v2)](#實驗-setup-結果-v2)",
        "    - [Intro](#intro-1)",
        "  - [1. 2024 Results](#results)",
    ]
    assert "\n".join(want) in md
    assert md.count("](#") == 4


def test_export_list_steps_back_out_to_a_sibling_heading() -> None:
    """A note whose headings start at level 2: a level 2 after a level 3
    is a sibling of the first, not its child (live gate: Discussion and
    Appendix exported under Results)."""
    doc = [
        _blk("t", "toc"),
        _blk("a", "heading", {"level": 2}, "Results"),
        _blk("b", "heading", {"level": 3}, "Ablations"),
        _blk("c", "heading", {"level": 2}, "Discussion"),
        _blk("d", "heading", {"level": 3}, "Folded"),
        _blk("e", "heading", {"level": 2}, "Appendix"),
        _blk("f", "heading", {"level": 1}, "Top"),
        _blk("g", "heading", {"level": 3}, "Deep"),
    ]
    md = note_to_markdown(NOTE, doc, {})[0]
    want = [
        "- [Results](#results)",
        "  - [Ablations](#ablations)",
        "- [Discussion](#discussion)",
        "  - [Folded](#folded)",
        "- [Appendix](#appendix)",
        "- [Top](#top)",
        "  - [Deep](#deep)",
    ]
    assert "\n".join(want) in md


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_every_toc_link_matches_a_pandoc_heading_id() -> None:
    md = note_to_markdown(NOTE, _doc(), {})[0]
    html = subprocess.run(["pandoc", "-f", "markdown", "-t", "html", "-s"],
                          input=md, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout
    ids = set(re.findall(r'<h[1-6] id="([^"]*)"', html))
    targets = re.findall(r"\]\(#([^)]*)\)", md)
    assert targets and set(targets) <= ids


def test_a_note_without_headings_exports_no_list() -> None:
    md = note_to_markdown(NOTE, [_blk("t", "toc")], {})[0]
    assert md.strip() == "# Notes"


def test_agent_reads_an_empty_toc_block() -> None:
    assert _block_xml(_blk("t", "toc")) == \
        '<note_block id="t" type="toc" author="user"/>'


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Chat')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_backend_accepts_and_returns_a_toc_block(client: AsyncClient) -> None:
    nid = (await client.post("/notes/target",
                             params={"kind": "session", "id": 1})).json()["note"]["id"]
    r = await client.post(f"/notes/{nid}/ops", json={"base_revision": 0, "ops": [
        {"op": "insert", "block_id": "toc1", "block": {"type": "toc"}}]})
    assert r.status_code == 200, r.text
    got = (await client.get(f"/notes/{nid}")).json()["blocks"]
    assert [b["type"] for b in got] == ["toc"]


@pytest.mark.asyncio
async def test_outline_endpoint_and_export_toc_list_the_same_entries(
    client: AsyncClient,
) -> None:
    nid = (await client.post("/notes/target",
                             params={"kind": "session", "id": 1})).json()["note"]["id"]
    ops = [
        {"op": "insert", "block_id": "h1",
         "block": {"type": "heading", "props": {"level": 1}, "content": "Intro"}},
        {"op": "insert", "block_id": "h2",
         "block": {"type": "heading", "props": {"level": 2}, "content": "   "}},
        {"op": "insert", "block_id": "s1",
         "block": {"type": "section", "props": {"title": ""},
                   "section_key": "2 Method"}},
        {"op": "insert", "block_id": "h3",
         "block": {"type": "heading", "props": {"level": 3}, "content": "Deep"}},
        {"op": "insert", "block_id": "h4",
         "block": {"type": "heading", "props": {"level": 4},
                   "content": "Too deep"}},
        {"op": "insert", "block_id": "t", "block": {"type": "toc"}},
    ]
    r = await client.post(f"/notes/{nid}/ops", json={"base_revision": 0, "ops": ops})
    assert r.status_code == 200, r.text
    outline = (await client.get(f"/notes/{nid}/outline")).json()
    blocks = [Block(**b) for b in (await client.get(f"/notes/{nid}")).json()["blocks"]]
    md = note_to_markdown(NOTE, blocks, {})[0]
    listed = re.findall(r"- \[(.*?)\]\(#", md)
    assert [o["text"] for o in outline] == listed == ["Intro", "2 Method", "Deep"]
