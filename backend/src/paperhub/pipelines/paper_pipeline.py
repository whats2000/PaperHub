"""Cache-aware Paper Pipeline orchestrator (SRS §III-5.1).

Stages:
1. Compute content_key (arxiv:<id> or sha256:<hex>)
2. Cache lookup on paper_content.content_key
3. On hit: insert papers row, return.
4. On miss: download → extract → chunk → render HTML → persist
   paper_content row + chunks rows → insert papers row.

NOTE (sync-in-async): ``download_arxiv_source`` and ``search_arxiv`` are
synchronous network calls invoked inside ``async`` methods.  This blocks the
event loop for the duration of the download/search.  For Plan C scope this
is accepted; wrapping in ``asyncio.to_thread`` is deferred.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import aiosqlite
import httpx
import tiktoken

from paperhub.db.connection import write_transaction
from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import SectionEntry
from paperhub.pipelines.arxiv_client import (
    _TRANSIENT_DOWNLOAD_EXCEPTIONS,
    TarballCorrupt,
    download_arxiv_pdf,
    download_arxiv_source,
    search_arxiv,
)
from paperhub.pipelines.chunker import (
    Chunk,
    chunk_text,
    map_stripped_offsets_to_original,
)
from paperhub.pipelines.extract import (
    _extract_pdf_metadata,
    extract_latex,
    extract_pdf_page1_text,
    extract_pdf_with_headings,
)
from paperhub.pipelines.figures import (
    rasterize_and_normalize_figures,
    strip_includegraphics_options,
)
from paperhub.pipelines.latex_to_asset import latex_source_to_asset
from paperhub.pipelines.marker_blocks_to_chunks import (
    build_layout_index,
    marker_blocks_to_chunks,
)
from paperhub.pipelines.marker_client import (
    MarkerClient,
    get_marker_client,
)
from paperhub.pipelines.marker_health import marker_available
from paperhub.pipelines.marker_to_asset import marker_doc_to_asset
from paperhub.pipelines.mathjax_macros import extract_macros_from_dir
from paperhub.pipelines.paper_asset import write_paper_asset
from paperhub.pipelines.pymupdf_to_asset import pymupdf_to_asset
from paperhub.pipelines.renderer import render_html
from paperhub.pipelines.sentinels import inject_sentinels, postprocess_sentinels
from paperhub.pipelines.table_figures import rasterize_complex_tables
from paperhub.pipelines.tikz_figures import rasterize_tikz_figures
from paperhub.pipelines.title_extract import llm_extract_title

logger = logging.getLogger(__name__)

_PDF_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_PDF_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"
# Shared tiktoken encoder — hoisted to module level so repeated calls to
# _build_sections_json don't pay the (cached but misleading) per-call cost.
_CL100K = tiktoken.get_encoding("cl100k_base")


def _bbox_json(bbox: tuple[float, float, float, float] | None) -> str | None:
    """Serialize a chunk's union bbox to a JSON ``[x0,y0,x1,y1]`` string, or
    ``None`` for non-Marker chunks (LaTeX / PyMuPDF)."""
    return json.dumps(list(bbox)) if bbox is not None else None


@dataclass(frozen=True)
class ArxivMetadata:
    """Caller-supplied metadata that skips the arXiv API lookup.

    Used by the ``ss:`` dispatcher branch in research_tools to pass
    Semantic Scholar metadata through so ``_ingest_arxiv`` never needs
    to hit the arXiv metadata API (hit #1 in the 3-hit rate-limit bug).
    The field names match the dict produced by ``_lookup_arxiv_metadata``.
    """

    title: str
    abstract: str
    authors: list[str]
    year: int | None


@dataclass(frozen=True)
class IngestRequest:
    session_id: int
    arxiv_id: str | None = None
    upload_path: Path | None = None
    upload_kind: Literal["pdf", "latex"] | None = None  # if upload_path is set
    metadata_override: ArxivMetadata | None = None  # skip arXiv metadata API when set


@dataclass(frozen=True)
class IngestResult:
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


def compute_content_key(
    *,
    arxiv_id: str | None = None,
    upload_path: Path | None = None,
) -> str:
    """Return a stable, human-readable cache key for a paper.

    - arXiv papers:  ``arxiv:<arxiv_id>``
    - Uploaded files: ``sha256:<hex-digest>``
    """
    if arxiv_id is not None:
        return f"arxiv:{arxiv_id}"
    if upload_path is not None:
        h = hashlib.sha256()
        with upload_path.open("rb") as fobj:
            for block in iter(lambda: fobj.read(1 << 20), b""):
                h.update(block)
        return f"sha256:{h.hexdigest()}"
    raise ValueError("must provide arxiv_id or upload_path")


class PaperPipeline:
    """Orchestrates the full paper ingestion pipeline with cache-aware short-circuiting."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        papers_cache_dir: Path,
        marker_client: MarkerClient | None = None,
        llm: LlmAdapter | None = None,
        title_extract_model: str | None = None,
    ) -> None:
        self._conn = conn
        self._cache_root = papers_cache_dir
        # Marker HTTP client for PDF extraction. Lazily resolved on first PDF
        # ingest, so callers that never ingest a PDF — and tests — don't need
        # a reachable Marker service.
        self._marker_client = marker_client
        # Optional LLM fallback for PDF title extraction. Both must be set
        # to enable the path; either ``None`` (legacy callers, tests) leaves
        # the existing metadata + page-1-font heuristic + filename-stem
        # ladder untouched.
        self._llm = llm
        self._title_extract_model = title_extract_model

    def _get_marker_client(self) -> MarkerClient:
        """Lazily resolve the Marker client on first PDF use."""
        if self._marker_client is None:
            self._marker_client = get_marker_client()
        return self._marker_client

    async def ingest(self, req: IngestRequest) -> IngestResult:
        """Ingest a paper, returning immediately on cache hit."""
        content_key = compute_content_key(
            arxiv_id=req.arxiv_id,
            upload_path=req.upload_path,
        )

        # Cache lookup.
        async with self._conn.execute(
            "SELECT id FROM paper_content WHERE content_key = ?",
            (content_key,),
        ) as cur:
            row = await cur.fetchone()

        if row is not None:
            paper_content_id = int(row[0])
            papers_id = await self._link_to_session(req.session_id, paper_content_id)
            async with self._conn.execute(
                "SELECT title FROM paper_content WHERE id = ?",
                (paper_content_id,),
            ) as cur:
                title_row = await cur.fetchone()
            title = str(title_row[0]) if title_row is not None else ""
            return IngestResult(
                paper_content_id, papers_id, cache_hit=True, title=title,
            )

        # Cache miss — full ingest.
        paper_content_id, papers_id, title = await self._fresh_ingest(req, content_key)
        return IngestResult(
            paper_content_id, papers_id, cache_hit=False, title=title,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _link_to_session(self, session_id: int, paper_content_id: int) -> int:
        """Upsert a ``papers`` row for (session_id, paper_content_id), return its id."""
        async with write_transaction(self._conn):
            await self._conn.execute(
                "INSERT OR IGNORE INTO papers (session_id, paper_content_id) "
                "VALUES (?, ?)",
                (session_id, paper_content_id),
            )
        async with self._conn.execute(
            "SELECT id FROM papers WHERE session_id = ? AND paper_content_id = ?",
            (session_id, paper_content_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError(
                "papers row missing after INSERT — DB invariant violated"
            )
        return int(row[0])

    async def _fresh_ingest(
        self,
        req: IngestRequest,
        content_key: str,
    ) -> tuple[int, int, str]:
        """Run the full ingest pipeline and persist results.

        Returns (paper_content_id, papers_id, title).
        """
        if req.arxiv_id is not None:
            return await self._ingest_arxiv(req, content_key)
        return await self._ingest_upload(req, content_key)

    async def _ingest_arxiv(
        self,
        req: IngestRequest,
        content_key: str,
    ) -> tuple[int, int, str]:
        assert req.arxiv_id is not None
        arxiv_id = req.arxiv_id
        cache_dir = self._cache_root / "arxiv" / arxiv_id

        # Prefer the e-print tarball (LaTeX → high-fidelity equations
        # in the Citation Canvas). Fall back to PDF only when the
        # source path is genuinely unrecoverable — both the resume-
        # capable downloader giving up AND a corrupt-tarball signal
        # qualify. The fallback persists with the same content_key
        # (``arxiv:<id>``) so future re-ingests still hit the cache.
        # Fall back to PDF on any of: transient transport failure that
        # the resume-capable downloader couldn't recover from, sustained
        # HTTP error (after retry-after honouring — typically 429 / 5xx),
        # or a corrupt tarball after the bytes arrived.
        _src_fallback_exc: tuple[type[BaseException], ...] = (
            *_TRANSIENT_DOWNLOAD_EXCEPTIONS, TarballCorrupt,
            httpx.HTTPStatusError,
        )
        try:
            source_dir = download_arxiv_source(
                arxiv_id,
                cache_root=self._cache_root / "arxiv",
            )
        except _src_fallback_exc as exc:
            return await self._ingest_arxiv_via_pdf(
                req, content_key, arxiv_id, cache_dir, reason=exc,
            )

        # Extract LaTeX text.
        ext = extract_latex(source_dir)
        full_text = ext.flattened_text
        source_path = ext.main_path

        # Persist flattened source alongside original.
        flat_path = cache_dir / "source.flattened.tex"
        flat_path.write_text(full_text, encoding="utf-8")

        # Emit PaperAsset from the LaTeX source (additive — never breaks ingest).
        # source_dir = the extracted source/ dir (figure files live here).
        # cache_dir  = the paper cache root (asset/ is written under it).
        try:
            _asset = latex_source_to_asset(source_dir, full_text, source_dir=cache_dir)
            write_paper_asset(_asset, cache_dir)
        except Exception:
            logger.warning(
                "PaperAsset extraction failed for arxiv %s; continuing",
                arxiv_id,
                exc_info=True,
            )

        # Chunk first — offsets are relative to strip_latex_comments(full_text),
        # which is exactly what chunk_text computes internally (strip_comments=True).
        # Chunking before rendering lets us inject sentinel tokens at each chunk's
        # start offset in the comment-stripped text, so the rendered HTML carries
        # deterministic <span id="phchunk-N"> anchors for the Citation Canvas.
        chunks = chunk_text(full_text)

        # Inject chunk-start sentinels into the comment-stripped source, then
        # normalize figures and render to HTML from that sentinel-marked copy.
        # Render from the FLATTENED source, not the original main .tex:
        # the flattened file is a single self-contained document (\input chains
        # already inlined by extract_latex), so pandoc can't hang/OOM resolving
        # includes (arxiv:2410.12557 reproduced that), and its char offsets align
        # with chunk char_start/char_end + sections_json (all computed against
        # flattened_text) for the Citation Canvas.
        # Rasterize PDF figures -> PNG + rewrite \includegraphics refs so pandoc
        # can embed them (arxiv figures are commonly PDF, often extensionless).
        # Figures live in the extracted source tree (source_path.parent) — pass
        # it as resource_dir so pandoc finds + embeds them into a self-contained
        # artefact for the Citation Canvas.
        # NOTE: chunk text comes from chunk_text(full_text) — the unmarked source —
        # so chunk texts are always clean and never contain sentinel tokens.
        # Inject into the RAW full_text (not the comment-stripped text): pandoc
        # fails on comment-stripped LaTeX for some papers, but renders the raw
        # source fine. Chunk char_start offsets are in stripped coords, so map
        # them back to raw-text positions first.
        starts = map_stripped_offsets_to_original(
            full_text, [c.char_start for c in chunks],
        )
        marked, _injected = inject_sentinels(full_text, starts)
        # Pre-rasterise TikZ-drawn figures (forest/tikzpicture/etc.) to PNG
        # before pandoc sees them — pandoc has no TikZ executor and would
        # otherwise dump the raw source into the HTML (the survey taxonomy
        # leak). Failures are graceful: an un-compilable block is left
        # as-is and the rest of the document still renders.
        marked = rasterize_tikz_figures(
            marked, preamble=ext.preamble, out_dir=source_path.parent,
        )
        # Rasterise pandoc-hostile tables (tabular*, \multirow, …) to images.
        marked = rasterize_complex_tables(
            marked, preamble=ext.preamble, out_dir=source_path.parent,
        )
        # Drop LaTeX column-width hints — pandoc would otherwise emit
        # style="width:50.0%" on every <img> and shrink high-DPI figures
        # to half-width on the wide Citation Canvas.
        marked = strip_includegraphics_options(marked)
        html_path = cache_dir / "source.html"
        render_tex_path = cache_dir / "source.render.tex"
        render_tex_path.write_text(
            rasterize_and_normalize_figures(marked, source_path.parent),
            encoding="utf-8",
        )
        render_html(
            source=render_tex_path, kind="latex", out_path=html_path,
            resource_dir=source_path.parent,
            macros=extract_macros_from_dir(source_path.parent, ext.preamble),
        )
        # Rewrite rendered HTML: replace surviving sentinel tokens with
        # <span id="phchunk-N"> anchors and tag each chunk with its dom_id.
        # Sentinels that pandoc dropped or mangled (e.g. those that landed in
        # math — inject_sentinels skips math spans) keep dom_id=None and fall
        # back to runtime text-search in the Citation Canvas.
        _raw_html = html_path.read_text(encoding="utf-8")
        _new_html, _dom_map = postprocess_sentinels(_raw_html)
        html_path.write_text(_new_html, encoding="utf-8")
        for i, c in enumerate(chunks):
            c.dom_id = _dom_map.get(i)

        # Metadata: use caller-supplied override when available (avoids an
        # arXiv API round-trip when the caller already has metadata from
        # Semantic Scholar).  Fall back to the arXiv API otherwise.
        metadata: dict[str, object] = (
            asdict(req.metadata_override)
            if req.metadata_override is not None
            else self._lookup_arxiv_metadata(arxiv_id)
        )

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(chunks, full_text)

        # Persist paper_content + chunks in a single transaction.
        # asset_status='latex': the asset was built synchronously from the
        # LaTeX e-print source above (no Marker upgrade ever needed).
        paper_content_id, _chunk_ids = await self._persist_paper_content_and_chunks(
            content_key=content_key,
            kind="arxiv",
            arxiv_id=arxiv_id,
            sha256=None,
            metadata=metadata,
            source_path=source_path,
            source_dir_path=cache_dir,
            html_path=html_path,
            chunks=chunks,
            sections_json=sections_json,
            asset_status="latex",
        )

        papers_id = await self._link_to_session(req.session_id, paper_content_id)
        return paper_content_id, papers_id, str(metadata.get("title", ""))

    async def _ingest_arxiv_via_pdf(
        self,
        req: IngestRequest,
        content_key: str,
        arxiv_id: str,
        cache_dir: Path,
        *,
        reason: BaseException,
    ) -> tuple[int, int, str]:
        """Fallback path when the arxiv e-print tarball is unavailable.

        Equation fidelity is lower than LaTeX rendering (PDF text
        extraction collapses math to glyph soup in many cases), but the
        paper is still ingestible end-to-end: chunked,
        searchable, and rendered in the Citation Canvas. Persisted with
        kind="arxiv" (the paper IS from arxiv; the kind enum reflects
        source identifier, not rendering path) and the same content_key
        as the LaTeX path so cache lookups stay coherent across modes.
        """
        logger.warning(
            "arxiv source tarball unavailable for %s (%s: %s); "
            "falling back to PDF ingest. Equation rendering quality "
            "will be lower than LaTeX.",
            arxiv_id, type(reason).__name__, reason,
        )

        pdf_path = download_arxiv_pdf(
            arxiv_id, cache_root=self._cache_root / "arxiv",
        )

        # PDF path: detect headings via font-size band so the paper_qa subagent
        # can navigate by section (mirrors the LaTeX \section{} path). No LaTeX
        # comments in PDF text → strip_comments=False (preserves "95%" etc.).
        full_text, headings = extract_pdf_with_headings(pdf_path)
        html_path = cache_dir / "source.html"
        render_html(source=pdf_path, kind="pdf", out_path=html_path)

        # Build the PyMuPDF "degraded" PaperAsset baseline synchronously (F2.1).
        # write_paper_asset / pymupdf_to_asset create the asset/ dir; cache_dir
        # itself already exists (download_arxiv_pdf wrote source.pdf into it).
        asset = pymupdf_to_asset(pdf_path, source_dir=cache_dir)
        write_paper_asset(asset, cache_dir)

        metadata: dict[str, object] = (
            asdict(req.metadata_override)
            if req.metadata_override is not None
            else self._lookup_arxiv_metadata(arxiv_id)
        )

        boundaries = self._pdf_boundaries(headings, str(metadata.get("title", "")))
        chunks = chunk_text(full_text, sections=boundaries, strip_comments=False)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(
            chunks, full_text, strip_comments=False,
        )

        # PDF source: enqueue a Marker upgrade when the service is reachable,
        # else stay on the PyMuPDF baseline.
        asset_status = "marker_pending" if marker_available() else "pymupdf_only"

        paper_content_id, _chunk_ids = await self._persist_paper_content_and_chunks(
            content_key=content_key,
            kind="arxiv",
            arxiv_id=arxiv_id,
            sha256=None,
            metadata=metadata,
            source_path=pdf_path,
            source_dir_path=cache_dir,
            html_path=html_path,
            chunks=chunks,
            sections_json=sections_json,
            asset_status=asset_status,
        )

        papers_id = await self._link_to_session(req.session_id, paper_content_id)
        return paper_content_id, papers_id, str(metadata.get("title", ""))

    async def _ingest_upload(
        self,
        req: IngestRequest,
        content_key: str,
    ) -> tuple[int, int, str]:
        assert req.upload_path is not None and req.upload_kind is not None
        sha = content_key.split(":", 1)[1]
        cache_dir = self._cache_root / "upload" / sha
        cache_dir.mkdir(parents=True, exist_ok=True)

        kind = req.upload_kind
        target = cache_dir / req.upload_path.name
        target.write_bytes(req.upload_path.read_bytes())

        html_path = cache_dir / "source.html"

        if kind == "latex":
            source_dir = target.parent
            ext = extract_latex(source_dir)
            full_text = ext.flattened_text
            source_path = ext.main_path
            flat_path = cache_dir / "source.flattened.tex"
            flat_path.write_text(full_text, encoding="utf-8")

            # Chunk first (offsets are relative to strip_latex_comments(full_text),
            # which chunk_text computes internally). Chunking before rendering lets
            # us inject sentinel tokens at each chunk's start offset in the
            # comment-stripped text so the rendered HTML gets deterministic
            # <span id="phchunk-N"> anchors for the Citation Canvas.
            chunks = chunk_text(full_text)

            # Inject sentinels into the comment-stripped flattened source, then
            # normalize figures and render to HTML from the sentinel-marked copy.
            # Render from a figure-normalized copy of the flattened source (see
            # arxiv branch): avoids pandoc hang/OOM on \input chains, rasterizes
            # PDF figures, and aligns canvas offsets (chunks use full_text).
            # Figures live in the extracted source tree, not next to the
            # flattened .tex — let pandoc find + embed them.
            # NOTE: chunk texts come from chunk_text(full_text) — the unmarked
            # source — so chunk texts are always clean and never contain sentinels.
            # Inject into the RAW full_text (pandoc fails on comment-stripped
            # LaTeX for some papers); chunk offsets are in stripped coords, so
            # map them back to raw-text positions.
            starts = map_stripped_offsets_to_original(
                full_text, [c.char_start for c in chunks],
            )
            marked, _injected = inject_sentinels(full_text, starts)
            marked = rasterize_tikz_figures(
                marked, preamble=ext.preamble, out_dir=source_path.parent,
            )
            # Rasterise pandoc-hostile tables (tabular*, \multirow, …) to images.
            marked = rasterize_complex_tables(
                marked, preamble=ext.preamble, out_dir=source_path.parent,
            )
            marked = strip_includegraphics_options(marked)
            render_source = cache_dir / "source.render.tex"
            render_source.write_text(
                rasterize_and_normalize_figures(marked, source_path.parent),
                encoding="utf-8",
            )
            render_html(
                source=render_source, kind=kind, out_path=html_path,
                resource_dir=source_path.parent,
                macros=extract_macros_from_dir(source_path.parent, ext.preamble),
            )
            # Rewrite rendered HTML: replace surviving sentinel tokens with
            # <span id="phchunk-N"> anchors and tag each chunk with its dom_id.
            # Sentinels dropped/mangled by pandoc keep dom_id=None and fall back
            # to runtime text-search in the Citation Canvas.
            _raw_html = html_path.read_text(encoding="utf-8")
            _new_html, _dom_map = postprocess_sentinels(_raw_html)
            html_path.write_text(_new_html, encoding="utf-8")
            for i, c in enumerate(chunks):
                c.dom_id = _dom_map.get(i)

            pdf_headings: list[tuple[str, int]] = []
        else:
            # PDF (F2.1): extract synchronously with PyMuPDF — instant, always
            # works, never blocks the event loop on a multi-minute Marker run.
            # Marker is an OPT-IN async upgrade: we merely RECORD whether one
            # should happen (asset_status='marker_pending') so a background
            # worker (a later task) runs the high-fidelity pass off the request
            # path. The PyMuPDF asset is a "degraded" baseline (real figure
            # files, but no captions/equations) good enough for ingest + RAG.
            full_text, pdf_headings = extract_pdf_with_headings(target)
            asset = pymupdf_to_asset(target, source_dir=cache_dir)
            write_paper_asset(asset, cache_dir)
            source_path = target
            render_html(
                source=source_path, kind=kind, out_path=html_path,
                resource_dir=None,
            )

        # Honor caller-supplied metadata override (e.g. a title typed in the
        # upload modal) the same way the arxiv branch does. Without one, try
        # the PDF's embedded metadata next (PyMuPDF ``doc.metadata``), and
        # only fall back to the filename stem when nothing usable is found —
        # so a publisher-prepared PDF with a real title ends up with the
        # real title, not the DOI-named stem.
        if req.metadata_override is not None:
            metadata: dict[str, object] = asdict(req.metadata_override)
        elif req.upload_kind == "pdf":
            auto = _extract_pdf_metadata(req.upload_path)
            title = str(auto["title"])
            # Third-tier fallback: ask a small-tier LLM to extract the title
            # from page-1 text. Only fires when (a) embedded metadata title
            # was empty/junk, (b) the page-1 largest-font heuristic also
            # came back empty, and (c) the route wired an ``llm`` adapter
            # plus a ``title_extract_model``. Best-effort: any LLM failure
            # returns ``""`` and we fall through to the filename stem.
            if (
                not title
                and self._llm is not None
                and self._title_extract_model is not None
            ):
                page1_text = extract_pdf_page1_text(req.upload_path)
                if len(page1_text) >= 100:
                    title = await llm_extract_title(
                        self._llm,
                        self._title_extract_model,
                        page1_text,
                    )
            if not title:
                title = req.upload_path.stem
            metadata = {
                "title": title,
                "authors": auto["authors"],
                "year": auto["year"],
                "abstract": "",
            }
        else:
            metadata = {
                "title": req.upload_path.stem,
                "authors": [],
                "year": None,
            }

        if kind == "pdf":
            boundaries = self._pdf_boundaries(
                pdf_headings, str(metadata.get("title", "")),
            )
            chunks = chunk_text(full_text, sections=boundaries, strip_comments=False)
            sections_json = self._build_sections_json(
                chunks, full_text, strip_comments=False,
            )
        else:
            # LaTeX: chunks already populated above with dom_ids set.
            sections_json = self._build_sections_json(chunks, full_text)

        db_kind: Literal["pdf_upload", "latex_upload"] = (
            "pdf_upload" if kind == "pdf" else "latex_upload"
        )

        # asset_status: latex uploads build the asset synchronously from source
        # ('latex'); PDFs use the PyMuPDF baseline and may enqueue a Marker
        # upgrade when the service is reachable ('marker_pending'), else stay on
        # the PyMuPDF baseline ('pymupdf_only').
        if kind == "pdf":
            asset_status = "marker_pending" if marker_available() else "pymupdf_only"
        else:
            asset_status = "latex"

        paper_content_id, _chunk_ids = await self._persist_paper_content_and_chunks(
            content_key=content_key,
            kind=db_kind,
            arxiv_id=None,
            sha256=sha,
            metadata=metadata,
            source_path=source_path,
            source_dir_path=cache_dir,
            html_path=html_path,
            chunks=chunks,
            sections_json=sections_json,
            asset_status=asset_status,
        )

        papers_id = await self._link_to_session(req.session_id, paper_content_id)
        return paper_content_id, papers_id, str(metadata.get("title", ""))

    async def ingest_pdf_from_url(
        self,
        *,
        session_id: int,
        pdf_url: str,
        title_hint: str,
        abstract_hint: str,
        authors_hint: list[str],
        year_hint: int | None,
    ) -> IngestResult:
        """Download a PDF from an open-access URL and ingest as kind='pdf_upload'.

        Used by the ``ss:<paperId>`` dispatcher branch when Semantic Scholar
        returns a paper without an arXiv ID but with ``openAccessPdf.url``.
        sha256-keyed cache (same as user PDF uploads), so the same URL
        downloaded twice deduplicates at ``paper_content``.
        """
        # 1. Fetch PDF bytes.
        async with httpx.AsyncClient(
            timeout=_PDF_DOWNLOAD_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _PDF_USER_AGENT},
        ) as client:
            resp = await client.get(pdf_url)
        resp.raise_for_status()
        pdf_bytes = resp.content

        # 2. Compute content_key.
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        content_key = f"sha256:{sha}"

        # 3. Cache lookup.
        async with self._conn.execute(
            "SELECT id, title FROM paper_content WHERE content_key = ?",
            (content_key,),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            paper_content_id = int(row[0])
            title = str(row[1] or title_hint)
            papers_id = await self._link_to_session(session_id, paper_content_id)
            return IngestResult(
                paper_content_id, papers_id, cache_hit=True, title=title,
            )

        # 4. Write PDF to cache.
        cache_dir = self._cache_root / "upload" / sha
        cache_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = cache_dir / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)

        # 5. Render HTML.
        html_path = cache_dir / "source.html"
        render_html(source=pdf_path, kind="pdf", out_path=html_path)

        # 5a. PyMuPDF "degraded" PaperAsset baseline (F2.1).
        asset = pymupdf_to_asset(pdf_path, source_dir=cache_dir)
        write_paper_asset(asset, cache_dir)

        # 6. Extract text + chunk. PDF path: heading detection for section
        # navigation; strip_comments=False (PDF text isn't LaTeX).
        full_text, headings = extract_pdf_with_headings(pdf_path)
        boundaries = self._pdf_boundaries(headings, title_hint)
        chunks = chunk_text(full_text, sections=boundaries, strip_comments=False)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(
            chunks, full_text, strip_comments=False,
        )

        metadata: dict[str, object] = {
            "title": title_hint,
            "authors": list(authors_hint),
            "year": year_hint,
            "abstract": abstract_hint,
        }

        # PDF source: enqueue a Marker upgrade when reachable, else baseline.
        asset_status = "marker_pending" if marker_available() else "pymupdf_only"

        # 7. Persist paper_content + chunks transactionally.
        paper_content_id, _chunk_ids = await self._persist_paper_content_and_chunks(
            content_key=content_key,
            kind="pdf_upload",
            arxiv_id=None,
            sha256=sha,
            metadata=metadata,
            source_path=pdf_path,
            source_dir_path=cache_dir,
            html_path=html_path,
            chunks=chunks,
            sections_json=sections_json,
            asset_status=asset_status,
        )

        papers_id = await self._link_to_session(session_id, paper_content_id)
        return IngestResult(
            paper_content_id, papers_id, cache_hit=False, title=title_hint,
        )

    async def upgrade_pdf_asset_via_marker(
        self, paper_content_id: int, *, max_pages: int | None,
    ) -> None:
        """Upgrade a PDF paper from the PyMuPDF baseline to Marker quality.

        Re-extracts the PDF via the Marker service, overwrites the on-disk
        PaperAsset, re-chunks from Marker's cleaner structure (better
        navigation), and flips ``asset_status`` to ``marker_ready``.

        Runs the (multi-minute, blocking) Marker call off the event loop. The
        re-chunk mirrors ``reingest._reingest_one``'s ordering: the destructive
        DELETE then INSERT new chunks + recompute ``sections_json`` happen in
        one serialised write transaction.

        Exceptions propagate (the worker records ``marker_failed``); they are
        NOT swallowed here.
        """
        async with self._conn.execute(
            "SELECT kind, source_path, source_dir_path "
            "FROM paper_content WHERE id = ?",
            (paper_content_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise ValueError(
                f"paper_content id={paper_content_id} not found"
            )
        kind = str(row[0])
        source_path_raw: str | None = row[1]
        source_dir_raw: str | None = row[2]
        if source_path_raw is None or source_dir_raw is None:
            raise ValueError(
                f"paper_content id={paper_content_id}: missing source path"
            )
        source_path = Path(source_path_raw)
        cache_dir = Path(source_dir_raw)

        # Only PDF sources are valid Marker inputs. pdf_upload always is; an
        # arxiv row qualifies only when its source fell back to a .pdf.
        is_pdf = source_path.suffix.lower() == ".pdf"
        if not (kind == "pdf_upload" or (kind == "arxiv" and is_pdf)):
            raise ValueError(
                f"paper_content id={paper_content_id}: kind={kind!r} "
                f"source={source_path.name!r} is not a PDF source"
            )

        # Marker extraction is a blocking, possibly multi-minute httpx call —
        # run it off the event loop.
        pdf_bytes = source_path.read_bytes()  # noqa: ASYNC240 — one-shot read before the to_thread Marker call
        client = self._get_marker_client()
        doc = await asyncio.to_thread(
            client.extract, pdf_bytes, max_pages=max_pages,
        )

        # Overwrite the PyMuPDF baseline asset with Marker quality in place.
        asset = marker_doc_to_asset(doc, source_dir=cache_dir)
        write_paper_asset(asset, cache_dir)

        # Re-chunk from Marker's block structure via the block-anchored
        # assembler: each chunk is a group of consecutive blocks sharing one
        # (section, page), rendered to REAL markdown (tables stay tables) and
        # carrying its union page + bbox so the Citation Canvas can highlight
        # geometrically. Replaces the old marker_doc_to_markdown flatten path.
        chunks = marker_blocks_to_chunks(doc)
        # Marker text comes from resp.json() and can carry lone UTF-16
        # surrogates (bad OCR / encoding artifacts) that SQLite can't store.
        # Drop them per chunk so the INSERTs never raise UnicodeEncodeError.
        for c in chunks:
            c.text = c.text.encode("utf-8", "ignore").decode("utf-8")
            if c.match_text is not None:
                c.match_text = c.match_text.encode("utf-8", "ignore").decode("utf-8")

        sections_json = self._build_sections_json(chunks)

        # Destructive replace: delete old chunks, insert new + flip
        # asset_status — all in ONE serialised write transaction (v2.23.2) so
        # the marker worker can't race a concurrent paper ingest on the SQLite
        # write lock.
        new_ids: list[int] = []
        async with write_transaction(self._conn):
            await self._conn.execute(
                "DELETE FROM chunks WHERE paper_content_id = ?",
                (paper_content_id,),
            )

            for c in chunks:
                async with self._conn.execute(
                    "INSERT INTO chunks "
                    "(paper_content_id, section, char_start, char_end, text, dom_id, "
                    "match_text, page, bbox) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (paper_content_id, c.section, c.char_start, c.char_end, c.text,
                     c.dom_id, c.match_text, c.page, _bbox_json(c.bbox)),
                ) as cur:
                    r = await cur.fetchone()
                    assert r is not None
                    new_ids.append(int(r[0]))

            # F2.1 A3: build the per-paper figure+table layout index from the
            # freshly-inserted chunks zipped with their assigned ids, and
            # persist it in the SAME transaction as sections_json/asset_status.
            layout_json = json.dumps(
                build_layout_index(list(zip(chunks, new_ids, strict=True)))
            )
            await self._conn.execute(
                "UPDATE paper_content SET sections_json = ?, layout_json = ?, "
                "asset_status = 'marker_ready' WHERE id = ?",
                (sections_json, layout_json, paper_content_id),
            )

    @staticmethod
    def _pdf_boundaries(
        headings: list[tuple[str, int]], fallback_name: str,
    ) -> list[tuple[str, int]]:
        """Section boundaries for a PDF: the detected headings, or a single
        synthetic section covering the whole doc when none were detected (so
        ``list_sections`` always returns a navigable entry)."""
        return headings if headings else [(fallback_name or "Full text", 0)]

    @staticmethod
    def _build_sections_json(
        chunks: list[Chunk], full_text: str | None = None, *,
        strip_comments: bool = True,
    ) -> str:
        """Compute the sections_json value from a list of chunks.

        Groups chunks by section name, computes char extents and token counts,
        and returns a JSON-encoded list of SectionEntry dicts ordered by
        appearance. Chunks with section=None (preamble / pre-first-section
        text) are excluded — they are not addressable by name.

        Option (a): token_count is computed by encoding each section's chunk
        TEXTS directly (no source-text slicing), so this never couples to a
        ``full_text`` whose char offsets must line up — important for the
        Marker block-anchored assembler, whose chunks carry offsets over a
        concatenation the pipeline doesn't hold. ``full_text``/``strip_comments``
        are accepted for backward compatibility and ignored.
        """
        per_section: dict[str, list[Chunk]] = defaultdict(list)
        section_order: list[str] = []
        for c in chunks:
            if c.section is None:
                continue
            if c.section not in per_section:
                section_order.append(c.section)
            per_section[c.section].append(c)

        entries: list[SectionEntry] = []
        for name in section_order:
            group = per_section[name]
            section_text = "\n\n".join(c.text for c in group)
            entries.append(
                SectionEntry(
                    name=name,
                    char_start=group[0].char_start,
                    char_end=group[-1].char_end,
                    # disallowed_special=() so literal "<|endoftext|>" in the
                    # text (NLP papers discuss it) is counted, not raised.
                    token_count=len(_CL100K.encode(section_text, disallowed_special=())),
                    chunk_count=len(group),
                )
            )
        return json.dumps([e.model_dump() for e in entries])

    async def _persist_paper_content_and_chunks(
        self,
        *,
        content_key: str,
        kind: Literal["arxiv", "pdf_upload", "latex_upload"],
        arxiv_id: str | None,
        sha256: str | None,
        metadata: dict[str, object],
        source_path: Path,
        source_dir_path: Path,
        html_path: Path,
        chunks: list[Chunk],
        sections_json: str | None = None,
        asset_status: str | None = None,
    ) -> tuple[int, list[int]]:
        """Persist paper_content + chunks in a single atomic transaction.

        Returns (paper_content_id, chunk_ids).  If anything raises, the
        partial writes are rolled back automatically. Serialised
        process-wide via ``write_transaction`` (v2.23.2 hotfix) so
        concurrent ``POST /papers`` requests don't pile up on the SQLite
        write lock — every write site that goes through
        ``write_transaction`` queues on the same app-layer asyncio lock,
        so the database file lock never sees more than one writer at a
        time from this process.
        """
        chunk_ids: list[int] = []
        async with write_transaction(self._conn):
            await self._conn.execute(
                "INSERT INTO paper_content "
                "(content_key, kind, arxiv_id, sha256, title, authors_json, year, "
                "abstract, sections_json, source_path, source_dir_path, html_path, "
                "asset_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content_key,
                    kind,
                    arxiv_id,
                    sha256,
                    str(metadata.get("title", "")),
                    json.dumps(metadata.get("authors", [])),
                    metadata.get("year"),
                    str(metadata.get("abstract", "")),
                    sections_json,
                    str(source_path),
                    str(source_dir_path),
                    str(html_path),
                    asset_status,
                ),
            )
            async with self._conn.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            assert row is not None
            paper_content_id = int(row[0])

            for c in chunks:
                await self._conn.execute(
                    "INSERT INTO chunks "
                    "(paper_content_id, section, char_start, char_end, text, dom_id, "
                    "match_text, page, bbox) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (paper_content_id, c.section, c.char_start, c.char_end, c.text,
                     c.dom_id, c.match_text, c.page, _bbox_json(c.bbox)),
                )
                async with self._conn.execute("SELECT last_insert_rowid()") as cur:
                    cid_row = await cur.fetchone()
                assert cid_row is not None
                chunk_ids.append(int(cid_row[0]))
        return paper_content_id, chunk_ids

    def _lookup_arxiv_metadata(self, arxiv_id: str) -> dict[str, object]:
        """Fetch title/authors/year from arXiv API (sync call — see module docstring)."""
        results = search_arxiv(arxiv_id, max_results=1)
        if not results:
            return {"title": arxiv_id, "authors": [], "year": None}
        r = results[0]
        return {
            "title": r.title,
            "authors": r.authors,
            "year": r.year,
            "abstract": r.abstract,
        }
