"""Re-chunk every paper_content row using the current chunker.

Preserves paper_content.id so membership (papers) + message history survive.
When the re-derived chunk spans match the stored rows, only section names are
updated in place (chunk ids, dom_id, page, bbox kept); rows are replaced only
when the boundaries really changed. Marker block-anchored chunks are left
alone. Idempotent — runs as many times as needed.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
import tiktoken

from paperhub.config import load_settings
from paperhub.db.section_rename import apply_section_renames
from paperhub.pipelines.chunker import Chunk, chunk_text, strip_latex_comments
from paperhub.pipelines.extract import extract_latex, extract_pdf_with_headings
from paperhub.pipelines.mathjax_macros import extract_macros_from_dir

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
                # disallowed_special=() — see paper_pipeline._build_sections_json.
                "token_count": len(_CL100K.encode(section_text, disallowed_special=())),
                "chunk_count": len(group),
            }
        )
    return json.dumps(entries)


@dataclass
class Derived:
    """Chunks re-derived from a paper's cached source (no DB writes)."""

    chunks: list[Chunk]
    extracted: str
    is_pdf: bool
    # The main .tex extraction picked (LaTeX only) — used to rewrite
    # source.flattened.tex + the stored source_path so the HTML re-render stays
    # in sync with the re-extracted body.
    latex_main_path: Path | None
    source_dir_path: str | None


