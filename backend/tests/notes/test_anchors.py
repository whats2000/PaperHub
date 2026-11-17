import json
from pathlib import Path

import pytest

from paperhub.notes.anchors import reanchor_paper
from paperhub.notes.marks import MarkIn, create_mark, list_marks
from paperhub.notes.store import list_blocks

TEXT_A = "Intro words here. The cat sat on the mat. More text follows."
TEXT_B = "Second chunk talks about gradient descent and loss."

# Re-anchoring reads the rendered HTML (the text the reader quoted), not the
# LaTeX in chunks.text: each chunk is a <p> opening with its phchunk span.
_HTML: dict[str, Path] = {}


def _html(paras):
    body = "".join(f'<p><span id="phchunk-{i}"></span>{t}</p>\n'
                   for i, t in enumerate(paras))
    return ("<!DOCTYPE html><html><head><title>t</title></head><body>\n"
            f"{body}</body></html>")


async def _paper(conn, chunks, html=None):
    path = _HTML["dir"] / "source.html"
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', ?)",
        (json.dumps([{"name": "Intro"}, {"name": "Method"}]), str(path)))
    return await _set_chunks(conn, chunks, html)


async def _set_chunks(conn, chunks, html=None, *, rendered=True):
    """Replace the paper's chunks (and its rendered HTML); return the new
    chunk ids in order. `rendered=False`: the HTML is not re-rendered yet
    (the chunks have no dom_id)."""
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = 1")
    pos, ids = 0, []
    for i, (section, text) in enumerate(chunks):
        cur = await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, "
            "char_end, text, dom_id) VALUES (1, ?, ?, ?, ?, ?)",
            (section, pos, pos + len(text), text,
             f"phchunk-{i}" if rendered else None))
        ids.append(cur.lastrowid)
        pos += len(text)
    await conn.commit()
    if rendered:
        (_HTML["dir"] / "source.html").write_text(
            html if html is not None else _html([t for _, t in chunks]),
            encoding="utf-8")
    return ids


@pytest.fixture(autouse=True)
def _html_dir(tmp_path):
    _HTML["dir"] = tmp_path


def _piece(quote, first, last, prefix="", suffix=""):
    """A reader-made piece: chunk ids + quote context, no text offsets."""
    return {"quote": quote, "chunk_start": first, "chunk_end": last,
            "prefix": prefix, "suffix": suffix}


async def _mark(conn, pieces, section="Intro", view="html"):
    m, _ = await create_mark(conn, MarkIn(
        paper_content_id=1, kind="highlight", color="green", view=view,
        pieces=pieces, section_key=section))
    return m


async def _block(conn, mark):
    [lm] = await list_marks(conn, 1)
    [b] = [b for b in await list_blocks(conn, lm["note_id"])
           if b.id == mark["block_id"]]
    return lm, b


async def test_reanchor_after_chunk_ids_move(conn):
    a, b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("gradient descent", b, b)])
    # Re-ingest: a new preamble chunk and brand-new chunk ids.
    pre, a2, b2 = await _set_chunks(
        conn, [("Intro", "New preamble. "), ("Intro", TEXT_A),
               ("Method", TEXT_B)])
    stats = await reanchor_paper(conn, 1)
    assert stats == {"ok": 0, "moved": 1, "lost": 0, "partly_lost": 0}
    lm, blk = await _block(conn, m)
    p = lm["pieces"][0]
    assert (p["chunk_start"], p["chunk_end"]) == (b2, b2)  # chunk IDS
    # The reading text: one space between paragraphs.
    joined = " ".join(["New preamble.", TEXT_A, TEXT_B])
    assert p["text_start"] == joined.index("gradient descent")
    assert p["text_end"] == p["text_start"] + len("gradient descent")
    assert p["quote"] == "gradient descent" and lm["status"] == "ok"
    assert blk.props["pieces"][0]["chunk_start"] == b2
    assert "anchorLost" not in blk.props
    assert await reanchor_paper(conn, 1) == {
        "ok": 1, "moved": 0, "lost": 0, "partly_lost": 0}  # idempotent
    assert a != a2 and pre


async def test_reanchor_quote_across_chunks_spans_first_to_last(conn):
    a, b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("More text follows. Second chunk", a, b)])
    a2, b2 = await _set_chunks(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    await reanchor_paper(conn, 1)
    lm, _ = await _block(conn, m)
    p = lm["pieces"][0]
    assert (p["chunk_start"], p["chunk_end"]) == (a2, b2)


async def test_reanchor_lost_keeps_quote(conn):
    a, b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("gradient descent", b, b)])
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", "Totally new.")])
    stats = await reanchor_paper(conn, 1)
    assert stats["lost"] == 1 and stats["ok"] == 0
    lm, blk = await _block(conn, m)
    assert lm["status"] == "lost"
    assert blk.props["anchorLost"] is True
    assert blk.props["quote"] == "gradient descent"
    # Found again later: the mark recovers.
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    assert (await reanchor_paper(conn, 1))["lost"] == 0
    lm, blk = await _block(conn, m)
    assert lm["status"] == "ok" and "anchorLost" not in blk.props


