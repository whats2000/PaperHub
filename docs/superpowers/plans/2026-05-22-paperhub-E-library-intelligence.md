# PaperHub Plan E — Library Intelligence + Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `_stub_library_stats` node with a real **SQL Agent** backed by a new in-process **read-only `sql` FastMCP server** (introspection-driven NL2SQL, `sqlglot`-validated, self-repair, library auto-attach), and add a **session+global Memory store** with its own write-capable **`memory` FastMCP server** (recall/add/edit/forget, scope-enforced), a router `memory` intent, and recall injection into `paper_qa` + `library_stats`.

**Architecture:** Two waves on one branch (`feat/plan-E-library-intelligence`, SRS v2.16). **Wave 1** stands up the `sql` MCP and the SQL Agent; **Wave 2** adds the `memories` table + `memory` MCP + memory node + recall injection. Both new MCP servers are in-process FastMCP sub-apps mounted on the existing FastAPI app exactly like `paperhub-papers` (§III-6) — reusing the request-context middleware + the client-headers contextvar, so loopback calls trace under the same `run_id`. The `sql` MCP is the SRS-mandated hard safety boundary (SELECT/WITH + table allowlist via `sqlglot`); the `memory` MCP is the only write-capable MCP surface, with deterministic scope/ownership enforcement. Out-of-scope SQL and ownership violations both surface as `tool_calls.status='rejected'` (closing the Plan B `RejectionPill` follow-up).

**Tech Stack:** `sqlglot` (new dep — AST-based SQL validation), SQLite FTS5 (memory recall, already used by `paper_content_fts`), FastMCP (`mcp.server.fastmcp`, existing), LangGraph + LiteLLM + aiosqlite (existing). No new frontend component — library auto-attach reuses the existing `SearchResultList`. New env: `PAPERHUB_SQL_AGENT_MODEL`, `PAPERHUB_SQL_ANSWER_MODEL`, `PAPERHUB_MEMORY_RECALL` (default on), `PAPERHUB_MEMORY_SEMANTIC` (default off, upgrade-path stub).

---

## Spec Coverage Summary

| SRS reference | Addressed by |
| --- | --- |
| §III-6 `sql` MCP row (read-only, allowlist, `sqlglot`, `rejected`) | Tasks 3, 4, 5 |
| §III-6 `memory` MCP row (write surface, scope-enforced, `rejected`) | Tasks 10 |
| §III-3 SQL Agent row (introspection NL2SQL, self-repair, read-and-act) | Tasks 6, 7 |
| §III-3 Memory node row (recall→decide→write) | Task 11 |
| UC-5 read-and-act (library auto-attach via `search_results`) | Task 7 |
| UC-7 remember / recall / edit / forget (session vs global) | Tasks 9, 10, 11 |
| FR-06 router `memory` intent (6 active intents) | Task 11 |
| FR-10 Memory store (table, tools, scope boundary, triggers, recall-on-by-default) | Tasks 9, 10, 12 |
| NFR-05 MCP scope boundary surfaced as `rejected` | Tasks 1, 4, 10 |
| §III-7 schema 7→8 tables (`memories` + FTS) | Task 9 |
| Plan B follow-up #2 — `RejectionPill` reachable | Tasks 1, 4 (verified end-to-end in Task 8 smoke) |

**Out of scope (deliberate):** DuckDB (dropped from SRS v2.16); semantic memory recall (env-flagged stub only — `PAPERHUB_MEMORY_SEMANTIC`, no Chroma-over-memories ingest in this plan); a dedicated memory-management UI (memory ops surface as ordinary chat turns; library auto-attach reuses `SearchResultList`).

---

## File Structure

```
backend/
├── pyproject.toml                                  # +sqlglot dep
├── .env.example                                    # +PAPERHUB_SQL_AGENT_MODEL / _ANSWER_MODEL / _MEMORY_RECALL / _MEMORY_SEMANTIC
├── mcp_servers.toml.example                        # uncomment+rewrite sql block (streamable_http); add memory block
├── src/paperhub/
│   ├── tracing/tracer.py                           # MODIFY — add mark_rejected + forced_status honoring
│   ├── mcp/
│   │   ├── server_context.py                       # MODIFY — add public require_request_context()
│   │   ├── mounting.py                             # NEW — generic mount_inprocess_mcp(app, server, path)
│   │   ├── server.py                               # MODIFY — mount_paperhub_papers_on delegates to mounting.py
│   │   ├── sql_safety.py                           # NEW — sqlglot SELECT/WITH + table allowlist validator
│   │   ├── sql_server.py                           # NEW — `sql` FastMCP: list_tables / describe / query
│   │   └── memory_server.py                        # NEW — `memory` FastMCP: recall / add / edit / forget
│   ├── agents/
│   │   ├── sql_agent.py                            # NEW — library_stats NL2SQL loop + library auto-attach
│   │   ├── memory_tools.py                         # NEW — recall/add/edit/forget dispatchers (scope-enforced)
│   │   ├── memory_node.py                          # NEW — `memory` intent handler (recall→decide→write)
│   │   ├── memory_recall.py                        # NEW — recall-injection helper (FTS top-k → context block)
│   │   ├── router.py                               # MODIFY — (prompt only) memory intent classification
│   │   ├── graph.py                                # MODIFY — wire library_stats + memory real nodes
│   │   └── stubs.py                                # (unchanged — slides keeps its stub)
│   ├── models/domain.py                            # MODIFY — add "memory" to Intent; AgentState recalled_memories
│   ├── api/chat.py                                 # MODIFY — library_stats + memory dispatch; client-headers ctx
│   ├── db/schema.sql                               # MODIFY — memories table + memories_fts + triggers
│   ├── db/migrate.py                               # MODIFY — idempotent memories migration
│   ├── llm/prompts/
│   │   ├── sql_planner_v1.yaml                     # NEW — NL2SQL planner (introspection-driven)
│   │   ├── sql_repair_v1.yaml                      # NEW — one-shot repair on SQL error
│   │   ├── sql_answer_v1.yaml                      # NEW — flagship answer phrasing (+SQL block)
│   │   ├── router_v1.yaml                          # MODIFY — add memory intent
│   │   └── memory_op_v1.yaml                       # NEW — memory node op/scope/content extraction
│   └── config.py                                   # MODIFY — 4 new settings
├── scripts/
│   ├── smoke_sql_agent.ps1                         # NEW — Wave 1 e2e (library_stats + rejected-row assert)
│   └── smoke_memory.ps1                            # NEW — Wave 2 e2e (remember→recall cross-session; edit)
└── tests/
    ├── test_tracer_rejected.py                     # NEW
    ├── test_mcp_mounting.py                        # NEW
    ├── test_sql_safety.py                          # NEW
    ├── test_sql_server.py                          # NEW
    ├── test_sql_agent.py                           # NEW
    ├── test_library_stats_dispatch.py              # NEW
    ├── test_memories_schema.py                     # NEW
    ├── test_memory_tools.py                        # NEW
    ├── test_memory_server.py                       # NEW
    ├── test_memory_node.py                         # NEW
    └── test_memory_recall.py                       # NEW
```

---

# Wave 1 — SQL Agent + `sql` MCP

### Task 1: Tracer `rejected` status support

NFR-05 requires out-of-scope MCP calls to land as `tool_calls.status='rejected'`. The tracer today only computes `ok`/`error`. Because loopback MCP calls trace through the **agent's** run-level tracer (the per-request server tracer would collide on `step_index`), the agent must be able to force a `rejected` status on the step it wraps around the MCP call.

**Files:**
- Modify: `backend/src/paperhub/tracing/tracer.py`
- Test: `backend/tests/test_tracer_rejected.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tracer_rejected.py
import aiosqlite
import pytest

from paperhub.tracing.tracer import Tracer


@pytest.mark.asyncio
async def test_mark_rejected_writes_rejected_status(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")

    async with tracer.step(agent="sql", tool="sql.query", model=None) as step:
        step.record_args({"sql": "DROP TABLE papers"})
        step.mark_rejected("verb 'DROP' not in {SELECT, WITH}")

    async with migrated_db.execute(
        "SELECT status, error FROM tool_calls WHERE run_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "rejected"
    assert "DROP" in row[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_tracer_rejected.py -v`
Expected: FAIL — `_StepBuffer` has no attribute `mark_rejected`.

- [ ] **Step 3: Implement `mark_rejected` + `forced_status` honoring**

In `tracer.py`, add a field + method to `_StepBuffer` (alongside the existing `forced_error`):

```python
@dataclass
class _StepBuffer:
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    token_in: int | None = None
    token_out: int | None = None
    forced_error: str | None = None
    forced_status: str | None = None  # e.g. "rejected" — overrides ok/error

    # ... existing record_args / record_result / record_tokens / mark_error ...

    def mark_rejected(self, message: str) -> None:
        """Force status='rejected' (NFR-05 scope boundary). Distinct from
        mark_error: a rejection is a deliberate policy stop, not a fault."""
        self.forced_status = "rejected"
        self.forced_error = message
```

In `step()`'s success (`else`) branch, honor `forced_status` before `forced_error`:

```python
    else:
        if buf.forced_status is not None:
            status, error = buf.forced_status, buf.forced_error
        elif buf.forced_error is not None:
            status, error = "error", buf.forced_error
        await self._write(buf, index, agent, tool, model, parent_step, started, status, error)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_tracer_rejected.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/tracing/tracer.py backend/tests/test_tracer_rejected.py
git commit -m "feat(tracer): add mark_rejected for NFR-05 scope-boundary rows"
```

---

### Task 2: Generic in-process MCP mount helper

