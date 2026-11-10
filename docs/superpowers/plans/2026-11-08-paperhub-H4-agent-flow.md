# H4 — Agent chat flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the router → fixed-pipeline chat flow with one tool-calling core agent (FR-21) that reads, searches, queries, recalls and acts over multiple steps with the whole conversation in view, built and measured in isolation from v2.41.0.

**Architecture:** A new `agents/core/` package holds a tool registry (existing capabilities wrapped, not rewritten), a precise-context builder and the agent loop. `POST /chat` on this branch drives the loop instead of `router_node` + intent dispatch; SSE events, tracing and persistence stay on today's channels. Everything runs in the `D:/GitHub/PaperHub-h4` worktree against a copied workspace on its own port; v2.41.0 keeps running from `D:/GitHub/PaperHub` (`main`).

**Tech Stack:** Python 3.11 / FastAPI / LangGraph (only where wrapped subgraphs need it) / LiteLLM tool calling / SQLite FTS5 / React + TypeScript + Vitest / the existing `backend/benchmark/` harness.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` — §III-10.4 (authoritative design), UC-11, FR-21, and the H4 notes in FR-01, FR-06, acceptance #1, §III-1, §III-3, §III-4, §III-5.2.

## Global Constraints

- **Isolation (spec §III-10.4, "must not affect the current version"):** all H4 work happens in the worktree `D:/GitHub/PaperHub-h4` (branch `feat/plan-h4-agent-mode`). Never edit, run migrations against, or restart anything from `D:/GitHub/PaperHub` or its `backend/workspace/`. The user's v2.41 backend on `:8000` and frontend on `:5173` are never touched.
- **H4 instance:** backend on **`:8100`**, frontend dev server on **`:4173`** (already in the CORS allow list), workspace **`D:/paperhub-h4-workspace`** (a path-rewritten copy, Task 1), its own MCP config (`PAPERHUB_MCP_CONFIG`), `PAPERHUB_MARKER_WORKER=0`, `PAPERHUB_ARXIV_BACKFILL=0`, `PAPERHUB_CITATION_EDGES=0`.
- **Backups:** before any H4 migration touches a workspace, back it up with the SQLite backup API (never a plain file copy — the DB has WAL files).
- **Migrations are additive only** (new columns / tables / triggers); new `runs` columns go **after** the runs-status rebuild in `db/migrate.py` (L659-695).
- **Record principle:** every agent loop step and every tool is a `Tracer` step recording enough state to reconstruct it; ids for library content, **full payloads for anything not re-fetchable** (web search results, SQL rows, gather selections, auto-approve decisions); never the rendered prompt. One `Tracer` per run.
- **Prompts:** every new prompt (`core_agent`, tool descriptions, `agent_gather`, `agent_autoapprove`) is authored by an agent that first invokes the `writing-agent-prompts` skill; ≥2 variants × the scenario + regression set × judged, on the **deploy models** resolved from settings (flagship tier for the core agent, small tier for gather and auto-approve).
- **Models:** core agent = `PAPERHUB_AGENT_MODEL` (default: the flagship tier); gather = `PAPERHUB_AGENT_GATHER_MODEL` (default: small tier); auto-approve = `PAPERHUB_AGENT_AUTOAPPROVE_MODEL` (default: small tier). Each new env var gets a one-line entry in `backend/.env.example` section 3 or 4 in the same commit, plus a `SettingField` and i18n label in all four locales.
- **Frontend:** every new string through `t()` in `en`, `zh-TW`, `zh-CN`, `ja`; everything that appears or disappears animates (enter **and** exit), respecting `prefers-reduced-motion`.
- **TDD**, Conventional Commits, `uv` only, commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push, merge or tag.
- **Merge gate (thresholds fixed here, before any prompt iteration — spec §III-10.4):**
  - **G-a per scenario:** each of the 8 scenarios has 3 cases; a scenario passes when **≥ 2 of 3** cases score 1 from the judge (correct, grounded, on the thread); all 8 must pass, and overall **≥ 20 / 24**.
  - **G-b no shallower:** on the cases v2.41 can attempt (scenarios 1, 2, 3, 6, 7, 8), the pairwise judge prefers H4 or ties in **≥ 70 %** of comparisons (both orders).
  - **G-c no regression:** on `benchmark/cases.example.toml`, H4's pass count is **≥ v2.41's** pass count in the same run.
  - **G-d simple turns:** median wall time of the chitchat and library-count cases is **≤ 2×** v2.41's.

## Review Focus

1. **A follow-up that only makes sense with the history** ("more detail on that", "what about the second paper?") — the agent must use the conversation, not answer generically (run 892). Pinned in Task 9 (context builder test) and scenario 1 cases (Task 14).
2. **A turn that hits the step/token budget** — it must still answer with what it has and say what it did not get to, never end silently or loop. Pinned in Task 9.
3. **The model citing a chunk it never read** (invented or from another paper) — invalid `[chunk:N]` markers are removed before the answer is saved and the removal is traced. Pinned in Task 9.
4. **Stop pressed mid-loop** (during a long `gather` or deck build) — the run ends `cancelled`, partial messages are retracted like today, no orphan background task keeps writing. Pinned in Task 10.
5. **A chat with no enabled papers** asking a paper question — the agent says so and offers `search_papers`, instead of calling read tools with nothing to read. Pinned in Task 4 (tools return a clear empty result) and Task 9.

---

## File Structure

**Backend (new)**
- `backend/src/paperhub/agents/core/__init__.py`
- `agents/core/tools.py` — `ToolSpec`, `ToolContext`, `ToolOutcome`, `ToolRegistry`, permission `Tier`.
- `agents/core/read_tools.py` — `list_sections`, `read_section`, `search_chunks`, `read_chunks`, `list_figures_tables`, `read_layout_object`.
- `agents/core/gather.py` — the `gather` tool (small-model subagent).
- `agents/core/action_tools.py` — `query_library`, `search_papers`, `add_paper`, `remember`, `forget`, `remove_paper`, `create_deck`, `edit_slides`, `write_notes`, `list_ideas`, `propose_ideas`.
- `agents/core/context.py` — the precise-context builder (session state, conversation with work records, compaction, token accounting).
- `agents/core/permissions.py` — tier policy, pending confirmations, auto-approve classifier call.
- `agents/core/agent.py` — the loop (`run_core_agent`).
- `agents/core/citations.py` — citation validation.
- `api/actions.py` — `POST /chat/actions/{action_id}/confirm` and `/dismiss`.
- `cli/copy_workspace.py` — `paperhub-copy-workspace`.
- `llm/prompts/core_agent_v1.yaml`, `agent_gather_v1.yaml`, `agent_autoapprove_v1.yaml`.

**Backend (modified)**
- `llm/litellm_adapter.py` (+ `llm/adapter.py` Protocol) — `tool_turn`.
- `db/migrate.py`, `db/schema.sql` — `runs.mode`, `runs.agent_record_json`, `runs.search_results_json` list semantics, `chat_sessions.auto_approve`, `chunks_fts` (+ triggers + backfill), `pending_actions` table.
- `api/chat.py` — `run_agent` drives `run_core_agent`; new SSE events.
- `api/sessions.py` — replay of `agent_summary`; `PATCH /sessions/{id}` `auto_approve`.
- `agents/report_graph.py` — `forced_command` override hook.
- `config.py`, `settings_registry.py`, `backend/.env.example`, `backend/pyproject.toml` (script entry).
- `feedback/models.py`, `feedback/signals.py`, `feedback/review.py`, `feedback/export.py` — agent-turn stages and trajectories.
- `backend/scripts/start.ps1` (`-Workspace`, `-McpConfig`), `backend/scripts/run-benchmark.ps1` (`-BaseUrl`, `-DbPath`).
- `backend/benchmark/{config,driver,runner,judge}.py` — scenario cases with turns, thread-continuity criterion, pairwise version comparison.

**Frontend**
- `components/chat/AgentSummaryBadge.tsx` (new), `components/chat/ConfirmActionCard.tsx` (new), `components/chat/AutoApproveToggle.tsx` (new), `components/chat/ProposalButtons.tsx` (new).
- `components/chat/ChatThread.tsx`, `MessageBubble.tsx`, `TraceInline.tsx`, `hooks/useChatStream.ts`, `store/chat.ts`, `types/domain.ts`, `lib/api.ts`, `pages/ChatPage.tsx`, `locales/*/chat.json`, `locales/*/settings.json`.

---

### Task 1: Isolated H4 environment (workspace copy, start/benchmark parameters, runbook)

**Files:**
- Create: `backend/src/paperhub/cli/copy_workspace.py`, `backend/tests/test_copy_workspace.py`, `docs/superpowers/plans/h4-runbook.md`
- Modify: `backend/pyproject.toml` (`paperhub-copy-workspace = "paperhub.cli.copy_workspace:main"`), `backend/scripts/start.ps1`, `backend/scripts/run-benchmark.ps1`

**Interfaces:**
- Produces: `copy_workspace(src: Path, dest: Path) -> CopyReport(db_rows_rewritten: int, files_copied: int)`; CLI `paperhub-copy-workspace --src <ws> --dest <ws> [--force]`; `start.ps1 -Workspace <path> -McpConfig <toml>`; `run-benchmark.ps1 -BaseUrl <url> -DbPath <path>`.

- [ ] **Step 1: Failing tests** in `test_copy_workspace.py`:
  - `test_copy_uses_backup_api_and_keeps_wal_rows` — a source DB in WAL mode with an uncheckpointed insert; after `copy_workspace`, the dest DB contains the row.
  - `test_copy_rewrites_absolute_paths` — rows in `paper_content.source_path/source_dir_path/html_path` and `decks.tex_path/pdf_path` that start with the source workspace path now start with the dest path; other rows unchanged; `CopyReport.db_rows_rewritten` equals the number changed.
  - `test_copy_refuses_existing_dest_without_force` — raises `FileExistsError`.
- [ ] **Step 2:** `uv run pytest tests/test_copy_workspace.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `copy_workspace` (SQLite `backup()` into the dest DB; `shutil.copytree` for `papers_cache`, `exports`; skip `logs/`; path rewrite with `UPDATE … SET col = replace(col, ?, ?) WHERE col LIKE ?` for both `\` and `/` spellings) and `main()`.
- [ ] **Step 4:** tests PASS; `uv run ruff check` / `mypy src` clean.
- [ ] **Step 5:** `start.ps1`: optional `-Workspace` (sets `$env:PAPERHUB_WORKSPACE`) and `-McpConfig` (sets `$env:PAPERHUB_MCP_CONFIG`); `run-benchmark.ps1`: optional `-BaseUrl` (default `http://127.0.0.1:8000`, used for the health check and passed through) and `-DbPath`. Defaults keep today's behaviour exactly.
- [ ] **Step 6: Runbook** `docs/superpowers/plans/h4-runbook.md` with the exact commands:
  - Back up v2.41's workspace: `uv run paperhub-copy-workspace --src D:/GitHub/PaperHub/backend/workspace --dest D:/paperhub-backups/v2.41-<date>` (from the H4 worktree's `backend/`).
  - Make the H4 workspace: same command with `--dest D:/paperhub-h4-workspace`.
  - H4 MCP config: copy `D:/GitHub/PaperHub/backend/mcp_servers.toml` to `D:/paperhub-h4-workspace/mcp_servers.toml` with `papers`/`sql`/`memory` URLs on `:8100`; copy `backend/.env` into the worktree.
  - Start H4: `cd D:/GitHub/PaperHub-h4/backend; $env:PAPERHUB_MARKER_WORKER='0'; $env:PAPERHUB_ARXIV_BACKFILL='0'; $env:PAPERHUB_CITATION_EDGES='0'; .\scripts\start.ps1 -BackendPort 8100 -Workspace D:/paperhub-h4-workspace -McpConfig D:/paperhub-h4-workspace/mcp_servers.toml -NoWebSearch`; frontend: `cd D:/GitHub/PaperHub-h4/frontend; $env:VITE_API_BASE_URL='http://localhost:8100'; npx vite --port 4173 --strictPort`.
  - Start / demo v2.41: unchanged (`D:/GitHub/PaperHub`, `:8000` / `:5173`).
  - Restore v2.41 data from a backup: stop v2.41, `paperhub-copy-workspace --src <backup> --dest D:/GitHub/PaperHub/backend/workspace --force`.
  - Before every H4 migration run: back up `D:/paperhub-h4-workspace` the same way.
