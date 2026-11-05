# Plan H1 — Bilingual Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read any paper with a translation beside the source. HTML papers get a translation under each paragraph. PDF papers keep their layout and show a hover/pin translation card to the left of the reader. Math and variables stay exactly in place, and the translation follows the user's remembered terminology.

**Architecture:** One translation API serves both views. The client sends blocks of text in which math and anchors are already replaced by numbered placeholders (`⟦0⟧`, `⟦1⟧`, …). The server translates a batch with the small model, checks every placeholder survived, escalates failures (retry → flagship → "unavailable"), and caches per (paper, block key, language) in a shared `translations` table. HTML blocks are extracted in the browser from the Canvas iframe; the rendered `source.html` and the `chunks` stay byte-identical. PDF paragraphs come from Marker blocks, now persisted as an additive `asset/blocks.json` with inline math recovered as LaTeX by Marker's `redo_inline_math`, which runs through the configured LLM provider.

**Tech Stack:** FastAPI + aiosqlite + LiteLLM (`LiteLlmAdapter.structured`), marker-pdf 1.10.2 service, React 19 + zustand + react-pdf + KaTeX + Base UI Popover + react-i18next, pytest / Vitest + RTL + MSW.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10 (shared constraints, UI rules), **§III-10.1 H1**. Visual base: `docs/superpowers/designs/2026-11-05-plan-h/` (translate pill, language menu, HTML layout "A", PDF hover/pin card, highlights, divider, motion).

**Depends on:** H0 merged (`Collapsible`, `usePresence`, `revealInScroll`, `PageHeader`). **Branch:** `feat/plan-h1-bilingual-reading` from `main` after H0.

**Parallel tracks.** Task 1 fixes the contract. After it, three tracks can run independently:
- **A (translation backend):** Tasks 2–5
- **B (PDF geometry):** Tasks 6–8
- **C (frontend):** Tasks 9–13; tests use MSW against the Task 1 contract

Task 14 joins them.

## Global Constraints

- **Overlay only:** `source.html` and every `chunks` row (`char_start`, `char_end`, `text`) stay byte-identical. Nothing in this plan writes to them. CLAUDE.md *render-change rule*.
- Every UI string goes through `t()` in all four locales. Icons are lucide; no emoji. **No new UI colour.** The translation-target highlight is **neutral gray, never yellow** (yellow = cited passage).
- Floating controls (the pill) are **≈50–55 % opaque when idle and opaque on hover, focus, or while their menu is open**. Content (card, menu) is always opaque. **Glow only on the pill.**
- Motion: menu enter is fade + zoom-in-95 (~150 ms) and exit mirrors it; the card has enter/leave transitions; the pill **grows leftward and the icon never moves**. Every exit animates (CLAUDE.md *UI transitions*). Respect `prefers-reduced-motion`.
- Every LLM prompt is authored by invoking the **`writing-agent-prompts`** skill first (≥ 2 variants × query set × judged). Injected context goes in XML tags.
- New env flags go in `backend/.env.example`, following its one-line style. User-facing runtime settings go through the FR-14 registry.
- A translation batch is a pipeline call, not a chat turn: **no `runs` row**. Failures go to the backend log with paper id, block keys, language and provider error.
- Python via `uv`. Conventional Commits, one concern each, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done = automated gates green.** The live gate on `:8000`, the Marker docker rebuild, the PDF backfill (GPU, sequential) and merge-prep happen with the user.

## Review Focus

- **A model reply that drops, duplicates or invents a placeholder** must never reach the page. It is retried, then sent to the flagship, then shown as "translation unavailable". Pinned in Task 4.
- **Changing terminology in memory** must not wipe the cache. Cached blocks still show, and the menu offers "re-translate with current terminology". Pinned in Tasks 4 and 10.
- **A free-text language with odd whitespace** ("  繁體中文（香港） ") must share one cache key with its trimmed form. Pinned in Task 2.
- **An HTML block containing a chunk anchor or a stray `PHCHUNKANCHOR…END` remnant** must not leak that text into what is sent. Pinned in Task 11.
- **A PDF opened before its blocks exist** (Marker unavailable, or no LLM at ingest) keeps the pill in place but disabled, with a tooltip saying why. It never translates garbled formulas. Pinned in Tasks 8 and 10.

---

## API contract (fixed by Task 1; every track codes against it)

