"""Cache-aware Paper Pipeline orchestrator (SRS §III-5.1).

Stages:
1. Compute content_key (arxiv:<id> or sha256:<hex>)
2. Cache lookup on paper_content.content_key
3. On hit: insert papers row, return.
4. On miss: download → extract → chunk → embed → render HTML → persist
   paper_content row + chunks rows + Chroma vectors → insert papers row.

NOTE (sync-in-async): ``download_arxiv_source`` and ``search_arxiv`` are
synchronous network calls invoked inside ``async`` methods.  This blocks the
event loop for the duration of the download/search.  For Plan C scope this
is accepted; wrapping in ``asyncio.to_thread`` is deferred.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import aiosqlite
import httpx
import tiktoken

from paperhub.llm.adapter import LlmAdapter
from paperhub.models.domain import SectionEntry
from paperhub.pipelines.arxiv_client import (
    _TRANSIENT_DOWNLOAD_EXCEPTIONS,
    TarballCorrupt,
    download_arxiv_pdf,
    download_arxiv_source,
    search_arxiv,
)
from paperhub.pipelines.chunker import Chunk, chunk_text, strip_latex_comments
from paperhub.pipelines.embedder import Embedder, get_embedder
from paperhub.pipelines.extract import (
    _extract_pdf_metadata,
    extract_latex,
    extract_pdf,
    extract_pdf_page1_text,
)
from paperhub.pipelines.renderer import render_html
from paperhub.pipelines.title_extract import llm_extract_title
from paperhub.rag.chroma import ChromaStore

_PDF_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_PDF_USER_AGENT = "PaperHub/0.1 (https://github.com/whats2000/PaperHub)"
# Shared tiktoken encoder — hoisted to module level so repeated calls to
# _build_sections_json don't pay the (cached but misleading) per-call cost.
_CL100K = tiktoken.get_encoding("cl100k_base")


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
        chroma: ChromaStore,
        embedder: Embedder | None = None,
        llm: LlmAdapter | None = None,
        title_extract_model: str | None = None,
    ) -> None:
        self._conn = conn
        self._cache_root = papers_cache_dir
        self._chroma = chroma
        self._embedder = embedder or get_embedder()
        # Optional LLM fallback for PDF title extraction. Both must be set
        # to enable the path; either ``None`` (legacy callers, tests) leaves
        # the existing metadata + page-1-font heuristic + filename-stem
        # ladder untouched.
        self._llm = llm
        self._title_extract_model = title_extract_model

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
        await self._conn.execute(
            "INSERT OR IGNORE INTO papers (session_id, paper_content_id) VALUES (?, ?)",
            (session_id, paper_content_id),
        )
        await self._conn.commit()
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

        # Render to HTML from the FLATTENED source, not the original main
        # .tex. The flattened file is a single self-contained document
        # (\input chains already inlined by extract_latex), so pandoc can't
        # hang/OOM resolving includes (arxiv:2410.12557 reproduced that), and
        # its char offsets align with chunk char_start/char_end + sections_json
        # (all computed against flattened_text) for the Citation Canvas.
        html_path = cache_dir / "source.html"
        render_html(source=flat_path, kind="latex", out_path=html_path)

        # Metadata: use caller-supplied override when available (avoids an
        # arXiv API round-trip when the caller already has metadata from
        # Semantic Scholar).  Fall back to the arXiv API otherwise.
        metadata: dict[str, object] = (
            asdict(req.metadata_override)
            if req.metadata_override is not None
            else self._lookup_arxiv_metadata(arxiv_id)
        )

        # Chunk + embed.
        chunks = chunk_text(full_text)
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(chunks, full_text)

        # Persist paper_content + chunks in a single transaction.
        paper_content_id, chunk_ids = await self._persist_paper_content_and_chunks(
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
        )

        self._chroma.add_chunks(
            paper_content_id=paper_content_id,
            chunk_ids=chunk_ids,
            texts=texts,
            embeddings=embeddings,
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
        paper is still ingestible end-to-end: chunked, embedded,
        searchable, and rendered in the Citation Canvas. Persisted with
        kind="arxiv" (the paper IS from arxiv; the kind enum reflects
        source identifier, not rendering path) and the same content_key
        as the LaTeX path so cache lookups stay coherent across modes.
        """
        import logging
        logging.getLogger(__name__).warning(
            "arxiv source tarball unavailable for %s (%s: %s); "
            "falling back to PDF ingest. Equation rendering quality "
            "will be lower than LaTeX.",
            arxiv_id, type(reason).__name__, reason,
        )

        pdf_path = download_arxiv_pdf(
            arxiv_id, cache_root=self._cache_root / "arxiv",
        )

        full_text = extract_pdf(pdf_path)
        html_path = cache_dir / "source.html"
        render_html(source=pdf_path, kind="pdf", out_path=html_path)

        metadata: dict[str, object] = (
            asdict(req.metadata_override)
            if req.metadata_override is not None
            else self._lookup_arxiv_metadata(arxiv_id)
        )

        chunks = chunk_text(full_text)
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(chunks, full_text)

        paper_content_id, chunk_ids = await self._persist_paper_content_and_chunks(
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
        )

        self._chroma.add_chunks(
            paper_content_id=paper_content_id,
            chunk_ids=chunk_ids,
            texts=texts,
            embeddings=embeddings,
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

        if kind == "latex":
            source_dir = target.parent
            ext = extract_latex(source_dir)
            full_text = ext.flattened_text
            source_path = ext.main_path
            flat_path = cache_dir / "source.flattened.tex"
            flat_path.write_text(full_text, encoding="utf-8")
            # Render from the flattened single-file source (see arxiv branch):
            # avoids pandoc hang/OOM on \input chains + aligns canvas offsets.
            render_source = flat_path
        else:
            full_text = extract_pdf(target)
            source_path = target
            render_source = source_path

        html_path = cache_dir / "source.html"
        render_html(source=render_source, kind=kind, out_path=html_path)

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

        chunks = chunk_text(full_text)
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(chunks, full_text)

        db_kind: Literal["pdf_upload", "latex_upload"] = (
            "pdf_upload" if kind == "pdf" else "latex_upload"
        )

        paper_content_id, chunk_ids = await self._persist_paper_content_and_chunks(
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
        )

        self._chroma.add_chunks(
            paper_content_id=paper_content_id,
            chunk_ids=chunk_ids,
            texts=texts,
            embeddings=embeddings,
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

        # 6. Extract text + chunk.
        full_text = extract_pdf(pdf_path)
        chunks = chunk_text(full_text)
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)

        # Compute section TOC for paper_qa subagent (v2.10-2).
        sections_json = self._build_sections_json(chunks, full_text)

        metadata: dict[str, object] = {
            "title": title_hint,
            "authors": list(authors_hint),
            "year": year_hint,
            "abstract": abstract_hint,
        }

        # 7. Persist paper_content + chunks transactionally.
        paper_content_id, chunk_ids = await self._persist_paper_content_and_chunks(
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
        )

        self._chroma.add_chunks(
            paper_content_id=paper_content_id,
            chunk_ids=chunk_ids,
            texts=texts,
            embeddings=embeddings,
        )

        papers_id = await self._link_to_session(session_id, paper_content_id)
        return IngestResult(
            paper_content_id, papers_id, cache_hit=False, title=title_hint,
        )

    @staticmethod
    def _build_sections_json(chunks: list[Chunk], full_text: str) -> str:
        """Compute the sections_json value from a list of chunks and source text.

        Groups chunks by section name, computes char extents and token counts,
        and returns a JSON-encoded list of SectionEntry dicts ordered by
        appearance. Chunks with section=None (preamble / pre-first-section
        text) are excluded — they are not addressable by name.
        """
        # Chunk char offsets are relative to the COMMENT-STRIPPED text, since
        # chunk_text strips before computing offsets. Apply the same strip
        # here so section_text slicing aligns with chunks[*].char_start /
        # char_end and the resulting token_count reflects content the model
        # will actually see.
        stripped_text = strip_latex_comments(full_text)
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
            section_text = stripped_text[group[0].char_start : group[-1].char_end]
            entries.append(
                SectionEntry(
                    name=name,
                    char_start=group[0].char_start,
                    char_end=group[-1].char_end,
                    token_count=len(_CL100K.encode(section_text)),
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
    ) -> tuple[int, list[int]]:
        """Persist paper_content + chunks in a single atomic transaction.

        Returns (paper_content_id, chunk_ids).  If anything raises, the
        partial writes are rolled back automatically.
        """
        await self._conn.execute("BEGIN")
        try:
            await self._conn.execute(
                "INSERT INTO paper_content "
                "(content_key, kind, arxiv_id, sha256, title, authors_json, year, "
                "abstract, sections_json, source_path, source_dir_path, html_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
            async with self._conn.execute("SELECT last_insert_rowid()") as cur:
                row = await cur.fetchone()
            assert row is not None
            paper_content_id = int(row[0])

            chunk_ids: list[int] = []
            for c in chunks:
                await self._conn.execute(
                    "INSERT INTO chunks "
                    "(paper_content_id, section, char_start, char_end, text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (paper_content_id, c.section, c.char_start, c.char_end, c.text),
                )
                async with self._conn.execute("SELECT last_insert_rowid()") as cur:
                    cid_row = await cur.fetchone()
                assert cid_row is not None
                chunk_ids.append(int(cid_row[0]))

            await self._conn.commit()
        except Exception:
            await self._conn.execute("ROLLBACK")
            raise
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
