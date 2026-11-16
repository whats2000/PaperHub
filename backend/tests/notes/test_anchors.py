import json

from paperhub.notes.anchors import reanchor_paper
from paperhub.notes.marks import MarkIn, create_mark, list_marks
from paperhub.notes.store import list_blocks

TEXT_A = "Intro words here. The cat sat on the mat. More text follows."
TEXT_B = "Second chunk talks about gradient descent and loss."


async def _paper(conn, chunks):
    await conn.execute(
        "INSERT INTO paper_content (id, content_key, kind, arxiv_id, title, "
        "sections_json, source_path, source_dir_path, html_path) "
        "VALUES (1, 'arxiv:1', 'arxiv', '1', 'A Paper', ?, 's', 'd', 'h')",
        (json.dumps([{"name": "Intro"}, {"name": "Method"}]),))
    await _set_chunks(conn, chunks)


async def _set_chunks(conn, chunks):
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = 1")
    pos = 0
    for section, text in chunks:
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, "
            "char_end, text) VALUES (1, ?, ?, ?, ?)",
            (section, pos, pos + len(text), text))
        pos += len(text)
    await conn.commit()


def _piece(quote, text, prefix="", suffix=""):
    s = text.index(quote)
    return {"quote": quote, "chunk_start": s, "chunk_end": s + len(quote),
            "prefix": prefix, "suffix": suffix}


async def _mark(conn, pieces, section="Intro"):
    m, _ = await create_mark(conn, MarkIn(
        paper_content_id=1, kind="highlight", color="green", view="html",
        pieces=pieces, section_key=section))
    return m


async def _block(conn, mark):
    [lm] = await list_marks(conn, 1)
    [b] = [b for b in await list_blocks(conn, lm["note_id"])
           if b.id == mark["block_id"]]
    return lm, b


async def test_reanchor_after_offsets_move(conn):
    await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("gradient descent", TEXT_A + TEXT_B)])
    # Re-ingest: a new preamble chunk shifts every offset and chunk id.
    await _set_chunks(conn, [("Intro", "New preamble. "), ("Intro", TEXT_A),
                             ("Method", TEXT_B)])
    stats = await reanchor_paper(conn, 1)
    assert stats == {"ok": 0, "moved": 1, "lost": 0, "partly_lost": 0}
    lm, b = await _block(conn, m)
    p = lm["pieces"][0]
    joined = "\n".join(["New preamble. ", TEXT_A, TEXT_B])
    assert p["chunk_start"] == joined.index("gradient descent")
    assert p["chunk_end"] == p["chunk_start"] + len("gradient descent")
    assert p["quote"] == "gradient descent" and lm["status"] == "ok"
    assert "anchorLost" not in b.props
    assert await reanchor_paper(conn, 1) == {
        "ok": 1, "moved": 0, "lost": 0, "partly_lost": 0}  # idempotent


async def test_reanchor_lost_keeps_quote(conn):
    await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    m = await _mark(conn, [_piece("gradient descent", TEXT_A + TEXT_B)])
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", "Totally new.")])
    stats = await reanchor_paper(conn, 1)
    assert stats["lost"] == 1 and stats["ok"] == 0
    lm, b = await _block(conn, m)
    assert lm["status"] == "lost"
    assert b.props["anchorLost"] is True
    assert b.props["quote"] == "gradient descent"
    # Found again later: the mark recovers.
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    assert (await reanchor_paper(conn, 1))["lost"] == 0
    lm, b = await _block(conn, m)
    assert lm["status"] == "ok" and "anchorLost" not in b.props


async def test_reanchor_partial_pieces(conn):
    await _paper(conn, [("Intro", TEXT_A), ("Method", TEXT_B)])
    both = TEXT_A + TEXT_B
    m = await _mark(conn, [_piece("cat sat", both),
                           _piece("gradient descent", both)])
    await _set_chunks(conn, [("Intro", TEXT_A), ("Method", "Different.")])
    stats = await reanchor_paper(conn, 1)
    assert stats["partly_lost"] == 1
    lm, b = await _block(conn, m)
    assert lm["status"] == "partly_lost"
    assert b.props["piecesLost"] == 1 and "anchorLost" not in b.props
    assert [p.get("lost") for p in lm["pieces"]] == [None, True]


async def test_reanchor_whitespace_and_repeat_disambiguation(conn):
    text = "alpha beta. first the cat. second the cat. end"
    await _paper(conn, [("Intro", text)])
    m = await _mark(conn, [{"quote": "the cat", "chunk_start": 0,
                            "chunk_end": 7, "prefix": "second ",
                            "suffix": ". end"}])
    await _set_chunks(conn, [("Intro", "alpha  beta.\nfirst the cat. second "
                                       "the\n cat. end")])
    await reanchor_paper(conn, 1)
    lm, _ = await _block(conn, m)
    p = lm["pieces"][0]
    assert p["chunk_start"] > 25  # the second occurrence, not the first
