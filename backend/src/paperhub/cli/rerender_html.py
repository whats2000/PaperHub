"""Re-render HTML with chunk-start sentinel anchors for existing LaTeX papers.

Reads the flattened ``.tex`` source already stored in the paper cache, injects
``PHCHUNKANCHOR{N}END`` sentinel tokens at each chunk's ``char_start``, runs the
render pipeline, post-processes the resulting HTML to replace each surviving
token with ``<span id="phchunk-N">``, and writes ``dom_id`` back to the
``chunks`` table.

Does NOT re-chunk, re-embed, or change chunk ids — existing message citations
keep working.  Only LaTeX papers (those that have ``source.flattened.tex``)
are processed; PDF-only papers are skipped.

Usage::

    uv run paperhub-rerender-html                        # all LaTeX papers
    uv run paperhub-rerender-html --paper-content-id 7  # just one
    uv run paperhub-rerender-html --db /path/to/custom.db
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import aiosqlite

from paperhub.config import load_settings
from paperhub.pipelines.chunker import map_stripped_offsets_to_original
from paperhub.pipelines.extract import extract_latex
from paperhub.pipelines.figures import (
    rasterize_and_normalize_figures,
    strip_includegraphics_options,
)
from paperhub.pipelines.mathjax_macros import MacroValue, extract_macros_from_dir
from paperhub.pipelines.renderer import render_html
from paperhub.pipelines.sentinels import inject_sentinels, postprocess_sentinels
from paperhub.pipelines.table_figures import rasterize_complex_tables
from paperhub.pipelines.tikz_figures import rasterize_tikz_figures

_LOG = logging.getLogger("paperhub.rerender_html")


async def _rerender_one(
    pcid: int,
    *,
    conn: aiosqlite.Connection,
) -> tuple[int, int]:
    """Re-render HTML with sentinel anchors for one paper_content row.

    Returns ``(chunks_total, chunks_anchored)``.  Both are 0 when the paper
    is skipped (not a LaTeX paper or missing flattened source).
    """
    async with conn.execute(
        "SELECT source_path, source_dir_path FROM paper_content WHERE id = ?",
        (pcid,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        _LOG.warning("pcid=%d: paper_content row missing — skipped", pcid)
        return (0, 0)

    source_path_raw: str | None = row[0]
    source_dir_path_raw: str | None = row[1]

    # Resolve the cache dir.
    if source_dir_path_raw is not None:
        source_dir = Path(source_dir_path_raw)
    elif source_path_raw is not None:
        source_dir = Path(source_path_raw).parent
    else:
        _LOG.warning("pcid=%d: no source_dir_path or source_path — skipped", pcid)
        return (0, 0)

    # LaTeX detection: must have the flattened .tex produced at ingest time.
    flat_path = source_dir / "source.flattened.tex"
    if not flat_path.exists():  # noqa: ASYNC240 — sequential CLI; sync I/O is fine
        _LOG.debug("pcid=%d: no source.flattened.tex — PDF paper, skipped", pcid)
        return (0, 0)

    # resource_dir for figure resolution: the extracted source/ subtree, i.e.
    # the directory that contains the original main .tex file (source_path).
    # Fall back to the cache dir itself when source_path is absent.
    resource_dir = Path(source_path_raw).parent if source_path_raw is not None else source_dir

    # Load chunks in id order (= enumerate order from ingest).
    async with conn.execute(
        "SELECT id, char_start FROM chunks WHERE paper_content_id = ? ORDER BY id",
        (pcid,),
    ) as cur:
        chunk_rows: list[tuple[int, int]] = [
            (int(r[0]), int(r[1])) for r in await cur.fetchall()
        ]

    if not chunk_rows:
        _LOG.warning("pcid=%d: no chunks — skipped", pcid)
        return (0, 0)

    chunk_ids = [cid for cid, _ in chunk_rows]
    stripped_starts = [cs for _, cs in chunk_rows]

    # Build the sentinel-marked source identical to the ingest path: inject into
    # the RAW full_text (pandoc fails on comment-stripped LaTeX for some papers)
    # at offsets mapped from the stored stripped-coord chunk char_starts.
    full_text = flat_path.read_text(encoding="utf-8")  # noqa: ASYNC240
    starts = map_stripped_offsets_to_original(full_text, stripped_starts)
    marked, _injected = inject_sentinels(full_text, starts)

    # Recover author macros + paper preamble from the original source tree
    # (the body-only flattened .tex doesn't carry the preamble) so \vx,
    # \Ls, … render AND so rasterize_tikz_figures has the packages /
    # tikz libraries to build each TikZ env as a standalone. Best effort:
    # a failure still gets curated package macros via render_html and the
    # TikZ pass falls back to leaving envs as-is.
    macros: dict[str, MacroValue] | None = None
    preamble = ""
    try:  # noqa: ASYNC240 — sequential CLI; sync extract is fine
        ext = extract_latex(resource_dir)
        # Include macros from bundled .cls/.sty files, not just the main
        # preamble (arXiv:2407.15595 defines its math macros in fairmeta.cls).
        macros = extract_macros_from_dir(resource_dir, ext.preamble)
        preamble = ext.preamble
    except Exception:  # noqa: BLE001 — never block a re-render on macro recovery
        _LOG.debug("pcid=%d: preamble recovery failed", pcid, exc_info=True)

    # Pre-rasterise TikZ-drawn figures so pandoc embeds them as <img>
    # instead of dumping raw TikZ source (the survey taxonomy leak).
    marked = rasterize_tikz_figures(
        marked, preamble=preamble, out_dir=resource_dir,
    )
    # Rasterise pandoc-hostile tables (tabular*, \multirow, …) to images.
    marked = rasterize_complex_tables(
        marked, preamble=preamble, out_dir=resource_dir,
    )
    # Drop LaTeX width hints — pandoc would otherwise emit
    # style="width:50.0%" on figures using \\includegraphics[width=...]
    # and shrink them on the wide Citation Canvas.
    marked = strip_includegraphics_options(marked)

    render_source = source_dir / "source.render.tex"
    render_source.write_text(  # noqa: ASYNC240
        rasterize_and_normalize_figures(marked, resource_dir),
        encoding="utf-8",
    )

    html_path = source_dir / "source.html"
    render_html(
        source=render_source,
        kind="latex",
        out_path=html_path,
        resource_dir=resource_dir,
        macros=macros,
    )

    raw_html = html_path.read_text(encoding="utf-8")  # noqa: ASYNC240
    new_html, dom_map = postprocess_sentinels(raw_html)
    html_path.write_text(new_html, encoding="utf-8")  # noqa: ASYNC240

    # Update dom_id for each chunk by its positional ordinal (id order).
    for ordinal, cid in enumerate(chunk_ids):
        dom_id = dom_map.get(ordinal)
        await conn.execute(
            "UPDATE chunks SET dom_id = ? WHERE id = ?",
            (dom_id, cid),
        )
    await conn.commit()

    anchored = len(dom_map)
    _LOG.info(
        "pcid=%d: %d chunks, %d anchored",
        pcid,
        len(chunk_ids),
        anchored,
    )
    return (len(chunk_ids), anchored)


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="paperhub-rerender-html",
        description=(
            "Re-render HTML for existing LaTeX papers, injecting chunk-start "
            'sentinel anchors (<span id="phchunk-N">) and setting chunks.dom_id. '
            "Does NOT re-chunk or re-embed. PDF-only papers are skipped."
        ),
    )
    parser.add_argument(
        "--paper-content-id",
        type=int,
        default=None,
        help="Re-render just this paper_content.id (default: all LaTeX papers).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to the SQLite DB (default: from settings / PAPERHUB_WORKSPACE).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    db_path = Path(args.db) if args.db is not None else settings.db_path

    async with aiosqlite.connect(db_path) as conn:
        if args.paper_content_id is not None:
            ids = [args.paper_content_id]
        else:
            async with conn.execute(
                "SELECT id FROM paper_content ORDER BY id"
            ) as cur:
                ids = [int(r[0]) for r in await cur.fetchall()]

        _LOG.info("scanning %d paper(s)...", len(ids))
        total_processed = 0
        total_chunks = 0
        total_anchored = 0
        for pcid in ids:
            try:
                n_chunks, n_anchored = await _rerender_one(pcid, conn=conn)
                if n_chunks > 0:
                    total_processed += 1
                    total_chunks += n_chunks
                    total_anchored += n_anchored
            except Exception as exc:  # noqa: BLE001 — per-paper recovery
                _LOG.exception("pcid=%d failed: %s", pcid, exc)

    print(
        f"Done. papers processed: {total_processed}, "
        f"total chunks: {total_chunks}, "
        f"total anchored: {total_anchored}."
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
