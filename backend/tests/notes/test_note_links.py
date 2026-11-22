"""The ``noteLink`` inline node (a ``[[`` link) and its link-target
endpoints (H5b.1 Task 6)."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub.agents.core.note_xml import cite_runs_xml
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema
from paperhub.notes.export import note_to_markdown
from paperhub.notes.inline_merge import preserve_runs, read_text
from paperhub.notes.models import Block, Note, check_inline
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.store import get_or_create_note, list_blocks

NAME = "RoboTTT › 閘控"


def _link(**over):
    props = {"targetKind": "note", "targetId": 12, "blockId": "b_abc",
             "sectionKey": "", "label": "", "name": NAME}
    props.update(over)
    return {"type": "noteLink", "props": props}


def _txt(t):
    return {"type": "text", "text": t, "styles": {}}


# --- validation --------------------------------------------------------------

def test_valid_note_link_passes() -> None:
    runs = [_link(), _link(targetKind="paper", sectionKey="3 Method")]
    assert check_inline(runs) is runs


@pytest.mark.parametrize("over", [
    {"targetKind": "idea"}, {"targetId": 0}, {"targetId": -3},
    {"targetId": True}, {"targetId": "12"}, {"label": 5}, {"name": None},
    {"blockId": 1}, {"sectionKey": []},
])
def test_bad_note_link_is_rejected(over) -> None:
    with pytest.raises(ValueError):
        check_inline([_link(**over)])


def test_note_link_inside_a_link_run_is_checked() -> None:
    with pytest.raises(ValueError):
        check_inline([{"type": "link", "href": "x",
                       "content": [_link(targetId=0)]}])


# --- text: label, else name --------------------------------------------------

@pytest.mark.asyncio
async def test_block_text_and_fts_use_label_or_name(conn: aiosqlite.Connection):
    n = await get_or_create_note(conn, target_kind="session", target_id="1", title="")
    await apply_ops(conn, n.id, base_revision=0, actor="user", ops=[
        Op(op="insert", block_id="p1", block=BlockIn(type="paragraph", content=[
            _txt("see "), _link()])),
        Op(op="insert", block_id="p2", block=BlockIn(type="paragraph", content=[
            _link(label="the gate")]))])
    texts = {b.id: b.text for b in await list_blocks(conn, n.id)}
    assert texts == {"p1": "see " + NAME, "p2": "the gate"}
    async with conn.execute(
        "SELECT rowid FROM note_blocks_fts WHERE note_blocks_fts MATCH 'RoboTTT'"
    ) as cur:
        assert len(await cur.fetchall()) == 1


@pytest.mark.asyncio
async def test_ops_reject_a_bad_note_link(conn: aiosqlite.Connection):
    n = await get_or_create_note(conn, target_kind="session", target_id="1", title="")
    with pytest.raises(ValueError):
        await apply_ops(conn, n.id, base_revision=0, actor="user", ops=[
            Op(op="insert", block=BlockIn(type="paragraph",
                                          content=[_link(targetId=0)]))])


# --- the agent's reading -----------------------------------------------------

def test_read_renders_a_note_link_tag() -> None:
    xml = cite_runs_xml([_txt("a < "), _link()])
    assert xml == ('a &lt; <note_link kind="note" id="12" block="b_abc">'
                   + NAME + "</note_link>")


def test_note_link_tag_omits_empty_block_and_adds_section() -> None:
    assert cite_runs_xml([_link(blockId="", label="L")]) == (
        '<note_link kind="note" id="12">L</note_link>')
    assert cite_runs_xml([_link(targetKind="paper", targetId=3,
                                blockId="", sectionKey='3 "M"')]) == (
        '<note_link kind="paper" id="3" section="3 &quot;M&quot;">'
        + NAME + "</note_link>")


def test_note_link_text_is_escaped() -> None:
    xml = cite_runs_xml([_link(name="<b>")])
    assert xml is not None and "&lt;b&gt;" in xml


def test_read_text_and_table_cell_read_a_note_link_as_its_text() -> None:
    assert read_text("paragraph", {}, [_txt("x "), _link(label="L")]) == "x L"
    table = {"type": "tableContent", "rows": [
        {"cells": [[_link()], [_txt("b")]]}, {"cells": [[_txt("c")], [_txt("d")]]}]}
    assert NAME in read_text("table", {}, table)


def test_agent_update_of_the_same_text_keeps_the_node() -> None:
    old = [_txt("see "), _link(), _txt(" now")]
    runs, report = preserve_runs(old, "see " + NAME + " now. Also more", {})
    assert any(r.get("type") == "noteLink" for r in runs)
    assert [k["type"] for k in report["kept"]] == ["noteLink"]
    assert report["dropped"] == []


def test_agent_echoing_the_tag_keeps_the_node() -> None:
    old = [_txt("see "), _link()]
    echoed = ('see <note_link kind="note" id="12" block="b_abc">'
              + NAME + "</note_link> ok")
    runs, report = preserve_runs(old, echoed, {})
    assert any(r.get("type") == "noteLink" for r in runs)
    assert report["dropped"] == []


# --- export ------------------------------------------------------------------

def test_markdown_export_writes_the_label_text() -> None:
    note = Note(id=1, target_kind="paper", target_id="5", title="T",
                revision=1, updated_at="")

    def blk(bid, content):
        return Block(id=bid, note_id=1, parent_id=None, type="paragraph", props={},
                     content=content, text="", author="user", section_key=None,
                     changed_rev=1)

    blocks = [blk("p1", [_txt("see "), _link(label="the *gate*"), _txt(".")]),
              blk("p2", [_link()])]
    md, _i, _b = note_to_markdown(note, blocks, {})
    assert "see the \\*gate\\*." in md
    assert NAME in md


# --- endpoints ---------------------------------------------------------------

@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await apply_schema(conn)
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (1, 'Gate chat')")
        await conn.execute("INSERT INTO chat_sessions (id, title) VALUES (2, 'Other')")
        await conn.commit()
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as c:
        yield c


def _h(text, level=1):
    return BlockIn(type="heading", props={"level": level}, content=text)


async def _seed(tmp_path: Path, client: AsyncClient):
    nid1 = (await client.post("/notes/target", params={"kind": "session", "id": 1})
            ).json()["note"]["id"]
    nid2 = (await client.post("/notes/target", params={"kind": "session", "id": 2})
            ).json()["note"]["id"]
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        r = await apply_ops(conn, nid1, base_revision=0, actor="user", ops=[
            Op(op="insert", block_id="b_h1", block=_h("Gate overview")),
            Op(op="insert", block_id="b_p", block=BlockIn(
                type="paragraph", content="not a heading")),
            Op(op="insert", block_id="b_t", block=_h("Toggle gate", 2)),
        ])
        await apply_ops(conn, nid1, base_revision=r.revision, actor="user", ops=[
            Op(op="insert", block_id="b_c", parent_id="b_t",
               block=_h("Nested gate", 3)),
            Op(op="insert", block_id="b_h4", after="b_t", block=_h("Later", 1)),
        ])
        await apply_ops(conn, nid2, base_revision=0, actor="user", ops=[
            Op(op="insert", block_id="b_o", block=_h("Other GATE"))])
    return nid1, nid2


@pytest.mark.asyncio
async def test_note_search_matches_titles_case_insensitively(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid1, _nid2 = await _seed(tmp_path, client)
    r = await client.get("/notes/search", params={"q": "GATE"})
    assert r.status_code == 200
    assert r.json() == [{"note_id": nid1, "kind": "session", "target_id": 1,
                         "title": "Gate chat"}]
    assert len((await client.get("/notes/search")).json()) == 2
    assert len((await client.get("/notes/search", params={"limit": 1})).json()) == 1


@pytest.mark.asyncio
async def test_deleted_notes_are_excluded(client: AsyncClient, tmp_path: Path) -> None:
    nid1, _ = await _seed(tmp_path, client)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.execute("UPDATE notes SET deleted_at = '2026-01-01' WHERE id = ?",
                           (nid1,))
        await conn.commit()
    assert (await client.get("/notes/search", params={"q": "gate"})).json() == []
    heads = (await client.get("/notes/headings/search", params={"q": "gate"})).json()
    assert [h["block_id"] for h in heads] == ["b_o"]
    assert (await client.get(f"/notes/{nid1}/outline")).status_code == 404


@pytest.mark.asyncio
async def test_heading_search_returns_headings_only(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid1, _ = await _seed(tmp_path, client)
    got = (await client.get("/notes/headings/search", params={"q": "gate"})).json()
    assert {h["block_id"] for h in got} == {"b_h1", "b_t", "b_c", "b_o"}
    h = next(h for h in got if h["block_id"] == "b_c")
    assert h == {"note_id": nid1, "note_title": "Gate chat", "block_id": "b_c",
                 "text": "Nested gate", "level": 3}
    assert (await client.get("/notes/headings/search", params={"q": "not a"})
            ).json() == []


@pytest.mark.asyncio
async def test_heading_search_follows_the_outline_rules(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heading picker offers what a note's outline offers: no empty
    heading, none below level 3; and it reads only the notes that hold a
    matching heading."""
    from paperhub.api import notes as notes_api
    nid1, nid2 = await _seed(tmp_path, client)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        async with conn.execute("SELECT revision FROM notes WHERE id = ?",
                                (nid1,)) as cur:
            rev = (await cur.fetchone())[0]
        await apply_ops(conn, nid1, base_revision=rev, actor="user", ops=[
            Op(op="insert", block_id="b_e", block=_h("  ")),
            Op(op="insert", block_id="b_4", block=_h("Deep gate", 4))])
    outline = {o["block_id"] for o in
               (await client.get(f"/notes/{nid1}/outline")).json()}
    heads = (await client.get("/notes/headings/search")).json()
    assert {h["block_id"] for h in heads if h["note_id"] == nid1} == outline
    read: list[int] = []
    real = notes_api.list_blocks

    async def spy(conn, note_id):  # type: ignore[no-untyped-def]
        read.append(note_id)
        return await real(conn, note_id)
    monkeypatch.setattr(notes_api, "list_blocks", spy)
    got = (await client.get("/notes/headings/search", params={"q": "other"})).json()
    assert [h["block_id"] for h in got] == ["b_o"]
    assert read == [nid2]


