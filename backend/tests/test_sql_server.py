import aiosqlite
import pytest

from paperhub.mcp.server_context import (
    PaperhubPapersRequestContext,
    reset_request_context,
    set_request_context,
)
from paperhub.mcp.sql_server import (
    _describe_handler,
    _list_tables_handler,
    _query_handler,
)
from paperhub.tracing.tracer import Tracer


@pytest.fixture
async def sql_ctx(migrated_db: aiosqlite.Connection):
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    ctx = PaperhubPapersRequestContext(
        conn=migrated_db, session_id=1, run_id=1, tracer=tracer, caller_supplied_run=True,
    )
    token = set_request_context(ctx)
    try:
        yield ctx
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_list_tables_returns_allowlist(sql_ctx) -> None:
    tables = await _list_tables_handler()
    assert "papers" in tables and "paper_content" in tables
    assert "memories" not in tables


@pytest.mark.asyncio
async def test_describe_returns_columns(sql_ctx) -> None:
    cols = await _describe_handler("papers")
    names = {c["name"] for c in cols}
    assert {"session_id", "paper_content_id", "enabled"} <= names
    assert all("name" in c and "type" in c for c in cols)


@pytest.mark.asyncio
async def test_describe_rejects_non_allowlisted_table(sql_ctx) -> None:
    out = await _describe_handler("memories")
    assert out["error"] == "rejected"


@pytest.mark.asyncio
async def test_query_select_returns_rows(sql_ctx) -> None:
    rows = await _query_handler("SELECT count(*) AS n FROM papers")
    assert rows == {"columns": ["n"], "rows": [[0]]}


@pytest.mark.asyncio
async def test_query_rejects_write(sql_ctx) -> None:
    out = await _query_handler("DELETE FROM papers")
    assert out["error"] == "rejected"
    assert "SELECT" in out["reason"] or "WITH" in out["reason"]


@pytest.mark.asyncio
async def test_query_caps_rows_at_200(sql_ctx) -> None:
    # sql_ctx already created session 1 + one run.  Add 201 more runs.
    for _ in range(201):
        await sql_ctx.conn.execute("INSERT INTO runs (session_id) VALUES (1)")
    await sql_ctx.conn.commit()
    out = await _query_handler("SELECT id FROM runs")
    assert len(out["rows"]) == 200


# ── Bug 3 regression: SQLite execution errors must return structured error ────


@pytest.mark.asyncio
async def test_query_execution_error_returns_dict_not_raises(sql_ctx) -> None:
    """Bug 3 regression: a valid-syntax but runtime-failing query (no such column)
    must return {"error": "query_failed", "reason": ...} rather than propagating
    an sqlite3.OperationalError / aiosqlite exception.

    This is distinct from a validation rejection ({"error": "rejected", ...});
    the different error key ensures the agent's self-repair path is NOT mistaken
    for a policy rejection.
    """
    # "no_such_col" does not exist in the papers table — triggers OperationalError.
    out = await _query_handler("SELECT no_such_col FROM papers")
    assert isinstance(out, dict), f"Expected dict, got {type(out)}: {out!r}"
    assert out.get("error") == "query_failed", (
        f"Bug 3: expected 'query_failed', got {out.get('error')!r}"
    )
    assert "reason" in out, f"Expected 'reason' key in error dict: {out!r}"
    # The reason should reference the column name or mention OperationalError.
    assert "no_such_col" in out["reason"] or "no such column" in out["reason"], (
        f"Expected sqlite error message in reason, got: {out['reason']!r}"
    )


@pytest.mark.asyncio
async def test_query_validation_rejection_still_returns_rejected_key(sql_ctx) -> None:
    """Validation rejections must still return {"error": "rejected", ...} (not "query_failed").

    This ensures the two error kinds remain distinct — the agent marks validation
    rejections as status='rejected' but treats execution failures as self-repairable.
    """
    out = await _query_handler("DELETE FROM papers")
    assert out.get("error") == "rejected", (
        f"Validation rejection must have error='rejected', got: {out.get('error')!r}"
    )
