# Plan E1 — `library_stats` rows as attachable cards (Implementation Plan)

> **STATUS: SUPERSEDED (2026-06-14) by [2026-06-14-paperhub-sql-agent-react.md](2026-06-14-paperhub-sql-agent-react.md).** Tasks 1, 2, 5 + the router nudge shipped (the `search_results` card plumbing + `SearchResultList`→`attachFromLibrary` are reused). The real-API gate showed the emit-EVERY-row approach surfaced coarse `LIKE` false positives as cards; the SQL Agent is being reworked into an intelligent ReAct agent that **curates** the relevant subset (SRS §III-3 v2.34). The prompt-nudge tasks (3, 4) here are replaced by that plan's ReAct agent prompt. Read the ReAct plan for the live work.

> **(original) STATUS: NOT STARTED.** Plan E follow-up E1. Fulfills the read-and-act intent already specced in SRS §III-3 SQL Agent (prose-only since v2.16). Branch: `fix/followups`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A `library_stats` turn no longer answers with prose + a bare SQL block. **Paper-shaped** results (the query returns papers) surface as **attachable cards** the user can add to the session; **aggregate** results (counts, per-year) render as a **legible table** instead of being buried in a sentence. UX-first: collect papers like search results; read aggregates like a table.

**Architecture:** Reuse, don't invent. The Research Agent's `search_results` SSE path + `SearchResultList` card already render `library:<id>` candidates and attach them via `POST /papers/from-library` (FR-08 path 3). E1 wires the SQL Agent into that path: when the executed `SELECT` includes a `paper_content_id` column, each result row becomes a `library:<id>` `SearchCandidate` (title, year, `already_in_session`) yielded as a `SearchResultsYield` — chat.py routes it exactly like a `paper_search` result (emit `search_results`, persist `runs.search_results_json`). Aggregate results (no `paper_content_id` column) are rendered by the answer LLM as a **GFM markdown table** in the prose (`remark-gfm` already enabled — no new event, no new component). Two prompt nudges make this reliable: the planner `SELECT`s `paper_content_id, title` for listing queries; the answer formats non-paper rows as a markdown table. The v2.32 collapsible SQL card is untouched.

**Why no new SSE event / schema change:** `library:<id>` candidates are already handled end-to-end (`chat.py:206` resolves the pcid + `already_in_session`; `attachFromLibrary` exists in `lib/api.ts`; `runs.search_results_json` already persists/replays). E1 is wiring, two prompt nudges, and one small frontend attach branch.

**Tech Stack:** Python 3.11 · Pydantic v2 · LangGraph · `litellm` via `LlmAdapter` · `aiosqlite` · pytest/pytest-asyncio · `uv` · React + TypeScript · Vitest/RTL/MSW.

**Authoritative spec:** SRS §III-3 SQL Agent row ("Read-and-act (wired in v2.34 — E1)") + FR-08 (three attach paths; E1 uses path 3 from a new surface).

---

## Acceptance criteria (verified at the real-API gate, Task 6 — pytest proves mechanism, the live run proves these)

