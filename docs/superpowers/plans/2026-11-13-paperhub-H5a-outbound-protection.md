# H5a — Outbound Protection and On-Prem Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every outbound request that carries the user's text passes one checkpoint that can allow, rewrite, ask or block it; protections are toggles that are on in the docker deployment, lockable by the deployer, never remove a capability, and leave an exportable log.

**Architecture:** A new `paperhub.outbound` package owns the policy (settings + profile defaults + locks), the destination allowlist (enforced at every HTTP client), the text checkpoint (allowlist → exact-id → sensitive terms → LLM review against this turn's private material → unsure policy), the append-only outbound log and the held-request queue. The two free-text call sites of the paper_search pipeline call the checkpoint before sending; the core agent's `search_papers` turns held/rewritten/blocked decisions into SSE events and cards. Frontend adds a Privacy & outbound settings category, an Outbound log section with a Held tab, a sidebar "Held searches" entry, and three chat elements.

**Tech Stack:** FastAPI + SQLite (`schema.sql` + `migrate.py`), httpx, LiteLLM via `LlmAdapter.structured`, the H4 `Tracer`; React + Zustand + react-i18next + lucide-react; Vitest/RTL/MSW; pytest.

**Spec:** [SRS §III-10.5 H5a](../specs/2026-05-17-paperhub-srs.md) (FR-22, acceptance I-8 #11), issues #48 #49. **Visual base:** [`designs/2026-11-13-plan-h5a/`](../designs/2026-11-13-plan-h5a/README.md) — signed off 2026-11-13; match its layout, states, copy and motion.

## Global Constraints

- **Protection never disables a capability** (SRS §III-10.5 Boundary): a safe way plus a risky way the user can switch on, off by default in deployment, with a plain warning of what leaves the device.
- **Dev behaviour is unchanged.** With `PAPERHUB_PROFILE` unset (dev), every new setting defaults to today's behaviour: review off, public web search on, allowlist `*`, private-upload default off, no locks.
- **Deployment profile** = `PAPERHUB_PROFILE=deployment`, set in `docker-compose.yml` for `backend`. Defaults there: review on, unsure = ask, public web search off, allowlist `export.arxiv.org, arxiv.org, api.semanticscholar.org` + the trusted search host, private-upload default on, answer review (H5e) not touched.
- **Locks:** `PAPERHUB_LOCKED_SETTINGS` (comma-separated registry keys) makes those keys env-only: `PATCH /settings` → 403, the DB overlay ignores them, the UI shows them disabled with "Set by the deployer". Compose default locks: `PAPERHUB_OUTBOUND_REVIEW, PAPERHUB_OUTBOUND_UNSURE, PAPERHUB_OUTBOUND_ALLOWLIST, PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS`.
- **Checkpoint stages, cheapest first:** destination allowlist → exact-identifier bypass (arXiv id, DOI, S2 id) → sensitive terms (case-insensitive) → LLM review (allow / rewrite / ask / block) with private material wrapped as data → cache per (text, private-material ids) → timeout and errors follow the unsure policy (`ask` default, or `block`); **never silently allow on failure**. Unattended callers turn `ask` into `hold`.
- **The user's own typed message is not private-derived by itself;** private material = private documents attached to the chat, chunk text read from them this turn, and the idea's problem text in an idea chat.
- **Outbound log:** one row per outbound request (time, owner, actor, caller, kind, destination, sent text, original if rewritten, decision, stage, reason, run id, session id); no foreign keys (session deletion never removes rows); append-only through the API; retention `PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS` (365); CSV export. Calls to a **non-local LLM provider** are logged by destination only, content `(content not logged)`, when review is on.
- **New H5 tables carry `owner_id INTEGER NOT NULL DEFAULT 1` and audit rows an `actor TEXT`** (`user` / `agent` / `system` / `deployer`).
- **Readiness warns, never refuses,** naming every setting that points at a non-local model.
- **Agent-flow record principle:** every checkpoint decision inside a run is a `tool_calls` step `agent="outbound", tool="outbound:review"` recording request, stage, decision, sent text, reason and the private-material ids — never the rendered prompt.
- **Prompts:** `outbound_review/v1` is authored by an agent that **invokes `writing-agent-prompts` first** (≥ 2 variants × leak + benign sets × judged) on the configured deploy model; results from a cloud dev model are recorded as a dev proxy.
- **Every new env var lands in `backend/.env.example`** in its numbered section, one-line comment style. Every UI string goes through `t()` in en / zh-TW / zh-CN / ja. Every appear/disappear animates (UI transitions rule). No new UI colour: amber = notices, red = blocked/destructive.
- Commits: Conventional Commits, one concern per commit, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Per task run only the relevant tests + targeted ruff/mypy; the full suites at the end.

## Review Focus

1. **The review model is down, slow or returns garbage** → the request follows the unsure policy (ask or block); it is never sent unreviewed. *(Task 6 test `test_review_error_follows_unsure_policy`, `test_review_timeout_follows_unsure_policy`.)*
2. **A sensitive term hides in case, punctuation or a longer word** ("kestrel's", "KESTREL-v3") → blocked; an unrelated word that merely contains the letters inside a longer token on a word boundary rule is not. *(Task 6 `test_terms_match_case_and_punctuation`.)*
3. **A held search whose chat was stopped or deleted** → Send answers 409 and nothing leaves. *(Task 9 `test_send_refused_after_stop_or_delete`.)*
4. **A locked setting changed through the API or a stale DB row** → 403 / ignored at boot. *(Task 2 `test_patch_locked_key_403`, `test_overlay_skips_locked_key`.)*
5. **An open-access PDF on a host outside the allowlist** → the existing manual-download card, not a 500, and a `blocked` log row. *(Task 4 `test_oa_download_blocked_host_gives_manual_card`.)*

---

### Task 1: Profile, outbound settings and env docs

**Files:**
- Create: `backend/src/paperhub/outbound/__init__.py`, `backend/src/paperhub/outbound/policy.py`
- Modify: `backend/src/paperhub/settings_registry.py` (new category `privacy_outbound`, new type `string_list`, `SettingField.profile_defaults`), `backend/.env.example` (new section `9. Privacy & outbound`), `docker-compose.yml` (`backend.environment`)
- Test: `backend/tests/outbound/test_policy.py`, `backend/tests/test_settings_registry.py`

**Interfaces:**
- Produces:
  - `SettingField.profile_defaults: Mapping[str, str] = {}` and `effective_default(field, profile: str) -> str | None`.
  - `current_profile() -> Literal["dev", "deployment"]` (reads `PAPERHUB_PROFILE`).
  - `@dataclass(frozen=True) class OutboundPolicy: review_enabled: bool; unsure: Literal["ask","block"]; terms: tuple[str, ...]; allowlist: frozenset[str] | None  # None = allow all; public_web_search: bool; trusted_search_url: str | None; private_upload_default: bool; log_retention_days: int; review_model: str; review_timeout_s: float`
  - `load_policy(env: Mapping[str, str] | None = None) -> OutboundPolicy`
  - Registry keys (category `privacy_outbound`): `PAPERHUB_OUTBOUND_REVIEW` bool (dev `0`, deployment `1`); `PAPERHUB_OUTBOUND_UNSURE` enum `ask|block` (`ask`); `PAPERHUB_SENSITIVE_TERMS` string_list (``); `PAPERHUB_PRIVATE_UPLOAD_DEFAULT` bool (dev `0`, deployment `1`); `PAPERHUB_TRUSTED_SEARCH_URL` string (``); `PAPERHUB_PUBLIC_WEB_SEARCH` bool (dev `1`, deployment `0`); `PAPERHUB_OUTBOUND_ALLOWLIST` string_list (dev `*`, deployment `export.arxiv.org,arxiv.org,api.semanticscholar.org`); `PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS` int (`365`, min 7); `PAPERHUB_OUTBOUND_REVIEW_MODEL` string (default = small tier); `PAPERHUB_OUTBOUND_REVIEW_TIMEOUT` int seconds (`8`, advanced).
  - `string_list` values are stored as one string, items separated by newline or comma; `parse_string_list(value: str) -> tuple[str, ...]` (trimmed, empty dropped, de-duplicated case-insensitively, order kept).

- [ ] **Step 1: Write the failing tests** — `test_dev_profile_defaults_match_today` (no env → `review_enabled False`, `public_web_search True`, `allowlist is None`, `private_upload_default False`); `test_deployment_profile_defaults` (`PAPERHUB_PROFILE=deployment` → `True, "ask", False`, allowlist `{"export.arxiv.org","arxiv.org","api.semanticscholar.org"}`, `private_upload_default True`); `test_explicit_env_beats_profile_default`; `test_parse_string_list` (`"KESTREL, project osprey\nKestrel"` → `("KESTREL","project osprey")`); `test_registry_has_privacy_category` (all ten keys present, category label `"Privacy & outbound"`).
- [ ] **Step 2: Run** `uv run pytest tests/outbound/test_policy.py tests/test_settings_registry.py -v` — expect FAIL (module missing).
- [ ] **Step 3: Implement** `policy.py` and the registry changes; `.env.example` section 9 lines (`# Deployment profile: dev or deployment.` / `# PAPERHUB_PROFILE=dev`, and one line + commented default per key above, plus `PAPERHUB_LOCKED_SETTINGS`); compose `backend.environment`: `PAPERHUB_PROFILE=deployment`, `PAPERHUB_LOCKED_SETTINGS=PAPERHUB_OUTBOUND_REVIEW,PAPERHUB_OUTBOUND_UNSURE,PAPERHUB_OUTBOUND_ALLOWLIST,PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS`.
- [ ] **Step 4: Run** the same tests + `uv run ruff check src tests` + `uv run mypy src/paperhub/outbound src/paperhub/settings_registry.py` — PASS.
- [ ] **Step 5: Commit** `feat(outbound): add the outbound policy, deployment profile and settings`.

### Task 2: Deployer locks

**Files:**
- Modify: `backend/src/paperhub/settings_overlay.py` (`apply_overlay` skips locked keys), `backend/src/paperhub/api/settings.py` (GET adds `locked: bool` per field; PATCH a locked key → 403 `{"detail": "locked", "keys": [...]}`), `backend/src/paperhub/outbound/policy.py`
- Test: `backend/tests/api/test_settings_locks.py`

**Interfaces:**
- Produces: `locked_keys(env: Mapping[str, str] | None = None) -> frozenset[str]`; GET field gains `"locked": bool`.

- [ ] **Step 1: Write the failing tests** — `test_get_marks_locked_fields`; `test_patch_locked_key_403` (body `{"PAPERHUB_OUTBOUND_REVIEW": "0"}` → 403, env value unchanged, no `settings` row written); `test_patch_mixed_locked_and_free_rejects_whole_patch`; `test_overlay_skips_locked_key` (a stale DB row for a locked key does not change `os.environ` at boot); `test_unlocked_keys_still_patch`.
- [ ] **Step 2: Run** `uv run pytest tests/api/test_settings_locks.py -v` — FAIL.
- [ ] **Step 3: Implement.** A locked key's value is whatever the environment has; the PATCH check runs before any write.
- [ ] **Step 4: Run** the tests + existing `tests/api/test_settings*.py` — PASS.
- [ ] **Step 5: Commit** `feat(settings): let the deployer lock settings to the environment`.

### Task 3: Outbound log and held-request tables

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`
- Create: `backend/src/paperhub/outbound/log.py`, `backend/src/paperhub/outbound/held.py`, `backend/src/paperhub/api/outbound.py` (router mounted in `app.py`)
- Test: `backend/tests/outbound/test_log.py`, `backend/tests/outbound/test_held_store.py`, `backend/tests/api/test_outbound_api.py`

**Interfaces:**
- Produces:
  - Tables (`CREATE TABLE IF NOT EXISTS`, no FKs): `outbound_log(id INTEGER PK, created_at TEXT NOT NULL, owner_id INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, caller TEXT NOT NULL, kind TEXT NOT NULL, destination TEXT NOT NULL, sent_text TEXT, original_text TEXT, decision TEXT NOT NULL CHECK (decision IN ('sent','rewritten','asked_sent','asked_discarded','blocked','held')), stage TEXT NOT NULL, reason TEXT, run_id INTEGER, session_id INTEGER)` + index on `created_at`; `held_requests(id TEXT PK, created_at TEXT, owner_id INTEGER NOT NULL DEFAULT 1, caller TEXT, kind TEXT, destination TEXT, text TEXT NOT NULL, reason TEXT, unattended INTEGER NOT NULL, run_id INTEGER, session_id INTEGER, status TEXT NOT NULL CHECK (status IN ('pending','sent','discarded')), sent_text TEXT, log_id INTEGER)`; `outbound_review_cache(key TEXT PK, decision_json TEXT, created_at TEXT)`.
  - `log.append(conn, *, actor, caller, kind, destination, decision, stage, sent_text=None, original_text=None, reason=None, run_id=None, session_id=None) -> int`; `log.query(conn, *, decision=None, destination=None, since=None, limit=200) -> list[LogRow]`; `log.to_csv(rows) -> str`; `log.prune(conn, retention_days: int) -> int`.
  - `held.create(conn, *, caller, kind, destination, text, reason, unattended, run_id, session_id) -> str`; `held.list_pending(conn) -> list[HeldRow]`; `held.count_pending(conn) -> int`; `held.mark(conn, held_id, status, *, sent_text=None, log_id=None) -> None`.
  - API: `GET /outbound/log?decision=&destination=&since=&limit=`, `GET /outbound/log.csv`, `GET /outbound/held`, `GET /outbound/held/count` → `{"count": n}`. **No delete or update endpoint for the log.**
  - Retention: `prune` runs once at startup and daily in a small loop started in `app.py` (pattern of `run_citation_loop`).

- [ ] **Step 1: Write the failing tests** — `test_append_and_query_filters`; `test_csv_has_header_and_escapes_quotes`; `test_prune_removes_only_older_rows`; `test_rows_survive_session_purge` (insert a log row with a session id, run `purge_deleted_sessions`, row still there); `test_held_lifecycle`; `test_api_has_no_mutating_log_routes` (`DELETE`/`PATCH /outbound/log/1` → 405/404); `test_held_count_endpoint`.
- [ ] **Step 2: Run** the three test files — FAIL.
- [ ] **Step 3: Implement** the tables, modules, router and retention loop.
- [ ] **Step 4: Run** the tests + `tests/db` — PASS.
- [ ] **Step 5: Commit** `feat(outbound): add the outbound log and held-request store`.

### Task 4: Destination allowlist at every HTTP client

**Files:**
- Create: `backend/src/paperhub/outbound/destinations.py`
- Modify: `pipelines/semantic_scholar.py`, `pipelines/arxiv_client.py`, `pipelines/unpaywall.py`, `pipelines/paper_pipeline.py` (`ingest_pdf_from_url`), `pipelines/page_pdf.py` (`fetch_arxiv_pdf`), `pipelines/arxiv_metadata_backfill.py` (also add `AND pc.is_private = 0` to its row query), `api/version.py`, `mcp/registry.py` (the `web` server is skipped while `public_web_search` is off), `api/outbound.py` (`GET /outbound/destinations`)
- Test: `backend/tests/outbound/test_destinations.py`, `backend/tests/pipelines/test_oa_blocked_host.py`, `backend/tests/test_arxiv_metadata_backfill.py`

**Interfaces:**
- Consumes: `load_policy`, `log.append`.
- Produces:
  - `class OutboundBlocked(Exception): destination: str; reason: str`.
  - `check_destination(url: str, *, caller: str, kind: str, conn: sqlite3.Connection | None = None) -> None` — raises `OutboundBlocked` (and logs a `blocked` row, stage `allowlist`) when the host is not allowed. The trusted search host is always allowed; the pseudo-destination `public-web-search` is allowed iff `public_web_search`.
  - `GET /outbound/destinations` → `[{host, source: "paper_source"|"trusted_search"|"public_search"|"deployer"}]`.

- [ ] **Step 1: Write the failing tests** — `test_allow_all_in_dev`; `test_deployment_blocks_unlisted_host`; `test_trusted_host_always_allowed`; `test_subdomain_not_implied` (`evil.arxiv.org.attacker.com` blocked); `test_oa_download_blocked_host_gives_manual_card` (an `ss:` paper whose only OA URL is on an unlisted host returns the existing manual-download result, not an exception, and logs `blocked`); `test_web_server_skipped_when_public_search_off`; `test_backfill_skips_private_rows`.
- [ ] **Step 2: Run** them — FAIL.
- [ ] **Step 3: Implement** `check_destination` and call it at the top of each client function before the request; callers already handling network errors treat `OutboundBlocked` like an unreachable source.
- [ ] **Step 4: Run** the tests + `tests/pipelines tests/test_arxiv_client.py tests/test_unpaywall*.py tests/mcp` — PASS.
- [ ] **Step 5: Commit** `feat(outbound): enforce the destination allowlist at every client`.

### Task 5: Trusted search service

**Files:**
- Create: `backend/src/paperhub/outbound/trusted_search.py`
- Modify: `backend/src/paperhub/agents/research_pipeline.py` (`discover_canonical` chooses trusted → public `web.search` → none)
- Test: `backend/tests/outbound/test_trusted_search.py`, `backend/tests/agents/test_discover_search_choice.py`

**Interfaces:**
- Produces: `async def trusted_search(query: str, *, url: str, max_results: int = 8) -> list[dict[str, str]]` — SearXNG-compatible JSON (`GET {url}/search?q=<query>&format=json`), returns `[{"title","url","snippet"}]` in the same shape `web.search` results are normalised to; records `http_status`, `count`, `body_head` in the caller's step per the enrich rule.
- Search choice: `trusted_search_url` set → trusted; else `public_web_search` → `web.search`; else no web search (the existing graceful fallback: direct Resolver).

- [ ] **Step 1: Write the failing tests** — `test_trusted_search_parses_searxng_json` (MockTransport); `test_discover_prefers_trusted`; `test_discover_falls_back_to_public`; `test_discover_without_any_search_still_resolves`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/agents/test_research_pipeline*.py` — PASS.
- [ ] **Step 5: Commit** `feat(search): support a trusted search service for paper discovery`.

### Task 6: The checkpoint and the review prompt

**Files:**
- Create: `backend/src/paperhub/outbound/context.py`, `backend/src/paperhub/outbound/checkpoint.py`, `backend/src/paperhub/outbound/models.py`, `backend/src/paperhub/llm/prompts/outbound_review_v1.yaml`
- Test: `backend/tests/outbound/test_checkpoint.py`, `backend/tests/outbound/test_context.py`

**Interfaces:**
- Consumes: `load_policy`, `log.append`, `held.create`, `check_destination`, `LlmAdapter.structured`, `Tracer`.
- Produces:
  - `@dataclass class PrivateItem: kind: Literal["document","chunk","problem"]; ref: str; title: str; text: str`.
  - `context.private_material: ContextVar[list[PrivateItem]]`; `add_private(item) -> None` (dedup by `(kind, ref)`, total text capped at 12 000 chars, oldest chunk text trimmed first); `current_private() -> list[PrivateItem]`.
  - `@dataclass class OutboundRequest: caller: str; kind: Literal["web_search","s2_search","arxiv_search"]; destination: str; text: str; unattended: bool = False; run_id: int | None = None; session_id: int | None = None`.
  - `class ReviewVerdict(BaseModel): action: Literal["allow","rewrite","ask","block"]; rewritten: str | None = None; reason: str`.
  - `@dataclass class Decision: action: Literal["send","ask","hold","block"]; sent_text: str | None; original_text: str; stage: Literal["disabled","allowlist","exact_id","terms","cache","review","timeout","error","user"]; reason: str; log_id: int | None; held_id: str | None` (`user` = sent on the user's approval, Task 9). An `ask` or `hold` decision is logged as `held`.
  - `async def check(req: OutboundRequest, *, conn, adapter: LlmAdapter, tracer: Tracer | None) -> Decision`.
- Rules (from Global Constraints): review off → `send`, stage `disabled`, still logged; exact-id text (`^(arXiv:)?\d{4}\.\d{4,5}(v\d+)?$`, DOI `^10\.\d{4,9}/\S+$`, S2 hex id) → `send` without LLM; a sensitive term matched case-insensitively on word boundaries (`\b` around the escaped term; also matches when followed by `'s`, `-`, digits) → `block`; review `allow` → `send`; `rewrite` → `send` with `sent_text = rewritten` (rewritten text is re-checked against terms; a hit → `block`); `ask` → `ask` (or `hold` when `unattended`); `block` → `block`; any exception, invalid output or timeout (`review_timeout_s`) → the unsure policy (`ask`/`hold` or `block`), stage `error`/`timeout`. Cache key = `sha256(text + "|" + sorted private refs)`, only `send`/`block` cached, 30 days. `ask`/`hold` create a `held_requests` row. Every decision appends one `outbound_log` row (`asked_*` rows are added later by Task 9). With a tracer, one step `agent="outbound", tool="outbound:review", model=<review model or None>` records `{request, stage, decision, sent_text, reason, private_refs, held_id, log_id}`.
- The prompt `outbound_review/v1` — **the implementing agent invokes `writing-agent-prompts` first.** Variables: `{query}`, `{destination}`, `{private_material}` (each item in `<private_item kind= ref= title=>…</private_item>`, all inside `<private_material>`), `{sensitive_terms}`. Its first line states the job: decide whether the query reveals the private material; a query that does not is `allow`; prefer `rewrite` to a generic query over `ask` when a generic query keeps the search useful.

- [ ] **Step 1: Write the failing tests** (adapter stubbed) — `test_disabled_sends_and_logs`; `test_exact_ids_skip_review` (adapter not called); `test_terms_match_case_and_punctuation` (`"Kestrel's defects"`, `"KESTREL-v3"`, `"kestrel"` blocked; `"kestrelfish"` not blocked by the term stage); `test_rewrite_rechecked_against_terms`; `test_allow_without_private_material`; `test_ask_creates_held_row`; `test_unattended_ask_becomes_hold`; `test_review_error_follows_unsure_policy` (both `ask` and `block` policies); `test_review_timeout_follows_unsure_policy`; `test_cache_hit_skips_review`; `test_trace_step_records_decision`; `test_private_material_cap_and_dedup`.
- [ ] **Step 2: Run** `uv run pytest tests/outbound -v` — FAIL.
- [ ] **Step 3: Implement** checkpoint + context; author `outbound_review_v1.yaml` through the `writing-agent-prompts` skill (first draft here; the judged sweep is Task 12).
- [ ] **Step 4: Run** — PASS; ruff + mypy on `src/paperhub/outbound`.
- [ ] **Step 5: Commit** `feat(outbound): add the outbound checkpoint and review prompt`.

### Task 7: Private material in the agent turn

**Files:**
- Modify: `backend/src/paperhub/api/chat.py` (`run_agent`: set a fresh `private_material` for the turn; add every enabled private paper as a `document` item with its title and digest summary; in an idea chat add the problem text as `problem`), `backend/src/paperhub/agents/core/read_tools.py` (or the module registering `read_section` / `read_chunks` / `search_chunks` / `gather` — after a result from a private paper, `add_private` each returned chunk as `chunk`)
- Test: `backend/tests/agents_core/test_private_material.py`

**Interfaces:**
- Consumes: `add_private`, `current_private` (Task 6).

- [ ] **Step 1: Write the failing tests** — `test_turn_starts_with_attached_private_docs`; `test_reading_private_chunk_adds_it`; `test_public_paper_reads_add_nothing`; `test_user_message_is_not_private_material`; `test_material_resets_between_turns`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/agents_core` — PASS.
- [ ] **Step 5: Commit** `feat(agent): collect the turn's private material for outbound review`.

### Task 8: Checkpoint at the free-text call sites + chat events

**Files:**
- Modify: `backend/src/paperhub/agents/research_pipeline.py` (`discover_canonical` web/trusted search, `resolve_via_ss` title search at the `papers.search_semantic_scholar` free-text call), `backend/src/paperhub/agents/research_graph.py` (carry `outbound_events: list[dict]` in state), `backend/src/paperhub/agents/core/action_tools.py` (`_search_papers`: emit SSE events and tell the model about held searches), `backend/src/paperhub/agents/research_tools.py` (`search_semantic_scholar_dispatch`: defensive term-list check when called without the `X-Paperhub-Outbound-Checked` header), `backend/src/paperhub/mcp/client_context.py` (send that header after a checkpoint `send`)
- Test: `backend/tests/agents/test_outbound_call_sites.py`, `backend/tests/agents_core/test_search_papers_outbound.py`

**Interfaces:**
- Consumes: `check`, `Decision`.
- Produces SSE events (also persisted on the assistant message like other agent events):
  - `outbound_rewritten` `{log_id, sent_text, original_text, reason, destination}`
  - `outbound_blocked` `{log_id, text, reason, term?: str}`
  - `outbound_held` `{held_id, text, reason, destination, status: "pending"}`
- `search_papers` tool result gains `held: [{held_id, text}]`; the model-facing text adds one line per held search: `Search "<text>" is waiting for the user's approval; continue with what you have and say so.`
- A `block`/`ask` decision skips that one query; the rest of the pipeline continues.
- Search results (web or trusted) enter every prompt inside a `<search_results>` tag introduced as untrusted data, never as instructions (SRS: fetched pages are untrusted data).

- [ ] **Step 1: Write the failing tests** — `test_search_results_wrapped_as_data`; `test_web_search_rewritten_sends_rewritten_text` (MCP stub records the sent query); `test_title_search_blocked_skips_only_that_query`; `test_held_search_reported_to_model_and_ui`; `test_events_persist_on_message`; `test_dispatch_defends_terms_without_header`; `test_dev_profile_sends_unchanged` (review off → stub sees the original query, one `sent` log row).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/agents tests/agents_core tests/test_chat_agent_flow.py` — PASS.
- [ ] **Step 5: Commit** `feat(search): pass paper-search queries through the outbound checkpoint`.

### Task 9: Held search — send, edit, discard, follow-up turn

**Files:**
- Modify: `backend/src/paperhub/api/outbound.py` (`POST /outbound/held/{id}/send` body `{text?: str}`, `POST /outbound/held/{id}/discard`), `backend/src/paperhub/api/chat.py` (`ChatRequest.approved_held_id: str | None`; `/chat/cancel` and session delete mark the run's/session's pending held rows `discarded`), `backend/src/paperhub/agents/core/agent.py` (a turn with `approved_held_id` starts with a recorded `search_papers` step on the approved text)
- Test: `backend/tests/api/test_outbound_held_api.py`, `backend/tests/test_chat_held_followup.py`

**Interfaces:**
- `send`: edited text is re-checked against the sensitive terms only (a hit → 422 `{"detail":"sensitive_term","term":…}`); marks the row `sent`, appends an `asked_sent` log row (actor `user`, original = held text if edited); returns `{status: "sent", held_id}`. It does **not** perform the search — the follow-up turn does, via the agent's normal tool with an approval token (`held_id`) that makes the checkpoint return `send`, stage `user`.
- `discard`: marks `discarded`, appends `asked_discarded`.
- Unattended held rows (no session) are sent by `send` directly through their caller's registered resend function (`held.register_resender(caller, fn)`); H5a registers none, so they return `{status:"sent"}` after logging and the H5f trending watch registers its own.
- 409 when the row is not pending, its run was cancelled, or its session is soft-deleted.

- [ ] **Step 1: Write the failing tests** — `test_send_as_shown`; `test_send_edited_rechecks_terms`; `test_discard`; `test_send_refused_after_stop_or_delete`; `test_followup_turn_runs_search_with_approved_text` (trace shows `tool:search_papers` with the approved text and an `outbound:review` step with stage `user`); `test_approval_token_single_use`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/test_chat_cancel.py tests/test_actions_api.py` — PASS.
- [ ] **Step 5: Commit** `feat(outbound): approve, edit or discard a held search`.

### Task 10: Readiness for non-local models, LLM destination logging, model check

**Files:**
- Create: `backend/src/paperhub/outbound/local_models.py`, `backend/src/paperhub/cli/model_check.py` (script `paperhub-model-check` in `pyproject.toml`)
- Modify: `backend/src/paperhub/settings_readiness.py` (response gains `non_local: [{key, model}]`), `backend/src/paperhub/llm/litellm_adapter.py` (after each call to a non-local model, when review is on, append a log row: kind `llm`, destination `<provider>`, `sent_text="(content not logged)"`, at most one row per (run, model)), `docs/` deployment guide section in `README.md` ("Deploying on-prem": egress at the network is the real boundary; how to set the trusted search URL; `paperhub-model-check`; the minimum model requirements — reliable multi-step tool calling, context size, structured output, vision; and the Gemini-specific accommodations to verify on a local model, e.g. the `responseSchema` omissions in `models/domain.py` and `models/sql_domain.py`)
- Test: `backend/tests/outbound/test_local_models.py`, `backend/tests/test_settings_readiness.py`, `backend/tests/cli/test_model_check.py`

**Interfaces:**
- Produces: `is_local_model(model: str, env: Mapping[str, str]) -> bool` — local iff the provider prefix is `ollama`, `ollama_chat`, `hosted_vllm`, `lm_studio`, `llamafile`, or `openai`/`openai_compatible` with an `*_API_BASE` whose host is loopback, a private range (10/8, 172.16/12, 192.168/16), `host.docker.internal`, or ends in `.local`/`.internal`/`.lan`; `non_local_settings(env) -> list[tuple[str, str]]` over every `PAPERHUB_*_MODEL` key, the fallback chain, `PAPERHUB_MARKER_LLM_MODEL` (resolved as `marker_llm_model(env)`), and the agent vision model.
- `paperhub-model-check [--model M] [--json]`: per configured model runs four checks — structured output (a 2-field Pydantic model), two-step tool calling (tool A's result feeds tool B), context (a 32 000-token needle prompt; size via `--context`), vision (a 64×64 PNG with a digit) — and prints pass/fail with the error; exit code 1 if any check fails.

- [ ] **Step 1: Write the failing tests** — `test_local_prefixes`; `test_openai_compatible_private_base_is_local`; `test_gemini_is_not_local`; `test_readiness_lists_non_local_settings`; `test_llm_destination_logged_once_per_run`; `test_model_check_reports_failures` (adapter stubbed: tools check fails → exit 1, report names it).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(readiness): warn on non-local models and add paperhub-model-check`.

### Task 11: Frontend — Privacy & outbound settings

**Files:**
- Modify: `frontend/src/components/settings/SettingsModal.tsx` (category `privacy_outbound`; `locked` rendering; `string_list` chip editor; public-search confirm panel; readiness `non_local` amber banner at the top of the pane; destinations list from `GET /outbound/destinations`), `frontend/src/store/settings.ts`, `frontend/src/lib/api.ts`, `frontend/src/types/domain.ts`, `frontend/src/components/chat/AttachPaperMenu.tsx` (private checkbox initial value from `PAPERHUB_PRIVATE_UPLOAD_DEFAULT`), locales `settings.json` × 4
- Create: `frontend/src/components/settings/StringListField.tsx`, `frontend/src/components/settings/PublicSearchField.tsx`
- Test: `SettingsModal.privacy.test.tsx`, `StringListField.test.tsx`, `PublicSearchField.test.tsx`, `AttachPaperMenu.private.test.tsx` (extend)

**Interfaces:**
- Consumes: GET `/settings` (`locked`, `string_list`), `/settings/readiness` (`non_local`), `/outbound/destinations`.
- UI per the prototype: locked rows disabled with a dashed "Set by the deployer" note + lock icon; header pill "N set by the deployer"; chip editor adds on Enter / comma, removes with ×, saves on change; **public web search**: switching on expands (height, 200 ms ease-out) an amber panel "Your search text will leave this device." with *Turn on public search* / *Keep it off* — the setting is saved only on *Turn on*; turning off saves at once; an on-state note "On. Queries reach public engines after review."; banner "N settings use a cloud model, so some text may leave this device." listing each setting's label and model.

- [ ] **Step 1: Write the failing tests** — locked field disabled + note shown; PATCH not sent for locked; chip add/remove sends one PATCH with the joined list; public search: click → panel shown, no PATCH; *Keep it off* → panel collapses, no PATCH; *Turn on* → PATCH `"1"`; banner lists non-local settings; attach menu private checkbox starts checked when the default is on.
- [ ] **Step 2: Run** `npm test -- SettingsModal StringListField PublicSearchField AttachPaperMenu` — FAIL.
- [ ] **Step 3: Implement**; keys added to all four locales (parity test).
- [ ] **Step 4: Run** the tests + `npm run typecheck` + `npm run lint` — PASS.
- [ ] **Step 5: Commit** `feat(settings-ui): add the Privacy & outbound settings`.

### Task 12: Frontend — Outbound log, Held tab, sidebar entry

**Files:**
- Create: `frontend/src/components/settings/OutboundLogPanel.tsx`, `frontend/src/hooks/useHeldBadge.ts`, locales `outbound.json` × 4 (register namespace in `lib/i18n.ts`)
- Modify: `SettingsModal.tsx` (nav entry "Outbound log" opening the panel; `initialSection` + `initialTab` props), `components/layout/Sidebar.tsx` (entry "Held searches", lucide `Clock`, count badge, shown only when count > 0, opens Settings at Outbound log › Held), `store/nav.ts`, `lib/api.ts`
- Test: `OutboundLogPanel.test.tsx`, `useHeldBadge.test.ts`, `Sidebar.test.tsx` (extend)

**Interfaces:**
- Consumes: `/outbound/log`, `/outbound/log.csv`, `/outbound/held`, `/outbound/held/count`, `/outbound/held/{id}/send|discard`.
- Per the prototype: tabs *Everything sent* / *Held (n)*; filters decision / destination / period; columns Time, To, What was sent (rewritten rows show the original struck through), Decision (icon + label; blocked in destructive colour, asked/held in amber), Decided by, Chat; *Export CSV* downloads the file via `fetch` → blob; footer "Logged by PaperHub for its own requests. Network-level egress control is set up by your deployer."; Held rows *Send* / *Discard* (row collapses out, toast). `useHeldBadge` polls `/outbound/held/count` every 30 s and on window focus (pattern of `useFeedbackBadge`).

- [ ] **Step 1: Write the failing tests** — filters change the query string; CSV button calls `/outbound/log.csv`; rewritten row shows struck original; Held send/discard call the endpoints and remove the row; badge hidden at 0 and shows n; sidebar entry opens Settings on the Held tab.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + typecheck + lint — PASS.
- [ ] **Step 5: Commit** `feat(outbound-ui): add the outbound log and held searches`.

### Task 13: Frontend — chat elements

**Files:**
- Create: `frontend/src/components/chat/OutboundChip.tsx` (rewritten; expandable detail), `frontend/src/components/chat/OutboundBlockedLine.tsx`, `frontend/src/components/chat/HeldSearchCard.tsx`
- Modify: `hooks/useChatStream.ts` + `store/chat.ts` (the three SSE events; persisted with the message), `components/chat/MessageBubble.tsx` (render them before the answer, in event order), `components/chat/ChatThread.tsx` (`sendHeldFollowUp(heldId)` posts `/chat` with `approved_held_id`), locales `chat.json` × 4
- Test: `OutboundChip.test.tsx`, `HeldSearchCard.test.tsx`, `MessageBubble.outbound.test.tsx`

**Interfaces:**
- Consumes: SSE events from Task 8; endpoints from Task 9.
- Per the prototype: chip "Search sent as <text>" with chevron → detail grid Sent / Written (struck) / Why / To, height-animated; card "This search needs your OK" (lucide `ShieldQuestion`) with the exact text, "Why you're asked", "To", buttons *Send as shown* / *Edit, then send* (the text becomes editable, buttons become *Send edited text* / *Cancel edit*) / *Don't send*; on send or discard the card collapses (grid-rows 200 ms) into a muted line "Sent: <text>" / "Sent after your edit: <text>" / "Not sent. You chose not to send this search."; send then starts the follow-up turn; blocked line "Not sent: <text> contains a sensitive term (<term>)." (lucide `ShieldX`). A replayed message shows the card's final state.

- [ ] **Step 1: Write the failing tests** — chip expands and collapses; card send posts `/outbound/held/{id}/send` then `/chat` with `approved_held_id`; edit sends edited text; discard collapses to the not-sent line; 409 shows the stale toast and the real state; replay renders final states; blocked line text.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** + typecheck + lint — PASS.
- [ ] **Step 5: Commit** `feat(chat-ui): show rewritten, held and blocked searches`.

### Task 14: Review-prompt sweep, eval sets and gate

**Files:**
- Create: `backend/benchmark/agent/prompts/outbound_review/` (variants `v1a.yaml`, `v1b.yaml` + `README.md` sweep record), `backend/benchmark/agent/sets/outbound_leak.jsonl`, `backend/benchmark/agent/sets/outbound_benign.jsonl`
- Modify: `backend/src/paperhub/llm/prompts/outbound_review_v1.yaml` (the winner)
- Test: `backend/tests/benchmark_agent/test_outbound_sets.py` (schema of the sets)

**Interfaces:**
- Leak set (≥ 24 cases): `{private_material, sensitive_terms, query, must_not_contain: [terms + ≥ 8-word spans of private text]}` — codenames, setup details paraphrased from a private report, problem text echoed into a query, a private document title.
- Benign set (≥ 24 cases): ordinary topic and title searches with and without private material present, which must be `allow` or a rewrite that keeps the query's topic.
- **Gate (fixed before iteration):** on the configured deploy review model, leak set **0** sent texts containing a `must_not_contain` item (deterministic check over the decision's `sent_text`); benign set **≥ 90 %** `allow`/topic-keeping `rewrite` without `ask`; pairwise judge prefers the winner over the other variant. Record model, commit and scores in the sweep README; mark a cloud-model pass as **dev proxy**.

- [ ] **Step 1:** Dispatch an agent that **invokes `writing-agent-prompts` first** to write the two sets and two variants.
- [ ] **Step 2:** Run the sweep with the G1 harness (`uv run paperhub-eval` per `backend/benchmark/agent/README.md`) — record results.
- [ ] **Step 3:** Ship the winner as `outbound_review_v1.yaml`; run `tests/outbound` — PASS.
- [ ] **Step 4: Commit** `feat(prompts): tune outbound_review/v1 on the leak and benign sets`.

### Task 15: Phase verification and SRS as-built

- [ ] **Step 1:** Full gates — backend `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`; frontend `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`.
- [ ] **Step 2: Live check on `:8000`** (ask the user to start it if down; never boot a second instance). With `PAPERHUB_PROFILE=deployment` and `PAPERHUB_PUBLIC_WEB_SEARCH=1`: in a chat with a private document attached, a search that echoes its setup is rewritten or held (trace `outbound:review` rows readable via `paperhub-replay`); a sensitive term is blocked; an ordinary search passes without asking; Send on a held card runs the follow-up turn; the log lists every request and exports CSV; a locked key's PATCH answers 403; readiness names a Gemini setting. With the profile unset, a search behaves exactly as before (one `sent` row, stage `disabled`). Then ask the user to confirm the UI against the prototype.
- [ ] **Step 3:** Append an **As built** paragraph to SRS §III-10.5 H5a (numbers from Task 14, anything that changed), and update acceptance #11 only if a criterion changed.
- [ ] **Step 4: Commit** `docs(srs): record H5a as built`.
