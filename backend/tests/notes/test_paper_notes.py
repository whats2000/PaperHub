import json

from paperhub.notes.models import Block
from paperhub.notes.ops import BlockIn, Op, apply_ops
from paperhub.notes.papers import (
    destination,
    ensure_section,
    paper_note,
    parts_of_section,
)
from paperhub.notes.store import get_note, list_blocks

SECTIONS = ["Intro", "Method", "Results"]


async def _paper(conn, sections=SECTIONS):
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', 'h')",
        (json.dumps([{"name": s} for s in sections]),))
    await conn.commit()


def _b(id, type):
    return Block(id=id, note_id=1, parent_id=None, type=type, props={},
                 content=None, text="", author="user", section_key=None,
                 changed_rev=1)


async def _add(conn, note_id, type, after, bid, text="t"):
    note = await get_note(conn, note_id)
    content = None if type == "divider" else [{"type": "text", "text": text}]
    res = await apply_ops(conn, note_id, base_revision=note.revision,
                          actor="user", ops=[Op(
                              op="insert", block_id=bid, after=after,
                              block=BlockIn(type=type, content=content))])
    assert bid in res.applied
    return bid


async def test_section_heading_created_in_paper_order(conn):
    await _paper(conn)
    note = await paper_note(conn, 1)
    assert note.title == "A Paper"
    m = await ensure_section(conn, note.id, 1, "Method")
    r = await ensure_section(conn, note.id, 1, "Results")
    i = await ensure_section(conn, note.id, 1, "Intro")
    assert await ensure_section(conn, note.id, 1, "Method") == m
    blocks = await list_blocks(conn, note.id)
    assert [b.id for b in blocks] == [i, m, r]
    assert [b.section_key for b in blocks] == SECTIONS
    assert blocks[0].props == {"title": "Intro"}


async def test_parts_split_only_at_dividers():
    blocks = [_b("s", "section"), _b("a", "paragraph"), _b("q", "paperQuote"),
              _b("h", "heading"), _b("t", "paragraph"), _b("d", "divider"),
              _b("u", "paragraph"), _b("s2", "section"), _b("z", "paragraph")]
    parts = parts_of_section(blocks, "s")
    assert [[b.id for b in p] for p in parts] == [
        ["s", "a", "q", "h", "t"], ["u"]]


async def _setup(conn):
    await _paper(conn)
    note = await paper_note(conn, 1)
    sec = await ensure_section(conn, note.id, 1, "Method")
    await _add(conn, note.id, "paragraph", sec, "a")
    await _add(conn, note.id, "divider", "a", "d")
    await _add(conn, note.id, "paragraph", "d", "b")
    await ensure_section(conn, note.id, 1, "Results")
    return note, sec


async def test_destination_uses_hint_part_in_same_section(conn):
    note, sec = await _setup(conn)
    assert await destination(conn, note.id, sec, "a") == ("a", 0, 2)
    assert await destination(conn, note.id, sec, "b") == ("b", 1, 2)


async def test_destination_falls_back_to_section_end(conn):
    note, sec = await _setup(conn)
    results = await ensure_section(conn, note.id, 1, "Results")
    assert await destination(conn, note.id, sec, None) == ("b", 1, 2)
    # a hint from another section does not count
    assert await destination(conn, note.id, sec, results) == ("b", 1, 2)
    assert await destination(conn, note.id, results, None) == (results, 0, 1)
