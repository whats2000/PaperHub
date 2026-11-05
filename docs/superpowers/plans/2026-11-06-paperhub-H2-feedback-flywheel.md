# Plan H2 — Feedback Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feedback that improves both the model and the system, at almost no effort for the user:
- one-click thumbs and preset reasons with a system guess;
- implicit flags confirmed by a reviewer agent, with only the uncertain ones asked of the user;
- a Feedback page that shows where the system goes wrong;
- Hugging Face SFT/DPO export, plus a bridge that turns failures into G1 to-label cases.

**Architecture:** One `feedback` table holds both explicit and implicit feedback, keyed by the answer's `run_id` (one assistant message per run). Pre-selection reads signals already recorded in the run's trace; there is no LLM call and no router change. An implicit trigger, either a keyword in the next user message or a fork, starts a background `feedback:review` run that is traced like any agent flow. The Feedback page reads aggregate endpoints. Export and the G1 inbox write files under `workspace/exports/`. Agent-step samples are CLI-only through the G1 harness, because `benchmark/` is not in the Docker image.

**Tech Stack:** FastAPI + aiosqlite + LiteLLM (`structured`), Tracer, `backend/benchmark/agent` (G1), React 19 + zustand + Base UI Popover + sonner + hand-rolled SVG charts (no chart library exists) + react-i18next, pytest / Vitest + RTL + MSW.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10 (shared constraints, UI rules), **§III-10.2 H2** (FR-18, FR-19), acceptance #9. Visual base: `docs/superpowers/designs/2026-11-05-plan-h/` (Feedback page: banner → full-page draft-and-submit card, stage master–detail, export dialog; thumbs popover).

**Depends on:** H0 merged (`useNavStore.feedbackBadge`, `FeedbackPage` slot, `PageHeader`, `Collapsible`, `usePresence`, `revealInScroll`, `paper_content.is_private`). **Branch:** `feat/plan-h2-feedback-flywheel` from `main` after H0.

**Scope moved to H3:** the **ideation feedback** block. It reads H3's idea lifecycle, so H3 adds it to this page.

**Parallel tracks.** Task 1 fixes the contract. After it:
- **A (backend):** Tasks 2–8
- **B (frontend):** Tasks 9–12, using MSW against the contract

Task 13 joins them.

## Global Constraints

- All UI strings through `t()` in four locales (new namespace `feedback`). Icons are lucide; no emoji.
- **No new UI colour.** Chart colours are exempt but **must pass the dataviz validator** (`validate_palette.js`, all-pairs, light and dark surfaces). Chart text uses text tokens.
- Motion follows user expectation, and every exit animates:
  - the confirmation card expands to the full page body and collapses back;
  - item changes **slide in the navigation direction**;
  - expanders collapse in place.
  CLAUDE.md *UI transitions*.
- **Stay in the page:** confirmation, dismissal and export complete on the Feedback page.
- The layout fills by flex, with one scroll container per region and no hard-coded heights of other UI.
- Agent-flow record principle: the review run records the trigger, the ids it read, the verdict, the reasons and the rationale. It never records the rendered prompt.
- Every LLM prompt is authored by invoking **`writing-agent-prompts`** first (≥ 2 variants × query set × judged).
- New env flags go in `backend/.env.example` in its one-line style; runtime settings go through the FR-14 registry.
- Single user: no keys or uniqueness that block a later user column.
- Python via `uv`. Conventional Commits with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done = automated gates green.** The live gate and merge-prep happen with the user.

## Review Focus

- **The keyword trigger must not fire on its own past answers.** It matches only the *user's* new message, and a message that merely quotes "wrong" inside a question ("is it wrong to …?") goes to the reviewer, which may reject it. Pinned in Tasks 3 and 4.
- **A review must never delay or break the chat turn.** The background task failing, timing out or hitting no LLM leaves the turn untouched and records an `error` step. Pinned in Task 4.
- **Thumbs changed from down to up** overwrite the row (one explicit feedback per message) and clear the reasons. Pinned in Task 2.
- **A DPO pair is emitted only when the fork's prompt is the same text as the original prompt.** An edited fork is a different prompt. Pinned in Task 7.
- **Export with "include private documents" off** must drop every sample whose cited passages come from an `is_private` paper. Pinned in Task 7.

