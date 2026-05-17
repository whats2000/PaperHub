"""Tests for McpClient I-1 fix: rejected scope writes a tool_calls row."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.data.db import apply_migrations
from paperhub.mcp.client import McpClient, McpScopeViolation
from paperhub.mcp.scopes import FilesystemWriteArgs, McpInvocation, McpToolScope
from paperhub.tracing.tracer import ToolCallTracer


async def _never_called(inv: McpInvocation) -> dict[str, object]:
    raise AssertionError("dispatcher should not be called on a scope rejection")  # pragma: no cover


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "hub.db"
    apply_migrations(path)
    return path


@pytest.mark.asyncio
async def test_scope_rejection_writes_rejected_row(db_path: Path, tmp_path: Path) -> None:
    """I-1: A rejected MCP call must persist a tool_calls row before raising."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    run_id = uuid4()
    tracer = ToolCallTracer(db_path)

    # Insert a parent runs row so the FK constraint on tool_calls is satisfied
    import sqlite3 as _sqlite3

    conn_setup = _sqlite3.connect(db_path)
    conn_setup.execute(
        "INSERT INTO runs (id, started_at, status) VALUES (?, datetime('now'), 'running')",
        (str(run_id),),
    )
    conn_setup.commit()
    conn_setup.close()

    scope = McpToolScope(
        tool_name="filesystem",
        filesystem_root=workspace,
        write_allowed=True,
    )
    # Path outside the workspace root — will be rejected
    invocation = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=workspace.parent / "escaped.pdf", content=b"bad"),
    )

    client = McpClient(
        scopes={"filesystem": scope},
        dispatcher=_never_called,
        tracer=tracer,
        run_id=run_id,
        step_index=0,
    )

    with pytest.raises(McpScopeViolation):
        await client.call(invocation)

    # The row must be in the DB AFTER the exception propagates
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT status, error FROM tool_calls WHERE run_id = ?", (str(run_id),)
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    status, error = rows[0]
    assert status == "rejected"
    assert error is not None and "outside filesystem root" in error


@pytest.mark.asyncio
async def test_scope_rejection_no_tracer_still_raises(tmp_path: Path) -> None:
    """Without a tracer the McpScopeViolation is still raised (no regression)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    scope = McpToolScope(
        tool_name="filesystem",
        filesystem_root=workspace,
        write_allowed=True,
    )
    invocation = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=workspace.parent / "escaped.pdf", content=b"bad"),
    )

    client = McpClient(scopes={"filesystem": scope}, dispatcher=_never_called)

    with pytest.raises(McpScopeViolation):
        await client.call(invocation)


@pytest.mark.asyncio
async def test_binary_content_is_redacted_not_leaked(db_path: Path, tmp_path: Path) -> None:
    """I-2: Binary content bytes must not appear raw in the audit log.

    The content field of FilesystemWriteArgs is bytes; if model_dump(mode='json')
    is used, Pydantic encodes bytes to a base64 string BEFORE redact() sees it,
    so the <bytes: N> placeholder never fires. This test verifies the fix.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    run_id = uuid4()
    tracer = ToolCallTracer(db_path)

    import sqlite3 as _sqlite3

    conn_setup = _sqlite3.connect(db_path)
    conn_setup.execute(
        "INSERT INTO runs (id, started_at, status) VALUES (?, datetime('now'), 'running')",
        (str(run_id),),
    )
    conn_setup.commit()
    conn_setup.close()

    binary_content = b"PDF_HEADER_AND_BODY"  # 19 bytes

    # write_allowed=False forces a scope rejection before dispatch
    scope = McpToolScope(
        tool_name="filesystem",
        filesystem_root=workspace,
        write_allowed=False,
    )
    invocation = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=workspace / "x.pdf", content=binary_content),
    )

    client = McpClient(
        scopes={"filesystem": scope},
        dispatcher=_never_called,
        tracer=tracer,
        run_id=run_id,
        step_index=0,
    )

    with pytest.raises(McpScopeViolation):
        await client.call(invocation)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT args_redacted_json FROM tool_calls WHERE run_id = ?",
        (str(run_id),),
    ).fetchall()
    conn.close()

    assert len(rows) == 1, "Expected exactly one tool_calls row"
    args_json_str: str = rows[0][0]

    # Must not be enormous — raw bytes as a string would blow this out
    assert len(args_json_str) < 300, (
        f"args_redacted_json is suspiciously large ({len(args_json_str)} bytes); "
        "binary content may have leaked through"
    )

    parsed = json.loads(args_json_str)
    # The payload is {"tool": ..., "method": ..., "args": {"path": ..., "content": ...}}
    content_val = parsed["args"]["content"]
    expected_placeholder = f"<bytes: {len(binary_content)}>"
    assert content_val == expected_placeholder, (
        f"Expected content to be {expected_placeholder!r}, got {content_val!r}"
    )
