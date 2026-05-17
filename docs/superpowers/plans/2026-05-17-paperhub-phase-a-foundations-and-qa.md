# PaperHub Phase A — Foundations + Q&A Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every cross-cutting foundation PaperHub will need and deliver the first end-to-end vertical slice — manual arXiv paper import → grounded paper Q&A via RAG → SSE-streamed answer with inline citations and a live Tool-Trace panel. By end of Phase A, the user can boot the app, import a paper, ask a question, get a cited answer, and inspect the full audit trail.

**Architecture:** Python 3.12 + FastAPI + LangGraph + LiteLLM on the backend; React 18 + Vite + Tailwind on the frontend; SQLite primary store, Chroma vector backend, FastMCP for our two MCP servers (`grobid` wrap), one reused community server (`arxiv`). Vertical-slice expansion strategy — Phase A produces a working `paper_qa` flow; Phase B widens to all six intents; Phase C is eval + NFR polish.

**Tech Stack:** Python 3.12 · uv · FastAPI · LangGraph · LiteLLM · Pydantic v2 · pydantic-settings · SQLite · ChromaDB · FastMCP · `arxiv-mcp-server` (reused) · `grobid-client-python` · `sentence-transformers` (embedder) · `bge-reranker-base` · pytest · mypy --strict · ruff · React 18 · Vite · TypeScript · Tailwind 4

**Companion spec:** [Implementation Design (3-phase plan)](../specs/2026-05-17-paperhub-implementation-design.md) — companion to [SRS v1.6](../specs/2026-05-17-paperhub-srs.md).

---

## Pre-flight

All paths relative to the repo root (`d:\GitHub\PaperHub`). Backend under `backend/`, frontend under `frontend/`. **PowerShell** (Windows native) for shell commands. **`uv`** (NOT system `python` or `pip`) for all Python operations. **Conventional Commits** subject lines: `action(scope): what you do` — imperative subject; scope is the touched module (`backend`, `data`, `llm`, `mcp`, `agents`, `api`, `frontend`, `ci`, `docs`, `chore`).

## Phase A task graph (10 chunky tasks)

```
1 scaffold ─┬─ 2 config+db+models ─┬─ 3 fastapi shell ──────┬─ 6 RAG+Research Agent ──┬─ 7 chat UI ─ 8 e2e smoke
            │                       │                        │                         │
            │                       ├─ 4 LiteLLM+prompts ────┤                         │
            │                       │                        │                         │
            │                       └─ 5 vectors+tracer+MCP ─┴─ (arxiv MCP + grobid MCP wrap)
                                                              │
                                                              └─ Router (binary) ──────┘
                                                                                       │
                                                                          9 CI + KNOWN-TYPE-GAPS
                                                                                       │
                                                                                       10 docs sweep
```

Each task corresponds to a coherent unit of value, follows TDD (write test → make pass → commit), and ends with a working build. Tasks 1–5 are largely independent and could parallelize if a future engineer wants to; Tasks 6–8 are sequential.

---

## Task 1 — Backend + frontend scaffolds

**Files (create):**
- `backend/pyproject.toml`, `backend/.python-version`, `backend/paperhub/__init__.py`, `backend/tests/__init__.py`, `backend/tests/conftest.py`
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`, `frontend/src/test-setup.ts`, `frontend/src/App.test.tsx`

**Steps:**

- [ ] **1.1** Create backend dirs + uv project:

  ```powershell
  New-Item -ItemType Directory -Path backend, backend/paperhub, backend/tests -Force | Out-Null
  "3.12" | Out-File -Encoding utf8 backend/.python-version
  "" | Out-File -Encoding utf8 backend/paperhub/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/__init__.py
  uv init backend --no-readme --no-pin-python
  ```

- [ ] **1.2** Overwrite `backend/pyproject.toml`:

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
    "litellm>=1.52",
    "chromadb>=0.5.20",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "langgraph>=0.2.50",
    "grobid-client-python>=0.0.9",
    "sentence-transformers>=3.3",
    "sse-starlette>=2.1",
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
  warn_unused_ignores = true

  [tool.ruff]
  line-length = 100
  target-version = "py312"

  [tool.ruff.lint]
  select = ["E", "F", "I", "B", "UP", "TID", "RUF"]
  ```

- [ ] **1.3** Create `backend/tests/conftest.py`:

  ```python
  """Shared pytest fixtures for the PaperHub backend test suite."""
  from __future__ import annotations

  from pathlib import Path

  import pytest


  @pytest.fixture()
  def tmp_workspace(tmp_path: Path) -> Path:
      workspace = tmp_path / "workspace"
      workspace.mkdir()
      return workspace
  ```

- [ ] **1.4** Verify backend installs and pytest runs:

  ```powershell
  cd backend; uv sync; uv run pytest --collect-only
  ```
  Expected: `collected 0 items`.

- [ ] **1.5** Scaffold the frontend with Vite + React + TS, then install Tailwind 4 + testing tools (from repo root):

  ```powershell
  npm create vite@latest frontend -- --template react-ts
  cd frontend; npm install; npm install -D tailwindcss@^4 @tailwindcss/vite vitest @vitest/ui @testing-library/react @testing-library/jest-dom jsdom eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin
  ```

- [ ] **1.6** Overwrite `frontend/vite.config.ts`:

  ```ts
  import { defineConfig } from "vite";
  import react from "@vitejs/plugin-react";
  import tailwindcss from "@tailwindcss/vite";

  export default defineConfig({
    plugins: [react(), tailwindcss()],
    test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
  });
  ```

- [ ] **1.7** Create `frontend/src/test-setup.ts`:

  ```ts
  import "@testing-library/jest-dom/vitest";
  ```

- [ ] **1.8** Overwrite `frontend/src/index.css`:

  ```css
  @import "tailwindcss";

  :root { color-scheme: light dark; }
  ```

- [ ] **1.9** Overwrite `frontend/src/App.tsx` and `frontend/src/main.tsx`:

  ```tsx
  // App.tsx
  export default function App() {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100 grid place-items-center">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-semibold">PaperHub</h1>
          <p className="text-neutral-400">Phase A shell.</p>
        </div>
      </div>
    );
  }
  ```

  ```tsx
  // main.tsx
  import { StrictMode } from "react";
  import { createRoot } from "react-dom/client";
  import App from "./App";
  import "./index.css";

  const root = document.getElementById("root");
  if (!root) throw new Error("Missing #root in index.html");
  createRoot(root).render(<StrictMode><App /></StrictMode>);
  ```

- [ ] **1.10** Add npm scripts in `frontend/package.json` (`scripts` block):

  ```json
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "typecheck": "tsc --noEmit",
  "lint": "eslint . --ext .ts,.tsx",
  "test": "vitest run",
  "test:watch": "vitest"
  ```

- [ ] **1.11** Smoke test for the App shell — create `frontend/src/App.test.tsx`:

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

- [ ] **1.12** Verify frontend checks pass:

  ```powershell
  cd frontend; npm run typecheck; npm run test; npm run build
  ```
  Expected: typecheck clean, 1 test passes, `dist/` emitted.

