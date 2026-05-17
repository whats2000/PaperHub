# PaperHub Phase 0 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boot the PaperHub project skeleton — backend + frontend scaffolds, SQLite schema applied at startup, every cross-cutting foundation built once and verified (Pydantic models, LLM Provider Adapter, YAML prompt registry, vector-store driver, Tool-Call Tracer with redaction, MCP scope-checker), and a strict-typing CI gate. After Phase 0, the app boots, `/health` returns, the `tool_calls` table is writable, and every later phase has a typed substrate to plug into.

**Architecture:** Python 3.12 backend (uv-managed) using FastAPI + LangGraph; React 18 + Vite + Tailwind frontend; SQLite as the structured store with Chroma as the default vector backend; FastMCP for PaperHub's own MCP servers. Per the design's vertical-slice expansion strategy, Phase 0 ships **no user-facing feature** — only the substrate Phase 1 will use to deliver the first vertical slice.

**Tech Stack:** Python 3.12 · uv · FastAPI · Pydantic v2 · pydantic-settings · SQLite · ChromaDB · FastMCP · Anthropic SDK · OpenAI SDK (compatibility layer) · pytest · mypy --strict · ruff · React 18 · Vite · TypeScript · Tailwind 4

**Companion spec:** [PaperHub Implementation Design](../specs/2026-05-17-paperhub-implementation-design.md) (companion to [SRS v1.5](../specs/2026-05-17-paperhub-srs.md)).

---

## Pre-flight

All paths are relative to the repo root (`d:\GitHub\PaperHub`). Backend lives under `backend/`, frontend under `frontend/`. Both are created in Task 1. The engineer should be in the repo root for every command unless otherwise noted; commands that need a subdirectory say so explicitly.

Use **PowerShell** (Windows native) for shell commands. Use `uv` (NOT system `python` or `pip`) for all Python operations — per the global preference.

**Conventional Commits style required:** every commit message follows `action(scope): what you do`. Subject line imperative, scope is the module touched.

## File map (Phase 0)

```
backend/
  pyproject.toml                             # uv project + deps + tool config
  .python-version                            # 3.12
  paperhub/
    __init__.py
    config.py                                # pydantic-settings Settings singleton
    api/
      __init__.py
      app.py                                 # FastAPI ASGI app + /health
      schemas.py                             # API request/response models
    data/
      __init__.py
      db.py                                  # SQLite connection + migration runner
      models.py                              # Pydantic data models (Paper, Chunk, …, ToolCall)
      vectors.py                             # Vector-store driver (Chroma default)
      migrations/
        0001_initial.sql                     # Initial schema (matches design §6)
    llm/
      __init__.py
      adapter.py                             # Provider Adapter; structured-output generate()
      prompts.py                             # YAML-driven prompt registry
      prompts.yaml                           # (empty registry seed)
    tracing/
      __init__.py
      tracer.py                              # Tool-Call Tracer (ctx mgr + decorator)
      redactor.py                            # Secret/path redaction
    mcp/
      __init__.py
      scopes.py                              # Typed McpToolScope declarations
      client.py                              # MCP client + scope-checker (stub dispatcher)
  tests/
    __init__.py
    conftest.py                              # shared pytest fixtures
    api/
      test_health.py
    data/
      test_db.py
      test_models.py
      test_vectors.py
    llm/
      test_adapter.py
      test_prompts.py
    tracing/
      test_tracer.py
      test_redactor.py
    mcp/
      test_scopes.py
      test_client.py
frontend/
  package.json                               # vite + react + tailwind + ts deps
  vite.config.ts
  tailwind.config.js
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    index.css
.github/
  workflows/
    ci.yml                                   # mypy --strict + pytest + ruff
.pre-commit-config.yaml                      # ruff format/check + mypy
docs/
  KNOWN-TYPE-GAPS.md                         # Per NFR-11 narrow exception register
```

---

## Task 1: Backend repo scaffold + uv project

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.python-version`
- Create: `backend/paperhub/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Initialize backend dir layout and the uv project**

Run (from repo root):
```powershell
New-Item -ItemType Directory -Path backend, backend/paperhub, backend/tests -Force | Out-Null
"3.12" | Out-File -Encoding utf8 backend/.python-version
"" | Out-File -Encoding utf8 backend/paperhub/__init__.py
"" | Out-File -Encoding utf8 backend/tests/__init__.py
uv init backend --no-readme --no-pin-python
```
Expected: `backend/pyproject.toml` is created by `uv init`.

- [ ] **Step 2: Replace the generated `pyproject.toml` with the PaperHub one**

Overwrite `backend/pyproject.toml`:

```toml
[project]
name = "paperhub"
version = "0.1.0"
description = "PaperHub — paper knowledge base & research assistant"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "anthropic>=0.40",
  "openai>=1.50",
  "chromadb>=0.5.20",
  "pyyaml>=6.0",
  "httpx>=0.27",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "mypy>=1.13",
  "ruff>=0.7",
  "types-pyyaml>=6.0",
]

[tool.uv]
package = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.mypy]
python_version = "3.12"
strict = true
files = ["paperhub", "tests"]
plugins = ["pydantic.mypy"]
# Surface forgotten `# type: ignore[...]` codes
warn_unused_ignores = true

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "TID", "RUF"]
```

- [ ] **Step 3: Create a minimal pytest conftest**

Create `backend/tests/conftest.py`:

```python
"""Shared pytest fixtures for the PaperHub backend test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    """A per-test workspace dir; mirrors the runtime ~/PaperHub/workspace layout."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
```

- [ ] **Step 4: Resolve dependencies (and verify the toolchain works)**

Run (from `backend/`):
```powershell
cd backend; uv sync; uv run pytest --collect-only
```
Expected: dependencies resolve; pytest reports `collected 0 items`.

- [ ] **Step 5: Commit**

```powershell
git add backend/pyproject.toml backend/uv.lock backend/.python-version backend/paperhub/__init__.py backend/tests/__init__.py backend/tests/conftest.py
git commit -m "chore(backend): scaffold uv project with pyproject + pytest config"
```

---

## Task 2: pydantic-settings Settings singleton

**Files:**
- Create: `backend/paperhub/config.py`
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config.py`:

```python
"""Tests for the typed Settings singleton."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from paperhub.config import Settings


def test_settings_defaults_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    s = Settings()  # type: ignore[call-arg]  # pydantic-settings reads env, not kwargs

    assert s.workspace_root == tmp_path
    assert s.db_path == tmp_path / "paperhub.db"
    assert s.vector_backend == "chroma"
    assert s.router_model == "claude-haiku-4-5"
    assert s.generation_model == "claude-sonnet-4-6"
    assert s.judge_model == "claude-haiku-4-5"
    assert s.judge_model != s.generation_model, "judge must differ from generator (FR-12)"


def test_settings_api_keys_load_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-456")

    s = Settings()  # type: ignore[call-arg]

    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-123"
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "sk-test-456"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`):
```powershell
cd backend; uv run pytest tests/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.config'`.

- [ ] **Step 3: Write the implementation**

Create `backend/paperhub/config.py`:

