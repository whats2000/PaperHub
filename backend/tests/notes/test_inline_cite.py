"""A short text mark lands in the note as an inline citation (H5b.1,
[user, 2026-11-21]): the same mark, another form."""
import json

from paperhub.notes.inline_cite import (
    INLINE_CITE_MAX_GRAPHEMES,
    grapheme_len,
    insert_cite,
    is_short_quote,
    remove_cite,
)
from paperhub.notes.marks import (
    CiteAt,
    MarkIn,
    create_mark,
    delete_mark,
    erase_piece,
    list_marks,
    restore_mark,
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
    base = dict(paper_content_id=1, kind="text", style="highlight",
                color="green", view="html", pieces=pieces, section_key="Method")
    return MarkIn(**{**base, **kw})


def _text(s):
    return {"type": "text", "text": s, "styles": {}}


async def _para(conn, block_id, runs, after=None, actor="user"):
    note = await paper_note(conn, 1)
    cur = await get_note(conn, note.id)
    await apply_ops(conn, note.id, base_revision=cur.revision, actor=actor,
                    ops=[Op(op="insert", block_id=block_id, after=after,
                            block=BlockIn(type="paragraph", content=runs))])
    return note.id


def _cites(block):
    return [r["props"]["markId"] for r in block.content or []
            if r.get("type") == "markCite"]


# ---- pure helpers -------------------------------------------------------

def test_short_is_24_graphemes_after_trim():
    assert INLINE_CITE_MAX_GRAPHEMES == 24
    assert is_short_quote("  62%  ")
    assert is_short_quote("x" * 24) and not is_short_quote("x" * 25)
    assert not is_short_quote("   ")


def test_grapheme_len_counts_what_a_reader_sees():
    assert grapheme_len("治療組反應率") == 6
    assert grapheme_len("é") == 1  # e + combining acute
    assert grapheme_len("👍🏽") == 1  # emoji + skin tone
    assert grapheme_len("👨‍👩‍👧") == 1  # ZWJ family
    assert grapheme_len("🇹🇼🇯🇵") == 2  # two flags
    assert is_short_quote("中" * 24) and not is_short_quote("中" * 25)


def test_insert_cite_at_an_offset_splits_the_text_run():
    run = {"type": "markCite", "props": {"markId": "mk_1", "text": "",
                                         "paperId": 1}}
    out, after = insert_cite([_text("ab😀cd")], 4, run)  # after the emoji
    assert out == [_text("ab😀"), run, _text("cd")]
    assert after == 5
    out, after = insert_cite([_text("ab")], None, run)  # no offset: the end
    assert out == [_text("ab"), run] and after == 3
    out, after = insert_cite([], 0, run)
    assert out == [run] and after == 1


def test_remove_cite_gives_back_where_it_was():
    run = {"type": "markCite", "props": {"markId": "mk_1", "text": "",
                                         "paperId": 1}}
    out, at = remove_cite([_text("ab"), run, _text("cd")], "mk_1")
    assert out == [_text("ab"), _text("cd")] and at == 2
    assert remove_cite([_text("ab")], "mk_1") == ([_text("ab")], None)


# ---- create -------------------------------------------------------------

async def test_short_text_mark_lands_as_a_chip_at_the_caret(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("rate , control")])
    m, saved = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=5)))
    blocks = await list_blocks(conn, note_id)
    assert [b.id for b in blocks] == ["p1"]  # no reference block
    p = blocks[0]
    assert p.content[0] == _text("rate ")
    assert p.content[1] == {"type": "markCite", "props": {
        "markId": m["id"], "text": "", "paperId": 1}}
    assert p.content[2] == _text(", control")
    assert m["cite"] == {"block_id": "p1", "offset": 6, "new_block": False}
    assert saved.note_id == note_id
    [lm] = await list_marks(conn, 1)
    assert lm["id"] == m["id"] and lm["cite_block_id"] == "p1"