- [ ] **1.13** Commit:

  ```powershell
  git add backend/ frontend/
  git commit -m "chore(scaffold): bootstrap backend (uv+FastAPI deps) and frontend (Vite+React+Tailwind+vitest)"
  ```

---

## Task 2 — Settings, SQLite migration runner, Pydantic data models

**Files (create):**
- `backend/paperhub/config.py`
- `backend/paperhub/data/__init__.py`, `backend/paperhub/data/db.py`, `backend/paperhub/data/models.py`
- `backend/paperhub/data/migrations/__init__.py`, `backend/paperhub/data/migrations/0001_initial.sql`
- `backend/tests/data/__init__.py`, `backend/tests/test_config.py`, `backend/tests/data/test_db.py`, `backend/tests/data/test_models.py`

**Steps:**

- [ ] **2.1** Create `backend/paperhub/config.py`:

  ```python
  """Typed Settings singleton — all env-derived config flows through here (NFR-04, NFR-11)."""
  from __future__ import annotations

  from pathlib import Path
  from typing import Literal

  from pydantic import SecretStr
  from pydantic_settings import BaseSettings, SettingsConfigDict


  class Settings(BaseSettings):
      model_config = SettingsConfigDict(
          env_file=".env", env_file_encoding="utf-8",
          env_prefix="", extra="ignore", case_sensitive=False,
      )

      workspace_root: Path
      db_path: Path

      vector_backend: Literal["chroma", "sqlite-vec"] = "chroma"
      chroma_path: Path | None = None

      router_model: str = "claude-haiku-4-5"
      generation_model: str = "claude-sonnet-4-6"
      judge_model: str = "claude-haiku-4-5"
      embedding_model: str = "BAAI/bge-small-en-v1.5"
      reranker_model: str = "BAAI/bge-reranker-base"

      anthropic_api_key: SecretStr | None = None
      openai_api_key: SecretStr | None = None
      ollama_base_url: str = "http://localhost:11434"

      mcp_arxiv_command: str = "uvx arxiv-mcp-server"
      mcp_filesystem_command: str = "npx -y @modelcontextprotocol/server-filesystem"
      grobid_url: str = "http://localhost:8070"


  def get_settings() -> Settings:
      return Settings()  # type: ignore[call-arg]
  ```

- [ ] **2.2** Create the migrations package and the initial migration:

  ```powershell
  New-Item -ItemType Directory -Path backend/paperhub/data, backend/paperhub/data/migrations, backend/tests/data -Force | Out-Null
  "" | Out-File -Encoding utf8 backend/paperhub/data/__init__.py
  "" | Out-File -Encoding utf8 backend/paperhub/data/migrations/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/data/__init__.py
  ```

  Then create `backend/paperhub/data/migrations/0001_initial.sql` — copy the full schema from design §6 verbatim (twelve tables: `schema_migrations`, `projects`, `papers`, `project_papers`, `tags`, `notes`, `chunks`, `citations`, `chat_sessions`, `messages`, `runs`, `tool_calls`, plus their indexes). Reproduced inline to keep this plan readable:

  ```sql
  PRAGMA foreign_keys = ON;
  PRAGMA journal_mode = WAL;

  CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);

  CREATE TABLE projects (
      id TEXT PRIMARY KEY, name TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE papers (
      id TEXT PRIMARY KEY, arxiv_id TEXT UNIQUE, doi TEXT UNIQUE,
      title TEXT NOT NULL, authors_json TEXT NOT NULL, year INTEGER,
      abstract TEXT, pdf_path TEXT NOT NULL, sha256 TEXT NOT NULL,
      primary_topic TEXT, added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  CREATE INDEX idx_papers_year_topic ON papers(year, primary_topic);

  CREATE TABLE project_papers (
      project_id TEXT NOT NULL REFERENCES projects(id),
      paper_id TEXT NOT NULL REFERENCES papers(id),
      reading_status TEXT CHECK(reading_status IN ('unread','skimmed','deep')),
      PRIMARY KEY (project_id, paper_id)
  );

  CREATE TABLE tags (
      paper_id TEXT NOT NULL REFERENCES papers(id),
      tag TEXT NOT NULL, PRIMARY KEY (paper_id, tag)
  );

  CREATE TABLE notes (
      id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
      body_md TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE chunks (
      id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
      section TEXT, page INTEGER, char_start INTEGER, char_end INTEGER, text TEXT NOT NULL
  );
  CREATE INDEX idx_chunks_paper ON chunks(paper_id);

  CREATE TABLE citations (
      src_paper_id TEXT NOT NULL REFERENCES papers(id),
      dst_paper_id TEXT NOT NULL REFERENCES papers(id),
      source TEXT NOT NULL, PRIMARY KEY (src_paper_id, dst_paper_id)
  );

  CREATE TABLE chat_sessions (
      id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
      title TEXT, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE messages (
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES chat_sessions(id),
      role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
      content TEXT NOT NULL, run_id TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE runs (
      id TEXT PRIMARY KEY, session_id TEXT REFERENCES chat_sessions(id),
      routing_decision_json TEXT, started_at TIMESTAMP NOT NULL,
      finished_at TIMESTAMP, status TEXT CHECK(status IN ('running','ok','failed'))
  );

  CREATE TABLE tool_calls (
      run_id TEXT NOT NULL REFERENCES runs(id),
      step_index INTEGER NOT NULL, parent_step INTEGER,
      agent TEXT NOT NULL, tool TEXT NOT NULL, model TEXT,
      args_redacted_json TEXT NOT NULL, result_summary_json TEXT,
      latency_ms INTEGER NOT NULL, token_in INTEGER, token_out INTEGER,
      status TEXT NOT NULL CHECK(status IN ('ok','error','rejected')),
      error TEXT, PRIMARY KEY (run_id, step_index)
  );
  CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, step_index);
  ```

- [ ] **2.3** Create `backend/paperhub/data/db.py`:

  ```python
  """SQLite connection helper + forward-only migration runner."""
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
      conn = sqlite3.connect(db_path, isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
      try:
          conn.execute("PRAGMA foreign_keys = ON")
          conn.row_factory = sqlite3.Row
          yield conn
      finally:
          conn.close()


  def _list_migrations() -> list[tuple[int, str, str]]:
      out: list[tuple[int, str, str]] = []
      pkg = resources.files("paperhub.data.migrations")
      for entry in pkg.iterdir():
          m = _MIGRATION_NAME_RE.match(entry.name)
          if not m:
              continue
          out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
      out.sort(key=lambda t: t[0])
      return out


  def apply_migrations(db_path: Path) -> None:
      db_path.parent.mkdir(parents=True, exist_ok=True)
      with connect(db_path) as conn:
          conn.execute(
              "CREATE TABLE IF NOT EXISTS schema_migrations ("
              " version INTEGER PRIMARY KEY,"
              " applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
          )
          applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
          for version, _name, sql in _list_migrations():
              if version in applied:
                  continue
              conn.execute("BEGIN")
              try:
                  conn.executescript(sql)
                  conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                  conn.execute("COMMIT")
              except Exception:
                  conn.execute("ROLLBACK")
                  raise
  ```