async def test_reanchor_partial_pieces(conn):
    a, b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("cat sat", a, a),
                           _piece("gradient descent", b, b)])
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", "Different.")])
    stats = await reanchor_paper(conn, 1)
    assert stats["partly_lost"] == 1
    lm, blk = await _block(conn, m)
    assert lm["status"] == "partly_lost"
    assert blk.props["piecesLost"] == 1 and "anchorLost" not in blk.props
    assert [p.get("lost") for p in lm["pieces"]] == [None, True]


async def test_reanchor_whitespace_and_repeat_disambiguation(conn):
    text = "alpha beta. first the cat. second the cat. end"
    [c] = await _paper(conn, [("Intro", text)])
    m = await _mark(conn, [_piece("the cat", c, c, "second ", ". end")])
    await _set_chunks(conn, [("Intro", "alpha  beta.\nfirst the cat. second "
                                       "the\n cat. end")])
    await reanchor_paper(conn, 1)
    lm, _ = await _block(conn, m)
    assert lm["pieces"][0]["text_start"] > 25  # the second occurrence


async def test_reanchor_leaves_pdf_marks_untouched(conn):
    await _paper(conn, [("Intro", TEXT_A)])
    pdf = {"quote": "cat sat", "page": 0, "block_id": "b1",
           "rects": [[0.1, 0.1, 0.2, 0.2]]}
    m = await _mark(conn, [pdf], view="pdf")
    await _set_chunks(conn, [("Intro", "nothing alike")])
    assert await reanchor_paper(conn, 1) == {
        "ok": 0, "moved": 0, "lost": 0, "partly_lost": 0}
    lm, blk = await _block(conn, m)
    assert lm["pieces"] == [{**pdf, "stroke": 0}] and lm["status"] == "ok"
    assert "anchorLost" not in blk.props


async def test_reanchor_finds_rendered_quote_with_percent_ref_and_math(conn):
    """The reader quotes rendered text ("89%", a ref's label, math as $TeX$);
    chunks.text is LaTeX (\%, \ref, \(...\)). Found after a re-render."""
    latex = r"The latency drops by 89\% (Table~\ref{tab:2}) for \(h_t\)."
    rendered = ('The latency drops by 89% (<a href="#tab2">Table 2</a>) for '
                '<span class="math inline">\(h_t\)</span>.')
    [c] = await _paper(conn, [("Intro", latex)], _html([rendered]))
    quote = "drops by 89% (Table 2) for $h_t$."
    m = await _mark(conn, [_piece(quote, c, c, "The latency ", "")])
    # Re-ingest + re-render: a new chunk before it, new ids.
    pre, c2 = await _set_chunks(
        conn, [("Intro", "Preamble."), ("Intro", latex)],
        _html(["Preamble.", rendered]))
    stats = await reanchor_paper(conn, 1)
    assert stats == {"ok": 0, "moved": 1, "lost": 0, "partly_lost": 0}
    lm, blk = await _block(conn, m)
    p = lm["pieces"][0]
    assert (p["chunk_start"], p["chunk_end"]) == (c2, c2)
    assert lm["status"] == "ok" and "anchorLost" not in blk.props
    assert pre


