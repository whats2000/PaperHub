"""Mark kinds vs. drawing styles, and the user's colour palette."""
import json

import pytest

from paperhub.notes.mark_colors import color_problem
from paperhub.notes.marks import (
    MarkIn,
    add_color,
    colors,
    create_mark,
    delete_color,
    list_marks,
    reorder_colors,
    set_color,
)
from paperhub.notes.papers import paper_note
from paperhub.notes.store import list_blocks


async def _paper(conn):
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', 'h')",
        (json.dumps([{"name": "Intro"}]),))
    await conn.commit()


def _in(**kw):
    base = dict(paper_content_id=1, kind="text", style="highlight",
                color="green", view="html", section_key="Intro",
                pieces=[{"quote": "q", "chunk_start": 0, "chunk_end": 1}])
    return MarkIn(**{**base, **kw})


@pytest.mark.parametrize("style", ["highlight", "underline", "wavy", "bar"])
async def test_text_mark_keeps_its_style(conn, style):
    await _paper(conn)
    m, saved = await create_mark(conn, _in(style=style))
    assert (m["kind"], m["style"]) == ("text", style)
    [b] = [b for b in await list_blocks(conn, saved.note_id)
           if b.type == "paperQuote"]
    assert b.props["kind"] == "text" and b.props["style"] == style
    [lm] = await list_marks(conn, 1)
    assert lm["style"] == style


async def test_underline_without_colour_is_allowed(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in(style="underline", color=None))
    assert m["color"] is None


async def test_highlight_needs_a_colour(conn):
    await _paper(conn)
    with pytest.raises(ValueError):
        await create_mark(conn, _in(style="highlight", color=None))


async def test_text_needs_a_known_style_and_objects_take_none(conn):
    await _paper(conn)
    with pytest.raises(ValueError):
        await create_mark(conn, _in(style="dotted"))
    with pytest.raises(ValueError):
        await create_mark(conn, _in(style=None))
    m, _ = await create_mark(conn, _in(kind="figure", style="underline"))
    assert m["style"] is None


async def test_legacy_kinds_are_read_as_text_styles(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in(kind="highlight", style=None))
    assert (m["kind"], m["style"]) == ("text", "highlight")
    q, _ = await create_mark(conn, _in(kind="quote", style=None, color=None))
    assert (q["kind"], q["style"]) == ("text", "underline")


async def test_join_needs_the_same_style(conn):
    await _paper(conn)
    m, _ = await create_mark(conn, _in())
    with pytest.raises(ValueError):
        await create_mark(conn, _in(style="wavy", join_to=m["id"]))


async def test_colours_carry_hex_and_order(conn):
    cs = await colors(conn)
    assert [(c["color"], c["hex"], c["builtin"]) for c in cs] == [
        ("green", "#2f9e63", True), ("blue", "#3b7dd8", True),
        ("pink", "#d6457e", True), ("purple", "#8b5cd6", True)]
    assert all(c["dark"].startswith("#") for c in cs)


async def test_add_rename_recolour_reorder_delete_custom_colour(conn):
    await _paper(conn)
    c = await add_color(conn, hex="#1f8a8a", name="  To verify ")
    assert c["name"] == "To verify" and not c["builtin"]
    assert [x["color"] for x in await colors(conn)][-1] == c["color"]
    await set_color(conn, c["color"], name="Check", hex="#2a6fb0")
    got = [x for x in await colors(conn) if x["color"] == c["color"]][0]
    assert (got["name"], got["hex"]) == ("Check", "#2a6fb0")
    order = [c["color"], "green", "blue", "pink", "purple"]
    await reorder_colors(conn, order)
    assert [x["color"] for x in await colors(conn)] == order
    await delete_color(conn, c["color"])
    assert c["color"] not in [x["color"] for x in await colors(conn)]


async def test_a_colour_needs_a_unique_label(conn):
    for name in ("", "   ", "Method", "x" * 41):
        with pytest.raises(ValueError):
            await add_color(conn, hex="#1f8a8a", name=name)
    with pytest.raises(ValueError):
        await set_color(conn, "green", name=" ")


async def test_used_or_builtin_colour_cannot_be_deleted(conn):
    await _paper(conn)
    c = await add_color(conn, hex="#1f8a8a", name="Mine")
    await create_mark(conn, _in(color=c["color"]))
    with pytest.raises(ValueError):
        await delete_color(conn, c["color"])
    with pytest.raises(ValueError):
        await delete_color(conn, "green")


async def test_a_mark_needs_a_known_colour(conn):
    await _paper(conn)
    with pytest.raises(ValueError):
        await create_mark(conn, _in(color="c_nope"))


@pytest.mark.parametrize("hex_", ["#fde047", "#facc15", "#eab308", "#f5d020"])
def test_yellows_are_reserved_for_citations(hex_):
    assert "yellow" in (color_problem(hex_) or "")


@pytest.mark.parametrize("hex_", ["#ffffff", "#f4f4f4", "#7b7b7b", "#111111",
                                  "nothex"])
def test_too_pale_grey_or_dark_colours_are_rejected(hex_):
    assert color_problem(hex_)


@pytest.mark.parametrize("hex_", ["#2f9e63", "#3b7dd8", "#d6457e", "#8b5cd6",
                                  "#1f8a8a", "#e0702a"])
def test_good_colours_pass(hex_):
    assert color_problem(hex_) is None


async def test_note_shows_after_adding_mark(conn):
    await _paper(conn)
    await create_mark(conn, _in(style="bar", color=None))
    note = await paper_note(conn, 1)
    assert any(b.type == "paperQuote" for b in await list_blocks(conn, note.id))