```
POST /translations
  body  { paper_content_id: int, language: str, force?: bool,
          blocks: [{ key: str, text: str }] }            # 1..5 adjacent blocks, text uses ⟦n⟧ placeholders
  200   { results: [{ key: str, status: "ok" | "unavailable", text: str | null,
                      model: str | null, stale_terminology: bool }] }

GET  /translations/config
  200   { full_paper: bool }

GET  /papers/content/{id}/paragraphs
  200   { status: "ready" | "no_blocks" | "no_inline_math_llm",
          paragraphs: [{ key: str, text: str, math: [str],            # math[n] = LaTeX for ⟦n⟧
                         fragments: [{ page: int, bbox: [x0,y0,x1,y1] }] }] }   # page 0-based, PDF points, top-left origin
```

- **Block keys:** HTML uses `h:<document-order index>`; PDF uses `p:<first Marker block_id>`.
- **Placeholders:** `⟦n⟧` holds protected inline content (math, citation/reference anchor, inline code). `⟦BR⟧` marks a page or column break inside one PDF paragraph.

---

### Task 1: Contract — models, types, `translations` table

**Files:**
- Create: `backend/src/paperhub/translation/__init__.py`, `backend/src/paperhub/translation/models.py`
- Modify: `backend/src/paperhub/db/schema.sql`, `backend/src/paperhub/db/migrate.py`
- Create: `frontend/src/types/translation.ts`
- Test: `backend/tests/test_translation_models.py`, `backend/tests/test_migrate.py`

**Interfaces:**
- Produces:
  - Pydantic models `TranslateBlock{key,text}`, `TranslateRequest{paper_content_id,language,force=False,blocks: list[TranslateBlock] (1..5)}`, `TranslateResult{key,status: Literal["ok","unavailable"],text: str|None,model: str|None,stale_terminology: bool}`, `TranslateResponse{results}`, `TranslateConfig{full_paper: bool}`, `PdfFragment{page,bbox}`, `PdfParagraph{key,text,math,fragments}`, `ParagraphsResponse{status,paragraphs}`. The TS mirrors in `types/translation.ts` use the same names.
  - Table:

    ```sql
    CREATE TABLE IF NOT EXISTS translations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
        block_key TEXT NOT NULL, language TEXT NOT NULL,
        source_hash TEXT NOT NULL, memory_hash TEXT NOT NULL,
        text TEXT NOT NULL, model TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (paper_content_id, block_key, language));
    ```

    No user column (single user). The unique key may gain one later.

