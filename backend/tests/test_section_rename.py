"""In-place section renames + their propagation (the section-name migration)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.cli.reingest import _reingest_one
from paperhub.cli.repair_section_names import plan_paper
from paperhub.db.migrate import apply_schema
from paperhub.db.section_rename import (
    apply_section_renames,
    rename_card_json,
    rename_cite_lines,
    rename_digest,
    rename_sections_json,
    rename_source_sections,
    repair_stored_names,
)
from paperhub.ideation.cards import digest_hash
from paperhub.models.slide_domain import PaperDigest
from paperhub.pipelines.chunker import chunk_text

# --- pure helpers ---------------------------------------------------------------


def test_rename_sections_json_merges_entries_that_now_share_a_name() -> None:
    raw = json.dumps([
        {"name": "Methods", "char_start": 0, "char_end": 10, "token_count": 3, "chunk_count": 1},
        {"name": "--genomeDir x \\", "char_start": 11, "char_end": 20,
         "token_count": 2, "chunk_count": 1},
        {"name": "Funding", "char_start": 21, "char_end": 30, "token_count": 1, "chunk_count": 1},
    ])
    out = json.loads(rename_sections_json(raw, {"--genomeDir x \\": "Methods"}) or "[]")
    assert out == [
        {"name": "Methods", "char_start": 0, "char_end": 20, "token_count": 5, "chunk_count": 2},
        {"name": "Funding", "char_start": 21, "char_end": 30, "token_count": 1, "chunk_count": 1},
    ]


def test_rename_card_json_renames_and_dedupes_every_sections_list() -> None:
    card = {
        "problem": {"text": "p", "sections": ["Introduction"]},
        "mechanism": {"text": "m", "sections": ["\\molmoactt", "\\molmothink", "MolmoAct2"]},
        "limitations": [{"text": "l", "sections": ["\\molmothink"]}],
        "domains": ["Robotics"],
    }
    mapping = {"\\molmoactt": "MolmoAct2", "\\molmothink": "MolmoAct2-Think"}
    out = json.loads(rename_card_json(json.dumps(card), mapping))
    assert out["mechanism"]["sections"] == ["MolmoAct2", "MolmoAct2-Think"]
    assert out["limitations"][0]["sections"] == ["MolmoAct2-Think"]
    assert out["problem"]["sections"] == ["Introduction"]
    unchanged = json.dumps(card)
    assert rename_card_json(unchanged, {"Nope": "X"}) == unchanged


def test_rename_digest_joins_insights_of_merged_sections() -> None:
    digest = {"paper_id": 1, "title": "t", "abstract": "a", "sections": [
        {"name": "Methods", "insight": "A."},
        {"name": "frag one", "insight": "B."},
        {"name": "Funding", "insight": "C."},
    ]}
    out = rename_digest(digest, {"frag one": "Methods"})
    assert out["sections"] == [
        {"name": "Methods", "insight": "A. B."},
        {"name": "Funding", "insight": "C."},
    ]


def test_rename_source_sections_only_touches_the_paper_and_merges_chunk_ids() -> None:
    raw = json.dumps([
        {"paper_id": 15, "section_name": "\\molmoer", "chunk_ids": [1, 2]},
        {"paper_id": 15, "section_name": "Molmo2-ER", "chunk_ids": [2, 3]},
        {"paper_id": 16, "section_name": "\\molmoer", "chunk_ids": [9]},
    ])
    out = json.loads(rename_source_sections(raw, 15, {"\\molmoer": "Molmo2-ER"}))
    assert out == [
        {"paper_id": 15, "section_name": "Molmo2-ER", "chunk_ids": [1, 2, 3]},
        {"paper_id": 16, "section_name": "\\molmoer", "chunk_ids": [9]},
    ]


def test_rename_cite_lines() -> None:
    tex = (
        "% cite: 25:\\modelname{; 7:Intro\n"
        "\\begin{frame}25:\\modelname{ in body\\end{frame}\n"
    )
    out = rename_cite_lines(tex, 25, {"\\modelname{": "GR00T N1 Foundation Model"})
    assert out.splitlines()[0] == "% cite: 25:GR00T N1 Foundation Model; 7:Intro"
    assert out.splitlines()[1] == tex.splitlines()[1]  # only cite lines change


def test_repair_stored_names_marker_patterns() -> None:
    names = [
        None, "B.3 Visualizing World Model Predictions", "(b) Reach with Cup",
        "(b) Reach with Cup", "A.4.1 Effect of Data Curation",
        "100 200 300 400 500 81 81.5 82 VM22M", "A.4.2 Effect of Long Training",
        "Literature on generative image augmentation for defect segmentation",
        "applications", "Methodology",
    ]
    assert repair_stored_names(names) == [
        None, "B.3 Visualizing World Model Predictions",
        "B.3 Visualizing World Model Predictions", "B.3 Visualizing World Model Predictions",
        "A.4.1 Effect of Data Curation", "A.4.1 Effect of Data Curation",
        "A.4.2 Effect of Long Training",
        "Literature on generative image augmentation for defect segmentation applications",
        "Literature on generative image augmentation for defect segmentation applications",
        "Methodology",
    ]


def test_repair_stored_names_keeps_a_fragment_with_nothing_to_inherit() -> None:
    assert repair_stored_names(["(a) panel", "Introduction"]) == ["(a) panel", "Introduction"]


# --- DB-level -----------------------------------------------------------------------

_TEX = (
    "\\documentclass{article}\n"
    "\\newcommand{\\algo}{MDLM}\n"
    "\\begin{document}\n"
    "\\section{Introduction}\nWe study masked diffusion language models in depth.\n\n"
    "\\section{\\algo{}}\nOur method masks tokens and denoises them step by step.\n"
    "\\end{document}\n"
)


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    c = await aiosqlite.connect(tmp_path / "t.db")
    await c.execute("PRAGMA foreign_keys = ON")
    await apply_schema(c)
    return c


async def _seed_latex_paper(conn: aiosqlite.Connection, tmp_path: Path) -> tuple[int, Path]:
    """A LaTeX paper stored the way the pre-fix chunker stored it."""
    cache = tmp_path / "cache"
    src = cache / "source"
    src.mkdir(parents=True)
    (src / "main.tex").write_text(_TEX, encoding="utf-8")
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, arxiv_id, title, source_path, "
        "source_dir_path, html_path) VALUES ('arxiv:x', 'arxiv', 'x', 'T', ?, ?, ?)",
        (str(src / "main.tex"), str(cache), str(cache / "source.html")),
    )
    pcid = int(cur.lastrowid or 0)
    body = _TEX.split("\\begin{document}\n", 1)[1].rsplit("\\end{document}", 1)[0]
    from paperhub.pipelines.extract import extract_latex
    flat = extract_latex(src).flattened_text
    assert body.strip() in flat
    for i, c in enumerate(chunk_text(flat)):  # no macros: the old `\algo{` name
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, dom_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pcid, c.section, c.char_start, c.char_end, c.text, f"phchunk-{i}"),
        )
    # The pre-fix chunker read the name up to the first `}`.
    await conn.execute(
        "UPDATE chunks SET section = '\\algo{' WHERE section = '\\algo{}'",
    )
    await conn.execute(
        "UPDATE paper_content SET sections_json = ? WHERE id = ?",
        (json.dumps([
            {"name": "Introduction", "char_start": 0, "char_end": 5,
             "token_count": 9, "chunk_count": 1},
            {"name": "\\algo{", "char_start": 6, "char_end": 9,
             "token_count": 9, "chunk_count": 1},
        ]), pcid),
    )
    digest = PaperDigest(paper_id=pcid, title="T", abstract="A", figures=[], sections=[
        {"name": "Introduction", "insight": "i"}, {"name": "\\algo{", "insight": "m"},
    ])  # type: ignore[list-item]
    (cache / "digest.json").write_text(digest.model_dump_json(), encoding="utf-8")
    await conn.execute(
        "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) VALUES (?, ?, ?)",
        (pcid, json.dumps({"mechanism": {"text": "m", "sections": ["\\algo{"]}}),
         digest_hash(digest)),
    )
    await conn.execute(
        "INSERT INTO ideas (source_doc, source_section, title) VALUES (?, '\\algo{', 'I')",
        (pcid,),
    )
    await conn.execute("INSERT INTO chat_sessions (id) VALUES (1)")
    tex_path = tmp_path / "deck.tex"
    tex_path.write_text(f"% cite: {pcid}:\\algo{{\n\\begin{{frame}}x\\end{{frame}}\n",
                        encoding="utf-8")
    await conn.execute("INSERT INTO decks (id, session_id, tex_path) VALUES (1, 1, ?)",
                       (str(tex_path),))
    await conn.execute(
        "INSERT INTO deck_slides (deck_id, slide_index, frame_tex, page_start, page_end, "
        "source_sections_json) VALUES (1, 0, ?, 1, 1, ?)",
        (f"\\begin{{frame}}\n% cite: {pcid}:\\algo{{\nx\\end{{frame}}",
         json.dumps([{"paper_id": pcid, "section_name": "\\algo{", "chunk_ids": [1]}])),
    )
    await conn.commit()
    return pcid, cache


async def _chunks(conn: aiosqlite.Connection, pcid: int) -> list[tuple[object, ...]]:
    async with conn.execute(
        "SELECT id, section, char_start, char_end, text, dom_id FROM chunks "
        "WHERE paper_content_id = ? ORDER BY id", (pcid,),
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_reingest_renames_in_place_and_propagates(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pcid, cache = await _seed_latex_paper(conn, tmp_path)
    before = await _chunks(conn, pcid)
    assert [r[1] for r in before] == ["Introduction", "\\algo{"]

    await _reingest_one(pcid, conn=conn, dry_run=False)

    after = await _chunks(conn, pcid)
    # ids, offsets, text and dom_id untouched; only the name changes.
    assert [(r[0], r[2], r[3], r[4], r[5]) for r in after] == [
        (r[0], r[2], r[3], r[4], r[5]) for r in before
    ]
    assert [r[1] for r in after] == ["Introduction", "MDLM"]

    async with conn.execute("SELECT sections_json FROM paper_content WHERE id=?", (pcid,)) as c:
        assert [e["name"] for e in json.loads((await c.fetchone())[0])] == ["Introduction", "MDLM"]
    digest = PaperDigest.model_validate_json((cache / "digest.json").read_text(encoding="utf-8"))
    assert [s.name for s in digest.sections] == ["Introduction", "MDLM"]
    async with conn.execute("SELECT card_json, digest_hash FROM mechanism_cards") as c:
        card_json, h = await c.fetchone()  # type: ignore[misc]
    assert json.loads(card_json)["mechanism"]["sections"] == ["MDLM"]
    assert h == digest_hash(digest)  # the card stays a cache hit
    async with conn.execute("SELECT source_section FROM ideas") as c:
        assert (await c.fetchone())[0] == "MDLM"  # type: ignore[index]
    async with conn.execute("SELECT frame_tex, source_sections_json FROM deck_slides") as c:
        frame, sources = await c.fetchone()  # type: ignore[misc]
    assert f"% cite: {pcid}:MDLM" in frame
    assert json.loads(sources)[0]["section_name"] == "MDLM"
    assert f"% cite: {pcid}:MDLM" in (tmp_path / "deck.tex").read_text(encoding="utf-8")

    # Idempotent: a second run plans nothing and changes nothing.
    plan = await plan_paper(conn, pcid)
    assert plan.status == "unchanged"
    await _reingest_one(pcid, conn=conn, dry_run=False)
    assert await _chunks(conn, pcid) == after


@pytest.mark.asyncio
async def test_repair_plans_rederived_names_then_applies(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pcid, _ = await _seed_latex_paper(conn, tmp_path)
    plan = await plan_paper(conn, pcid)
    assert (plan.status, plan.method) == ("renamed", "rederived")
    assert plan.renames == {"\\algo{": "MDLM"}
    rep = await apply_section_renames(conn, pcid, plan.names)
    assert rep.chunks == 1 and rep.ideas == 1 and rep.deck_slides == 1
    assert (await plan_paper(conn, pcid)).status == "unchanged"


@pytest.mark.asyncio
async def test_marker_anchored_paper_uses_stored_rule_and_reingest_leaves_it(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pdf = tmp_path / "doc.pdf"
    shutil.copy(Path(__file__).parent / "fixtures" / "papers" / "sample.pdf", pdf)
    cur = await conn.execute(
        "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
        "source_dir_path, html_path) VALUES ('pdf:y', 'pdf_upload', 'y', 'T', ?, ?, ?)",
        (str(pdf), str(tmp_path), str(tmp_path / "source.html")),
    )
    pcid = int(cur.lastrowid or 0)
    for i, sec in enumerate(["B.3 Visualizing", "(b) Reach with Cup", "C Results"]):
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, page) "
            "VALUES (?, ?, ?, ?, ?, 1)", (pcid, sec, i * 10, i * 10 + 5, f"text {i}"),
        )
    await conn.commit()
    snapshot = await _chunks(conn, pcid)
    await _reingest_one(pcid, conn=conn, dry_run=False)
    assert await _chunks(conn, pcid) == snapshot  # Marker rows never re-chunked

    plan = await plan_paper(conn, pcid)
    assert (plan.status, plan.method) == ("renamed", "stored_rule")
    assert plan.renames == {"(b) Reach with Cup": "B.3 Visualizing"}


def test_rename_cite_lines_merges_sources_renamed_to_one_name() -> None:
    """Two stale names mapped to the same section must not leave a duplicate
    ``89:X; 89:X`` source (deck 181 slide 2614)."""
    tex = "% cite: 89:frag one; 89:frag two; 12:Intro\n"
    out = rename_cite_lines(tex, 89, {"frag one": "Abstract", "frag two": "Abstract"})
    assert out == "% cite: 89:Abstract; 12:Intro\n"
