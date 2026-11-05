# Plan H0 — Shared Foundation for H1 / H2 / H3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the pieces H1, H2 and H3 all touch: page navigation slots for Ideas and Feedback, the private-document flag with its upload checkbox, one shared header-height token, and the enter/exit/collapse motion primitives. After this, the three sub-plans can run in parallel worktrees without editing the same foundations.

**Architecture:** Backend adds one additive column (`paper_content.is_private`) and exposes it on the upload form and the reference/library listings. Frontend adds a tiny `nav` store and switches the main pane between Chat, Ideas and Feedback. Chat stays **mounted but hidden**, so streams and the canvas survive. The new pages are placeholders that H2 and H3 fill. Motion primitives (`Collapsible`, `usePresence`, `revealInScroll`) turn the CLAUDE.md transition rule into reusable code.

**Tech Stack:** FastAPI + aiosqlite (backend), React 19 + zustand + Tailwind v4 + tw-animate-css + Base UI + lucide-react + react-i18next (frontend), pytest / Vitest + RTL.

**Spec:** `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10 (shared constraints + UI rules), §III-10.2 (*Storage*: private flag), §III-10.3 (*Private documents*). Visual base: `docs/superpowers/designs/2026-11-05-plan-h/` (sidebar entries, page header, lock marker, collapse/exit motion).

**Branch:** `feat/plan-h0-foundation` from `main` (after `docs/roadmap-new-plan-h` merges). H1 and H2 branch from `main` **after H0 merges**.

## Global Constraints

- Every user-facing string goes through `t("<ns>:<key>", "English fallback")` and exists in **all four** locales (`en`, `zh-TW`, `zh-CN`, `ja`); `src/locales/parity.test.ts` must stay green.
- Icons are lucide; no emoji.
- **No new UI colour.** Neutral tokens only. Yellow stays reserved for "passage cited by an answer".
- **Motion follows what the user expects** (CLAUDE.md *UI transitions*): enter = fade + zoom-in-95 (~150 ms); exit mirrors it (fade-out + zoom-out-95); inline collapse/expand = height ~200 ms ease-out; nothing visible unmounts instantly. Respect `prefers-reduced-motion`.
- Single user: the new column must not carry any key or uniqueness constraint that would block a later user column.
- Python via `uv` only (`uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`); frontend via `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`.
- Commits are Conventional Commits, `action(scope): imperative subject`, one concern per commit, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done = automated gates green.** No live `:8000` test and no merge in this plan's run; those happen with the user.

## Review Focus

- **Re-uploading a PDF that is already private without ticking the box keeps it private.** The flag only ever upgrades on upload and is never silently cleared. Pinned in Task 1.
- **Ticking "private" when re-uploading an already-public PDF (cache hit) marks it private.** Pinned in Task 1.
- **Switching to Ideas or Feedback while an answer is streaming must not interrupt it.** Chat stays mounted and comes back with the stream intact. Pinned in Task 4.
- **Toggling open → close → open within the exit window must not leave the element unmounted.** `usePresence` cancels the pending unmount. Pinned in Task 2.
- **Content inside a closed `Collapsible` must not be reachable by Tab or screen readers.** It is `inert` and `aria-hidden`. Pinned in Task 2.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/src/paperhub/db/schema.sql` | `is_private` on `paper_content` for fresh DBs |
| `backend/src/paperhub/db/migrate.py` | idempotent column-add for existing DBs |
| `backend/src/paperhub/api/papers.py` | upload `private` form field; `is_private` on `ReferenceItem` / `LibraryItem` |
| `frontend/src/index.css` | `--header-h` token |
| `frontend/src/components/ui/collapsible.tsx` | height-animated expand/collapse |
| `frontend/src/hooks/usePresence.ts` | keep an element mounted through its exit animation |
| `frontend/src/lib/motion.ts` | `revealInScroll` + `prefersReducedMotion` |
| `frontend/src/components/layout/PageHeader.tsx` | shared page header (icon, title, meta, actions) at `--header-h` |
| `frontend/src/store/nav.ts` | active page (`chat` / `ideas` / `feedback`) |
| `frontend/src/pages/IdeasPage.tsx`, `FeedbackPage.tsx` | placeholder pages (filled by H3 / H2) |
| `frontend/src/components/layout/Sidebar.tsx` | page entries + header height |
| `frontend/src/App.tsx` | page switching, chat kept mounted |
| `frontend/src/components/chat/AttachPaperMenu.tsx`, `lib/api.ts` | private checkbox → upload |
| `frontend/src/components/references/ReferenceSourcesPanel.tsx` | lock marker |
| `frontend/src/locales/{en,zh-TW,zh-CN,ja}/{states,chat,references}.json` | new strings |

