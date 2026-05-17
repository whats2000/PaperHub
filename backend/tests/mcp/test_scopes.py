"""Tests for the typed MCP scope-checker (NFR-10)."""

from __future__ import annotations

from pathlib import Path

from paperhub.mcp.scopes import (
    ArxivDownloadPaperArgs,
    ArxivGetAbstractArgs,
    FilesystemReadArgs,
    FilesystemWriteArgs,
    GrobidProcessFulltextArgs,
    GrobidProcessHeaderArgs,
    McpInvocation,
    McpToolScope,
    ScopeRejection,
    check_scope,
)


def test_filesystem_write_inside_root_is_ok(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True)
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / "out.pdf", content=b"hi"),
    )
    assert check_scope(inv, scope) is None


def test_filesystem_write_outside_root_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True)
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace.parent / "escaped.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "outside filesystem root" in result.reason


def test_filesystem_write_traversal_attempt_is_rejected(tmp_workspace: Path) -> None:
    """EscapeRoute regression: `..` must not escape the root (CVE-2025-53109/53110)."""
    scope = McpToolScope(tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True)
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / ".." / "escaped.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)


def test_filesystem_write_when_write_not_allowed_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=False)
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / "x.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "write" in result.reason.lower()


def test_filesystem_read_inside_root_is_ok(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=False)
    inv = McpInvocation(
        tool="filesystem",
        method="read_file",
        args=FilesystemReadArgs(path=tmp_workspace / "x.pdf"),
    )
    assert check_scope(inv, scope) is None


def test_tool_mismatch_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="arxiv")
    inv = McpInvocation(
        tool="filesystem",
        method="read_file",
        args=FilesystemReadArgs(path=tmp_workspace / "x"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "tool mismatch" in result.reason.lower()


def test_arxiv_get_abstract_invocation_parses_cleanly() -> None:
    inv = McpInvocation(
        tool="arxiv",
        method="get_abstract",
        args=ArxivGetAbstractArgs(paper_id="2401.00001"),
    )
    scope = McpToolScope(tool_name="arxiv")
    assert check_scope(inv, scope) is None


def test_arxiv_download_paper_invocation_parses_cleanly() -> None:
    inv = McpInvocation(
        tool="arxiv",
        method="download_paper",
        args=ArxivDownloadPaperArgs(paper_id="2401.00001"),
    )
    scope = McpToolScope(tool_name="arxiv")
    assert check_scope(inv, scope) is None


def test_grobid_pdf_path_inside_workspace_is_ok(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="grobid", filesystem_root=tmp_workspace)
    inv = McpInvocation(
        tool="grobid",
        method="process_header",
        args=GrobidProcessHeaderArgs(pdf_path=tmp_workspace / "paper.pdf"),
    )
    assert check_scope(inv, scope) is None


def test_grobid_pdf_path_outside_workspace_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="grobid", filesystem_root=tmp_workspace)
    inv = McpInvocation(
        tool="grobid",
        method="process_header",
        args=GrobidProcessHeaderArgs(pdf_path=tmp_workspace.parent / "evil.pdf"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)


def test_grobid_fulltext_pdf_path_inside_workspace_is_ok(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="grobid", filesystem_root=tmp_workspace)
    inv = McpInvocation(
        tool="grobid",
        method="process_fulltext",
        args=GrobidProcessFulltextArgs(pdf_path=tmp_workspace / "paper.pdf"),
    )
    assert check_scope(inv, scope) is None


def test_grobid_missing_filesystem_root_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(tool_name="grobid")  # no filesystem_root
    inv = McpInvocation(
        tool="grobid",
        method="process_header",
        args=GrobidProcessHeaderArgs(pdf_path=tmp_workspace / "paper.pdf"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "filesystem_root" in result.reason