@pytest.mark.asyncio
async def test_outline_follows_document_order_with_nested_children(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid1, _ = await _seed(tmp_path, client)
    got = (await client.get(f"/notes/{nid1}/outline")).json()
    assert got == [
        {"block_id": "b_h1", "type": "heading", "text": "Gate overview", "level": 1},
        {"block_id": "b_t", "type": "heading", "text": "Toggle gate", "level": 2},
        {"block_id": "b_c", "type": "heading", "text": "Nested gate", "level": 3},
        {"block_id": "b_h4", "type": "heading", "text": "Later", "level": 1}]
    assert (await client.get("/notes/9999/outline")).status_code == 404


@pytest.mark.asyncio
async def test_deleted_heading_block_is_absent_from_outline_and_search(
    client: AsyncClient, tmp_path: Path,
) -> None:
    nid1, _ = await _seed(tmp_path, client)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        async with conn.execute("SELECT revision FROM notes WHERE id = ?",
                                (nid1,)) as cur:
            rev = (await cur.fetchone())[0]
        await apply_ops(conn, nid1, base_revision=rev, actor="user",
                        ops=[Op(op="delete", block_id="b_h1")])
    outline = (await client.get(f"/notes/{nid1}/outline")).json()
    assert "b_h1" not in [o["block_id"] for o in outline]
    heads = (await client.get("/notes/headings/search", params={"q": "overview"})).json()
    assert heads == []


@pytest.mark.asyncio
async def test_orphan_note_is_not_a_search_hit(
    client: AsyncClient, tmp_path: Path,
) -> None:
    await _seed(tmp_path, client)
    async with aiosqlite.connect(tmp_path / "paperhub.db") as conn:
        await conn.execute(
            "INSERT INTO notes (target_kind, target_id, title, created_at, "
            "updated_at) VALUES ('paper', '999', '', 'x', 'x')")
        await conn.execute(
            "INSERT INTO notes (target_kind, target_id, title, created_at, "
            "updated_at) VALUES ('paper', '998', 'Kept title', 'x', 'x')")
        await conn.commit()
    titles = [h["title"] for h in (await client.get("/notes/search")).json()]
    assert "Untitled" not in titles
    assert "Kept title" in titles
    assert len(titles) == 3