The `sql` and `memory` servers mount exactly like `paperhub-papers` (request-context middleware + chained lifespan + settings copy). Extract the generic mounting into `mounting.py`, add a public `require_request_context()`, and refactor `mount_paperhub_papers_on` to delegate (DRY; keeps the working papers path covered by its existing tests).

**Files:**
- Create: `backend/src/paperhub/mcp/mounting.py`
- Modify: `backend/src/paperhub/mcp/server_context.py`, `backend/src/paperhub/mcp/server.py`
- Test: `backend/tests/test_mcp_mounting.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mcp_mounting.py
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from paperhub.mcp.mounting import mount_inprocess_mcp


def test_mount_inprocess_mcp_adds_route_and_middleware() -> None:
    app = FastAPI()
    server = FastMCP("demo", streamable_http_path="/")
    mount_inprocess_mcp(app, server, path="/mcp-demo")
    mounted = [r for r in app.routes if getattr(r, "path", "") == "/mcp-demo"]
    assert mounted, "sub-app not mounted at /mcp-demo"
```

```python
# also assert the public context accessor exists + raises cleanly when unset
def test_require_request_context_raises_runtimeerror_when_unset() -> None:
    from paperhub.mcp.server_context import require_request_context
    import pytest
    with pytest.raises(RuntimeError, match="request context"):
        require_request_context()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_mcp_mounting.py -v`
Expected: FAIL — `paperhub.mcp.mounting` does not exist.

- [ ] **Step 3: Add `require_request_context` to `server_context.py`**

```python
def require_request_context() -> PaperhubPapersRequestContext:
    """Fetch the active per-call MCP context or raise a clean RuntimeError.

    Shared by every in-process FastMCP server (papers / sql / memory): the
    middleware sets it from the X-Paperhub-* headers; tests prime it via
    set_request_context(). Translates the bare LookupError into a diagnostic.
    """
    try:
        return current_request_context()
    except LookupError as exc:
        raise RuntimeError(
            "in-process MCP tool invoked without a request context "
            "(no X-Paperhub-Session-Id header, and no fixture primed the contextvar)"
        ) from exc
```

- [ ] **Step 4: Create `mounting.py` (generic mount)**

```python
# backend/src/paperhub/mcp/mounting.py
"""Generic mounter for in-process FastMCP servers (papers / sql / memory).

Every PaperHub-owned FastMCP server is an ASGI sub-app on the main FastAPI
app, fronted by PaperhubPapersRequestContextMiddleware (which opens a fresh
aiosqlite.Connection + Tracer per call from the X-Paperhub-* headers) and
sharing the parent's resolved Settings. Starlette does not propagate a
mounted sub-app's lifespan, so we chain it into the parent's.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from paperhub.mcp.server import PaperhubPapersRequestContextMiddleware

__all__ = ["mount_inprocess_mcp"]


def mount_inprocess_mcp(app: FastAPI, server: FastMCP, *, path: str) -> None:
    sub_app = server.streamable_http_app()
    sub_app.add_middleware(PaperhubPapersRequestContextMiddleware)
    app.mount(path, sub_app)

    parent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _chained(target_app: FastAPI) -> AsyncIterator[None]:
        async with parent_lifespan(target_app), sub_app.router.lifespan_context(sub_app):
            sub_app.state.settings = target_app.state.settings
            yield

    app.router.lifespan_context = _chained
```

> Note: `mounting.py` imports the middleware from `server.py`; `server.py` must NOT import `mounting.py` (one-way) to avoid a cycle. The refactor in Step 5 keeps the middleware class defined in `server.py`.

- [ ] **Step 5: Refactor `mount_paperhub_papers_on` to delegate**

In `server.py`, replace the body of `mount_paperhub_papers_on` (keep the signature + the `_LOG.info` line) with a delegation, importing locally to avoid the cycle:

```python
def mount_paperhub_papers_on(app: FastAPI, server: FastMCP, *, path: str = "/mcp") -> None:
    from paperhub.mcp.mounting import mount_inprocess_mcp
    mount_inprocess_mcp(app, server, path=path)
    _LOG.info("mcp.server mounted name=%s path=%s db=%s", server.name, path, _safe_db_path_log())
```

- [ ] **Step 6: Run tests (new + papers regression)**

Run: `cd backend; uv run pytest tests/test_mcp_mounting.py tests/test_mcp_server.py tests/smoke -k papers -q`
Expected: PASS (new mount test + existing papers mount/server tests still green).
Run the broader MCP papers smoke if present: `cd backend; uv run pytest tests/test_mcp_server.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/mcp/mounting.py backend/src/paperhub/mcp/server_context.py backend/src/paperhub/mcp/server.py backend/tests/test_mcp_mounting.py
git commit -m "refactor(mcp): extract generic mount_inprocess_mcp + require_request_context"
```

---

### Task 3: SQL safety validator (`sqlglot`)

The hard NFR-05 boundary: `SELECT`/`WITH` only, every referenced table in the allowlist (`memories` deliberately excluded — owned by the `memory` MCP). Pure function, no I/O — fully unit-testable.

**Files:**
- Modify: `backend/pyproject.toml` (+`sqlglot`)
- Create: `backend/src/paperhub/mcp/sql_safety.py`
- Test: `backend/tests/test_sql_safety.py`

- [ ] **Step 1: Add the dependency**

Run: `cd backend; uv add sqlglot`
Expected: `pyproject.toml` gains `sqlglot` under `[project] dependencies`; lockfile updates.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_sql_safety.py
import pytest

from paperhub.mcp.sql_safety import (
    ALLOWED_TABLES,
    SqlValidationError,
    validate_read_only_sql,
)


def test_plain_select_on_allowlisted_table_passes() -> None:
    sql = "SELECT count(*) FROM papers WHERE session_id = 1"
    assert validate_read_only_sql(sql) == sql


def test_with_cte_passes() -> None:
    sql = "WITH t AS (SELECT id FROM paper_content) SELECT count(*) FROM t"
    assert validate_read_only_sql(sql)  # no raise


def test_join_across_allowlisted_tables_passes() -> None:
    validate_read_only_sql(
        "SELECT s.id FROM papers p JOIN paper_content pc ON p.paper_content_id = pc.id "
        "JOIN chat_sessions s ON p.session_id = s.id"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE papers",
        "DELETE FROM papers",
        "UPDATE papers SET enabled = 0",
        "INSERT INTO papers (session_id) VALUES (1)",
        "SELECT 1; DROP TABLE papers",            # multi-statement
        "PRAGMA table_info(papers)",
    ],
)
def test_non_select_verbs_rejected(sql: str) -> None:
    with pytest.raises(SqlValidationError):
        validate_read_only_sql(sql)


def test_query_against_non_allowlisted_table_rejected() -> None:
    with pytest.raises(SqlValidationError, match="memories"):
        validate_read_only_sql("SELECT * FROM memories")


def test_unknown_table_rejected() -> None:
    with pytest.raises(SqlValidationError):
        validate_read_only_sql("SELECT * FROM secrets")


def test_memories_excluded_from_allowlist() -> None:
    assert "memories" not in ALLOWED_TABLES
    assert "papers" in ALLOWED_TABLES
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_sql_safety.py -v`
Expected: FAIL — module `paperhub.mcp.sql_safety` does not exist.

- [ ] **Step 4: Implement the validator**

```python
# backend/src/paperhub/mcp/sql_safety.py
"""Deterministic read-only SQL gate for the `sql` MCP (SRS §III-6, II-1 #3).

LLM-authored SQL is parsed with sqlglot and accepted ONLY if it is a single
SELECT/WITH statement referencing tables in ALLOWED_TABLES. Everything else
(writes, DDL, PRAGMA, multi-statement, unknown/disallowed tables) raises
SqlValidationError — the caller turns that into a status='rejected' tool row.
The `memories` table is intentionally absent: it is owned by the `memory` MCP.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

# Mirrors the §III-6 sql-MCP allowlist (NOT memories).
ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "paper_content",
        "papers",
        "chunks",
        "chat_sessions",
        "messages",
        "runs",
        "tool_calls",
    }
)

# Also allow FTS shadow tables that hang off allowlisted content tables, so a
# query may use them, but never `memories`/`memories_fts`.
_ALLOWED_FTS: frozenset[str] = frozenset({"paper_content_fts"})

_ALLOWED_ROOTS: frozenset[str] = ALLOWED_TABLES | _ALLOWED_FTS


class SqlValidationError(ValueError):
    """Raised when LLM-authored SQL violates the read-only/allowlist policy."""


def validate_read_only_sql(sql: str) -> str:
    """Return ``sql`` unchanged if it is a safe single read statement; else raise.

    Safe == exactly one statement whose root is SELECT or WITH(...SELECT), and
    every table identifier is in ALLOWED_TABLES (+ allowed FTS shadows).
    """
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as exc:  # sqlglot.errors.ParseError and friends
        raise SqlValidationError(f"unparseable SQL: {exc}") from exc

    real = [s for s in statements if s is not None]
    if len(real) != 1:
        raise SqlValidationError(
            f"expected exactly one statement, got {len(real)} (multi-statement SQL is rejected)"
        )
    stmt = real[0]

    # Root must be SELECT (optionally fronted by a WITH/CTE wrapper).
    root = stmt
    if isinstance(root, exp.With):
        root = root.this
    if not isinstance(stmt, (exp.Select,)) and not (
        isinstance(stmt, exp.Subquery) and isinstance(stmt.this, exp.Select)
    ):
        # sqlglot parses `WITH ... SELECT` into a Select carrying a `with` arg,
        # so the common case is already exp.Select; reject anything else.
        if not isinstance(stmt, exp.Select):
            raise SqlValidationError(
                f"only SELECT / WITH...SELECT allowed, got {type(stmt).__name__.upper()}"
            )

    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name not in _ALLOWED_ROOTS:
            raise SqlValidationError(
                f"table {name!r} is not allowlisted "
                f"(allowed: {sorted(_ALLOWED_ROOTS)})"
            )
    return sql
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_sql_safety.py -v`
Expected: PASS (all parametrized rejections + accepts).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/paperhub/mcp/sql_safety.py backend/tests/test_sql_safety.py
git commit -m "feat(sql): add sqlglot read-only allowlist validator"
```

---

### Task 4: `sql` FastMCP server

Three read-only tools. `query` validates via Task 3 and, **on rejection, returns a structured `{"error": "rejected", "reason": ...}` payload (does NOT raise)** so the agent can mark its run-level step `rejected` (the per-request server tracer can't write — `step_index` would collide on the loopback path; see Task 1 rationale).

**Files:**
- Create: `backend/src/paperhub/mcp/sql_server.py`
- Test: `backend/tests/test_sql_server.py`

- [ ] **Step 1: Write the failing test** (drives tools directly via the request-context fixture, bypassing the wire)

```python
# backend/tests/test_sql_server.py
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


@pytest.mark.asyncio
async def test_describe_rejects_non_allowlisted_table(sql_ctx) -> None:
    out = await _describe_handler("memories")
    assert out == {"error": "rejected", "reason": pytest.approx} or out["error"] == "rejected"


@pytest.mark.asyncio
async def test_query_select_returns_rows(sql_ctx) -> None:
    rows = await _query_handler("SELECT count(*) AS n FROM papers")
    assert rows == {"columns": ["n"], "rows": [[0]]}


@pytest.mark.asyncio
async def test_query_rejects_write(sql_ctx) -> None:
    out = await _query_handler("DELETE FROM papers")
    assert out["error"] == "rejected"
    assert "SELECT" in out["reason"] or "WITH" in out["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_sql_server.py -v`
Expected: FAIL — module `paperhub.mcp.sql_server` does not exist.

- [ ] **Step 3: Implement the server + handlers**

```python
# backend/src/paperhub/mcp/sql_server.py
"""In-process read-only `sql` FastMCP server (SRS v2.16, Plan E Wave 1).