async def derive_chunks(conn: aiosqlite.Connection, pcid: int) -> Derived | None:
    """Re-run extraction + chunking for one paper from its cached source.
    ``None`` (logged) when the source is missing or not re-derivable."""
    async with conn.execute(
        "SELECT kind, source_path, source_dir_path, title "
        "FROM paper_content WHERE id = ?",
        (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        _LOG.warning("pcid=%d: paper_content row missing — skipped", pcid)
        return None
    kind: str | None = row[0]
    source_path_raw: str | None = row[1]
    source_dir_path_raw: str | None = row[2]
    title: str = str(row[3] or "")

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
            return None
        if not source_dir.is_dir():  # noqa: ASYNC240 — sequential CLI; sync I/O is fine
            _LOG.warning(
                "pcid=%d: LaTeX source dir missing at %s — skipped", pcid, source_dir,
            )
            return None
        try:
            ext = extract_latex(source_dir)
        except FileNotFoundError as exc:
            _LOG.warning("pcid=%d: extract_latex failed: %s — skipped", pcid, exc)
            return None
        macros = extract_macros_from_dir(ext.main_path.parent, ext.preamble)
        return Derived(
            chunks=chunk_text(ext.flattened_text, macros=macros),
            extracted=ext.flattened_text, is_pdf=False,
            latex_main_path=ext.main_path, source_dir_path=source_dir_path_raw,
        )
    if kind == "pdf_upload":
        if source_path_raw is None:
            _LOG.warning("pcid=%d: pdf_upload has no source_path — skipped", pcid)
            return None
        pdf_path = Path(source_path_raw)
        if not pdf_path.exists():  # noqa: ASYNC240 — sequential CLI; sync I/O is fine
            _LOG.warning(
                "pcid=%d: PDF source missing at %s — skipped", pcid, pdf_path,
            )
            return None
        try:
            extracted, pdf_headings = extract_pdf_with_headings(pdf_path)
        except FileNotFoundError as exc:
            _LOG.warning("pcid=%d: extract_pdf failed: %s — skipped", pcid, exc)
            return None
        # Section boundaries from detected headings (single synthetic section
        # on no-headings) + strip_comments=False (PDF text isn't LaTeX).
        boundaries = pdf_headings if pdf_headings else [(title or "Full text", 0)]
        return Derived(
            chunks=chunk_text(extracted, sections=boundaries, strip_comments=False),
            extracted=extracted, is_pdf=True,
            latex_main_path=None, source_dir_path=source_dir_path_raw,
        )
    _LOG.warning("pcid=%d: unknown kind %r — skipped", pcid, kind)
    return None


StoredRow = tuple[int, int, int, str, int | None]


async def stored_chunk_rows(conn: aiosqlite.Connection, pcid: int) -> list[StoredRow]:
    """``(id, char_start, char_end, text, page)`` in document (id) order."""
    async with conn.execute(
        "SELECT id, char_start, char_end, text, page FROM chunks "
        "WHERE paper_content_id = ? ORDER BY id",
        (pcid,),
    ) as cur:
        return [
            (int(r[0]), int(r[1]), int(r[2]), str(r[3]), r[4])
            for r in await cur.fetchall()
        ]


def same_geometry(stored: list[StoredRow], chunks: list[Chunk]) -> bool:
    """True when re-derived chunks match the stored rows span-for-span."""
    return len(stored) == len(chunks) and all(
        (s[1], s[2], s[3]) == (c.char_start, c.char_end, c.text)
        for s, c in zip(stored, chunks, strict=True)
    )


async def _reingest_one(
    pcid: int,
    *,
    conn: aiosqlite.Connection,
    dry_run: bool,
) -> tuple[int, int]:
    """Re-chunk one paper_content row.

    Returns (chunks_before, chunks_after) for logging.
    chunks_before is 0 when the row is skipped (missing source_path).

    Chunk rows are referenced by id (message ``[chunk:N]`` citations, idea and
    slide chunk-id lists) and carry ``dom_id`` / ``page`` / ``bbox`` this CLI
    cannot recompute. So Marker block-anchored chunks (``page`` set) are left
    alone; when the re-derived spans match the stored rows, only section names
    change, in place, with the rename carried to every consumer; rows are
    replaced only when the boundaries really changed.
    """
    derived = await derive_chunks(conn, pcid)
    if derived is None:
        return (0, 0)
    stored = await stored_chunk_rows(conn, pcid)
    before = len(stored)
    chunks = derived.chunks
    if any(r[4] is not None for r in stored):
        _LOG.info(
            "pcid=%d: Marker block-anchored chunks — left unchanged "
            "(paperhub-repair-section-names fixes their names)", pcid,
        )
        return (before, before)
    if not chunks:
        _LOG.warning("pcid=%d: chunker produced zero chunks", pcid)
        return (before, 0)

    in_place = same_geometry(stored, chunks)
    if dry_run:
        if in_place:
            n = await _count_name_changes(conn, stored, chunks)
            _LOG.info("pcid=%d dry-run: spans unchanged; would rename %d chunk(s)", pcid, n)
        else:
            _LOG.info(
                "pcid=%d dry-run: would replace %d chunks with %d", pcid, before, len(chunks),
            )
        return (before, len(chunks))

    # Keep the HTML-render input in sync with the re-extracted body: the
    # Citation-Canvas re-render (paperhub-rerender-html) reads
    # <cache>/source.flattened.tex, so a reingest that corrects extraction
    # (e.g. a fixed main-file pick) must rewrite it too — otherwise the Canvas
    # keeps rendering the old (wrong) body while the DB chunks are correct.
    # Also correct the stored main-file path when extraction re-picked it (the
    # original ingest may have recorded a standalone fragment, arXiv:2406.07524).
    if derived.latex_main_path is not None and derived.source_dir_path is not None:
        (Path(derived.source_dir_path) / "source.flattened.tex").write_text(  # noqa: ASYNC240
            derived.extracted, encoding="utf-8",
        )
        await conn.execute(
            "UPDATE paper_content SET source_path = ? WHERE id = ?",
            (str(derived.latex_main_path), pcid),
        )
        await conn.commit()

    if in_place:
        rep = await apply_section_renames(
            conn, pcid, {s[0]: c.section for s, c in zip(stored, chunks, strict=True)},
        )
        if rep.chunks:
            _LOG.info("pcid=%d: spans unchanged; renamed sections in place: %s", pcid, rep)
        return (before, len(chunks))

    # Compute sections_json before delete (pure function; no I/O).
    sections_json = _build_sections_json(
        chunks, derived.extracted, strip_comments=not derived.is_pdf,
    )
    _LOG.warning(
        "pcid=%d: chunk boundaries changed — replacing %d rows (chunk ids change; "
        "re-render the Citation Canvas with paperhub-rerender-html)", pcid, before,
    )

    # Destructive delete then re-insert.
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = ?", (pcid,))
    await conn.commit()

    # Insert new chunks.
    for c in chunks:
        bbox_json = json.dumps(list(c.bbox)) if c.bbox is not None else None
        await conn.execute(
            "INSERT INTO chunks "
            "(paper_content_id, section, char_start, char_end, text, match_text, "
            "page, bbox) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pcid, c.section, c.char_start, c.char_end, c.text, c.match_text,
             c.page, bbox_json),
        )
    await conn.execute(
        "UPDATE paper_content SET sections_json = ? WHERE id = ?",
        (sections_json, pcid),
    )
    await conn.commit()

    return (before, len(chunks))


async def _count_name_changes(
    conn: aiosqlite.Connection, stored: list[StoredRow], chunks: list[Chunk],
) -> int:
    names = {s[0]: c.section for s, c in zip(stored, chunks, strict=True)}
    async with conn.execute(
        "SELECT id, section FROM chunks WHERE paper_content_id = "
        "(SELECT paper_content_id FROM chunks WHERE id = ?)",
        (stored[0][0],),
    ) as cur:
        return sum(1 for r in await cur.fetchall() if names.get(int(r[0]), r[1]) != r[1])


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-reingest",
        description=(
            "Re-chunk every paper_content row using the current chunker. "
            "Deletes chunks first; preserves paper_content.id so "
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
        help="Print what would happen without modifying the DB.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    settings = load_settings()
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
                    pcid, conn=conn, dry_run=args.dry_run
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