- [ ] **Step 1: Write the failing tests.** `TranslateRequest` rejects 0 and 6 blocks. `translations` exists after `apply_schema`, and applying twice does not raise.
- [ ] **Step 2: Run.** From `backend/`: `uv run pytest tests/test_translation_models.py tests/test_migrate.py -k translation -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the models, the schema DDL and the idempotent `CREATE TABLE IF NOT EXISTS` in `apply_schema`, plus the TS types.
- [ ] **Step 4: Run.** Same command. Expected: PASS. Then `npm run typecheck` in `frontend/`.
- [ ] **Step 5: Commit** with `feat(translation): fix the bilingual-reading API contract and cache table`.

---

## Track A — translation backend

### Task 2: Placeholder rules and the cache repository

**Files:**
- Create: `backend/src/paperhub/translation/placeholders.py`, `backend/src/paperhub/translation/cache.py`
- Test: `backend/tests/test_translation_placeholders.py`, `backend/tests/test_translation_cache.py`

**Interfaces:**
- Produces:
  - `placeholder_ids(text: str) -> list[str]` returns the ordered tokens matched by `⟦(\d+|BR)⟧`.
  - `placeholders_intact(source: str, output: str) -> bool` is true when the token multisets are equal.
  - `normalize_language(s: str) -> str` strips and collapses inner whitespace runs to one space.
  - `source_hash(text: str) -> str` is the sha256 hex of the text.
  - `get_cached(conn, paper_content_id, keys: list[str], language) -> dict[str, CachedRow]`, where `CachedRow` has `source_hash, memory_hash, text, model`.
  - `put_cached(conn, paper_content_id, key, language, source_hash, memory_hash, text, model) -> None` upserts.

- [ ] **Step 1: Write the failing tests.**

```python
assert placeholders_intact("a ⟦0⟧ b ⟦1⟧", "x ⟦1⟧ y ⟦0⟧")      # order may change in translation
assert not placeholders_intact("a ⟦0⟧ ⟦1⟧", "x ⟦0⟧")           # dropped
assert not placeholders_intact("a ⟦0⟧", "x ⟦0⟧ ⟦0⟧")           # duplicated
assert not placeholders_intact("a ⟦0⟧", "x ⟦0⟧ ⟦7⟧")           # invented
assert normalize_language("  繁體中文（香港）  ") == "繁體中文（香港）"
# cache: put then get round-trips; second put on same (paper,key,language) replaces text
```

- [ ] **Step 2: Run** `uv run pytest tests/test_translation_placeholders.py tests/test_translation_cache.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** to the interfaces above.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(translation): add placeholder checks and the translation cache`.

### Task 3: Translation prompt (writing-agent-prompts)

**Files:**
- Create: `backend/src/paperhub/llm/prompts/translate_blocks_v1.yaml`
- Create: `backend/benchmark/agent/prompts/translate_blocks/` (≥ 2 variants) plus a query set of **12 blocks**:
  - 4 formula-dense blocks, one of them the 17-formula Nature paragraph shape
  - 3 blocks with citation placeholders
  - 2 list items
  - 1 caption
  - 2 plain blocks
  - targets zh-TW, ja and the free-text "繁體中文（香港）"
  - one case with a terminology memory ("always translate *token* as 詞元")

**Interfaces:**
- Produces: slot `"translate_blocks/v1"` with variables `{language, terminology, context, blocks_xml}`. The user template wraps them as `<target_language>`, `<terminology>` (the active global memory block, may be empty), `<previous_translation>` and `<blocks>` (each block as `<block key="…">text</block>`). The response model `TranslatedBlocks{items: list[{key: str, text: str}]}` lives in `translation/models.py`.

- [ ] **Step 1: Invoke the `writing-agent-prompts` skill** and follow it. Rules for the prompt:
  - The first line is direct.
  - Keep every `⟦n⟧` and `⟦BR⟧` exactly.
  - Translate only the prose.
  - Keep variable names untranslated.
  - Apply the terminology.
  - Return one item per input key.
- [ ] **Step 2: Write ≥ 2 variants and run the judged comparison** over the query set using `backend/benchmark/agent/`. Grading:
  - **Deterministic** (`placeholders_intact` on every item, the key set equals the input set): any failure loses.
  - **Pairwise judge** on fidelity and fluency.
  - Record the result table in the variant folder's README.
- [ ] **Step 3: Ship the winner** as `translate_blocks_v1.yaml`.
- [ ] **Step 4: Commit** the prompt, the variants, the query set and the README, with `feat(translation): add the block translation prompt chosen by eval`.

### Task 4: `TranslationService` — batch, validate, escalate, cache

**Files:**
- Create: `backend/src/paperhub/translation/service.py`
- Test: `backend/tests/test_translation_service.py` (stub `LlmAdapter`)

**Interfaces:**
- Consumes: Tasks 2 and 3, `build_active_memory_block(conn, session_id=None)` (`agents/memory_recall.py`), `small_tier_model()` / `flagship_tier_model()` (`config.py`).
- Produces: `class TranslationService(llm: LlmAdapter, *, model: str, flagship_model: str, max_in_flight: int = 2)` with `async translate(conn, req: TranslateRequest) -> TranslateResponse`. The behaviour, in order:
  1. Normalize the language.
  2. Compute `memory_hash` = sha256 of the global-only memory block.
  3. Serve cache hits whose `source_hash` matches. When `force` is false, a `memory_hash` mismatch still serves the cached text with `stale_terminology=True`.
  4. Send the misses (or all blocks when `force` is set) in **one** `structured` call on `model`, with context = the cached translation of the block whose key immediately precedes the first block, if any.
  5. Any block failing `placeholders_intact`, or missing from the reply, is retried once alone on `model`, then once on `flagship_model`, then returned as `unavailable`. Unavailable results are not cached.
  6. Cache the successes.
  7. Run at most `max_in_flight` concurrent batches **per paper**, using an `asyncio.Semaphore` per `paper_content_id` held in a dict on the service.
  8. Log failures at WARNING with paper id, keys, language and exception.

- [ ] **Step 1: Write the failing tests.**

```python
test_cache_hit_skips_llm                 # stub asserts not called
test_dropped_placeholder_escalates_to_flagship   # small returns "x" for ⟦0⟧ block twice → flagship called once → ok, model == flagship
test_all_attempts_fail_returns_unavailable_and_does_not_cache
test_terminology_change_marks_stale_not_retranslated   # memory_hash differs, force False → cached text, stale_terminology True, llm not called
test_force_retranslates_and_clears_stale
test_language_whitespace_shares_cache
test_per_paper_concurrency_capped_at_two  # 4 concurrent requests for one paper → stub sees ≤2 overlapping
```

- [ ] **Step 2: Run** `uv run pytest tests/test_translation_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** to the interface above.
- [ ] **Step 4: Run.** Expected: PASS. Then run ruff and mypy.
- [ ] **Step 5: Commit** with `feat(translation): translate blocks with placeholder checks and escalation`.