async def test_chip_without_offset_goes_at_the_block_end(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("rate")])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=None)))
    [p] = await list_blocks(conn, note_id)
    assert _cites(p) == [m["id"]] and p.content[0] == _text("rate")
    assert m["cite"]["offset"] == 5


async def test_chip_with_no_caret_goes_at_the_end_of_the_last_edit(conn):
    # No caret this visit (a reload): the block the user last changed,
    # at its end. [user, 2026-11-22]
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("first")])
    await _para(conn, "p2", [_text("second")], after="p1")
    cur = await get_note(conn, note_id)
    await apply_ops(conn, note_id, base_revision=cur.revision, actor="user",
                    ops=[Op(op="update", block_id="p1", block=BlockIn(
                        type="paragraph", content=[_text("first!")]))])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id=None, offset=None)))
    blocks = await list_blocks(conn, note_id)
    assert [b.id for b in blocks] == ["p1", "p2"]
    assert _cites(blocks[0]) == [m["id"]]
    assert m["cite"] == {"block_id": "p1", "offset": 7, "new_block": False}


async def test_chip_in_a_note_never_edited_is_a_new_paragraph_at_the_end(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("first")], actor="agent")
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id=None, offset=None)))
    blocks = await list_blocks(conn, note_id)
    assert [b.type for b in blocks] == ["paragraph", "paragraph"]
    assert _cites(blocks[1]) == [m["id"]]
    assert m["cite"] == {"block_id": blocks[1].id, "offset": 1,
                         "new_block": True}


async def test_chip_after_a_reference_block_caret_is_a_new_paragraph(conn):
    await _paper(conn)
    first, _ = await create_mark(conn, _in([_piece("a long passage " * 3)]))
    m, _ = await create_mark(conn, _in(
        [_piece("62%", 99)], cite_at=CiteAt(block_id=first["block_id"])))
    note = await paper_note(conn, 1)
    blocks = await list_blocks(conn, note.id)
    assert [b.type for b in blocks] == ["paperQuote", "paragraph"]
    assert _cites(blocks[1]) == [m["id"]]


async def test_long_quote_stays_a_reference_block(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("x")])
    m, _ = await create_mark(conn, _in(
        [_piece("y" * 25)], cite_at=CiteAt(block_id="p1", offset=0)))
    blocks = await list_blocks(conn, note_id)
    assert [b.id for b in blocks] == ["p1", m["block_id"]]
    assert _cites(blocks[0]) == [] and "cite" not in m


async def test_figure_marks_are_always_blocks(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("x")])
    m, _ = await create_mark(conn, _in(
        [{"quote": "Fig. 1", "figure_id": "fig-001"}], kind="figure",
        style=None, color=None, cite_at=CiteAt(block_id="p1", offset=0)))
    blocks = await list_blocks(conn, note_id)
    assert [b.type for b in blocks] == ["paragraph", "paperFigure"]
    assert blocks[1].id == m["block_id"]


# ---- undo / redo of the create ------------------------------------------

async def test_undo_of_a_short_mark_removes_its_chip_and_redo_restores(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("ab")])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=1)))
    await delete_mark(conn, m["id"], remove_cite=True)
    assert await list_marks(conn, 1) == []
    [p] = await list_blocks(conn, note_id)
    assert _cites(p) == [] and p.text == "ab"
    await restore_mark(conn, m["id"], cite_at=CiteAt(block_id="p1", offset=1))
    [p] = await list_blocks(conn, note_id)
    assert p.content[1]["props"]["markId"] == m["id"]
    assert [x["id"] for x in await list_marks(conn, 1)] == [m["id"]]


async def test_undo_drops_the_paragraph_made_for_the_chip(conn):
    await _paper(conn)
    # The agent's paragraph: never edited by the user, so the chip makes one.
    note_id = await _para(conn, "p1", [_text("first")], actor="agent")
    m, _ = await create_mark(conn, _in([_piece("62%")], cite_at=CiteAt()))
    made = m["cite"]["block_id"]
    await delete_mark(conn, m["id"], remove_cite=True, drop_block=made)
    assert [b.id for b in await list_blocks(conn, note_id)] == ["p1"]
    await restore_mark(conn, m["id"], cite_at=CiteAt(block_id=made, offset=0))
    blocks = await list_blocks(conn, note_id)
    assert [b.id for b in blocks] == ["p1", made]
    assert _cites(blocks[1]) == [m["id"]]


