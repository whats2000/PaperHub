# Plan — SQL Agent → intelligent ReAct agent with curated structured output (Implementation Plan)

> **STATUS: SHIPPED (v2.34.0, 2026-06-14).** All 7 tasks landed + the gate fixes (router routing, candidate dedup, planner alias, frontend one-line-fence SqlCard). Live-verified: 9 `LIKE` hits → 5 curated reasoned cards; aggregate → markdown table. Reworks the SQL Agent (Plan E / v2.16) per SRS §III-3 (v2.34 revision). **Absorbs and supersedes** the emit-every-row approach of [2026-06-13-paperhub-E1-library-stats-attachable-rows.md](2026-06-13-paperhub-E1-library-stats-attachable-rows.md) — that plan's card/attach plumbing (search_results forwarding, `SearchResultList` → `attachFromLibrary`, the router nudge) is **reused**; its deterministic emit-all + the `sql_planner`/`sql_answer` prompt nudges are **replaced** by the ReAct loop + curation here. Branch: `fix/followups`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. TDD per task.

**Goal:** Make `library_stats` an *intelligent agent*, not a SQL result-dumper. The agent REASONS about the question, ACTS by running read-only queries over the `sql` MCP, OBSERVES the rows, and REFINES until it can answer — then emits a **structured final output** `{answer, papers:[{paper_content_id, reason}]}`. The `papers` set is the agent's **curated** shortlist (the genuinely-relevant subset, with a reason each — NOT every `LIKE` hit), surfaced as attachable `library:<id>` cards. Pure aggregate/stat questions return empty `papers` and a markdown-table answer.

**Why:** SQL retrieval is coarse — no vector store since v2.27, so a topic query is a `LIKE`/exact filter that over-returns (a stray abstract mention surfaces an off-topic paper). The old pipeline (plan→query→repair-once→dump every row→phrase) put the LLM only at the ends and surfaced all false positives as cards. The fix is to put the LLM **in the loop** and make it **decide the final set** — the same shortlist-and-explain discipline the Research Agent (`paper_search`) already uses, applied to the user's own library.