### Task 5: Settings and the API router

**Files:**
- Modify: `backend/src/paperhub/config.py` (`Settings` + `load_settings`), `backend/src/paperhub/settings_registry.py`, `backend/.env.example` (section `# 3. LLM model selection` for the model, `# 4. Agent tunables` for the toggle)
- Create: `backend/src/paperhub/api/translations.py`; Modify: `backend/src/paperhub/app.py` (`include_router`, service on `app.state`)
- Modify: frontend settings label/help keys in all four `settings.json` (registry labels are localized frontend-side)
- Test: `backend/tests/test_translations_api.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings.translate_model` comes from `PAPERHUB_TRANSLATE_MODEL`, defaulting to `small_tier_model()`.
  - `Settings.translate_full_paper: bool` comes from `PAPERHUB_TRANSLATE_FULL_PAPER`, default `1`.
  - Registry entries:
    - `PAPERHUB_TRANSLATE_MODEL`: `models_providers`, string, advanced, help "Translates papers for bilingual reading. Defaults to the small tier."
    - `PAPERHUB_TRANSLATE_FULL_PAPER`: `agents_memory`, bool, default `"1"`, help "After the visible part, translate the rest of the paper in the background."
  - `.env.example` lines, one line each, in the file's style:

    ```
    # Translation model for bilingual reading [small].
    # PAPERHUB_TRANSLATE_MODEL=
    # Translate the whole paper in the background after the visible part (1=on, 0=off).
    # PAPERHUB_TRANSLATE_FULL_PAPER=1
    ```

  - `POST /translations` and `GET /translations/config` per the contract. `POST /translations` returns 404 for an unknown `paper_content_id`.

- [ ] **Step 1: Write the failing tests.**
  - The default model equals the small tier.
  - The toggle reads 0/1.
  - POST round-trips through a stubbed service set on `app.state`.
  - An unknown paper returns 404.
  - Config reflects the env.
- [ ] **Step 2: Run** `uv run pytest tests/test_translations_api.py tests/test_config.py -k translat -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. Also run `npx vitest run src/locales/parity.test.ts`.
- [ ] **Step 5: Commit** with `feat(translation): expose the translation API and its settings`.

---

## Track B — PDF geometry

### Task 6: Marker inline math through the configured provider

**Files:**
- Modify: `marker_service/app.py` (`_converter` :95, `_use_llm` :55, `/health` :223)
- Create: `marker_service/llm_config.py`; Test: `marker_service/tests/test_llm_config.py` (create the folder if absent; run with `uv run --directory marker_service pytest`)
- Modify: `backend/.env.example` (new `# 6. External services` lines), `CLAUDE.md` (the Marker bullet: the LLM now follows the configured provider)

**Interfaces:**
- Produces: `marker_llm_config(env: Mapping[str, str]) -> dict[str, str] | None`. It reads `PAPERHUB_MARKER_LLM_MODEL`, a LiteLLM-style id that defaults to the backend's `PAPERHUB_MODEL_SMALL`, then `gemini/gemini-3.1-flash-lite`. It returns `None` when no credential is usable. Otherwise it returns Marker 1.10.2 config keys:
  - `gemini/<m>` + `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) → `{"llm_service": "marker.services.gemini.GoogleGeminiService", "gemini_api_key": …, "gemini_model_name": m}`.
  - `ollama/<m>` → `{"llm_service": "marker.services.ollama.OllamaService", "ollama_base_url": OLLAMA_API_BASE or "http://localhost:11434", "ollama_model": m}`.
  - `openai/<m>` or `hosted_vllm/<m>` → `{"llm_service": "marker.services.openai.OpenAIService", "openai_base_url": OPENAI_API_BASE, "openai_api_key": OPENAI_API_KEY or "none", "openai_model": m}`.
  - When the config is not `None`, `_converter` sets `use_llm=True` and `redo_inline_math=True` and passes `cfg.get_llm_service()`. `/health` adds `"llm_model"`.
- `.env.example`:

  ```
  # Vision model Marker uses to recover inline math as LaTeX (defaults to PAPERHUB_MODEL_SMALL).
  # PAPERHUB_MARKER_LLM_MODEL=
  ```

- [ ] **Step 1: Write the failing tests**, one per provider branch plus the `None` case (no key and a gemini model).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Before relying on the 1.10.2 service class paths and config key names, check them against the installed package (`uv run --directory marker_service python -c "import marker.services.ollama, marker.services.openai, marker.services.gemini"`), and fix the names in the test if they differ.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(marker): recover inline math through the configured LLM provider`.