Tools (namespace `sql.*`):
  * list_tables()        -> list[str]            (the §III-6 allowlist)
  * describe(table)       -> list[{name,type}]    (PRAGMA table_info, allowlisted)
  * query(sql)            -> {columns, rows}       (sqlglot-validated SELECT/WITH)

Rejections (non-allowlisted table / non-SELECT verb) are returned as
{"error": "rejected", "reason": ...} rather than raised, so the calling
SQL Agent can mark its run-level tracer step status='rejected' (NFR-05).
The per-request server tracer must NOT write on the loopback path
(step_index collides with the agent's run-level tracer) — same contract as
the papers server's `_tool_step`.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from paperhub.mcp.server_context import require_request_context
from paperhub.mcp.sql_safety import ALLOWED_TABLES, SqlValidationError, validate_read_only_sql

SQL_SERVER_NAME = "sql"
_MAX_ROWS = 200


async def _list_tables_handler() -> list[str]:
    return sorted(ALLOWED_TABLES)


async def _describe_handler(table: str) -> Any:
    if table.lower() not in ALLOWED_TABLES:
        return {"error": "rejected", "reason": f"table {table!r} is not allowlisted"}
    ctx = require_request_context()
    async with ctx.conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)
    return [{"name": r[1], "type": r[2]} for r in rows]


async def _query_handler(sql: str) -> Any:
    try:
        validate_read_only_sql(sql)
    except SqlValidationError as exc:
        return {"error": "rejected", "reason": str(exc)}
    ctx = require_request_context()
    async with ctx.conn.execute(sql) as cur:
        fetched = await cur.fetchmany(_MAX_ROWS)
        columns = [d[0] for d in (cur.description or [])]
    return {"columns": columns, "rows": [list(r) for r in fetched]}