async def test_deleting_a_chip_mark_keeps_the_chip(conn):
    """Delete from the reader: the user's words stay (a dashed chip)."""
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("ab")])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=2)))
    await delete_mark(conn, m["id"])
    assert await list_marks(conn, 1) == []
    [p] = await list_blocks(conn, note_id)
    assert _cites(p) == [m["id"]]
    await restore_mark(conn, m["id"])  # no reference block comes back
    assert [b.id for b in await list_blocks(conn, note_id)] == ["p1"]
    assert len(await list_marks(conn, 1)) == 1


# ---- the editor's conversions --------------------------------------------

async def test_block_turned_into_a_chip_keeps_its_mark_on_restore(conn):
    """The editor deletes the reference block and adds the chip in one
    burst (the server deletes the mark with its block); the reader then
    restores it: the mark comes back without its block."""
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("rate")])
    m, _ = await create_mark(conn, _in([_piece("62%")]))  # a block
    cur = await get_note(conn, note_id)
    await apply_ops(conn, note_id, base_revision=cur.revision, actor="user",
                    ops=[Op(op="delete", block_id=m["block_id"]),
                         Op(op="update", block_id="p1", block=BlockIn(
                             type="paragraph", content=[
                                 _text("rate "),
                                 {"type": "markCite", "props": {
                                     "markId": m["id"], "text": "",
                                     "paperId": 1}}]))])
    assert await list_marks(conn, 1) == []
    await restore_mark(conn, m["id"])
    assert [b.id for b in await list_blocks(conn, note_id)] == ["p1"]
    [lm] = await list_marks(conn, 1)
    assert lm["id"] == m["id"] and lm["cite_block_id"] == "p1"


async def test_chip_turned_into_a_block_takes_the_marks_block_id(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("ab")])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=2)))
    [lm] = await list_marks(conn, 1)
    cur = await get_note(conn, note_id)
    await apply_ops(conn, note_id, base_revision=cur.revision, actor="user",
                    ops=[Op(op="update", block_id="p1", block=BlockIn(
                        type="paragraph", content=[_text("ab")])),
                         Op(op="insert", block_id=m["block_id"], after="p1",
                            block=BlockIn(type="paperQuote",
                                          props=lm["block_props"]))])
    blocks = await list_blocks(conn, note_id)
    assert [b.id for b in blocks] == ["p1", m["block_id"]]
    [lm] = await list_marks(conn, 1)
    assert lm["cite_block_id"] is None
    # Its pieces now update the block again.
    await create_mark(conn, _in([_piece("more", 9)], join_to=m["id"]))
    assert (await list_blocks(conn, note_id))[1].text == "62% … more"


async def test_a_chip_mark_joins_erases_and_splits(conn):
    await _paper(conn)
    note_id = await _para(conn, "p1", [_text("ab")])
    m, _ = await create_mark(conn, _in(
        [_piece("62%")], cite_at=CiteAt(block_id="p1", offset=2)))
    j, _ = await create_mark(conn, _in([_piece("41%", 9)], join_to=m["id"]))
    assert len(j["pieces"]) == 2
    out = await erase_piece(conn, m["id"], 1)
    assert out["pieces"][1]["erased"] is True
    await create_mark(conn, _in([_piece("7%", 20)], join_to=m["id"]))
    new = await split_last_piece(conn, m["id"])
    blocks = await list_blocks(conn, note_id)
    # The split piece is a reference block after the chip's paragraph.
    assert [b.id for b in blocks] == ["p1", new["block_id"]]
    assert _cites(blocks[0]) == [m["id"]]