**Architecture:** A bounded ReAct loop modeled on the slide orchestrator's `RoundAction` pattern (`sl_outline`). Each round the LLM receives the question + injected table schemas + accumulated query results and returns a structured `SqlRoundAction`:
- `action="query"` → carries a `sql` string; the loop validates + runs it via the `sql` MCP (`sqlglot` verb/table allowlist, NFR-05) and appends the rows to the running context, then loops.
- `action="finalize"` → carries `answer` (prose, in `response_language`, with the executed SQL as a ```sql block; a GFM markdown table when the result is aggregate) + `papers` (the curated `[{paper_content_id, reason}]`).
Capped at **≤4 query rounds**; the last round is forced to `finalize`. On finalize, the curated `papers` become `library:<id>` `SearchResultsYield` candidates (reusing the E1 plumbing) and the `answer` streams. Two-layer scoping (library = `paper_content` vs this-session = `papers ⨝ paper_content WHERE session_id`) is retained in the agent prompt.

**Tech Stack:** Python 3.11 · Pydantic v2 · `litellm` via `LlmAdapter.structured` · `aiosqlite` · the in-repo `sql` MCP · pytest/pytest-asyncio · `uv`.

**Authoritative spec:** SRS §III-3 SQL Agent row ("Reworked into an agentic ReAct loop in v2.34").

---

## Acceptance criteria (verified at the real-API gate, Task 7 — pytest proves mechanism, the live run proves these)

- **Q1 — Curation, not dumping.** "list my papers about transformers" → the cards are the **relevant subset only** (e.g. "Attention Is All You Need", "BERT", "A Survey of Transformers", "Efficient Transformers" — NOT "Self-Supervised Learning from Images…" that matched a stray abstract token), each card carrying the agent's `reason`. The trace shows the agent SELECTed candidate rows and then finalized a SMALLER curated set.
- **Q2 — Reasons through queries.** When the first query is too broad/narrow, the trace shows ≥1 refining query before finalize (not always — only when warranted), within the ≤4-round cap. A one-shot finalize on a clean question is fine.
- **Q3 — Aggregate path intact.** "how many papers per year" → `papers=[]`, no cards, the `answer` renders a markdown table.
- **Q4 — No regression.** The ```sql block still appears (SqlCard); `paper_search`/`paper_suggest` unaffected; already-in-session library papers render "in session" not a no-op Add; rejected out-of-scope SQL still traces `status='rejected'`.

If any fail, the phase is not done.

---

## File map

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `backend/src/paperhub/models/slide_domain.py` *(or a new `sql_domain.py`)* | Create/Modify | `SqlRoundAction` (`action: Literal["query","finalize"]`, `sql: str\|None`, `answer: str\|None`, `papers: list[SqlPaperPick]`) + `SqlPaperPick` (`paper_content_id: int`, `reason: str`). All decision fields REQUIRED in the schema (per the Gemini nullable-omission lesson — see SRS/commit 72c31a5) so the model always emits them. |
| `backend/src/paperhub/llm/prompts/sql_agent_v1.yaml` | Create | The ReAct system prompt: loop protocol, two-layer scoping, **curation instruction** (pick only genuinely-relevant rows + a reason; do NOT return every row), `id AS paper_content_id` aliasing for listing, the `SqlRoundAction` finalize schema, aggregate→markdown-table guidance, ≤4-round + forced-finalize rule. |
| `backend/src/paperhub/agents/sql_agent.py` | Rewrite | `sql_agent_stream` becomes the bounded ReAct loop: per round → `adapter.structured(SqlRoundAction)` → validate+run `sql.query` on `action="query"` (append rows to context) or break on `action="finalize"`; force finalize on the last round; trace each round + each `sql.query`; keep the recalled-memory block. On finalize: resolve the curated `papers` to `library:<id>` candidates (title/year/`already_in_session` from DB by `paper_content_id`, `reason` from the pick) → `yield SearchResultsYield`; then stream the `answer`. Remove the deterministic `_emit_library_candidates` emit-all. |
| `backend/src/paperhub/llm/prompts/sql_planner_v1.yaml`, `sql_repair_v1.yaml`, `sql_answer_v1.yaml` | Delete | Superseded by `sql_agent_v1.yaml`. Remove their slots + the prompt-content tests that assert on them. |
| `backend/src/paperhub/api/chat.py` | Modify (verify) | The `library_stats` branch already forwards `SearchResultsYield` + streams tokens + drains tool_steps (E1 Task 2, reused). Confirm the new stream shape (round steps + search_results + answer tokens) flows; adjust only if needed. |
| `backend/tests/…` | Create/Modify | Per-task tests; replace the old `test_sql_agent*.py` / `test_sql_prompts.py` cases that asserted the planner/answer/emit-all behavior. |

**Scope boundary:** OUT = exposing the sql MCP as native LLM tool-calling (we keep the structured-round `SqlRoundAction` for deterministic validation + tracing); a styled aggregate-table component (markdown table stays); changing the frontend (`SearchResultList`/`attachFromLibrary` are reused as-is); semantic/vector retrieval (still none — v2.27).

**Conventions:** backend cmds from `backend/` via `uv`; PowerShell `;`; strict ruff + mypy; TDD per task; full suite + real-API gate once at the end (Tasks 6–7); commit per task; no push.

---

### Task 1: Schema — `SqlRoundAction` + `SqlPaperPick`

**Files:** Modify `backend/src/paperhub/models/slide_domain.py` (or new `models/sql_domain.py`); Test `backend/tests/models/test_sql_round_action.py`.

- [ ] **Step 1 — failing test:** construct a `finalize` action with `answer` + two `SqlPaperPick`s; assert fields; assert `model_json_schema()["required"]` contains `action` AND the decision fields the model must always emit (so Gemini can't drop them — mirror the `DeckCommand.target_page` fix). A `query` action carries `sql`.
- [ ] **Step 2 — impl:** add the two models. `action` is a `Literal`; `sql`/`answer`/`papers` are present-but-typed for the union (a `query` leaves `answer`/`papers` empty, a `finalize` leaves `sql` empty) — make them REQUIRED-in-schema (no silent default) and validate the shape in code, OR use a discriminated union; pick the cleanest that keeps mypy --strict happy and forces emission.
- [ ] **Step 3:** `uv run pytest tests/models/test_sql_round_action.py -q`; ruff + mypy. Commit.

### Task 2: ReAct agent prompt `sql_agent/v1`

**Files:** Create `backend/src/paperhub/llm/prompts/sql_agent_v1.yaml`; Test `backend/tests/test_sql_agent_prompt.py`.

- [ ] **Step 1 — failing test:** load `sql_agent/v1`; assert the system prompt contains: the round protocol (query vs finalize), the curation instruction (relevant subset + reason, NOT every row), `AS paper_content_id` aliasing, two-layer scoping (library vs session), the ≤4-round/forced-finalize rule, and the aggregate→markdown-table guidance. (System block is sent raw — literal braces OK; only `user_template` is `.format()`-ed.)
- [ ] **Step 2 — impl:** author the prompt. Keep two-layer scoping wording from the old `sql_planner` (it was correct). Be explicit that coarse `LIKE` over-returns and the agent must filter.
- [ ] **Step 3:** pytest the assertions; ruff. Commit. (LLM behavior verified at Task 7.)

### Task 3: The ReAct loop in `sql_agent_stream`

**Files:** Rewrite `backend/src/paperhub/agents/sql_agent.py`; Test `backend/tests/test_sql_agent_react.py`.

- [ ] **Step 1 — failing tests (stub adapter + stub `sql` MCP):**
  - one-shot: round 1 returns `finalize{answer, papers:[…]}` → exactly one `sql.query`-free path is fine; the loop ends; the answer streams; cards (Task 4) emitted from `papers`.
  - refine: round 1 returns `query{sql}` (stub MCP returns rows), round 2 returns `finalize` → two `sql.query` traced, then finalize.
  - cap: an adapter that always returns `query` is forced to `finalize` by round 4 (assert ≤4 `sql.query` calls, then a finalize path).
  - validation: a `query` whose SQL is out-of-scope → the MCP rejects → `status='rejected'` traced; the loop sees the error and can refine.
- [ ] **Step 2 — impl:** replace the fixed pipeline with the loop: per round `adapter.structured(slot="sql_agent/v1", response_model=SqlRoundAction, …)` with the running context (question, schemas, prior query results); `action="query"` → `_mcp_call("sql.query", …)` (existing validation) → append rows; `action="finalize"` → break. Force `must_finalize` on the last round (prompt var). Trace each round (`sql:react` round step) + each `sql.query`. Keep `_drain_new_steps` + recalled-memory injection. Drop `_emit_library_candidates` (replaced in Task 4) and the old plan/repair/answer calls.
- [ ] **Step 3:** `uv run pytest tests/test_sql_agent_react.py -q`; ruff + mypy. Commit.

### Task 4: Emit curated cards + stream the answer on finalize

**Files:** Modify `backend/src/paperhub/agents/sql_agent.py`; Test extend `tests/test_sql_agent_react.py`.

- [ ] **Step 1 — failing test:** on `finalize{papers:[{paper_content_id:52,reason:"…"},…]}`, the stream yields one `SearchResultsYield` with `paper_id="library:52"`, `reason` set from the pick, `already_in_session` from a seeded `papers` membership, title/year resolved from `paper_content` by id; emitted BEFORE the answer tokens. A `finalize{papers:[]}` (aggregate) yields NO `SearchResultsYield`, and the `answer` (a markdown table) still streams.
- [ ] **Step 2 — impl:** build candidates from the curated `papers`: one `SELECT id, title, year FROM paper_content WHERE id IN (…)` + one `papers` membership query (set lookup, not per-row); map to `SearchCandidate(paper_id=f"library:{id}", title, year, already_in_session, reason=pick.reason, finalize=False)`; `yield SearchResultsYield(candidates)`. Then stream `answer` as tokens (re-chunk the finalized string so the UX matches the old token stream). Reuse the ragged/dedup guards.
- [ ] **Step 3:** pytest; ruff + mypy. Commit.

### Task 5: Remove superseded prompts + the emit-all path

**Files:** Delete `sql_planner_v1.yaml`, `sql_repair_v1.yaml`, `sql_answer_v1.yaml`; prune their registry registration; Modify/remove `tests/test_sql_prompts.py` + the E1 emit-all tests (`test_sql_agent_candidates.py`) that no longer apply.

- [ ] **Step 1:** delete the three slots + any registry references; remove `_emit_library_candidates` if not already gone in Task 3/4.
- [ ] **Step 2:** delete/replace the now-obsolete tests (the curation is covered by Task 3/4 tests). Keep any still-valid assertions.
- [ ] **Step 3:** `uv run pytest tests/test_sql_agent*.py -q`; ruff + mypy; grep for dangling references to the removed slots. Commit.

### Task 6: chat.py integration check + full quality gates

**Files:** Verify `backend/src/paperhub/api/chat.py` library_stats branch; run full suites.

- [ ] **Step 1:** confirm the branch forwards the new stream (round `tool_step`s + `search_results` + answer tokens) — it already handles `ToolStepYield`/`SearchResultsYield`/`str` (E1 Task 2). Adjust only if the shape changed.
- [ ] **Step 2:** full backend (`uv run pytest -q`, ruff, mypy) + full frontend (`npm test`, typecheck, lint, build). Commit any fixes.

### Task 7: Real-API gate (live `:8000`)

- [ ] **Step 1:** drive the live API (per CLAUDE.md — use the user's running backend; ask for a restart if prompts/code aren't loaded):
  - "list my papers about transformers" → **curated** cards (false positives DROPPED), each with a `reason`; answer summarizes; trace shows the agent selected then curated a smaller set.
  - a deliberately-broad query that warrants refinement → trace shows ≥1 refining `query` round before finalize.
  - "how many papers per year" → markdown table, `papers=[]`, no cards.
  - an out-of-scope SQL attempt (if reproducible) → `status='rejected'`.
- [ ] **Step 2:** read each run's trace (`paperhub-replay` / `tool_calls`) to confirm the rounds, the curated finalize, and the emitted payloads.
- [ ] **Step 3 — frontend human sign-off:** the user confirms in the app: curated cards + reasons render, Add works, aggregate table renders, no duplicate/false-positive cards.
- [ ] **Step 4 — ship:** bump SRS to v2.34.0 + add the Revision History row (final test counts); mark this plan + the E1 plan shipped; drop E1 from CLAUDE.md "Known follow-ups". Commit. (Push only on explicit approval.)

---

## Out of scope (YAGNI / deferred)
- Native LLM tool-calling for the sql MCP (structured-round keeps validation + tracing deterministic).
- A styled aggregate-table React component (markdown table stays).
- Vector/semantic library retrieval (none since v2.27).
- Multi-statement / write SQL (read-only allowlist unchanged).