---

### Task 1: Private-document flag (backend)

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql` (the `paper_content` table)
- Modify: `backend/src/paperhub/db/migrate.py` (`apply_schema`, next to the `asset_status` column-add block)
- Modify: `backend/src/paperhub/api/papers.py` (`ReferenceItem`, `LibraryItem`, `list_session_references`, `list_library`, `upload_paper`)
- Test: `backend/tests/test_migrate.py`, `backend/tests/test_papers_upload.py`, `backend/tests/test_papers_api.py`

**Interfaces:**
- Produces: column `paper_content.is_private INTEGER NOT NULL DEFAULT 0` (0/1); `POST /papers/upload` accepts form field `private: bool = False`; `ReferenceItem.is_private: bool` and `LibraryItem.is_private: bool` in responses. H2's export and H3's lock marker read this column.

- [ ] **Step 1: Write the failing tests**

```python
# test_migrate.py
async def test_paper_content_has_is_private_column(migrated_db):
    async with migrated_db.execute("PRAGMA table_info(paper_content)") as cur:
        cols = {row[1]: row for row in await cur.fetchall()}
    assert "is_private" in cols
    assert cols["is_private"][3] == 1          # NOT NULL
    assert cols["is_private"][4] == "0"        # DEFAULT 0

async def test_apply_schema_idempotent_for_is_private(tmp_path): ...  # apply twice, no raise

# test_papers_upload.py  (same isolated-app fixture pattern as the file's existing tests)
async def test_upload_private_flag_sets_is_private(...):
    # POST /papers/upload with data={"session_id": sid, "private": "true"}
    # → SELECT is_private FROM paper_content WHERE id = body["paper_content_id"] == 1
async def test_upload_default_is_public(...):            # no field → 0
async def test_reupload_without_flag_keeps_private(...): # private upload, then same bytes without flag → still 1
async def test_reupload_with_flag_upgrades_cache_hit(...):# public upload, then same bytes with private=true → cache_hit True and is_private 1

# test_papers_api.py
async def test_references_and_library_expose_is_private(...):
    # GET /papers?session_id=… and GET /papers/library?session_id=… items carry "is_private": bool
```

- [ ] **Step 2: Run them and confirm they fail**

Run (from `backend/`): `uv run pytest tests/test_migrate.py tests/test_papers_upload.py tests/test_papers_api.py -k "is_private or private" -v`
Expected: FAIL (column missing / unknown form field / missing key).

- [ ] **Step 3: Implement**

- `schema.sql`: add `is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1))` to `paper_content`.
- `migrate.py`: add a `PRAGMA table_info` guarded `ALTER TABLE paper_content ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0`, with a comment block in the file's existing style ("H0: private-document flag…").
- `upload_paper`: add `private: bool = Form(False)`. After `pipeline.ingest(...)` returns, and only when `private` is true, run `UPDATE paper_content SET is_private = 1 WHERE id = ?` and commit. Never write 0 here; the flag is upgrade-only on upload.
- Add `is_private` to both SELECTs and both response models.

- [ ] **Step 4: Run the tests and confirm they pass**

Same command. Expected: PASS. Then run `uv run ruff check src tests` and `uv run mypy src` (clean).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/db/schema.sql backend/src/paperhub/db/migrate.py backend/src/paperhub/api/papers.py backend/tests/test_migrate.py backend/tests/test_papers_upload.py backend/tests/test_papers_api.py
git commit -m "feat(papers): add the private-document flag to uploads"
```

