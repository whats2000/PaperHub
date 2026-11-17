"""Locating text read off a PDF page in the paper's reading text (H5b.1).

The LaTeX-compiled PDF of an HTML paper has no Marker blocks: a mark's
section and chunk ids, and Follow's section, come from finding the page's
text in the rendered HTML's reading text (``notes/html_text.py``)."""
import json
from pathlib import Path

import pytest

from paperhub.notes.anchors import reanchor_paper
from paperhub.notes.locate import MAX_QUERY_CHARS, locate_text, match_text, pdf_normalise, prepared
from paperhub.notes.marks import MarkIn, create_mark, list_marks
from paperhub.notes.store import get_note, list_blocks

pytestmark = pytest.mark.asyncio


@pytest.mark.filterwarnings("ignore")
def test_pdf_normalise_ligatures_hyphens_whitespace() -> None:
    assert pdf_normalise("We deﬁne  eﬃcient demon-\nstrations\n here") == (
        "We define efficient demonstrations here")


async def test_match_survives_ligatures_and_line_hyphens() -> None:
    text = "Intro. We define efficient demonstrations for robots. End."
    span, meta = match_text(text, "We deﬁne eﬃcient demon-\nstrations for robots")
    assert span is not None
    assert text[span[0]:span[1]] == "We define efficient demonstrations for robots"
    assert meta["matched"] is True


async def test_math_is_a_gap_on_both_sides() -> None:
    # The reading text has $TeX$; the PDF text layer has glyphs.
    text = "Let $h_t$ be the hidden state at step $t$ of the model, as before."
    span, _ = match_text(text, "Let h t be the hidden state at step t of the model")
    assert span is not None
    assert text[span[0]:span[1]].startswith("Let $h_t$ be")
    assert text[span[0]:span[1]].endswith("of the model")


async def test_unrelated_text_is_a_miss() -> None:
    span, meta = match_text("The cat sat on the mat.", "Gradient descent converges slowly here")
    assert span is None
    assert meta["matched"] is False


async def test_too_little_text_is_a_miss() -> None:
    span, meta = match_text("The cat sat on the mat.", "cat")
    assert span is None and meta["reason"] == "too_short"


def _html(paras: list[str]) -> str:
    body = "".join(f'<p><span id="phchunk-{i}"></span>{t}</p>\n' for i, t in enumerate(paras))
    return f"<!DOCTYPE html><html><body>\n{body}</body></html>"


async def _paper(conn, tmp_path: Path, chunks: list[tuple[str, str]]) -> list[int]:
    html = tmp_path / "source.html"
    html.write_text(_html([t for _, t in chunks]), encoding="utf-8")
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', ?)",
        (json.dumps([{"name": s} for s, _ in chunks]), str(html)))
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = 1")
    ids = []
    for i, (section, text) in enumerate(chunks):
        cur = await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, dom_id) "
            "VALUES (1, ?, 0, ?, ?, ?)", (section, len(text), text, f"phchunk-{i}"))
        ids.append(cur.lastrowid)
    await conn.commit()
    return ids


async def test_locate_gives_chunks_section_and_offsets(conn, tmp_path: Path) -> None:
    ids = await _paper(conn, tmp_path, [
        ("1 Introduction", "Robots learn from demonstrations."),
        ("2 Method", "We predict the agent pose ahead of time."),
    ])
    found, meta = await locate_text(conn, 1, "learn from demon-\nstrations. We predict the")
    assert found == {
        "chunk_start": ids[0], "chunk_end": ids[1], "chunk_ids": ids,
        "section": "1 Introduction",
        "text_start": found["text_start"], "text_end": found["text_end"],
    }
    assert meta["source"] == "html_text" and meta["matched"] is True
    missing, meta = await locate_text(conn, 1, "Nothing like this is in the paper at all")
    assert missing is None and meta["matched"] is False


async def test_locate_without_rendered_html_is_a_miss(conn) -> None:
    found, meta = await locate_text(conn, 42, "Robots learn from demonstrations")
    assert found is None and meta["reason"] == "no_reading_text"


async def test_reanchor_moves_a_pdf_piece_s_chunk_ids(conn, tmp_path: Path) -> None:
    ids = await _paper(conn, tmp_path, [
        ("1 Introduction", "Robots learn from demonstrations."),
        ("2 Method", "We predict the agent pose ahead of time."),
    ])
    piece = {"quote": "We predict the agent pose", "page": 0,
             "rects": [[0.1, 0.1, 0.5, 0.12]], "chunk_start": ids[1], "chunk_end": ids[1],
             "text_start": 0, "text_end": 1}
    m, _ = await create_mark(conn, MarkIn(
        paper_content_id=1, kind="highlight", color="green", view="pdf",
        pieces=[piece], section_key="2 Method"))
    new = await _paper(conn, tmp_path, [
        ("1 Introduction", "Robots learn from demonstrations. Robots also fail."),
        ("2 Method", "We predict the agent pose ahead of time."),
    ])
    await reanchor_paper(conn, 1)
    [lm] = await list_marks(conn, 1)
    [p] = lm["pieces"]
    assert (p["chunk_start"], p["chunk_end"]) == (new[1], new[1])
    assert p["rects"] == piece["rects"]  # the geometry is the page's own
    assert lm["status"] == "ok"


