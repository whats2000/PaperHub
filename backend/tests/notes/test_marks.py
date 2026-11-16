import json

import aiosqlite
import pytest

import paperhub.notes.marks as marks_mod
from paperhub.notes.marks import (
    MarkIn,
    colors,
    create_mark,
    delete_mark,
    erase_piece,
    list_marks,
    restore_mark,
    restore_piece,
    set_color,
    split_last_piece,
)
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.papers import paper_note
from paperhub.notes.store import get_note, list_blocks


def _piece(q, start=0):
    return {"quote": q, "chunk_start": start, "chunk_end": start + len(q),
            "prefix": "", "suffix": ""}


async def _paper(conn):
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', 'h')",
        (json.dumps([{"name": "Intro"}, {"name": "Method"}]),))
    await conn.commit()


def _in(pieces, **kw):
    base = dict(paper_content_id=1, kind="highlight", color="green",
                view="html", pieces=pieces, section_key="Method")
    return MarkIn(**{**base, **kw})


async def test_create_mark_builds_quote_block(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("hello world")]))
    assert saved.paper_title == "A Paper" and saved.section == "Method"
    assert (saved.part_index, saved.part_count) == (0, 1)
    blocks = await list_blocks(conn, saved.note_id)
    assert [b.type for b in blocks] == ["section", "paperQuote"]
    b = blocks[1]
    assert b.id == m["block_id"] and b.text == "hello world"
    assert b.props["markId"] == m["id"] and b.props["color"] == "green"
    assert b.props["source"] == {"section": "Method"}
    [lm] = await list_marks(conn, 1)
    assert lm["block_id"] == b.id and lm["note_id"] == saved.note_id
    assert lm["part_label"] == "Method"


async def test_join_appends_piece(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in([_piece("one")]))
    m2, _ = await create_mark(conn, _in([_piece("two", 9)], join_to=m["id"]))
    assert m2["id"] == m["id"] and len(m2["pieces"]) == 2
    note = await paper_note(conn, 1)
    b = [x for x in await list_blocks(conn, note.id)
         if x.id == m["block_id"]][0]
    assert b.text == "one two" and len(b.props["pieces"]) == 2
    assert len(await list_marks(conn, 1)) == 1


async def test_split_last_piece(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("one"), _piece("two", 9)]))
    new = await split_last_piece(conn, m["id"])
    assert [p["quote"] for p in new["pieces"]] == ["two"]
    blocks = await list_blocks(conn, saved.note_id)
    assert [b.id for b in blocks[1:]] == [m["block_id"], new["block_id"]]
    assert [b.text for b in blocks[1:]] == ["one", "two"]
    assert len(await list_marks(conn, 1)) == 2


async def test_delete_and_restore_mark_keeps_user_text(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("quoted")]))
    note = await get_note(conn, saved.note_id)
    await apply_ops(conn, note.id, base_revision=note.revision, actor="user",
                    ops=[Op(op="insert", block_id="mine", after=m["block_id"],
                            block=BlockIn(type="paragraph", content=[
                                {"type": "text", "text": "my thought"}]))])
    await delete_mark(conn, m["id"])
    assert await list_marks(conn, 1) == []
    texts = [b.text for b in await list_blocks(conn, note.id)]
    assert texts == ["", "my thought"]
    await restore_mark(conn, m["id"])
    texts = [b.text for b in await list_blocks(conn, note.id)]
    assert texts == ["", "quoted", "my thought"]
    assert len(await list_marks(conn, 1)) == 1


async def test_erase_piece_keeps_rest(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("one"), _piece("two", 9)]))
    out = await erase_piece(conn, m["id"], 0)
    assert out["pieces"][0]["erased"] is True
    blocks = await list_blocks(conn, saved.note_id)
    assert blocks[1].text == "two"
    back = await restore_piece(conn, m["id"], 0)
    assert "erased" not in back["pieces"][0]
    assert (await list_blocks(conn, saved.note_id))[1].text == "one two"
    # erasing every piece removes the mark; restoring one brings it back
    await erase_piece(conn, m["id"], 0)
    await erase_piece(conn, m["id"], 1)
    assert await list_marks(conn, 1) == []
    await restore_piece(conn, m["id"], 1)
    assert (await list_marks(conn, 1))[0]["id"] == m["id"]
    assert (await list_blocks(conn, saved.note_id))[1].text == "two"