```python
"""Typed application settings — the single source for env-derived config.

Every API key, model name, and filesystem path used by the system flows
through this Settings singleton (NFR-04, NFR-11).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",  # PaperHub-prefixed vars also work via the explicit aliases below
        extra="ignore",
        case_sensitive=False,
    )

    # Storage
    workspace_root: Path
    db_path: Path

    # Vector backend
    vector_backend: Literal["chroma", "sqlite-vec"] = "chroma"
    chroma_path: Path | None = None  # defaults to workspace_root / "chroma" if unset

    # LLM model picks (pinned per design §3 Default model picks)
    router_model: str = "claude-haiku-4-5"
    generation_model: str = "claude-sonnet-4-6"
    judge_model: str = "claude-haiku-4-5"
    embedding_model: str = "text-embedding-3-small"
    reranker_model: str = "BAAI/bge-reranker-base"

    # Provider credentials
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"


def get_settings() -> Settings:
    """Return the Settings singleton. Re-reads env each call — tests can monkeypatch freely."""
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/test_config.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/paperhub/config.py backend/tests/test_config.py
git commit -m "feat(config): add typed Settings singleton with pinned model defaults"
```

---

## Task 3: Initial SQLite schema migration

**Files:**
- Create: `backend/paperhub/data/__init__.py`
- Create: `backend/paperhub/data/migrations/__init__.py`
- Create: `backend/paperhub/data/migrations/0001_initial.sql`

- [ ] **Step 1: Create the migrations package**

Run:
```powershell
New-Item -ItemType Directory -Path backend/paperhub/data, backend/paperhub/data/migrations, backend/tests/data -Force | Out-Null
"" | Out-File -Encoding utf8 backend/paperhub/data/__init__.py
"" | Out-File -Encoding utf8 backend/paperhub/data/migrations/__init__.py
"" | Out-File -Encoding utf8 backend/tests/data/__init__.py
```

- [ ] **Step 2: Write the initial migration SQL**

Create `backend/paperhub/data/migrations/0001_initial.sql` (mirrors design §6 exactly):

```sql
-- Migration: initial schema (Phase 0)
-- Matches docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md §6.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE papers (
    id            TEXT PRIMARY KEY,
    arxiv_id      TEXT UNIQUE,
    doi           TEXT UNIQUE,
    title         TEXT NOT NULL,
    authors_json  TEXT NOT NULL,
    year          INTEGER,
    abstract      TEXT,
    pdf_path      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    primary_topic TEXT,
    added_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_papers_year_topic ON papers(year, primary_topic);

CREATE TABLE project_papers (
    project_id     TEXT NOT NULL REFERENCES projects(id),
    paper_id       TEXT NOT NULL REFERENCES papers(id),
    reading_status TEXT CHECK(reading_status IN ('unread','skimmed','deep')),
    PRIMARY KEY (project_id, paper_id)
);

CREATE TABLE tags (
    paper_id TEXT NOT NULL REFERENCES papers(id),
    tag      TEXT NOT NULL,
    PRIMARY KEY (paper_id, tag)
);

CREATE TABLE notes (
    id         TEXT PRIMARY KEY,
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    body_md    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE chunks (
    id         TEXT PRIMARY KEY,
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    section    TEXT,
    page       INTEGER,
    char_start INTEGER,
    char_end   INTEGER,
    text       TEXT NOT NULL
);
CREATE INDEX idx_chunks_paper ON chunks(paper_id);

CREATE TABLE citations (
    src_paper_id TEXT NOT NULL REFERENCES papers(id),
    dst_paper_id TEXT NOT NULL REFERENCES papers(id),
    source       TEXT NOT NULL,
    PRIMARY KEY (src_paper_id, dst_paper_id)
);

CREATE TABLE chat_sessions (
    id         TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title      TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    role       TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content    TEXT NOT NULL,
    run_id     TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE runs (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT REFERENCES chat_sessions(id),
    routing_decision_json TEXT,
    started_at            TIMESTAMP NOT NULL,
    finished_at           TIMESTAMP,
    status                TEXT CHECK(status IN ('running','ok','failed'))
);

CREATE TABLE tool_calls (
    run_id              TEXT NOT NULL REFERENCES runs(id),
    step_index          INTEGER NOT NULL,
    parent_step         INTEGER,
    agent               TEXT NOT NULL,
    tool                TEXT NOT NULL,
    model               TEXT,
    args_redacted_json  TEXT NOT NULL,
    result_summary_json TEXT,
    latency_ms          INTEGER NOT NULL,
    token_in            INTEGER,
    token_out           INTEGER,
    status              TEXT NOT NULL CHECK(status IN ('ok','error','rejected')),
    error               TEXT,
    PRIMARY KEY (run_id, step_index)
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, step_index);
```

- [ ] **Step 3: Commit**

```powershell
git add backend/paperhub/data/__init__.py backend/paperhub/data/migrations/__init__.py backend/paperhub/data/migrations/0001_initial.sql backend/tests/data/__init__.py
git commit -m "feat(data): add initial SQLite schema migration (papers, runs, tool_calls, ...)"
```

---

## Task 4: SQLite connection + migration runner

**Files:**
- Create: `backend/paperhub/data/db.py`
- Create: `backend/tests/data/test_db.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/data/test_db.py`:

```python
"""Tests for the SQLite connection helper and migration runner."""
from __future__ import annotations

from pathlib import Path

from paperhub.data.db import apply_migrations, connect


def test_apply_migrations_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    table_names = {row[0] for row in rows}
    assert {
        "papers", "chunks", "projects", "project_papers", "tags", "notes",
        "citations", "chat_sessions", "messages", "runs", "tool_calls",
        "schema_migrations",
    }.issubset(table_names)


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    apply_migrations(db_path)  # second call must be a no-op, not a crash
    with connect(db_path) as conn:
        versions = [
            r[0] for r in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert versions == [1]


def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "paperhub.db"
    apply_migrations(db_path)
    with connect(db_path) as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/data/test_db.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.data.db'`.

- [ ] **Step 3: Write the implementation**

Create `backend/paperhub/data/db.py`:

```python
"""SQLite connection helper + forward-only migration runner.

Migrations live as `NNNN_*.sql` files under `paperhub/data/migrations/`.
Each file is one transaction; the runner records its version in
`schema_migrations` so subsequent runs are idempotent. This is invoked
at FastAPI startup, but the helpers here are deliberately decoupled
from FastAPI so the runner is callable from tests and CLI tools.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

_MIGRATION_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with foreign-key enforcement on."""
    conn = sqlite3.connect(db_path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _list_migrations() -> list[tuple[int, str, str]]:
    """Return [(version, filename, sql_text), …] sorted by version."""
    out: list[tuple[int, str, str]] = []
    migrations_pkg = resources.files("paperhub.data.migrations")
    for entry in migrations_pkg.iterdir():
        name = entry.name
        m = _MIGRATION_NAME_RE.match(name)
        if not m:
            continue
        version = int(m.group(1))
        sql = entry.read_text(encoding="utf-8")
        out.append((version, name, sql))
    out.sort(key=lambda t: t[0])
    return out


def apply_migrations(db_path: Path) -> None:
    """Apply every unapplied migration in order. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        applied: set[int] = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, filename, sql in _list_migrations():
            if version in applied:
                continue
            conn.execute("BEGIN")
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/data/test_db.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/paperhub/data/db.py backend/tests/data/test_db.py
git commit -m "feat(data): add SQLite connection helper and idempotent migration runner"
```

