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
```