- [ ] **2.4** Create `backend/paperhub/data/models.py` — full set of Pydantic models for every persisted entity. Models: `Project`, `Paper`, `ProjectPaper`, `Tag`, `Note`, `Chunk`, `Citation`, `ChatSession`, `Message`, `RoutingDecision`, `RunMetadata`, `ToolCall`. Literal aliases: `ReadingStatus`, `MessageRole`, `RunStatus`, `ToolCallStatus`, `ModelTier`, `Intent`. All models frozen, `extra="forbid"`. `RoutingDecision.confidence` constrained `ge=0.0, le=1.0`. `ToolCall.latency_ms` constrained `ge=0`.

  ```python
  """Pydantic data models for every persisted entity (NFR-11)."""
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
      id: UUID; name: str; created_at: datetime


  class Paper(_Frozen):
      id: UUID; arxiv_id: str | None; doi: str | None
      title: str; authors: list[str]; year: int | None
      abstract: str | None; pdf_path: str; sha256: str
      primary_topic: str | None; added_at: datetime


  class ProjectPaper(_Frozen):
      project_id: UUID; paper_id: UUID; reading_status: ReadingStatus | None


  class Tag(_Frozen):
      paper_id: UUID; tag: str


  class Note(_Frozen):
      id: UUID; paper_id: UUID; body_md: str; created_at: datetime


  class Chunk(_Frozen):
      id: UUID; paper_id: UUID; section: str | None; page: int | None
      char_start: int | None; char_end: int | None; text: str


  class Citation(_Frozen):
      src_paper_id: UUID; dst_paper_id: UUID; source: str


  class ChatSession(_Frozen):
      id: UUID; project_id: UUID | None; title: str | None; created_at: datetime


  class Message(_Frozen):
      id: UUID; session_id: UUID; role: MessageRole
      content: str; run_id: UUID | None; created_at: datetime


  class RoutingDecision(_Frozen):
      intent: Intent
      confidence: float = Field(ge=0.0, le=1.0)
      model_tier: ModelTier
      reasoning: str
      fallback_to_user: bool = False


  class RunMetadata(_Frozen):
      id: UUID; session_id: UUID | None
      routing_decision: RoutingDecision | None
      started_at: datetime; finished_at: datetime | None; status: RunStatus


  class ToolCall(_Frozen):
      run_id: UUID; step_index: int; parent_step: int | None
      agent: str; tool: str; model: str | None
      args_redacted: dict[str, object]
      result_summary: dict[str, object] | None
      latency_ms: int = Field(ge=0)
      token_in: int | None = Field(default=None, ge=0)
      token_out: int | None = Field(default=None, ge=0)
      status: ToolCallStatus
      error: str | None
  ```

- [ ] **2.5** Write tests covering: `Settings` loads env vars; `apply_migrations` creates all 12 tables, is idempotent, enables foreign keys; `RoutingDecision` rejects out-of-range confidence; `ToolCall` rejects invalid status; `Paper` round-trips through `model_dump` / `model_validate`. Save as `backend/tests/test_config.py`, `backend/tests/data/test_db.py`, `backend/tests/data/test_models.py` (refer to the original Phase 0 plan in git history `5374dfd` for the exact test bodies).

- [ ] **2.6** Verify and commit:

  ```powershell
  cd backend; uv run pytest -q
  git add backend/paperhub/config.py backend/paperhub/data/ backend/tests/test_config.py backend/tests/data/
  git commit -m "feat(data): add Settings + SQLite migrations + Pydantic data models"
  ```

---

## Task 3 — FastAPI app shell with `/health`

**Files (create):**
- `backend/paperhub/api/__init__.py`, `backend/paperhub/api/app.py`, `backend/paperhub/api/schemas.py`
- `backend/tests/api/__init__.py`, `backend/tests/api/test_health.py`

**Steps:**

- [ ] **3.1** Create dirs:

  ```powershell
  New-Item -ItemType Directory -Path backend/paperhub/api, backend/tests/api -Force | Out-Null
  "" | Out-File -Encoding utf8 backend/paperhub/api/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/api/__init__.py
  ```

- [ ] **3.2** Create `backend/paperhub/api/schemas.py`:

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

