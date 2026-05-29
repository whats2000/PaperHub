"""Research Agent tool dispatchers (SRS v2.6, FR-07).

v2.4 design: the LLM-visible palette is **read-only**. The agent shortlists
candidates, and the user is the sole ingestion trigger (via SearchResultList
"Add" buttons → POST /papers) — with one exception: the agent may flag up
to 2 picks with ``finalize: true``, which the chat endpoint auto-attaches
on its behalf. ``add_paper_to_session_dispatch`` survives as a Python
callable for the chat endpoint + POST /papers, but is no longer in the
LLM-visible palette.

v2.6 (Task v2.5-4): the agent's tool palette is sourced **exclusively**
from the MCP registry — see :func:`build_tool_schemas`. The dispatcher
functions in this module are no longer invoked from the agent's hot path;
they live on as the in-process backing for the FastMCP ``papers`` server
mounted at ``/mcp`` (see :mod:`paperhub.mcp.server`). The base
:data:`_BASE_PAPER_TOOL_SCHEMAS` list is private — only the FastMCP
server consumes it to pin its advertised JSON-schemas; the agent never
imports it.
"""
from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import aiosqlite

from paperhub.pipelines.arxiv_client import search_arxiv as _search_arxiv_sync
from paperhub.pipelines.paper_pipeline import ArxivMetadata, IngestRequest, PaperPipeline
from paperhub.pipelines.semantic_scholar import (
    Mode,
    SemanticScholarMetadata,
    fetch_paper_metadata,
    find_related,
    search_papers,
)
from paperhub.pipelines.unpaywall import find_oa_pdf_by_doi

if TYPE_CHECKING:
    from paperhub.mcp.registry import MCPRegistry

__all__ = [
    "AddResult",
    "ArxivHit",
    "LibraryHit",
    "NoIngestibleSourceError",
    "SemanticScholarToolHit",
    "_BASE_PAPER_TOOL_SCHEMAS",
    "_to_fts5_query",
    "add_paper_to_session_dispatch",
    "build_tool_schemas",
    "find_related_papers_dispatch",
    "search_arxiv_dispatch",
    "search_library_dispatch",
    "search_semantic_scholar_dispatch",
]


@dataclass(frozen=True)
class LibraryHit:
    paper_content_id: int
    arxiv_id: str | None
    title: str
    abstract: str
    year: int | None


@dataclass(frozen=True)
class ArxivHit:
    arxiv_id: str
    title: str
    abstract: str
    year: int | None
    authors: list[str]


@dataclass(frozen=True)
class SemanticScholarToolHit:
    """LLM-visible shape of a Semantic Scholar hit.

    Mirrors ``SemanticScholarHit`` from ``pipelines.semantic_scholar`` but
    lives here so it can be exposed via the MCP wrapper alongside the
    other tool result types.
    """

    paper_id: str  # "ss:<paperId>" or "arxiv:<arxiv_id>" (preferred when present)
    title: str
    abstract: str | None
    year: int | None
    authors: list[str]
    arxiv_id: str | None
    has_open_pdf: bool


@dataclass(frozen=True)
class AddResult:
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


class NoIngestibleSourceError(Exception):
    """Raised when an ``ss:<paperId>`` has no ``externalIds.ArXiv`` and no
    ``openAccessPdf.url``. POST /papers translates this to HTTP 422 so the
    frontend can disable the Add button persistently."""

    def __init__(self, paper_id: str, title: str) -> None:
        super().__init__(f"no ingestible source for {paper_id}")
        self.paper_id = paper_id
        self.title = title


