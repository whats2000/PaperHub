import json

import pytest

from paperhub.notes.marks import MarkIn, create_mark, list_marks
from paperhub.notes.store import list_blocks
from paperhub.notes.translated import in_translation, recheck_translation_marks

LANG = "繁體中文"


@pytest.mark.parametrize(("quote", "text"), [
    # Unrelated words never match, placeholder or not.
    ("完全不同的句子", "損失 ⟦0⟧ 隨時間下降。"),
    ("貓坐在墊子上", "一隻貓待在地毯上 ⟦1⟧。"),
    # A quote never starts or ends inside a placeholder.
    ("完全不同", "⟦0⟧"),
    ("隨時間下降。多的", "損失 ⟦0⟧ 隨時間下降。"),
    # Its words must appear in order, around the placeholder.
    ("損失下降", "損失 ⟦0⟧ 隨時間下降。"),
])
def test_in_translation_rejects_words_that_are_not_there(quote, text):
    assert in_translation(quote, text) is False


@pytest.mark.parametrize(("quote", "text", "math"), [
    # The reader leaves the HTML math out of the quote: the words around it.
    ("損失隨時間下降", "損失 ⟦0⟧ 隨時間下降。", None),
    # A citation the reader shows as text, between translated words.
    ("如[3]所示", "如⟦2⟧所示", None),
    # A PDF paragraph's formulas restored from its placeholder map.
    ("損失 $L_t$ 隨時間下降", "損失 ⟦0⟧ 隨⟦BR⟧時間下降。", ["L_t"]),
    ("貓 坐在墊子上", "貓坐在墊子上。", None),
])
def test_in_translation_finds_a_real_quote(quote, text, math):
    assert in_translation(quote, text, math) is True


def test_in_translation_with_the_map_rejects_other_math():
    assert in_translation("損失 $L_t$ 隨", "損失 ⟦0⟧ 隨", ["x^2"]) is False


async def _paper(conn, tmp_path):
    html = tmp_path / "source.html"
    html.write_text("<html><body><p><span id=\"phchunk-0\"></span>The cat sat."
                    "</p></body></html>", encoding="utf-8")
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', ?)",
        (json.dumps([{"name": "Intro"}]), str(html)))
    cur = await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, "
        "text, dom_id) VALUES (1, 'Intro', 0, 12, 'The cat sat.', 'phchunk-0')")
    await conn.commit()
    return cur.lastrowid


async def _cache(conn, text, key="h:0"):
    await conn.execute(
        "INSERT INTO translations (paper_content_id, block_key, language, "
        "source_hash, memory_hash, text, model) VALUES (1, ?, ?, 'h', 'm', ?, "
        "'x') ON CONFLICT(paper_content_id, block_key, language) DO UPDATE "
        "SET text = excluded.text", (key, LANG, text))
    await conn.commit()


async def test_recheck_after_a_cache_rewrite(conn, tmp_path):
    c = await _paper(conn, tmp_path)
    await _cache(conn, "貓 ⟦0⟧ 坐著。")
    m, _ = await create_mark(conn, MarkIn(
        paper_content_id=1, kind="highlight", color="green", view="html",
        pieces=[{"quote": "貓坐著", "orig": "The cat sat.", "lang": LANG,
                 "source_ref": "h:0", "chunk_start": c, "chunk_end": c}],
        lang=LANG, source_ref="h:0", section_key="Intro"))
    # Nothing changed yet: nothing to report.
    assert await recheck_translation_marks(conn, 1, ["h:0"], LANG) == []
    await _cache(conn, "那隻貓 ⟦0⟧ 待著。")
    assert await recheck_translation_marks(conn, 1, ["h:0"], "Traditional Chinese") == [m["id"]]
    [lm] = await list_marks(conn, 1)
    assert lm["status"] == "translation_changed"
    [b] = [b for b in await list_blocks(conn, lm["note_id"]) if b.id == m["block_id"]]
    assert b.props["translationChanged"] is True and b.props["quote"] == "貓坐著"
    # Another block or language is not this mark's.
    assert await recheck_translation_marks(conn, 1, ["h:1"], LANG) == []
    # Back: ok again.
    await _cache(conn, "貓 ⟦0⟧ 坐著。")
    assert await recheck_translation_marks(conn, 1, ["h:0"], LANG) == [m["id"]]
    assert (await list_marks(conn, 1))[0]["status"] == "ok"