> **Morning item, not in this run:** `docker compose build marker`. Then run the **inline-math quality check** with a local vision model on the Nature test page. The spec flags this as the H1 risk: if a local model cannot recover the 17-formula paragraph, PDF translation needs a cloud model.

### Task 7: Persist Marker blocks (`asset/blocks.json`)

**Files:**
- Modify: `backend/src/paperhub/pipelines/paper_asset.py` (add a blocks reader/writer), `backend/src/paperhub/pipelines/marker_to_asset.py` (`marker_doc_to_asset` also writes blocks)
- Modify: `backend/src/paperhub/cli/backfill_assets.py` (add `--blocks-only`)
- Test: `backend/tests/test_paper_asset.py`, `backend/tests/test_marker_to_asset.py`, `backend/tests/test_backfill_assets.py`

**Interfaces:**
- Produces:
  - `BlockAsset(block_id: str, block_type: str, page: int, bbox: tuple[float, float, float, float], html: str, section: str | None)`.
  - `write_block_assets(source_dir, blocks, *, inline_math_llm: bool) -> None` writes `asset/blocks.json` = `{"inline_math_llm": bool, "blocks": [...]}` in Marker reading order.
  - `read_block_assets(source_dir) -> tuple[list[BlockAsset], bool] | None`.
  - `marker_doc_to_asset(doc, *, source_dir, inline_math_llm: bool = False)` writes it. `MarkerClient` passes `inline_math_llm` from the service's `/health` `llm_model` being present.
  - `paperhub-backfill-assets --blocks-only` reruns Marker for PDF sources lacking `blocks.json` and writes **only** `blocks.json`. It never rewrites chunks, `figures.json` or `paper_content`, and it runs strictly sequentially.

- [ ] **Step 1: Write the failing tests.**
  - Round-trip `blocks.json`.
  - `marker_doc_to_asset` on the existing Marker fixture writes it, with PageHeader/PageFooter blocks still present (filtering happens in Task 8).
  - The backfill `--blocks-only` on a fixture paper writes `blocks.json`, while `chunks` rows and `figures.json` bytes stay unchanged (compare before and after).
- [ ] **Step 2: Run** `uv run pytest tests/test_paper_asset.py tests/test_marker_to_asset.py tests/test_backfill_assets.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(pipeline): persist Marker paragraph blocks for PDF reading`.

### Task 8: PDF paragraphs endpoint

**Files:**
- Create: `backend/src/paperhub/translation/pdf_paragraphs.py`; Modify: `backend/src/paperhub/api/papers.py` (`GET /papers/content/{id}/paragraphs`)
- Test: `backend/tests/test_pdf_paragraphs.py` (unit), `backend/tests/test_papers_api.py` (endpoint)

**Interfaces:**
- Consumes: `read_block_assets` (Task 7), `PdfParagraph` / `ParagraphsResponse` (Task 1).
- Produces: `build_paragraphs(blocks: list[BlockAsset]) -> list[PdfParagraph]`. The rules:
  - **Translatable** block types: `Text`, `ListItem`, `Caption`, `Footnote`, `TextInlineMath`.
  - **Skipped:** `PageHeader`, `PageFooter`, `SectionHeader`, `Picture`, `Figure`, `Equation`, `Table*` and page numbers.
  - Block html becomes text: each `<math>…</math>` becomes `⟦n⟧`, with its LaTeX appended to `math`; `<a>`/`<sup>` reference markers are kept as `⟦n⟧` holding their text. The remaining tags are stripped and whitespace collapsed.
  - **Continuation:** a block whose text does not end a sentence (last non-space char not in `.?!。？！:;` and not ending a placeholder that closes a sentence) merges with the next translatable block in reading order. That covers column breaks, page breaks and prose resuming after a display equation. Each merge joins with ` ⟦BR⟧ ` and appends the next block's `{page,bbox}` to `fragments`.
  - Key = `p:<first block_id>`.
  - Endpoint status:
    - `no_blocks` when `blocks.json` is missing.
    - `no_inline_math_llm` when `inline_math_llm` is false. Paragraphs are still returned, but the client disables translation.
    - `ready` otherwise.