---

## Task 5: Pydantic data models

**Files:**
- Create: `backend/paperhub/data/models.py`
- Create: `backend/tests/data/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/data/test_models.py`:

```python
"""Tests for Pydantic data models — the typed shape every persisted entity must take."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from paperhub.data.models import (
    Chunk,
    Note,
    Paper,
    Project,
    ReadingStatus,
    RoutingDecision,
    RunMetadata,
    RunStatus,
    ToolCall,
    ToolCallStatus,
)


def test_paper_round_trip() -> None:
    pid = uuid4()
    p = Paper(
        id=pid,
        arxiv_id="2401.00001",
        doi=None,
        title="A Paper",
        authors=["Alice", "Bob"],
        year=2024,
        abstract="Abstract.",
        pdf_path="papers/abc.pdf",
        sha256="0" * 64,
        primary_topic=None,
        added_at=datetime(2026, 5, 17, 12, 0, 0),
    )
    dumped = p.model_dump()
    assert dumped["authors"] == ["Alice", "Bob"]
    revived = Paper.model_validate(dumped)
    assert revived == p


def test_routing_decision_intent_literal_is_enforced() -> None:
    with pytest.raises(ValueError):
        RoutingDecision(
            intent="nonsense",  # type: ignore[arg-type]
            confidence=0.5,
            model_tier="small",
            reasoning="x",
        )


def test_routing_decision_confidence_bounds() -> None:
    with pytest.raises(ValueError):
        RoutingDecision(
            intent="paper_qa", confidence=1.5, model_tier="small", reasoning="x"
        )


def test_tool_call_status_is_constrained() -> None:
    tc = ToolCall(
        run_id=uuid4(),
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model="claude-haiku-4-5",
        args_redacted={"prompt": "<REDACTED>"},
        result_summary={"intent": "paper_qa"},
        latency_ms=120,
        token_in=42,
        token_out=10,
        status="ok",
        error=None,
    )
    assert tc.status == "ok"
    with pytest.raises(ValueError):
        ToolCall(
            run_id=uuid4(),
            step_index=0,
            parent_step=None,
            agent="router",
            tool="llm",
            model=None,
            args_redacted={},
            result_summary=None,
            latency_ms=1,
            token_in=None,
            token_out=None,
            status="weird",  # type: ignore[arg-type]
            error=None,
        )


def test_chunk_project_note_validate() -> None:
    Project(id=uuid4(), name="Thesis", created_at=datetime.utcnow())
    Note(
        id=uuid4(), paper_id=uuid4(), body_md="note", created_at=datetime.utcnow()
    )
    Chunk(
        id=uuid4(), paper_id=uuid4(), section="intro", page=1,
        char_start=0, char_end=100, text="hello",
    )


def test_reading_status_and_run_status_literals() -> None:
    # These act as Literal type aliases — they exist to be importable and
    # used as field types; here we just smoke-check they accept the right strings.
    assert "deep" in ReadingStatus.__args__  # type: ignore[attr-defined]
    assert "running" in RunStatus.__args__  # type: ignore[attr-defined]
    assert "rejected" in ToolCallStatus.__args__  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/data/test_models.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.data.models'`.

- [ ] **Step 3: Write the implementation**

Create `backend/paperhub/data/models.py`:

```python
"""Pydantic data models for every persisted entity.

These are the SHAPES — converting to/from SQL rows is the data-access
layer's job, added in Phase 1. Per NFR-11, no `Any`, no untyped dict in
public field types; `dict[str, object]`-shaped JSON payloads are bounded
by per-field documentation only because their schemas vary by call site.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ReadingStatus = Literal["unread", "skimmed", "deep"]
MessageRole = Literal["user", "assistant", "system"]
RunStatus = Literal["running", "ok", "failed"]
ToolCallStatus = Literal["ok", "error", "rejected"]
ModelTier = Literal["small", "flagship"]
Intent = Literal[
    "paper_qa", "library_stats", "research_suggest", "slides", "mcp_tool", "chitchat"
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Project(_Frozen):
    id: UUID
    name: str
    created_at: datetime


class Paper(_Frozen):
    id: UUID
    arxiv_id: str | None
    doi: str | None
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    pdf_path: str
    sha256: str
    primary_topic: str | None
    added_at: datetime


class ProjectPaper(_Frozen):
    project_id: UUID
    paper_id: UUID
    reading_status: ReadingStatus | None


class Tag(_Frozen):
    paper_id: UUID
    tag: str


class Note(_Frozen):
    id: UUID
    paper_id: UUID
    body_md: str
    created_at: datetime


class Chunk(_Frozen):
    id: UUID
    paper_id: UUID
    section: str | None
    page: int | None
    char_start: int | None
    char_end: int | None
    text: str


class Citation(_Frozen):
    src_paper_id: UUID
    dst_paper_id: UUID
    source: str


class ChatSession(_Frozen):
    id: UUID
    project_id: UUID | None
    title: str | None
    created_at: datetime


class Message(_Frozen):
    id: UUID
    session_id: UUID
    role: MessageRole
    content: str
    run_id: UUID | None
    created_at: datetime


class RoutingDecision(_Frozen):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    model_tier: ModelTier
    reasoning: str
    fallback_to_user: bool = False


class RunMetadata(_Frozen):
    id: UUID
    session_id: UUID | None
    routing_decision: RoutingDecision | None
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus


class ToolCall(_Frozen):
    run_id: UUID
    step_index: int
    parent_step: int | None
    agent: str
    tool: str
    model: str | None
    # JSON columns: argument schemas vary per (tool, method); the tracer enforces
    # they were Pydantic-validated at the call site before reaching this model.
    args_redacted: dict[str, object]
    result_summary: dict[str, object] | None
    latency_ms: int = Field(ge=0)
    token_in: int | None = Field(default=None, ge=0)
    token_out: int | None = Field(default=None, ge=0)
    status: ToolCallStatus
    error: str | None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/data/test_models.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/paperhub/data/models.py backend/tests/data/test_models.py
git commit -m "feat(data): add Pydantic models for every persisted entity (NFR-11)"
```

---

## Task 6: FastAPI app shell + /health endpoint

**Files:**
- Create: `backend/paperhub/api/__init__.py`
- Create: `backend/paperhub/api/app.py`
- Create: `backend/paperhub/api/schemas.py`
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/test_health.py`

- [ ] **Step 1: Create the package dirs**

Run:
```powershell
New-Item -ItemType Directory -Path backend/paperhub/api, backend/tests/api -Force | Out-Null
"" | Out-File -Encoding utf8 backend/paperhub/api/__init__.py
"" | Out-File -Encoding utf8 backend/tests/api/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/api/test_health.py`:

```python
"""Smoke tests for the FastAPI app shell."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
    from paperhub.api.app import create_app

    return TestClient(create_app())


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "paperhub"
    assert "schema_version" in body


def test_health_endpoint_reports_applied_migration(client: TestClient) -> None:
    r = client.get("/health")
    assert r.json()["schema_version"] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/api/test_health.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.api.app'`.

- [ ] **Step 4: Write the schemas**

Create `backend/paperhub/api/schemas.py`:

```python
"""HTTP request/response schemas for the FastAPI surface."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: Literal["paperhub"]
    schema_version: int
