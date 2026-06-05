# F5 — Presentation Mode + Voice Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a generated deck deliverable — open a clean audience window on the projector, drive it from a presenter cockpit inside the app, field an audience question mid-talk without disturbing the projected slide, and dictate questions by voice.

**Architecture:** Entirely frontend. A second self-contained Vite entry (`present.html`) is the audience window — it fetches the deck PDF itself and follows a `BroadcastChannel('paperhub-present-<sid>')` page stream, so it is immune to the in-app panel's lifecycle. The in-app `SlidesPanel` becomes the presenter cockpit (timer · next-slide preview · sync badge · Stop) when presenting; `presenting` + `currentPage` live in the `slides` Zustand store so the Q&A close/reopen loses neither. Voice input is a composer mic over the browser Web Speech API.

**Tech Stack:** React + TypeScript + Vite (multi-page build), Zustand, react-pdf (already a dep), Web `BroadcastChannel`, Web Speech `SpeechRecognition`. Vitest + RTL + the existing jsdom setup. No backend.

**Spec:** SRS **v2.26** — UC-4 (present→ask→resume loop), FR-05 (composer voice dictation), FR-12 (`present.html` audience entry, presenter cockpit, `BroadcastChannel` sync, Q&A-during-talk). TTS is explicitly out of scope (deferred to a future voice-tutor plan).

**Conventions:** TDD per task (failing test → minimal impl → commit). Frontend gates from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`. Conventional Commits. `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Branch `feat/F5-presentation-voice` (already created).

---

## File Structure

**Frontend — new:**
- `frontend/present.html` — second Vite HTML entry (audience window).
- `frontend/src/present/main.tsx` — mounts the audience app from `?session=N`.
- `frontend/src/present/PresentPage.tsx` — slide-only fullscreen view; fetches the PDF, follows the channel.
- `frontend/src/lib/presentChannel.ts` — `BroadcastChannel` wrapper (`postPage`/`onPage`/`ping`/`onPing`/`pong`/`onPong`/`close`).
- `frontend/src/lib/speech.ts` — Web Speech `SpeechRecognition` wrapper.
- `frontend/src/hooks/usePresentation.ts` — channel lifecycle, page broadcast, heartbeat, present/stop.
- `frontend/src/components/slides/PresenterControls.tsx` — timer + next-slide preview + sync badge + Stop.

**Frontend — modified:**
- `frontend/src/store/slides.ts` — `presentingBySession` + `presentStartedAtBySession` + `startPresenting`/`stopPresenting`.
- `frontend/src/components/slides/SlidesPanel.tsx` — Present button + presenter cockpit wiring.
- `frontend/src/components/chat/Composer.tsx` — voice-input mic button.
- `frontend/vite.config.ts` — register the `present.html` rollup input.
- `frontend/tests/setup.ts` — in-memory `BroadcastChannel` polyfill (jsdom lacks one).

**Docs — modified:**
- `CLAUDE.md` — flip the Plan F5 row + add pointers (SRS already bumped to v2.26 in this session).

**No backend changes.** `present.html` is served by the existing nginx `try_files $uri $uri/ /index.html` (a real file wins over the SPA fallback); the audience window calls the existing `GET /sessions/{id}/deck/pdf` via the existing proxy.

---

## Task 1: Slides store — presentation state

**Files:**
- Modify: `frontend/src/store/slides.ts`
- Test: `frontend/tests/store/slides.presentation.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/store/slides.presentation.test.ts
import { beforeEach, describe, expect, it } from "vitest";
import { useSlidesStore } from "@/store/slides";

describe("slides store — presentation state", () => {
  beforeEach(() => {
    useSlidesStore.setState({
      open: false,
      presentingBySession: {},
      presentStartedAtBySession: {},
      currentPageBySession: {},
    });
  });

  it("startPresenting flips the per-session flag + stamps a start time", () => {
    useSlidesStore.getState().startPresenting(7);
    const s = useSlidesStore.getState();
    expect(s.presentingBySession[7]).toBe(true);
    expect(s.presentStartedAtBySession[7]).toBeGreaterThan(0);
  });

  it("stopPresenting clears only the flag", () => {
    useSlidesStore.getState().startPresenting(7);
    useSlidesStore.getState().stopPresenting(7);
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(false);
  });

  it("closePanel preserves presenting + current page (the Q&A-reopen invariant)", () => {
    const st = useSlidesStore.getState();
    st.startPresenting(7);
    st.setCurrentPage(7, 4);
    st.openPanel();
    st.closePanel();
    const s = useSlidesStore.getState();
    expect(s.open).toBe(false);
    expect(s.presentingBySession[7]).toBe(true);
    expect(s.currentPageBySession[7]).toBe(4);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/store/slides.presentation.test.ts`
Expected: FAIL — `startPresenting`/`presentingBySession` do not exist.

- [ ] **Step 3: Add the state + actions**

In `frontend/src/store/slides.ts`, extend the `SlidesState` interface (after `currentPageBySession`):