- [ ] **Step 1: Write the failing tests.**
  - A two-column fixture: a block ending "…the model" plus the next block "collapses quickly." becomes one paragraph with 2 fragments and `⟦BR⟧`.
  - A block ending "." does not merge.
  - Inline math becomes placeholders, with `math` in order.
  - Headers and footers are excluded.
  - Each endpoint status is covered.
- [ ] **Step 2: Run** `uv run pytest tests/test_pdf_paragraphs.py tests/test_papers_api.py -k paragraphs -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(translation): serve PDF paragraphs with geometry and continuations`.

---

## Track C — frontend

### Task 9: Translation store, API client, request queue

**Files:**
- Create: `frontend/src/store/translation.ts`, `frontend/src/lib/translationQueue.ts`; Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/store/translation.test.ts`, `frontend/src/lib/translationQueue.test.ts`

**Interfaces:**
- Produces:
  - `useTranslationStore` (persisted under the name `"paperhub-translation-v1"`, all storage access inside try/catch):
    - `language: string` defaults to the UI locale's display name, e.g. "繁體中文"
    - `recent: string[]`, max 5, most recent first, deduped after trim
    - `enabledByPaper: Record<number, boolean>`
    - `setLanguage(l)`, `toggle(paperId)`
  - API: `translateBlocks(req): Promise<TranslateResponse>`, `getTranslateConfig()`, `getPdfParagraphs(id)`.
  - `createTranslationQueue({ paperId, language, fullPaper, send = translateBlocks, maxInFlight = 2, batchSize = 5 })` returns `{ setBlocks(keysInOrder: string[], textByKey), setVisible(keys: string[]), onResult(cb), dispose() }`. Order of work:
    1. Visible keys.
    2. The next 3 screens' worth, which is the 30 keys after the last visible key.
    3. The rest, only when `fullPaper` is set.

    Requests are made of ≤ 5 **adjacent** keys. `setVisible` re-prioritizes everything not yet sent.

- [ ] **Step 1: Write the failing tests.**
  - Visible blocks go first.
  - `fullPaper=false` never sends past the look-ahead window.
  - At most 2 requests are in flight.
  - Batches contain only adjacent keys.
  - Re-prioritizing after a scroll works.
  - `recent` dedupes trimmed entries and caps at 5.
- [ ] **Step 2: Run** `npx vitest run src/store/translation.test.ts src/lib/translationQueue.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): queue translation requests by reading position`.

### Task 10: Translate pill and language menu

**Files:**
- Create: `frontend/src/components/canvas/TranslatePill.tsx`; Modify: `frontend/src/components/canvas/CitationCanvas.tsx` (mount inside the reading body `div.relative`, :420)
- Create: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/translation.json` and register the namespace in `lib/i18n.ts`
- Test: `frontend/src/components/canvas/TranslatePill.test.tsx`

**Interfaces:**
- Consumes: `useTranslationStore`, `usePresence` (H0).
- Produces: `TranslatePill({ paperId, disabledReason?: "no_blocks" | "no_inline_math_llm" | null, staleTerminology: boolean, onRetranslate: () => void })`.
  - **Position:** the pill sits absolutely at bottom-right with offsets `right-4 bottom-4`.
  - **Toggle:** the round icon button (lucide `Languages`) toggles translation on/off. When on, a language label button grows **leftward** (animate `max-width`/opacity on the label only, so the icon stays fixed).
  - **Opacity and glow:** idle `opacity-55`, opaque on hover, focus-within or open menu, with a glow (`shadow` using a ring-coloured blur, no border).
  - **Language menu:** clicking the label opens the Base UI Popover menu, which lists:
    - the 4 UI languages
    - the `recent` entries
    - a free-text input ("Any language, e.g. 繁體中文（香港）"), submitted with Enter
    - a "Re-translate with current terminology" item, only when `staleTerminology` is true

    The selected item is `bg-accent` plus a `Check` icon.
  - **Disabled:** when `disabledReason` is set, the pill is disabled and a Tooltip explains why ("This PDF has no paragraph layout yet" / "Formulas in this PDF couldn't be read; translation is off"). The pill never disappears.
  - The same component serves HTML and PDF.