---

### Task 2: Motion primitives

**Files:**
- Create: `frontend/src/components/ui/collapsible.tsx`, `frontend/src/hooks/usePresence.ts`, `frontend/src/lib/motion.ts`
- Test: `frontend/src/components/ui/collapsible.test.tsx`, `frontend/src/hooks/usePresence.test.ts`, `frontend/src/lib/motion.test.ts`

**Interfaces:**
- Produces:
  - `Collapsible({ open: boolean; children: ReactNode; className?: string; onExpanded?: (el: HTMLDivElement) => void })`. Outer `div` uses `grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none` with `grid-rows-[0fr]` / `grid-rows-[1fr]`. The inner `div` has `min-h-0 overflow-hidden`. When closed, the outer element gets `inert` + `aria-hidden="true"` and `data-state="closed"`; when open, `data-state="open"`. `onExpanded` fires on the `transitionend` that completes an open, or immediately under reduced motion.
  - `usePresence(open: boolean, exitMs = 150): { mounted: boolean; state: "open" | "closed" }`. `mounted` stays true for `exitMs` after `open` becomes false, while `state` is already `"closed"`, so callers render `data-state={state}` with tw-animate `data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95`. Reopening before the timer fires cancels the unmount.
  - `prefersReducedMotion(): boolean` and `revealInScroll(el: Element, block: "nearest" | "end" = "nearest"): void`, which calls `el.scrollIntoView({ block, behavior: prefersReducedMotion() ? "auto" : "smooth" })`.

- [ ] **Step 1: Write the failing tests**

```ts
// usePresence.test.ts (vi.useFakeTimers, renderHook)
it("stays mounted during the exit window, then unmounts", ...)   // open→false: {mounted:true,state:"closed"}; advance 150 → mounted:false
it("reopening mid-exit cancels the unmount", ...)                 // false, advance 100, true, advance 200 → mounted:true,state:"open"

// collapsible.test.tsx
it("closed content is inert and hidden from assistive tech", ...) // data-state="closed", has attr inert, aria-hidden="true"
it("open content is reachable", ...)                              // no inert, data-state="open"
it("calls onExpanded after the open transition", ...)             // fire transitionEnd(propertyName:"grid-template-rows") → called once

// motion.test.ts
it("revealInScroll uses smooth unless reduced motion", ...)       // stub matchMedia; assert scrollIntoView args
```

- [ ] **Step 2: Run and confirm failure**

Run (from `frontend/`): `npx vitest run src/hooks/usePresence.test.ts src/components/ui/collapsible.test.tsx src/lib/motion.test.ts`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement the three modules** to the interfaces above (a `setTimeout` held in a ref for `usePresence`, cleared on reopen and on unmount).

- [ ] **Step 4: Run and confirm pass**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/collapsible.tsx frontend/src/hooks/usePresence.ts frontend/src/lib/motion.ts frontend/src/components/ui/collapsible.test.tsx frontend/src/hooks/usePresence.test.ts frontend/src/lib/motion.test.ts
git commit -m "feat(frontend): add enter/exit and collapse motion primitives"
```

---

### Task 3: Shared header height and `PageHeader`

**Files:**
- Modify: `frontend/src/index.css` (`:root`; Tailwind 4.3's `h-(--header-h)` reads the variable directly, no `@theme` entry needed)
- Create: `frontend/src/components/layout/PageHeader.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (header row, currently `p-4 border-b`)
- Test: `frontend/src/components/layout/PageHeader.test.tsx`

