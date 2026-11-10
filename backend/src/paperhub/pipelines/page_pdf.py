"""The paper's *page PDF*: the pages the agent views and region citations
point into.

A PDF paper's page PDF is its own file (the first ``*.pdf`` at the cache
root — the same file the reader opens). A LaTeX paper's is compiled locally
with pdflatex into ``asset/paper.pdf``; it lives under ``asset/`` because a
PDF at the cache root switches the reader to PDF mode. Only when a strict
compile fails is the arXiv PDF fetched — once: a failure marker stops any
retry, so ingest never hammers arXiv. Without an arXiv copy (a LaTeX upload)
a lenient compile (errors skipped) is accepted. Files are written to a temp
name and renamed, so a reader never sees a partial PDF.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pymupdf

from paperhub.pipelines.arxiv_client import _download_with_resume
from paperhub.pipelines.paper_asset import paper_asset_dir

logger = logging.getLogger(__name__)

COMPILED_PDF = "paper.pdf"
FAILED_MARKER = "paper.pdf.failed.json"
COMPILE_TIMEOUT_S = 300


def page_pdf_path(cache_dir: Path) -> Path | None:
    """The paper's page PDF: its own PDF, else the compiled one, else None."""
    own = sorted(Path(cache_dir).glob("*.pdf"))
    if own:
        return own[0]
    compiled = paper_asset_dir(cache_dir) / COMPILED_PDF
    return compiled if compiled.is_file() else None


def is_compiled_pdf(pdf: Path) -> bool:
    """A page PDF we produced (asset/paper.pdf), as opposed to the paper's own
    PDF — which may itself be an upload named ``paper.pdf``."""
    return pdf.name == COMPILED_PDF and pdf.parent.name == paper_asset_dir(pdf.parent.parent).name


def _page_count(path: Path) -> int:
    try:
        with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
            return int(doc.page_count)
    except Exception:  # noqa: BLE001 - an unreadable PDF counts as none
        return 0


def _install(produced: Path, cache_dir: Path) -> Path | None:
    """Move a produced PDF into place atomically; None if it has no pages."""
    if not produced.is_file() or _page_count(produced) == 0:
        return None
    asset = paper_asset_dir(cache_dir)
    asset.mkdir(parents=True, exist_ok=True)
    tmp = asset / f"{COMPILED_PDF}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    shutil.copyfile(produced, tmp)
    final = asset / COMPILED_PDF
    os.replace(tmp, final)
    return final


def compile_paper_pdf(
    main_tex: Path, cache_dir: Path, *, strict: bool = True,
    timeout_s: int = COMPILE_TIMEOUT_S,
) -> Path | None:
    """Compile ``main_tex`` (in a temp copy of its directory, so the source
    tree is untouched) twice with pdflatex; ``strict`` stops at the first
    error. Returns ``asset/paper.pdf`` or None."""
    if not main_tex.is_file() or shutil.which("pdflatex") is None:
        return None
    # Paper sources are third-party: no shell escape, and "paranoid" file
    # access (TeX reads and writes only below its working directory, never
    # e.g. backend/.env), so nothing outside the paper can land in the PDF.
    args = ["pdflatex", "-interaction=nonstopmode", "-no-shell-escape"]
    env = {**os.environ, "openin_any": "p", "openout_any": "p"}
    if strict:
        args.append("-halt-on-error")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src"
        shutil.copytree(main_tex.parent, work)
        try:
            for _ in range(2):
                done = subprocess.run(
                    [*args, main_tex.name], cwd=work, capture_output=True,
                    timeout=timeout_s, check=False, env=env)
                if strict and done.returncode != 0:
                    logger.info("pdflatex failed for %s (exit %s)", main_tex,
                                done.returncode)
                    return None
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("pdflatex did not finish for %s: %s", main_tex, exc)
            return None
        return _install(work / f"{main_tex.stem}.pdf", cache_dir)


def _download(url: str, target: Path) -> None:
    _download_with_resume(url, target)


# arXiv answers that settle it: the PDF does not exist (and will not).
_PERMANENT_HTTP = frozenset({404, 410})


def fetch_arxiv_pdf(arxiv_id: str, cache_dir: Path) -> Path | None:
    """One download of the arXiv PDF into ``asset/paper.pdf``. A permanent
    failure (HTTP 404/410, a PDF with no pages) leaves a marker so it is never
    tried again; a transient one (rate limit, server error, network) leaves
    none, so the next backfill tries once more."""
    import httpx

    asset = paper_asset_dir(cache_dir)
    marker = asset / FAILED_MARKER
    if marker.exists():
        return None
    asset.mkdir(parents=True, exist_ok=True)
    reason: str | None = None
    with tempfile.TemporaryDirectory(dir=asset) as tmp:
        target = Path(tmp) / "download.pdf"
        try:
            _download(f"https://arxiv.org/pdf/{arxiv_id}", target)
            installed = _install(target, cache_dir)
        except httpx.HTTPStatusError as exc:
            installed = None
            if exc.response.status_code in _PERMANENT_HTTP:
                reason = f"HTTP {exc.response.status_code}"
            else:
                logger.info("arXiv PDF %s: HTTP %s, will retry on a later run",
                         arxiv_id, exc.response.status_code)
        except Exception as exc:  # noqa: BLE001 - transient: retried on a later run
            installed = None
            logger.info("arXiv PDF %s: %r, will retry on a later run", arxiv_id, exc)
        else:
            if installed is None:
                reason = "no pages"
    if installed is None and reason is not None:
        marker.write_text(json.dumps({"arxiv_id": arxiv_id, "reason": reason,
                                      "at": time.time()}), encoding="utf-8")
    return installed


def ensure_page_pdf(
    main_tex: Path | None, arxiv_id: str | None, cache_dir: Path,
) -> Path | None:
    """The page PDF, made if missing: strict compile, else the arXiv PDF
    (once), else a lenient compile."""
    existing = page_pdf_path(cache_dir)
    if existing is not None:
        return existing
    if main_tex is not None:
        out = compile_paper_pdf(main_tex, cache_dir)
        if out is not None:
            return out
    if arxiv_id:
        out = fetch_arxiv_pdf(arxiv_id, cache_dir)
        if out is not None:
            return out
    if main_tex is not None:
        return compile_paper_pdf(main_tex, cache_dir, strict=False)
    return None