# ---- Fix round 2 ---------------------------------------------------------

def _long_reading(n: int) -> str:
    return " ".join(f"Sentence number {i} says thing {i * 7} about item {i * 13}." for i in range(n))


async def test_an_oversized_query_matches_head_and_tail_and_covers_it_all() -> None:
    reading = _long_reading(400)
    s0 = reading.index("Sentence number 10 ")
    e0 = reading.index("Sentence number 160 ") - 1  # ends "...item 2067."
    query = reading[s0:e0]
    assert len(query) > MAX_QUERY_CHARS
    span, meta = match_text(reading, query)
    assert span == (s0, e0 - 1)  # the closing "." is edge punctuation
    assert meta["matched"] is True and meta["split"] is True


async def test_an_oversized_query_whose_ends_do_not_line_up_is_a_miss() -> None:
    reading = _long_reading(400)
    head = reading[reading.index("Sentence number 300 "):reading.index("Sentence number 390 ")]
    tail = reading[reading.index("Sentence number 10 "):reading.index("Sentence number 100 ")]
    span, meta = match_text(reading, head + " " + tail)
    assert span is None and meta["reason"] == "ends_apart"


async def test_an_empty_or_all_math_query_is_too_short() -> None:
    assert match_text("The cat sat on the mat.", "")[1]["reason"] == "too_short"
    assert match_text("The cat sat on the mat.", "   \n ")[1]["reason"] == "too_short"
    span, meta = match_text("Let $x$ be $y$ here.", "∑ ∫ ≤ = + −")
    assert span is None and meta["reason"] == "too_short"


async def test_the_reading_words_are_prepared_once_per_file_version(tmp_path: Path) -> None:
    html = tmp_path / "p.html"
    html.write_text(_html(["Robots learn from demonstrations."]), encoding="utf-8")
    mtime = html.stat().st_mtime_ns
    a = prepared(str(html), mtime)
    assert prepared(str(html), mtime) is a  # cached with the index
    assert prepared(str(html), mtime + 1) is not a  # a re-render reads it again
    span, _ = match_text("", "Robots learn from demonstrations", words=a)
    assert span is not None


async def test_relocate_refreshes_the_note_block_and_drops_a_lost_place(conn, tmp_path: Path) -> None:
    ids = await _paper(conn, tmp_path, [
        ("1 Introduction", "Robots learn from demonstrations."),
        ("2 Method", "We predict the agent pose ahead of time."),
    ])
    found = {"quote": "We predict the agent pose", "page": 0, "rects": [[0.1, 0.1, 0.5, 0.12]],
             "chunk_start": ids[1], "chunk_end": ids[1], "text_start": 0, "text_end": 1}
    gone = {"quote": "Robots learn from", "page": 0, "rects": [[0.1, 0.2, 0.5, 0.22]],
            "chunk_start": ids[0], "chunk_end": ids[0], "text_start": 0, "text_end": 1}
    m1, _ = await create_mark(conn, MarkIn(paper_content_id=1, kind="highlight", color="green",
                                           view="pdf", pieces=[found], section_key="2 Method"))
    m2, _ = await create_mark(conn, MarkIn(paper_content_id=1, kind="quote", color=None,
                                           view="pdf", pieces=[gone], section_key="1 Introduction"))
    new = await _paper(conn, tmp_path, [
        ("1 Introduction", "Agents imitate experts."),
        ("2 Method", "We predict the agent pose ahead of time."),
    ])
    await reanchor_paper(conn, 1)
    marks = {m["id"]: m for m in await list_marks(conn, 1)}
    note_id = marks[m1["id"]]["note_id"]
    blocks = {b.id: b for b in await list_blocks(conn, note_id)}
    moved = blocks[m1["block_id"]].props["pieces"][0]
    assert (moved["chunk_start"], moved["chunk_end"]) == (new[1], new[1])
    lost = marks[m2["id"]]["pieces"][0]
    assert "chunk_start" not in lost and "text_start" not in lost
    assert "chunk_start" not in blocks[m2["block_id"]].props["pieces"][0]
    assert marks[m2["id"]]["status"] == "ok"  # the PDF geometry still marks it
    # A second run changes nothing: no new revision.
    before = (await get_note(conn, note_id)).revision
    await reanchor_paper(conn, 1)
    assert (await get_note(conn, note_id)).revision == before