```

- [ ] **Step 5: Write the FastAPI app**

Create `backend/paperhub/api/app.py`:

```python
"""FastAPI ASGI app factory.

The app is built via `create_app()` so tests can swap settings via env
before construction. The startup hook applies pending SQLite migrations
once; runtime endpoints assume the schema is up-to-date.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from paperhub.api.schemas import HealthResponse
from paperhub.config import get_settings
from paperhub.data.db import apply_migrations, connect


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        apply_migrations(settings.db_path)
        yield

    app = FastAPI(title="PaperHub", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        with connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        return HealthResponse(status="ok", app="paperhub", schema_version=row[0])

    return app
```

- [ ] **Step 6: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/api/test_health.py -v
```
Expected: 2 passed.

- [ ] **Step 7: Commit**

```powershell
git add backend/paperhub/api/__init__.py backend/paperhub/api/app.py backend/paperhub/api/schemas.py backend/tests/api/__init__.py backend/tests/api/test_health.py
git commit -m "feat(api): add FastAPI app shell with /health and migrations-on-startup"
```

---

## Task 7: LLM Provider Adapter (Anthropic, structured output)

**Files:**
- Create: `backend/paperhub/llm/__init__.py`
- Create: `backend/paperhub/llm/adapter.py`
- Create: `backend/tests/llm/__init__.py`
- Create: `backend/tests/llm/test_adapter.py`

The adapter has two implementations: `AnthropicAdapter` (real) and `FakeAdapter` (returns canned `BaseModel` instances). Phase-0 tests only cover the `FakeAdapter` contract + the adapter's `generate(response_model)` shape; live API calls are exercised in Phase 1.

- [ ] **Step 1: Create the package dirs**

Run:
```powershell
New-Item -ItemType Directory -Path backend/paperhub/llm, backend/tests/llm -Force | Out-Null
"" | Out-File -Encoding utf8 backend/paperhub/llm/__init__.py
"" | Out-File -Encoding utf8 backend/tests/llm/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/llm/test_adapter.py`:

```python
"""Tests for the LLM Provider Adapter contract.

Phase 0 only covers the FakeAdapter (the test-time double agents will use).
The real Anthropic / OpenAI adapters are smoke-tested in Phase 1 against
recorded fixtures.
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from paperhub.llm.adapter import FakeAdapter, LlmMessage, ModelTier


class _Intent(BaseModel):
    intent: Literal["paper_qa", "out_of_scope"]
    confidence: float


@pytest.mark.asyncio()
async def test_fake_adapter_returns_canned_pydantic_instance() -> None:
    adapter = FakeAdapter(
        canned={"router": _Intent(intent="paper_qa", confidence=0.95)}
    )
    out = await adapter.generate(
        messages=[LlmMessage(role="user", content="hi")],
        model_tier="small",
        response_model=_Intent,
        slot="router",
    )
    assert isinstance(out, _Intent)
    assert out.intent == "paper_qa"
    assert out.confidence == pytest.approx(0.95)


@pytest.mark.asyncio()
async def test_fake_adapter_raises_for_unknown_slot() -> None:
    adapter = FakeAdapter(canned={})
    with pytest.raises(KeyError, match="router"):
        await adapter.generate(
            messages=[LlmMessage(role="user", content="hi")],
            model_tier="small",
            response_model=_Intent,
            slot="router",
        )


@pytest.mark.asyncio()
async def test_fake_adapter_type_mismatch_raises() -> None:
    adapter = FakeAdapter(canned={"router": _Intent(intent="paper_qa", confidence=1.0)})

    class _Other(BaseModel):
        x: int

    with pytest.raises(TypeError):
        await adapter.generate(
            messages=[LlmMessage(role="user", content="hi")],
            model_tier="small",
            response_model=_Other,
            slot="router",
        )


def test_model_tier_literal_is_two_valued() -> None:
    valid: ModelTier = "small"
    valid = "flagship"  # noqa: F841 — just proving the assignment type-checks
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/llm/test_adapter.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.llm.adapter'`.

- [ ] **Step 4: Write the implementation**

Create `backend/paperhub/llm/adapter.py`:

```python
"""Provider Adapter for LLMs.

`LlmAdapter` is the abstract interface; agents only see `generate(...)`
which always returns a typed Pydantic instance via structured output.
`AnthropicAdapter` implements it for production (Phase 1 fills in the
network call); `FakeAdapter` returns canned Pydantic values for tests.
"""
from __future__ import annotations

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

ModelTier = Literal["small", "flagship"]
LlmRole = Literal["system", "user", "assistant"]


class LlmMessage(BaseModel):
    role: LlmRole
    content: str


T = TypeVar("T", bound=BaseModel)


class LlmAdapter(Protocol):
    """A pluggable LLM provider exposing one typed structured-output call."""

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T: ...


class FakeAdapter:
    """Test-time double. Returns canned Pydantic instances keyed by `slot`."""

    def __init__(self, canned: dict[str, BaseModel]) -> None:
        self._canned = canned

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T:
        if slot not in self._canned:
            raise KeyError(f"No canned response for slot {slot!r}")
        value = self._canned[slot]
        if not isinstance(value, response_model):
            raise TypeError(
                f"Canned value for slot {slot!r} is {type(value).__name__}, "
                f"expected {response_model.__name__}"
            )
        return value


class AnthropicAdapter:
    """Production adapter for Anthropic Claude. Phase 1 fills in the network call."""

    def __init__(self, api_key: str, router_model: str, generation_model: str) -> None:
        self._api_key = api_key
        self._router_model = router_model
        self._generation_model = generation_model

    async def generate(
        self,
        *,
        messages: list[LlmMessage],
        model_tier: ModelTier,
        response_model: type[T],
        slot: str,
    ) -> T:
        raise NotImplementedError(
            "AnthropicAdapter.generate is implemented in Phase 1 (Task: real Anthropic call)"
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/llm/test_adapter.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```powershell
git add backend/paperhub/llm/__init__.py backend/paperhub/llm/adapter.py backend/tests/llm/__init__.py backend/tests/llm/test_adapter.py
git commit -m "feat(llm): add LlmAdapter Protocol + FakeAdapter + AnthropicAdapter skeleton"
```

---

## Task 8: YAML-driven prompt registry

**Files:**
- Create: `backend/paperhub/llm/prompts.py`
- Create: `backend/paperhub/llm/prompts.yaml`
- Create: `backend/tests/llm/test_prompts.py`

- [ ] **Step 1: Seed the prompt registry file**

Create `backend/paperhub/llm/prompts.yaml`:

```yaml
# PaperHub prompt registry — every agent prompt lives here, never in code.
# Schema: <slot>.<version>.{system, user_template}
# `user_template` is rendered with str.format(**vars).

router:
  v1:
    system: |
      You are PaperHub's task router. Classify the user's request into exactly one
      of the configured intents. Be conservative — when in doubt, return
      `out_of_scope` with low confidence so the user is asked to clarify.
    user_template: |
      User request:
      {user_message}
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/llm/test_prompts.py`:

```python
"""Tests for the YAML-driven prompt registry."""
from __future__ import annotations

import pytest

from paperhub.llm.prompts import PromptNotFoundError, PromptRegistry


