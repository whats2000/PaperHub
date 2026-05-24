"""Re-chunk + re-embed every paper_content row using the current chunker.

Deletes chunks + Chroma vectors first; preserves paper_content.id so
membership (papers) + message history survive. Idempotent — runs as
many times as needed.

Usage:
    uv run paperhub-reingest                 # all paper_content rows
    uv run paperhub-reingest --paper-content-id 15   # just one
    uv run paperhub-reingest --dry-run       # print what would happen
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiosqlite
import tiktoken

from paperhub.config import load_settings
from paperhub.pipelines.chunker import Chunk, chunk_text, strip_latex_comments
from paperhub.pipelines.embedder import get_embedder
from paperhub.pipelines.extract import extract_latex, extract_pdf_with_headings
from paperhub.rag.chroma import ChromaStore

_LOG = logging.getLogger("paperhub.reingest")
_CL100K = tiktoken.get_encoding("cl100k_base")


def _build_sections_json(
    chunks: list[Chunk], full_text: str, *, strip_comments: bool = True,
) -> str:
    """Mirror of PaperPipeline._build_sections_json; kept local so the CLI
    has minimal coupling to PaperPipeline internals. ``strip_comments`` MUST
    match the value passed to ``chunk_text`` (LaTeX strips comments, PDF does
    not) so char offsets used for slicing align with the chunks."""
    stripped = strip_latex_comments(full_text) if strip_comments else full_text
    per_section: dict[str | None, list[Chunk]] = defaultdict(list)
    order: list[str] = []
    for c in chunks:
        if c.section is None:
            continue
        if c.section not in per_section:
            order.append(c.section)
        per_section[c.section].append(c)
    entries: list[dict[str, Any]] = []
    for name in order:
        group = per_section[name]
        section_text = stripped[group[0].char_start : group[-1].char_end]
        entries.append(
            {
                "name": name,
                "char_start": group[0].char_start,
                "char_end": group[-1].char_end,
                "token_count": len(_CL100K.encode(section_text)),
                "chunk_count": len(group),
            }
        )
    return json.dumps(entries)


async def _reingest_one(
    pcid: int,
    *,
    conn: aiosqlite.Connection,
    chroma: ChromaStore,
    dry_run: bool,
) -> tuple[int, int]:
    """Re-chunk + re-embed one paper_content row.

    Returns (chunks_before, chunks_after) for logging.
    chunks_before is 0 when the row is skipped (missing source_path).
    """
    async with conn.execute(
        "SELECT kind, source_path, source_dir_path, title "
        "FROM paper_content WHERE id = ?",
        (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        _LOG.warning("pcid=%d: paper_content row missing — skipped", pcid)
        return (0, 0)
    kind: str | None = row[0]
    source_path_raw: str | None = row[1]
    source_dir_path_raw: str | None = row[2]
    title: str = str(row[3] or "")
    # PDF heading boundaries (populated only for pdf_upload below).
    pdf_headings: list[tuple[str, int]] = []

    # CRITICAL: chunking must run on EXTRACTED body text, NOT raw source bytes.
    # For arxiv / latex_upload the chunker needs the flattened body (preamble
    # stripped, \input expanded, \section{...} headers preserved). For
    # pdf_upload it needs PyMuPDF-extracted plain text. Reading source_path
    # directly produces preamble-only chunks for arxiv papers (a few KB of
    # \usepackage declarations) and binary-decoded garbage for PDFs.
    if kind in ("arxiv", "latex_upload"):
        # The LaTeX source dir is the directory CONTAINING the main .tex
        # file — i.e. source_path's parent. This is robust across the
        # cache layout (`<root>/source/main.tex` → `<root>/source/`) and
        # flat fixtures (`arxiv_sample/main.tex` → `arxiv_sample/`), unlike
        # blindly appending "/source" to source_dir_path. Fall back to
        # source_dir_path when source_path is somehow absent.
        if source_path_raw is not None:
            source_dir = Path(source_path_raw).parent
        elif source_dir_path_raw is not None:
            source_dir = Path(source_dir_path_raw)
        else:
            _LOG.warning("pcid=%d: kind=%s has no source path — skipped", pcid, kind)
            return (0, 0)
        if not source_dir.is_dir():  # noqa: ASYNC240 — sequential CLI; sync I/O is fine
            _LOG.warning(
                "pcid=%d: LaTeX source dir missing at %s — skipped", pcid, source_dir,
            )
            return (0, 0)
        try:
            extracted = extract_latex(source_dir).flattened_text
        except FileNotFoundError as exc:
            _LOG.warning("pcid=%d: extract_latex failed: %s — skipped", pcid, exc)
            return (0, 0)
    elif kind == "pdf_upload":
        if source_path_raw is None:
            _LOG.warning("pcid=%d: pdf_upload has no source_path — skipped", pcid)
            return (0, 0)
        pdf_path = Path(source_path_raw)
        if not pdf_path.exists():  # noqa: ASYNC240 — sequential CLI; sync I/O is fine
            _LOG.warning(
                "pcid=%d: PDF source missing at %s — skipped", pcid, pdf_path,
            )
            return (0, 0)
        try:
            extracted, pdf_headings = extract_pdf_with_headings(pdf_path)
        except FileNotFoundError as exc:
            _LOG.warning("pcid=%d: extract_pdf failed: %s — skipped", pcid, exc)
            return (0, 0)
    else:
        _LOG.warning("pcid=%d: unknown kind %r — skipped", pcid, kind)
        return (0, 0)

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pcid,)
    ) as cur:
        before_row = await cur.fetchone()
    before = int(before_row[0]) if before_row else 0

    # PDF: section boundaries from detected headings (single synthetic section
    # on no-headings) + strip_comments=False (PDF text isn't LaTeX). LaTeX:
    # the \section{} regex path, unchanged.
    pdf_path_kind = kind == "pdf_upload"
    if pdf_path_kind:
        boundaries = pdf_headings if pdf_headings else [(title or "Full text", 0)]
        chunks = chunk_text(extracted, sections=boundaries, strip_comments=False)
    else:
        chunks = chunk_text(extracted)
    flattened = extracted  # alias for downstream sections_json computation
    if not chunks:
        _LOG.warning("pcid=%d: chunker produced zero chunks", pcid)
        return (before, 0)

    if dry_run:
        _LOG.info(
            "pcid=%d dry-run: would replace %d chunks with %d",
            pcid,
            before,
            len(chunks),
        )
        return (before, len(chunks))

    # Embed FIRST (idempotent if it fails — no mutation yet).
    embedder = get_embedder()
    embeddings = embedder.embed([c.text for c in chunks])

    # Compute sections_json before delete too (pure function; no I/O).
    sections_json = _build_sections_json(
        chunks, flattened, strip_comments=not pdf_path_kind,
    )

    # Only now do destructive deletes.
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = ?", (pcid,))
    chroma.delete_paper(pcid)
    await conn.commit()

    # Insert new chunks; capture auto-assigned ids.
    new_ids: list[int] = []
    for c in chunks:
        async with conn.execute(
            "INSERT INTO chunks "
            "(paper_content_id, section, char_start, char_end, text, match_text) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (pcid, c.section, c.char_start, c.char_end, c.text, c.match_text),
        ) as cur:
            r = await cur.fetchone()
            assert r is not None
            new_ids.append(int(r[0]))

    await conn.execute(
        "UPDATE paper_content SET sections_json = ? WHERE id = ?",
        (sections_json, pcid),
    )
    await conn.commit()

    # Insert Chroma vectors keyed by the new chunk ids.
    chroma.add_chunks(
        paper_content_id=pcid,
        chunk_ids=new_ids,
        texts=[c.text for c in chunks],
        embeddings=embeddings,
    )
    return (before, len(chunks))


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-reingest",
        description=(
            "Re-chunk + re-embed every paper_content row using the current chunker. "
            "Deletes chunks + Chroma vectors first; preserves paper_content.id so "
            "membership (papers) + message history survive. Idempotent."
        ),
    )
    parser.add_argument(
        "--paper-content-id",
        type=int,
        default=None,
        help="Re-ingest just this paper_content.id (default: all rows).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without modifying DB or Chroma.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    chroma = ChromaStore(settings.chroma_dir)
    async with aiosqlite.connect(settings.db_path) as conn:
        if args.paper_content_id is not None:
            ids = [args.paper_content_id]
        else:
            async with conn.execute(
                "SELECT id FROM paper_content ORDER BY id"
            ) as cur:
                ids = [int(r[0]) for r in await cur.fetchall()]
        _LOG.info("re-ingesting %d paper(s)...", len(ids))
        for pcid in ids:
            try:
                before, after = await _reingest_one(
                    pcid, conn=conn, chroma=chroma, dry_run=args.dry_run
                )
                _LOG.info("pcid=%d: %d chunks -> %d chunks", pcid, before, after)
            except Exception as exc:  # noqa: BLE001 — per-paper recovery
                _LOG.exception("pcid=%d failed: %s", pcid, exc)
                # Don't abort the whole run; move on to the next paper.
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
