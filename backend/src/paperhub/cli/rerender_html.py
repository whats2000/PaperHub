"""Re-render every cached ``source.html`` with the current renderer.

The Citation Canvas renderer used to base64-inline figures, which produced
multi-MB / 70MB HTML files that OOM'd the iframe (arxiv:2605.02881). The
renderer now serves figures as files (relative ``asset/`` URLs), but already
-cached ``source.html`` artefacts are still the old bloated blobs. This command
re-runs ``render_html`` over each paper's on-disk cache (the figure-normalized
``source.render.tex`` + ``source/`` subtree are already present), rewriting
``source.html`` in place. Chunks/embeddings are untouched (use
``paperhub-reingest`` for those).

Usage::

    uv run python -m paperhub.cli.rerender_html              # all paper_content rows
    uv run python -m paperhub.cli.rerender_html --paper-content-id 15
    uv run python -m paperhub.cli.rerender_html --dry-run    # report only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.pipelines.renderer import render_html

_LOG = logging.getLogger("paperhub.rerender_html")


def _file_size(path_str: str | None) -> int:
    """Size of ``path_str`` in bytes, or 0 if absent. Sequential CLI: sync I/O ok."""
    if not path_str:
        return 0
    p = Path(path_str)
    return p.stat().st_size if p.is_file() else 0  # noqa: ASYNC240 — sequential CLI


def _rerender_one(
    *, source_path: str | None, source_dir_path: str | None, html_path: str | None,
) -> str:
    """Re-render one paper's source.html in place. Returns a one-word status."""
    if not html_path or not source_dir_path:
        return "skipped(no-paths)"
    cache_dir = Path(source_dir_path)
    out_path = Path(html_path)
    render_tex = cache_dir / "source.render.tex"

    if render_tex.is_file():
        # LaTeX: figures live next to the main .tex (source_path's parent),
        # mirroring paper_pipeline's render call.
        resource_dir = (
            Path(source_path).parent if source_path else cache_dir / "source"
        )
        render_html(
            source=render_tex, kind="latex", out_path=out_path,
            resource_dir=resource_dir,
        )
        return "rendered(latex)"
    if source_path and Path(source_path).suffix.lower() == ".pdf" and Path(source_path).is_file():
        render_html(source=Path(source_path), kind="pdf", out_path=out_path)
        return "rendered(pdf)"
    return "skipped(no-source)"


async def _amain() -> int:
    parser = argparse.ArgumentParser(prog="paperhub-rerender-html")
    parser.add_argument("--paper-content-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        if args.paper_content_id is not None:
            sql = (
                "SELECT id, source_path, source_dir_path, html_path "
                "FROM paper_content WHERE id = ?"
            )
            params: tuple[int, ...] = (args.paper_content_id,)
        else:
            sql = (
                "SELECT id, source_path, source_dir_path, html_path "
                "FROM paper_content ORDER BY id"
            )
            params = ()
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

    if not rows:
        _LOG.info("no paper_content rows to re-render")
        return 0

    for row in rows:
        pcid, source_path, source_dir_path, html_path = (
            int(row[0]), row[1], row[2], row[3],
        )
        before = _file_size(html_path)
        if args.dry_run:
            _LOG.info("pcid=%d dry-run: would re-render (was %d bytes)", pcid, before)
            continue
        try:
            status = _rerender_one(
                source_path=source_path, source_dir_path=source_dir_path,
                html_path=html_path,
            )
        except Exception as exc:  # noqa: BLE001 — report + continue across papers
            _LOG.warning("pcid=%d: re-render FAILED: %s: %s", pcid, type(exc).__name__, exc)
            continue
        after = _file_size(html_path)
        _LOG.info("pcid=%d: %s  %d -> %d bytes", pcid, status, before, after)
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
