# PaperHub Plan D — Citation Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `[chunk:<id>]` marker in an assistant answer a clickable superscript that opens a right-side Citation Canvas, loads the cited paper's pre-rendered HTML in a sandboxed iframe, locates the cited passage by content-search, and transiently highlights it (FR-03, SRS v2.13).

**Architecture:** A click resolves a chunk via a new read-only `GET /chunks/{id}` endpoint (`{paper_content_id, section, text}`). The HTML is already served by the existing `GET /papers/content/{id}/html`. The canvas hosts that document in a same-origin `<iframe sandbox="allow-scripts allow-same-origin">` (style isolation + MathJax + `contentDocument` access). A pure `findAndHighlight(doc, needle)` utility text-searches the live DOM — `chunks.char_start/char_end` index the *extracted* text and only align with the `<pre>` fallback renders, never the pandoc DOM, so resolution is **by content, not offset**. Canvas UI state lives in a small **unpersisted** Zustand store, separate from the persisted chat store. The canvas component is `React.lazy`-loaded (closes Plan B follow-up #1).

**Tech Stack:** Backend — FastAPI, aiosqlite, Pydantic v2, pytest (`uv run`). Frontend — React 19, TypeScript strict, Zustand, react-markdown + rehype, Vitest + RTL + MSW.

---

## File Structure

**Backend (create):**
- `backend/src/paperhub/api/chunks.py` — new `APIRouter(prefix="/chunks")` with a single `GET /{chunk_id}` resolver. One responsibility: resolve a chunk id to the data the canvas needs.
- `backend/tests/test_chunks_api.py` — endpoint tests (hit, 404).

**Backend (modify):**
- `backend/src/paperhub/app.py:20-21,143-146` — import + register the chunks router.

**Frontend (create):**
- `frontend/src/store/canvas.ts` — unpersisted Zustand store for canvas open/target state.
- `frontend/src/lib/chunkCitations.ts` — pure `buildChunkOrdinalMap(content)` (marker → per-message ordinal, deduped).
- `frontend/src/lib/rehypeChunkCitations.ts` — rehype plugin rewriting `[chunk:N]` text-node matches into `<sup>` citation elements.
- `frontend/src/lib/findAndHighlight.ts` — pure DOM locate-and-highlight utility.
- `frontend/src/components/canvas/CitationMarker.tsx` — the clickable superscript button.
- `frontend/src/components/canvas/CitationCanvas.tsx` — the right-side drawer + iframe host (lazy-loaded target).
- Tests: `frontend/tests/lib/chunkCitations.test.ts`, `frontend/tests/lib/findAndHighlight.test.ts`, `frontend/tests/store/canvas.test.ts`, `frontend/tests/components/CitationCanvas.test.tsx`, `frontend/tests/components/MessageBubble.citations.test.tsx`.

**Frontend (modify):**
- `frontend/src/types/domain.ts` — add `ChunkResolution` type.
- `frontend/src/lib/api.ts` — add `getChunk(chunkId)` client.
- `frontend/src/components/chat/MessageBubble.tsx` — pass the rehype plugin + `CitationMarker` component into the existing `ReactMarkdown`.
- `frontend/src/pages/ChatPage.tsx` — mount the lazy `CitationCanvas` under `<Suspense>`.

---

## Task 1: Backend — `GET /chunks/{id}` resolver

**Files:**
- Create: `backend/src/paperhub/api/chunks.py`
- Create: `backend/tests/test_chunks_api.py`
- Modify: `backend/src/paperhub/app.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_chunks_api.py`. This mirrors the ASGI client + `PAPERHUB_WORKSPACE` pattern from `test_papers_api.py`.

```python
"""Tests for the chunk-resolution endpoint (Plan D, FR-03 Citation Canvas)."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from paperhub.app import create_app
from paperhub.db.migrate import apply_schema


async def _seed_chunk(
    conn: aiosqlite.Connection,
    *,
    paper_content_id: int = 1,
    section: str | None = "3.2 Routing",
    text: str = "Expert collapse is mitigated by load balancing.",
) -> int:
    # paper_content has NOT-NULL columns + a CHECK that exactly one of
    # arxiv_id / sha256 is set; seed a minimal valid row first.
    await conn.execute(
        "INSERT OR IGNORE INTO paper_content "
        "(id, content_key, kind, arxiv_id, title, authors_json, year, abstract, "
        " source_path, source_dir_path, html_path) "
        "VALUES (?, ?, 'arxiv', ?, ?, '[]', 2024, '', '/tmp/s.tex', '/tmp', '/tmp/s.html')",
        (paper_content_id, f"arxiv:test-{paper_content_id}", f"test-{paper_content_id}",
         "Test Paper"),
    )
    await conn.execute(
        "INSERT INTO chunks (paper_content_id, section, char_start, char_end, text) "
        "VALUES (?, ?, 0, ?, ?)",
        (paper_content_id, section, len(text), text),
    )
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_get_chunk_returns_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        chunk_id = await _seed_chunk(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/chunks/{chunk_id}")

    assert r.status_code == 200
    body = r.json()
    assert body == {
        "id": chunk_id,
        "paper_content_id": 1,
        "section": "3.2 Routing",
        "text": "Expert collapse is mitigated by load balancing.",
    }


async def test_get_chunk_404_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/chunks/99999")

    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `uv run pytest tests/test_chunks_api.py -v`
Expected: FAIL — `404` for both (no `/chunks` route registered yet; FastAPI returns 404 for the happy-path call too).

- [ ] **Step 3: Create the chunks router**

Create `backend/src/paperhub/api/chunks.py`:

```python
"""Chunk-resolution surface (SRS v2.13, FR-03 Citation Canvas).

The canvas resolves a `[chunk:<id>]` click to the data it needs to
text-search the paper's rendered HTML: which paper to load
(`paper_content_id`) and what passage to find (`text`). Read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub.config import load_settings
from paperhub.db.connection import open_db

router = APIRouter(prefix="/chunks", tags=["chunks"])


class ChunkResolution(BaseModel):
    id: int
    paper_content_id: int
    section: str | None
    text: str


@router.get("/{chunk_id}", response_model=ChunkResolution)
async def get_chunk(chunk_id: int) -> ChunkResolution:
    settings = load_settings()
    async with (
        open_db(settings.db_path) as conn,
        conn.execute(
            "SELECT id, paper_content_id, section, text FROM chunks WHERE id = ?",
            (chunk_id,),
        ) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"no chunk {chunk_id}")
    return ChunkResolution(
        id=int(row[0]),
        paper_content_id=int(row[1]),
        section=row[2],
        text=row[3],
    )
```

- [ ] **Step 4: Register the router**

In `backend/src/paperhub/app.py`, add to the imports block (next to the other `from paperhub.api import ...` lines, ~line 20):

```python
from paperhub.api import chunks as chunks_api
```

And in `create_app()` after `app.include_router(papers_api.router)` (~line 146):

```python
    app.include_router(chunks_api.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `backend/`): `uv run pytest tests/test_chunks_api.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run quality gates**

Run (from `backend/`): `uv run ruff check src tests; uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/src/paperhub/api/chunks.py backend/tests/test_chunks_api.py backend/src/paperhub/app.py
git commit -m "feat(api): GET /chunks/{id} resolver for Citation Canvas (FR-03)"
```

---

## Task 2: Frontend — `getChunk` API client + type

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/tests/lib/api.test.ts` (append)

- [ ] **Step 1: Write the failing test**

Append to `frontend/tests/lib/api.test.ts`. Check the file's top for its existing MSW server setup; if it has none, add this self-contained block. (The describe below sets up its own server so it works regardless.)

```typescript
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { getChunk, API_BASE_URL } from "@/lib/api";

const chunkServer = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2 Routing",
      text: "Expert collapse is mitigated by load balancing.",
    }),
  ),
  http.get(`${API_BASE_URL}/chunks/999`, () =>
    HttpResponse.json({ detail: "no chunk 999" }, { status: 404 }),
  ),
);

describe("getChunk", () => {
  beforeAll(() => chunkServer.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => chunkServer.close());

  it("resolves a chunk id to its paper + text", async () => {
    const c = await getChunk(42);
    expect(c.paper_content_id).toBe(7);
    expect(c.text).toContain("Expert collapse");
    expect(c.section).toBe("3.2 Routing");
  });

  it("throws on 404", async () => {
    await expect(getChunk(999)).rejects.toThrow(/404/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/lib/api.test.ts -t getChunk`
Expected: FAIL — `getChunk` is not exported.

- [ ] **Step 3: Add the type**

In `frontend/src/types/domain.ts`, add after the `LibraryItem` interface:

```typescript
export interface ChunkResolution {
  id: number;
  paper_content_id: number;
  section: string | null;
  text: string;
}
```

- [ ] **Step 4: Add the client**

In `frontend/src/lib/api.ts`, add `ChunkResolution` to the type import block at the top, then append:

```typescript
/** Resolve a `[chunk:<id>]` citation marker to the paper it lives in and the
 * passage text the Citation Canvas searches for in the rendered HTML. */
export async function getChunk(chunkId: number): Promise<ChunkResolution> {
  return apiFetch<ChunkResolution>(`/chunks/${chunkId}`);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/lib/api.test.ts -t getChunk`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts frontend/tests/lib/api.test.ts
git commit -m "feat(frontend): getChunk client + ChunkResolution type"
```

---

## Task 3: Frontend — canvas store (unpersisted)

**Files:**
- Create: `frontend/src/store/canvas.ts`
- Test: `frontend/tests/store/canvas.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/store/canvas.test.ts`:

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("canvas store", () => {
  it("starts closed", () => {
    const s = useCanvasStore.getState();
    expect(s.open).toBe(false);
    expect(s.chunkId).toBeNull();
  });

  it("openCitation sets target + open", () => {
    useCanvasStore.getState().openCitation(42);
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.chunkId).toBe(42);
  });

  it("closeCanvas resets open but is idempotent", () => {
    useCanvasStore.getState().openCitation(42);
    useCanvasStore.getState().closeCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("re-opening with a new chunk updates the target", () => {
    useCanvasStore.getState().openCitation(42);
    useCanvasStore.getState().openCitation(7);
    expect(useCanvasStore.getState().chunkId).toBe(7);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/store/canvas.test.ts`
Expected: FAIL — module `@/store/canvas` not found.

- [ ] **Step 3: Create the store**

Create `frontend/src/store/canvas.ts`. Deliberately **not** wrapped in `persist` — canvas state is ephemeral UI, unlike the chat store.

```typescript
import { create } from "zustand";

interface CanvasState {
  open: boolean;
  /** The chunk whose passage we want to scroll to + highlight. */
  chunkId: number | null;
  openCitation: (chunkId: number) => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  open: false,
  chunkId: null,
  openCitation: (chunkId) => set({ open: true, chunkId }),
  closeCanvas: () => set({ open: false }),
}));
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/store/canvas.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/canvas.ts frontend/tests/store/canvas.test.ts
git commit -m "feat(frontend): unpersisted canvas store for Citation Canvas"
```

---

## Task 4: Frontend — `buildChunkOrdinalMap` (pure)

**Files:**
- Create: `frontend/src/lib/chunkCitations.ts`
- Test: `frontend/tests/lib/chunkCitations.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/lib/chunkCitations.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { buildChunkOrdinalMap, CHUNK_MARKER_RE } from "@/lib/chunkCitations";

describe("buildChunkOrdinalMap", () => {
  it("assigns ordinals in first-appearance order", () => {
    const m = buildChunkOrdinalMap("a[chunk:50]b[chunk:12]c");
    expect(m.get(50)).toBe(1);
    expect(m.get(12)).toBe(2);
  });

  it("dedupes: a re-cited chunk reuses its ordinal", () => {
    const m = buildChunkOrdinalMap("[chunk:7] then [chunk:9] then [chunk:7]");
    expect(m.get(7)).toBe(1);
    expect(m.get(9)).toBe(2);
    expect(m.size).toBe(2);
  });

  it("returns an empty map when there are no markers", () => {
    expect(buildChunkOrdinalMap("no citations here").size).toBe(0);
  });

  it("CHUNK_MARKER_RE matches [chunk:<digits>] globally", () => {
    const matches = [...":a[chunk:1]b[chunk:23]".matchAll(CHUNK_MARKER_RE)];
    expect(matches.map((x) => x[1])).toEqual(["1", "23"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/lib/chunkCitations.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the module**

Create `frontend/src/lib/chunkCitations.ts`:

```typescript
/** Matches `[chunk:<id>]` markers emitted by the paper_qa finalizer.
 * `g` flag so it can drive matchAll / replace; capture group 1 is the id. */
export const CHUNK_MARKER_RE = /\[chunk:(\d+)\]/g;

/**
 * Map each distinct chunk id in a message to its citation ordinal (1-based),
 * assigned in order of first appearance and deduped (a re-cited chunk keeps
 * its first ordinal). Used to render academic-style superscripts.
 */
export function buildChunkOrdinalMap(content: string): Map<number, number> {
  const map = new Map<number, number>();
  // Reset lastIndex defensively — the regex is module-level + stateful with /g.
  CHUNK_MARKER_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = CHUNK_MARKER_RE.exec(content)) !== null) {
    const id = Number(m[1]);
    if (!map.has(id)) map.set(id, map.size + 1);
  }
  return map;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/lib/chunkCitations.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/chunkCitations.ts frontend/tests/lib/chunkCitations.test.ts
git commit -m "feat(frontend): buildChunkOrdinalMap for citation numbering"
```

---

## Task 5: Frontend — `findAndHighlight` DOM utility (pure)

**Files:**
- Create: `frontend/src/lib/findAndHighlight.ts`
- Test: `frontend/tests/lib/findAndHighlight.test.ts`

This is the load-bearing locate-and-highlight logic. It is decoupled from the iframe and `scrollIntoView` (guarded), so it's testable against a plain jsdom `Document`.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/lib/findAndHighlight.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { findAndHighlight, HIGHLIGHT_CLASS } from "@/lib/findAndHighlight";

function docFrom(html: string): Document {
  return new DOMParser().parseFromString(
    `<!DOCTYPE html><html><body>${html}</body></html>`,
    "text/html",
  );
}

describe("findAndHighlight", () => {
  it("finds text within a single text node and highlights it", () => {
    const doc = docFrom("<p>Expert collapse is mitigated by load balancing.</p>");
    const ok = findAndHighlight(doc, "Expert collapse is mitigated");
    expect(ok).toBe(true);
    expect(doc.querySelector(`.${HIGHLIGHT_CLASS}`)).not.toBeNull();
  });

  it("normalizes whitespace across the needle and the DOM", () => {
    // DOM has a newline + indentation; needle has collapsed single spaces.
    const doc = docFrom("<p>Expert collapse\n   is   mitigated by balancing.</p>");
    const ok = findAndHighlight(doc, "Expert collapse is mitigated by balancing.");
    expect(ok).toBe(true);
  });

  it("matches on a long needle's prefix (rendering drops the tail)", () => {
    const doc = docFrom("<p>The router assigns tokens to experts.</p>");
    const longNeedle =
      "The router assigns tokens to experts. " +
      "Then $\\mathcal{L}$ regularizes — math the renderer mangled.";
    expect(findAndHighlight(doc, longNeedle)).toBe(true);
  });

  it("returns false when the passage is absent", () => {
    const doc = docFrom("<p>Completely unrelated content.</p>");
    expect(findAndHighlight(doc, "this text does not appear anywhere")).toBe(false);
  });

  it("removes a prior highlight before adding a new one", () => {
    const doc = docFrom("<p>alpha bravo charlie delta echo foxtrot.</p>");
    findAndHighlight(doc, "alpha bravo");
    findAndHighlight(doc, "charlie delta");
    expect(doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`).length).toBe(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/lib/findAndHighlight.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the utility**

Create `frontend/src/lib/findAndHighlight.ts`:

```typescript
export const HIGHLIGHT_CLASS = "ph-cite-hl";

/** How many leading characters of the chunk text to match. Rendering
 * (math, ligatures, figure captions) mangles the tail of dense passages,
 * so a normalized prefix match is far more reliable than full-text. */
const PREFIX_LEN = 150;
const HIGHLIGHT_MS = 2500;

const normalize = (s: string): string => s.replace(/\s+/g, " ").trim();

interface NodeSpan {
  node: Text;
  start: number; // index into the concatenated normalized string
  end: number;
}

/**
 * Locate `needle` (by normalized prefix) inside `doc`, scroll it into view,
 * and apply a transient highlight. Returns whether a match was found.
 *
 * Decoupled from the iframe + from layout: `scrollIntoView` is feature-detected
 * so this runs under jsdom. The highlight is wrapped in a <mark> when the match
 * lies within one text node, else applied as a class on the start node's parent
 * element (robust across node boundaries without fragile Range surgery).
 */
export function findAndHighlight(doc: Document, needle: string): boolean {
  const target = normalize(needle).slice(0, PREFIX_LEN);
  if (!target) return false;

  clearHighlight(doc);

  // Build a concatenated normalized string with a node→offset index.
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const spans: NodeSpan[] = [];
  let combined = "";
  let cursor: Node | null = walker.nextNode();
  while (cursor) {
    const textNode = cursor as Text;
    const norm = normalize(textNode.data);
    if (norm) {
      // Join with a single space so adjacent block elements don't fuse words.
      const prefix = combined.length > 0 ? " " : "";
      const start = combined.length + prefix.length;
      combined += prefix + norm;
      spans.push({ node: textNode, start, end: combined.length });
    }
    cursor = walker.nextNode();
  }

  const hitIndex = combined.indexOf(target);
  if (hitIndex < 0) return false;

  const span = spans.find((s) => hitIndex >= s.start && hitIndex < s.end);
  if (!span) return false;

  const el = span.node.parentElement;
  if (el) {
    el.classList.add(HIGHLIGHT_CLASS);
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const win = doc.defaultView;
    const setTimeoutFn = win?.setTimeout ?? globalThis.setTimeout;
    setTimeoutFn(() => el.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
  }
  return true;
}

function clearHighlight(doc: Document): void {
  doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`).forEach((el) => {
    el.classList.remove(HIGHLIGHT_CLASS);
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/lib/findAndHighlight.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/findAndHighlight.ts frontend/tests/lib/findAndHighlight.test.ts
git commit -m "feat(frontend): findAndHighlight content-search DOM utility"
```

---

## Task 6: Frontend — citation markers in MessageBubble

Wire `[chunk:N]` markers into the existing `ReactMarkdown` render via a rehype plugin that rewrites matching text nodes into superscript citation elements, mapped to a clickable `CitationMarker`. Clicking calls the canvas store's `openCitation`.

**Files:**
- Create: `frontend/src/lib/rehypeChunkCitations.ts`
- Create: `frontend/src/components/canvas/CitationMarker.tsx`
- Modify: `frontend/src/components/chat/MessageBubble.tsx`
- Test: `frontend/tests/components/MessageBubble.citations.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/components/MessageBubble.citations.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { useCanvasStore } from "@/store/canvas";
import type { ChatMessage } from "@/types/domain";

function assistantMsg(content: string): ChatMessage {
  return { role: "assistant", content, run_id: 1, status: "ok" };
}

beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("MessageBubble citation markers", () => {
  it("renders [chunk:id] as a deduped superscript ordinal", () => {
    render(
      <MessageBubble
        message={assistantMsg("Collapse is mitigated[chunk:50] though[chunk:12], see[chunk:50].")}
      />,
    );
    // chunk 50 -> 1 (appears twice, same ordinal), chunk 12 -> 2.
    const ones = screen.getAllByRole("button", { name: /citation 1/i });
    expect(ones).toHaveLength(2);
    expect(screen.getByRole("button", { name: /citation 2/i })).toBeInTheDocument();
    expect(ones[0]).toHaveTextContent("1");
  });

  it("clicking a marker opens the canvas on that chunk", async () => {
    render(
      <MessageBubble message={assistantMsg("balanced[chunk:77].")} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /citation 1/i }));
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.chunkId).toBe(77);
  });

  it("leaves text without markers untouched", () => {
    render(<MessageBubble message={assistantMsg("plain answer, no citations")} />);
    expect(screen.queryByRole("button", { name: /citation/i })).toBeNull();
    expect(screen.getByText(/plain answer/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/components/MessageBubble.citations.test.tsx`
Expected: FAIL — markers render as literal text; no citation buttons.

- [ ] **Step 3: Create the rehype plugin**

Create `frontend/src/lib/rehypeChunkCitations.ts`. It walks the hast tree, splits text nodes on `[chunk:N]`, and replaces each marker with a custom element node `chunk-cite` carrying the id + ordinal as properties. react-markdown maps `chunk-cite` to our component via the `components` prop.

```typescript
import type { Root, Text, Element, ElementContent } from "hast";
import { visit } from "unist-util-visit";
import { CHUNK_MARKER_RE, buildChunkOrdinalMap } from "@/lib/chunkCitations";

/**
 * Rehype plugin: rewrite `[chunk:<id>]` text occurrences into
 * `<chunk-cite data-chunk-id data-ordinal>` element nodes. The ordinal map is
 * built once per tree from the concatenated text so numbering + dedup are
 * stable across text nodes split by inline markdown.
 */
export function rehypeChunkCitations() {
  return (tree: Root): void => {
    // Concatenate all text to compute a tree-wide ordinal map.
    let full = "";
    visit(tree, "text", (node: Text) => {
      full += node.value;
    });
    const ordinals = buildChunkOrdinalMap(full);
    if (ordinals.size === 0) return;

    visit(tree, "text", (node: Text, index, parent) => {
      if (parent == null || index == null) return;
      if (!node.value.includes("[chunk:")) return;

      const out: ElementContent[] = [];
      let last = 0;
      CHUNK_MARKER_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = CHUNK_MARKER_RE.exec(node.value)) !== null) {
        if (m.index > last) {
          out.push({ type: "text", value: node.value.slice(last, m.index) });
        }
        const id = Number(m[1]);
        const ordinal = ordinals.get(id) ?? 0;
        const cite: Element = {
          type: "element",
          tagName: "chunk-cite",
          properties: { dataChunkId: id, dataOrdinal: ordinal },
          children: [],
        };
        out.push(cite);
        last = m.index + m[0].length;
      }
      if (last < node.value.length) {
        out.push({ type: "text", value: node.value.slice(last) });
      }
      (parent as Element).children.splice(index, 1, ...out);
      return index + out.length; // skip the nodes we just inserted
    });
  };
}
```

> Note: `unist-util-visit` and `hast` types ship transitively with `react-markdown`/`remark`. If `npm run typecheck` reports them missing, add them explicitly: `npm i -D unist-util-visit @types/hast`.

- [ ] **Step 4: Create the CitationMarker component**

Create `frontend/src/components/canvas/CitationMarker.tsx`:

```typescript
import { useCanvasStore } from "@/store/canvas";

interface Props {
  chunkId: number;
  ordinal: number;
}

/** Academic-style superscript citation. Clicking opens the Citation Canvas
 * on the cited chunk (FR-03). */
export function CitationMarker({ chunkId, ordinal }: Props) {
  const openCitation = useCanvasStore((s) => s.openCitation);
  return (
    <sup>
      <button
        type="button"
        aria-label={`citation ${ordinal}`}
        onClick={() => openCitation(chunkId)}
        className="mx-0.5 cursor-pointer rounded px-1 text-[0.7em] font-medium text-primary hover:bg-primary/10 hover:underline"
      >
        {ordinal}
      </button>
    </sup>
  );
}
```

- [ ] **Step 5: Wire the plugin + component into MessageBubble**

In `frontend/src/components/chat/MessageBubble.tsx`, add imports near the top:

```typescript
import { rehypeChunkCitations } from "@/lib/rehypeChunkCitations";
import { CitationMarker } from "@/components/canvas/CitationMarker";
```

Then change the existing `<ReactMarkdown ...>` invocation (currently with `remarkPlugins` + `rehypePlugins`) to register the plugin and map the `chunk-cite` element. react-markdown lowercases tag names and passes unknown HTML attributes through; read `node.properties` to get the typed values:

```tsx
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex, rehypeChunkCitations]}
              components={{
                // The rehype plugin emits <chunk-cite data-chunk-id data-ordinal>.
                // react-markdown passes the hast node so we read the numeric props.
                "chunk-cite": ({ node }) => {
                  const props = (node?.properties ?? {}) as {
                    dataChunkId?: number | string;
                    dataOrdinal?: number | string;
                  };
                  return (
                    <CitationMarker
                      chunkId={Number(props.dataChunkId)}
                      ordinal={Number(props.dataOrdinal)}
                    />
                  );
                },
              }}
            >
              {message.content || " "}
            </ReactMarkdown>
```

> If TypeScript rejects the `"chunk-cite"` key on the `components` map (it types known HTML tags), cast the components object: `components={ { "chunk-cite": ... } as Components}` importing `import type { Components } from "react-markdown";`.

- [ ] **Step 6: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/components/MessageBubble.citations.test.tsx`
Expected: PASS (3 tests). Also re-run the existing bubble test to ensure no regression: `npx vitest run tests/components/MessageBubble.test.tsx`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/rehypeChunkCitations.ts frontend/src/components/canvas/CitationMarker.tsx frontend/src/components/chat/MessageBubble.tsx frontend/tests/components/MessageBubble.citations.test.tsx
git commit -m "feat(frontend): render [chunk:id] markers as clickable superscripts"
```

---

## Task 7: Frontend — CitationCanvas drawer + iframe host

The drawer reads canvas store state, resolves the chunk via `getChunk`, loads the paper HTML in a sandboxed iframe, and runs `findAndHighlight` on the iframe document once it loads. A failed search shows a toast (NFR-02 — no silent failure).

**Files:**
- Create: `frontend/src/components/canvas/CitationCanvas.tsx`
- Test: `frontend/tests/components/CitationCanvas.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/components/CitationCanvas.test.tsx`. jsdom doesn't load iframe `src`, so the test asserts the resolved iframe URL + drawer behavior, not real highlighting (that's covered by Task 5's unit tests).

```typescript
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { useCanvasStore } from "@/store/canvas";
import { API_BASE_URL } from "@/lib/api";

const server = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2 Routing",
      text: "Expert collapse is mitigated.",
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("CitationCanvas", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("opens, resolves the chunk, and points the iframe at the paper HTML", async () => {
    render(<CitationCanvas />);
    useCanvasStore.getState().openCitation(42);

    const iframe = await screen.findByTitle(/citation canvas/i);
    await waitFor(() =>
      expect(iframe).toHaveAttribute(
        "src",
        `${API_BASE_URL}/papers/content/7/html`,
      ),
    );
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
  });

  it("closes when the close button is clicked", async () => {
    render(<CitationCanvas />);
    useCanvasStore.getState().openCitation(42);
    await screen.findByTitle(/citation canvas/i);

    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(useCanvasStore.getState().open).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run tests/components/CitationCanvas.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/canvas/CitationCanvas.tsx`:

```typescript
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";

import { useCanvasStore } from "@/store/canvas";
import { getChunk, API_BASE_URL } from "@/lib/api";
import { findAndHighlight } from "@/lib/findAndHighlight";
import { Button } from "@/components/ui/button";
import type { ChunkResolution } from "@/types/domain";

export function CitationCanvas() {
  const open = useCanvasStore((s) => s.open);
  const chunkId = useCanvasStore((s) => s.chunkId);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);

  const [chunk, setChunk] = useState<ChunkResolution | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Resolve the chunk whenever the target changes.
  useEffect(() => {
    if (!open || chunkId == null) return;
    let cancelled = false;
    getChunk(chunkId)
      .then((c) => {
        if (!cancelled) setChunk(c);
      })
      .catch(() => {
        if (!cancelled) toast.error("Couldn't load the cited paper");
      });
    return () => {
      cancelled = true;
    };
  }, [open, chunkId]);

  if (!open) return null;

  const src =
    chunk == null ? undefined : `${API_BASE_URL}/papers/content/${chunk.paper_content_id}/html`;

  const handleIframeLoad = (): void => {
    if (chunk == null) return;
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    const found = findAndHighlight(doc, chunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  };

  return (
    <aside
      className="fixed right-0 top-0 z-40 flex h-full w-[min(560px,45vw)] flex-col border-l border-border bg-card shadow-xl"
      aria-label="Citation Canvas"
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="truncate text-sm font-medium">
          {chunk?.section ? `§ ${chunk.section}` : "Cited passage"}
        </span>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7"
          aria-label="Close canvas"
          onClick={closeCanvas}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>
      {src && (
        <iframe
          ref={iframeRef}
          title="Citation Canvas"
          src={src}
          onLoad={handleIframeLoad}
          sandbox="allow-scripts allow-same-origin"
          className="h-full w-full flex-1 bg-white"
        />
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run tests/components/CitationCanvas.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/canvas/CitationCanvas.tsx frontend/tests/components/CitationCanvas.test.tsx
git commit -m "feat(frontend): CitationCanvas drawer with sandboxed iframe host"
```

---

## Task 8: Frontend — lazy-mount the canvas (code-split)

Mount `CitationCanvas` in the page via `React.lazy` + `Suspense` so its code (and the rehype/hast machinery it pulls) only loads when a citation is first opened. Closes Plan B known-follow-up #1.

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`

- [ ] **Step 1: Add the lazy import + Suspense mount**

In `frontend/src/pages/ChatPage.tsx`, add at the top:

```typescript
import { lazy, Suspense } from "react";
import { useCanvasStore } from "@/store/canvas";

const CitationCanvas = lazy(() =>
  import("@/components/canvas/CitationCanvas").then((m) => ({
    default: m.CitationCanvas,
  })),
);
```

Inside the component, read whether the canvas is open and render it under Suspense (the canvas itself returns null when closed, but gating the mount on `open` is what defers the chunk load + keeps the lazy chunk un-fetched until first use):

```tsx
export function ChatPage() {
  useGlobalShortcuts();
  useReferencesSync();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const canvasOpen = useCanvasStore((s) => s.open);
  const { send } = useChatStream();

  // ... existing activeSession / isStreaming / handleSubmit unchanged ...

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <ChatThread session={activeSession} />
      <Composer onSubmit={handleSubmit} disabled={isStreaming} />
      {canvasOpen && (
        <Suspense fallback={null}>
          <CitationCanvas />
        </Suspense>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the production build code-splits the canvas**

Run (from `frontend/`): `npm run build`
Expected: build succeeds AND the output lists a separate chunk for `CitationCanvas` (a `CitationCanvas-*.js` asset in the Vite chunk list), confirming the split.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(frontend): lazy-load CitationCanvas (closes Plan B follow-up #1)"
```

---

## Task 9: Full quality gates + manual smoke

**Files:** none (verification only).

- [ ] **Step 1: Backend gates**

Run (from `backend/`):
```powershell
uv run pytest -v
uv run ruff check src tests
uv run mypy src
```
Expected: all pass (existing suite + the 2 new chunk tests).

- [ ] **Step 2: Frontend gates**

Run (from `frontend/`):
```powershell
npm test
npm run typecheck
npm run lint
npm run build
```
Expected: all pass (existing suite + the new canvas/citation tests).

- [ ] **Step 3: Manual smoke (real backend)**

Start the stack (`scripts/start.ps1`), open a session, attach a paper, ask a `paper_qa` question that produces `[chunk:<id>]` markers, and verify: markers render as superscripts; clicking one opens the right drawer; the paper HTML loads; the cited passage scrolls into view + highlights; clicking a marker from a different paper swaps the document; the close button dismisses the drawer.

- [ ] **Step 4: Add the Plan D quality-gate note to CLAUDE.md (optional housekeeping)**

If desired, update the Plan table in `CLAUDE.md` to mark Plan D status + link this document, and move Plan B follow-up #1 to "closed".

---

## Self-Review

**Spec coverage (FR-03):**
- "Inline citations rendered as clickable buttons" → Task 6 (`CitationMarker` superscripts).
- "sequential superscripts, deduped per message" → Task 4 (`buildChunkOrdinalMap`) + Task 6.
- "Click opens/focuses a right-pane canvas" → Task 7 (`CitationCanvas` drawer) + Task 3 (store).
- "loads the paper's pre-rendered HTML" → Task 7 (iframe `src` = existing `/papers/content/{id}/html`).
- "locate-and-highlight by content, not offset" → Task 5 (`findAndHighlight`) + Task 1 (`/chunks/{id}` supplies `text`).
- "failed search → toast, never silent (NFR-02)" → Task 7 (`toast.message` on miss).
- "closes / switches papers without page reload" → Task 7 (store-driven `src` swap; close button).
- "same-origin sandboxed iframe" → Task 7 (`sandbox="allow-scripts allow-same-origin"`).
- Plan B follow-up #1 (code-split) → Task 8.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output.

**Type consistency:** `ChunkResolution` (Task 2) is consumed in Tasks 6/7; the backend `ChunkResolution` Pydantic model (Task 1) matches its fields (`id`, `paper_content_id`, `section`, `text`). `useCanvasStore` actions (`openCitation`, `closeCanvas`) and state (`open`, `chunkId`) defined in Task 3 are used identically in Tasks 6/7/8. `HIGHLIGHT_CLASS` / `CHUNK_MARKER_RE` / `buildChunkOrdinalMap` exported in Tasks 4-5 are imported by name in Tasks 5/6. `getChunk` (Task 2) used in Task 7.

**Out of scope (per brainstorm):** section-jump fallback on a failed search (only a toast is in scope); standalone canvas-open from the Reference Sources panel.

---

# Wave 2 — Citation Canvas → Reference Reading Panel (post-review redesign)

> **Status:** added after Wave 1 shipped + live UI testing. Wave 1 delivered a single-chunk overlay drawer. Live testing surfaced three problems that this wave fixes. The earlier "standalone canvas-open" item that Wave 1 declared out-of-scope is now **in scope** (the user wants it).

## Why this wave exists

1. **Bad UX — overlay, not push.** Wave 1's `CitationCanvas` is `fixed right-0 z-40` — it floats over the chat. The left chat sidebar (`Shell.tsx`) instead uses an animated CSS-grid column that *pushes* `<main>`. The canvas must mirror that: a right-side column that pushes the chat and animates open/closed.
2. **Single-paper only.** Wave 1 shows exactly the one paper tied to the clicked citation, with no way to browse the session's other references. The redesign turns the canvas into a **reading panel** with a **paper switcher** at the top — one tab per enabled reference, overflowing to a "…" dropdown when there are more than fit. The composer **References** button (currently a disabled `BookOpen` placeholder tooltipped "Coming in Plan D — toggle which papers are in scope for this turn") becomes the **toggle** that opens/closes this panel.
3. **404 on citation click (diagnosed, evidence-based).** Clicking a citation in older answers 404s. Root cause confirmed against the live `workspace/paperhub.db`: cited ids `74674…74792` are **absent** from `chunks` (current range `74924–76741`), while newer ids (`74927`, `75528`, …) resolve fine. The `workspace/*.bak.v2.10` artefacts show the corpus was **re-ingested**; `paperhub-reingest` preserves `paper_content.id` but **deletes + re-inserts `chunks` with fresh AUTOINCREMENT ids**, orphaning every `[chunk:<id>]` marker in answers generated before the reingest. The click path is correct (the Wave 1 Task 6 test proves the real id is passed). **Plan D scope:** handle the 404 gracefully (clear inline "passage no longer available — paper re-indexed" notice, distinct from a network error; NFR-02). **Out of Plan D scope (Plan C reingest follow-up):** making chunk ids stable across reingest so historical citations survive.

## Target behaviour

- Right-side panel that **pushes** the chat (animated grid column; collapses to zero width when closed), mirroring the left sidebar.
- **Paper switcher** header: a tab per *enabled* reference paper of the active session; when more than `MAX_VISIBLE_TABS` (3), the overflow goes into a "…" dropdown. The active tab is the displayed paper.
- The composer **References** (`BookOpen`) button **toggles** the panel (open in browse mode / close).
- Clicking a `[chunk:<id>]` marker opens the panel, switches the switcher to that chunk's paper, and scrolls+highlights the passage. Opening via the References button shows the active/first paper with **no** highlight (browse mode).
- A stale/missing chunk (`getChunk` 404) shows an inline notice in the panel (panel still opens, tabs still usable); a real network error shows a `toast.error`.

## File structure (Wave 2)

- **Modify:** `frontend/src/store/canvas.ts` — redesign state (`requestedChunkId`, `requestNonce`) + actions (`openCitation`, `toggleCanvas`, `closeCanvas`).
- **Rewrite:** `frontend/src/components/canvas/CitationCanvas.tsx` — reading panel (switcher + iframe + resolve + 404 + highlight), fill-column root (not `fixed`).
- **Modify:** `frontend/src/pages/ChatPage.tsx` — horizontal push layout (animated grid; canvas pushes chat).
- **Modify:** `frontend/src/components/chat/Composer.tsx` — wire the `References` button to `toggleCanvas`, remove its disabled placeholder.
- **Tests:** update `frontend/tests/store/canvas.test.ts`, `frontend/tests/components/CitationCanvas.test.tsx`; add `frontend/tests/components/Composer.canvas.test.tsx` (or extend the existing Composer test).

`CitationMarker` (Wave 1) is unchanged — it still calls `openCitation(chunkId)`.

---

## Task W2-0: Backend — serve PDF for PDF-rendered papers

**Why:** The canvas renders `paper_content.html_path`. For LaTeX-sourced papers that HTML is good (pandoc). But papers rendered from a PDF — both `kind='pdf_upload'` AND `kind='arxiv'` that fell back to the arXiv source-archive PDF (SRS v2.7 size-cap fallback) — get a PyMuPDF `get_text("html")` render that is visually broken. For those, the canvas should display the original PDF (browser-native viewer), not the broken HTML. `kind` is NOT a reliable discriminator (arxiv-PDF-fallback keeps `kind='arxiv'`). The reliable signal is on disk: a **top-level `.pdf` in `source_dir_path`** means the paper was rendered from a PDF (LaTeX renders have `source.flattened.tex` and no top-level PDF; verified against the live cache).

**Files:**
- Modify: `backend/src/paperhub/api/papers.py` (add two routes near `serve_html`)
- Modify: `backend/tests/test_papers_api.py` (or `test_chunks_api.py`) — add tests

**Endpoints:**
1. `GET /papers/content/{id}/document` → `{"mode": "pdf" | "html"}`. Resolves the paper, globs `Path(source_dir_path).glob("*.pdf")` (top level only); a hit → `"pdf"`, else `"html"`. 404 if the paper_content row is missing.
2. `GET /papers/content/{id}/pdf` → `FileResponse(pdf_path, media_type="application/pdf", content_disposition_type="inline")` so the browser renders it inline in the iframe (NOT a download). Pick the PDF: prefer `source_path` if it ends in `.pdf` and exists, else the first top-level `*.pdf` in `source_dir_path`. 404 if the paper is missing; 404 (or 410) if no PDF exists for it.

- [ ] **Step 1: Write failing tests** (mirror the `test_papers_api.py` ASGI + `PAPERHUB_WORKSPACE` + `_seed_paper_content` pattern). Seed two papers in a tmp workspace: (a) a LaTeX one whose `source_dir_path` contains only `source.flattened.tex` + `source.html` (write those files into tmp), (b) a PDF one whose `source_dir_path` contains a `foo.pdf` + `source.html`. Assert:
  - `GET /papers/content/{latex_id}/document` → `200 {"mode": "html"}`.
  - `GET /papers/content/{pdf_id}/document` → `200 {"mode": "pdf"}`.
  - `GET /papers/content/{pdf_id}/pdf` → `200`, `content-type: application/pdf`, inline disposition.
  - `GET /papers/content/{latex_id}/pdf` → `404`/`410` (no PDF).
  - `GET /papers/content/99999/document` → `404`.
- [ ] **Step 2: Run → fail.** `uv run pytest tests/test_papers_api.py -k document_or_pdf -v` (name your tests accordingly).
- [ ] **Step 3: Implement** the two routes in `papers.py` (reuse `load_settings()` + `open_db()` like `serve_html`; the SELECT pulls `source_path, source_dir_path`). Sync `Path.glob`/`is_file` is acceptable here (same scope decision as `serve_html`, which already uses sync `path.is_file()` with a `# noqa: ASYNC240`).
- [ ] **Step 4: Run → pass.** Then `uv run ruff check src tests` + `uv run mypy src`.
- [ ] **Step 5: Commit.** `git commit -m "feat(api): serve PDF for PDF-rendered papers + /document mode probe"`.

---

## Task W2-1: Canvas store redesign

**Files:**
- Modify: `frontend/src/store/canvas.ts`
- Modify: `frontend/tests/store/canvas.test.ts`

The store stays tiny: it records *what the user requested* (a citation, or a browse toggle); the component owns the resolved paper + highlight. `requestNonce` lets the same chunk re-trigger resolution if clicked twice.

- [ ] **Step 1: Replace the test** `frontend/tests/store/canvas.test.ts`:

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 }));

describe("canvas store", () => {
  it("starts closed with no request", () => {
    const s = useCanvasStore.getState();
    expect(s.open).toBe(false);
    expect(s.requestedChunkId).toBeNull();
  });

  it("openCitation opens, records the chunk, and bumps the nonce", () => {
    const before = useCanvasStore.getState().requestNonce;
    useCanvasStore.getState().openCitation(42);
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.requestedChunkId).toBe(42);
    expect(s.requestNonce).toBe(before + 1);
  });

  it("clicking the same chunk again re-bumps the nonce (re-triggers resolve)", () => {
    useCanvasStore.getState().openCitation(42);
    const n1 = useCanvasStore.getState().requestNonce;
    useCanvasStore.getState().openCitation(42);
    expect(useCanvasStore.getState().requestNonce).toBe(n1 + 1);
  });

  it("toggleCanvas opens when closed and closes when open", () => {
    expect(useCanvasStore.getState().open).toBe(false);
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().open).toBe(true);
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("toggleCanvas open does NOT set a chunk request (browse mode)", () => {
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().requestedChunkId).toBeNull();
  });

  it("closeCanvas closes but preserves the last requested chunk", () => {
    useCanvasStore.getState().openCitation(7);
    useCanvasStore.getState().closeCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
    expect(useCanvasStore.getState().requestedChunkId).toBe(7);
  });
});
```

- [ ] **Step 2: Run → fail.** `npx vitest run tests/store/canvas.test.ts` (old shape: `chunkId`/`openCitation` signature differs).

- [ ] **Step 3: Rewrite** `frontend/src/store/canvas.ts`:

```typescript
import { create } from "zustand";

interface CanvasState {
  open: boolean;
  /** The chunk the user clicked a citation for. Null when opened via the
   *  References button (browse mode). The component resolves it → paper. */
  requestedChunkId: number | null;
  /** Bumped on every openCitation so clicking the SAME chunk twice re-triggers
   *  resolution in the component (which keys an effect on this). */
  requestNonce: number;
  openCitation: (chunkId: number) => void;
  /** References button: open in browse mode if closed, else close. */
  toggleCanvas: () => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  open: false,
  requestedChunkId: null,
  requestNonce: 0,
  openCitation: (chunkId) =>
    set((s) => ({ open: true, requestedChunkId: chunkId, requestNonce: s.requestNonce + 1 })),
  toggleCanvas: () => set((s) => ({ open: !s.open })),
  closeCanvas: () => set({ open: false }),
}));
```

- [ ] **Step 4: Run → pass.** `npx vitest run tests/store/canvas.test.ts` (6 tests).
- [ ] **Step 5: Gates + commit.** `npm run typecheck`; `npm run lint`. NOTE: `MessageBubble.citations.test.tsx` calls `useCanvasStore.getState().closeCanvas()` in `beforeEach` — still valid. The `CitationMarker` still calls `openCitation(chunkId)` — still valid. Commit: `git commit -m "feat(frontend): canvas store redesign for reading-panel (browse + citation modes)"`.

---

## Task W2-2: CitationCanvas → reading panel

**Files:**
- Rewrite: `frontend/src/components/canvas/CitationCanvas.tsx`
- Rewrite: `frontend/tests/components/CitationCanvas.test.tsx`

The panel reads the active session's *enabled* references from the chat store (same derivation as `ReferenceSourcesPanel`: `activeSessionId → session.backend_session_id → referencesBySession[backendId]`, filter `enabled`). It renders a switcher (tabs + overflow dropdown), an iframe for the displayed paper, resolves the requested chunk to pick the displayed paper + highlight target, and handles the 404 stale case.

**Component contract:**
- Local state: `displayedPaperId: number | null`, `activeChunk: ChunkResolution | null`, `stale: boolean`.
- Enabled refs derived from the store (mirror `ReferenceSourcesPanel` lines 38–47).
- Effective displayed paper = `displayedPaperId ?? firstEnabledRef?.paper_content_id ?? null`.
- Resolve effect keyed on `[requestNonce]`: if `open && requestedChunkId != null`, `getChunk(requestedChunkId)` → success: `setActiveChunk(c); setDisplayedPaperId(c.paper_content_id); setStale(false)`. On error whose message matches `/\b404\b/`: `setActiveChunk(null); setStale(true)`. Other error: `toast.error("Couldn't load the cited paper")`. (Guard with a `cancelled` flag.)
- Tab click → `setDisplayedPaperId(pcid); setActiveChunk(null); setStale(false)` (browse, no highlight).
- **Document mode (PDF vs HTML), per W2-0:** when `displayedPaperId` changes, fetch the mode via a new `getDocumentMode(paperContentId): Promise<"pdf"|"html">` API client (`GET /papers/content/{id}/document` → `{mode}`). iframe `src` = `${API_BASE_URL}/papers/content/${displayedPaperId}/pdf` when mode is `"pdf"`, else `${API_BASE_URL}/papers/content/${displayedPaperId}/html`. `sandbox="allow-scripts allow-same-origin"`, `title="Citation Canvas"`. (Browser renders the PDF natively in the iframe.)
- **Highlight only for HTML mode** (PDF in a native viewer has no searchable DOM; SRS §I-7 — no PDF.js). Reuse Wave 1's `loadedSrcRef`: in `onLoad`, set `loadedSrcRef.current = src`; then, if `mode === "html" && activeChunk && activeChunk.paper_content_id === displayedPaperId`, `findAndHighlight(doc, activeChunk.text)` and toast.message on miss. Also a `[activeChunk, displayedPaperId, mode]` effect that highlights when `loadedSrcRef.current === src`. (`findAndHighlight` is idempotent.) When a citation is opened (`activeChunk` set) but the resolved paper is `mode === "pdf"`, show a small inline note like "Showing the source PDF — passage highlighting isn't available for PDF papers." instead of attempting a highlight.
- Returns `null` when `!open`.

- [ ] **Step 1: Replace the test** `frontend/tests/components/CitationCanvas.test.tsx`:

```typescript
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), message: vi.fn() } }));
import { toast } from "sonner";

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { API_BASE_URL } from "@/lib/api";
import type { ReferenceItem } from "@/types/domain";

function ref(over: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    papers_id: 1, paper_content_id: 7, enabled: true, added_at: "2024-01-01",
    arxiv_id: "1706.03762", title: "Attention Is All You Need", year: 2017, kind: "arxiv",
    ...over,
  };
}

const server = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({ id: 42, paper_content_id: 7, section: "3.2", text: "Expert collapse is mitigated." }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  vi.clearAllMocks();
  useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 });
  useChatStore.getState().reset();
  // Seed an active session with two enabled references.
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 99);
  useChatStore.getState().setReferences(99, [
    ref({ papers_id: 1, paper_content_id: 7, title: "Paper A" }),
    ref({ papers_id: 2, paper_content_id: 8, title: "Paper B", arxiv_id: "2005.14165" }),
  ]);
});

describe("CitationCanvas reading panel", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("opening via citation resolves the chunk and shows that paper", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    const iframe = await screen.findByTitle(/citation canvas/i);
    await waitFor(() =>
      expect(iframe).toHaveAttribute("src", `${API_BASE_URL}/papers/content/7/html`),
    );
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
  });

  it("renders a switcher tab per enabled reference; clicking one switches the paper", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().toggleCanvas()); // browse mode → defaults to first ref (paper 7)
    await screen.findByTitle(/citation canvas/i);
    expect(screen.getByRole("button", { name: /Paper A/ })).toBeInTheDocument();
    const tabB = screen.getByRole("button", { name: /Paper B/ });
    await userEvent.click(tabB);
    await waitFor(() =>
      expect(screen.getByTitle(/citation canvas/i)).toHaveAttribute(
        "src", `${API_BASE_URL}/papers/content/8/html`,
      ),
    );
  });

  it("shows a stale-passage notice when getChunk 404s, panel still open", async () => {
    server.use(http.get(`${API_BASE_URL}/chunks/42`, () => HttpResponse.json({ detail: "no chunk 42" }, { status: 404 })));
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    expect(await screen.findByText(/no longer available|re-indexed/i)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled(); // 404 is a known-stale case, not a network error
  });

  it("close removes the panel from the DOM", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await screen.findByTitle(/citation canvas/i);
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByLabelText(/citation canvas/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run → fail.** `npx vitest run tests/components/CitationCanvas.test.tsx`.

- [ ] **Step 3: Rewrite** `frontend/src/components/canvas/CitationCanvas.tsx`. Read `frontend/src/components/references/ReferenceSourcesPanel.tsx` (lines 31–47) for the refs-derivation pattern and `frontend/src/components/chat/AttachPaperMenu.tsx` for the project's `@base-ui` Popover idiom (use it for the overflow "…" dropdown). Implement to the contract above. Key points the implementer must honour:
  - Derive enabled refs from the store; `displayedPaperId ?? firstEnabledRef?.paper_content_id` is the effective paper.
  - `MAX_VISIBLE_TABS = 3`; refs beyond that go into a `@base-ui` Popover triggered by a "…" button. Each menu item / tab is a `<button>` whose accessible name contains the paper title (the test queries `getByRole("button", { name: /Paper B/ })`). If a paper in the overflow dropdown is the active one, surface it (e.g. show its title on the "…" trigger or mark it) — but the test only requires it be reachable as a button by name when ≤3 refs (so with 2 refs both are visible tabs; the dropdown path is exercised manually).
  - Resolve effect keyed on `[requestNonce]` (NOT `[requestedChunkId]`, so re-clicking the same chunk re-resolves). Guard with `cancelled`. Match 404 via the thrown `Error` message containing `404` (apiFetch throws `API 404: ...`).
  - 404 → render an inline notice containing text like "This citation's passage is no longer available — the paper may have been re-indexed." (the test matches `/no longer available|re-indexed/i`). Do NOT call `toast.error` for the 404 case.
  - Root element: `<aside aria-label="Citation Canvas" className="flex h-full w-full flex-col border-l border-border bg-card">` — a FILL-COLUMN, NOT `fixed`. (The push layout in W2-3 owns the width.)
  - Keep the close button (`aria-label="Close canvas"` → `closeCanvas()`), the `loadedSrcRef` highlight discipline, and the toast.message-on-miss from Wave 1.

  Provide a complete implementation (no placeholders). The implementer writes it following the contract; this is an integration task, so the implementer should verify each test passes and not weaken any assertion. If `act()` warnings appear from the store-driven open, keep the `act()` wrapping already in the test.

- [ ] **Step 4: Run → pass.** `npx vitest run tests/components/CitationCanvas.test.tsx` (5 tests). Run the full suite `npx vitest run` to catch regressions (MessageBubble citation tests still pass — they only assert the marker→`openCitation` call, which is unchanged).
- [ ] **Step 5: Gates + commit.** `npm run typecheck`; `npm run lint`. Commit: `git commit -m "feat(frontend): CitationCanvas reading panel with paper switcher + stale-chunk notice"`.

---

## Task W2-3: Push layout + Composer References toggle

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/components/chat/Composer.tsx`
- Test: extend `frontend/tests/components/Composer.test.tsx` (or add `Composer.canvas.test.tsx`)

### Part A — push layout in ChatPage

Replace ChatPage's vertical wrapper with a horizontal grid whose right column animates from `0` (closed) to a clamped width (open), pushing the chat. Mirror `Shell.tsx`'s `transition-[grid-template-columns] duration-200` idiom. Keep the lazy-load + mount-on-open (so no chunk/iframe fetch when closed).

```tsx
import { lazy, Suspense } from "react";
import { toast } from "sonner";

import { ChatThread } from "@/components/chat/ChatThread";
import { Composer } from "@/components/chat/Composer";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { useGlobalShortcuts } from "@/hooks/useGlobalShortcuts";
import { useReferencesSync } from "@/hooks/useReferencesSync";
import { cn } from "@/lib/utils";

const CitationCanvas = lazy(() =>
  import("@/components/canvas/CitationCanvas").then((m) => ({ default: m.CitationCanvas })),
);

export function ChatPage() {
  useGlobalShortcuts();
  useReferencesSync();
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const newSession = useChatStore((s) => s.newSession);
  const canvasOpen = useCanvasStore((s) => s.open);
  const { send } = useChatStream();

  const activeSession =
    activeSessionId === null
      ? null
      : (sessions.find((s) => s.id === activeSessionId) ?? null);

  const isStreaming =
    activeSession?.messages.some((m) => m.status === "streaming") ?? false;

  const handleSubmit = (text: string): void => {
    const sessionId = activeSessionId ?? newSession();
    send(sessionId, text).catch((err: unknown) => {
      toast.error("Request failed", {
        description: err instanceof Error ? err.message : String(err),
      });
    });
  };

  return (
    <div
      className={cn(
        "grid flex-1 min-h-0 transition-[grid-template-columns] duration-200",
        canvasOpen ? "grid-cols-[1fr_clamp(360px,38vw,560px)]" : "grid-cols-[1fr_0px]",
      )}
    >
      <div className="flex min-h-0 min-w-0 flex-col">
        <ChatThread session={activeSession} />
        <Composer onSubmit={handleSubmit} disabled={isStreaming} />
      </div>
      <div className="overflow-hidden">
        {canvasOpen && (
          <Suspense fallback={null}>
            <CitationCanvas />
          </Suspense>
        )}
      </div>
    </div>
  );
}
```

### Part B — Composer References button → toggle

In `frontend/src/components/chat/Composer.tsx`: remove the `References` entry from the disabled `CAPABILITIES` array (keep `Slides` + `Compare`), and render an ENABLED `BookOpen` button before the disabled placeholders that calls `useCanvasStore.getState().toggleCanvas()` (import `useCanvasStore`). Tooltip: `"Toggle the reference reading panel"`. Give it `aria-label="References"`.

Concretely, add near the other imports: `import { useCanvasStore } from "@/store/canvas";` and inside the component read `const toggleCanvas = useCanvasStore((s) => s.toggleCanvas);`. In the tool row, after `<AttachPaperMenu />` and before the `CAPABILITIES.map(...)`, insert:

```tsx
                <Tooltip>
                  <TooltipTrigger render={<span tabIndex={0} className="inline-flex" />}>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => toggleCanvas()}
                      className="h-8 w-8"
                      aria-label="References"
                    >
                      <BookOpen className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>Toggle the reference reading panel</p>
                  </TooltipContent>
                </Tooltip>
```

Remove the `BookOpen` `References` object from `CAPABILITIES` (so it's no longer rendered as a disabled icon). Keep `Presentation`/`Slides` and `Columns2`/`Compare` disabled placeholders. `BookOpen` is already imported.

- [ ] **Step 1: Write the failing test** — `frontend/tests/components/Composer.canvas.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { Composer } from "@/components/chat/Composer";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 }));

describe("Composer References button", () => {
  it("is enabled and toggles the canvas open/closed", async () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    const btn = screen.getByRole("button", { name: /^references$/i });
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(useCanvasStore.getState().open).toBe(true);
    await userEvent.click(btn);
    expect(useCanvasStore.getState().open).toBe(false);
  });
});
```

- [ ] **Step 2: Run → fail** (References is currently a disabled placeholder). `npx vitest run tests/components/Composer.canvas.test.tsx`.
- [ ] **Step 3: Implement Parts A + B.**
- [ ] **Step 4: Run → pass.** That test + `npm run build` (confirm the `CitationCanvas-*.js` chunk still splits — the lazy import is retained). Full suite `npx vitest run` (no regressions; the existing Composer test may assert the References placeholder was disabled — UPDATE that assertion if present, since References is now enabled).
- [ ] **Step 5: Gates + commit.** `npm run typecheck`; `npm run lint`. Commit: `git commit -m "feat(frontend): push-layout canvas column + References toggle button"`.

---

## Task W2-4: Gates + SRS reconciliation

- [ ] **Step 1: Full gates.** From `frontend/`: `npx vitest run`, `npm run typecheck`, `npm run lint`, `npm run build`. From `backend/`: unchanged, but run `uv run pytest -q` to confirm nothing regressed.
- [ ] **Step 2: SRS v2.14.** Update `docs/superpowers/specs/2026-05-17-paperhub-srs.md`: bump version to v2.14; add a revision-history row describing the reading-panel redesign (push layout, paper switcher, References-button toggle, browse mode, stale-chunk 404 notice) + note the reingest-renumbers-chunk-ids limitation as a Plan C follow-up; update FR-03 wording to describe the reading panel + switcher + toggle (not just the single-chunk drawer).
- [ ] **Step 3: Manual smoke (human).** Start the stack, open a NEW session (so chunk ids are current), attach ≥2 papers, ask a `paper_qa` question, verify: References button toggles the panel; the panel pushes the chat; switcher tabs switch papers; clicking a citation switches+scrolls+highlights; a citation in a pre-reingest answer shows the stale notice (not a crash).
- [ ] **Step 4: Commit any SRS changes.** `git commit -m "docs(srs): v2.14 Citation Canvas reading-panel redesign"`.

## Wave 2 Self-Review

- **Push not overlay** → W2-3 Part A (animated grid column).
- **Paper switcher (tabs + overflow dropdown)** → W2-2.
- **References button toggles panel** → W2-3 Part B + W2-1 `toggleCanvas`.
- **Citation click switches+scrolls+highlights; browse mode no highlight** → W2-1 (`requestedChunkId`/nonce) + W2-2 (resolve effect + highlight discipline).
- **404 stale-chunk graceful notice (NFR-02)** → W2-2 (404 branch, inline notice, no toast.error).
- **Type consistency:** `useCanvasStore` new shape (`open`, `requestedChunkId`, `requestNonce`, `openCitation`, `toggleCanvas`, `closeCanvas`) defined in W2-1, consumed in W2-2 (`requestNonce` effect, `closeCanvas`), W2-3 (`toggleCanvas`, `open`), and unchanged `CitationMarker` (`openCitation`). `ChunkResolution` (Wave 1 Task 2) reused. `findAndHighlight` (Wave 1 Task 5) reused.
- **Out of Plan D scope:** stable chunk ids across reingest (Plan C reingest follow-up) — Wave 2 only handles the 404 gracefully.

---

# Wave 3 — Reading-panel polish (post-Wave-2 live testing)

> Three UX issues found while testing Wave 2 live. All centred on the reading panel.

## W3-1: Close the canvas when the chat session changes

**Why:** the canvas shows the *active session's* references. Switching sessions while the panel is open leaves it showing the previous session's paper (and switcher tabs for refs that don't belong to the new session). Closing it on session change is the correct reset (re-open shows the new session's refs).

**Files:** `frontend/src/pages/ChatPage.tsx` (always mounted — owns this).

**Implementation:** track the previous `activeSessionId` in a ref *inside an effect* (ref access in effects is allowed; ref access during render is NOT — that's why this lives in an effect, not render). On a real change (not initial mount), call `closeCanvas()`.

```tsx
import { lazy, Suspense, useEffect, useRef } from "react";
// ...
const closeCanvas = useCanvasStore((s) => s.closeCanvas);
const prevSessionRef = useRef(activeSessionId);
useEffect(() => {
  if (prevSessionRef.current !== activeSessionId) {
    prevSessionRef.current = activeSessionId;
    closeCanvas();
  }
}, [activeSessionId, closeCanvas]);
```

- [ ] Test (`frontend/tests/pages/ChatPage.canvas.test.tsx` or extend): render ChatPage with a session, open the canvas (`useCanvasStore.getState().toggleCanvas()`), switch `activeSessionId` (via `useChatStore.getState().selectSession(otherId)`), assert `useCanvasStore.getState().open === false`. (ChatPage needs the chat-stream/store deps; if too heavy to render, instead verify via a focused hook test or accept manual verification — keep the test honest, skip with a reason if jsdom can't drive it.)
- [ ] Verify `closeCanvas()` in an effect does NOT trip the `react-hooks/set-state-in-effect` lint rule (it's a zustand action, not a React `useState` setter). If it does, move the close into the chat store's `selectSession` action instead.

## W3-2: Cache rendered pages (keep loaded papers alive)

**Why:** switching switcher tabs (or re-opening a paper) re-sets the iframe `src`, forcing a full re-parse + MathJax re-render — slow for large papers. Keep each *visited* paper's iframe mounted and just toggle visibility, so switching back is instant.

**Files:** `frontend/src/components/canvas/CitationCanvas.tsx`.

**Implementation:**
- Track `visited: { paperContentId: number; mode: "pdf" | "html"; src: string }[]` (component state). When the active paper's `mode` resolves and yields a `src`, append it to `visited` if that `paperContentId` isn't already present (dedupe by paperContentId; cap the list at e.g. 8 most-recent to bound memory).
- Render ONE iframe per visited entry, all mounted; only the active one visible (`hidden={entry.paperContentId !== effectivePaperId}` or `style={{ display: ... }}` — use `hidden` so layout collapses). Active iframe fills the body; others are display:none but stay loaded.
- Track loaded state + highlight per active iframe: a `Map<number, HTMLIFrameElement>` via callback refs keyed by paperContentId; `loadedPaperIds: Set<number>`. On a paper's iframe `onLoad`, mark it loaded, and if it's the active paper + html + has an `activeChunk` for it, highlight (also apply dark-mode style — W3-3). The same-paper highlight effect targets the active paper's iframe doc.
- **Also (backend, cheap first-load win):** add `Cache-Control: public, max-age=31536000, immutable` to the `FileResponse`s in `serve_html` + `serve_pdf` (content is content-addressed by `paper_content` id / sha) so the browser caches the bytes across reloads/sessions. `headers={"Cache-Control": "..."}` on the `FileResponse`.

- [ ] Tests: jsdom can't truly verify "no re-parse", but assert structure: after visiting paper 7 then paper 8 then back to 7, there are 2 iframes mounted (one per visited paper) and the paper-7 iframe was not re-created (e.g. stable via `key`/ref identity, or assert both `src`s are present in the DOM simultaneously with only one visible). Backend: add a test that `serve_html`/`serve_pdf` responses carry a `Cache-Control` header.

## W3-3: Dark-mode-aware iframe HTML

**Why:** the rendered paper HTML is light (white bg). In the app's dark theme the iframe is a jarring white block. Invert/adapt the document for dark mode (HTML mode only; the native PDF viewer can't be styled).

**Files:** `frontend/src/components/canvas/CitationCanvas.tsx`.

**Implementation:**
- Detect dark mode via `next-themes` `useTheme()` (`resolvedTheme === "dark"`) — the app already uses next-themes (`ThemeToggle`). 
- On iframe load (and when `resolvedTheme` changes while open), inject/remove a `<style id="ph-dark">` into the iframe document (mode `"html"` only). Use the invert-with-image-reinvert trick so figures stay correct:
  ```css
  html { background: #0f1115 !important; }
  html { filter: invert(0.9) hue-rotate(180deg); }
  img, svg, video, canvas, [style*="background"] { filter: invert(1) hue-rotate(180deg); }
  ```
  Inject only when dark; remove the style node when light. Factor this as a small helper `applyIframeTheme(doc, dark)` (parallels `findAndHighlight`'s style injection).
- Apply on every iframe `onLoad` and via an effect keyed on `resolvedTheme` (re-apply to the active iframe's doc when the user toggles theme with the panel open).

- [ ] Test: a unit test for `applyIframeTheme(doc, true)` injects `#ph-dark` with a `filter` rule into a jsdom `Document`; `applyIframeTheme(doc, false)` removes it. (Mirror `findAndHighlight.test.ts`.)

## Wave 3 Self-Review
- Session-swap closes the panel → W3-1 (extracted to `useCloseCanvasOnSessionChange` for testability).
- Loaded papers cached (instant re-display) → W3-2 (`modeByPaper` cache + one mounted iframe per visited paper, only active visible). Backend HTTP cache header deliberately NOT added — caching rendered HTML while the operator is actively re-ingesting risks serving stale content (the very cause of the v2.13 404).
- Dark-mode-aware document → W3-3 (`applyIframeTheme`, HTML-mode only; PDF native viewer unaffected).
- `findAndHighlight` highlight discipline preserved across the keep-alive iframe set (highlight targets the ACTIVE paper's iframe doc; hidden iframes get theme-by-pid but never highlight).

### Wave 3 known follow-ups (out of scope; deferred with reason)
1. **Browse-mode default-paper flip (review B2).** Before any tab/citation interaction, the displayed paper derives live from `firstEnabledRef` (`refs[0]`, backend `added_at DESC`). `useReferencesSync` replaces the whole refs array on refetch, so a paper added/reordered while the panel is open in browse mode can flip the displayed paper. Narrow (pre-interaction only). The clean fix (seed `displayedPaperId` once on open) collides with the repo's strict lint rules (`react-hooks/set-state-in-effect`, `react-hooks/refs` ban ref-read-in-render); deferred rather than adding an eslint-disable.
2. **`modeByPaper` keep-alive cap (review B5).** All visited papers' iframes stay mounted for the panel's lifetime (panel remounts per open, so bounded by papers-browsed-per-open). Plan suggested an ~8 LRU cap; not implemented — acceptable given few refs/session. Add an LRU eviction if a session ever browses many large papers.
3. **Dark-mode + hidden-iframe-onLoad integration tests.** jsdom doesn't populate iframe `contentDocument` from `src`, so the dark-mode application and hidden-iframe non-highlighting seams are covered only by the `applyIframeTheme` + `findAndHighlight` unit tests, not an integration test.

---

# Wave 4 + 5 — Same-origin content + library-based viewer (post-live-testing)

> Live testing revealed the iframe-`src`-to-backend approach was **cross-origin** (app `:5173`, backend `:8000`): `contentDocument` was null, so highlight + dark-mode silently no-op'd ("never work"), and the PDF iframe blanked / triggered a browser download. Wave 4 first tried a Vite proxy; Wave 5 superseded it with embedded content + a real PDF library (user direction: "stop building it yourself, use a package").

## What shipped (final)
- **Backend (W2-0, already shipped):** `GET /papers/content/{id}/document` → `{mode}` (top-level `*.pdf` ⇒ pdf) + `GET /papers/content/{id}/pdf` (inline `FileResponse`).
- **W5 — content fetched + embedded (same-origin):**
  - `frontend/src/lib/api.ts`: `fetchPaperHtml(id)` (text) + `fetchPaperPdfData(id)` (`Uint8Array`) — fetched via CORS (the backend CORS middleware already allows GET from `:5173`).
  - `frontend/src/components/canvas/HtmlView.tsx`: iframe `srcDoc={html}` (same-origin → `applyIframeTheme` + `findAndHighlight` work; MathJax runs; figures data-URI inlined). Self-applies theme + highlight on load + on `[isDark, highlightText]` change.
  - `frontend/src/components/canvas/PdfView.tsx`: **`react-pdf`** (`pdfjs`); worker via `new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url)`; `AnnotationLayer.css` + `TextLayer.css`; `<Document file={{ data }}>` (memoized) + a `<Page>` per page fit to the container width (ResizeObserver). No iframe, no download.
  - `frontend/src/components/canvas/CitationCanvas.tsx`: rewritten to fetch+cache content per paper in `docByPaper` (survives the session via the always-mounted panel), prefetch all enabled refs, render the active paper's `HtmlView`/`PdfView`. HTML views stay mounted (hidden) for instant switching; PDF renders only when active (react-pdf is heavy).
  - Vite proxy (W4a) **removed** — embedding makes it unnecessary.
- **Dependency:** `react-pdf` added (lazy-loaded with the canvas chunk; pulls a ~1 MB pdf.worker asset, isolated from the main bundle).

## Tests
- `CitationCanvas.test.tsx`: react-pdf is `vi.mock`ed (pdfjs can't run in jsdom); MSW serves `/document`, `/html` (text), `/pdf` (arraybuffer). Asserts: HTML embedded via `srcDoc` (contains the body), tab-switch swaps the active `srcDoc`, PDF path renders the mocked `PdfView` + the inline note, stale-404 notice (no `toast.error`), close → `open=false` + `aria-hidden`.
- `applyIframeTheme.test.ts` + `findAndHighlight.test.ts` unit-cover the theme + highlight logic that `HtmlView` invokes.

## Wave 4/5 known follow-ups (out of scope)
1. **Operator setup:** `npm install` (adds react-pdf) + a dev-server restart to pick up the new dependency. No proxy needed.
2. **Non-inlined HTML assets:** the renderer inlines raster figures as data URIs, so `srcDoc` shows them; a paper with non-inlined assets referenced by relative URL would 404 (rare). A same-origin asset route or absolute-URL rewrite would close it.
3. **PDF keep-alive:** PDF papers re-render (pdfjs re-parse) on re-activation since only the active PDF is mounted (HTML papers are kept mounted). Acceptable; the bytes are cached so there's no re-fetch.

---

# Wave 6 — Deterministic citation anchors (source sentinels)

> Click-time text-search resolves *most* citations but misses chunks whose text was mangled by rendering (math-heavy passages). Inject a hidden marker at each chunk's start during ingest so the canvas resolves a citation by `getElementById` — deterministic — falling back to text-search only where a marker couldn't be placed.

## Model (user's framing)
- One marker per chunk, at its **start**. A chunk's highlight region is `[its marker, next chunk's marker)` within the section — markers partition the content, so no end-markers needed.
- Anchor id `phchunk-{ordinal}` where ordinal = the chunk's 0-based index within the paper. Stored on the chunk row so the frontend maps a clicked chunk → its anchor.
- **LaTeX-rendered (HTML) papers only.** PDF papers render via react-pdf (no HTML DOM to anchor); they keep the "highlighting unavailable for PDF" note.

## Alignment (the load-bearing detail)
Chunk `char_start` is relative to `strip_latex_comments(full_text)`. The renderer renders a *figure-normalized* copy of the raw flattened text. To put a sentinel at the position a chunk's `char_start` denotes, injection MUST happen on the comment-stripped text, in this order:
1. `base = strip_latex_comments(full_text)` — chunk offsets index this.
2. Inject a sentinel token at each chunk's `char_start` in `base`, **back-to-front** so earlier offsets stay valid → `base_marked`.
3. `render_source = rasterize_and_normalize_figures(base_marked, resource_dir)` — only rewrites `\includegraphics` args; doesn't disturb sentinels elsewhere.
4. `render_html(render_source, kind="latex", …)` → HTML containing the sentinel tokens as text.
5. Post-process the HTML: replace each surviving sentinel with `<span id="phchunk-{ordinal}"></span>`. Chunks whose sentinel didn't survive (mangled / skipped) get **no** `dom_id` → runtime text-search fallback.

This same order works for **existing** papers via a re-render CLI that reads the stored flattened source + the existing chunk `char_start`s (no re-chunk, no re-embed, no chunk-id change — so existing message citations keep working).

## Math safety
A sentinel inside `$…$` / `\(…\)` / `\[…\]` / `\begin{equation|align|math|...}…\end{…}` would be swallowed into the math TeX and break MathJax (and the post-process would inject a `<span>` inside math). So: scan `base` for math regions and **skip** injecting at any `char_start` that falls inside one (that chunk → text-search fallback). Chunk starts are usually at paragraph boundaries (text mode), so most are safe.

## Sentinel token
A unique ASCII token that survives pandoc latex→html intact and is regex-recoverable: `PHCHUNKANCHOR{ordinal}END` (letters+digits only — no chars pandoc reflows). Post-process regex `/PHCHUNKANCHOR(\d+)END/g` → `<span id="phchunk-$1"></span>`. Served HTML contains spans, not tokens.

## Schema
`ALTER TABLE chunks ADD COLUMN dom_id TEXT` (nullable; idempotent migration). `null` = no anchor (use fallback).

## Tasks
- [ ] **W6-1 — sentinel util + schema (backend, TDD).** `backend/src/paperhub/pipelines/sentinels.py`: `find_math_spans(text) -> list[(start,end)]`; `inject_sentinels(base, starts) -> (marked, set_of_injected_ordinals)` (back-to-front, math-safe skip); `postprocess_sentinels(html) -> (html, {ordinal: dom_id})`. Schema migration for `chunks.dom_id`. Tests: injection preserves offsets, math positions skipped, round-trip (inject→postprocess) yields the expected `phchunk-N` spans, tokens survive a representative pandoc-style transform.
- [ ] **W6-2 — wire into LaTeX ingest.** In `paper_pipeline.py` `_ingest_arxiv` (and the LaTeX branch of `_ingest_upload`): after `chunk_text`, build `base`+`base_marked` per the order above, render the marked source, post-process, and persist `dom_id` per chunk. Store `dom_id` in `_persist_paper_content_and_chunks` (chunks insert). Guard: only the LaTeX render path.
- [ ] **W6-3 — re-render CLI** `paperhub-rerender-html`: for each LaTeX `paper_content`, read flattened source + existing chunks (id, char_start, order), recompute `base`, inject at existing `char_start`s, normalize, render, post-process, rewrite `html_path`, `UPDATE chunks SET dom_id=…`. No re-chunk/embed. Run it once to upgrade existing papers.
- [ ] **W6-4 — `GET /chunks/{id}` returns `dom_id`** (+ keep `text` for fallback). Add `dom_id` to the `ChunkResolution` model.
- [ ] **W6-5 — frontend resolve.** `ChunkResolution` gains `dom_id: string | null`. In `HtmlView`, when `dom_id` is set, `getElementById(dom_id)` in the iframe doc → scroll into view + highlight the region from that span up to the next `[id^="phchunk-"]` element (or block). Fall back to `findAndHighlight(text)` when `dom_id` is null or the element is missing. (`findAndHighlight` stays as the fallback.)

## Out of scope
- PDF papers (react-pdf; no HTML anchors).
- Chunks whose start is inside math (rare) — text-search fallback.