**Interfaces:**
- Produces: CSS token `--header-h: 3.5rem`. `PageHeader({ icon: LucideIcon; title: string; meta?: ReactNode; actions?: ReactNode })`: a `shrink-0 h-(--header-h) flex items-center gap-2.5 px-5 border-b border-border` row containing an `h1` (`text-lg font-semibold`), muted `text-xs` meta, a flex spacer, then the actions. The Sidebar header becomes `h-(--header-h) px-4` (no vertical padding), so the two bottom rules align. That alignment was the user's explicit request in the design review.

- [ ] **Step 1: Write the failing test**

```ts
it("renders title as h1 with meta and actions", ...)    // getByRole("heading",{level:1,name:"Feedback"}); meta + button present
it("uses the shared header height", ...)                 // container.firstChild has class "h-(--header-h)"
```

- [ ] **Step 2: Run and confirm failure.** `npx vitest run src/components/layout/PageHeader.test.tsx` fails: module not found.
- [ ] **Step 3: Implement** the token, `PageHeader` and the Sidebar header change.
- [ ] **Step 4: Run and confirm pass.** Run the same test, plus `npx vitest run src/components/layout`, so the existing Sidebar and AccountMenu tests still pass.
- [ ] **Step 5: Commit** with `feat(frontend): share one header height between sidebar and pages`.

---

### Task 4: Page navigation (Ideas / Feedback slots)

**Files:**
- Create: `frontend/src/store/nav.ts`, `frontend/src/pages/IdeasPage.tsx`, `frontend/src/pages/FeedbackPage.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/layout/Sidebar.tsx`, `frontend/src/store/chat.ts` (`selectSession`)
- Modify: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/states.json`
- Test: `frontend/src/store/nav.test.ts`, `frontend/src/App.nav.test.tsx`

**Interfaces:**
- Consumes: `PageHeader` (Task 3).
- Produces:
  - `useNavStore`: `{ page: "chat" | "ideas" | "feedback"; feedbackBadge: number; setPage(p): void; setFeedbackBadge(n: number): void }`. Not persisted; the app always opens on chat. **H2 sets `feedbackBadge`** (count of items needing confirmation); **H3 and H2 render their pages inside `IdeasPage` / `FeedbackPage`**.
  - Sidebar: two entries **above the chat/references tabs**, Ideas (`Lightbulb`) and Feedback (`BarChart3`, with a `tabular-nums` count chip when `feedbackBadge > 0`). The active entry is `bg-accent`. The collapsed rail shows the same two icons, each with a Tooltip.
  - `selectSession(id)` and "New chat" also call `useNavStore.getState().setPage("chat")`.
  - App: the main pane renders `ChatPage` **always mounted**, wrapped in `<div hidden={page !== "chat"}>`. Ideas and Feedback mount when active. The incoming page fades in (`animate-in fade-in-0 duration-150`); page switches are lateral navigation, so a fade is the expected motion there.
  - Placeholders use `PageHeader` (Ideas: `Lightbulb`, "Ideas"; Feedback: `BarChart3`, "Feedback") plus a muted one-line empty state.
  - i18n keys (`states` namespace): `nav.ideas`, `nav.feedback`, `nav.feedbackBadge` (`"{{count}} need your confirmation"`), `ideasPage.empty`, `feedbackPage.empty`.

- [ ] **Step 1: Write the failing tests**

```ts
// nav.test.ts
it("defaults to chat and switches pages", ...)
it("selecting a chat session returns to the chat page", ...)   // setPage("ideas"); useChatStore.getState().selectSession(1) → page "chat"

