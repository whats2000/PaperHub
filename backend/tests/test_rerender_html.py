"""Tests for paperhub-rerender-html CLI (Plan D W6-3).

Exercises _rerender_one() in isolation against a tmp-path SQLite DB.
render_html is monkeypatched to write the marked source verbatim to out_path,
simulating pandoc passing sentinels through so the test runs without pandoc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import aiosqlite
import pytest
import pytest_asyncio

from paperhub.db.migrate import apply_schema

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

_TINY_TEX = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Hello world. This is the first chunk.

\section{Method}
Here is the method section text.
\end{document}
"""


@pytest_asyncio.fixture
async def test_db(tmp_path: Path) -> aiosqlite.Connection:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA foreign_keys = ON")
    await apply_schema(conn)
    return conn


_SEED_COUNTER = 0


async def _seed_paper_content(
    conn: aiosqlite.Connection,
    *,
    source_path: str,
    source_dir_path: str,
    kind: str = "arxiv",
) -> int:
    global _SEED_COUNTER
    _SEED_COUNTER += 1
    content_key = f"arxiv:rerender-test-{_SEED_COUNTER}"
    arxiv_id = f"rerender-{_SEED_COUNTER}"
    await conn.execute(
        "INSERT INTO paper_content "
        "(content_key, kind, arxiv_id, title, authors_json, abstract, "
        "source_path, source_dir_path, html_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            content_key,
            kind,
            arxiv_id,
            "Test Rerender Paper",
            "[]",
            "Abstract.",
            source_path,
            source_dir_path,
            str(Path(source_dir_path) / "source.html"),
        ),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _seed_chunks(
    conn: aiosqlite.Connection,
    pcid: int,
    char_starts: list[int],
) -> list[int]:
    """Insert synthetic chunks at the given char_starts and return their ids."""
    chunk_ids: list[int] = []
    for i, cs in enumerate(char_starts):
        await conn.execute(
            "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, ?, ?, ?, ?)",
            (pcid, f"Section{i}", cs, cs + 20, f"chunk {i} text"),
        )
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
        assert row is not None
        chunk_ids.append(int(row[0]))
    await conn.commit()
    return chunk_ids