---

## API contract (fixed by Task 1)

```
REASONS = wrong_route | wrong_source | citation | factual | off_topic | language_format   (+ promoted presets, + "other")
STAGE_OF_REASON: wrong_route→router, wrong_source→retrieval, citation→citation, factual→answer, off_topic→answer, language_format→answer

PUT    /feedback/runs/{run_id}          { polarity: "up"|"down"|null, reasons: [str], other_text?: str, correction?: str }   null clears
GET    /feedback/runs/{run_id}/guess    → { reasons: [str] }                        # trace-signal pre-selection
GET    /feedback/runs?session_id=       → [{ run_id, polarity, reasons }]           # restore thumbs on reload
GET    /feedback/queue                  → [{ feedback_id, run_id, session_id, session_title, papers: [str],
                                             earlier: [{role, content}], question, answer, flagged, next_reply: str|null,
                                             reviewer_doubt, guessed_reasons: [str] }]
POST   /feedback/queue/submit           { answers: [{ feedback_id, confirmed: bool, reasons: [str] }] } → { confirmed, rejected }
GET    /feedback/summary                → { kpis: {answers_with_feedback, approval_rate, negatives_explicit, negatives_implicit, pending},
                                             stages: [{ stage, explicit, implicit, weekly: [int,int,int,int], delta }],
                                             other_reasons: [{ text, count }] }
GET    /feedback/cases?stage=           → [{ feedback_id, run_id, session_id, question, kind, created_at }]
POST   /feedback/presets                { label, stage } → { label }                # promote an "other" text
GET    /feedback/presets                → [{ label, stage }]
POST   /feedback/g1-inbox               { feedback_ids: [int] } → { appended }
GET    /feedback/export/preview?…       → { sft, dpo, includes_private: bool }
POST   /feedback/export                 { include_implicit, include_private=true, since?, until?, model?, cited_only } → { dir, files: [str], sft, dpo }
GET    /feedback/exports/{dir}/{file}   → FileResponse
```

---

