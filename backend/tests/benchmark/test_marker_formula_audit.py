"""The Marker formula audit: equation blocks → page crop + Marker LaTeX →
a vision verdict, a CSV for spot checks, and the rates."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import aiosqlite
import pymupdf
import pytest

from benchmark.marker_formula_audit import (
    EqBlock,
    EqVerdict,
    chunk_equations,
    crop_block,
    equation_blocks,
    latex_of,
    run_audit,
)
from paperhub.db.migrate import apply_schema


async def _workspace(tmp_path: Path) -> Path:
    cache = tmp_path / "cache" / "p"
    (cache / "asset").mkdir(parents=True)
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=612, height=792)
    page.insert_text((100, 120), "E = mc^2", fontsize=14)
    doc.save(cache / "paper-own.pdf")
    (cache / "asset" / "blocks.json").write_text(json.dumps({"blocks": [
        {"block_id": "/page/1/Equation/3", "block_type": "Equation", "page": 1,
         "bbox": [90.0, 100.0, 300.0, 130.0],
         "html": '<p block-type="Equation"><math display="block">a &lt; b + c^{2}</math></p>'},
        {"block_id": "/page/1/Text/4", "block_type": "Text", "page": 1,
         "bbox": [90.0, 140.0, 300.0, 160.0], "html": "<p>prose</p>"},
    ]}), encoding="utf-8")
    db = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO paper_content (content_key, kind, sha256, title, source_path, "
            "source_dir_path, html_path) VALUES ('sha256:p', 'pdf_upload', 'p', 'P', '/x', ?, '/x')",
            (str(cache),))
        # What the agent reads: a Marker chunk holding the equation (its own
        # transcription, from the ingest run) and an old chunk with no page.
        await conn.executemany(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text, "
            "page, bbox) VALUES (1, 's', 0, 10, ?, ?, ?)",
            [("Prose before.\n\n$$ a < b + c^{3} $$\n\nProse after.", 1,
              "[80.0, 90.0, 320.0, 170.0]"),
             ("$$ x = y $$", None, None)])
        await conn.commit()
    return db


def test_latex_of_unwraps_the_math_element() -> None:
    assert latex_of('<p><math display="block">a &lt; b \\quad (1)</math></p>') == (
        "a < b \\quad (1)")
    assert latex_of("<p>no math</p>") == ""


@pytest.mark.asyncio
async def test_equation_blocks_come_from_each_papers_blocks(tmp_path: Path) -> None:
    db = await _workspace(tmp_path)
    blocks = equation_blocks(db)
    assert len(blocks) == 1
    b = blocks[0]
    assert (b.paper, b.block_id, b.page) == (1, "/page/1/Equation/3", 1)
    assert b.bbox == (90.0, 100.0, 300.0, 130.0)
    assert b.latex == "a < b + c^{2}"
    assert b.pdf_path.name == "paper-own.pdf"


@pytest.mark.asyncio
async def test_chunk_equations_audit_what_the_agent_reads(tmp_path: Path) -> None:
    """A chunk's $$..$$ equation, located by the Marker equation block on the
    chunk's page inside its box; chunks without a page are not locatable."""
    db = await _workspace(tmp_path)
    eqs = chunk_equations(db)
    assert len(eqs) == 1
    e = eqs[0]
    assert e.latex == "a < b + c^{3}"          # the chunk's text, not the block's
    assert (e.paper, e.page, e.bbox) == (1, 1, (90.0, 100.0, 300.0, 130.0))
    assert e.block_id == "chunk:1#0"


@pytest.mark.asyncio
async def test_crop_block_renders_the_region(tmp_path: Path) -> None:
    db = await _workspace(tmp_path)
    png = crop_block(equation_blocks(db)[0], dpi=144)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = pymupdf.Pixmap(png)
    # 210 x 30 pt plus padding, at 2x.
    assert 420 <= img.width <= 460 and 60 <= img.height <= 100


@pytest.mark.asyncio
async def test_run_audit_writes_a_csv_and_the_rates(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(pdf)
    blocks = [EqBlock(paper=1, block_id=f"/page/0/Equation/{i}", page=0,
                      bbox=(10.0, 10.0, 200.0, 40.0), latex=f"x_{i}", pdf_path=pdf)
              for i in range(4)]
    verdicts = ["match", "minor", "wrong", "match"]

    async def judge(block: EqBlock, png: bytes) -> EqVerdict:
        i = int(block.block_id.rsplit("/", 1)[1])
        return EqVerdict(verdict=verdicts[i], error="" if verdicts[i] == "match" else "sub")  # type: ignore[arg-type]

    rates = await run_audit(blocks, judge=judge, out_dir=tmp_path / "out")
    assert rates == {"n": 4, "match": 0.5, "minor": 0.25, "wrong": 0.25, "failed": 0}
    rows = list(csv.DictReader((tmp_path / "out" / "audit.csv").open(encoding="utf-8")))
    assert [r["verdict"] for r in rows] == verdicts
    assert set(rows[0]) >= {"paper", "block_id", "page", "bbox", "latex", "verdict",
                            "error", "crop"}
    assert (tmp_path / "out" / rows[2]["crop"]).is_file()
