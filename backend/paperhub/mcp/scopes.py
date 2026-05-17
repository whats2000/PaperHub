"""Typed MCP scopes + per-tool arg schemas + scope-checker.

Per design §7, every outbound MCP call is validated against a typed
McpToolScope *before* dispatch. Argument payloads from the JSON-RPC wire
are parsed into one of the per-(tool, method) Pydantic models below —
the documented exception to NFR-11's "no untyped dict at I/O boundary".

Phase A ships the arg schemas Task 6 needs (arxiv + filesystem); later
phases add the remaining tools (`sqlite`, `grobid` methods, `latex`).

arXiv MCP tool surface (blazickjp/arxiv-mcp-server):
- search_papers(query, ...) → list of matches
- get_abstract(paper_id: str) → metadata dict (title, authors, abstract, etc.)
- download_paper(paper_id: str) → {status, content} where content is markdown text
- list_papers({}) → list of downloaded paper IDs
- read_paper(paper_id) → {status, paper_id, content}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class McpToolScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_name: str
    # Also used by 'grobid' tool to validate pdf_path is under the workspace root
    filesystem_root: Path | None = None
    sqlite_table_allowlist: list[str] | None = None
    url_domain_allowlist: list[str] | None = None
    write_allowed: bool = False


class ArxivSearchArgs(BaseModel):
    query: str
    max_results: int = 10


class ArxivGetAbstractArgs(BaseModel):
    """Args for the upstream ``get_abstract`` tool (returns metadata only)."""

    paper_id: str


class ArxivDownloadPaperArgs(BaseModel):
    """Args for the upstream ``download_paper`` tool (returns markdown content)."""

    paper_id: str


# ---------------------------------------------------------------------------
# arxiv-latex-mcp args (Tier 1 — takashiishida/arxiv-latex-mcp)
# Tool surface:
#   get_paper_prompt(arxiv_id)   → complete flattened LaTeX source as raw text
#   get_paper_abstract(arxiv_id) → abstract (may include title/authors)
#   list_paper_sections(arxiv_id) → section headings
#   get_paper_section(arxiv_id, section_path) → specific section
# ---------------------------------------------------------------------------


class ArxivLatexGetPaperPromptArgs(BaseModel):
    """Args for ``get_paper_prompt`` — returns the full flattened LaTeX source."""

    arxiv_id: str


class ArxivLatexGetPaperAbstractArgs(BaseModel):
    """Args for ``get_paper_abstract`` — returns abstract (+ possible metadata)."""

    arxiv_id: str


class ArxivLatexListSectionsArgs(BaseModel):
    """Args for ``list_paper_sections`` — returns section headings."""

    arxiv_id: str


class ArxivLatexGetSectionArgs(BaseModel):
    """Args for ``get_paper_section`` — returns a specific section."""

    arxiv_id: str
    section_path: str


class FilesystemReadArgs(BaseModel):
    path: Path


class FilesystemWriteArgs(BaseModel):
    path: Path
    content: bytes


class GrobidProcessHeaderArgs(BaseModel):
    pdf_path: Path


class GrobidProcessFulltextArgs(BaseModel):
    pdf_path: Path


McpArgs = (
    ArxivSearchArgs
    | ArxivGetAbstractArgs
    | ArxivDownloadPaperArgs
    | ArxivLatexGetPaperPromptArgs
    | ArxivLatexGetPaperAbstractArgs
    | ArxivLatexListSectionsArgs
    | ArxivLatexGetSectionArgs
    | FilesystemReadArgs
    | FilesystemWriteArgs
    | GrobidProcessHeaderArgs
    | GrobidProcessFulltextArgs
)


class McpInvocation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tool: str
    method: str
    args: McpArgs


@dataclass(frozen=True)
class ScopeRejection:
    reason: str


def _is_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def check_scope(inv: McpInvocation, scope: McpToolScope) -> ScopeRejection | None:
    """Return None if allowed; ScopeRejection otherwise."""
    if inv.tool != scope.tool_name:
        return ScopeRejection(
            reason=f"tool mismatch: invocation={inv.tool!r}, scope={scope.tool_name!r}"
        )
    if isinstance(inv.args, FilesystemWriteArgs):
        if not scope.write_allowed:
            return ScopeRejection(reason="write not allowed by scope")
        if scope.filesystem_root is None:
            return ScopeRejection(reason="filesystem scope missing root")
        if not _is_inside(inv.args.path, scope.filesystem_root):
            return ScopeRejection(
                reason=f"path {inv.args.path} is outside filesystem root {scope.filesystem_root}"
            )
        return None
    if isinstance(inv.args, FilesystemReadArgs):
        if scope.filesystem_root is None:
            return ScopeRejection(reason="filesystem scope missing root")
        if not _is_inside(inv.args.path, scope.filesystem_root):
            return ScopeRejection(
                reason=f"path {inv.args.path} is outside filesystem root {scope.filesystem_root}"
            )
        return None
    if isinstance(inv.args, GrobidProcessHeaderArgs | GrobidProcessFulltextArgs):
        if scope.filesystem_root is None:
            return ScopeRejection(
                reason="grobid scope missing filesystem_root for pdf_path validation"
            )
        if not _is_inside(inv.args.pdf_path, scope.filesystem_root):
            return ScopeRejection(
                reason=(
                    f"pdf_path {inv.args.pdf_path} is outside filesystem root"
                    f" {scope.filesystem_root}"
                )
            )
        return None
    return None  # arxiv / arxiv-latex args: no path/domain check needed at this layer
