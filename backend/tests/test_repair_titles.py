"""Title repair for PDF uploads whose title came from a bad page-1 pick."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pymupdf
import pytest_asyncio

from paperhub.cli.repair_titles import apply_title, plan_title, title_defects
from paperhub.db.migrate import apply_schema
from paperhub.ideation.cards import digest_hash
from paperhub.models.slide_domain import PaperDigest

_REAL = "A Review of Ghost Imaging via Sparsity Constraints"


def _pdf(path: Path, *, meta_title: str = "") -> Path:
    """MDPI-like first page: a lowercase wordmark above the real title."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()
    y = 72.0
    for text, size in [("applied", 19), ("sciences", 19), (_REAL, 17.9)] + [
        ("Different from conventional imaging methods, which acquire", 10),
    ] * 6:
        page.insert_text((72, y), text, fontsize=size)
        y += size + 8
    doc.set_metadata({"title": meta_title})  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    c = await aiosqlite.connect(tmp_path / "t.db")
    await apply_schema(c)
    return c


async def _seed(
    conn: aiosqlite.Connection, tmp_path: Path, title: str, *, kind: str = "pdf_upload",
    pdf: bool = True,
) -> tuple[int, Path]:
    cache = tmp_path / f"cache-{abs(hash(title))}"
    src = cache / "applsci-08-01379-v2.pdf"
    if pdf:
        _pdf(src)
    ident = ("arxiv_id", "x") if kind == "arxiv" else ("sha256", str(abs(hash(title))))
    cur = await conn.execute(
        f"INSERT INTO paper_content (content_key, kind, {ident[0]}, title, source_path, "  # noqa: S608
        "source_dir_path, html_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"k:{title}", kind, ident[1], title, str(src), str(cache), str(cache / "s.html")),
    )
    await conn.commit()
    return int(cur.lastrowid or 0), cache


def test_title_defects() -> None:
    assert title_defects("applied sciences", "x.pdf") == ["not_title_shaped"]
    assert title_defects("LIVECell�XA large-scale", "x.pdf") == ["garbled"]
    assert title_defects("s41592-021-01249-6", "s41592-021-01249-6.pdf") == ["filename_stem"]
    assert title_defects(_REAL, "x.pdf") == []


async def test_wordmark_title_is_rederived_and_propagated(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pcid, cache = await _seed(conn, tmp_path, "applied sciences")
    digest = PaperDigest(paper_id=pcid, title="applied sciences", abstract="", figures=[],
                         sections=[])
    (cache / "digest.json").write_text(digest.model_dump_json(), encoding="utf-8")
    await conn.execute(
        "INSERT INTO mechanism_cards (paper_content_id, card_json, digest_hash) VALUES (?, ?, ?)",
        (pcid, json.dumps({"mechanism": {"text": "m", "sections": []}}), digest_hash(digest)),
    )
    await conn.commit()

    plan = await plan_title(conn, pcid)
    assert (plan.status, plan.old, plan.new) == ("updated", "applied sciences", _REAL)

    await apply_title(conn, pcid, plan.new)
    async with conn.execute("SELECT title FROM paper_content WHERE id = ?", (pcid,)) as c:
        assert (await c.fetchone()) == (_REAL,)
    async with conn.execute(
        "SELECT rowid FROM paper_content_fts WHERE paper_content_fts MATCH 'ghost'",
    ) as c:
        assert [r[0] for r in await c.fetchall()] == [pcid]
    new_digest = PaperDigest.model_validate_json(
        (cache / "digest.json").read_text(encoding="utf-8"))
    assert new_digest.title == _REAL
    async with conn.execute("SELECT digest_hash FROM mechanism_cards") as c:
        assert (await c.fetchone()) == (digest_hash(new_digest),)  # card stays a cache hit

    assert (await plan_title(conn, pcid)).status == "unchanged"  # idempotent


async def test_filename_stem_title_is_replaced(
    conn: aiosqlite.Connection, tmp_path: Path,
) -> None:
    pcid, _ = await _seed(conn, tmp_path, "applsci-08-01379-v2")
    plan = await plan_title(conn, pcid)
    assert (plan.status, plan.new) == ("updated", _REAL)


async def test_typed_title_is_kept(conn: aiosqlite.Connection, tmp_path: Path) -> None:
    pcid, _ = await _seed(conn, tmp_path, "My GISC review")
    plan = await plan_title(conn, pcid)
    assert plan.status == "kept_typed_title"
    assert plan.new is None


async def test_arxiv_and_missing_pdf(conn: aiosqlite.Connection, tmp_path: Path) -> None:
    arxiv, _ = await _seed(conn, tmp_path, "Mamba: Linear-Time", kind="arxiv", pdf=False)
    assert (await plan_title(conn, arxiv)).status == "unchanged"
    gone, _ = await _seed(conn, tmp_path, "applied sciences", pdf=False)
    plan = await plan_title(conn, gone)
    assert plan.status == "flagged"
    assert plan.defects == ["not_title_shaped"]