### Task 1: Contract — tables, models, types

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`, `backend/src/paperhub/db/migrate.py`
- Create: `backend/src/paperhub/feedback/__init__.py`, `backend/src/paperhub/feedback/models.py`
- Create: `frontend/src/types/feedback.ts`
- Test: `backend/tests/test_feedback_models.py`, `backend/tests/test_migrate.py`

**Interfaces:**
- Produces:
  - **`feedback` table** — `id, run_id NOT NULL REFERENCES runs(id) ON DELETE CASCADE, message_id REFERENCES messages(id) ON DELETE SET NULL, kind CHECK IN ('explicit','implicit'), polarity CHECK IN ('up','down'), reasons_json DEFAULT '[]', other_text, correction, trigger CHECK IN ('keyword','fork') NULL, verdict CHECK IN ('confirmed','rejected','uncertain') NULL, status CHECK IN ('final','needs_confirmation') DEFAULT 'final', reviewer_rationale, created_at, updated_at`. A partial unique index `ON feedback(run_id) WHERE kind='explicit'` enforces one explicit row per answer.
  - **`feedback_presets(id, label UNIQUE, stage, created_at)`.**
  - **`chat_sessions.forked_from_run_id INTEGER NULL REFERENCES runs(id) ON DELETE SET NULL`**, as an additive column. Without it, fork siblings cannot be paired for DPO.
  - Pydantic models for every body and response in the contract.
  - `REASONS`, `STAGE_OF_REASON` and `STAGES = ["router","retrieval","citation","answer"]` in `feedback/models.py`.
  - The TS mirrors.
- [ ] **Step 1: Write failing tests.**
  - The three DDL changes exist after `apply_schema` and are idempotent.
  - A second explicit row for the same run violates the partial index, while implicit rows are unconstrained.
  - Models reject an unknown polarity.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_models.py tests/test_migrate.py -k "feedback or forked_from_run" -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. Then run `npm run typecheck`.
- [ ] **Step 5: Commit** with `feat(feedback): fix the feedback API contract and tables`.

---

## Track A — backend

### Task 2: Explicit feedback and trace-signal pre-selection

**Files:**
- Create: `backend/src/paperhub/feedback/signals.py`, `backend/src/paperhub/feedback/repo.py`, `backend/src/paperhub/api/feedback.py`; Modify: `backend/src/paperhub/app.py` (include router)
- Test: `backend/tests/test_feedback_signals.py`, `backend/tests/test_feedback_api.py`

**Interfaces:**
- Produces:
  - `guess_reasons(conn, run_id) -> list[str]`. Deterministic, and reads only `runs` + `tool_calls`:
    - `wrong_route`: the router result has `intent == "clarify"`, **or** its `reasoning` matches `r"\bpriority\b|優先"`. The tie-break wording comes from the router v2 prompt; no router field exists for ties, and the spec forbids adding one.
    - `wrong_source`: every `research/paper_qa:subagent` step has `chunks_read == 0`.
    - `citation`: the assistant content has no `\[chunk:\s*\d+` marker on a `paper_qa` run.
    - `error` step → that step's stage reason: router → `wrong_route`, `paper_qa:*` → `wrong_source`, otherwise `factual`.
  - `upsert_explicit(conn, run_id, polarity | None, reasons, other_text, correction)`. Up clears the reasons; `None` deletes the explicit row.
  - The `PUT /feedback/runs/{run_id}`, `GET …/guess` and `GET /feedback/runs?session_id=` endpoints. The PUT returns 404 for an unknown run.
- [ ] **Step 1: Write failing tests.** Build fixture runs with recorded `tool_calls` for each guess branch, plus one clean run that guesses nothing. Also cover: down then up leaves one row with `polarity=up` and `reasons=[]`; `null` removes the row.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_signals.py tests/test_feedback_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. Then run ruff and mypy.
- [ ] **Step 5: Commit** with `feat(feedback): record thumbs feedback with a trace-based reason guess`.

### Task 3: Keyword trigger and reviewer prompt

**Files:**
- Create: `backend/src/paperhub/feedback/trigger.py`, `backend/src/paperhub/llm/prompts/feedback_review_v1.yaml`, `backend/benchmark/agent/prompts/feedback_review/` (≥ 2 variants + README) and a **query set of 16 turns**:
  - 6 real corrections across zh/en/ja, including "我是問第二篇論文" and "wrong, I asked about the decoder";
  - 5 non-corrections that contain a keyword, such as "is it wrong to use CEM here?" and "我是問題的提出者…";
  - 5 ambiguous turns that should come back `uncertain`.
- Test: `backend/tests/test_feedback_trigger.py`

**Interfaces:**
- Produces:
  - `DEFAULT_KEYWORDS` covers zh, en and ja: "不對, 錯了, 不是這個, 我是問, 答非所問, wrong, that's not, not what i asked, incorrect, 違う, 間違, そうじゃない".
  - `keywords_from_env() -> list[str]` reads `PAPERHUB_FEEDBACK_KEYWORDS`, a comma-separated list that falls back to the defaults.
  - `matches_trigger(user_message: str, keywords) -> bool` is a case-insensitive substring match.
  - Slot `"feedback_review/v1"` takes `{question, answer, user_followup, trace_summary, reasons}`, each in XML tags. Its response model is `ReviewVerdict{verdict: Literal["confirmed","rejected","uncertain"], reasons: list[str], doubt: str}`, where `doubt` is one sentence shown to the user as the reviewer's concern.
- [ ] **Step 1: Write failing trigger tests.** Each default keyword matches, matching is case-insensitive, an env override replaces the list, and an empty message never matches.
- [ ] **Step 2: Implement the trigger.** Run `uv run pytest tests/test_feedback_trigger.py -v`. Expected: PASS.
- [ ] **Step 3: Invoke `writing-agent-prompts`.** Write ≥ 2 variants and run the judged comparison on the query set using `backend/benchmark/agent/`. Score:
  - verdict accuracy against labels (deterministic)
  - pairwise judgment on the `doubt` sentence
  Record the table in the README, then ship the winner as `feedback_review_v1.yaml`.
- [ ] **Step 4: Commit** with `feat(feedback): add the correction trigger and the reviewer prompt`.

### Task 4: Background review run

**Files:**
- Create: `backend/src/paperhub/feedback/review.py`
- Modify: `backend/src/paperhub/api/chat.py` (in `run_agent`, right after `_record_user_message` at :615), `backend/src/paperhub/db/fork.py` (set `forked_from_run_id`), `backend/src/paperhub/api/sessions.py` (`fork_session_endpoint` schedules the fork trigger)
- Modify: `backend/src/paperhub/config.py`, `settings_registry.py`, `backend/.env.example` (`# 4. Agent tunables`), all four frontend `settings.json`
- Test: `backend/tests/test_feedback_review.py`, `backend/tests/test_fork.py`

**Interfaces:**
- Consumes: `matches_trigger`, `ReviewVerdict`, `guess_reasons`, `Tracer`.
- Produces: `schedule_review(*, db_path, session_id, target_run_id, followup: str | None, trigger: "keyword" | "fork", llm, model) -> None`. It fires an `asyncio.create_task` held in a module-level task set, using the `_live_tasks` pattern. The task:
  1. opens its own connection;
  2. inserts a `runs` row for the session (status `running`);
  3. traces one step `agent="feedback", tool="feedback:review"` with `record_args({target_run_id, trigger, followup, answer_message_id})` and `record_result({verdict, reasons, doubt, guessed_reasons, step_ids_read})`;
  4. writes an `implicit` / `down` feedback row with `status = "needs_confirmation"` when the verdict is `uncertain`, otherwise `final`;
  5. finalises the run as `ok`, or as `error` on any exception, which it never re-raises.

  Rejected verdicts are kept. The review run has no messages, so it never shows in chat.
  - **Keyword hook:** in `run_agent`, after `_record_user_message`, when the setting is on and `matches_trigger(req.user_message)` holds, target the latest previous `assistant` message's `run_id`.
  - **Fork hook:** `fork_session` stores `forked_from_run_id = fork_run_id`. The fork endpoint schedules a review of that run with `followup=None`.
  - **Settings:**
    - `PAPERHUB_FEEDBACK_REVIEW` (`agents_memory`, bool, default `"1"`, "Ask a reviewer agent whether a correction-like message means the previous answer was wrong.")
    - `PAPERHUB_FEEDBACK_KEYWORDS` (`agents_memory`, string, advanced)
    - `PAPERHUB_FEEDBACK_REVIEW_MODEL` (`models_providers`, string, advanced, defaults to the small tier)

    Each is one `.env.example` line in the file's style.
- [ ] **Step 1: Write failing tests.**
  - A keyword message schedules a review targeting the previous answer's run.
  - A non-matching message schedules nothing.
  - An `uncertain` stub verdict gives a `needs_confirmation` row, and `rejected` gives a stored rejected row.
  - An LLM exception gives an `error` step, a run with status `error` and no feedback row, while the chat turn still completes `ok`.
  - A fork stores `forked_from_run_id` and schedules a `fork` review.
  - The setting off disables both hooks.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_review.py tests/test_fork.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. Then run `npx vitest run src/locales/parity.test.ts`.
- [ ] **Step 5: Commit** with `feat(feedback): review correction-like turns in the background`.

### Task 5: Confirmation queue with full conversation context

**Files:**
- Modify: `backend/src/paperhub/feedback/repo.py`, `backend/src/paperhub/api/feedback.py`
- Test: `backend/tests/test_feedback_queue.py`

**Interfaces:**
- Produces `GET /feedback/queue` and `POST /feedback/queue/submit` per the contract. Each queue item carries:
  - `session_title`
  - `papers`: titles of the session's enabled papers
  - `earlier`: every prior user/assistant message of the session before `question`, oldest first
  - `question` / `answer`: the target run's user and assistant messages
  - `flagged`: the follow-up user message, for a keyword trigger; for a fork it is the fork's first new user message, or `""`
  - `next_reply`: the assistant message after `flagged`, if any
  - `reviewer_doubt`
  - `guessed_reasons`: the verdict reasons, falling back to `guess_reasons`

  Submit behaviour:
  - It sets `status=final`, with `verdict=confirmed` and `reasons` when the answer is confirmed, otherwise `verdict=rejected`.
  - Unknown ids return 404.
  - Only the submitted items change.
- [ ] **Step 1: Write failing tests.**
  - The queue item has the full context, in the correct order.
  - `next_reply` is null when the user has not continued.
  - Submitting 2 of 3 items leaves 1 in the queue.
  - Confirmed reasons are stored.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_queue.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(feedback): serve the confirmation queue with full context`.

### Task 6: Summary, cases and promoted presets

**Files:**
- Modify: `backend/src/paperhub/feedback/repo.py`, `backend/src/paperhub/api/feedback.py`
- Test: `backend/tests/test_feedback_summary.py`

**Interfaces:**
- Produces `GET /feedback/summary`, `GET /feedback/cases` and `GET/POST /feedback/presets`. The definitions:
  - **Negatives:** confirmed implicit plus explicit `down` rows.
  - **Approval rate:** explicit up ÷ explicit total, or `null` when there is no explicit feedback.
  - **Weekly:** counts for the last 4 ISO weeks, oldest first, with `delta = weekly[3] - weekly[2]`.
  - **Stages:** the counts by `STAGE_OF_REASON`. Promoted presets carry their own stage.
  - **Other reasons:** grouped by trimmed lower-case `other_text`, with counts.
  - **Presets:** `POST /feedback/presets` with an existing label returns 409. Presets are added to the reasons the popover shows.
- [ ] **Step 1: Write failing tests** on a fixture with known rows covering each KPI, the weekly bucketing across a week boundary, the stage mapping, a promoted preset counted in its stage, and the 409.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_summary.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(feedback): aggregate feedback by stage for the analysis page`.

### Task 7: Hugging Face export

**Files:**
- Create: `backend/src/paperhub/feedback/export.py`; Modify: `backend/src/paperhub/config.py` (`exports_dir = workspace/"exports"`), `backend/src/paperhub/api/feedback.py`
- Test: `backend/tests/test_feedback_export.py`

**Interfaces:**
- Produces `build_export(conn, opts) -> ExportPlan`, giving the sample lists and counts, and `write_export(plan, exports_dir) -> Path`, which writes `workspace/exports/<YYYYmmdd-HHMMSS>/`. The files:
  - **`sft.jsonl`:** one line per thumbs-up answer, `{"messages": [system?, …history, user, assistant]}`. The cited passages go in a system message `"<sources>…</sources>"` built from the `chunks` rows cited by `[chunk:N]`.
  - **`dpo.jsonl`:** `{"prompt": messages, "chosen": [assistant], "rejected": [assistant]}`. Two sources:
    1. A thumbs-down answer with a `correction`; the correction is chosen.
    2. Fork siblings: the original run (`chat_sessions.forked_from_run_id`) and the fork's first answer **when the prompts are identical after trimming** and the two answers have opposite explicit polarity.
  - **`README.md`:** the dataset card, with counts, the options, a "contains document text" warning, and "includes private documents" when it does.
  - Options filter by implicit, date, model (`tool_calls.model` of the answering step) and `cited_only`. With `include_private=false`, any sample citing a chunk of an `is_private` paper is dropped.
  - `GET /feedback/export/preview` returns the same counts without writing anything. `GET /feedback/exports/{dir}/{file}` serves files only from inside `exports_dir`, with a traversal guard.
- [ ] **Step 1: Write failing tests.**
  - The SFT line has a sources system message.
  - An edited fork prompt produces no DPO pair, while an identical one does.
  - A correction produces a pair.
  - `include_private=false` drops the private-cited sample.
  - The preview counts equal the written counts.
  - `../` in the download path returns 400 or 404.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_export.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(feedback): export SFT and DPO datasets in Hugging Face format`.

### Task 8: G1 bridge — inbox, import, agent-step export

**Files:**
- Modify: `backend/src/paperhub/api/feedback.py` (`POST /feedback/g1-inbox`), `backend/src/paperhub/feedback/export.py`
- Modify: `backend/benchmark/agent/cli.py` (new subcommands), `backend/benchmark/agent/corpus.py`
- Test: `backend/tests/test_feedback_g1_inbox.py`, `backend/tests/benchmark_agent/test_inbox_import.py`

**Interfaces:**
- Produces:
  - **`POST /feedback/g1-inbox`** appends one line per feedback id to `workspace/exports/g1-inbox.jsonl`. Each line is `{case_id: "fb<id>-run<run_id>", stage_hint: STAGE_OF_REASON of the first reason, reasons, correction, source_run_id, variables: <the router step's recorded args>, observed: <its recorded result>, expect: {}}`. The append is idempotent per `case_id`.
  - **`python -m benchmark.agent.cli import-inbox --inbox <path> --stage router --out benchmark/agent/corpus/router.harvest.jsonl`** imports only lines whose `expect` was filled by a human, as `CorpusCase` rows. It skips unlabelled lines and reports the count.
  - **`python -m benchmark.agent.cli export-steps --db <path> --stage router --out <file>`** reconstructs `messages` samples from recorded state through `replay.render_messages` for registered stages (router today). This is the CLI-only agent-step export.
- [ ] **Step 1: Write failing tests.**
  - Inbox lines carry the recorded router args, and appending the same id twice keeps one line.
  - `import-inbox` skips empty `expect` and imports labelled lines.
  - `export-steps` renders a sample whose user turn contains the recorded `user_message`.
- [ ] **Step 2: Run.** `uv run pytest tests/test_feedback_g1_inbox.py tests/benchmark_agent/test_inbox_import.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(eval): bridge negative feedback into the G1 corpus`.

---

## Track B — frontend

### Task 9: Thumbs and reasons popover

**Files:**
- Create: `frontend/src/components/chat/FeedbackButtons.tsx`, `frontend/src/store/feedback.ts`; Modify: `frontend/src/components/chat/MessageBubble.tsx` (the action row, :212-250), `frontend/src/lib/api.ts`
- Create: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/feedback.json` and register it in `lib/i18n.ts`
- Test: `frontend/src/components/chat/FeedbackButtons.test.tsx`

**Interfaces:**
- Produces: `FeedbackButtons({ runId })`, shown for completed assistant messages next to Copy.
  - **Thumbs up** is one click, and clicking it again clears it.
  - **Thumbs down** opens a Popover listing:
    - the preset reasons as multi-select toggle chips (`aria-pressed`, `bg-accent` when on), with the guessed ones pre-selected and tagged "system guess";
    - promoted presets;
    - "Other reason", which reveals a textarea through `Collapsible`;
    - a collapsed "Correct answer (optional)" field;
    - Submit.
  - Submitting saves, closes with the exit animation and shows a toast.
  - `useFeedbackStore` holds `byRun` and is hydrated from `GET /feedback/runs?session_id=` whenever the active session changes.
- [ ] **Step 1: Write failing tests.**
  - Thumbs up sends `PUT {polarity:"up"}`, and a second click sends `null`.
  - Thumbs down pre-selects the guessed chips.
  - Choosing "Other reason" reveals the textarea.
  - Submit sends the reasons and text, and the popover stays in the DOM during exit, gone after 150 ms.
  - Reload state restores the pressed thumb.
- [ ] **Step 2: Run.** `npx vitest run src/components/chat/FeedbackButtons.test.tsx src/locales/parity.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): add thumbs feedback with preset reasons`.

### Task 10: Feedback page, part 1 — badge, banner, confirmation card

**Files:**
- Create: `frontend/src/hooks/useFeedbackBadge.ts`, `frontend/src/components/feedback/ConfirmQueue.tsx`, `frontend/src/components/feedback/ConversationContext.tsx`; Modify: `frontend/src/pages/FeedbackPage.tsx`, `frontend/src/App.tsx` (mount the badge hook)
- Test: `frontend/src/components/feedback/ConfirmQueue.test.tsx`, `frontend/src/hooks/useFeedbackBadge.test.ts`

**Interfaces:**
- Consumes: `useNavStore.setFeedbackBadge`, `Collapsible`, `revealInScroll`.
- Produces:
  - **`useFeedbackBadge()`** sets the badge from `GET /feedback/queue` length on mount, after each finished turn, and every 60 s while the tab is visible (the `visibilitychange` pause pattern from `useRunReattach`).
  - **`ConfirmQueue` banner:** "N items need your confirmation" plus a muted line, with **Start confirming / Collapse**.
  - **Expanding:**
    - The page becomes `flex flex-col`; the queue card grows from `flex-grow:0` to `1` and the analysis region from `1` to `0`, transitioned over ~280 ms, which pushes the analysis out.
    - Inside the card, the header (question, reviewer doubt, ‹ ›, numbered dots showing ✓/✗, progress bar) and the submit row stay fixed. **Only the conversation scrolls.**
  - **`ConversationContext`:**
    - shows the session title, papers and run id;
    - `earlier` is shown and collapsible in place;
    - the questioned answer is labelled, `flagged` outlined, and `next_reply` shown muted with a left rule;
    - long assistant texts are clamped (2 lines for context, 3 for the questioned answer) with "Show full text", which appears only when the text is truncated (`scrollHeight > clientHeight`);
    - opening an item scrolls the conversation to the questioned exchange.
  - **Answers are a draft:**
    - Y/N and the reason chips record an answer, which auto-advances to the next unanswered item.
    - ‹ ›, ←/→ and the dots navigate. Item changes **slide horizontally in the navigation direction** (out ∓32 px, in ±32 px, ~180 ms ease-out, header fixed).
    - **Submit N** (Ctrl+Enter) posts only the answered items. The rest stay queued.
    - When the queue is empty, an inline "all done" message fades out.
- [ ] **Step 1: Write failing tests.**
  - The badge equals the queue length, and polling stops while the tab is hidden.
  - Answering advances to the next unanswered item.
  - ← returns and keeps the answer selected.
  - Revising works.
  - Submit sends only the answered items and the queue shrinks.
  - The slide direction class is the left class on next and the right class on previous.
  - "Show full text" appears only for truncated text (mock `scrollHeight`).
  - The analysis region is not in the accessibility tree while expanded (`aria-hidden`).
- [ ] **Step 2: Run.** `npx vitest run src/components/feedback src/hooks/useFeedbackBadge.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): confirm uncertain feedback in place with full context`.

### Task 11: Feedback page, part 2 — stage analysis

**Files:**
- Create: `frontend/src/components/feedback/StageAnalysis.tsx`, `frontend/src/components/feedback/StageCases.tsx`, `frontend/src/components/feedback/charts.tsx`; Modify: `frontend/src/index.css` (two chart tokens `--viz-explicit`, `--viz-implicit` for light and dark)
- Test: `frontend/src/components/feedback/StageAnalysis.test.tsx`

**Interfaces:**
- Produces:
  - **KPI row:** answers with feedback, approval rate (`—` when null), negatives (explicit · implicit), pending.
  - **Stage list (left):** one row per stage, holding:
    - a stacked SVG bar for explicit and implicit, with a 2 px surface gap between the two parts;
    - the total;
    - a 4-week sparkline with an emphasised endpoint;
    - the week-over-week delta.

    The rows have hover values and a legend; a "Table" toggle shows the same data as a table. **The top stage is preselected.**
  - **Detail (right, a permanent pane):** `StageCases` lists the selected stage's cases with checkboxes, "View conversation" (`selectSession`) and "View trace". **Send to G1 to-label (N)** posts the checked ids and toasts the appended count.
  - **"Other reasons":** each row shows its count and a **Promote to preset** action, which opens an inline stage picker (no navigation away).
- [ ] **Step 1: Load the `dataviz` skill.** Pick the two series colours, then **run `validate_palette.js` with `--pairs all` against the light and dark card surfaces** until both pass. Put the passing values in the tokens, and record the validator output in the commit body.
- [ ] **Step 2: Write failing tests.**
  - The top stage is preselected and its cases are shown.
  - Selecting another stage swaps the detail.
  - Bar widths are proportional to the counts (assert the `width` attributes).
  - The table toggle shows the same numbers.
  - Send to G1 posts the checked ids.
  - Promote posts `{label, stage}`.
- [ ] **Step 3: Run.** `npx vitest run src/components/feedback/StageAnalysis.test.tsx`. Expected: FAIL.
- [ ] **Step 4: Implement.** Run again. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): show where the system goes wrong by stage`.