async def _get_dom_ids(conn: aiosqlite.Connection, pcid: int) -> list[str | None]:
    """Return dom_id values for chunks of pcid, ordered by id."""
    async with conn.execute(
        "SELECT dom_id FROM chunks WHERE paper_content_id = ? ORDER BY id",
        (pcid,),
    ) as cur:
        rows = await cur.fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerender_sets_dom_ids_and_writes_html(
    test_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After _rerender_one, chunks gain phchunk-N dom_ids and source.html has
    the <span> anchors; no raw PHCHUNKANCHOR token survives in the output."""
    import paperhub.cli.rerender_html as rr_mod

    # Stub render_html to write the tex source verbatim so sentinels survive.
    def _fake_render_html(
        *,
        source: Path,
        kind: Literal["latex", "pdf"],
        out_path: Path,
        resource_dir: Path | None = None,
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return out_path

    monkeypatch.setattr(rr_mod, "render_html", _fake_render_html)

    # Set up a fake paper cache dir with a flattened .tex and the source .tex
    # (source_path).  Two subdirs mirror the real layout:
    #   cache_dir/source.flattened.tex   <- what we read
    #   cache_dir/source/main.tex        <- source_path (resource_dir parent)
    cache_dir = tmp_path / "cache" / "arxiv" / "rerender-test"
    source_subdir = cache_dir / "source"
    source_subdir.mkdir(parents=True)
    flat_path = cache_dir / "source.flattened.tex"
    flat_path.write_text(_TINY_TEX, encoding="utf-8")
    main_tex = source_subdir / "main.tex"
    main_tex.write_text(_TINY_TEX, encoding="utf-8")

    # Seed paper_content pointing at the source .tex.
    pcid = await _seed_paper_content(
        test_db,
        source_path=str(main_tex),
        source_dir_path=str(cache_dir),
    )
    # Seed two chunks at PROSE offsets (the body text). The LaTeX-safe mask
    # only injects at brace-depth-0, non-math, non-fragile-env positions, so a
    # chunk start must land in real prose — not at `\documentclass` (offset 0)
    # or inside a `\section{}` argument. (_TINY_TEX has no comments, so the
    # stripped→original offset map is the identity here.)
    await _seed_chunks(
        test_db,
        pcid,
        char_starts=[
            _TINY_TEX.index("Hello world"),
            _TINY_TEX.index("Here is the method"),
        ],
    )

    n_chunks, n_anchored = await rr_mod._rerender_one(pcid, conn=test_db)

    # (a) Both chunks processed.
    assert n_chunks == 2

    # (b) dom_ids set to phchunk-0, phchunk-1 in id order.
    dom_ids = await _get_dom_ids(test_db, pcid)
    assert dom_ids[0] == "phchunk-0"
    assert dom_ids[1] == "phchunk-1"

    # (c) source.html contains the span anchors and no raw sentinel tokens.
    html_path = cache_dir / "source.html"
    assert html_path.exists(), "source.html should have been written"
    html = html_path.read_text(encoding="utf-8")
    assert '<span id="phchunk-0"></span>' in html
    assert "PHCHUNKANCHOR" not in html, "raw sentinel token must not survive postprocess"

    # Return value agrees with dom_ids found.
    assert n_anchored == 2


@pytest.mark.asyncio
async def test_rerender_skips_pdf_paper(
    test_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A PDF-only paper (no source.flattened.tex) must be skipped.
    Its chunks keep dom_id=NULL and the function returns (0, 0)."""
    import paperhub.cli.rerender_html as rr_mod

    # Track calls to render_html — should never be called for a PDF paper.
    render_calls: list[object] = []

    def _spy_render_html(**kwargs: object) -> Path:
        render_calls.append(kwargs)
        raise AssertionError("render_html should not be called for PDF-only paper")

    monkeypatch.setattr(rr_mod, "render_html", _spy_render_html)

    # PDF paper: cache dir has NO source.flattened.tex.
    cache_dir = tmp_path / "cache" / "upload" / "pdfonly"
    cache_dir.mkdir(parents=True)
    pdf_path = cache_dir / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")  # non-empty, but no .tex

    pcid = await _seed_paper_content(
        test_db,
        source_path=str(pdf_path),
        source_dir_path=str(cache_dir),
        kind="pdf_upload",
    )
    # Seed a chunk -- dom_id should remain NULL after the skip.
    await _seed_chunks(test_db, pcid, char_starts=[0])

    n_chunks, n_anchored = await rr_mod._rerender_one(pcid, conn=test_db)

    assert (n_chunks, n_anchored) == (0, 0), "PDF paper should be skipped -> (0, 0)"
    assert not render_calls, "render_html must not be called for a PDF paper"

    # dom_id unchanged (still NULL).
    dom_ids = await _get_dom_ids(test_db, pcid)
    assert dom_ids == [None], "PDF paper chunk dom_id must stay NULL"


@pytest.mark.asyncio
async def test_rerender_missing_flattened_tex_skips(
    test_db: aiosqlite.Connection,
    tmp_path: Path,
) -> None:
    """If source_dir_path exists but source.flattened.tex is absent, skip."""
    import paperhub.cli.rerender_html as rr_mod

    cache_dir = tmp_path / "cache" / "arxiv" / "no-flat"
    cache_dir.mkdir(parents=True)
    # No source.flattened.tex written.

    pcid = await _seed_paper_content(
        test_db,
        source_path=str(cache_dir / "source" / "main.tex"),
        source_dir_path=str(cache_dir),
    )
    await _seed_chunks(test_db, pcid, char_starts=[0])

    n_chunks, n_anchored = await rr_mod._rerender_one(pcid, conn=test_db)

    assert (n_chunks, n_anchored) == (0, 0)
    dom_ids = await _get_dom_ids(test_db, pcid)
    assert dom_ids == [None]
