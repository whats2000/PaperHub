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
from functools import partial
from pathlib import Path
from typing import Any

import aiosqlite
import tiktoken

from paperhub.config import load_settings
from paperhub.pipelines.chunker import Chunk, chunk_text, strip_latex_comments
from paperhub.pipelines.embedder import get_embedder
from paperhub.rag.chroma import ChromaStore

_LOG = logging.getLogger("paperhub.reingest")
_CL100K = tiktoken.get_encoding("cl100k_base")


def _build_sections_json(chunks: list[Chunk], full_text: str) -> str:
    """Mirror of PaperPipeline._build_sections_json; kept local so the CLI
    has minimal coupling to PaperPipeline internals. Stripped text is the
    source-of-truth for char offsets — chunker already strips LaTeX
    comments internally."""
    stripped = strip_latex_comments(full_text)
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
        "SELECT source_path FROM paper_content WHERE id = ?", (pcid,)
    ) as cur:
        row = await cur.fetchone()
    if row is None or row[0] is None:
        _LOG.warning("pcid=%d: no source_path on row — skipped", pcid)
        return (0, 0)
    source_path = Path(row[0])
    exists = await asyncio.to_thread(source_path.exists)
    if not exists:
        _LOG.warning(
            "pcid=%d: source file missing at %s — skipped", pcid, source_path
        )
        return (0, 0)
    flattened = await asyncio.to_thread(
        partial(source_path.read_text, encoding="utf-8", errors="replace")
    )

    async with conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE paper_content_id = ?", (pcid,)
    ) as cur:
        before_row = await cur.fetchone()
    before = int(before_row[0]) if before_row else 0

    chunks = chunk_text(flattened)
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

    # Delete old data (chunks rows + Chroma vectors).
    await conn.execute("DELETE FROM chunks WHERE paper_content_id = ?", (pcid,))
    chroma.delete_paper(pcid)
    await conn.commit()

    # Embed all chunk texts in one call (batched internally by ChromaStore).
    embedder = get_embedder()
    embeddings = embedder.embed([c.text for c in chunks])

    # Insert new chunks; capture auto-assigned ids.
    new_ids: list[int] = []
    for c in chunks:
        async with conn.execute(
            "INSERT INTO chunks "
            "(paper_content_id, section, char_start, char_end, text) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (pcid, c.section, c.char_start, c.char_end, c.text),
        ) as cur:
            r = await cur.fetchone()
            assert r is not None
            new_ids.append(int(r[0]))

    sections_json = _build_sections_json(chunks, flattened)
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