# Canonical JSON-schemas the FastMCP ``papers`` server advertises.
# Private to this module: only :mod:`paperhub.mcp.server` consumes it to
# pin its ``tools/list`` output. The agent reads its palette from the MCP
# registry via :func:`build_tool_schemas` — which returns namespaced
# (``papers.search_library``, ``web.search``, …) schemas.
#
# v2.4: search_arxiv + add_paper_to_session are REMOVED from this list.
# The agent is read-only (no write tool); ingestion is user-driven via
# POST /papers, with the chat endpoint auto-attaching agent ``finalize:true``
# picks on its behalf.
_BASE_PAPER_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": (
                "Search the user's already-indexed paper library (deduplicated "
                "across all sessions). Excludes papers already attached to the "
                "current session. Cheap — prefer this first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text search terms.",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 8,
                        "minimum": 1,
                        "maximum": 25,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_semantic_scholar",
            "description": (
                "Free-text search across Semantic Scholar's ~200M paper corpus. "
                "Returns papers with title, abstract, year, authors, plus "
                "arxiv_id (when present) and has_open_pdf (when an open-access "
                "PDF is available). Prefer this over arxiv-only search — "
                "broader coverage. Use find_related_papers instead when "
                "looking for follow-up work to a specific paper."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer",
                        "default": 8,
                        "minimum": 1,
                        "maximum": 25,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related_papers",
            "description": (
                "Citation-graph navigation via Semantic Scholar. Use when the "
                "user wants follow-up work to a specific paper (mode=cited_by), "
                "the references of a paper (mode=cites), or generally similar "
                "work (mode=similar). Prefer this for 'what's next after paper X'. "
                "paper_id accepts 'arxiv:<id>' or 'ss:<paperId>'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": (
                            "Either 'arxiv:<id>' or 'ss:<paperId>'."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["cites", "cited_by", "similar"],
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 8,
                        "minimum": 1,
                        "maximum": 25,
                    },
                },
                "required": ["paper_id", "mode"],
            },
        },
    },
]


async def build_tool_schemas(
    mcp_registry: MCPRegistry,
) -> list[dict[str, Any]]:
    """Return the LLM-visible tool palette by aggregating every reachable
    MCP server's schemas (namespaced ``<server>.<tool>``).

    The registry has at minimum the in-process ``papers`` server (see
    ``mcp_servers.toml.example``); operators may add ``web``, ``sql``, etc.
    Lazy connection is triggered on first call; transient failures are
    logged + skipped by the registry — see :class:`MCPRegistry`.
    """
    return await mcp_registry.aggregate_tool_schemas()


def _to_fts5_query(query: str) -> str:
    """Sanitize a free-text query into an FTS5 MATCH expression.

    Strategy: split into tokens, strip FTS5 operator characters, double-quote
    each surviving token so FTS5 reserved keywords (AND/OR/NOT/NEAR) become
    literal tokens. Join with AND for Google-style multi-word semantics.

    Trade-off: this also disables FTS5 wildcards (`transformer*`) and column
    filters. Acceptable for Plan C; a richer query DSL is a Plan F follow-up.
    """
    tokens = [
        "".join(c for c in tok if c.isalnum() or c == "_")
        for tok in query.split()
    ]
    tokens = [t for t in tokens if t]  # drop empties
    if not tokens:
        return ""
    # Quote each token so reserved keywords (AND/OR/NOT/NEAR) are literal.
    return " AND ".join(f'"{t}"' for t in tokens)


async def search_library_dispatch(
    *,
    query: str,
    max_results: int = 8,
    conn: aiosqlite.Connection,
    session_id: int,
) -> list[LibraryHit]:
    """Full-text search across paper_content via FTS5, excluding rows
    already attached to this session."""
    fts_query = _to_fts5_query(query)
    if not fts_query:
        return []
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year "
        "FROM paper_content pc "
        "JOIN paper_content_fts fts ON fts.rowid = pc.id "
        "WHERE paper_content_fts MATCH ? "
        "  AND pc.id NOT IN ("
        "    SELECT paper_content_id FROM papers WHERE session_id = ?"
        "  ) "
        "ORDER BY rank "
        "LIMIT ?"
    )
    async with conn.execute(sql, (fts_query, session_id, max_results)) as cur:
        rows = await cur.fetchall()
    return [
        LibraryHit(
            paper_content_id=int(r[0]),
            arxiv_id=r[1],
            title=r[2] or "",
            abstract=r[3] or "",
            year=int(r[4]) if r[4] is not None else None,
        )
        for r in rows
    ]


async def search_arxiv_dispatch(
    *,
    query: str,
    max_results: int = 8,
) -> list[ArxivHit]:
    """Internal arXiv search — NOT in the LLM-visible palette as of v2.4.

    Kept as a Python helper for Plan F (the slides corpus may want a
    direct arXiv lookup). The agent uses ``search_semantic_scholar`` instead.
    """
    results = await asyncio.to_thread(_search_arxiv_sync, query, max_results)
    return [
        ArxivHit(
            arxiv_id=r.arxiv_id,
            title=r.title,
            abstract=r.abstract,
            year=r.year,
            authors=list(r.authors),
        )
        for r in results
    ]