- [ ] **Step 7: Execute the runbook once** (backup, H4 workspace, `uv sync` + `npm ci` in the worktree, start H4 on `:8100`/`:4173`), check `GET :8100/health` and that `GET :8000/health` is unaffected; stop H4. Commit (`feat(ops): copy a workspace safely and start an isolated instance`).

### Task 2: Schema for the agent flow (additive)

**Files:** Modify `backend/src/paperhub/db/schema.sql`, `db/migrate.py`; Test `backend/tests/test_migrate_agent.py`

**Interfaces:**
- Produces: `runs.mode TEXT NOT NULL DEFAULT 'pipeline'` (`'pipeline' | 'agent'`); `runs.agent_record_json TEXT` (the turn's work record: `{"tools": [names], "read": [chunk ids], "cited": [chunk ids], "papers": [paper_content ids], "steps": n}`); `chat_sessions.auto_approve INTEGER NOT NULL DEFAULT 0`; table `pending_actions(id TEXT PK, run_id INT FK runs ON DELETE CASCADE, session_id INT, tool TEXT, args_json TEXT, status TEXT CHECK(status IN ('pending','done','dismissed','failed')) DEFAULT 'pending', result_json TEXT, created_at)`; FTS5 `chunks_fts(text, content='chunks', content_rowid='id')` with insert/delete/update triggers, backfilled when empty. `runs.search_results_json` keeps its column but the agent writes a JSON **list of card lists** under key `{"batches": [...]}`; readers accept both shapes.

- [ ] **Step 1: Failing tests:** `test_agent_columns_added_after_runs_rebuild` (fresh DB and a DB at the pre-H4 schema both end with the columns; running `apply_schema` twice is a no-op); `test_chunks_fts_backfilled_and_kept_in_sync` (existing chunks searchable via `chunks_fts MATCH`; insert/delete of a chunk updates it); `test_v241_reads_migrated_db` (a migrated DB still serves v2.41's `SELECT`s on `runs`/`chat_sessions` — insert with only the v2.41 columns works); `test_search_results_reader_accepts_both_shapes`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** with the `PRAGMA table_info` / `sqlite_master` patterns, placed after L695; the reader helper `load_search_results(raw) -> list[list[dict]]` in `api/sessions.py`.
- [ ] **Step 4:** PASS; ruff/mypy. **Step 5:** back up the H4 workspace, start H4 once so the migration runs on `D:/paperhub-h4-workspace`, check the FTS row count equals the `chunks` count; commit `feat(db): add the agent-flow schema`.

### Task 3: Tool-calling turn in the LLM adapter

**Files:** Modify `llm/adapter.py`, `llm/litellm_adapter.py`; Test `tests/test_litellm_tool_turn.py`

**Interfaces:**
- Produces: `ToolCall(id: str, name: str, arguments: dict[str, Any])`; `ToolTurn(content: str, tool_calls: list[ToolCall], usage: dict)`; `async LiteLlmAdapter.tool_turn(*, messages: list[dict], tools: list[dict], model: str, on_token: Callable[[str], Awaitable[None]] | None = None, tool_choice: str = "auto", **kw) -> ToolTurn` — streams (`stream=True`), forwards content deltas to `on_token`, assembles streamed tool-call deltas, records usage via the same path as `_acompletion_noted`, retries transient errors before the first token and applies the flagship → small fallback like `stream`.

- [ ] **Step 1: Failing tests** with a fake `acompletion` returning streamed chunks: content-only turn → `content` assembled, `on_token` called per delta, `tool_calls == []`; two parallel tool calls split over deltas → two `ToolCall`s with parsed JSON args; malformed argument JSON → `ToolCall.arguments == {"_raw": "<text>"}` (never raises); usage noted on the active trace step.
- [ ] **Step 2–4:** FAIL → implement → PASS (+ ruff/mypy). **Step 5:** commit `feat(llm): add a streamed tool-calling turn`.

### Task 4: Tool framework and read tools

**Files:** Create `agents/core/__init__.py`, `agents/core/tools.py`, `agents/core/read_tools.py`; Test `tests/agents_core/test_read_tools.py`

**Interfaces:**
- Produces:
  - `class Tier(StrEnum): FREE, SHOW_UNDO, IF_ASKED, CONFIRM`
  - `@dataclass ToolContext(conn, tracer, session_id: int, run_id: int, settings, adapter, emit: Callable[[str, dict], Awaitable[None]], enabled_papers: list[tuple[int, str]], history: list[dict], state: AgentState)`
  - `@dataclass ToolOutcome(content: str, record: dict, chunk_ids_read: list[int] = [], events: list[tuple[str, dict]] = [])` — `content` is what the model sees; `record` is what the trace stores.
  - `@dataclass ToolSpec(name: str, description: str, parameters: dict, tier: Tier, handler: Callable[[ToolContext, dict], Awaitable[ToolOutcome]])`
  - `ToolRegistry.register(spec)`, `.schemas() -> list[dict]` (OpenAI function format), `.get(name) -> ToolSpec`, `async .run(ctx, call: ToolCall) -> ToolOutcome` (wraps each call in `tracer.step(agent="agent", tool=f"tool:{name}")`, records `args` and `outcome.record`, marks errors; an unknown tool or bad args returns an error `ToolOutcome` the model can read, never raises).
  - Read tools (all `Tier.FREE`), operating on `paper` = a `paper_content_id` among `ctx.enabled_papers` unless stated:
    - `list_sections(paper)` → outline `[{name, chunks, tokens}]` (reuse `_list_sections`).
    - `read_section(paper, section, page=1)` → at most `READ_PAGE_CHUNKS = 12` chunks per page as `<chunk id="N">…</chunk> (p.P)` plus `page k of n`; record `{chunk_ids, page, pages}`.
    - `search_chunks(query, papers=None, limit=10)` → FTS5 over `chunks_fts` scoped to the given or enabled papers; each hit with id, paper, section, a 300-char snippet; record `{query_as_sent, hit_ids}`.
    - `read_chunks(ids)` → full text of those chunks (any paper in the library, so earlier turns' ids can be re-read); record `{chunk_ids}`.
    - `list_figures_tables(paper)`, `read_layout_object(paper, label)` — reuse the subagent helpers.
- [ ] **Step 1: Failing tests:** paging (a 30-chunk section → 3 pages, page 2 holds chunks 13–24 in `char_start` order); `search_chunks` scoping (a hit in a non-enabled paper is excluded unless named in `papers`); `read_chunks` of an unknown id → content says it is unknown, no exception; a paper not enabled → a readable error naming the enabled papers; **no enabled papers** → every paper-scoped tool returns "no papers are enabled in this chat; use search_papers or ask the user to add one" (Review Focus 5); `ToolRegistry.run` traces one `tool:read_section` row with `chunk_ids` in its result.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(agent): add the tool framework and paper reading tools`.

### Task 5: The `gather` tool (small-model subagent returning verbatim chunks)

**Files:** Create `agents/core/gather.py`, `llm/prompts/agent_gather_v1.yaml`; Test `tests/agents_core/test_gather.py`

**Interfaces:**
- Consumes: Task 4 read helpers; Task 3 `tool_turn`.
- Produces: tool `gather(papers: list[int], brief: str)` (`Tier.FREE`) → content = the selected chunks verbatim, each as `<chunk id="N" paper="…" section="…">text</chunk>` followed by `reason: <one line>`; record `{brief, papers, sections_read, chunk_ids_selected, reasons}` (full selection payload — not re-derivable). Internally a loop like `run_paper_qa_subagent` on `settings.agent_gather_model` with tools `list_sections`/`read_section`/`search_chunks` across the given papers, budget `GATHER_MAX_READS = 10`, ending with a structured `select(chunks: [{id, reason}])` call; selected ids not actually read in the loop are dropped (and the drop recorded).
- [ ] **Step 1: Failing tests** (fake `tool_turn`): returns only chunks it read, verbatim from the DB; a selected id it never read is dropped and recorded; budget exhaustion still returns the selection so far; traced as `tool:gather` with a nested `agent:gather` step.
- [ ] **Step 2–4:** FAIL → implement (prompt authored via `writing-agent-prompts`; at this task, the baseline v1 + one variant compared on 6 briefs from the scenario set, judged on "selected chunks answer the brief / nothing relevant missed") → PASS. **Step 5:** commit `feat(agent): add the gather tool`.

### Task 6: Action tools — library, discovery, memory, session

**Files:** Create `agents/core/action_tools.py` (first part); Test `tests/agents_core/test_action_tools_basic.py`

**Interfaces:**
- Produces tools:
  - `query_library(question)` `FREE` — wraps `sql_agent_stream` with `state.effective_query = question`; content = the answer + the SQL rows; record `{question, sql, rows}` (full rows, capped at 200); emits a `search_results` batch for its `library:` cards.
  - `search_papers(query, mode="search"|"suggest")` `FREE` — runs `build_paper_search_subgraph(ResearchDeps(...)).astream` with `effective_query = query`; collects `search_results` custom events and tool steps; does **not** auto-attach (the agent decides via `add_paper`); content = candidates (id, title, year, reason); record `{query, candidates}` (full); emits a `search_results` batch.
  - `add_paper(paper_id)` `SHOW_UNDO` — `add_paper_to_session_dispatch`; emits `paper_added {papers_id, paper_content_id, title}` (the card offers undo = disable); `NoIngestibleSourceError` → readable content with tried URLs.
  - `remember(fact, scope=None)` `SHOW_UNDO` — `add_memory_with_supersede` (gate refusal → readable content); emits `memory_saved {memory_id, content, scope}` (undo = forget).
  - `forget(memory_id)` `CONFIRM`; `remove_paper(paper)` `CONFIRM` (handlers run only via Task 8's confirmation path; the agent-facing call returns a pending action).
  - `list_ideas(filter="active", problem_id=None)` `FREE` — via the `_ideas` helper; `propose_ideas(problem: str)` `IF_ASKED` — creates the problem and starts `run_problem` in the background; content says ideas will appear on the Ideas page, with the problem id.
- [ ] **Step 1: Failing tests** with stubbed registries/adapters: each tool's content and record shape; `search_papers` never inserts into `papers`; `add_paper` emits `paper_added`; memory gate refusal is readable, not an exception; `query_library` record holds the rows.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(agent): wrap library, discovery, memory and ideation as tools`.

### Task 7: Slide tools with a forced-command hook

**Files:** Modify `agents/report_graph.py`; extend `agents/core/action_tools.py`; Test `tests/agents_core/test_slide_tools.py`, `tests/test_report_forced_command.py`

**Interfaces:**
- Produces: `AgentState.report_forced_command: DeckCommand | None` — when set, `_resolve` skips `classify_deck_command` and uses it (the classifier still runs when it is `None`, so v2.41 behaviour is unchanged). Tools (`IF_ASKED`): `create_deck(brief, slides=None, language=None)` (`regenerate` when a deck exists, else generate; `effective_query = brief`), `edit_slides(instruction, pages=None)` (`edit_slides` command with `target_page`/scope), `write_notes(language=None)` (`generate_notes`). Each runs `build_report_subgraph(ReportDeps(...)).astream`, forwards `deck` events and tool steps, stamps `runs.deck_version_id`; content = deck summary (title, pages, version id); record `{command, brief, deck_id, version_id}`.
- [ ] **Step 1: Failing tests:** `test_forced_command_skips_classifier`; `test_classifier_still_runs_without_forced_command`; `create_deck` on a chat without a deck generates and emits one `deck` event; `edit_slides(pages=[3])` forces `edit_slides` with `target_page=3`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(agent): drive slide generation and editing as tools`.

### Task 8: Permissions — confirmations and auto-approve

**Files:** Create `agents/core/permissions.py`, `api/actions.py`, `llm/prompts/agent_autoapprove_v1.yaml`; Modify `api/sessions.py` (`PATCH /sessions/{id}` accepts `auto_approve: bool`); Test `tests/agents_core/test_permissions.py`, `tests/test_actions_api.py`

**Interfaces:**
- Produces:
  - `async gate(ctx, spec, args) -> GateResult(run_now: bool, pending_id: str | None, reason: str)` — `FREE`/`SHOW_UNDO`/`IF_ASKED` → run now (`IF_ASKED` is a prompt rule, per spec); `CONFIRM` → if `chat_sessions.auto_approve` is on, ask the classifier (`agent_autoapprove/v1`, small model, structured `{run: bool, reason}` given the tool, args and the last user message), else pending. Every decision traced as `agent:approve` with `{tool, args, auto_approve, decision, reason}`.
  - A pending action inserts a `pending_actions` row and emits `confirm_request {action_id, tool, args_summary, reason}`; the tool's content tells the model "waiting for the user to confirm <summary>; do not retry".
  - `POST /chat/actions/{action_id}/confirm` runs the handler directly (no LLM), stores `result_json`, sets `done`, returns `{status, result}`; `/dismiss` sets `dismissed`. Both 404 on unknown id, 409 when not `pending`.
  - Auto-approve default **off**, **per chat** (`chat_sessions.auto_approve`) — the spec left both to the plan.
- [ ] **Step 1: Failing tests:** `CONFIRM` tool with auto-approve off → pending row + `confirm_request` event, handler not called; confirm endpoint runs the handler once, second confirm → 409; auto-approve on + classifier `{run: true}` → runs now, traced; classifier failure → falls back to asking (fail closed); `PATCH /sessions/{id} {auto_approve: true}` persists.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(agent): confirm destructive actions, with auto-approve`.

### Task 9: Context builder, citations and the agent loop

**Files:** Create `agents/core/context.py`, `agents/core/citations.py`, `agents/core/agent.py`, `llm/prompts/core_agent_v1.yaml`; Test `tests/agents_core/test_context.py`, `test_citations.py`, `test_agent_loop.py`

**Interfaces:**
- Produces:
  - `async build_session_state(conn, session_id, state) -> str` — `<enabled_papers>` (id, title, pdf/latex, section count), `<idea_context>` (from `build_idea_context`), `<slide_context>`, `<memories>` (`build_active_memory_block`), `<ui_language>`.
  - `async build_conversation(conn, session_id, history, *, keep_turns=AGENT_KEEP_TURNS=12) -> list[dict]` — earlier turns as user/assistant text plus, per assistant turn, `<work_record>` from `runs.agent_record_json` (tools, read and cited ids); turns older than `keep_turns` folded into one `<earlier_summary>` (deterministic: their user questions and cited ids — no LLM call).
  - `ContextBlocks(instructions, tools, session_state, conversation, this_turn)` with `token_counts() -> dict[str, int]` (litellm `token_counter`).
  - `validate_citations(text, allowed_ids: set[int]) -> (clean_text, removed_ids)` — removes `[chunk:N]` (and `[chunk:N,M]` members) not in `allowed_ids`.
  - `async run_core_agent(state, deps: AgentDeps) -> AsyncIterator[AgentEvent]` where `AgentDeps(adapter, tracer, conn, settings, registry: ToolRegistry, emit)` and `AgentEvent` is one of `Token(text)`, `Step(record)`, `Final(text, record)`. Loop: build messages (system = instructions; tools; session state + conversation; this turn), `tool_turn`, run tool calls **concurrently** (`asyncio.gather`, permission gate first), append results, repeat until no tool calls or the budget (`AGENT_MAX_STEPS = 12`, `AGENT_MAX_TURN_TOKENS = 120_000` counted on this turn's tool results). On budget: one final `tool_turn` with `tool_choice="none"` and an instruction to answer with what it has and name what is missing. Each loop iteration is one `agent:step` row recording `{step, model, context_tokens: {...}, tool_calls: [{name, args}], tool_results: [{name, status, ids}], answer?}`. The final answer passes `validate_citations` with `allowed_ids` = chunks read this turn ∪ ids in kept work records; removals are recorded on the last step. The work record is written to `runs.agent_record_json`.
- [ ] **Step 1: Failing tests** (fake adapter scripting tool calls):
  - `test_follow_up_carries_history` — history with an earlier answer about GISC→YOLOSeg; the messages sent to the model contain that answer text and its `<work_record>` (Review Focus 1).
  - `test_old_turns_fold_into_summary` — 20 turns → 12 verbatim + one summary block; no LLM call made for it.
  - `test_parallel_tool_calls_run_concurrently_and_trace_each`.
  - `test_budget_reached_forces_an_answer` — a model that always calls tools → after 12 steps a `tool_choice="none"` turn; the answer names what is missing; status `ok` (Review Focus 2).
  - `test_invented_citation_removed_and_traced` — answer cites a read id and an unread id → only the read one survives; removal on the last `agent:step` (Review Focus 3).
  - `test_no_papers_answer_offers_search` — no enabled papers; the tool results from Task 4 lead the scripted model's answer; assert no read tool hit the DB (Review Focus 5).
  - `test_context_token_counts_recorded`.
- [ ] **Step 2–4:** FAIL → implement (prompt `core_agent/v1` authored via `writing-agent-prompts` — the baseline here is a first draft plus one variant compared on 8 scenario-1/2/3 cases offline with Claude-as-judge; the full sweep is Task 14) → PASS. **Step 5:** commit `feat(agent): add the core agent loop with precise context`.

### Task 10: Chat endpoint drives the agent

**Files:** Modify `api/chat.py`, `api/sessions.py`, `models/events.py`; Test `tests/test_chat_agent_flow.py`

**Interfaces:**
- Consumes: Task 9 `run_core_agent`; Task 4–8 registry (`build_default_registry(settings) -> ToolRegistry` in `agents/core/action_tools.py`).
- Produces: `run_agent` on this branch skips `router_node`; sets `runs.mode='agent'`; streams `Token` → `token`, `Step` and tool rows → `tool_step` (drained with the **emitted-index set** pattern, not a watermark, because parallel tools close out of order), tool events (`search_results`, `deck`, `paper_added`, `memory_saved`, `confirm_request`) as their own SSE events, and at the end `agent_summary {run_id, papers_read: int, steps: int, tools: [names]}` then `final`. Search batches are appended to `runs.search_results_json` `{"batches": [...]}`. Replay: `MessageOut.agent_summary` from `runs.agent_record_json`; `routing_decision` stays for `mode='pipeline'` runs. Stop: `POST /chat/cancel` cancels the loop task; in-flight tool tasks are cancelled with it; run `cancelled`, messages retracted as today.
- [ ] **Step 1: Failing tests** (TestClient + fake adapter): a turn emits `session`, `tool_step`×n, `token`…, `agent_summary`, `final` in order, no `routing_decision`; two `search_papers` calls → two batches persisted and replayed; cancel during a slow tool → `cancelled`, no assistant message, the tool task cancelled (Review Focus 4); a pre-H4 run in the DB replays with its `routing_decision`.
- [ ] **Step 2–4:** FAIL → implement → PASS (+ the existing `tests/test_chat_*` still pass — the pipeline wrappers stay importable). **Step 5:** commit `feat(chat): drive every turn through the core agent`.

### Task 11: Settings and environment

**Files:** Modify `config.py`, `settings_registry.py`, `backend/.env.example`, `frontend/src/locales/*/settings.json`; Test `tests/test_settings_agent.py`

- [ ] **Step 1: Failing tests:** `load_settings()` exposes `agent_model` (default flagship tier), `agent_gather_model` (small), `agent_autoapprove_model` (small), `agent_max_steps` (12), `agent_max_turn_tokens` (120000), `agent_keep_turns` (12) from `PAPERHUB_AGENT_MODEL`, `PAPERHUB_AGENT_GATHER_MODEL`, `PAPERHUB_AGENT_AUTOAPPROVE_MODEL`, `PAPERHUB_AGENT_MAX_STEPS`, `PAPERHUB_AGENT_MAX_TURN_TOKENS`, `PAPERHUB_AGENT_KEEP_TURNS`; each has a `SettingField`; locale parity passes.
- [ ] **Step 2–4:** FAIL → implement; `.env.example`: models in section 3 as `# Core agent (H4) [flagship].` / `# PAPERHUB_AGENT_MODEL=` etc., tunables in section 4, one line each in the file's style → PASS. (Tasks 5, 8, 9 read these; until this task they use the constants named there.) **Step 5:** commit `feat(settings): configure the core agent`.

### Task 12: Frontend — summary badge, trace rows, cards, confirmations, auto-approve

**Files:** Create `AgentSummaryBadge.tsx`, `ConfirmActionCard.tsx`, `AutoApproveToggle.tsx`, `ProposalButtons.tsx` (+ tests next to each); Modify `ChatThread.tsx`, `MessageBubble.tsx`, `TraceInline.tsx`, `hooks/useChatStream.ts`, `store/chat.ts`, `types/domain.ts`, `lib/api.ts`, `pages/ChatPage.tsx`, `locales/*/chat.json`

**Interfaces:**
- Consumes: SSE `agent_summary`, `paper_added`, `memory_saved`, `confirm_request`, `search_results` (several per turn); `POST /chat/actions/{id}/confirm|dismiss`; `PATCH /sessions/{id} {auto_approve}`; replayed `agent_summary`.
- Produces:
  - `AgentSummaryBadge` — `Agent · read {n} papers · {s} steps` (pluralised via i18n); click toggles the trace like `RoutingBadge`; shown when `agent_summary` is set, `RoutingBadge` otherwise.
  - `TraceInline` — rows for `agent:step` (step n, tools called) and `tool:*` (tool name + a short argument summary: `paper`, `section`, `query`, `brief` (first 60 chars), `ids` count).
  - Multiple `SearchResultList`s per message (one per batch).
  - `paper_added` / `memory_saved` chips with Undo (paper: `PATCH /papers/{id} {enabled:false}`; memory: `DELETE /memories/{id}`).
  - `ConfirmActionCard` — summary + reason, Confirm / Dismiss; after a click shows the result; enters with fade+zoom, exits by collapsing height.
  - `AutoApproveToggle` under the composer (per chat; off by default) with a tooltip explaining it; state from the session.
  - `ProposalButtons` — when the answer ends with proposals (the agent marks them as `<proposal>…</proposal>` lines, stripped from the visible text), render buttons that send that text as a new message.
- [ ] **Step 1: Failing tests** (Vitest + MSW): each component renders and its click calls the right endpoint; two batches → two lists; a replayed pipeline message still shows `RoutingBadge`; confirm card exit animates (has the collapse class on `data-state=closed`, unmounts after the exit); locale parity.
- [ ] **Step 2–4:** FAIL → implement → PASS; `npm run typecheck`, `npx eslint` on changed files. **Step 5:** commit per component group (`feat(chat): …`).

### Task 13: Feedback and training export for agent turns

**Files:** Modify `feedback/models.py`, `feedback/signals.py`, `feedback/review.py`, `feedback/export.py`; Test `tests/test_feedback_agent.py`

**Interfaces:**
- Produces: stage attribution for `mode='agent'` runs: `STAGE_OF_TOOL = {"agent:step(no tools)": "answer", "tool:read_*|search_chunks|gather": "retrieval", "tool:query_library": "retrieval", "tool:search_papers": "router", …}` and the citation stage from `validate_citations` removals; `guess_reasons` and `load_review_inputs` read `agent:step` / `tool:*` rows. Export: `export_agent_trajectory(conn, run_id) -> dict` returning `{"messages": [system, *history, user, assistant(tool_calls), tool(results)…, assistant(final)], "tools": [schemas]}` rebuilt from `agent:step` + `tool:*` records — library chunks re-read by id, ephemeral payloads from the records; private papers' chunk text redacted by the existing fail-closed rule; SFT/DPO builders use it for agent runs.
- [ ] **Step 1: Failing tests:** a recorded agent run exports a trajectory whose tool results equal the original content (chunks re-read, SQL rows from the record); a private paper's chunk text is redacted; a pre-H4 run exports exactly as before; stage guess for a run that dropped a citation → `citation`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat(feedback): attribute and export agent turns`.

### Task 14: Scenario benchmark, gate run and prompt iteration

**Files:** Create `backend/benchmark/cases.h4-scenarios.toml`; Modify `benchmark/config.py`, `driver.py`, `runner.py`, `judge.py`; Test `tests/benchmark/test_scenarios_config.py`, `test_judge_thread.py`

**Interfaces:**
- Produces: TOML cases may have `turns = [{user = "...", assistant = "..."}]` (seeded history: the driver sends them as `history` in `POST /chat`) and `scenario = 1..8`; `judge.py` adds a `thread` criterion (does the answer continue the conversation's subject?) — a case scores 1 only if correct, grounded and on the thread; `runner.py --compare <base_url_b>` runs each case on both instances and writes pairwise verdicts (both orders) with `benchmark/agent/pairwise.py`'s judge; a summary block computes G-a…G-d from the Global Constraints.
- [ ] **Step 1: Failing tests** for config parsing (`turns`, `scenario`), the driver sending history, the gate summary maths (e.g. 2/3 per scenario, 70 % pairwise, latency ratio).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Author 24 cases** (3 per scenario, from the user's real library in the H4 workspace; case 1 of scenario 1 = run 892's turn with its real history), each with a rubric naming what must be stated, cited, or honestly reported missing.
- [ ] **Step 6: Gate run:** start H4 (`:8100`, runbook) — v2.41 on `:8000` is the user's; **do not use it for the comparison** — start a second v2.41 instance from a temporary worktree of tag `v2.41.0` on `:8200` against **another** copy of the workspace (`D:/paperhub-v241-eval-workspace`, own MCP config) so the user's instance is never loaded. Run scenarios + `cases.example.toml` on both with `--judge --compare`.
- [ ] **Step 7: Prompt iteration** (dispatch an agent that invokes `writing-agent-prompts` first): `core_agent`, tool descriptions, `agent_gather`, `agent_autoapprove` — ≥2 variants per change, judged on the scenario set + regression cases, deploy models, until G-a…G-d pass. Ship each winner as a new version file. Record every sweep in `benchmark/agent/prompts/<stage>/README.md`.
- [ ] **Step 8:** commit benchmark code, cases and prompt versions separately; save the final gate report JSON/Markdown under `backend/benchmark/results/` (gitignored) and summarise the numbers in the final report.

### Task 15: Live check, spec sync and wrap-up

**Files:** Modify `docs/superpowers/specs/2026-05-17-paperhub-srs.md` (only where the built tool names / numbers differ from §III-10.4), `CLAUDE.md` (plan table row for H4: status), `backend/scripts/reingest_all_papers.ps1` (only if a per-paper backfill CLI was added — `chunks_fts` backfills in the migration, so none is expected).

- [ ] **Step 1:** Full gates in the worktree: `uv run pytest -q`, `ruff check src tests`, `mypy src`; `npm test -- --run`, `typecheck`, `lint`, `build`.
- [ ] **Step 2: Live user simulation on H4 (`:8100` / `:4173`)** via Playwright and the API: run 892's turn in a fork; a multi-paper comparison; a confirm-tier action with auto-approve off then on; Stop mid-gather; a pre-H4 chat replays with its routing badge. Read each run's trace (`paperhub-replay --run-id`) and confirm the recorded state matches the answer. Delete test chats.
- [ ] **Step 3: Isolation check:** `git -C D:/GitHub/PaperHub status` shows only the user's own files; `D:/GitHub/PaperHub` is on `main`; `GET :8000/health` OK; the v2.41 workspace DB has no `runs.mode` column.
- [ ] **Step 4:** Update the SRS §III-10.4 for any name or number that changed during the build (single-spec rule: update in place, no contradictions); CLAUDE.md H4 row. Commit `docs: record H4 as built`.
- [ ] **Step 5:** Final report for the user: gate numbers (G-a…G-d), what passed or failed, every issue fixed along the way, cost/latency table, open items. Do **not** run merge-prep, merge, tag or push.