// App.nav.test.tsx (render <App/> with the existing MSW handlers)
it("Feedback entry shows the feedback page and keeps chat mounted", ...)
//   click Feedback → heading "Feedback" visible; the chat composer textarea is still in the DOM (not removed) and inside an element with the hidden attribute
it("shows the feedback badge count", ...)                      // setFeedbackBadge(3) → chip "3" in the Feedback entry
```

- [ ] **Step 2: Run and confirm failure.** `npx vitest run src/store/nav.test.ts src/App.nav.test.tsx` fails.
- [ ] **Step 3: Implement** to the interfaces above, and add the keys to all four `states.json` files.
- [ ] **Step 4: Run and confirm pass.** Run the same tests, plus `npx vitest run src/locales/parity.test.ts src/components/layout`.
- [ ] **Step 5: Commit** with `feat(frontend): add Ideas and Feedback page slots to the sidebar`.

---

### Task 5: Private checkbox on upload and lock marker

**Files:**
- Modify: `frontend/src/lib/api.ts` (`uploadPdf`), `frontend/src/types/domain.ts` (`ReferenceItem`, `LibraryItem`)
- Modify: `frontend/src/components/chat/AttachPaperMenu.tsx` (the PDF file-input block), `frontend/src/components/references/ReferenceSourcesPanel.tsx`
- Modify: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/{chat,references}.json`
- Test: `frontend/src/lib/api.upload.test.ts`, `frontend/src/components/chat/AttachPaperMenu.private.test.tsx`, `frontend/src/components/references/ReferenceSourcesPanel.private.test.tsx`

**Interfaces:**
- Consumes: Task 1's `private` form field and `is_private` response fields.
- Produces:
  - `uploadPdf(sessionId: number, file: File, opts?: { private?: boolean }): Promise<IngestResult>`. It appends `private=true` only when set. `SearchResultList`'s manual-download upload keeps calling it without opts, because those are public OA papers and the spec says uploads are never inferred as private.
  - `ReferenceItem.is_private: boolean`, `LibraryItem.is_private: boolean`.
  - AttachPaperMenu: a checkbox, **unchecked by default**, placed directly above the PDF file input: "Private document" with muted help text "Marks it as a confidential document — shown with a lock." It resets to unchecked after each upload. The local reference built after upload carries `is_private`.
  - ReferenceSourcesPanel: rows with `is_private` show a lucide `Lock` (`h-3.5 w-3.5 text-muted-foreground`) before the title, with Tooltip "Private document".
  - i18n keys: `chat:attach.private`, `chat:attach.privateHelp`, `references:privateDocument`.

- [ ] **Step 1: Write the failing tests**

```ts
it("uploadPdf sends private=true only when asked", ...)   // MSW captures FormData: has "private"="true"; without opts → no "private" key
it("checkbox is unchecked by default and is sent when ticked", ...)
it("private references show a lock with an accessible label", ...) // getByLabelText("Private document") within the row
```

- [ ] **Step 2: Run and confirm failure.** `npx vitest run src/lib/api.upload.test.ts src/components/chat/AttachPaperMenu.private.test.tsx src/components/references/ReferenceSourcesPanel.private.test.tsx`
- [ ] **Step 3: Implement** to the interfaces above, and add the keys to all four locales.
- [ ] **Step 4: Run and confirm pass.** Run the same tests, plus `npx vitest run src/locales/parity.test.ts src/components/chat src/components/references`.
- [ ] **Step 5: Commit** with `feat(frontend): mark uploads as private documents`.

---

### Task 6: Full gates and spec pointer

**Files:**
- Modify: `docs/superpowers/specs/2026-05-17-paperhub-srs.md` §III-10.2 *Storage*: the private flag ships in **H0**, not H2
- Modify: `CLAUDE.md` (plan table: add the H0 row with this plan's link)

- [ ] **Step 1: Backend gates.** From `backend/`, run `uv run pytest -q`, `uv run ruff check src tests` and `uv run mypy src`. Expected: all green.
- [ ] **Step 2: Frontend gates.** From `frontend/`, run `npm test`, `npm run typecheck`, `npm run lint` and `npm run build`. Expected: all green.
- [ ] **Step 3: Update the SRS sentence and the CLAUDE.md plan table.**
- [ ] **Step 4: Commit** with `docs(spec): record the Plan H shared foundation as H0`.

Stop here. The live check (upload a PDF with the box ticked on `:8000`, see the lock, open both pages) and merge-prep happen with the user.