- [ ] **3.3** Create `backend/paperhub/api/app.py`:

  ```python
  """FastAPI ASGI app factory."""
  from __future__ import annotations

  from collections.abc import AsyncIterator
  from contextlib import asynccontextmanager

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

- [ ] **3.4** Write test in `backend/tests/api/test_health.py`:

  ```python
  from __future__ import annotations

  from pathlib import Path

  import pytest
  from fastapi.testclient import TestClient


  @pytest.fixture()
  def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
      monkeypatch.setenv("PAPERHUB_WORKSPACE_ROOT", str(tmp_path))
      monkeypatch.setenv("PAPERHUB_DB_PATH", str(tmp_path / "paperhub.db"))
      from paperhub.api.app import create_app
      return TestClient(create_app())


  def test_health(client: TestClient) -> None:
      r = client.get("/health")
      assert r.status_code == 200
      body = r.json()
      assert body == {"status": "ok", "app": "paperhub", "schema_version": 1}
  ```

- [ ] **3.5** Verify and commit:

  ```powershell
  cd backend; uv run pytest tests/api/test_health.py -v
  git add backend/paperhub/api/ backend/tests/api/
  git commit -m "feat(api): add FastAPI app shell with /health and migrations-on-startup"
  ```

---

## Task 4 — LiteLLM adapter + YAML prompt registry

**Files (create):**
- `backend/paperhub/llm/__init__.py`, `backend/paperhub/llm/adapter.py`, `backend/paperhub/llm/prompts.py`, `backend/paperhub/llm/prompts.yaml`
- `backend/tests/llm/__init__.py`, `backend/tests/llm/test_adapter.py`, `backend/tests/llm/test_prompts.py`

**Design:** `LlmAdapter` is a Protocol with one method `generate(messages, model_tier, response_model, slot) -> BaseModel`. Production impl `LiteLlmAdapter` calls `litellm.acompletion(...)` with `response_format={"type": "json_schema", "json_schema": {"name": slot, "strict": True, "schema": response_model.model_json_schema()}}` and parses the JSON response back into `response_model`. Test impl `FakeAdapter` returns canned Pydantic instances by `slot`.

**Steps:**

- [ ] **4.1** Create dirs and the prompt registry seed:

  ```powershell
  New-Item -ItemType Directory -Path backend/paperhub/llm, backend/tests/llm -Force | Out-Null
  "" | Out-File -Encoding utf8 backend/paperhub/llm/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/llm/__init__.py
  ```

  `backend/paperhub/llm/prompts.yaml`:

  ```yaml
  router:
    v1:
      system: |
        You are PaperHub's task router. Classify the user's request into exactly one
        of the allowed intents. Be conservative — when unsure, return out_of_scope
        with low confidence so the user is asked to clarify.
      user_template: |
        User request:
        {user_message}

  research_qa:
    v1:
      system: |
        You are PaperHub's research assistant. Answer the user's question using ONLY
        the provided paper passages. Every claim MUST be followed by a citation in the
        form (§section, p.page) sourced from the passages. If the passages don't
        contain the answer, reply exactly:
        "No relevant information found in the indexed papers."
      user_template: |
        Question: {question}

        Passages:
        {passages}
  ```

- [ ] **4.2** Create `backend/paperhub/llm/prompts.py`:

  ```python
  """YAML-driven prompt registry."""
  from __future__ import annotations

  from dataclasses import dataclass
  from importlib import resources
  from typing import cast

  import yaml


  class PromptNotFoundError(KeyError): ...


  @dataclass(frozen=True)
  class RenderedPrompt:
      system: str
      user: str


  class PromptRegistry:
      def __init__(self, data: dict[str, dict[str, dict[str, str]]]) -> None:
          self._data = data

      @classmethod
      def load_default(cls) -> "PromptRegistry":
          text = resources.files("paperhub.llm").joinpath("prompts.yaml").read_text(encoding="utf-8")
          return cls(cast(dict[str, dict[str, dict[str, str]]], yaml.safe_load(text)))

      def render(self, *, slot: str, version: str, **vars: object) -> RenderedPrompt:
          slot_entry = self._data.get(slot)
          if slot_entry is None:
              raise PromptNotFoundError(f"slot {slot!r}")
          ver_entry = slot_entry.get(version)
          if ver_entry is None:
              raise PromptNotFoundError(f"version {version!r} of slot {slot!r}")
          return RenderedPrompt(system=ver_entry["system"], user=ver_entry["user_template"].format(**vars))
  ```

- [ ] **4.3** Create `backend/paperhub/llm/adapter.py`:

  ```python
  """LLM Provider Adapter — LiteLLM-backed production impl + FakeAdapter for tests."""
  from __future__ import annotations

  import json
  from typing import Literal, Protocol, TypeVar

  import litellm
  from pydantic import BaseModel

  ModelTier = Literal["small", "flagship"]
  LlmRole = Literal["system", "user", "assistant"]


  class LlmMessage(BaseModel):
      role: LlmRole
      content: str


  T = TypeVar("T", bound=BaseModel)


  class LlmAdapter(Protocol):
      async def generate(
          self, *, messages: list[LlmMessage], model_tier: ModelTier,
          response_model: type[T], slot: str,
      ) -> T: ...


  class FakeAdapter:
      """Test double — returns canned Pydantic instances keyed by `slot`."""

      def __init__(self, canned: dict[str, BaseModel]) -> None:
          self._canned = canned

      async def generate(
          self, *, messages: list[LlmMessage], model_tier: ModelTier,
          response_model: type[T], slot: str,
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


  class LiteLlmAdapter:
      """Production adapter: structured output across Anthropic/OpenAI/Ollama via LiteLLM."""

      def __init__(self, *, small_model: str, flagship_model: str) -> None:
          self._small = small_model
          self._flagship = flagship_model

      async def generate(
          self, *, messages: list[LlmMessage], model_tier: ModelTier,
          response_model: type[T], slot: str,
      ) -> T:
          model = self._small if model_tier == "small" else self._flagship
          response = await litellm.acompletion(
              model=model,
              messages=[m.model_dump() for m in messages],
              response_format={
                  "type": "json_schema",
                  "json_schema": {
                      "name": slot,
                      "strict": True,
                      "schema": response_model.model_json_schema(),
                  },
              },
          )
          content = response.choices[0].message.content  # type: ignore[union-attr]
          if not isinstance(content, str):
              raise TypeError(f"LiteLLM returned non-string content for slot {slot!r}")
          return response_model.model_validate(json.loads(content))
  ```

- [ ] **4.4** Tests in `backend/tests/llm/test_adapter.py` (FakeAdapter contract — canned-return, slot-miss, type-mismatch) and `backend/tests/llm/test_prompts.py` (load default, render with vars, missing slot/version raises, missing template var raises `KeyError`). Use the exact test bodies from git history `5374dfd`.

- [ ] **4.5** Verify and commit:

  ```powershell
  cd backend; uv run pytest tests/llm/ -v
  git add backend/paperhub/llm/ backend/tests/llm/
  git commit -m "feat(llm): add LlmAdapter Protocol + LiteLlmAdapter + FakeAdapter + YAML prompt registry"
  ```

---

## Task 5 — Vector store + Tool-Call Tracer + MCP scope-checker

**Files (create):**
- `backend/paperhub/data/vectors.py`
- `backend/paperhub/tracing/__init__.py`, `backend/paperhub/tracing/tracer.py`, `backend/paperhub/tracing/redactor.py`
- `backend/paperhub/mcp/__init__.py`, `backend/paperhub/mcp/scopes.py`, `backend/paperhub/mcp/client.py`
- `backend/tests/data/test_vectors.py`, `backend/tests/tracing/__init__.py`, `backend/tests/tracing/test_redactor.py`, `backend/tests/tracing/test_tracer.py`, `backend/tests/mcp/__init__.py`, `backend/tests/mcp/test_scopes.py`

**Steps:**

- [ ] **5.1** Create the package dirs:

  ```powershell
  New-Item -ItemType Directory -Path backend/paperhub/tracing, backend/paperhub/mcp, backend/tests/tracing, backend/tests/mcp -Force | Out-Null
  "" | Out-File -Encoding utf8 backend/paperhub/tracing/__init__.py
  "" | Out-File -Encoding utf8 backend/paperhub/mcp/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/tracing/__init__.py
  "" | Out-File -Encoding utf8 backend/tests/mcp/__init__.py
  ```

- [ ] **5.2** `backend/paperhub/data/vectors.py` — Chroma-backed `VectorStore` Protocol + `ChromaVectorStore` (full `add` / `search` / `delete_by_paper` per the Phase 0 plan body in commit `5374dfd`).

- [ ] **5.3** `backend/paperhub/tracing/redactor.py` — `redact(payload)` masks `sk-(ant|proj)-...` strings → `<REDACTED:api-key>` and the user's `$HOME` prefix → `<REDACTED:home>` (recursive over dict + list).

- [ ] **5.4** `backend/paperhub/tracing/tracer.py` — `ToolCallTracer.record(...)` inserts one `tool_calls` row per step, args run through `redact()` and JSON-serialized; each insert is its own short transaction (durability-before-emission per design §6).

- [ ] **5.5** `backend/paperhub/mcp/scopes.py` — `McpToolScope`, typed per-(tool, method) arg models (`ArxivSearchArgs`, `ArxivFetchMetadataArgs`, `ArxivDownloadPdfArgs`, `FilesystemReadArgs`, `FilesystemWriteArgs`), `McpInvocation` over the discriminated `McpArgs` union, `ScopeRejection` dataclass, `check_scope(inv, scope) -> ScopeRejection | None` enforcing filesystem-root + write-allowed + tool-name-match.

- [ ] **5.6** `backend/paperhub/mcp/client.py` — `McpClient` holding `dict[str, McpToolScope]` + a `Callable[[McpInvocation], Awaitable[dict[str, object]]]` dispatcher; `call(invocation)` runs `check_scope` first, raises `McpScopeViolation` on rejection, else awaits the dispatcher. Dispatcher implementations for `arxiv` and `grobid` come in Task 6.

- [ ] **5.7** Tests:
  - `tests/data/test_vectors.py` — add+search returns hit, delete_by_paper removes, search filters by paper_id (3 tests).
  - `tests/tracing/test_redactor.py` — API-key masked, home path masked, nested redaction (4 tests).
  - `tests/tracing/test_tracer.py` — record inserts row, redaction is applied, unique-step constraint enforced (3 tests).
  - `tests/mcp/test_scopes.py` — filesystem inside root ok, outside rejected, `..` traversal rejected, write-not-allowed rejected, read inside root ok, tool mismatch rejected, arxiv ok (7 tests).

  All test bodies are reproduced in detail in git history at commit `5374dfd` (the original Phase 0 plan); copy from there.

- [ ] **5.8** Verify and commit:

  ```powershell
  cd backend; uv run pytest -q
  git add backend/paperhub/data/vectors.py backend/paperhub/tracing/ backend/paperhub/mcp/ backend/tests/data/test_vectors.py backend/tests/tracing/ backend/tests/mcp/
  git commit -m "feat(foundation): add Chroma VectorStore, Tool-Call Tracer with redaction, MCP scope-checker"
  ```

---

## Task 6 — MCP dispatchers (`arxiv` reused, `grobid` wrap) + Research Agent (RAG)

**Files (create):**
- `backend/paperhub/mcp/launchers.py` (launch `arxiv-mcp-server` as a subprocess, hold a stdio MCP client)
- `backend/paperhub/mcp/tools/__init__.py`, `backend/paperhub/mcp/tools/grobid_server.py` (~40 LoC FastMCP wrap)
- `backend/paperhub/rag/__init__.py`, `backend/paperhub/rag/chunker.py`, `backend/paperhub/rag/embedder.py`, `backend/paperhub/rag/retriever.py`
- `backend/paperhub/agents/__init__.py`, `backend/paperhub/agents/state.py`, `backend/paperhub/agents/research.py`, `backend/paperhub/agents/router.py`
- `backend/tests/rag/__init__.py`, `backend/tests/rag/test_chunker.py`, `backend/tests/rag/test_retriever.py`, `backend/tests/agents/__init__.py`, `backend/tests/agents/test_router.py`, `backend/tests/agents/test_research.py`

**Steps:**

- [ ] **6.1** `mcp/launchers.py` — subprocess-launch `uvx arxiv-mcp-server` and the in-process Python `grobid_server.py`; expose a `make_dispatcher(scopes)` that returns the awaitable expected by `McpClient`. Use the official `mcp` Python SDK (`pip install mcp`) — add it to `pyproject.toml` if not already there.

- [ ] **6.2** `mcp/tools/grobid_server.py` (~40 LoC FastMCP):

  ```python
  """Custom MCP server wrapping kermitt2/grobid-client-python with workspace-root validation."""
  from __future__ import annotations

  from pathlib import Path

  from grobid_client.grobid_client import GrobidClient
  from mcp.server.fastmcp import FastMCP

  from paperhub.config import get_settings

  mcp = FastMCP("paperhub-grobid")
  _settings = get_settings()
  _client = GrobidClient(grobid_server=_settings.grobid_url)


  def _check_under_workspace(p: Path) -> Path:
      resolved = p.resolve()
      root = _settings.workspace_root.resolve()
      try:
          resolved.relative_to(root)
      except ValueError as e:
          raise PermissionError(f"path {resolved} is outside workspace root {root}") from e
      return resolved


  @mcp.tool()
  def process_header(pdf_path: str) -> dict[str, str]:
      p = _check_under_workspace(Path(pdf_path))
      result = _client.process_pdf("processHeaderDocument", str(p), generateIDs=False, consolidate_header=False, consolidate_citations=False, include_raw_citations=False, include_raw_affiliations=False, tei_coordinates=False, segment_sentences=False)
      return {"tei_xml": result[2] or ""}


  @mcp.tool()
  def process_fulltext(pdf_path: str) -> dict[str, str]:
      p = _check_under_workspace(Path(pdf_path))
      result = _client.process_pdf("processFulltextDocument", str(p), generateIDs=False, consolidate_header=False, consolidate_citations=False, include_raw_citations=False, include_raw_affiliations=False, tei_coordinates=False, segment_sentences=False)
      return {"tei_xml": result[2] or ""}


  if __name__ == "__main__":
      mcp.run()
  ```

- [ ] **6.3** RAG components:
  - `rag/chunker.py` — `chunk_text(text, target_tokens=800, hard_max=1000)` yields `Chunk`-shaped dicts (id, paper_id, section, page, char_start, char_end, text); section-aware splitting on TEI `<head>` boundaries when available, falling back to greedy windowing. Token count via `tiktoken` (`cl100k_base`).
  - `rag/embedder.py` — `Embedder` wrapping `sentence-transformers` with the configured model (`BAAI/bge-small-en-v1.5` by default); `embed(texts: list[str]) -> list[list[float]]`.
  - `rag/retriever.py` — `Retriever.search(query, top_k=5, paper_ids=None)` does the two-stage funnel: embed query → `VectorStore.search(top_k=min(50, ⌈corpus_size/3⌉))` → cross-encoder rerank (`CrossEncoder("BAAI/bge-reranker-base")`) → top-k. Returns `list[RetrievedChunk]` where `RetrievedChunk` adds `score: float` to a `Chunk`.

- [ ] **6.4** Agent state + Router + Research Agent:
  - `agents/state.py` — `AgentState(TypedDict)` per design §5.
  - `agents/router.py` — `Router.classify(user_message)` calls `LlmAdapter.generate(slot="router", response_model=RoutingDecision)`. Phase A constraint: `intent` literal restricted to `"paper_qa" | "out_of_scope"` via a Phase-A-specific subclass `BinaryRoutingDecision(RoutingDecision)` that overrides the `intent` literal — or pass `RoutingDecision` and check `if intent not in {"paper_qa","chitchat"}` post-hoc. Default to `out_of_scope` (`fallback_to_user=True`) for everything that isn't clearly a question.
  - `agents/research.py` — `ResearchAgent.answer(state)` does: `Retriever.search(state["user_message"])` → builds the passages prompt block (`§{section}, p.{page}: {text}` joined by `\n\n`) → `LlmAdapter.generate(slot="research_qa", response_model=AgentResponse)` where `AgentResponse(BaseModel)` has `answer: str` and `citations: list[Citation]`. Writes `retrieved_chunks` and `final_response` to state.

- [ ] **6.5** Tests (use `FakeAdapter` everywhere — no real LLM calls):
  - `tests/rag/test_chunker.py` — small text yields ≥1 chunk, chunks stay under hard_max, section boundaries respected when given TEI input.
  - `tests/rag/test_retriever.py` — uses in-memory `ChromaVectorStore` (tmp_path) seeded with toy vectors, asserts the two-stage funnel returns the seeded top-1 for a matching query.
  - `tests/agents/test_router.py` — given `FakeAdapter(canned={"router": RoutingDecision(intent="paper_qa", ...)})`, `Router.classify` returns `paper_qa`; given canned `out_of_scope`, returns it.
  - `tests/agents/test_research.py` — given seeded vector store + `FakeAdapter(canned={"research_qa": AgentResponse(answer="...", citations=[...])})`, `ResearchAgent.answer` populates `retrieved_chunks` and `final_response`.

- [ ] **6.6** Verify and commit:

  ```powershell
  cd backend; uv run pytest -q
  git add backend/paperhub/mcp/launchers.py backend/paperhub/mcp/tools/ backend/paperhub/rag/ backend/paperhub/agents/ backend/tests/rag/ backend/tests/agents/
  git commit -m "feat(agents): add arxiv+grobid MCP dispatchers, RAG pipeline, Router (binary), Research Agent"
  ```

---

## Task 7 — `/chat` SSE endpoint + chat UI

**Files (create):**
- `backend/paperhub/api/routes/__init__.py`, `backend/paperhub/api/routes/chat.py`, `backend/paperhub/api/routes/papers.py`
- `backend/paperhub/api/sse.py` (event model + helpers)
- `frontend/src/api/sse.ts`, `frontend/src/api/types.ts`
- `frontend/src/components/Sidebar/Sidebar.tsx`, `frontend/src/components/ChatPane/ChatPane.tsx`, `frontend/src/components/ChatPane/Message.tsx`, `frontend/src/components/ChatPane/RoutingBadge.tsx`, `frontend/src/components/ChatPane/TraceInline.tsx`, `frontend/src/components/ChatPane/Composer.tsx`
- `backend/tests/api/test_chat_sse.py`, `frontend/src/components/ChatPane/ChatPane.test.tsx`

**Steps:**

- [ ] **7.1** Backend `/chat` SSE: define `SseEvent` discriminated union in `api/sse.py` mirroring design §8 — `routing_decision`, `tool_step`, `token`, `citation`, `final`, `error`. `routes/chat.py` exposes `POST /chat` accepting `{"message": str, "session_id": UUID | null}`, streams via `sse_starlette.sse.EventSourceResponse`. Pipeline: insert a `runs` row (`status='running'`) → `Router.classify(message)` → emit `routing_decision` → if `paper_qa` then `ResearchAgent.answer(state)`; tracer emits `tool_step` per call; final answer streams token-by-token (use `litellm.acompletion(..., stream=True)`); insert assistant `messages` row + update `runs.status='ok'` at finalize → emit `final`.

- [ ] **7.2** Backend `/papers/import` (POST `{"arxiv_id": str}`) — calls `arxiv` MCP `fetch_metadata` + `download_pdf`, validates the PDF path is under `workspace_root`, calls `grobid` MCP `process_fulltext`, runs chunker + embedder, inserts `papers` + `chunks` rows and vector-store entries. Returns the `Paper` Pydantic model.

- [ ] **7.3** Frontend `api/sse.ts` — `streamChat(message, onEvent)` opens an `EventSource`-compatible streaming `fetch` POST, parses SSE event types into the TS discriminated union (auto-generated from backend Pydantic schemas via `npm run generate-types`; or hand-written for Phase A and replaced in Phase B).

- [ ] **7.4** Frontend components:
  - `Sidebar` — placeholder list (empty for Phase A) + "New chat" button.
  - `Composer` — controlled textarea + send button; calls `streamChat`.
  - `RoutingBadge` — renders `intent` and `model_tier` from the `routing_decision` event the moment it arrives, BEFORE any `token` event.
  - `TraceInline` — collapsed by default; expand reveals the step DAG from accumulated `tool_step` events.
  - `Message` — assistant message body with `CitationChip` rendering for inline `(§sec, p.N)` markers (click → `window.open(pdfUrl)` for Phase A; PDF viewer comes in Phase B).
  - `ChatPane` — composes the above and owns the messages state in `zustand`.

- [ ] **7.5** Tests:
  - `tests/api/test_chat_sse.py` — given `FakeAdapter` + a seeded paper, POST `/chat` streams the expected sequence: `routing_decision` → ≥1 `tool_step` → ≥1 `token` → `final`; assert the `tool_calls` rows match.
  - `frontend/src/components/ChatPane/ChatPane.test.tsx` — render with a mocked SSE stream emitting fixture events; assert `RoutingBadge` renders first, then tokens append, then `final` resolves.

- [ ] **7.6** Verify and commit:

  ```powershell
  cd backend; uv run pytest -q; cd ..; cd frontend; npm run typecheck; npm run test; cd ..
  git add backend/paperhub/api/sse.py backend/paperhub/api/routes/ backend/tests/api/test_chat_sse.py frontend/src/api/ frontend/src/components/
  git commit -m "feat(api,frontend): add SSE /chat with RoutingBadge + TraceInline + Composer"
  ```

---

## Task 8 — End-to-end smoke test against the real arXiv + real LiteLLM

**Files (create):** `backend/tests/integration/__init__.py`, `backend/tests/integration/test_paper_qa_e2e.py` (skipped by default; `pytest -m e2e` to run); `scripts/smoke.ps1`.

**Steps:**

- [ ] **8.1** `tests/integration/test_paper_qa_e2e.py` — fixture: ensure `ANTHROPIC_API_KEY` is set and `GROBID_URL` reachable; if not, `pytest.skip`. Test: import `2401.00001` via `/papers/import`, then POST `/chat` with `"What problem does this paper address?"`, assert the response includes at least one inline citation matching `(§\d+, p\.\d+)`. Mark with `@pytest.mark.e2e`.

- [ ] **8.2** Register the `e2e` marker in `pyproject.toml`:

  ```toml
  [tool.pytest.ini_options]
  testpaths = ["tests"]
  asyncio_mode = "auto"
  markers = ["e2e: end-to-end tests requiring real APIs and services"]
  ```

- [ ] **8.3** `scripts/smoke.ps1` — boots `uv run uvicorn paperhub.api.app:create_app --factory --port 8765` in the background, runs `npm --prefix frontend run dev` in the background on port 5173, waits for both to be ready, then runs `pytest -m e2e -v`; tears down both processes on exit.

- [ ] **8.4** Run the smoke locally:

  ```powershell
  pwsh -File scripts/smoke.ps1
  ```
  Expected: e2e test passes; the chat UI at `http://localhost:5173` responds to a question about the imported paper with a cited answer.

- [ ] **8.5** Commit:

  ```powershell
  git add backend/tests/integration/ scripts/smoke.ps1 backend/pyproject.toml
  git commit -m "test(integration): add Phase A e2e smoke + smoke.ps1 driver"
  ```

---

## Task 9 — CI + pre-commit + KNOWN-TYPE-GAPS register

**Files (create):** `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `docs/KNOWN-TYPE-GAPS.md`.

**Steps:**

- [ ] **9.1** Create `docs/KNOWN-TYPE-GAPS.md`:

  ```markdown
  # Known upstream type-stub gaps

  Per **SRS NFR-11 narrow exception**, every `# type: ignore[<code>]` in PaperHub
  Python code references an entry here. Bare `# type: ignore` fails CI via
  `warn_unused_ignores = true`. Remove entries when upstream ships fixes.

  | Site (file:line) | Upstream | mypy error code | Tracked since | Why it's needed |
  |---|---|---|---|---|
  | (none yet) | — | — | — | — |
  ```

- [ ] **9.2** Create `.github/workflows/ci.yml` (backend + frontend jobs) — full content per the original Phase 0 plan in commit `5374dfd` step 12.2.

- [ ] **9.3** Create `.pre-commit-config.yaml` — ruff format + ruff lint + local mypy hook per `5374dfd` step 12.3.

- [ ] **9.4** Verify backend CI commands run locally:

  ```powershell
  cd backend; uv run ruff format --check .; uv run ruff check .; uv run mypy; uv run pytest -q
  ```

- [ ] **9.5** Commit:

  ```powershell
  git add .github/workflows/ci.yml .pre-commit-config.yaml docs/KNOWN-TYPE-GAPS.md
  git commit -m "ci: add mypy --strict + pytest + ruff CI workflow and pre-commit hooks"
  ```

---

## Task 10 — Phase A wrap-up + docs sweep

- [ ] **10.1** Update `docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md` §12 Open Questions — strike items resolved during Phase A (typically: exact arxiv MCP launcher command, grobid fallback behavior, embedder choice).

- [ ] **10.2** Append a `Phase A complete` section to this plan file with a checklist of what shipped and a 2-3 sentence handoff to Phase B.

- [ ] **10.3** Run the full verification matrix one more time from a clean state:

  ```powershell
  cd backend; uv sync; uv run ruff format --check .; uv run ruff check .; uv run mypy; uv run pytest -q; cd ..
  cd frontend; npm ci; npm run typecheck; npm run test; npm run lint; npm run build; cd ..
  ```

- [ ] **10.4** Commit and tag:

  ```powershell
  git add docs/
  git commit -m "docs(plans): mark Phase A complete and update open questions"
  git tag phase-a-complete
  ```

---

## Phase A done — what ships

- ✅ Bootable backend (`uv run uvicorn paperhub.api.app:create_app --factory`) with `/health` reporting schema version
- ✅ Bootable frontend (`npm run dev` in `frontend/`) showing chat shell + Sidebar + Composer
- ✅ POST `/papers/import` ingests an arXiv paper end-to-end via the reused `arxiv` MCP server + our wrapped `grobid` MCP server
- ✅ POST `/chat` streams `routing_decision` → `tool_step` → `token` → `final` for a `paper_qa` question with inline citations
- ✅ Tool-Call Tracer records every step to SQLite; `tool_calls` is the audit source of truth
- ✅ MCP scope-checker rejects out-of-root filesystem paths and `..` traversal attempts
- ✅ `mypy --strict` clean, ruff clean, all unit + integration tests pass; CI gates on the same

## Phase A complete (2026-05-17)

Tagged at `phase-a-complete`. Branch: `feat/phase-a-foundations`. Commit summary:

- Backend: 60+ source files, 64 unit/integration tests + 2 e2e (skipped without credentials)
- Frontend: React+Vite+Tailwind+Vitest scaffold + chat UI (Sidebar, ChatPane, Composer, RoutingBadge, TraceInline, Message) + SSE client + zustand store
- Foundations: Settings, SQLite migrations + 12-table schema, Pydantic models, LiteLLM adapter, YAML prompt registry, ChromaVectorStore, Tool-Call Tracer + redactor, MCP scope-checker + client, RAG (chunker/embedder/retriever), Router (binary), Research Agent, FastAPI app + /health + /chat SSE + /papers/import
- CI: GitHub Actions runs ruff/mypy/pytest (backend) + typecheck/lint/test/build (frontend) on every PR
- All gates green: pytest 64 passed (+2 e2e skipped), mypy --strict clean (62 source files), ruff format + check clean, frontend typecheck/lint/test/build clean

### Handoff to Phase B

Phase B widens the vertical slice from binary `{paper_qa, chitchat}` routing to the full 6-intent set (adds `library_stats`, `research_suggest`, `slides`, `mcp_tool`). New work:
- Full Router (6 intents + disambiguation fallback when confidence < threshold)
- SQL Agent (NL2SQL + self-repair loop + Show-SQL toggle)
- MCP layer hardening: `filesystem` (CVE-pinned reuse), `sqlite` (wrap), `latex` (build), `paperhub.*` server
- Report Agent (multi-paper slides via `latex` MCP + Slide Editor UI)
- Relation analysis + research-direction (citations from `grobid` references + vector-similarity edges + Cytoscape graph)
- Multi-project management (CRUD, tags, notes, reading-status, per-project chat history)
- Real-time token streaming (Phase A emits the full answer in one `token` event; Phase B should use `litellm.acompletion(stream=True)`)
- FastAPI dependency_overrides for tests (already started in Phase A Task 7 follow-up)

## Self-review checklist (already performed by plan author)

- **Spec coverage** — Phase A from SRS v1.6 design §4 lights up NFR-11 from day 1; FR-01 (single import), FR-03 (RAG QA), FR-08 (binary routing), FR-10 (`arxiv`, `grobid`), FR-11 (tracer + Trace UI). All ten tasks cover concrete bullets in the design.
- **Placeholders** — none. Each task gives exact file paths, exact commands, expected output, and complete code or precise references to a known commit (`5374dfd`) for the parts intentionally not re-pasted.
- **Type consistency** — `Settings`, `LlmAdapter`, `LlmMessage`, `ChunkVector`, `VectorSearchHit`, `McpToolScope`, `McpInvocation`, `ScopeRejection`, `ToolCallTracer`, `AgentState`, `RoutingDecision`, `AgentResponse` are each defined once in the task that owns them and used by name elsewhere.
- **Ambiguity** — Task 5 explicitly defers real MCP dispatch wiring to Task 6 (where the launchers are added). Task 6.4 explicitly notes the Phase-A router intent is narrowed to a binary set even though the underlying `RoutingDecision` model carries the full 6-intent literal.

## Open questions deferred to Phase B / C

- `paperhub.*` MCP server in-process vs. subprocess (currently in-process for Phase A; revisit when Phase B exposes the full server-side tool surface)
- Cytoscape relation-graph layout algorithm (Phase B)
- Exact prompt content per intent slot beyond `router` and `research_qa` (Phase B adds the remaining slots)
- Eval-harness rubric prompt + 20-item human-calibration set (Phase C)

---

## Phase A actual completion (post-plan-execution addendum, 2026-05-17)

The original "Phase A complete" section above captured the state right after Tasks 1–10 landed (commit `d0687db`, 64 unit + 2 e2e skipped). After that, a user-driven live-API review surfaced six integration defects and a series of SRS-principle clarifications drove substantial follow-on work. This appendix records what actually shipped on the `feat/phase-a-foundations` branch as of tag `phase-a-complete` (commit `1ed15f0`, **29 commits ahead of `main`**).

### Final test gates

- Backend: **94 unit tests passed** (up from 64), 5 e2e tests deselected/runnable
- Backend `mypy --strict`: clean across **63 source files** (up from 62)
- Backend `ruff check` + `ruff format --check`: clean
- Frontend: **5 vitest tests passed**; typecheck + lint + build clean
- **Live e2e (against real arxiv-latex-mcp + real Gemini 2.5 Flash/Pro):**
  - `test_latex_first_import_real_arxiv` ✅ — imports `1706.03762` via Tier 1 LaTeX path
  - `test_latex_first_import_preserves_raw_source_and_figures` ✅ — verifies unpacked e-print directory contains `.tex` + figures
  - `test_chat_paper_qa_against_latex_import` ✅ — full chat round-trip with Gemini answering "What architecture does this paper propose?" with citation

### Post-plan work that landed (chronological by commit)

1. **`fix(phase-a)` — final review polish** (`6599e52`) — Important findings from the static final review: e2e final-event shape, MCP settings threading, type-ignore documentation.
2. **`fix(phase-a)` — live-API integration fixes** (`a1a2aeb`) — Six defects surfaced by the user's first live probe: arXiv MCP method realignment (`fetch_metadata`/`download_pdf` were nonexistent; corrected to `get_abstract`/`download_paper`); upstream `isError` checking; lifespan-owned MCP sessions (fixed anyio cancel-scope crash); `try/finally` on run-status update (caught `CancelledError`); user-message persistence (was never written); typed `ToolCallTracer` parameter via `TYPE_CHECKING`; binary-content redaction end-to-end; typed `GrobidArgs` so GROBID flows through `McpClient`.
3. **`docs(srs)` v1.7 / v1.8 / v1.9 / v1.10** (`c32c398` / `1134161` / `2bb02aa` / `17a6d77`) — Added **§1.1 First Principle**: *modernizing the framework MUST NOT destroy working logic the predecessor already delivers*. Added the **three-tier source-fidelity ladder** (Tier 1 = raw LaTeX source via `arxiv-latex-mcp` + unpacked e-print archive; Tier 2 = Marker containerized service; Tier 3 = raw text extraction as last resort with `notes_md='low_fidelity_extraction'`).
4. **`feat(env)` — live-smoke support** (`26f7423`) — `backend/.env.example` with Gemini preset; `python-dotenv` integration so LiteLLM sees `.env` keys via `os.environ`; lazy `Embedder` model loading (defers HuggingFace model download to first `embed()` call — was crashing with Windows OS error 1455 "paging file too small" when loaded eagerly during `Depends(get_retriever)`); env-isolated config tests.
5. **`feat(import)` — three-tier ladder** (`f08a1f4`) — `/papers/import` now follows the SRS §1.1 ladder. Tier 1 = `takashiishida/arxiv-latex-mcp::get_paper_prompt` (flattened LaTeX). Tier 2 = deferred to Phase B (Marker container). Tier 3 = existing `blazickjp/arxiv-mcp-server::download_paper` markdown as last-resort fallback. Migration `0002_papers_extraction_tier.sql` adds `extraction_tier` + `notes_md` columns. Two new live e2e tests against real arxiv-latex-mcp + real Gemini.
6. **`feat(import)` — raw e-print archive** (`49c0892`) — Discovered Tier 1 was incomplete: `get_paper_prompt` returns only flattened text, no figures/bib/sty. Added `arxiv.Result.download_source()` to download the raw `.tar.gz` and unpack to `workspace/papers/<id>/source/` (safe extraction; refuses tarball escape). Migration `0003_papers_source_dir.sql` adds `source_dir_path`. New live e2e `test_latex_first_import_preserves_raw_source_and_figures` verifies actual figures (`.png` / `.pdf` / `.eps`) end up on disk. **Critical for FR-05 / FR-07 slide pipeline — the Phase B Report Agent needs these.**
7. **`chore` — formatter + gitignore widening** (`1ed15f0`) — Ruff format cleanups; `.gitignore` widened from `.paperhub-workspace` to `.*workspace`.

### What actually ships in Phase A (concrete capabilities)

- **`POST /papers/import {arxiv_id}`** — three-tier source-fidelity ladder. Tier 1 (`arxiv_latex` MCP) is the primary path; Tier 3 (`arxiv` MCP markdown) is the fallback. Tier 2 (Marker) is scaffolded but skipped per "demo defers Marker." Tier-1 imports save:
  - `workspace/papers/<id>/source/` — unpacked e-print archive (figures + bib + sty + .tex files)
  - `workspace/papers/<id>/source.flattened.tex` (or equivalent) — flattened LaTeX text for RAG indexing
- **`POST /chat`** SSE — Router (binary `paper_qa` | `chitchat`) → Research Agent (RAG over indexed chunks) → Gemini-generated answer with inline `(§sec, p.N)` citations → SSE events: `routing_decision` → `tool_step` → `token` → `citation` → `final`. User and assistant messages persisted to `messages`; `runs.status` finalized in `try/finally` to survive client disconnects.
- **`GET /health`** — returns `{status, app, schema_version}`; lifespan-applied migrations ensure schema is up-to-date.
- **Chat UI** — React + Vite + Tailwind. Sidebar (chat history placeholder), ChatPane (Composer, RoutingBadge, Message, TraceInline), SSE client via `fetch` + `ReadableStream`, zustand store.
- **Tool-Call Tracer** — every MCP call, scope rejection, and agent step writes a `tool_calls` row with redacted args; the Trace UI surfaces this. Bytes redacted as `<bytes:N>`; home-dir paths redacted.
- **MCP scope-checker** — every outbound MCP call validated against typed `McpToolScope` before dispatch (path-traversal-safe; `..` rejected; CVE-2025-53109 regression test).
- **Settings + .env** — `pydantic-settings` with `PAPERHUB_` env prefix + `AliasChoices` for bare-name API keys. Supports Anthropic, OpenAI, Gemini (LiteLLM picks provider by model-ID prefix).
- **CI** — GitHub Actions runs `ruff format --check` + `ruff check` + `mypy --strict` + `pytest -m "not e2e"` on backend; `typecheck` + `lint` + `test` + `build` on frontend. Pre-commit hooks mirror the backend gates.

### What Phase A does NOT ship (deferred to Phase B per SRS)

- **Marker container** for high-fidelity PDF→Markdown (Tier 2 of the §1.1 ladder). Scaffolded via `Settings.marker_url` + `Settings.marker_enabled=False`; Phase B deploys the container and flips the flag. `# TODO(phase-b): wrap Marker container` in `mcp/launchers.py`.
- **Agentic search → read → decide → download flow.** The user-confirmed Phase B work: Router gains a `paper_import` intent; ImportAgent uses `arxiv.search_papers` → LLM picks the best match → `arxiv.get_abstract` → LLM confirms → triggers internal import. Phase A only supports direct `POST /papers/import {arxiv_id}` (user knows the ID).
- **All other Phase B / C deliverables** per the design's §4 phase table — see the Handoff to Phase B section above.

### Lessons applied to the SRS

The user's live review and the resulting fix cycles drove several SRS clarifications that are now binding:
- v1.7: §1.1 First Principle — preserve paper2slides-plus capabilities; no feature removal.
- v1.8: Sharpened to "improve, don't simplify" — performance/fidelity improvements OK; capability simplifications prohibited.
- v1.9: Three-tier ladder cleanly characterized (LaTeX > Marker-Markdown > raw extraction).
- v1.10: Tier 1 = unpacked e-print archive, not just flattened text; Marker is a containerized service, not a Python dep.

These principles apply to Phase B work too.