def test_registry_loads_router_v1() -> None:
    reg = PromptRegistry.load_default()
    rendered = reg.render(slot="router", version="v1", user_message="Hello there")
    assert "PaperHub's task router" in rendered.system
    assert "Hello there" in rendered.user


def test_registry_missing_slot_raises() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(PromptNotFoundError):
        reg.render(slot="nonexistent", version="v1", x="y")


def test_registry_missing_version_raises() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(PromptNotFoundError):
        reg.render(slot="router", version="v99", user_message="x")


def test_registry_missing_template_var_raises_key_error() -> None:
    reg = PromptRegistry.load_default()
    with pytest.raises(KeyError):
        # `user_message` is the required var; omitting it must surface clearly
        reg.render(slot="router", version="v1")
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/llm/test_prompts.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.llm.prompts'`.

- [ ] **Step 4: Write the implementation**

Create `backend/paperhub/llm/prompts.py`:

```python
"""YAML-driven prompt registry.

All prompts live in `prompts.yaml`. Slots are addressable as
`<slot>.<version>`; subsequent phases add slots and additional
versions for A/B work. Templates render with `str.format`.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import cast

import yaml


class PromptNotFoundError(KeyError):
    """Raised when a (slot, version) pair is missing from the registry."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


class PromptRegistry:
    def __init__(self, data: dict[str, dict[str, dict[str, str]]]) -> None:
        self._data = data

    @classmethod
    def load_default(cls) -> "PromptRegistry":
        text = (
            resources.files("paperhub.llm").joinpath("prompts.yaml").read_text(encoding="utf-8")
        )
        loaded = cast(dict[str, dict[str, dict[str, str]]], yaml.safe_load(text))
        return cls(loaded)

    def render(self, *, slot: str, version: str, **vars: object) -> RenderedPrompt:
        slot_entry = self._data.get(slot)
        if slot_entry is None:
            raise PromptNotFoundError(f"slot {slot!r} not in registry")
        version_entry = slot_entry.get(version)
        if version_entry is None:
            raise PromptNotFoundError(f"version {version!r} of slot {slot!r} not in registry")
        system = version_entry["system"]
        template = version_entry["user_template"]
        return RenderedPrompt(system=system, user=template.format(**vars))
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/llm/test_prompts.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```powershell
git add backend/paperhub/llm/prompts.py backend/paperhub/llm/prompts.yaml backend/tests/llm/test_prompts.py
git commit -m "feat(llm): add YAML-driven prompt registry with rendered slot/version lookup"
```

---

## Task 9: Vector-store driver interface (Chroma default)

**Files:**
- Create: `backend/paperhub/data/vectors.py`
- Create: `backend/tests/data/test_vectors.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/data/test_vectors.py`:

```python
"""Tests for the vector-store driver interface (Chroma default backend)."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.data.vectors import ChromaVectorStore, ChunkVector, VectorSearchHit


def _vec(seed: float) -> list[float]:
    # Deterministic 8-dim toy vectors so tests don't depend on a real embedder
    return [seed, seed + 0.1, seed + 0.2, seed + 0.3, seed + 0.4, seed + 0.5, seed + 0.6, seed + 0.7]


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(path=tmp_path / "chroma")


def test_add_then_search_returns_hits(store: ChromaVectorStore) -> None:
    paper_id = uuid4()
    chunk_id = uuid4()
    store.add([
        ChunkVector(
            chunk_id=chunk_id, paper_id=paper_id, embedding=_vec(0.0),
            metadata={"section": "intro", "page": 1},
        ),
    ])
    hits = store.search(query_embedding=_vec(0.0), top_k=5)
    assert len(hits) == 1
    assert hits[0].chunk_id == chunk_id
    assert hits[0].paper_id == paper_id
    assert isinstance(hits[0], VectorSearchHit)


def test_delete_by_paper_removes_vectors(store: ChromaVectorStore) -> None:
    paper_id = uuid4()
    store.add([
        ChunkVector(chunk_id=uuid4(), paper_id=paper_id, embedding=_vec(0.0), metadata={}),
        ChunkVector(chunk_id=uuid4(), paper_id=paper_id, embedding=_vec(1.0), metadata={}),
    ])
    store.delete_by_paper(paper_id)
    hits = store.search(query_embedding=_vec(0.0), top_k=5)
    assert hits == []


def test_search_filters_by_paper_id(store: ChromaVectorStore) -> None:
    p1, p2 = uuid4(), uuid4()
    c1, c2 = uuid4(), uuid4()
    store.add([
        ChunkVector(chunk_id=c1, paper_id=p1, embedding=_vec(0.0), metadata={}),
        ChunkVector(chunk_id=c2, paper_id=p2, embedding=_vec(0.0), metadata={}),
    ])
    hits = store.search(query_embedding=_vec(0.0), top_k=5, paper_ids=[p1])
    assert [h.chunk_id for h in hits] == [c1]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/data/test_vectors.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.data.vectors'`.

- [ ] **Step 3: Write the implementation**

Create `backend/paperhub/data/vectors.py`:

```python
"""Vector-store driver behind a narrow typed interface.

Phase 0 ships only the Chroma backend (the SRS-default per v1.5). The
`sqlite-vec` opt-in backend is added in a later phase; both implement the
same `add` / `search` / `delete_by_paper` contract so agent code never
sees the backend choice.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import chromadb
from chromadb.config import Settings as ChromaSettings
from pydantic import BaseModel

VectorBackendName = Literal["chroma", "sqlite-vec"]


class ChunkVector(BaseModel):
    chunk_id: UUID
    paper_id: UUID
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


class VectorSearchHit(BaseModel):
    chunk_id: UUID
    paper_id: UUID
    score: float
    metadata: dict[str, str | int | float | bool]


class VectorStore(Protocol):
    def add(self, vectors: list[ChunkVector]) -> None: ...
    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        paper_ids: list[UUID] | None = None,
    ) -> list[VectorSearchHit]: ...
    def delete_by_paper(self, paper_id: UUID) -> None: ...


class ChromaVectorStore:
    """Local persistent Chroma backend — default per SRS v1.5."""

    _COLLECTION = "chunks"

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
        self._coll = self._client.get_or_create_collection(
            name=self._COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    def add(self, vectors: list[ChunkVector]) -> None:
        if not vectors:
            return
        self._coll.add(
            ids=[str(v.chunk_id) for v in vectors],
            embeddings=[v.embedding for v in vectors],
            metadatas=[
                {"paper_id": str(v.paper_id), **v.metadata} for v in vectors
            ],
        )

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        paper_ids: list[UUID] | None = None,
    ) -> list[VectorSearchHit]:
        where: dict[str, object] | None = None
        if paper_ids:
            where = {"paper_id": {"$in": [str(pid) for pid in paper_ids]}}
        res = self._coll.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,  # type: ignore[arg-type]
        )
        hits: list[VectorSearchHit] = []
        ids_outer = res.get("ids") or [[]]
        dists_outer = res.get("distances") or [[]]
        metas_outer = res.get("metadatas") or [[]]
        for chunk_id_s, dist, meta in zip(
            ids_outer[0], dists_outer[0], metas_outer[0], strict=False
        ):
            meta_dict = dict(meta or {})
            paper_id_s = str(meta_dict.pop("paper_id"))
            hits.append(
                VectorSearchHit(
                    chunk_id=UUID(chunk_id_s),
                    paper_id=UUID(paper_id_s),
                    score=1.0 - float(dist),  # cosine distance → similarity
                    metadata={
                        k: v
                        for k, v in meta_dict.items()
                        if isinstance(v, (str, int, float, bool))
                    },
                )
            )
        return hits

    def delete_by_paper(self, paper_id: UUID) -> None:
        self._coll.delete(where={"paper_id": str(paper_id)})  # type: ignore[arg-type]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/data/test_vectors.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add backend/paperhub/data/vectors.py backend/tests/data/test_vectors.py