- [ ] **Step 1: Write the failing tests.**
  - Off by default: no label is rendered and the icon has an accessible name.
  - Clicking turns it on, the label appears and the icon element is the same node.
  - Free-text "  日本語（関西） " sets the trimmed language and adds it to recent.
  - The stale item appears only when stale.
  - Disabled shows the tooltip text and does not toggle.
- [ ] **Step 2: Run** `npx vitest run src/components/canvas/TranslatePill.test.tsx src/locales/parity.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): add the translate pill and language menu`.

### Task 11: HTML overlay — translation under each paragraph

**Files:**
- Create: `frontend/src/lib/htmlTranslation.ts`; Modify: `frontend/src/components/canvas/HtmlView.tsx` (inside `apply()`, after the theme)
- Test: `frontend/src/lib/htmlTranslation.test.ts`, `frontend/src/components/canvas/HtmlView.translation.test.tsx`

**Interfaces:**
- Consumes: the queue (Task 9) and the contract.
- Produces:
  - `extractBlocks(doc: Document): { key: string; el: Element; text: string; protectedNodes: Node[] }[]`.
    - It selects `p, li, figcaption, h1, h2, h3, h4, h5, h6` in document order, skipping elements nested in another selected block and those inside `math`/`.math.display`/`pre`.
    - Key is `h:<index>`.
    - `span.math.inline`, `a.citation`/`a[href^="#"]` reference anchors and `code` are replaced in `text` by `⟦n⟧`, with the originals kept in `protectedNodes[n]`.
    - Chunk anchors (`[id^="phchunk-"]`) and any `PHCHUNKANCHOR\d+END` / bare `\d+END` text remnants are dropped from `text`.
  - `renderTranslation(block, result | "pending" | "unavailable")` inserts or updates one `div.ph-tr[data-ph-tr-key]` **after** `block.el`:
    - `ok`: text with `⟦n⟧` replaced by clones of `protectedNodes[n]`, so MathJax output and links are reused verbatim
    - `pending`: a small spinner
    - `unavailable`: a muted "Translation unavailable"
  - `removeTranslations(doc)` removes them all.
  - An injected `<style id="ph-tr-style">`: muted foreground, thin left rule in the border colour, `margin: .25em 0 1em`. The colours must read correctly under `applyIframeTheme`'s dark `invert` filter, which inverts the translation like the rest of the page. Insertion uses the H0 collapse motion (height 0 → auto, ~200 ms). Turning translation off removes them with the same collapse.
  - `IntersectionObserver` on block elements feeds `queue.setVisible`.

- [ ] **Step 1: Write the failing tests** (jsdom documents).
  - A paragraph with `<span class="math inline">\(x\)</span>` becomes text `"… ⟦0⟧ …"`, and the rendered translation contains the cloned math node.
  - `<span id="phchunk-3"></span>` and `PHCHUNKANCHOR3END` never appear in `text`.
  - A nested `li > p` yields one block.
  - `source.html`'s original nodes are unchanged except for inserted siblings (compare the `outerHTML` of every original block).
  - Unavailable renders the muted label.
- [ ] **Step 2: Run** `npx vitest run src/lib/htmlTranslation.test.ts src/components/canvas/HtmlView.translation.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): show translations under HTML paper paragraphs`.

### Task 12: PDF hover/pin translation card

**Files:**
- Create: `frontend/src/components/canvas/PdfTranslationLayer.tsx`, `frontend/src/components/canvas/TranslationCard.tsx`; Modify: `frontend/src/components/canvas/PdfView.tsx` (per-page overlay next to the existing highlight overlay)
- Test: `frontend/src/components/canvas/PdfTranslationLayer.test.tsx`

**Interfaces:**
- Consumes: `getPdfParagraphs`, the queue, `bboxToRect` and `markerPageToPageNumber` (`lib/bboxHighlight.ts`), `usePresence`.
- Produces:
  - Per page, one absolutely positioned hit-rect per fragment (`pointer-events-auto`, `rounded-[2px]`). The background is **page-relative black at 9 % on hover and 16 % when pinned**, applied to every fragment of the same paragraph, with no outline. Fragment rects never cover figures: only translatable blocks get rects.
  - Hovering shows `TranslationCard` for that paragraph and requests its translation at top priority. Clicking pins it, and the pinned rects stay at 16 %. ✕ or Esc unpins and closes.
  - `TranslationCard` is portalled to the body:
    - It uses the Popover surface (`bg-popover border shadow-md rounded-md`), with `max-width: 460px` and the width of the available gutter.
    - It is placed **to the left of the reading area**, aligned with the paragraph's top, with an **arrow** pointing at it.
    - Enter/leave use the fade + zoom transition through `usePresence`.
    - Content: the translation with `⟦n⟧` rendered by KaTeX from `math[n]`, plus the pin/close buttons.
  - `mathToKatexHtml(text, math): string` is a pure helper.