### Task 12: Export dialog

**Files:**
- Create: `frontend/src/components/feedback/ExportDialog.tsx`; Modify: `frontend/src/pages/FeedbackPage.tsx` (header action "Export training data")
- Test: `frontend/src/components/feedback/ExportDialog.test.tsx`

**Interfaces:**
- Produces:
  - The dialog follows the existing modal pattern (`SettingsModal`), with fade + zoom on enter and exit.
  - Options: include implicit, **include private documents (on by default)**, date range, model, cited only.
  - A **live summary** from `/feedback/export/preview`, debounced 300 ms, shows the SFT and DPO counts and a warning line. When private documents are included, the warning says so explicitly.
  - **Export** writes the files, then lists them as links to `/feedback/exports/{dir}/{file}`.
- [ ] **Step 1: Write failing tests.**
  - Toggling an option refetches the preview.
  - The private warning appears only when `includes_private` is true.
  - Export shows the file links.
  - Close animates out (still in the DOM right after close, gone after 150 ms).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): export training data from the feedback page`.

---

### Task 13: Join and gates

**Files:**
- Modify: `CLAUDE.md` (plan table: an H2 row)
- Test: `frontend/src/pages/FeedbackPage.test.tsx` covers the full MSW flow: badge → page → confirm two items → submit → summary refetched.

- [ ] **Step 1: Write the integration test and wire anything missing.** Run `npx vitest run src/pages/FeedbackPage.test.tsx`. Expected: PASS.
- [ ] **Step 2: Backend gates.** From `backend/`: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`. Expected: green.
- [ ] **Step 3: Frontend gates.** From `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Expected: green.
- [ ] **Step 4: Commit** with `feat(feedback): connect the feedback flywheel end to end`.

**Stop here.** The morning session with the user covers:
1. The live gate on `:8000`:
   - thumbs on a real answer;
   - a correction in zh, en and ja that triggers a review (trace `feedback:review` via `paperhub-replay`);
   - the queue confirm flow;
   - export files opened and checked;
   - the inbox imported with `import-inbox`.
2. Visual sign-off against the prototype.
3. `paperhub-merge-prep`.
