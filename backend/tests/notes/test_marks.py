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
    assert b.text == "one … two" and len(b.props["pieces"]) == 2
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
    assert texts == ["Method", "my thought"]  # the section heading, then the block
    await restore_mark(conn, m["id"])
    texts = [b.text for b in await list_blocks(conn, note.id)]
    assert texts == ["Method", "quoted", "my thought"]
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
    assert (await list_blocks(conn, saved.note_id))[1].text == "one\ntwo"  # one save: a paragraph break
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
    # The block's delete took the mark with it; deleting the mark again is
    # rejected and changes nothing.
    async with conn.execute("SELECT deleted_at FROM note_marks WHERE id = ?",
                            (m["id"],)) as cur:
        gone_at = (await cur.fetchone())[0]
    assert gone_at is not None
    with pytest.raises(RuntimeError):
        await delete_mark(conn, m["id"])
    async with conn.execute("SELECT deleted_at FROM note_marks WHERE id = ?",
                            (m["id"],)) as cur:
        assert (await cur.fetchone())[0] == gone_at


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
    assert blocks[1].text == "one\ntwo"  # one save: a paragraph break


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


async def test_block_delete_takes_its_mark_and_undo_brings_it_back(conn):
    """Deleting a mark's block through the op engine (the editor, the
    agent) deletes the mark with it; undo / restore brings both back."""
    from paperhub.notes.ops import restore_blocks, undo_change_set

    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("hello")]))
    note = await get_note(conn, saved.note_id)
    res = await apply_ops(conn, note.id, base_revision=note.revision,
                          actor="user",
                          ops=[Op(op="delete", block_id=m["block_id"])])
    assert res.applied == [m["block_id"]]
    assert await list_marks(conn, 1) == []
    await undo_change_set(conn, res.change_set_id)
    [back] = await list_marks(conn, 1)
    assert back["id"] == m["id"]

    note = await get_note(conn, saved.note_id)
    await apply_ops(conn, note.id, base_revision=note.revision, actor="user",
                    ops=[Op(op="delete", block_id=m["block_id"])])
    assert await list_marks(conn, 1) == []
    await restore_blocks(conn, note.id, [m["block_id"]], actor="user")
    assert [x["id"] for x in await list_marks(conn, 1)] == [m["id"]]


async def test_quote_reads_in_reading_order_pieces_keep_stroke_order(conn):
    await _paper(conn)

    def at(q, chunk, text_start):
        return {**_piece(q, chunk), "text_start": text_start,
                "text_end": text_start + len(q)}

    m, saved = await create_mark(conn, _in([at("ahead of time", 4, 80)]))
    m2, _ = await create_mark(conn, _in([at("We predict", 4, 10)], join_to=m["id"]))
    # Stored in stroke order: indexes stay put for erase / restore / split.
    assert [p["quote"] for p in m2["pieces"]] == ["ahead of time", "We predict"]
    [b] = [x for x in await list_blocks(conn, saved.note_id) if x.id == m["block_id"]]
    # Shown (and read by the agent, searched) in reading order.
    assert b.text == "We predict … ahead of time"
    # Split still moves the newest piece.
    new = await split_last_piece(conn, m["id"])
    assert [p["quote"] for p in new["pieces"]] == ["We predict"]


async def test_pdf_quote_reads_by_page_then_top(conn):
    await _paper(conn)
    lower = {"quote": "lower", "page": 0, "rects": [[0.1, 0.5, 0.3, 0.52]]}
    upper = {"quote": "upper", "page": 0, "rects": [[0.1, 0.2, 0.3, 0.22]]}
    first = {"quote": "page one", "page": 1, "rects": [[0.1, 0.1, 0.3, 0.12]]}
    m, saved = await create_mark(conn, _in([first, lower, upper], view="pdf"))
    [b] = [x for x in await list_blocks(conn, saved.note_id) if x.id == m["block_id"]]
    assert b.text == "upper\nlower\npage one"  # one save


async def test_one_stroke_across_paragraphs_reads_as_paragraphs(conn):
    """"…" stands for skipped words only: the pieces of one selection or
    stroke across paragraphs are joined by a paragraph break; pieces of
    strokes joined later, by "…"."""
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("end of one", 1), _piece("start of two", 2)]))
    assert [p["stroke"] for p in m["pieces"]] == [0, 0]
    m2, _ = await create_mark(conn, _in([_piece("later words", 3)], join_to=m["id"]))
    assert [p["stroke"] for p in m2["pieces"]] == [0, 0, 1]
    [b] = [x for x in await list_blocks(conn, saved.note_id) if x.id == m["block_id"]]
    assert b.text == "end of one\nstart of two … later words"


async def test_restore_mark_brings_back_the_last_erased_piece(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in(
        [_piece("a"), _piece("b", 5), _piece("c", 9)]))
    await erase_piece(conn, m["id"], 0)
    await erase_piece(conn, m["id"], 2)
    await erase_piece(conn, m["id"], 1)  # the last live piece: the mark goes
    assert await list_marks(conn, 1) == []
    await restore_mark(conn, m["id"])
    [back] = await list_marks(conn, 1)
    assert [bool(p.get("erased")) for p in back["pieces"]] == [True, False, True]
    assert "erased_seq" not in back["pieces"][1]
    [b] = [x for x in await list_blocks(conn, saved.note_id) if x.id == m["block_id"]]
    assert b.text == "b"


async def test_restore_mark_with_legacy_erased_pieces_takes_the_last_by_index(conn):
    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("a"), _piece("b", 5)]))
    await erase_piece(conn, m["id"], 1)
    await erase_piece(conn, m["id"], 0)
    # Pieces erased before the erase order was recorded carry no sequence.
    rec = await marks_mod._get(conn, m["id"])
    rec["pieces"] = [{k: v for k, v in p.items() if k != "erased_seq"}
                     for p in rec["pieces"]]
    await marks_mod.save_pieces(conn, rec)
    await conn.commit()
    await restore_mark(conn, m["id"])
    [back] = await list_marks(conn, 1)
    assert [bool(p.get("erased")) for p in back["pieces"]] == [True, False]


async def test_block_undo_of_an_erase_delete_revives_a_piece(conn):
    from paperhub.notes.ops import restore_blocks

    await _paper(conn)
    m, saved = await create_mark(conn, _in([_piece("a"), _piece("b", 5)]))
    await erase_piece(conn, m["id"], 0)
    await erase_piece(conn, m["id"], 1)
    await restore_blocks(conn, saved.note_id, [m["block_id"]], actor="user")
    [back] = await list_marks(conn, 1)
    assert [bool(p.get("erased")) for p in back["pieces"]] == [True, False]
