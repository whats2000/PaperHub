"""Tests for the typed MCP scope-checker (NFR-10)."""

from __future__ import annotations

from pathlib import Path

from paperhub.mcp.scopes import (
    ArxivFetchMetadataArgs,
    FilesystemReadArgs,
    FilesystemWriteArgs,
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


def test_arxiv_invocation_parses_cleanly() -> None:
    inv = McpInvocation(
        tool="arxiv",
        method="fetch_metadata",
        args=ArxivFetchMetadataArgs(arxiv_id="2401.00001"),
    )
    scope = McpToolScope(tool_name="arxiv")
    assert check_scope(inv, scope) is None