```typescript
  /** Per-session "presentation mode active" flag. Ephemeral (NOT persisted) and
   *  kept in the store — never in SlidesPanel local state — so the Q&A
   *  close/reopen (a panel unmount/remount) does not lose it. */
  presentingBySession: Record<number, boolean>;
  /** Epoch ms when presentation began, per session — drives the cockpit timer
   *  so it survives a panel remount during Q&A. */
  presentStartedAtBySession: Record<number, number>;
```

Add to the action signatures (after `setRestoring`):

```typescript
  startPresenting: (sid: number) => void;
  stopPresenting: (sid: number) => void;
```

In the store body, add the initial values (next to `currentPageBySession: {}`):

```typescript
      presentingBySession: {},
      presentStartedAtBySession: {},
```

And the actions (next to `setRestoring`):

```typescript
      startPresenting: (sid) =>
        set((s) => ({
          presentingBySession: { ...s.presentingBySession, [sid]: true },
          presentStartedAtBySession: {
            ...s.presentStartedAtBySession,
            [sid]: Date.now(),
          },
        })),
      stopPresenting: (sid) =>
        set((s) => ({
          presentingBySession: { ...s.presentingBySession, [sid]: false },
        })),
```

(`partialize` already persists only `filmstripWidth` + `noteHeight`, so the new maps stay ephemeral — no change needed there.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/store/slides.presentation.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/slides.ts frontend/tests/store/slides.presentation.test.ts
git commit -m "feat(slides): per-session presentation state in the slides store"
```

---

## Task 2: `presentChannel.ts` + BroadcastChannel test polyfill

**Files:**
- Create: `frontend/src/lib/presentChannel.ts`
- Modify: `frontend/tests/setup.ts`
- Test: `frontend/tests/lib/presentChannel.test.ts`

- [ ] **Step 1: Add the in-memory BroadcastChannel polyfill**

jsdom has no `BroadcastChannel`. Append to `frontend/tests/setup.ts` (before the `afterEach`):

```typescript
// jsdom has no BroadcastChannel. Provide a deterministic in-memory one that
// delivers synchronously to other open instances of the same name (excluding
// the sender), so presentation-sync tests don't depend on event-loop timing.
class MemoryBroadcastChannel {
  static channels = new Map<string, Set<MemoryBroadcastChannel>>();
  name: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(name: string) {
    this.name = name;
    const set = MemoryBroadcastChannel.channels.get(name) ?? new Set();
    set.add(this);
    MemoryBroadcastChannel.channels.set(name, set);
  }
  postMessage(data: unknown) {
    for (const ch of MemoryBroadcastChannel.channels.get(this.name) ?? []) {
      if (ch !== this && ch.onmessage) ch.onmessage({ data } as MessageEvent);
    }
  }
  close() {
    MemoryBroadcastChannel.channels.get(this.name)?.delete(this);
  }
}
(globalThis as unknown as { BroadcastChannel: unknown }).BroadcastChannel =
  MemoryBroadcastChannel;
```

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/tests/lib/presentChannel.test.ts
import { describe, expect, it } from "vitest";
import { createPresentChannel } from "@/lib/presentChannel";

describe("presentChannel", () => {
  it("broadcasts page changes presenter → audience", () => {
    const presenter = createPresentChannel(7);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));
    presenter.postPage(3);
    expect(pages).toEqual([3]);
    presenter.close();
    audience.close();
  });

  it("ping → pong round-trips for the heartbeat", () => {
    const presenter = createPresentChannel(9);
    const audience = createPresentChannel(9);
    let pongs = 0;
    presenter.onPong(() => (pongs += 1));
    audience.onPing(() => audience.pong());
    presenter.ping();
    expect(pongs).toBe(1);
    presenter.close();
    audience.close();
  });

  it("does not deliver a channel's own messages to itself", () => {
    const a = createPresentChannel(1);
    let seen = false;
    a.onPage(() => (seen = true));
    a.postPage(2);
    expect(seen).toBe(false);
    a.close();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/lib/presentChannel.test.ts`
Expected: FAIL — `@/lib/presentChannel` does not exist.

- [ ] **Step 4: Implement `presentChannel.ts`**

```typescript
// frontend/src/lib/presentChannel.ts

/** Messages exchanged between the presenter cockpit and the audience window
 *  over BroadcastChannel('paperhub-present-<sid>'). */
export type PresentMessage =
  | { type: "page"; page: number }
  | { type: "ping" }
  | { type: "pong" };

export interface PresentChannel {
  /** Presenter → audience: show this 1-indexed page. */
  postPage: (page: number) => void;
  onPage: (cb: (page: number) => void) => void;
  /** Presenter → audience heartbeat. */
  ping: () => void;
  onPing: (cb: () => void) => void;
  /** Audience → presenter heartbeat reply. */
  pong: () => void;
  onPong: (cb: () => void) => void;
  close: () => void;
}

export function presentChannelName(sessionId: number): string {
  return `paperhub-present-${sessionId}`;
}

/** Wrap a BroadcastChannel for one session. Same-origin only — the presenter
 *  cockpit and the audience window are both served from the app origin and run
 *  on the same machine (one projector); this is NOT a cross-device transport. */
export function createPresentChannel(sessionId: number): PresentChannel {
  const ch = new BroadcastChannel(presentChannelName(sessionId));
  let pageCb: ((p: number) => void) | null = null;
  let pingCb: (() => void) | null = null;
  let pongCb: (() => void) | null = null;
  ch.onmessage = (e: MessageEvent<PresentMessage>) => {
    const msg = e.data;
    if (msg?.type === "page" && typeof msg.page === "number") pageCb?.(msg.page);
    else if (msg?.type === "ping") pingCb?.();
    else if (msg?.type === "pong") pongCb?.();
  };
  return {
    postPage: (page) => ch.postMessage({ type: "page", page } as PresentMessage),
    onPage: (cb) => {
      pageCb = cb;
    },
    ping: () => ch.postMessage({ type: "ping" } as PresentMessage),
    onPing: (cb) => {
      pingCb = cb;
    },
    pong: () => ch.postMessage({ type: "pong" } as PresentMessage),
    onPong: (cb) => {
      pongCb = cb;
    },
    close: () => ch.close(),
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/lib/presentChannel.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/presentChannel.ts frontend/tests/setup.ts frontend/tests/lib/presentChannel.test.ts
git commit -m "feat(slides): BroadcastChannel present-channel wrapper + jsdom polyfill"
```

---

## Task 3: Audience window — `present.html` second Vite entry

**Files:**
- Create: `frontend/present.html`
- Create: `frontend/src/present/main.tsx`
- Create: `frontend/src/present/PresentPage.tsx`
- Modify: `frontend/vite.config.ts`

This entry renders react-pdf, which does not unit-test cleanly in jsdom; it is verified by `npm run build` emitting `dist/present.html` (Step 5) and by the manual two-window gate (Task 9). Write the exact code below.

- [ ] **Step 1: Create `frontend/present.html`** (mirror `index.html`)

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PaperHub — Present</title>
  </head>
  <body style="margin: 0; background: #000">
    <div id="present-root"></div>
    <script type="module" src="/src/present/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Create `frontend/src/present/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";

import { PresentPage } from "./PresentPage";
import "../index.css";

const session = Number(
  new URLSearchParams(window.location.search).get("session"),
);

ReactDOM.createRoot(document.getElementById("present-root")!).render(
  <React.StrictMode>
    {Number.isFinite(session) && session > 0 ? (
      <PresentPage sessionId={session} />
    ) : (
      <div style={{ color: "#fff", fontFamily: "sans-serif", padding: 24 }}>
        Missing or invalid <code>?session</code> parameter.
      </div>
    )}
  </React.StrictMode>,
);
```

- [ ] **Step 3: Create `frontend/src/present/PresentPage.tsx`**

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { fetchDeckPdfData } from "@/lib/api";
import { createPresentChannel, type PresentChannel } from "@/lib/presentChannel";

// pdf.js worker — resolved from the installed pdfjs-dist via import.meta.url so
// the worker is bundled + served from the app origin (same as SlidesPanel).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface Props {
  sessionId: number;
}

/**
 * PresentPage — the audience window. Slide-only, fullscreen, zero chrome.
 * Owns its OWN PDF bytes + page state, so closing the in-app Slides panel
 * (the Q&A loop) never affects it. Follows the presenter's page over
 * BroadcastChannel and answers heartbeat pings so the cockpit badge can show
 * "audience connected".
 */
export function PresentPage({ sessionId }: Props) {
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState({
    w: window.innerWidth,
    h: window.innerHeight,
  });
  const chRef = useRef<PresentChannel | null>(null);

  // Fetch the compiled deck PDF once.
  useEffect(() => {
    let cancelled = false;
    fetchDeckPdfData(sessionId)
      .then((b) => {
        if (!cancelled) setBytes(b);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Channel: follow the presenter's page; answer pings; announce presence.
  useEffect(() => {
    const ch = createPresentChannel(sessionId);
    ch.onPage((p) => setPage(p));
    ch.onPing(() => ch.pong());
    ch.pong();
    chRef.current = ch;
    return () => ch.close();
  }, [sessionId]);

  // Refit on window resize.
  useEffect(() => {
    const onResize = () =>
      setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // pdfjs transfers (detaches) the ArrayBuffer to its worker, so pass a fresh
  // copy each render (same pattern as SlidesPanel/PdfView).
  const file = useMemo(() => (bytes ? { data: bytes.slice() } : null), [bytes]);
  const safePage = Math.min(Math.max(1, page), numPages || 1);

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        background: "#000",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      {file && (
        <Document
          file={file}
          onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
          loading=""
        >
          {/* Fit by width; a landscape Beamer slide fits the viewport height at
              full width on a typical 16:9 / 4:3 projector. */}
          <Page pageNumber={safePage} width={size.w} loading="" />
        </Document>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Register the second entry in `frontend/vite.config.ts`**

Add `build.rollupOptions.input` to the `defineConfig` object (after the `server` key):

```typescript
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        present: path.resolve(__dirname, "present.html"),
      },
    },
  },