git commit -m "feat(data): add Chroma-backed VectorStore behind a typed Protocol"
```

---

## Task 10: Tool-Call Tracer + secret redaction

**Files:**
- Create: `backend/paperhub/tracing/__init__.py`
- Create: `backend/paperhub/tracing/redactor.py`
- Create: `backend/paperhub/tracing/tracer.py`
- Create: `backend/tests/tracing/__init__.py`
- Create: `backend/tests/tracing/test_redactor.py`
- Create: `backend/tests/tracing/test_tracer.py`

- [ ] **Step 1: Create the package dirs**

Run:
```powershell
New-Item -ItemType Directory -Path backend/paperhub/tracing, backend/tests/tracing -Force | Out-Null
"" | Out-File -Encoding utf8 backend/paperhub/tracing/__init__.py
"" | Out-File -Encoding utf8 backend/tests/tracing/__init__.py
```

- [ ] **Step 2: Write the failing redactor test**

Create `backend/tests/tracing/test_redactor.py`:

```python
"""Tests for the args redactor (NFR-09)."""
from __future__ import annotations

from pathlib import Path

from paperhub.tracing.redactor import redact


def test_redact_anthropic_api_key_in_string() -> None:
    out = redact({"prompt": "sk-ant-abc123xyz hello", "x": 1})
    assert "sk-ant-abc123xyz" not in out["prompt"]  # type: ignore[operator]
    assert "<REDACTED:api-key>" in out["prompt"]  # type: ignore[operator]
    assert out["x"] == 1


def test_redact_openai_api_key_in_string() -> None:
    out = redact({"k": "sk-proj-Abc123-_xyz"})
    assert "<REDACTED:api-key>" in out["k"]  # type: ignore[operator]


def test_redact_absolute_home_directory_path() -> None:
    home_path = str(Path.home() / "secret" / "doc.pdf")
    out = redact({"path": home_path})
    assert "<REDACTED:home>" in out["path"]  # type: ignore[operator]


def test_redact_nested_dict_and_list() -> None:
    out = redact({"outer": {"inner": ["sk-ant-zzz", "ok"]}})
    nested = out["outer"]
    assert isinstance(nested, dict)
    inner = nested["inner"]
    assert isinstance(inner, list)
    assert "<REDACTED:api-key>" in inner[0]
    assert inner[1] == "ok"
```

- [ ] **Step 3: Run redactor test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/tracing/test_redactor.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.tracing.redactor'`.

- [ ] **Step 4: Write the redactor**

Create `backend/paperhub/tracing/redactor.py`:

```python
"""Secret + path redaction for the tool-call audit log (NFR-09).

`redact` walks a JSON-shaped value recursively and replaces:
  - Anthropic / OpenAI API key shapes  → "<REDACTED:api-key>"
  - Absolute paths under the user's home dir → "<REDACTED:home>"

The redaction is intentionally over-eager rather than under-eager —
better to obscure a non-secret than to leak a real one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

_API_KEY_RE = re.compile(r"sk-(?:ant|proj)-[A-Za-z0-9_\-]{8,}")
_HOME_PREFIX = str(Path.home())


def _scrub_string(s: str) -> str:
    redacted = _API_KEY_RE.sub("<REDACTED:api-key>", s)
    if _HOME_PREFIX and _HOME_PREFIX in redacted:
        redacted = redacted.replace(_HOME_PREFIX, "<REDACTED:home>")
    return redacted


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in cast(dict[str, object], value).items()}
    if isinstance(value, list):
        return [_scrub(v) for v in cast(list[object], value)]
    return value


def redact(payload: dict[str, object]) -> dict[str, object]:
    """Return a redacted copy of `payload`. The input is not mutated."""
    return {k: _scrub(v) for k, v in payload.items()}
```

- [ ] **Step 5: Run redactor test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/tracing/test_redactor.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Write the failing tracer test**

Create `backend/tests/tracing/test_tracer.py`:

```python
"""Tests for the Tool-Call Tracer (FR-11)."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from paperhub.data.db import apply_migrations, connect
from paperhub.tracing.tracer import ToolCallTracer


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "paperhub.db"
    apply_migrations(p)
    return p


def _insert_run(db_path: Path, run_id_str: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs(id, session_id, routing_decision_json, started_at, status) "
            "VALUES (?, NULL, NULL, CURRENT_TIMESTAMP, 'running')",
            (run_id_str,),
        )


def test_record_step_commits_a_tool_calls_row(db_path: Path) -> None:
    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id,
        step_index=0,
        parent_step=None,
        agent="router",
        tool="llm",
        model="claude-haiku-4-5",
        args={"prompt": "hi"},
        result_summary={"intent": "paper_qa"},
        latency_ms=42,
        token_in=10,
        token_out=2,
        status="ok",
        error=None,
    )
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT agent, tool, args_redacted_json, result_summary_json, status "
            "FROM tool_calls WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
    assert row["agent"] == "router"
    assert row["tool"] == "llm"
    assert row["status"] == "ok"
    assert json.loads(row["args_redacted_json"]) == {"prompt": "hi"}
    assert json.loads(row["result_summary_json"]) == {"intent": "paper_qa"}


def test_record_redacts_api_keys(db_path: Path) -> None:
    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id, step_index=0, parent_step=None,
        agent="router", tool="llm", model="claude-haiku-4-5",
        args={"prompt": "sk-ant-AAAAA1234567890 hello"},
        result_summary=None, latency_ms=1, token_in=None, token_out=None,
        status="ok", error=None,
    )
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT args_redacted_json FROM tool_calls WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
    args = json.loads(row["args_redacted_json"])
    assert "sk-ant-AAAAA1234567890" not in args["prompt"]
    assert "<REDACTED:api-key>" in args["prompt"]


def test_record_step_enforces_unique_step_index(db_path: Path) -> None:
    run_id = uuid4()
    _insert_run(db_path, str(run_id))
    tracer = ToolCallTracer(db_path=db_path)
    tracer.record(
        run_id=run_id, step_index=0, parent_step=None,
        agent="router", tool="llm", model=None,
        args={}, result_summary=None, latency_ms=1,
        token_in=None, token_out=None, status="ok", error=None,
    )
    with pytest.raises(Exception):  # sqlite3.IntegrityError; broad to avoid import noise
        tracer.record(
            run_id=run_id, step_index=0, parent_step=None,
            agent="router", tool="llm", model=None,
            args={}, result_summary=None, latency_ms=1,
            token_in=None, token_out=None, status="ok", error=None,
        )
```

- [ ] **Step 7: Run tracer test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/tracing/test_tracer.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.tracing.tracer'`.

- [ ] **Step 8: Write the tracer**

Create `backend/paperhub/tracing/tracer.py`:

```python
"""Tool-Call Tracer — single source of truth for FR-11 and FR-12.

Per design §6 persistence model, each call commits its own short
transaction *before* the corresponding SSE event is emitted. This module
gives agents one synchronous `record(...)` method; the LangGraph
context-manager / decorator wrappers that wire it into the call graph
are added in Phase 1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from paperhub.data.db import connect
from paperhub.data.models import ToolCallStatus
from paperhub.tracing.redactor import redact


class ToolCallTracer:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def record(
        self,
        *,
        run_id: UUID,
        step_index: int,
        parent_step: int | None,
        agent: str,
        tool: str,
        model: str | None,
        args: dict[str, object],
        result_summary: dict[str, object] | None,
        latency_ms: int,
        token_in: int | None,
        token_out: int | None,
        status: ToolCallStatus,
        error: str | None,
    ) -> None:
        redacted_args = redact(args)
        with connect(self._db_path) as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT INTO tool_calls("
                    "  run_id, step_index, parent_step, agent, tool, model,"
                    "  args_redacted_json, result_summary_json,"
                    "  latency_ms, token_in, token_out, status, error"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(run_id),
                        step_index,
                        parent_step,
                        agent,
                        tool,
                        model,
                        json.dumps(redacted_args, sort_keys=True),
                        json.dumps(result_summary, sort_keys=True) if result_summary is not None else None,
                        latency_ms,
                        token_in,
                        token_out,
                        status,
                        error,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
```

- [ ] **Step 9: Run tracer test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/tracing/test_tracer.py -v
```
Expected: 3 passed.

- [ ] **Step 10: Commit**

```powershell
git add backend/paperhub/tracing/__init__.py backend/paperhub/tracing/redactor.py backend/paperhub/tracing/tracer.py backend/tests/tracing/__init__.py backend/tests/tracing/test_redactor.py backend/tests/tracing/test_tracer.py
git commit -m "feat(tracing): add Tool-Call Tracer with secret/home-path redaction"
```

---

## Task 11: MCP scope-checker + typed McpInvocation

**Files:**
- Create: `backend/paperhub/mcp/__init__.py`
- Create: `backend/paperhub/mcp/scopes.py`
- Create: `backend/paperhub/mcp/client.py`
- Create: `backend/tests/mcp/__init__.py`
- Create: `backend/tests/mcp/test_scopes.py`

The full client wiring (dispatching to actual MCP servers over stdio) happens in Phase 3. Phase 0 ships the *scope-checker* — the gate that decides whether a call is allowed — plus the typed argument schemas for the three tools we definitely need by Phase 1 (`arxiv`, `grobid`, `filesystem`). Other tools' arg models are added in their owning phases.

- [ ] **Step 1: Create the package dirs**

Run:
```powershell
New-Item -ItemType Directory -Path backend/paperhub/mcp, backend/tests/mcp -Force | Out-Null
"" | Out-File -Encoding utf8 backend/paperhub/mcp/__init__.py
"" | Out-File -Encoding utf8 backend/tests/mcp/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/mcp/test_scopes.py`:

```python
"""Tests for the typed MCP scope-checker (NFR-10)."""
from __future__ import annotations

from pathlib import Path

import pytest

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
    scope = McpToolScope(
        tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True
    )
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / "out.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert result is None  # None = allowed


def test_filesystem_write_outside_root_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(
        tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True
    )
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace.parent / "escaped.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "outside filesystem root" in result.reason


def test_filesystem_write_traversal_attempt_is_rejected(tmp_workspace: Path) -> None:
    """EscapeRoute regression: `..` in the path must not escape the root.

    Mirrors the CVE-2025-53109/53110 attack surface for upstream
    `@modelcontextprotocol/server-filesystem`. The Python-side
    scope-checker is the second line of defense.
    """
    scope = McpToolScope(
        tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=True
    )
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / ".." / "escaped.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)


def test_filesystem_write_when_write_not_allowed_is_rejected(tmp_workspace: Path) -> None:
    scope = McpToolScope(
        tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=False
    )
    inv = McpInvocation(
        tool="filesystem",
        method="write_file",
        args=FilesystemWriteArgs(path=tmp_workspace / "x.pdf", content=b"hi"),
    )
    result = check_scope(inv, scope)
    assert isinstance(result, ScopeRejection)
    assert "write" in result.reason.lower()


def test_filesystem_read_inside_root_is_ok(tmp_workspace: Path) -> None:
    scope = McpToolScope(
        tool_name="filesystem", filesystem_root=tmp_workspace, write_allowed=False
    )
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
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```powershell
cd backend; uv run pytest tests/mcp/test_scopes.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'paperhub.mcp.scopes'`.

- [ ] **Step 4: Write the typed args + scopes module**

Create `backend/paperhub/mcp/scopes.py`:

```python
"""Typed MCP scopes + per-tool argument schemas + scope-checker.

Per design §7, every outbound MCP call is validated against a typed
`McpToolScope` *before* dispatch. Argument payloads from the JSON-RPC
wire are parsed into one of the per-(tool, method) Pydantic models
below — this is the documented exception to NFR-11's "no untyped dict
at I/O boundary" rule.

Phase 0 ships the three argument schemas Phase 1 needs (`arxiv` and
`filesystem`); later phases add the rest in their owning files.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class McpToolScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool_name: str
    filesystem_root: Path | None = None
    sqlite_table_allowlist: list[str] | None = None
    url_domain_allowlist: list[str] | None = None
    write_allowed: bool = False


# ---------- Per-(tool, method) typed args (extended in later phases) ----------

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


# ---------- Scope check ----------

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
    # arXiv args: no path / domain check needed at this layer — the
    # `arxiv` MCP server itself is domain-pinned (per the launchers config).
    return None
```

- [ ] **Step 5: Write a thin stub MCP client that uses the scope-checker**

Create `backend/paperhub/mcp/client.py`:

```python
"""MCP client + scope-check dispatcher (Phase 0 stub).

The real stdio/socket dispatch to upstream MCP servers (via FastMCP) is
filled in in Phase 1 (`arxiv`, `grobid`) and Phase 3 (the rest). The
*scope-check gate* lives here from day 1 so every later phase plugs
into a single auditable validation point.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from paperhub.mcp.scopes import McpInvocation, McpToolScope, ScopeRejection, check_scope


class McpScopeViolation(RuntimeError):
    def __init__(self, rejection: ScopeRejection, invocation: McpInvocation) -> None:
        super().__init__(rejection.reason)
        self.rejection = rejection
        self.invocation = invocation


# Dispatcher signature — Phase 1 implementations will be stdio/FastMCP backed.
McpDispatcher = Callable[[McpInvocation], Awaitable[dict[str, object]]]


class McpClient:
    def __init__(self, *, scopes: dict[str, McpToolScope], dispatcher: McpDispatcher) -> None:
        self._scopes = scopes
        self._dispatcher = dispatcher

    async def call(self, invocation: McpInvocation) -> dict[str, object]:
        scope = self._scopes.get(invocation.tool)
        if scope is None:
            raise McpScopeViolation(
                ScopeRejection(reason=f"no scope configured for tool {invocation.tool!r}"),
                invocation,
            )
        rejection = check_scope(invocation, scope)
        if rejection is not None:
            raise McpScopeViolation(rejection, invocation)
        return await self._dispatcher(invocation)
```

