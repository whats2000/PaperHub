"""Typed MCP scopes + per-tool arg schemas + scope-checker.

Per design §7, every outbound MCP call is validated against a typed
McpToolScope *before* dispatch. Argument payloads from the JSON-RPC wire
are parsed into one of the per-(tool, method) Pydantic models below —
the documented exception to NFR-11's "no untyped dict at I/O boundary".

Phase A ships the arg schemas Task 6 needs (arxiv + filesystem); later
phases add the remaining tools (`sqlite`, `grobid` methods, `latex`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class McpToolScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_name: str
    filesystem_root: Path | None = None
    sqlite_table_allowlist: list[str] | None = None
    url_domain_allowlist: list[str] | None = None
    write_allowed: bool = False


class ArxivSearchArgs(BaseModel):
    query: str
    max_results: int = 10


class ArxivFetchMetadataArgs(BaseModel):
    arxiv_id: str


class ArxivDownloadPdfArgs(BaseModel):
    arxiv_id: str


class FilesystemReadArgs(BaseModel):
    path: Path


class FilesystemWriteArgs(BaseModel):
    path: Path
    content: bytes


McpArgs = (
    ArxivSearchArgs
    | ArxivFetchMetadataArgs
    | ArxivDownloadPdfArgs
    | FilesystemReadArgs
    | FilesystemWriteArgs
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
    return None  # arxiv args: no path/domain check needed at this layer