```

(`path` is already imported at the top of the file.)

- [ ] **Step 5: Build to verify the second entry compiles + emits**

Run: `cd frontend; npm run build`
Expected: build succeeds and `dist/present.html` exists. Verify:

Run: `cd frontend; node -e "process.exit(require('fs').existsSync('dist/present.html') ? 0 : 1)"`
Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/present.html frontend/src/present/ frontend/vite.config.ts
git commit -m "feat(slides): self-contained present.html audience window (second Vite entry)"
```

---

## Task 4: `usePresentation` hook — channel lifecycle + page broadcast + heartbeat

**Files:**
- Create: `frontend/src/hooks/usePresentation.ts`
- Test: `frontend/tests/hooks/usePresentation.test.tsx`

The hook owns the channel. It creates it whenever `presenting` is true and no channel exists (so it reconnects after the panel remounts during Q&A), broadcasts page changes, pings on an interval, and exposes `present()`/`stop()`. `openWindow` is injectable for tests.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/hooks/usePresentation.test.tsx
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePresentation } from "@/hooks/usePresentation";
import { useSlidesStore } from "@/store/slides";
import { createPresentChannel } from "@/lib/presentChannel";

describe("usePresentation", () => {
  beforeEach(() => {
    useSlidesStore.setState({
      presentingBySession: {},
      presentStartedAtBySession: {},
    });
  });

  it("present() opens the audience window + broadcasts the current page", () => {
    const openWindow = vi.fn(() => null);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));

    const { result } = renderHook(() =>
      usePresentation(7, 5, { openWindow }),
    );
    act(() => result.current.present());

    expect(openWindow).toHaveBeenCalledWith(
      "/present.html?session=7",
      "paperhub-present-7",
      expect.stringContaining("popup"),
    );
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(true);
    expect(pages).toContain(5);
    audience.close();
    act(() => result.current.stop());
  });

  it("broadcasts subsequent page changes while presenting", () => {
    const openWindow = vi.fn(() => null);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));

    let currentPage = 1;
    const { result, rerender } = renderHook(() =>
      usePresentation(7, currentPage, { openWindow }),
    );
    act(() => result.current.present());
    currentPage = 8;
    rerender();
    expect(pages).toContain(8);
    audience.close();
    act(() => result.current.stop());
  });

  it("stop() clears presenting", () => {
    const openWindow = vi.fn(() => null);
    const { result } = renderHook(() => usePresentation(7, 1, { openWindow }));
    act(() => result.current.present());
    act(() => result.current.stop());
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/hooks/usePresentation.test.tsx`
Expected: FAIL — `@/hooks/usePresentation` does not exist.

- [ ] **Step 3: Implement the hook**

```typescript
// frontend/src/hooks/usePresentation.ts
import { useCallback, useEffect, useRef, useState } from "react";

import { useSlidesStore } from "@/store/slides";
import { createPresentChannel, type PresentChannel } from "@/lib/presentChannel";

const HEARTBEAT_MS = 1000;
const STALE_MS = 2500;

interface Options {
  /** Injectable for tests; defaults to window.open. */
  openWindow?: (url: string, target: string, features: string) => Window | null;
}

export interface Presentation {
  presenting: boolean;
  audienceConnected: boolean;
  present: () => void;
  stop: () => void;
}

/**
 * Owns the BroadcastChannel for one session's presentation. The channel is
 * (re)created whenever `presenting` is true and none exists — so after the
 * Slides panel unmounts/remounts for a Q&A turn, reopening it reconnects to the
 * still-open audience window without reopening it. `presenting` lives in the
 * store, so it survives that remount.
 */
export function usePresentation(
  sessionId: number,
  currentPage: number,
  opts: Options = {},
): Presentation {
  const openWindow =
    opts.openWindow ?? ((u, t, f) => window.open(u, t, f));
  const presenting = useSlidesStore(
    (s) => s.presentingBySession[sessionId] ?? false,
  );
  const startPresenting = useSlidesStore((s) => s.startPresenting);
  const stopPresenting = useSlidesStore((s) => s.stopPresenting);

  const channelRef = useRef<PresentChannel | null>(null);
  const lastPongRef = useRef(0);
  const pageRef = useRef(currentPage);
  pageRef.current = currentPage;
  const [audienceConnected, setAudienceConnected] = useState(false);

  const present = useCallback(() => {
    startPresenting(sessionId);
    openWindow(
      `/present.html?session=${sessionId}`,
      `paperhub-present-${sessionId}`,
      "popup,width=1280,height=800",
    );
    // The channel is created by the effect below when `presenting` flips true.
  }, [sessionId, openWindow, startPresenting]);

  const stop = useCallback(() => {
    channelRef.current?.close();
    channelRef.current = null;
    setAudienceConnected(false);
    stopPresenting(sessionId);
  }, [sessionId, stopPresenting]);

  // (Re)create the channel whenever presenting and none exists. Covers both the
  // initial present() and a panel remount during Q&A.
  useEffect(() => {
    if (presenting && !channelRef.current) {
      const ch = createPresentChannel(sessionId);
      ch.onPong(() => {
        lastPongRef.current = Date.now();
      });
      channelRef.current = ch;
      ch.postPage(pageRef.current);
    }
  }, [presenting, sessionId]);

  // Broadcast page changes while presenting.
  useEffect(() => {
    if (presenting) channelRef.current?.postPage(currentPage);
  }, [presenting, currentPage]);

  // Heartbeat: ping the audience; mark connected while pongs stay fresh.
  useEffect(() => {
    if (!presenting) return;
    const id = setInterval(() => {
      channelRef.current?.ping();
      setAudienceConnected(Date.now() - lastPongRef.current < STALE_MS);
    }, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [presenting]);

  // Close the channel on unmount (it is recreated by the effect above on a
  // remount if still presenting). Does NOT stop presenting — Q&A reopen resumes.
  useEffect(
    () => () => {
      channelRef.current?.close();
      channelRef.current = null;
    },
    [],
  );

  return { presenting, audienceConnected, present, stop };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/hooks/usePresentation.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/usePresentation.ts frontend/tests/hooks/usePresentation.test.tsx
git commit -m "feat(slides): usePresentation hook (channel lifecycle, page sync, heartbeat)"
```

---

## Task 5: `PresenterControls` — timer + next-slide preview + sync badge + Stop

**Files:**
- Create: `frontend/src/components/slides/PresenterControls.tsx`
- Test: `frontend/tests/components/PresenterControls.test.tsx`

The next-slide preview is a react-pdf `<Page>` that must live inside the panel's `<Document>`; `PresenterControls` accepts it as a `nextPreview` slot so the component itself is react-pdf-free and unit-testable. `now` is injectable for a deterministic timer.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/components/PresenterControls.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PresenterControls } from "@/components/slides/PresenterControls";

describe("PresenterControls", () => {
  const base = {
    startedAt: 1000,
    currentPage: 2,
    numPages: 10,
    audienceConnected: true,
    onStop: () => {},
    now: () => 1000 + 65_000, // 65s elapsed
  };

  it("renders elapsed time mm:ss from startedAt", () => {
    render(<PresenterControls {...base} />);
    expect(screen.getByLabelText("elapsed time").textContent).toBe("01:05");
  });

  it("shows audience-connected state", () => {
    render(<PresenterControls {...base} />);
    expect(screen.getByText(/audience connected/i)).toBeInTheDocument();
  });

  it("shows audience-closed state when disconnected", () => {
    render(<PresenterControls {...base} audienceConnected={false} />);
    expect(screen.getByText(/audience window closed/i)).toBeInTheDocument();
  });

  it("calls onStop when Stop is clicked", () => {
    const onStop = vi.fn();
    render(<PresenterControls {...base} onStop={onStop} />);
    fireEvent.click(screen.getByLabelText("stop presenting"));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("renders the nextPreview slot when a next slide exists", () => {
    render(
      <PresenterControls
        {...base}
        nextPreview={<div data-testid="next" />}
      />,
    );
    expect(screen.getByTestId("next")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/components/PresenterControls.test.tsx`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement `PresenterControls.tsx`**

```tsx
import { useEffect, useState, type ReactNode } from "react";
import { Radio, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

interface Props {
  /** Epoch ms when presentation began (store presentStartedAtBySession). */
  startedAt: number;
  currentPage: number;
  numPages: number;
  audienceConnected: boolean;
  onStop: () => void;
  /** A react-pdf <Page> of currentPage+1, supplied by SlidesPanel so this
   *  component stays react-pdf-free (and unit-testable). */
  nextPreview?: ReactNode;
  /** Injectable clock for tests; defaults to Date.now. */
  now?: () => number;
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** Presenter cockpit strip — rendered at the top of the Slides panel's main
 *  column (inside the <Document>) while presenting. */
export function PresenterControls({
  startedAt,
  currentPage,
  numPages,
  audienceConnected,
  onStop,
  nextPreview,
  now = Date.now,
}: Props) {
  const [elapsed, setElapsed] = useState(() => now() - startedAt);
  useEffect(() => {
    const id = setInterval(() => setElapsed(now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [startedAt, now]);

  const hasNext = currentPage < numPages;

  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-3 py-1.5 text-xs">
      <span className="font-medium tabular-nums" aria-label="elapsed time">
        {formatElapsed(elapsed)}
      </span>
      <span
        className={
          audienceConnected
            ? "flex items-center gap-1 text-green-600 dark:text-green-400"
            : "flex items-center gap-1 text-muted-foreground"
        }
      >
        <Radio className="h-3 w-3" />
        {audienceConnected ? "audience connected" : "audience window closed"}
      </span>
      {hasNext && (
        <span className="ml-auto flex items-center gap-1 text-muted-foreground">
          next →
          <span className="block w-16 overflow-hidden rounded border border-border">
            {nextPreview}
          </span>
        </span>
      )}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={hasNext ? "h-6 px-2 gap-1" : "ml-auto h-6 px-2 gap-1"}
        onClick={onStop}
        aria-label="stop presenting"
      >
        <Square className="h-3 w-3" />
        Stop
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/components/PresenterControls.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/slides/PresenterControls.tsx frontend/tests/components/PresenterControls.test.tsx
git commit -m "feat(slides): PresenterControls (timer, next preview, sync badge, stop)"
```

---

## Task 6: Wire the presenter cockpit into `SlidesPanel`

**Files:**
- Modify: `frontend/src/components/slides/SlidesPanel.tsx`

SlidesPanel renders react-pdf, which isn't reliably unit-testable in jsdom; this wiring is verified by `npm run typecheck` + `npm run build` (Step 4) and the manual two-window gate (Task 9). The store-level Q&A invariant is already covered by Task 1.

- [ ] **Step 1: Add imports + hook + store reads**

In `frontend/src/components/slides/SlidesPanel.tsx`, add imports near the existing ones:

```typescript
import { Presentation } from "lucide-react";
import { usePresentation } from "@/hooks/usePresentation";
import { PresenterControls } from "@/components/slides/PresenterControls";
```

After the existing `setCurrentPage` store read (around line 87), add:

```typescript
  const presentStartedAt = useSlidesStore(
    (s) => s.presentStartedAtBySession[sessionId] ?? 0,
  );
  const { presenting, audienceConnected, present, stop } = usePresentation(
    sessionId,
    currentPage,
  );
```

- [ ] **Step 2: Add the Present/Stop button to the header**

In the header `<div>` (the block with the prev/next nav + download links), add a Present button just before the Download links `<a>`. When presenting, the header button reads "Presenting" and is disabled (Stop lives in the cockpit strip); otherwise it starts presentation. Disable it when the deck isn't ready.

```tsx
        <Button
          type="button"
          size="icon-xs"
          variant={presenting ? "default" : "ghost"}
          aria-label={presenting ? "presenting" : "present"}
          aria-pressed={presenting}
          disabled={presenting || numPages === 0 || deck?.status !== "ok"}
          onClick={() => present()}
          title={presenting ? "Presenting — Stop from the presenter bar" : "Open the audience window"}
        >
          <Presentation className="h-3 w-3" />
        </Button>
```

- [ ] **Step 3: Render the cockpit strip at the top of the main column**

Inside the `<Document>`, in the main content column `<div ref={measureMainArea} ...>`, render `PresenterControls` as the FIRST child (before the `Array.from({ length: numPages } ...)` pages map), so its next-slide `<Page>` shares the Document context:

```tsx
            {presenting && (
              <PresenterControls
                startedAt={presentStartedAt}
                currentPage={currentPage}
                numPages={numPages}
                audienceConnected={audienceConnected}
                onStop={stop}
                nextPreview={
                  currentPage < numPages ? (
                    <Page pageNumber={currentPage + 1} width={64} />
                  ) : undefined
                }
              />
            )}
```

- [ ] **Step 4: Verify types + build**

Run: `cd frontend; npm run typecheck; npm run build`
Expected: both succeed; `dist/present.html` still emitted.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/slides/SlidesPanel.tsx
git commit -m "feat(slides): presenter cockpit in SlidesPanel (Present button + controls strip)"
```

---

## Task 7: `speech.ts` — Web Speech recognizer wrapper

**Files:**
- Create: `frontend/src/lib/speech.ts`
- Test: `frontend/tests/lib/speech.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/tests/lib/speech.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSpeechRecognizer, isSpeechSupported } from "@/lib/speech";

interface FakeResult {
  0: { transcript: string };
}

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  onresult: ((e: { results: FakeResult[] }) => void) | null = null;
  onerror: ((e: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  emit(transcripts: string[]) {
    this.onresult?.({
      results: transcripts.map((t) => ({ 0: { transcript: t } })) as FakeResult[],
    });
  }
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).SpeechRecognition;
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
});

describe("speech", () => {
  it("isSpeechSupported reflects the API presence", () => {
    expect(isSpeechSupported()).toBe(false);
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    expect(isSpeechSupported()).toBe(true);
  });

  it("returns null when unsupported", () => {
    expect(createSpeechRecognizer({ onInterim: () => {} })).toBeNull();
  });

  it("feeds concatenated transcript to onInterim and starts/stops", () => {
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    const seen: string[] = [];
    const rec = createSpeechRecognizer({ onInterim: (t) => seen.push(t) });
    expect(rec).not.toBeNull();
    rec!.start();
    // grab the underlying fake to emit a result
    const instance = (rec as unknown as { _raw: FakeRecognition })._raw;
    instance.emit(["hello ", "world"]);
    expect(seen).toContain("hello world");
    rec!.stop();
    expect(instance.stop).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/lib/speech.test.ts`
Expected: FAIL — `@/lib/speech` does not exist.

- [ ] **Step 3: Implement `speech.ts`**

```typescript
// frontend/src/lib/speech.ts

export interface SpeechRecognizer {
  start: () => void;
  stop: () => void;
  /** The underlying recognition instance — exposed for tests only. */
  _raw: unknown;
}

interface Handlers {
  /** Full transcript (interim + final) accumulated so far this session. */
  onInterim: (text: string) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

interface MinimalRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type RecognitionCtor = new () => MinimalRecognition;

function getCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function isSpeechSupported(): boolean {
  return getCtor() !== null;
}

/** Create a recognizer, or null if the browser has no Web Speech API.
 *  Continuous (manual stop, not silence-gated); interim results stream so the
 *  composer fills live as you speak. */
export function createSpeechRecognizer(
  handlers: Handlers,
): SpeechRecognizer | null {
  const Ctor = getCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = navigator.language || "en-US";
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = (e) => {
    let text = "";
    for (let i = 0; i < e.results.length; i++) {
      text += e.results[i][0].transcript;
    }
    handlers.onInterim(text);
  };
  rec.onerror = (e) => handlers.onError?.(String(e?.error ?? "speech-error"));
  rec.onend = () => handlers.onEnd?.();
  return {
    start: () => rec.start(),
    stop: () => rec.stop(),
    _raw: rec,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/lib/speech.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/speech.ts frontend/tests/lib/speech.test.ts
git commit -m "feat(chat): Web Speech recognizer wrapper (lib/speech)"
```

---

## Task 8: Voice mic button in the Composer

**Files:**
- Modify: `frontend/src/components/chat/Composer.tsx`
- Test: `frontend/tests/components/Composer.voice.test.tsx`

Dictation appends to the existing draft (capture the draft at start; interim transcript is appended to that base). Manual stop; the user reviews and sends. Hidden when the API is unsupported.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/components/Composer.voice.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/Composer";
import { useChatStore } from "@/store/chat";

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null = null;
  onerror: (() => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  emit(t: string) {
    this.onresult?.({ results: [{ 0: { transcript: t } }] });
  }
}

beforeEach(() => {
  useChatStore.setState({ composerDraft: "" });
});
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).SpeechRecognition;
});

describe("Composer voice input", () => {
  it("hides the mic when Web Speech is unsupported", () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    expect(screen.queryByLabelText("Voice input")).not.toBeInTheDocument();
  });

  it("dictates interim transcript into the composer draft", () => {
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    render(<Composer onSubmit={() => {}} disabled={false} />);
    const mic = screen.getByLabelText("Voice input");
    fireEvent.click(mic); // start
    // reach the constructed instance via the global last-created hook
    const instance = (
      window as unknown as { __lastRecognition?: FakeRecognition }
    ).__lastRecognition;
    expect(instance).toBeDefined();
    instance!.emit("find the limitations");
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).value,
    ).toContain("find the limitations");
  });
});
```

> Note: to let the test reach the constructed recognition instance, the fake stores itself on `window.__lastRecognition` in its constructor. Add that line to the `FakeRecognition` constructor in the test:
> ```tsx
> constructor() {
>   (window as unknown as { __lastRecognition?: FakeRecognition }).__lastRecognition = this;
> }
> ```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend; npx vitest run tests/components/Composer.voice.test.tsx`
Expected: FAIL — no "Voice input" button.

- [ ] **Step 3: Implement the mic in `Composer.tsx`**

Add imports:

```typescript
import { useState } from "react";
import { Mic } from "lucide-react";
import {
  createSpeechRecognizer,
  isSpeechSupported,
  type SpeechRecognizer,
} from "@/lib/speech";
```

(Merge `useState` into the existing `react` import line; it currently imports `KeyboardEvent, useEffect, useRef`.)

Inside the `Composer` component body (after the existing `ref` declaration), add the voice state + handlers:

```typescript
  const [listening, setListening] = useState(false);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const baseDraftRef = useRef("");
  const speechSupported = isSpeechSupported();

  const toggleVoice = () => {
    if (listening) {
      recognizerRef.current?.stop();
      return;
    }
    baseDraftRef.current = value;
    const rec = createSpeechRecognizer({
      onInterim: (text) => {
        const base = baseDraftRef.current;
        setValue(base && text ? `${base} ${text}` : base || text);
      },
      onEnd: () => setListening(false),
      onError: () => setListening(false),
    });
    if (!rec) return;
    recognizerRef.current = rec;
    rec.start();
    setListening(true);
  };
```

In the tool-row `<div className="flex items-center gap-0.5">`, add the mic button right after `<AttachPaperMenu />` (only when supported):

```tsx
                {speechSupported && (
                  <Tooltip>
                    <TooltipTrigger
                      render={<span tabIndex={0} className="inline-flex" />}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={toggleVoice}
                        aria-pressed={listening}
                        className={
                          listening
                            ? "h-8 w-8 bg-accent text-foreground"
                            : "h-8 w-8 text-muted-foreground hover:text-foreground"
                        }
                        aria-label="Voice input"
                      >
                        <Mic className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p>
                        {listening
                          ? "Listening — click to stop"
                          : "Dictate your question (Web Speech)"}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend; npx vitest run tests/components/Composer.voice.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/Composer.tsx frontend/tests/components/Composer.voice.test.tsx
git commit -m "feat(chat): voice-input mic in the composer (Web Speech, manual stop/send)"
```

---

## Task 9: Full gates, docs, and manual two-window verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the full frontend gate**

Run from `frontend/`:

```powershell
npm test ; npm run typecheck ; npm run lint ; npm run build
```

Expected: all green; `dist/present.html` emitted. Fix any failure before continuing.

- [ ] **Step 2: Verify nginx serves `present.html` (no change expected)**

Confirm `frontend/nginx.conf`'s SPA block is `location / { try_files $uri $uri/ /index.html; }` — `$uri` resolves the real `/present.html` file before the `index.html` fallback, so no edit is needed. (If, and only if, a future change makes `/` proxy or rewrite everything, add `location = /present.html { try_files $uri =404; }` above the SPA block.) No commit if unchanged.

- [ ] **Step 3: Manual two-window verification (the real F5 gate)**

Prereq: the user's stack is up on `:8000` + the frontend dev server (or container). If `:8000` is not reachable, STOP and ask the user to start it — do not boot your own.

1. Generate a deck (any `slides` turn) and open the **Slides panel**.
2. Click **Present** → a second window opens; drag it to a second screen / fullscreen it. It shows the current slide, black background, no chrome.
3. Flip slides in the cockpit (arrows / filmstrip) → the audience window follows within ~1 frame; the **sync badge** reads "audience connected"; the **timer** counts up; the **next-slide preview** shows page+1.
4. Type a question, then dictate one with the **mic** (Chrome/Edge) → both produce a normal `paper_qa` turn.
5. Click a `[chunk:N]` citation → the **Citation Canvas** opens and the **Slides panel closes**; confirm the **audience window keeps its slide** (unchanged on the projector).
6. Click the **Slides button** → the panel reopens **in presenter mode on the same page** (timer still running); flip a slide → the audience window resumes following.
7. Click **Stop** in the cockpit → the channel closes (badge → "audience window closed"); the audience window keeps its last slide. Close it manually.
8. In Firefox/Safari, confirm the mic button is **absent** (graceful degrade) and everything else works.

- [ ] **Step 4: Update `CLAUDE.md`**

- In the plan table, change the **F5** row status from `pending` to `**complete**` and point it at this plan file (`docs/superpowers/plans/2026-06-05-paperhub-F5-presentation-voice.md`).
- In **Plan F known follow-ups**, mark item #1 (presentation mode) closed, noting TTS remains deferred to a future voice-tutor plan.
- Add two pointers under "Pointers to common questions":
  - *"How does presentation page-sync work? → the audience window is a self-contained `present.html` second Vite entry (`/present.html?session=<sid>`) that fetches the deck PDF itself; the in-app SlidesPanel cockpit broadcasts `{page}` over `BroadcastChannel('paperhub-present-<sid>')`. `presenting` + `currentPage` live in the `slides` Zustand store so the Q&A close/reopen resumes on the same page; the audience window holds its own state. SRS v2.26 / FR-12."*
  - *"How does voice input work? → a composer mic (`lib/speech.ts` over the browser Web Speech `SpeechRecognition`) dictates interim transcript into the draft; manual stop, manual send; hidden where the API is absent. TTS (spoken answers) is deferred to a future voice-tutor plan. SRS v2.26 / FR-05."*

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(F5): mark presentation + voice complete; add sync/voice pointers"
```

- [ ] **Step 6: Finish the branch**

Use the **superpowers:finishing-a-development-branch** skill to decide merge/PR. Per CLAUDE.md, `git push` / PR creation / merge stay gated on explicit user approval — describe the exact command and wait. A release version bump (→ 2.26.0 across `paperhub` / `frontend` / `paperhub-marker`, matching the SRS) belongs to that finishing step.

---

## Self-Review

**Spec coverage (SRS v2.26 / UC-4 / FR-05 / FR-12):**
- Audience window = self-contained `present.html` ✓ (Task 3).
- Presenter cockpit (timer · next preview · sync badge · Stop) ✓ (Tasks 5–6).
- `BroadcastChannel` page sync ✓ (Tasks 2, 4).
- Q&A-during-talk loop — store invariant (presenting + page survive close/reopen) ✓ (Task 1); choreography rides the existing ChatPage right-slot subscription (verified manual, Task 9 step 3.5–3.6).
- Voice input (Web Speech mic, manual stop/send, graceful degrade) ✓ (Tasks 7–8).
- TTS explicitly out of scope ✓ (stated in header + Task 9 docs).
- Backend: none ✓ (nginx serves the static entry; existing PDF endpoint reused — Task 3, Task 9 step 2).

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. The one cross-step reference (the test fake storing itself on `window.__lastRecognition`) is spelled out inline in Task 8 Step 1.

**Type consistency:** `createPresentChannel` shape (`postPage`/`onPage`/`ping`/`onPing`/`pong`/`onPong`/`close`) is identical across Task 2 (def), Task 3 (audience consumer), and Task 4 (presenter producer). `usePresentation(sessionId, currentPage, { openWindow })` signature matches between Task 4 (def/test) and Task 6 (caller). `PresenterControls` props (`startedAt`/`currentPage`/`numPages`/`audienceConnected`/`onStop`/`nextPreview`/`now`) match between Task 5 (def/test) and Task 6 (caller). `startPresenting`/`stopPresenting`/`presentingBySession`/`presentStartedAtBySession` match between Task 1 (store), Task 4 (hook), Task 6 (panel). `createSpeechRecognizer`/`isSpeechSupported`/`SpeechRecognizer._raw` match between Task 7 (def/test) and Task 8 (composer).

**Untestable-by-design (no silent gaps):** `present.html`/`PresentPage` (Task 3) and the SlidesPanel react-pdf wiring (Task 6) are verified by `npm run build` + typecheck + the manual two-window gate (Task 9), because react-pdf does not render reliably in jsdom — stated explicitly in each task, not skipped.