- [ ] **Step 6: Run test to verify it passes**

Run:
```powershell
cd backend; uv run pytest tests/mcp/test_scopes.py -v
```
Expected: 7 passed.

- [ ] **Step 7: Commit**

```powershell
git add backend/paperhub/mcp/__init__.py backend/paperhub/mcp/scopes.py backend/paperhub/mcp/client.py backend/tests/mcp/__init__.py backend/tests/mcp/test_scopes.py
git commit -m "feat(mcp): add typed McpToolScope + scope-checker + stub MCP client"
```

---

## Task 12: KNOWN-TYPE-GAPS register + CI workflow + pre-commit

**Files:**
- Create: `docs/KNOWN-TYPE-GAPS.md`
- Create: `.github/workflows/ci.yml`
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Create the KNOWN-TYPE-GAPS register**

Create `docs/KNOWN-TYPE-GAPS.md`:

```markdown
# Known upstream type-stub gaps

Per **SRS NFR-11 narrow exception**, every `# type: ignore[<code>]` comment
in PaperHub Python code must reference an entry here. Bare `# type: ignore`
fails CI via `warn_unused_ignores = true` + ruff.

When upstream ships a fix, remove the `# type: ignore[...]` comment and
delete the corresponding row below.

| Site (file:line) | Upstream | mypy error code | Tracked since | Why it's needed |
|---|---|---|---|---|
| (none yet — Phase 0 has no upstream-boundary ignores) | — | — | — | — |
```

- [ ] **Step 2: Write the CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "latest"

      - name: Set up Python
        run: uv python install 3.12

      - name: Sync dependencies
        run: uv sync --frozen

      - name: Ruff (format + lint)
        run: |
          uv run ruff format --check .
          uv run ruff check .

      - name: Mypy --strict
        run: uv run mypy

      - name: Pytest
        run: uv run pytest -q

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4

      - name: Use Node 20
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json

      - name: Install
        run: npm ci

      - name: Type-check
        run: npm run typecheck

      - name: Lint
        run: npm run lint

      - name: Build
        run: npm run build
```

- [ ] **Step 3: Write the pre-commit config**

Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.4
    hooks:
      - id: ruff-format
        files: ^backend/
      - id: ruff
        args: [--fix]
        files: ^backend/
  - repo: local
    hooks:
      - id: mypy-strict
        name: mypy --strict (backend)
        entry: bash -c "cd backend && uv run mypy"
        language: system
        pass_filenames: false
        files: ^backend/.*\.py$
```

- [ ] **Step 4: Verify the backend CI commands run locally**

Run (from `backend/`):
```powershell
cd backend; uv run ruff format --check .; uv run ruff check .; uv run mypy; uv run pytest -q
```
Expected: ruff clean, mypy clean, pytest reports all tests passing.

- [ ] **Step 5: Commit**

```powershell
git add docs/KNOWN-TYPE-GAPS.md .github/workflows/ci.yml .pre-commit-config.yaml
git commit -m "ci(backend): add mypy --strict + pytest + ruff CI and pre-commit hooks"
```

---

## Task 13: Frontend scaffold (React + Vite + Tailwind shell)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/.gitignore`

- [ ] **Step 1: Scaffold the Vite + React + TS project**

Run (from repo root):
```powershell
New-Item -ItemType Directory -Path frontend -Force | Out-Null
npm create vite@latest frontend -- --template react-ts
```
When the wizard runs, accept the defaults (it creates `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, and `src/`).

- [ ] **Step 2: Install Tailwind 4 and the toolchain deps**

Run (from `frontend/`):
```powershell
cd frontend; npm install; npm install -D tailwindcss@^4 @tailwindcss/vite typescript@^5 vitest @vitest/ui @testing-library/react @testing-library/jest-dom jsdom eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin
```

- [ ] **Step 3: Configure Tailwind via the Vite plugin**

Overwrite `frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
```

Create `frontend/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

Overwrite `frontend/src/index.css`:

```css
@import "tailwindcss";

:root {
  color-scheme: light dark;
}
```

- [ ] **Step 4: Replace the generated App with the PaperHub shell**

Overwrite `frontend/src/App.tsx`:

```tsx
export default function App() {
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 grid place-items-center">
      <div className="text-center space-y-2">
        <h1 className="text-3xl font-semibold">PaperHub</h1>
        <p className="text-neutral-400">
          Phase 0 shell. The chat, paper panel, slide editor, and trace viewer
          land in subsequent phases.
        </p>
      </div>
    </div>
  );
}
```

Overwrite `frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element in index.html");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

- [ ] **Step 5: Add `typecheck`, `lint`, and `test` npm scripts**

Patch `frontend/package.json` so the `scripts` block reads:

```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "typecheck": "tsc --noEmit",
  "lint": "eslint . --ext .ts,.tsx",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 6: Write a smoke test for the App shell**

Create `frontend/src/App.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App shell", () => {
  it("renders the PaperHub heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "PaperHub" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 7: Run frontend checks to verify they pass**

Run (from `frontend/`):
```powershell
cd frontend; npm run typecheck; npm run test; npm run build
```
Expected: typecheck clean, 1 vitest passes, `vite build` emits `dist/`.

- [ ] **Step 8: Commit**

```powershell
git add frontend/
git commit -m "feat(frontend): scaffold React+Vite+Tailwind shell with vitest smoke test"
```

---

## Phase 0 done

Verify end-to-end (from repo root):

```powershell
cd backend; uv run ruff format --check .; uv run ruff check .; uv run mypy; uv run pytest -q; cd ..; cd frontend; npm run typecheck; npm run test; npm run build; cd ..
```

All commands should succeed. Phase 1 picks up from here with the first vertical slice: manual paper import → RAG QA → SSE-streamed answer with citations + tool trace.

## Self-review checklist (already performed by plan author)

- **Spec coverage** — Phase 0 from the design (§4) lights up NFR-11 (strict typing CI), groundwork for FR-08 and FR-11, and the substrate every later phase depends on. Tasks 1-13 collectively land every Phase-0 bullet from the design.
- **Placeholders** — none. Each step contains the exact file path, the exact command, expected output, and complete code where code is involved.
- **Type consistency** — `Settings`, `LlmMessage`, `LlmAdapter`, `ChunkVector`, `VectorSearchHit`, `McpToolScope`, `McpInvocation`, `ScopeRejection`, `ToolCallTracer` are defined once in the task that owns them and used by name in tests.
- **Ambiguity** — Task 7 explicitly notes the real Anthropic call is deferred to Phase 1; Task 11 explicitly notes the real MCP dispatch is deferred to Phase 1/3; Task 13 uses `npm create vite@latest` and notes the wizard defaults are accepted.
- **TDD discipline** — every behavior task follows write-test → verify-fail → write-impl → verify-pass → commit. Scaffold tasks (1, 3, 12, 13) are config-only and have no failing-test step.

## Open items deferred to Phase 1

- `AnthropicAdapter.generate` body (real network call against the Anthropic SDK, structured output via tool-use)
- Real MCP dispatcher launching the configured upstream servers from `mcp/launchers.yaml`
- LangGraph wiring of the Router + Research sub-graph + tracer integration
- The `chroma` collection-per-project filter (currently single collection; project filter lands when projects ship in Phase 6)