- [ ] **Step 1: Write the failing tests.**
  - Hovering a fragment shows the card with translated text and KaTeX output for `⟦0⟧`.
  - Every fragment of the paragraph gets the hover class.
  - Click pins the card and the mouseleave no longer hides it.
  - Esc closes it with an exit (the card element still exists right after Esc, and is gone after 150 ms with fake timers).
  - Paragraphs status `no_inline_math_llm` renders no hit-rects.
- [ ] **Step 2: Run** `npx vitest run src/components/canvas/PdfTranslationLayer.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): show a hover and pin translation card for PDF paragraphs`.

### Task 13: Card behaviour for long and split paragraphs

**Files:**
- Modify: `frontend/src/components/canvas/TranslationCard.tsx`, `PdfTranslationLayer.tsx`
- Create: `frontend/src/lib/cardPlacement.ts`; Test: `frontend/src/lib/cardPlacement.test.ts`, extend `PdfTranslationLayer.test.tsx`

**Interfaces:**
- Produces: `placeCard({ paraRects: DOMRect[], viewport: {top,bottom}, gutter: {left,right}, cardHeight }) → { side: "left" | "below"; top: number; maxHeight: number; arrowY: number; activeFragment: number }`.
  - **Clamping:** the card stays inside the viewport while the paragraph is visible, and `maxHeight` is the visible height.
  - **Scroll sync:** the content scroll fraction follows the reader's progress through the paragraph, i.e. the fraction of the paragraph's height scrolled past the viewport top.
  - **Split paragraphs:** `activeFragment` is the fragment most in view, and the arrow tracks it. A `⟦BR⟧` renders as a divider, "continued on page N" or "continued in the right column", computed from the next fragment's page and x position.
  - **Narrow fallback:** when the gutter is narrower than **260 px**, `side = "below"` (inside the reader, arrow up, `maxHeight ≤ 45 %` of the viewport).
  - **First use:** the first hover ever shows a one-off Tooltip-style hint, "Hover a paragraph to see its translation", remembered in `useTranslationStore`.

- [ ] **Step 1: Write the failing tests** for the clamping, the scroll-fraction mapping, the arrow following the second fragment once the first scrolls out, the below-fallback at gutter 259 versus 260, both divider texts, and the hint showing once.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(frontend): keep long and split PDF translations readable`.

---

### Task 14: Wire the tracks together and run the gates

**Files:**
- Modify: `frontend/src/components/canvas/CitationCanvas.tsx`. It passes the paragraphs `status` to `TranslatePill.disabledReason`, wires `onRetranslate` to resend the visible blocks with `force: true`, and remembers on/off per paper.
- Modify: `CLAUDE.md` (plan table: an H1 row, *in progress → complete* is set by merge-prep).
- Test: `frontend/src/components/canvas/CitationCanvas.translation.test.tsx` covers the full MSW flow: toggle on → visible blocks requested → translations rendered → stale → re-translate.

- [ ] **Step 1: Write the failing integration test.** Then run `npx vitest run src/components/canvas/CitationCanvas.translation.test.tsx`. Expected: FAIL.
- [ ] **Step 2: Implement the wiring.** Run again. Expected: PASS.
- [ ] **Step 3: Backend gates.** From `backend/`: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`. Expected: green.
- [ ] **Step 4: Frontend gates.** From `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Expected: green.
- [ ] **Step 5: Commit** with `feat(canvas): connect bilingual reading end to end`.

**Stop here.** The morning session with the user covers these steps:
1. Rebuild Marker (`docker compose build marker`).
2. Run the inline-math check with the configured and local models.
3. Run `paperhub-backfill-assets --blocks-only` (sequential, GPU).
4. Run the live gate on `:8000`: an HTML paper and the Nature PDF; zh-TW, ja and a free-text language; formulas intact; a terminology memory honoured; `source.html` and `chunks` diffed unchanged.
5. Run `paperhub-merge-prep`.
