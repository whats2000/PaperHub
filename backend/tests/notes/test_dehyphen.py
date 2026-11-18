"""A PDF line-end hyphen is joined only when the paper has the joined word
(H5b.1): the server knows the paper's words, the reader does not."""
import json
from collections import Counter
from pathlib import Path

import pytest

from paperhub.notes import dehyphen as dh
from paperhub.notes.dehyphen import dehyphen, paper_words
from paperhub.notes.locate import match_text
from paperhub.notes.marks import MarkIn, create_mark

pytestmark = pytest.mark.asyncio


async def test_join_keep_or_keep_hyphen_by_the_paper_s_words() -> None:
    words = Counter({"effective": 3, "self-supervised": 2})
    assert dehyphen("an effec-\ntive model", words) == "an effective model"
    assert dehyphen("is self-\nsupervised here", words) == "is self-supervised here"
    # Neither form in the paper: the hyphen stays, without the space.
    assert dehyphen("a multi-\nscale view", words) == "a multi-scale view"
    # Plain line breaks read as spaces; other hyphens are left alone.
    assert dehyphen("one\ntwo - three x-y", words) == "one two - three x-y"


async def test_matching_ignores_intra_word_hyphens() -> None:
    text = "We train it self-supervised on video. End."
    span, _ = match_text(text, "We train it self-\nsupervised on video")
    assert span is not None
    assert text[span[0]:span[1]] == "We train it self-supervised on video"


async def _html_paper(conn, tmp_path: Path, text: str) -> None:
    html = tmp_path / "source.html"
    html.write_text(f'<!DOCTYPE html><html><body><p><span id="phchunk-0"></span>{text}'
                    "</p></body></html>", encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', ?)",
        (json.dumps([{"name": "Intro"}]), str(html)))
    # The chunk text differs on purpose: an HTML paper's words come from
    # its reading text.
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, dom_id) "
        "VALUES (1, 'Intro', 0, 4, 'none', 'phchunk-0')")
    await conn.commit()


async def _pdf_paper(conn, tmp_path: Path, text: str) -> None:
    # Its HTML is read off the PDF too: the chunks are what count.
    html = tmp_path / "pdf.html"
    html.write_text("<html><body><p>nothing here</p></body></html>", encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, sha256, title, sections_json, "
        "source_path, source_dir_path, html_path) "
        "VALUES (2, 'pdf:2', 'pdf_upload', 'ab12', 'A PDF', ?, 'paper.pdf', 'd', ?)",
        (json.dumps([{"name": "Intro"}]), str(html)))
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (2, 'Intro', 0, ?, ?)", (len(text), text))
    await conn.commit()


def _mark(pcid: int, raw: str) -> MarkIn:
    return MarkIn(paper_content_id=pcid, kind="highlight", color="green", view="pdf",
                  pieces=[{"quote": "provisional", "raw": raw, "page": 0,
                           "rects": [[0.1, 0.1, 0.5, 0.2]]}], section_key="Intro")


async def test_create_mark_decides_the_join_from_the_reading_text(conn, tmp_path: Path) -> None:
    dh.clear_cache()
    await _html_paper(conn, tmp_path, "An effective and self-supervised model.")
    m, _ = await create_mark(conn, _mark(1, "an effec-\ntive and self-\nsupervised"))
    assert m["pieces"][0]["quote"] == "an effective and self-supervised"
    assert "raw" not in m["pieces"][0]


async def test_pdf_only_paper_uses_its_chunks(conn, tmp_path: Path) -> None:
    dh.clear_cache()
    await _pdf_paper(conn, tmp_path, "An effective and self-supervised model.")
    words = await paper_words(conn, 2)
    assert words["effective"] == 1 and words["self-supervised"] == 1
    m, _ = await create_mark(conn, _mark(2, "an effec-\ntive and self-\nsupervised"))
    assert m["pieces"][0]["quote"] == "an effective and self-supervised"


async def test_word_cache_is_bounded_and_evicts_the_least_recently_used(conn) -> None:
    dh.clear_cache()
    for i in range(1, 10):
        await conn.execute(
            "INSERT INTO paper_content (id, content_key, kind, sha256, title, "
            "sections_json, source_path, source_dir_path, html_path) "
            "VALUES (?, ?, 'pdf_upload', ?, 't', '[]', 'p.pdf', 'd', '')",
            (100 + i, f"pdf:{100 + i}", f"sha{i}"))
    await conn.commit()
    for i in range(1, 9):
        await paper_words(conn, 100 + i)
    await paper_words(conn, 101)  # touched again: now the newest
    await paper_words(conn, 109)  # a ninth paper evicts 102, not 101
    assert len(dh._cache) == 8
    assert 101 in dh._cache and 102 not in dh._cache and 109 in dh._cache