async def search_semantic_scholar_dispatch(
    *,
    query: str,
    max_results: int = 8,
) -> list[SemanticScholarToolHit]:
    """Free-text Semantic Scholar search. Returns hits with an LLM-friendly
    ``paper_id`` prefix (``arxiv:<id>`` when available, else ``ss:<paperId>``)
    so the agent can pass it straight back into ``find_related_papers``
    or into the final ``json:candidates`` shortlist."""
    hits = await search_papers(query, max_results)
    out: list[SemanticScholarToolHit] = []
    for h in hits:
        pid = f"arxiv:{h.arxiv_id}" if h.arxiv_id else f"ss:{h.paperId}"
        out.append(
            SemanticScholarToolHit(
                paper_id=pid,
                title=h.title,
                abstract=h.abstract,
                year=h.year,
                authors=list(h.authors),
                arxiv_id=h.arxiv_id,
                has_open_pdf=bool(h.open_access_pdf_url),
            ),
        )
    return out


async def find_related_papers_dispatch(
    *,
    paper_id: str,
    mode: Mode,
    max_results: int = 8,
) -> list[dict[str, Any]]:
    """v2.4: accepts ``arxiv:<id>`` or ``ss:<paperId>``. The Semantic Scholar
    endpoints accept ``arXiv:<id>`` natively when the paper has an arxiv ID;
    for ss: IDs we pass the raw paper ID. Internally we still call
    ``find_related`` which builds the ``arXiv:<id>`` prefix itself, so for
    ``ss:`` IDs we need to dispatch differently — for now we resolve via
    fetch_paper_metadata first when the prefix is ``ss:``."""
    if paper_id.startswith("arxiv:"):
        arxiv_id = paper_id.removeprefix("arxiv:")
        related = await find_related(arxiv_id, mode=mode, max_results=max_results)
        return [asdict(r) for r in related]
    if paper_id.startswith("ss:"):
        # find_related currently only supports arxiv IDs via /paper/arXiv:<id>/...
        # Resolve the SS paper first; if it has an arxiv ID, recurse; else
        # surface an empty result rather than failing (the agent can fall
        # back to search_semantic_scholar with a query).
        meta = await fetch_paper_metadata(paper_id.removeprefix("ss:"))
        if meta.arxiv_id:
            related = await find_related(
                meta.arxiv_id, mode=mode, max_results=max_results,
            )
            return [asdict(r) for r in related]
        return []
    raise ValueError(
        f"find_related_papers: unrecognised paper_id prefix in {paper_id!r}",
    )


# ---------------------------------------------------------------------------
# add_paper_to_session_dispatch — NOT in _BASE_PAPER_TOOL_SCHEMAS as of v2.4.
# Invoked by:
#   - POST /papers (user clicks "Add as reference" in SearchResultList)
#   - chat endpoint paper_search branch (finalize: true auto-attach)
# ---------------------------------------------------------------------------


async def _attach_library(
    pcid_raw: str,
    *,
    conn: aiosqlite.Connection,
    session_id: int,
) -> AddResult:
    pcid = int(pcid_raw)
    await conn.execute(
        "INSERT OR IGNORE INTO papers (session_id, paper_content_id) "
        "VALUES (?, ?)",
        (session_id, pcid),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT p.id, pc.title FROM papers p "
        "JOIN paper_content pc ON pc.id = p.paper_content_id "
        "WHERE p.session_id = ? AND p.paper_content_id = ?",
        (session_id, pcid),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"library paper {pcid} not found after INSERT — DB invariant violated",
        )
    return AddResult(
        paper_content_id=pcid,
        papers_id=int(row[0]),
        cache_hit=True,
        title=row[1] or "",
    )


async def _attach_arxiv(
    arxiv_id: str,
    *,
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,  # noqa: ARG001 — pipeline owns the conn
    session_id: int,
    metadata_override: ArxivMetadata | None = None,
) -> AddResult:
    result = await pipeline.ingest(
        IngestRequest(
            session_id=session_id,
            arxiv_id=arxiv_id,
            metadata_override=metadata_override,
        ),
    )
    return AddResult(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )


async def _attach_pdf(
    meta: SemanticScholarMetadata,
    *,
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,  # noqa: ARG001 — pipeline owns the conn
    session_id: int,
) -> AddResult:
    assert meta.open_access_pdf_url is not None, "_attach_pdf requires open_access_pdf_url"
    result = await pipeline.ingest_pdf_from_url(
        session_id=session_id,
        pdf_url=meta.open_access_pdf_url,
        title_hint=meta.title,
        abstract_hint=meta.abstract or "",
        authors_hint=list(meta.authors),
        year_hint=meta.year,
    )
    return AddResult(
        paper_content_id=result.paper_content_id,
        papers_id=result.papers_id,
        cache_hit=result.cache_hit,
        title=result.title,
    )


async def add_paper_to_session_dispatch(
    paper_id: str,
    *,
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,
    session_id: int,
    metadata_override: ArxivMetadata | None = None,
    unpaywall_email: str | None = None,  # NEW
) -> AddResult:
    """Resolve a prefixed paper_id and attach it to the session.

    Invoked from:
      - ``POST /papers`` when the user clicks "Add as reference"
      - the chat endpoint's paper_search branch for ``finalize: true`` picks

    NOT exposed to the LLM as a tool (v2.4 design: read-only agent).

    ``metadata_override`` is forwarded to the ``arxiv:`` branch so the
    caller (POST /papers, chat auto-attach) can supply title/abstract/
    authors/year when it already has them, skipping the arXiv metadata
    API round-trip.  The ``ss:`` branch builds its own override from SS
    metadata (which is authoritative for that paper_id) and ignores the
    caller-supplied value.  The ``library:`` branch ignores it (no ingest).

    Prefix discriminator:
      - ``library:<paper_content_id>`` — cache-hit insert papers row
      - ``arxiv:<arxiv_id>`` — full PaperPipeline ingest
      - ``ss:<paperId>`` — SS metadata → arxiv path if externalIds.ArXiv,
        else openAccessPdf.url PDF, else raises NoIngestibleSourceError
    """
    if paper_id.startswith("library:"):
        return await _attach_library(
            paper_id.removeprefix("library:"),
            conn=conn,
            session_id=session_id,
        )

    if paper_id.startswith("arxiv:"):
        return await _attach_arxiv(
            paper_id.removeprefix("arxiv:"),
            pipeline=pipeline,
            conn=conn,
            session_id=session_id,
            metadata_override=metadata_override,
        )

    if paper_id.startswith("ss:"):
        ss_id = paper_id.removeprefix("ss:")
        meta = await fetch_paper_metadata(ss_id)
        if meta.arxiv_id:
            # Arxiv path is cleaner (LaTeX source + arxiv: content_key).
            # Pass SS metadata directly so _ingest_arxiv skips the arXiv
            # metadata API call — avoiding the redundant 2nd hit (hit #1 of
            # the 3-hit 429 bug).  The source URL (hit #2) is now also built
            # deterministically in download_arxiv_source, so only one real
            # HTTP request reaches arxiv.org (the tarball GET, hit #3).
            return await _attach_arxiv(
                meta.arxiv_id,
                pipeline=pipeline,
                conn=conn,
                session_id=session_id,
                metadata_override=ArxivMetadata(
                    title=meta.title,
                    abstract=meta.abstract or "",
                    authors=list(meta.authors),
                    year=meta.year,
                ),
            )
        if meta.open_access_pdf_url:
            return await _attach_pdf(
                meta, pipeline=pipeline, conn=conn, session_id=session_id,
            )
        # F4.3: Unpaywall fallback. When SS has no openAccessPdf URL but
        # we have a DOI AND the operator configured PAPERHUB_UNPAYWALL_EMAIL,
        # query Unpaywall to find a free PDF on the publisher's site /
        # preprint mirror. This is what closes the AlphaGenome-class gap
        # (Nature paper not indexed by SS but freely available).
        if meta.doi and unpaywall_email:
            oa_url = await find_oa_pdf_by_doi(meta.doi, email=unpaywall_email)
            if oa_url:
                synthesized = dataclasses.replace(
                    meta, open_access_pdf_url=oa_url,
                )
                return await _attach_pdf(
                    synthesized, pipeline=pipeline, conn=conn,
                    session_id=session_id,
                )
        raise NoIngestibleSourceError(paper_id=paper_id, title=meta.title)

    raise ValueError(
        f"add_paper_to_session: unrecognised paper_id prefix in {paper_id!r}",
    )