async def test_colors_seeded_and_renamed(conn):
    cs = await colors(conn)
    assert [(c["color"], c["name"]) for c in cs] == [
        ("green", "Key result"), ("blue", "Method"), ("pink", "Question"),
        ("purple", "Disagree")]
    assert all(c["visible"] for c in cs)
    await set_color(conn, "blue", name="Approach", visible=False)
    blue = [c for c in await colors(conn) if c["color"] == "blue"][0]
    assert blue == {"color": "blue", "name": "Approach", "visible": False}


async def _note_rev(conn, note_id):
    return (await get_note(conn, note_id)).revision


async def test_delete_rejected_when_block_already_deleted(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("x")]))
    note = await get_note(conn, saved.note_id)
    await apply_ops(conn, note.id, base_revision=note.revision, actor="user",
                    ops=[Op(op="delete", block_id=m["block_id"])])
    with pytest.raises(RuntimeError):
        await delete_mark(conn, m["id"])
    async with conn.execute("SELECT deleted_at FROM note_marks WHERE id = ?",
                            (m["id"],)) as cur:
        assert (await cur.fetchone())[0] is None


async def test_failed_mark_insert_leaves_no_orphan_block(conn, monkeypatch):
    await _paper(conn)
    note = await paper_note(conn, 1)
    before = await _note_rev(conn, note.id)
    monkeypatch.setattr(marks_mod, "_INSERT", "INSERT INTO nope VALUES (?)")
    with pytest.raises(aiosqlite.OperationalError):
        await create_mark(conn, _in([_piece("x")]))
    assert await list_blocks(conn, note.id) == []
    assert await _note_rev(conn, note.id) == before


async def test_invalid_kind_or_view_writes_nothing(conn):
    await _paper(conn)
    for kw in ({"kind": "bogus"}, {"view": "epub"}):
        with pytest.raises(ValueError):
            await create_mark(conn, _in([_piece("x")], **kw))
    note = await paper_note(conn, 1)
    assert await list_blocks(conn, note.id) == []


async def test_join_must_match_paper_colour_kind_and_be_live(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in([_piece("one")]))
    with pytest.raises(ValueError):
        await create_mark(conn, _in([_piece("t")], join_to=m["id"],
                                    color="blue"))
    with pytest.raises(ValueError):
        await create_mark(conn, _in([_piece("t")], join_to=m["id"],
                                    kind="quote"))
    with pytest.raises(ValueError):
        await create_mark(conn, _in([_piece("t")], join_to=m["id"],
                                    paper_content_id=2))
    await delete_mark(conn, m["id"])
    with pytest.raises(ValueError):
        await create_mark(conn, _in([_piece("t")], join_to=m["id"]))


async def test_restore_piece_does_not_revive_user_deleted_mark(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("one"), _piece("two", 9)]))
    await erase_piece(conn, m["id"], 0)
    await delete_mark(conn, m["id"])  # the user deleted it on purpose
    await restore_piece(conn, m["id"], 0)
    assert await list_marks(conn, 1) == []
    await restore_mark(conn, m["id"])  # block props are synced on restore
    blocks = await list_blocks(conn, saved.note_id)
    assert blocks[1].text == "one two"


async def test_split_keeps_piece_indexes_stable(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in(
        [_piece("a"), _piece("b", 5), _piece("c", 9)]))
    await erase_piece(conn, m["id"], 0)
    new = await split_last_piece(conn, m["id"])
    assert [p["quote"] for p in new["pieces"]] == ["c"]
    [orig] = [x for x in await list_marks(conn, 1) if x["id"] == m["id"]]
    assert [p["quote"] for p in orig["pieces"]] == ["a", "b", "c"]
    assert orig["pieces"][0].get("erased") and orig["pieces"][2].get("removed")
    back = await restore_piece(conn, m["id"], 0)
    assert "erased" not in back["pieces"][0]
    with pytest.raises(ValueError):
        await restore_piece(conn, m["id"], 2)  # moved to another mark


async def test_no_section_lands_at_note_end_and_bounds_raise(conn):
    await _paper(conn)
    a, saved = await create_mark(conn, _in([_piece("a")]))
    b, _ = await create_mark(conn, _in([_piece("b")], section_key=None))
    blocks = await list_blocks(conn, saved.note_id)
    assert [x.id for x in blocks][-1] == b["block_id"]
    for fn in (erase_piece, restore_piece):
        for i in (-1, 5):
            with pytest.raises(ValueError):
                await fn(conn, a["id"], i)