def build_paperhub_sql_server() -> FastMCP:
    server = FastMCP(SQL_SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True
    server.add_tool(
        _list_tables_handler,
        name="list_tables",
        description="List the read-only tables you may query (the allowlist).",
    )
    server.add_tool(
        _describe_handler,
        name="describe",
        description="Return [{name,type}] columns for one allowlisted table.",
    )
    server.add_tool(
        _query_handler,
        name="query",
        description=(
            "Run ONE read-only SQL statement (SELECT or WITH...SELECT) over the "
            "allowlisted tables. Returns {columns, rows}. Writes/DDL are rejected."
        ),
    )
    return server
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_sql_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/mcp/sql_server.py backend/tests/test_sql_server.py
git commit -m "feat(sql): add read-only sql FastMCP server (list_tables/describe/query)"
```

---

### Task 5: Mount the `sql` server + register it

Mount at `/mcp-sql` in `create_app`, and add the `sql` `[[server]]` block (streamable_http, loopback URL) to `mcp_servers.toml.example` so the registry advertises `sql.*` to the agent.

**Files:**
- Modify: `backend/src/paperhub/app.py`
- Modify: `backend/mcp_servers.toml.example`
- Test: `backend/tests/test_sql_server.py` (add a wire smoke)

- [ ] **Step 1: Write the failing wire test**

```python
# append to backend/tests/test_sql_server.py
import httpx
import pytest
from asgi_lifespan import LifespanManager

from paperhub.app import create_app


@pytest.mark.asyncio
async def test_sql_mcp_mounted_and_reachable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            # MCP initialize handshake on the sql sub-app path.
            resp = await client.post(
                "/mcp-sql/",
                headers={
                    "X-Paperhub-Session-Id": "1",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            assert resp.status_code in (200, 202)
```

> If `asgi-lifespan` is not already a dev dep, install it: `cd backend; uv add --dev asgi-lifespan`. (Mirror however `tests/test_mcp_server.py` boots the app if it already has a helper — prefer the existing pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_sql_server.py::test_sql_mcp_mounted_and_reachable -v`
Expected: FAIL — nothing mounted at `/mcp-sql`.

- [ ] **Step 3: Mount in `create_app`**

In `app.py`, immediately after the existing `mount_paperhub_papers_on(app, build_paperhub_papers_server(), path="/mcp")` line:

```python
    from paperhub.mcp.mounting import mount_inprocess_mcp
    from paperhub.mcp.sql_server import build_paperhub_sql_server

    mount_inprocess_mcp(app, build_paperhub_sql_server(), path="/mcp-sql")
```

- [ ] **Step 4: Add the `sql` server block to `mcp_servers.toml.example`**

Replace the commented-out future sqlite block with a live streamable_http block (port matches the backend's own bind; mirror the `papers` block's URL convention):

```toml
[[server]]
name = "sql"
transport = "streamable_http"
url = "http://localhost:8000/mcp-sql"
expose = ["list_tables", "describe", "query"]
timeout_seconds = 8.0
```

- [ ] **Step 5: Run the wire test + the papers regression**

Run: `cd backend; uv run pytest tests/test_sql_server.py -v`
Expected: PASS
Run: `cd backend; uv run pytest tests/test_mcp_server.py -v`
Expected: PASS (papers still mounts; chained lifespans nest cleanly)

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/app.py backend/mcp_servers.toml.example backend/tests/test_sql_server.py
git commit -m "feat(sql): mount sql MCP at /mcp-sql + register in mcp_servers.toml"
```

---

### Task 6: SQL Agent node (NL2SQL loop + self-repair + answer)

`library_stats` handler. Reads `effective_query` + `response_language`. Loop: introspect (`sql.list_tables` → `sql.describe`) → small-model emits SQL → `sql.query` → on error/empty, **self-repair once** → flagship phrases the answer with the executed SQL in a fenced block. Each MCP call is wrapped in the run-level `tracer.step`; a `{"error":"rejected",...}` result marks the step `rejected` (Task 1).

**Files:**
- Create: `backend/src/paperhub/agents/sql_agent.py`
- Create: `backend/src/paperhub/llm/prompts/sql_planner_v1.yaml`, `sql_repair_v1.yaml`, `sql_answer_v1.yaml`
- Modify: `backend/src/paperhub/config.py` (+`sql_agent_model`, `sql_answer_model`)
- Test: `backend/tests/test_sql_agent.py`

- [ ] **Step 1: Add config settings**

In `config.py` `Settings` add fields and load them (small for planner/repair, flagship for the answer):

```python
    # ── 3. LLM model selection ──
    sql_agent_model: str        # NL2SQL planner + repair (small tier)
    sql_answer_model: str       # answer phrasing (flagship tier)
```

```python
    # in load_settings(), alongside the other model envs:
    sql_agent_model=os.environ.get("PAPERHUB_SQL_AGENT_MODEL", "gemini/gemini-3.1-flash-lite"),
    sql_answer_model=os.environ.get("PAPERHUB_SQL_ANSWER_MODEL", "gemini/gemini-2.5-pro"),
```

Add the two vars to `.env.example` with brief comments.

- [ ] **Step 2: Write the prompt slots**

`sql_planner_v1.yaml`:

```yaml
system: |
  You translate a user's question about THEIR PaperHub library into ONE SQLite
  read query. You may ONLY read these tables: paper_content, papers, chunks,
  chat_sessions, messages, runs, tool_calls. (There is no `memories` table here.)
  Workflow: call sql.list_tables, then sql.describe on the tables you need, then
  emit ONE statement that is a single SELECT or WITH...SELECT. Never write/DDL.
  When the question is about "papers I have", join papers (membership) to
  paper_content (identity) on papers.paper_content_id = paper_content.id and scope
  by papers.session_id when the question is about THIS chat.
  Respond with ONLY the SQL, no prose, no markdown fence.
user: |
  Current chat session_id: {session_id}
  Question: {question}
```

`sql_repair_v1.yaml`:

```yaml
system: |
  Your previous SQLite query failed or returned nothing. Fix it. Same rules:
  ONE SELECT/WITH statement over the allowlisted tables only. Output ONLY the
  corrected SQL, no prose.
user: |
  Question: {question}
  Schema (relevant tables): {schema}
  Previous SQL: {previous_sql}
  Error / result note: {error}
```

`sql_answer_v1.yaml`:

```yaml
system: |
  Answer the user's question about their library from the SQL result rows.
  Write in {response_language}. Be concise and concrete (use the numbers).
  End with the exact SQL you ran inside a ```sql fenced block.
user: |
  Question: {question}
  SQL: {sql}
  Columns: {columns}
  Rows (JSON): {rows}
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_sql_agent.py
import json

import aiosqlite
import pytest

from paperhub.agents.sql_agent import sql_agent_stream
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.mcp.registry import MCPRegistry
from paperhub.tracing.tracer import Tracer


class _FakeRegistry:
    """Stand-in MCPRegistry that answers sql.* calls deterministically."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "sql.list_tables":
            return ["papers", "paper_content"]
        if name == "sql.describe":
            return [{"name": "session_id", "type": "INTEGER"}]
        if name == "sql.query":
            return {"columns": ["n"], "rows": [[3]]}
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_sql_agent_emits_sql_runs_and_answers(
    migrated_db: aiosqlite.Connection,
) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    adapter = LiteLlmAdapter()
    state: AgentState = {
        "run_id": 1, "session_id": 1, "user_message": "how many papers do I have?",
        "effective_query": "how many papers do I have?", "response_language": "English",
    }
    # planner returns SQL; answer returns prose. (stream() mock + structured-free path)
    tokens: list[str] = []
    async for tok in sql_agent_stream(
        state, adapter=adapter, tracer=tracer, registry=_FakeRegistry(),
        planner_model="m", answer_model="m",
        planner_mock="SELECT count(*) AS n FROM papers",
        answer_mock="You have 3 papers.\n```sql\nSELECT count(*) AS n FROM papers\n```",
    ):
        tokens.append(tok)
    out = "".join(tokens)
    assert "3 papers" in out
    assert "```sql" in out
    # the executed SQL was traced
    async with migrated_db.execute(
        "SELECT tool FROM tool_calls WHERE run_id = 1 AND tool LIKE 'sql.%'"
    ) as cur:
        tools = {r[0] for r in await cur.fetchall()}
    assert "sql.query" in tools
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_sql_agent.py -v`
Expected: FAIL — `paperhub.agents.sql_agent` does not exist.

- [ ] **Step 5: Implement the SQL agent**

```python
# backend/src/paperhub/agents/sql_agent.py
"""SQL Agent — the `library_stats` intent (SRS v2.16, §III-3).

Introspection-driven NL2SQL over the read-only `sql` MCP, one self-repair on
error/empty, then a flagship answer that embeds the executed SQL. Streams the
final answer token-by-token (so the chat SSE `token` path is reused). Each
sql.* call is wrapped by the run-level Tracer; a rejected result marks the
step status='rejected' (NFR-05).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


class _Registry(Protocol):
    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any: ...


async def _mcp_call(
    tracer: Tracer, registry: _Registry, tool: str, args: dict[str, Any],
) -> Any:
    async with tracer.step(agent="sql", tool=tool, model=None) as step:
        step.record_args(args)
        result = await registry.call(tool, args)
        if isinstance(result, dict) and result.get("error") == "rejected":
            step.mark_rejected(str(result.get("reason", "rejected")))
            step.record_result(result)
        else:
            step.record_result({"ok": True})
        return result


async def _plan_sql(
    adapter: LlmAdapter, tracer: Tracer, *, slot: str, model: str,
    variables: dict[str, Any], mock: str | None,
) -> str:
    kwargs: dict[str, Any] = {}
    if mock is not None:
        kwargs["mock_response"] = mock
    parts: list[str] = []
    async with tracer.step(agent="sql", tool="sql:plan", model=model) as step:
        step.record_args(variables)
        async for tok in adapter.stream(slot=slot, variables=variables, model=model, **kwargs):
            parts.append(tok)
        sql = "".join(parts).strip().strip("`").removeprefix("sql").strip()
        step.record_result({"sql": sql})
    return sql


async def sql_agent_stream(
    state: AgentState,
    *,
    adapter: LlmAdapter,
    tracer: Tracer,
    registry: _Registry,
    planner_model: str,
    answer_model: str,
    planner_mock: str | None = None,
    repair_mock: str | None = None,
    answer_mock: str | None = None,
) -> AsyncIterator[str]:
    question = effective_query(state)
    language = response_language(state)
    session_id = state.get("session_id")

    # 1. Introspect (best-effort; failures are non-fatal context loss).
    await _mcp_call(tracer, registry, "sql.list_tables", {})

    # 2. Plan SQL.
    sql = await _plan_sql(
        adapter, tracer, slot="sql_planner/v1", model=planner_model,
        variables={"session_id": session_id, "question": question}, mock=planner_mock,
    )

    # 3. Execute, self-repair once on rejection/error/empty.
    result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
    rows = result.get("rows") if isinstance(result, dict) else None
    if (not isinstance(result, dict)) or ("error" in result) or (not rows):
        schema = await _mcp_call(
            tracer, registry, "sql.describe", {"table": "papers"},
        )
        repaired = await _plan_sql(
            adapter, tracer, slot="sql_repair/v1", model=planner_model,
            variables={
                "question": question, "schema": json.dumps(schema),
                "previous_sql": sql,
                "error": (result.get("reason") or result.get("error") or "empty result")
                if isinstance(result, dict) else "execution failed",
            },
            mock=repair_mock if repair_mock is not None else planner_mock,
        )
        sql = repaired
        result = await _mcp_call(tracer, registry, "sql.query", {"sql": sql})
        rows = result.get("rows") if isinstance(result, dict) else []

    columns = result.get("columns", []) if isinstance(result, dict) else []
    rows = rows or []

    # 4. Flagship answer, streamed.
    kwargs: dict[str, Any] = {}
    if answer_mock is not None:
        kwargs["mock_response"] = answer_mock
    async with tracer.step(agent="sql", tool="sql:answer", model=answer_model) as step:
        step.record_args({"sql": sql, "row_count": len(rows)})
        collected: list[str] = []
        async for tok in adapter.stream(
            slot="sql_answer/v1",
            variables={
                "question": question, "sql": sql, "response_language": language,
                "columns": json.dumps(columns), "rows": json.dumps(rows),
            },
            model=answer_model, **kwargs,
        ):
            collected.append(tok)
            yield tok
        step.record_result({"length": sum(len(c) for c in collected)})
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_sql_agent.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/agents/sql_agent.py backend/src/paperhub/llm/prompts/sql_planner_v1.yaml backend/src/paperhub/llm/prompts/sql_repair_v1.yaml backend/src/paperhub/llm/prompts/sql_answer_v1.yaml backend/src/paperhub/config.py backend/.env.example backend/tests/test_sql_agent.py
git commit -m "feat(sql): SQL Agent — introspection NL2SQL + self-repair + answer"
```

---

### Task 7: Wire `library_stats` into chat.py + graph.py (+ optional library auto-attach)

Replace the `_stub_library_stats` path. The chat endpoint already dispatches per-intent (it does NOT route everything through `build_graph`); add a `library_stats` branch that streams `sql_agent_stream` tokens as SSE `token` events, mirroring the `chitchat` branch. Set the MCP **client-headers context** for the turn (so `sql.*` loopback calls carry `X-Paperhub-Session-Id`/`Run-Id`) — mirror exactly what the `paper_search` branch does. Update `graph.py`'s `library_stats` node for graph-level completeness.

**Files:**
- Modify: `backend/src/paperhub/api/chat.py`
- Modify: `backend/src/paperhub/agents/graph.py`
- Test: `backend/tests/test_library_stats_dispatch.py`

- [ ] **Step 1: Write the failing test** (SSE-level, mirror `tests/test_chat_sse.py` patterns — monkeypatch `sql_agent_stream`)

```python
# backend/tests/test_library_stats_dispatch.py
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

import paperhub.api.chat as chat_mod
from paperhub.app import create_app


@pytest.mark.asyncio
async def test_library_stats_streams_sql_agent_answer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_INPROCESS_MODELS", "1")
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")

    async def fake_sql_agent_stream(state, **kwargs):
        for tok in ["You have ", "3 papers."]:
            yield tok

    # router classifies library_stats; sql agent is faked.
    monkeypatch.setattr(chat_mod, "sql_agent_stream", fake_sql_agent_stream)

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/chat",
                json={
                    "message": "how many papers do I have?",
                    "session_id": None,
                    "router_mock": json.dumps({
                        "intent": "library_stats", "model_tier": "small",
                        "confidence": 0.95, "reasoning": "stats",
                        "resolved_query": "how many papers do I have?",
                        "response_language": "English",
                    }),
                },
            )
            body = resp.text
    assert "3 papers." in body
    assert "event: final" in body
```

> Match the actual `/chat` request schema + `router_mock` plumbing used by `tests/test_chat_sse.py`; if the test harness injects the router mock via `GraphDeps`/env rather than the request body, follow that existing convention instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_library_stats_dispatch.py -v`
Expected: FAIL — library_stats still returns the stub text.

- [ ] **Step 3: Add the dispatch branch in `chat.py`**

Import at top: `from paperhub.agents.sql_agent import sql_agent_stream`. In the intent dispatch (where `chitchat` / `paper_search` / `clarify` branches live), add before the `else: stub` fallback:

```python
        elif intent == "library_stats":
            registry = request.app.state.mcp_registry
            chunks: list[str] = []
            # Reuse the same client-headers context the paper_search branch sets
            # so sql.* loopback calls carry X-Paperhub-Session-Id / X-Paperhub-Run-Id.
            async with _client_headers(session_id=session_id, run_id=run_id):
                async for token in sql_agent_stream(
                    state, adapter=adapter, tracer=tracer, registry=registry,
                    planner_model=settings.sql_agent_model,
                    answer_model=settings.sql_answer_model,
                ):
                    chunks.append(token)
                    token_evt = TokenEvent(run_id=run_id, branch="", text=token)
                    yield {"event": "token", "data": token_evt.model_dump_json(exclude={"type"})}
            final_content = "".join(chunks)
```

> `_client_headers(...)` is whatever context manager / setter the `paper_search` branch already uses to prime `paperhub.mcp.client_context` (look for `set_client_headers_context` / `ClientHeadersContext` near the existing `paper_search` dispatch and reuse it verbatim). If the branch sets it once at the top of `stream_events` for all intents, no per-branch wrapping is needed — confirm and match.

- [ ] **Step 4: Replace the graph.py stub node**

In `graph.py`, the `library_stats` node is used for graph-level completeness only (chat.py drives the real path). Keep it simple but real-ish by delegating to the stub-free message; the minimal change that removes the "not yet wired" text:

```python
    async def _library_stats(state: AgentState) -> AgentState:
        # chat.py drives the streaming SQL agent directly; this node exists for
        # build_graph completeness. It returns a terse marker (the SSE path is
        # the user-facing one).
        return {**state, "final_response": "library_stats handled by the SQL Agent (see chat SSE path)."}
```

Replace `g.add_node("library_stats", _stub_library_stats)` with `g.add_node("library_stats", _library_stats)` and delete the now-unused `_stub_library_stats` inner function. (Leave `_stub_slides` as-is — Plan F.)

- [ ] **Step 5: Run tests**

Run: `cd backend; uv run pytest tests/test_library_stats_dispatch.py tests/test_graph.py -v`
Expected: PASS (and existing graph tests still green).

- [ ] **Step 6: (Read-and-act) Library auto-attach** — emit `search_results` for "find papers I already have" asks.

Extend `sql_agent_stream` to optionally yield a sentinel of library candidates the chat layer auto-attaches. Add a structured pre-step: when the planner's question is a "find/recommend from my library" ask, the agent runs a `search_library`-shaped query (via `sql.query` over `paper_content`) and returns up to 5 `SearchCandidate`s (`paper_id="library:<id>"`), ≤2 with `finalize=True`. Rather than overload the token stream, have `sql_agent_stream` accept an `on_candidates` callback the chat layer passes:

```python
# sql_agent.py signature addition:
#   on_candidates: Callable[[list[SearchCandidate]], Awaitable[None]] | None = None
# After producing rows for a "find papers" question, build SearchCandidate list
# from paper_content columns and `await on_candidates(cands)` before the answer.
```

Write the failing test first (assert the callback receives `library:<id>` candidates with exactly ≤2 `finalize=True`), then implement. In `chat.py`'s `library_stats` branch, pass `on_candidates=lambda cands: _emit_and_persist_search_results(cands, ...)` reusing the **exact** `_process_search_results` + `runs.search_results_json` persistence + `search_results` SSE emission the `paper_search` branch already calls (Explore refs: `chat.py` `_process_search_results`, the `SearchResultsEvent` block). No new frontend code — `SearchResultList` renders `library:` candidates unchanged.

> If, in review, the callback indirection feels heavier than the value, the fallback is to keep Wave 1 prose-only and ship library auto-attach as a Wave 1.1 follow-up. Decide at implementation time based on how cleanly `_process_search_results` factors out; **prefer shipping it** (it's the UX-first payoff the spec calls for). Either way, do not duplicate the auto-attach/persist logic — reuse the paper_search helpers.

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/api/chat.py backend/src/paperhub/agents/graph.py backend/src/paperhub/agents/sql_agent.py backend/tests/test_library_stats_dispatch.py backend/tests/test_sql_agent.py
git commit -m "feat(library_stats): wire SQL Agent into chat dispatch + library auto-attach"
```

---

### Task 8: Wave 1 smoke script

Prove end-to-end (mocked LLM, no key): a `library_stats` turn returns an answer with a fenced SQL block AND a deliberately out-of-scope query produces a `status='rejected'` `tool_calls` row (closes Plan B follow-up #2 / acceptance I-8 #1).

**Files:**
- Create: `backend/scripts/smoke_sql_agent.ps1`

- [ ] **Step 1: Write the smoke script**

```powershell
# backend/scripts/smoke_sql_agent.ps1
# Wave 1 e2e: boots the backend on :8771 with a mocked router+LLM, sends a
# library_stats turn, asserts the answer + a rejected tool_calls row.
$ErrorActionPreference = "Stop"
$env:PAPERHUB_WORKSPACE = Join-Path $PSScriptRoot "..\workspace_smoke_sql"
$env:PAPERHUB_INPROCESS_MODELS = "1"
$env:PAPERHUB_BOOT_BANNER = "0"
# ... boot uvicorn on 127.0.0.1:8771 (mirror scripts/smoke_chat.ps1 boot/teardown) ...
# POST /chat with router_mock=library_stats and a planner that emits an
# allowlisted SELECT; assert the SSE 'final' contains '```sql'.
# Then directly call the sql MCP query handler with "DROP TABLE papers" via the
# /mcp-sql wire OR a second /chat turn whose planner emits a write, and assert a
# tool_calls row with status='rejected' exists (uv run paperhub-replay --run-id N).
Write-Host "smoke_sql_agent: OK"
```

> Mirror the boot/teardown + `try/finally` of the existing `scripts/smoke_chat.ps1` exactly (port, health-poll, process kill). The assertions: (1) `final` event body contains a ` ```sql ` fence; (2) a `tool_calls` row for the run has `status='rejected'` after a write-SQL turn.

- [ ] **Step 2: Run it**

Run: `cd backend; .\scripts\smoke_sql_agent.ps1`
Expected: prints `smoke_sql_agent: OK`; exit code 0.

- [ ] **Step 3: Run the full Wave 1 gates**

Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/smoke_sql_agent.ps1
git commit -m "test(sql): Wave 1 e2e smoke (answer + rejected-row assert)"
```

---

# Wave 2 — Session + Global Memory

### Task 9: `memories` table + FTS5 + migration

Add the 8th table + an FTS5 shadow (mirrors the existing `paper_content_fts` pattern) + sync triggers, with an idempotent migration.

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`
- Modify: `backend/src/paperhub/db/migrate.py`
- Test: `backend/tests/test_memories_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memories_schema.py
import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_memories_table_and_fts_exist(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table') AND name IN ('memories','memories_fts')"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert {"memories", "memories_fts"} <= names


@pytest.mark.asyncio
async def test_global_requires_null_session(migrated_db: aiosqlite.Connection) -> None:
    # scope='global' with a session_id violates the CHECK
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO memories (scope, session_id, content) VALUES ('global', 1, 'x')"
        )


@pytest.mark.asyncio
async def test_fts_sync_on_insert(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'flow matching survey')"
    )
    await migrated_db.commit()
    async with migrated_db.execute(
        "SELECT m.content FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH 'flow'"
    ) as cur:
        rows = await cur.fetchall()
    assert rows and "flow matching" in rows[0][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_memories_schema.py -v`
Expected: FAIL — no `memories` table.

- [ ] **Step 3: Add DDL to `schema.sql`** (after `tool_calls`, mirroring `paper_content_fts` triggers)

```sql
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL CHECK (scope IN ('session', 'global')),
    session_id  INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((scope = 'global') = (session_id IS NULL))
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
```

- [ ] **Step 4: Add the idempotent migration in `migrate.py`**

The whole block above is `CREATE ... IF NOT EXISTS`, so `apply_schema`'s `executescript(schema.sql)` already creates it on fresh + existing DBs. Add an explicit guard near the other column-add migrations so an older DB created before this schema definitely picks it up even if `schema.sql` execution is partial:

```python
    # v2.16 — memories table + FTS (idempotent; schema.sql also creates these).
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    ) as cur:
        has_memories = await cur.fetchone() is not None
    if not has_memories:
        # re-run just the memories DDL section (kept identical to schema.sql)
        await conn.executescript(_MEMORIES_DDL)
        await conn.commit()
```

where `_MEMORIES_DDL` is a module-level string holding the exact DDL from Step 3.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend; uv run pytest tests/test_memories_schema.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/db/schema.sql backend/src/paperhub/db/migrate.py backend/tests/test_memories_schema.py
git commit -m "feat(db): add memories table + FTS5 + sync triggers (schema 7->8 tables)"
```

---

### Task 10: Memory dispatchers (scope-enforced) + `memory` MCP server + mount

The write surface. Dispatchers in `memory_tools.py` (pure DB, unit-testable); the FastMCP server wraps them and converts `MemoryScopeError` into a `rejected` payload. Mount at `/mcp-memory`, register in `mcp_servers.toml.example`.

**Files:**
- Create: `backend/src/paperhub/agents/memory_tools.py`
- Create: `backend/src/paperhub/mcp/memory_server.py`
- Modify: `backend/src/paperhub/app.py`, `backend/mcp_servers.toml.example`
- Test: `backend/tests/test_memory_tools.py`, `backend/tests/test_memory_server.py`

- [ ] **Step 1: Write the failing dispatcher test**

```python
# backend/tests/test_memory_tools.py
import aiosqlite
import pytest

from paperhub.agents.memory_tools import (
    MemoryScopeError,
    add_memory,
    edit_memory,
    forget_memory,
    recall_memories,
)


@pytest.fixture
async def two_sessions(migrated_db: aiosqlite.Connection):
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # id 1
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # id 2
    await migrated_db.commit()
    return migrated_db


@pytest.mark.asyncio
async def test_add_and_recall_session(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=1, content="comparing MoE routing papers", scope="session")
    assert isinstance(mid, int)
    hits = await recall_memories(two_sessions, session_id=1, query="MoE routing", scope="both")
    assert any("MoE routing" in h.content for h in hits)


@pytest.mark.asyncio
async def test_global_recall_crosses_sessions(two_sessions) -> None:
    await add_memory(two_sessions, session_id=None, content="answer in Traditional Chinese", scope="global")
    hits = await recall_memories(two_sessions, session_id=2, query="Chinese", scope="both")
    assert any("Traditional Chinese" in h.content for h in hits)


@pytest.mark.asyncio
async def test_session_recall_excludes_other_sessions(two_sessions) -> None:
    await add_memory(two_sessions, session_id=1, content="session-one secret", scope="session")
    hits = await recall_memories(two_sessions, session_id=2, query="secret", scope="both")
    assert all("session-one secret" not in h.content for h in hits)


@pytest.mark.asyncio
async def test_edit_other_session_memory_rejected(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=1, content="owned by 1", scope="session")
    with pytest.raises(MemoryScopeError):
        await edit_memory(two_sessions, session_id=2, memory_id=mid, content="hijack")


@pytest.mark.asyncio
async def test_forget_global_allowed_from_any_session(two_sessions) -> None:
    mid = await add_memory(two_sessions, session_id=None, content="global note", scope="global")
    await forget_memory(two_sessions, session_id=2, memory_id=mid)  # no raise
    hits = await recall_memories(two_sessions, session_id=2, query="global", scope="both")
    assert not hits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_memory_tools.py -v`
Expected: FAIL — module `paperhub.agents.memory_tools` does not exist.

- [ ] **Step 3: Implement `memory_tools.py`**

```python
# backend/src/paperhub/agents/memory_tools.py
"""Memory dispatchers (SRS v2.16 FR-10). Pure DB ops over the `memories`
table; scope/ownership enforced deterministically (NFR-05).

scope:
  * 'session' — bound to a chat session_id (visible only to that session)
  * 'global'  — session_id NULL, visible to every session

recall returns session-scoped rows for the caller's session plus all global
rows (scope='both'), or restricts to one scope. edit/forget refuse to touch a
memory owned by a different session (global is editable from anywhere).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import aiosqlite

Scope = Literal["session", "global"]
RecallScope = Literal["session", "global", "both"]


class MemoryScopeError(Exception):
    """Raised when an edit/forget targets a memory the caller doesn't own."""


@dataclass(frozen=True)
class MemoryRow:
    id: int
    scope: str
    session_id: int | None
    content: str
    created_at: str
    updated_at: str


_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _fts_match(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression: OR of quoted alnum tokens."""
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


async def add_memory(
    conn: aiosqlite.Connection, *, session_id: int | None, content: str, scope: Scope,
) -> int:
    bound = None if scope == "global" else session_id
    if scope == "session" and bound is None:
        raise MemoryScopeError("session-scoped memory requires a session_id")
    await conn.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES (?, ?, ?)",
        (scope, bound, content),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def recall_memories(
    conn: aiosqlite.Connection, *, session_id: int | None, query: str,
    scope: RecallScope = "both", limit: int = 5,
) -> list[MemoryRow]:
    match = _fts_match(query)
    if match is None:
        return []
    # scope predicate
    if scope == "session":
        where = "m.scope = 'session' AND m.session_id = ?"
        params: tuple = (match, session_id, limit)
    elif scope == "global":
        where = "m.scope = 'global'"
        params = (match, limit)
    else:  # both
        where = "(m.scope = 'global' OR (m.scope = 'session' AND m.session_id = ?))"
        params = (match, session_id, limit)
    sql = (
        "SELECT m.id, m.scope, m.session_id, m.content, m.created_at, m.updated_at "
        "FROM memories_fts f JOIN memories m ON m.id = f.rowid "
        f"WHERE memories_fts MATCH ? AND {where} ORDER BY rank LIMIT ?"
    )
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [MemoryRow(*r) for r in rows]


async def _owned_or_raise(
    conn: aiosqlite.Connection, *, session_id: int | None, memory_id: int,
) -> None:
    async with conn.execute(
        "SELECT scope, session_id FROM memories WHERE id = ?", (memory_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise MemoryScopeError(f"memory {memory_id} not found")
    scope, owner = row
    if scope == "session" and owner != session_id:
        raise MemoryScopeError(
            f"memory {memory_id} belongs to another session; cannot modify"
        )


async def edit_memory(
    conn: aiosqlite.Connection, *, session_id: int | None, memory_id: int, content: str,
) -> None:
    await _owned_or_raise(conn, session_id=session_id, memory_id=memory_id)
    await conn.execute(
        "UPDATE memories SET content = ?, updated_at = datetime('now') WHERE id = ?",
        (content, memory_id),
    )
    await conn.commit()


async def forget_memory(
    conn: aiosqlite.Connection, *, session_id: int | None, memory_id: int,
) -> None:
    await _owned_or_raise(conn, session_id=session_id, memory_id=memory_id)
    await conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    await conn.commit()
```

- [ ] **Step 4: Run dispatcher test to verify it passes**

Run: `cd backend; uv run pytest tests/test_memory_tools.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing server test**

```python
# backend/tests/test_memory_server.py
import aiosqlite
import pytest

from paperhub.mcp.memory_server import (
    _add_handler,
    _edit_handler,
    _recall_handler,
)
from paperhub.mcp.server_context import (
    PaperhubPapersRequestContext,
    reset_request_context,
    set_request_context,
)
from paperhub.tracing.tracer import Tracer


@pytest.fixture
async def mem_ctx(migrated_db: aiosqlite.Connection):
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # 1
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")  # 2
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    ctx = PaperhubPapersRequestContext(
        conn=migrated_db, session_id=2, run_id=1, tracer=tracer, caller_supplied_run=True,
    )
    token = set_request_context(ctx)
    try:
        yield ctx
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_add_then_recall(mem_ctx) -> None:
    out = await _add_handler(content="prefers concise answers", scope="session")
    assert out["id"]
    hits = await _recall_handler(query="concise", scope="both")
    assert any("concise" in h["content"] for h in hits)


@pytest.mark.asyncio
async def test_edit_other_session_returns_rejected(mem_ctx) -> None:
    # seed a memory owned by session 1 (the ctx session is 2)
    await mem_ctx.conn.execute(
        "INSERT INTO memories (scope, session_id, content) VALUES ('session', 1, 'owned by 1')"
    )
    await mem_ctx.conn.commit()
    async with mem_ctx.conn.execute("SELECT last_insert_rowid()") as cur:
        mid = (await cur.fetchone())[0]
    out = await _edit_handler(memory_id=int(mid), content="hijack")
    assert out["error"] == "rejected"
```

- [ ] **Step 6: Implement `memory_server.py`**

```python
# backend/src/paperhub/mcp/memory_server.py
"""In-process write-capable `memory` FastMCP server (SRS v2.16 FR-10).

Tools (namespace `memory.*`):
  * recall(query, scope='both')      -> list[{id, scope, content}]
  * add(content, scope)              -> {id}
  * edit(memory_id, content)         -> {ok} | {error: rejected}
  * forget(memory_id)                -> {ok} | {error: rejected}

The ONLY write-capable MCP surface. Scope/ownership is enforced by
memory_tools; a MemoryScopeError becomes a {"error":"rejected", ...} payload
so the calling agent marks its run-level step status='rejected' (NFR-05).
Owns the `memories` table exclusively (the `sql` MCP can't see it).
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from paperhub.agents.memory_tools import (
    MemoryScopeError,
    add_memory,
    edit_memory,
    forget_memory,
    recall_memories,
)
from paperhub.mcp.server_context import require_request_context

MEMORY_SERVER_NAME = "memory"


async def _recall_handler(query: str, scope: str = "both") -> list[dict[str, Any]]:
    ctx = require_request_context()
    hits = await recall_memories(
        ctx.conn, session_id=ctx.session_id, query=query, scope=scope,  # type: ignore[arg-type]
    )
    return [asdict(h) for h in hits]


async def _add_handler(content: str, scope: str) -> dict[str, Any]:
    ctx = require_request_context()
    try:
        mid = await add_memory(
            ctx.conn, session_id=ctx.session_id, content=content, scope=scope,  # type: ignore[arg-type]
        )
    except MemoryScopeError as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"id": mid}


async def _edit_handler(memory_id: int, content: str) -> dict[str, Any]:
    ctx = require_request_context()
    try:
        await edit_memory(ctx.conn, session_id=ctx.session_id, memory_id=memory_id, content=content)
    except MemoryScopeError as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"ok": True}


async def _forget_handler(memory_id: int) -> dict[str, Any]:
    ctx = require_request_context()
    try:
        await forget_memory(ctx.conn, session_id=ctx.session_id, memory_id=memory_id)
    except MemoryScopeError as exc:
        return {"error": "rejected", "reason": str(exc)}
    return {"ok": True}


def build_paperhub_memory_server() -> FastMCP:
    server = FastMCP(MEMORY_SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True
    server.add_tool(_recall_handler, name="recall",
                    description="Recall remembered facts relevant to a query (session + global).")
    server.add_tool(_add_handler, name="add",
                    description="Remember a fact. scope='session' (this chat) or 'global' (all chats).")
    server.add_tool(_edit_handler, name="edit",
                    description="Replace the content of a memory you own (or a global one).")
    server.add_tool(_forget_handler, name="forget",
                    description="Delete a memory you own (or a global one).")
    return server
```

- [ ] **Step 7: Mount + register**

In `app.py`, after the sql mount:

```python
    from paperhub.mcp.memory_server import build_paperhub_memory_server
    mount_inprocess_mcp(app, build_paperhub_memory_server(), path="/mcp-memory")
```

In `mcp_servers.toml.example`, add:

```toml
[[server]]
name = "memory"
transport = "streamable_http"
url = "http://localhost:8000/mcp-memory"
expose = ["recall", "add", "edit", "forget"]
timeout_seconds = 8.0
```

- [ ] **Step 8: Run server + dispatcher tests + papers regression**

Run: `cd backend; uv run pytest tests/test_memory_tools.py tests/test_memory_server.py tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/src/paperhub/agents/memory_tools.py backend/src/paperhub/mcp/memory_server.py backend/src/paperhub/app.py backend/mcp_servers.toml.example backend/tests/test_memory_tools.py backend/tests/test_memory_server.py
git commit -m "feat(memory): scope-enforced memory dispatchers + memory MCP server"
```

---

### Task 11: Router `memory` intent + memory node

Add `memory` to the `Intent` literal, teach the router prompt to classify explicit "remember/update/forget" turns, and add the memory node (LLM extracts op/scope/content/target → calls `memory.*` via the registry → confirms in `response_language`; a `rejected` result marks the step + surfaces a clear message).

**Files:**
- Modify: `backend/src/paperhub/models/domain.py`, `backend/src/paperhub/llm/prompts/router_v1.yaml`
- Create: `backend/src/paperhub/agents/memory_node.py`, `backend/src/paperhub/llm/prompts/memory_op_v1.yaml`
- Modify: `backend/src/paperhub/api/chat.py`, `backend/src/paperhub/agents/graph.py`
- Test: `backend/tests/test_memory_node.py` (+ a router classification case in `tests/test_router.py`)

- [ ] **Step 1: Add `memory` to the Intent literal**

In `domain.py`:

```python
Intent = Literal[
    "paper_search", "paper_suggest", "paper_qa", "slides", "library_stats",
    "memory", "chitchat", "clarify",
]
```

Also add the recall slot to `AgentState`:

```python
    # v2.16: top-k recalled session+global memories injected into paper_qa /
    # library_stats (list of MemoryRow as dicts). Empty/absent => no injection.
    recalled_memories: list[dict[str, Any]]
```

- [ ] **Step 2: Update `router_v1.yaml`** — add a `memory` bullet to the intent menu in the system prompt, e.g.:

```
- memory: the user asks you to REMEMBER, UPDATE, or FORGET a fact/preference
  ("remember that...", "update my note about...", "forget that..."). Scope is
  this chat unless they say "always"/"in general" (global). NOT for questions
  about their library counts (that's library_stats).
```

- [ ] **Step 3: Write the `memory_op_v1.yaml` slot** (structured op extraction)

```yaml
system: |
  Extract the memory operation the user wants. Output JSON with fields:
    op: "add" | "edit" | "forget"
    scope: "session" | "global"   (global only if they said always/in general)
    content: the fact to store/replace with (for add/edit; "" for forget)
    target: a short search phrase identifying which existing memory to
            edit/forget ("" for add)
user: |
  User message: {user_message}
```

- [ ] **Step 4: Write the failing memory-node test**

```python
# backend/tests/test_memory_node.py
import aiosqlite
import pytest

from paperhub.agents.memory_node import memory_node
from paperhub.agents.state import AgentState
from paperhub.llm.litellm_adapter import LiteLlmAdapter
from paperhub.tracing.tracer import Tracer


class _FakeRegistry:
    def __init__(self, conn):
        self.conn = conn

    async def call(self, name: str, args: dict):
        # delegate to the in-process dispatchers so the DB really changes
        from paperhub.agents import memory_tools as mt
        if name == "memory.add":
            mid = await mt.add_memory(self.conn, session_id=1, content=args["content"], scope=args["scope"])
            return {"id": mid}
        if name == "memory.recall":
            hits = await mt.recall_memories(self.conn, session_id=1, query=args["query"], scope="both")
            return [{"id": h.id, "scope": h.scope, "content": h.content} for h in hits]
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_memory_node_add_persists_and_confirms(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.execute("INSERT INTO runs (session_id) VALUES (1)")
    await migrated_db.commit()
    tracer = Tracer(migrated_db, run_id=1, branch="")
    state: AgentState = {
        "run_id": 1, "session_id": 1,
        "user_message": "remember I'm comparing MoE routing papers",
        "effective_query": "remember I'm comparing MoE routing papers",
        "response_language": "English",
    }
    out = await memory_node(
        state, adapter=LiteLlmAdapter(), tracer=tracer,
        registry=_FakeRegistry(migrated_db), model="m",
        op_mock='{"op":"add","scope":"session","content":"comparing MoE routing papers","target":""}',
    )
    assert "remember" in out["final_response"].lower() or "noted" in out["final_response"].lower()
    async with migrated_db.execute("SELECT content FROM memories") as cur:
        rows = await cur.fetchall()
    assert rows and "MoE routing" in rows[0][0]
```

- [ ] **Step 5: Implement `memory_node.py`**

```python
# backend/src/paperhub/agents/memory_node.py
"""Memory node — the `memory` intent (SRS v2.16, §III-3).

Extracts op/scope/content/target with one small structured call, then writes
via the `memory` MCP (registry). For edit/forget it first recalls the target.
Returns a short confirmation in response_language. A rejected MCP result marks
the tracer step status='rejected' and surfaces a clear message.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from paperhub.agents.state import AgentState, effective_query, response_language
from paperhub.llm.adapter import LlmAdapter
from paperhub.tracing.tracer import Tracer


class _Registry(Protocol):
    async def call(self, namespaced_name: str, args: dict[str, Any]) -> Any: ...


async def _mcp(tracer: Tracer, registry: _Registry, tool: str, args: dict[str, Any]) -> Any:
    async with tracer.step(agent="memory", tool=tool, model=None) as step:
        step.record_args(args)
        res = await registry.call(tool, args)
        if isinstance(res, dict) and res.get("error") == "rejected":
            step.mark_rejected(str(res.get("reason", "rejected")))
        step.record_result(res if isinstance(res, dict) else {"count": len(res)})
        return res


async def memory_node(
    state: AgentState, *, adapter: LlmAdapter, tracer: Tracer, registry: _Registry,
    model: str, op_mock: str | None = None,
) -> AgentState:
    message = effective_query(state)
    language = response_language(state)

    kwargs: dict[str, Any] = {}
    if op_mock is not None:
        kwargs["mock_response"] = op_mock
    parts: list[str] = []
    async with tracer.step(agent="memory", tool="memory:plan", model=model) as step:
        step.record_args({"user_message": message})
        async for tok in adapter.stream(
            slot="memory_op/v1", variables={"user_message": message}, model=model, **kwargs,
        ):
            parts.append(tok)
        op = json.loads("".join(parts))
        step.record_result(op)

    kind = op.get("op")
    scope = op.get("scope", "session")
    content = op.get("content", "")
    target = op.get("target", "")

    if kind == "add":
        res = await _mcp(tracer, registry, "memory.add", {"content": content, "scope": scope})
        msg = f"Got it — I'll remember that." if "error" not in res else f"Couldn't save that: {res['reason']}"
    elif kind in ("edit", "forget"):
        hits = await _mcp(tracer, registry, "memory.recall", {"query": target or content, "scope": "both"})
        if not hits:
            msg = "I couldn't find a matching note to update."
        else:
            mid = hits[0]["id"]
            if kind == "edit":
                res = await _mcp(tracer, registry, "memory.edit", {"memory_id": mid, "content": content})
                msg = "Updated that note." if "error" not in res else f"Couldn't update it: {res['reason']}"
            else:
                res = await _mcp(tracer, registry, "memory.forget", {"memory_id": mid})
                msg = "Forgotten." if "error" not in res else f"Couldn't forget it: {res['reason']}"
    else:
        msg = "I wasn't sure what to remember — could you rephrase?"

    # NOTE: msg is composed in English here for determinism; if response_language
    # is non-English, wrap one short adapter.stream over a tiny "translate this
    # confirmation to {language}" slot OR (preferred) phrase msg directly in the
    # memory_op prompt's language. Keep it a single cheap call. For the first
    # cut, return msg directly (language polish is a non-blocking follow-up).
    _ = language
    return {**state, "final_response": msg}
```

> Implementation note for the worker: prefer phrasing the confirmation in `response_language` (the spec answers in the user's language everywhere). The simplest faithful approach is to add `response_language` to the `memory_op_v1` prompt and have it also return a `confirmation` field already in the right language — then return that. Adjust the test's mock JSON accordingly if you take that path.

- [ ] **Step 6: Wire dispatch in `chat.py` + node in `graph.py`**

`chat.py` — add a branch (non-streaming; emit one `token` then `final`, or stream the single confirmation):

```python
        elif intent == "memory":
            registry = request.app.state.mcp_registry
            async with _client_headers(session_id=session_id, run_id=run_id):
                result_state = await memory_node(
                    state, adapter=adapter, tracer=tracer, registry=registry,
                    model=settings.router_model,  # small tier
                )
            final_content = result_state["final_response"]
            token_evt = TokenEvent(run_id=run_id, branch="", text=final_content)
            yield {"event": "token", "data": token_evt.model_dump_json(exclude={"type"})}
```

(Import `from paperhub.agents.memory_node import memory_node`.) In `graph.py`, add a `memory` node + route entry mirroring `library_stats` (real node returning a terse marker; chat.py owns the user-facing path), and add `"memory"` to the terminal-edges loop + `routes` dict.

- [ ] **Step 7: Run tests (node + router classification + dispatch)**

Run: `cd backend; uv run pytest tests/test_memory_node.py tests/test_router.py tests/test_graph.py -v`
Expected: PASS. (Add a `test_router.py` case asserting "remember that I prefer X" → `intent == "memory"` using a `mock_response`; or add it to the router-accuracy fixture `fixtures/router_intents.jsonl`.)

- [ ] **Step 8: Commit**

```bash
git add backend/src/paperhub/models/domain.py backend/src/paperhub/llm/prompts/router_v1.yaml backend/src/paperhub/llm/prompts/memory_op_v1.yaml backend/src/paperhub/agents/memory_node.py backend/src/paperhub/api/chat.py backend/src/paperhub/agents/graph.py backend/tests/test_memory_node.py backend/tests/test_router.py
git commit -m "feat(memory): router memory intent + memory node (recall->decide->write)"
```

---

### Task 12: Recall injection + autonomous memory + Wave 2 smoke + gates

Inject recalled memories (on by default) into the `paper_qa` finalizer and the `library_stats` answer; allow `paper_qa`/`library_stats` to autonomously `memory.add` a salient fact (cap 2/turn); env flags; Wave 2 smoke; full gates.

**Files:**
- Create: `backend/src/paperhub/agents/memory_recall.py`
- Modify: `backend/src/paperhub/config.py` (+`memory_recall_enabled`, `memory_semantic_enabled`)
- Modify: `backend/src/paperhub/agents/sql_agent.py` (inject recall block; optional autonomous add)
- Modify: the `paper_qa` finalizer (inject recall block into its prompt variables)
- Create: `backend/scripts/smoke_memory.ps1`
- Test: `backend/tests/test_memory_recall.py`

- [ ] **Step 1: Add config flags**

```python
    # ── 6. Agent tunables ──
    memory_recall_enabled: bool       # inject recalled memories into qa/stats (default on)
    memory_semantic_enabled: bool     # upgrade-path stub: semantic recall (default off)
```

```python
    memory_recall_enabled=os.environ.get("PAPERHUB_MEMORY_RECALL", "1") not in ("0", "", "false", "False"),
    memory_semantic_enabled=os.environ.get("PAPERHUB_MEMORY_SEMANTIC", "0") not in ("0", "", "false", "False"),
```

Add both to `.env.example`.

- [ ] **Step 2: Write the failing recall-injection test**

```python
# backend/tests/test_memory_recall.py
import aiosqlite
import pytest

from paperhub.agents.memory_recall import build_memory_context_block
from paperhub.agents.memory_tools import add_memory


@pytest.mark.asyncio
async def test_context_block_includes_relevant_memories(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await migrated_db.commit()
    await add_memory(migrated_db, session_id=None, content="answer in Traditional Chinese", scope="global")
    block = await build_memory_context_block(
        migrated_db, session_id=1, query="Chinese answer please", enabled=True,
    )
    assert "Traditional Chinese" in block
    assert block.startswith("Relevant remembered facts")


@pytest.mark.asyncio
async def test_disabled_returns_empty(migrated_db: aiosqlite.Connection) -> None:
    block = await build_memory_context_block(migrated_db, session_id=1, query="x", enabled=False)
    assert block == ""
```

- [ ] **Step 3: Implement `memory_recall.py`**

```python
# backend/src/paperhub/agents/memory_recall.py
"""Recall-injection helper (SRS v2.16 FR-10). FTS top-k → labeled context block
injected into paper_qa / library_stats prompts. On by default; semantic recall
is an env-flagged upgrade path (not implemented here)."""
from __future__ import annotations

import aiosqlite

from paperhub.agents.memory_tools import recall_memories

_HEADER = "Relevant remembered facts (use if helpful, ignore if not):"


async def build_memory_context_block(
    conn: aiosqlite.Connection, *, session_id: int | None, query: str,
    enabled: bool = True, limit: int = 5,
) -> str:
    if not enabled:
        return ""
    hits = await recall_memories(conn, session_id=session_id, query=query, scope="both", limit=limit)
    if not hits:
        return ""
    lines = "\n".join(f"- ({h.scope}) {h.content}" for h in hits)
    return f"{_HEADER}\n{lines}"
```

> The two call sites (sql_agent answer, paper_qa finalizer) read `settings.memory_recall_enabled`, build the block with `effective_query`, and prepend it to the relevant prompt variable (e.g. add a `{memory_context}` placeholder to `sql_answer_v1.yaml` and the paper_qa finalizer slot; pass `""` when empty so the prompt is unchanged). Wire `build_memory_context_block(...)` before the answer/finalize call. Add a focused test per call site (sql_agent: assert the answer prompt received a non-empty `memory_context` when a matching global memory exists).

- [ ] **Step 4: Autonomous `memory.add` (capped)** — in `sql_agent.py` and the paper_qa finalizer, after producing the answer, allow an optional single `memory.add` when the model flags a durable fact. Keep it bounded: a module constant `MAX_AUTONOMOUS_ADDS_PER_TURN = 2` and a per-turn counter; the agent only adds when its prompt explicitly emits an `add_memory` directive. For the first cut, gate this behind the same `memory_recall_enabled` flag and add one test asserting the cap is respected (a stubbed agent that emits 3 add directives results in exactly 2 `memory.add` registry calls).

- [ ] **Step 5: Write the Wave 2 smoke**

```powershell
# backend/scripts/smoke_memory.ps1
# Wave 2 e2e: remember a global fact in session A, then recall it in session B;
# edit a session memory; assert an ownership-violation edit yields a rejected row.
$ErrorActionPreference = "Stop"
$env:PAPERHUB_WORKSPACE = Join-Path $PSScriptRoot "..\workspace_smoke_mem"
$env:PAPERHUB_INPROCESS_MODELS = "1"
$env:PAPERHUB_BOOT_BANNER = "0"
# Boot uvicorn on :8772 (mirror smoke_chat.ps1). Turn 1 (session A, memory intent
# mock): "always answer in Traditional Chinese" -> global memory. Turn 2 (new
# session B, paper_qa or library_stats): assert the recalled fact appears in the
# injected context (inspect tool_calls / final). Turn 3: edit. Then assert via
# paperhub-replay that an ownership-violation edit produced status='rejected'.
Write-Host "smoke_memory: OK"
```

- [ ] **Step 6: Run Wave 2 smoke + full gates**

Run: `cd backend; .\scripts\smoke_memory.ps1` → `smoke_memory: OK`
Run: `cd backend; uv run pytest -q; uv run ruff check src tests; uv run mypy src`
Expected: all green.
Run frontend gates (no FE code changed, but confirm nothing regressed): `cd frontend; npm test; npm run typecheck; npm run lint; npm run build` → green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/agents/memory_recall.py backend/src/paperhub/config.py backend/.env.example backend/src/paperhub/agents/sql_agent.py backend/src/paperhub/llm/prompts/sql_answer_v1.yaml backend/scripts/smoke_memory.ps1 backend/tests/test_memory_recall.py
git commit -m "feat(memory): recall injection (on by default) + capped autonomous add + Wave 2 smoke"
```

---

## Spec Coverage Self-Review

- **§III-6 `sql` MCP** — Tasks 3 (validator), 4 (server), 5 (mount/register). ✓
- **§III-6 `memory` MCP** — Task 10 (server + mount + register). ✓
- **§III-3 SQL Agent** (introspection NL2SQL, self-repair, answer+SQL block) — Task 6; wired Task 7. ✓
- **§III-3 Memory node** — Task 11. ✓
- **UC-5 read-and-act** (library auto-attach via `search_results`) — Task 7 Step 6. ✓
- **UC-7** (remember/recall/edit/forget; session vs global) — Tasks 9–11. ✓
- **FR-06** (router `memory` intent) — Task 11 Steps 1–2. ✓
- **FR-10** (table, tools, scope boundary, recall on by default) — Tasks 9, 10, 12. ✓
- **NFR-05** (`rejected` rows) — Task 1 (tracer), 4 (sql), 10 (memory); end-to-end asserted in Tasks 8 + 12 smokes. ✓ (Closes Plan B follow-up #2.)
- **§III-7** (8 tables + FTS) — Task 9. ✓

**Type-consistency check:** `validate_read_only_sql`/`SqlValidationError`/`ALLOWED_TABLES` (Task 3) ↔ used in Task 4. `MemoryRow`/`MemoryScopeError`/`add_memory`/`recall_memories`/`edit_memory`/`forget_memory` (Task 10) ↔ used in Tasks 10 (server), 11 (node fake), 12 (recall). `mark_rejected` (Task 1) ↔ used in Tasks 6, 11. `mount_inprocess_mcp`/`require_request_context` (Task 2) ↔ used in Tasks 5, 10. `sql_agent_stream` signature (Task 6) ↔ called in Task 7. `build_memory_context_block` (Task 12) ↔ defined+used same task. `Intent` gains `"memory"` (Task 11) before any router/dispatch references it. No drift found.

**Known investigation points the worker must confirm against live code (flagged inline, not placeholders):**
1. The exact `/chat` request schema + how `router_mock` is injected in `tests/test_chat_sse.py` (Tasks 7, 11 tests assume a `router_mock`/request shape — match the existing convention).
2. The exact client-headers context manager name in `paperhub.mcp.client_context` used by the `paper_search` branch (Tasks 7, 11 reference it as `_client_headers`).
3. The paper_qa finalizer prompt slot name + variables (Task 12 injects `{memory_context}` there).
4. Whether the app boots in tests via `asgi-lifespan` or an existing helper (Tasks 5, 7, 11) — reuse the existing pattern.

These are reads-before-code, not unspecified behavior: each has a single correct answer discoverable in the named file.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-paperhub-E-library-intelligence.md`.