- **Q1 — Paper-shaped query is attachable.** "list my papers about X" → the recorded trace shows the SQL `SELECT`ed `paper_content_id`; a `search_results` event carries one `library:<id>` candidate per result row with `already_in_session` correct; the frontend renders `SearchResultList` cards; clicking Add → the paper appears in the References panel (`POST /papers/from-library`).
- **Q2 — Aggregate query is legible.** "how many papers per year" → the answer renders a GFM markdown **table** of the rows (not a prose sentence), and **no** `search_results` candidates are emitted (nothing to attach).
- **Q3 — No regression to the existing surface.** The v2.32 SQL card still lifts the ```sql block; `paper_search`/`paper_suggest` cards are unaffected (shared path); already-in-session library rows render as "in session", not an Add that no-ops.

If the gate shows any of these failing, the phase is not done.

---

## File map

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `backend/src/paperhub/agents/sql_agent.py` | Modify | After `sql.query`, detect a `paper_content_id` column; build `library:<id>` `SearchCandidate`s (title, year, `already_in_session` from one `papers` membership query); `yield SearchResultsYield(candidates=…)` before the answer stream. |
| `backend/src/paperhub/api/chat.py` | Modify | In the `library_stats` loop (~L800), add an `isinstance(item, SearchResultsYield)` branch mirroring the `paper_search` branch: `_process_search_results` → emit `search_results` SSE → persist `runs.search_results_json`. |
| `backend/src/paperhub/llm/prompts/sql_planner_v1.yaml` | Modify | Nudge: for listing/finding queries (not aggregations) include `paper_content_id, title` in the `SELECT` so rows are attachable. |
| `backend/src/paperhub/llm/prompts/sql_answer_v1.yaml` | Modify | Nudge: when the result is NOT paper-shaped (no `paper_content_id`), render the rows as a GFM markdown table in the answer. |
| `frontend/src/components/chat/SearchResultList.tsx` | Modify (verify) | Ensure a `library:<id>` candidate's Add calls `attachFromLibrary(paper_content_id)` (not the `ss:`/`arxiv:` `ingestPaper` path). Add the branch if missing. |
| `backend/tests/…`, `frontend/tests/…` | Create/Modify | Per-task tests below. |

**Scope boundary:** OUT = a dedicated styled aggregate-table component (markdown table is the v1); "Add all" bulk button (per-row Add via the existing card is the v1 — the card has no bulk control and we are not adding one here); any new SSE event or `runs` column; editing/re-running SQL from the UI.

**Conventions:** all backend cmds from `backend/` via `uv` (never pip/system python); PowerShell `;`; strict ruff + mypy; TDD (failing test first) per task; relevant test files per task, full suite + real-API gate once at the end (Tasks 5–6). Commit per task, no push.

---

### Task 1: SQL Agent yields `library:<id>` candidates for paper-shaped results

**Files:** Modify `backend/src/paperhub/agents/sql_agent.py`; Test `backend/tests/test_sql_agent_candidates.py` (new).

- [ ] **Step 1 — failing test:** drive `sql_agent_stream` with a stub registry whose `sql.query` returns `columns=["paper_content_id","title","year"]` + two rows; assert the stream yields a `SearchResultsYield` whose candidates are `paper_id="library:<id>"`, with `title`/`year` set and `already_in_session` reflecting a stubbed `papers` membership. A second stub returning `columns=["year","n"]` (no `paper_content_id`) yields **no** `SearchResultsYield`.
- [ ] **Step 2 — impl:** after the `sql.query` result is in hand, if `paper_content_id` ∈ columns, map each row → `SearchCandidate(paper_id=f"library:{pcid}", title=…, year=…, already_in_session=…, finalize=False, …)`; resolve `already_in_session` with one `SELECT paper_content_id FROM papers WHERE session_id=?` set lookup (not per-row). `yield SearchResultsYield(candidates=…)` before the answer stream. No emission when the column is absent.
- [ ] **Step 3:** `uv run pytest tests/test_sql_agent_candidates.py`; ruff + mypy on the file. Commit.

### Task 2: chat.py routes the SQL Agent's `SearchResultsYield`

**Files:** Modify `backend/src/paperhub/api/chat.py`; Test `backend/tests/test_chat_library_stats_cards.py` (new, or extend an existing chat stream test).

- [ ] **Step 1 — failing test:** a `library_stats` turn whose stubbed SQL agent yields a `SearchResultsYield` → the SSE stream contains a `search_results` event with the candidates, and `runs.search_results_json` is persisted for that run.
- [ ] **Step 2 — impl:** in the `intent == "library_stats"` loop, add `elif isinstance(item, SearchResultsYield):` mirroring the `paper_search` branch (L699–732): `_process_search_results` → `SearchResultsEvent` → yield → `UPDATE runs SET search_results_json`.
- [ ] **Step 3:** `uv run pytest tests/test_chat_library_stats_cards.py`; ruff + mypy. Commit.

### Task 3: Planner prompt nudge — SELECT `paper_content_id, title` for listing queries

**Files:** Modify `backend/src/paperhub/llm/prompts/sql_planner_v1.yaml`; Test extend `backend/tests/test_sql_agent*.py` (prompt-content assertion).

- [ ] **Step 1 — failing test:** the loaded `sql_planner/v1` system prompt contains the listing-query nudge (mentions including `paper_content_id` + `title` when listing/finding papers, and NOT forcing it for aggregations).
- [ ] **Step 2 — impl:** add the nudge line(s). Keep it language-agnostic and aggregation-safe.
- [ ] **Step 3:** pytest the assertion; ruff. Commit. (Live behavior verified in Task 6.)

### Task 4: Answer prompt nudge — aggregate rows as a markdown table

**Files:** Modify `backend/src/paperhub/llm/prompts/sql_answer_v1.yaml`; Test prompt-content assertion.

- [ ] **Step 1 — failing test:** the `sql_answer/v1` prompt instructs rendering non-paper (aggregate) results as a GFM markdown table.
- [ ] **Step 2 — impl:** add the nudge; ensure it does NOT duplicate paper rows already shown as cards (paper-shaped results are surfaced as cards, so the answer should summarize, not re-table them).
- [ ] **Step 3:** pytest; ruff. Commit. (Live behavior verified in Task 6.)

### Task 5: Frontend — `library:<id>` Add routes through `attachFromLibrary`

**Files:** Modify `frontend/src/components/chat/SearchResultList.tsx`; Test extend `frontend/tests/components/SearchResultList.test.tsx`.

- [ ] **Step 1 — verify/failing test:** a candidate with `paper_id="library:42"` → clicking Add calls `attachFromLibrary(sessionId, 42)` (NOT `ingestPaper`), then optimistically inserts the reference. (If the component already branches on `library:`, the test just locks it in; if not, the test fails first.)
- [ ] **Step 2 — impl (if needed):** branch `doIngest` on `paper_id.startsWith("library:")` → `attachFromLibrary(sessionId, Number(pcid))`; reuse the existing optimistic-insert + confirm flow.
- [ ] **Step 3:** `npm test SearchResultList`; typecheck + lint. Commit.

### Task 6: Quality gates + real-API gate (run once, at the end)

- [ ] **Step 1:** full backend suite (`uv run pytest -q`, ruff, mypy) + full frontend suite (`npm test`, typecheck, lint, build).
- [ ] **Step 2 — real-API (live `:8000`, per CLAUDE.md):**
  - "list my papers about X" → confirm a `search_results` event with `library:<id>` candidates (trace shows the SQL `SELECT`ed `paper_content_id`); frontend renders cards; click Add → reference appears (`POST /papers/from-library`); already-in-session rows show "in session".
  - "how many papers per year" → answer renders a markdown table; no `search_results` candidates.
  - Read the recorded run (`paperhub-replay --run-id <N>` / `tool_calls`) to confirm the right stages + payloads.
- [ ] **Step 3 — frontend human sign-off:** open the app, run both turns, confirm the cards + table render and Add works visually.
- [ ] **Step 4 — ship:** bump SRS to v2.34.0 + add the Revision History row (with final test counts); remove E1 from CLAUDE.md "Known follow-ups". Commit.

---

## Out of scope (YAGNI / deferred)

- Styled aggregate-table component (markdown table is the v1).
- "Add all" bulk attach (per-row Add via the existing card; revisit if users ask).
- A new `sql_results` SSE event or `runs` schema column (the `search_results` path + markdown table cover both row kinds).
- Editing / re-running the SQL from the card.