async def test_reanchor_waits_for_the_html_render(conn):
    """Right after a re-chunk the chunks have no spans yet: nothing is
    marked lost; the re-anchor after the re-render places the marks."""
    a, b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("gradient descent", b, b)])
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", TEXT_B)],
                      rendered=False)
    assert await reanchor_paper(conn, 1) == {
        "ok": 0, "moved": 0, "lost": 0, "partly_lost": 0}
    lm, _ = await _block(conn, m)
    assert lm["status"] == "ok" and lm["pieces"][0]["chunk_start"] == b
    a2, b2 = await _set_chunks(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    assert (await reanchor_paper(conn, 1))["moved"] == 1
    lm, _ = await _block(conn, m)
    assert lm["pieces"][0]["chunk_start"] == b2 and a != a2


# ---- Marks on a translation (H1 bilingual reading) ----------------------

LANG = "繁體中文"


async def _translation(conn, key, text, lang=LANG):
    await conn.execute(
        "INSERT INTO translations (paper_content_id, block_key, language, "
        "source_hash, memory_hash, text, model) VALUES (1, ?, ?, 'h', 'm', ?, "
        "'x') ON CONFLICT(paper_content_id, block_key, language) DO UPDATE "
        "SET text = excluded.text", (key, lang, text))
    await conn.commit()


async def _tr_mark(conn, piece, *, view="html", section="Intro"):
    m, _ = await create_mark(conn, MarkIn(
        paper_content_id=1, kind="highlight", color="purple", view=view,
        pieces=[piece], lang=LANG, source_ref=piece["source_ref"],
        section_key=section))
    return m


def _tr_piece(quote, orig, first, last, key="h:0"):
    return {**_piece(orig, first, last), "quote": quote, "orig": orig,
            "lang": LANG, "source_ref": key}


async def test_reanchor_translation_piece_follows_its_source_paragraph(conn):
    a, _b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    await _translation(conn, "h:0", "介紹文字。貓 坐在墊子上。更多文字。")
    m = await _tr_mark(conn, _tr_piece("貓坐在墊子上", TEXT_A, a, a))
    _pre, a2, _b2 = await _set_chunks(
        conn, [("Intro", "New preamble. "), ("Intro", TEXT_A),
               ("Method", TEXT_B)])
    await reanchor_paper(conn, 1)
    lm, blk = await _block(conn, m)
    p = lm["pieces"][0]
    # Anchored by the source paragraph; the translated words are kept.
    assert (p["chunk_start"], p["chunk_end"]) == (a2, a2)
    assert p["quote"] == "貓坐在墊子上" and p["orig"] == TEXT_A
    assert lm["status"] == "ok"
    assert blk.props["lang"] == LANG and blk.props["orig"] == TEXT_A
    assert blk.props["sourceRef"] == "h:0"
    assert "translationChanged" not in blk.props


async def test_reanchor_flags_a_changed_translation_and_keeps_the_quote(conn):
    [a] = await _paper(conn, [("Intro", TEXT_A)])
    await _translation(conn, "h:0", "貓坐在墊子上。")
    m = await _tr_mark(conn, _tr_piece("貓坐在墊子上", TEXT_A, a, a))
    await _translation(conn, "h:0", "一隻貓待在地毯上。")
    await reanchor_paper(conn, 1)
    lm, blk = await _block(conn, m)
    assert lm["status"] == "translation_changed"
    assert blk.props["translationChanged"] is True
    assert blk.props["quote"] == "貓坐在墊子上"
    # Idempotent, and back to ok once the words are there again.
    await reanchor_paper(conn, 1)
    assert (await _block(conn, m))[0]["status"] == "translation_changed"
    await _translation(conn, "h:0", "貓坐在墊子上。")
    await reanchor_paper(conn, 1)
    lm, blk = await _block(conn, m)
    assert lm["status"] == "ok" and "translationChanged" not in blk.props


async def test_reanchor_translation_check_reads_placeholders_as_any_text(conn):
    [a] = await _paper(conn, [("Intro", TEXT_A)])
    # The quote holds the math the reader showed where the cache has ⟦0⟧.
    await _translation(conn, "h:0", "損失 ⟦0⟧ 隨⟦BR⟧時間下降。")
    m = await _tr_mark(conn, _tr_piece("損失 $L$ 隨時間下降", TEXT_A, a, a))
    await reanchor_paper(conn, 1)
    assert (await _block(conn, m))[0]["status"] == "ok"


async def test_reanchor_translation_never_checked_without_a_cached_text(conn):
    [a] = await _paper(conn, [("Intro", TEXT_A)])
    m = await _tr_mark(conn, _tr_piece("貓坐在墊子上", TEXT_A, a, a))
    await reanchor_paper(conn, 1)
    assert (await _block(conn, m))[0]["status"] == "ok"


async def test_reanchor_checks_pdf_translation_marks_without_html(conn):
    """A PDF-only paper has no reading text; its translation marks are
    still checked against the translation cache."""
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, title, "
        "sections_json, source_path, source_dir_path, html_path, sha256) "
        "VALUES (1, 'pdf:1', 'pdf_upload', 'A Paper', '[]', 's', 'd', '', "
        "'abc')")
    await conn.commit()
    await _translation(conn, "p:b1", "貓坐在墊子上。")
    piece = {"quote": "貓坐在墊子上", "orig": "The cat sat on the mat.",
             "lang": LANG, "source_ref": "p:b1", "page": 0, "block_id": "b1",
             "box": [0.1, 0.1, 0.9, 0.2]}
    m = await _tr_mark(conn, piece, view="pdf")
    await _translation(conn, "p:b1", "完全不同的句子。")
    await reanchor_paper(conn, 1)
    lm, blk = await _block(conn, m)
    assert lm["status"] == "translation_changed"
    assert blk.props["translationChanged"] is True
    assert lm["pieces"][0]["block_id"] == "b1"


async def test_reanchor_relocates_a_pdf_translation_piece_by_its_original(conn):
    a, _b = await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    await _translation(conn, "p:b1", "梯度下降與損失。")
    piece = {"quote": "梯度下降與損失", "orig": "gradient descent and loss",
             "lang": LANG, "source_ref": "p:b1", "page": 0, "block_id": "b1",
             "box": [0.1, 0.1, 0.9, 0.2], "chunk_start": a, "chunk_end": a}
    m = await _tr_mark(conn, piece, view="pdf")
    _pre, _a2, b2 = await _set_chunks(
        conn, [("Intro", "Pre. "), ("Intro", TEXT_A), ("Method", TEXT_B)])
    await reanchor_paper(conn, 1)
    lm, _ = await _block(conn, m)
    p = lm["pieces"][0]
    assert (p["chunk_start"], p["chunk_end"]) == (b2, b2)
    assert lm["status"] == "ok"
