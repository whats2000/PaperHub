"""Research Agent tool dispatchers (SRS v2.3, FR-07).

Each function here is intended to be exposed as a tool to the LLM.
Contracts (JSON-schema args, structured returns) are MCP-compatible —
Plan E wraps this module as the `paperhub-papers` MCP server with
zero call-shape changes.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

import aiosqlite

from paperhub.pipelines.arxiv_client import search_arxiv as _search_arxiv_sync
from paperhub.pipelines.paper_pipeline import IngestRequest, PaperPipeline
from paperhub.pipelines.semantic_scholar import Mode, find_related

__all__ = [
    "AddResult",
    "ArxivHit",
    "LibraryHit",
    "TOOL_SCHEMAS",
    "add_paper_to_session_dispatch",
    "find_related_papers_dispatch",
    "search_arxiv_dispatch",
    "search_library_dispatch",
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
class AddResult:
    paper_content_id: int
    papers_id: int
    cache_hit: bool
    title: str


# The JSON-schemas LiteLLM (and later the MCP wrapper) hands to the LLM.
# Keep field names + descriptions stable across Plan C/E — they become
# part of the public MCP contract.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_library",
            "description": (
                "Search the user's already-indexed paper library (deduplicated "
                "across all sessions). Excludes papers already attached to the "
                "current session. Cheap. Prefer this before search_arxiv."
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
            "name": "search_arxiv",
            "description": (
                "Search arXiv full-text. Use when search_library doesn't cover "
                "the intent. May be called up to 3 times per turn with refined "
                "queries — the loop enforces this cap."
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
                "work (mode=similar). Prefer over search_arxiv when the user "
                "is asking 'what's next after paper X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
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
                "required": ["arxiv_id", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_paper_to_session",
            "description": (
                "Attach a paper to the current session with enabled=true (so it "
                "is immediately in scope for paper_qa). paper_id accepts "
                "'arxiv:<id>' for arXiv ingestion or "
                "'library:<paper_content_id>' for an already-indexed library "
                "paper. `reason` is a short human-readable string explaining "
                "WHY this paper matches the user's intent — surfaced in the "
                "trace for FR-02."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": (
                            "Either 'arxiv:<id>' or 'library:<paper_content_id>'."
                        ),
                    },
                    "reason": {"type": "string"},
                },
                "required": ["paper_id", "reason"],
            },
        },
    },
]


async def search_library_dispatch(
    *,
    query: str,
    max_results: int = 8,
    conn: aiosqlite.Connection,
    session_id: int,
) -> list[LibraryHit]:
    """Full-text search across paper_content, excluding rows already in
    this session. SQLite has no FTS in the schema yet — use LIKE on
    title + abstract for Plan C; FTS5 is a Plan F follow-up.
    """
    # Match terms on title OR abstract; exclude already-attached.
    # Known limitation (Plan F follow-up): LIKE has no ESCAPE clause, so a
    # literal '%' in the query is interpreted as a wildcard. Acceptable for
    # Plan C scope — users won't type literal '%'.
    escaped = query.strip().replace("%", "")
    like = f"%{escaped}%"
    sql = (
        "SELECT pc.id, pc.arxiv_id, pc.title, pc.abstract, pc.year "
        "FROM paper_content pc "
        "WHERE (pc.title LIKE ? OR pc.abstract LIKE ?) "
        "  AND pc.id NOT IN ("
        "    SELECT paper_content_id FROM papers WHERE session_id = ?"
        "  ) "
        "ORDER BY pc.year DESC NULLS LAST "
        "LIMIT ?"
    )
    async with conn.execute(sql, (like, like, session_id, max_results)) as cur:
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
    """arxiv.Search.results() is sync + network-bound — wrap in to_thread
    to avoid blocking the event loop (review C4 fix)."""
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


async def find_related_papers_dispatch(
    *,
    arxiv_id: str,
    mode: Mode,
    max_results: int = 8,
) -> list[dict[str, Any]]:
    related = await find_related(arxiv_id, mode=mode, max_results=max_results)
    return [asdict(r) for r in related]


async def add_paper_to_session_dispatch(
    *,
    paper_id: str,
    reason: str,  # noqa: ARG001 — surfaced in trace at the call site
    pipeline: PaperPipeline,
    conn: aiosqlite.Connection,
    session_id: int,
) -> AddResult:
    """``paper_id`` discriminator: 'arxiv:<id>' triggers ingest; 'library:<int>'
    skips ingest and just inserts a papers row referencing the existing
    paper_content. Either path lands enabled=true (schema default)."""
    if paper_id.startswith("library:"):
        pcid = int(paper_id.removeprefix("library:"))
        # Idempotent: ON CONFLICT (UNIQUE session_id+paper_content_id) → no-op,
        # then SELECT the existing papers row.
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
        assert row is not None, "library paper not found"
        return AddResult(
            paper_content_id=pcid,
            papers_id=int(row[0]),
            cache_hit=True,
            title=row[1] or "",
        )

    if paper_id.startswith("arxiv:"):
        arxiv_id = paper_id.removeprefix("arxiv:")
        result = await pipeline.ingest(
            IngestRequest(session_id=session_id, arxiv_id=arxiv_id)
        )
        return AddResult(
            paper_content_id=result.paper_content_id,
            papers_id=result.papers_id,
            cache_hit=result.cache_hit,
            title=result.title,
        )

    raise ValueError(
        f"add_paper_to_session: unrecognised paper_id prefix in {paper_id!r}"
    )
