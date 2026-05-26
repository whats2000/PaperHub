import { StrictMode } from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), message: vi.fn() } }));
import { toast } from "sonner";

// react-pdf pulls in pdfjs (worker, canvas) which doesn't run under jsdom —
// stub it to a simple marker so we can assert the PDF path without rendering.
vi.mock("@/components/canvas/PdfView", () => ({
  PdfView: ({
    data,
    highlightText,
  }: {
    data: Uint8Array;
    highlightText?: string | null;
  }) => (
    <div data-testid="pdf-view">
      pdf:{data.length}:{highlightText ?? ""}
    </div>
  ),
}));

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { API_BASE_URL } from "@/lib/api";
import type { ReferenceItem } from "@/types/domain";

function ref(over: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: 7,
    enabled: true,
    added_at: "2024-01-01",
    arxiv_id: "1706.03762",
    title: "Attention Is All You Need",
    year: 2017,
    kind: "arxiv",
    ...over,
  };
}

const htmlBody = (label: string) =>
  `<!DOCTYPE html><html><body><p>${label} body</p></body></html>`;

const server = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2",
      text: "Expert collapse is mitigated.",
      dom_id: null,
    }),
  ),
  http.get(`${API_BASE_URL}/papers/content/7/document`, () =>
    HttpResponse.json({ mode: "html" }),
  ),
  http.get(`${API_BASE_URL}/papers/content/8/document`, () =>
    HttpResponse.json({ mode: "html" }),
  ),
  http.get(`${API_BASE_URL}/papers/content/7/html`, () =>
    HttpResponse.text(htmlBody("Paper A")),
  ),
  http.get(`${API_BASE_URL}/papers/content/8/html`, () =>
    HttpResponse.text(htmlBody("Paper B")),
  ),
  http.get(`${API_BASE_URL}/papers/content/9/document`, () =>
    HttpResponse.json({ mode: "html" }),
  ),
  http.get(`${API_BASE_URL}/papers/content/9/html`, () =>
    HttpResponse.text(htmlBody("Paper C")),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  vi.clearAllMocks();
  useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 });
  useChatStore.getState().reset();
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 99);
  useChatStore.getState().setReferences(99, [
    ref({ papers_id: 1, paper_content_id: 7, title: "Paper A" }),
    ref({ papers_id: 2, paper_content_id: 8, title: "Paper B", arxiv_id: "2005.14165" }),
  ]);
});

const activeHtmlView = (c: HTMLElement): HTMLIFrameElement | null =>
  c.querySelector('div:not([hidden]) > iframe[title="Citation Canvas"]');

describe("CitationCanvas reading panel", () => {
  it("renders nothing when closed with no references", () => {
    useChatStore.getState().setReferences(99, []);
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("embeds the paper HTML via srcdoc (same-origin) when opened via citation", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() => {
      const iframe = activeHtmlView(container);
      expect(iframe).not.toBeNull();
      expect(iframe?.getAttribute("srcdoc")).toContain("Paper A body");
    });
  });

  it("loads paper content under StrictMode (no stuck 'Loading…')", async () => {
    // StrictMode double-invokes effects (setup→cleanup→setup). A `cancelled`
    // guard + fetch dedup would discard the in-flight result and block the
    // re-fetch, leaving the paper stuck loading. This guards that regression.
    const { container } = render(<CitationCanvas />, { wrapper: StrictMode });
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper A body",
      ),
    );
  });

  it("switches papers via the tab switcher", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().toggleCanvas());
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper A body",
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: /Paper B/ }));
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper B body",
      ),
    );
  });

  it("renders a PDF via react-pdf (PdfView) for pdf-rendered papers", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/content/7/document`, () =>
        HttpResponse.json({ mode: "pdf" }),
      ),
      http.get(`${API_BASE_URL}/papers/content/7/pdf`, () =>
        HttpResponse.arrayBuffer(new Uint8Array([1, 2, 3, 4]).buffer, {
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    );
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    const view = await screen.findByTestId("pdf-view");
    expect(view).toBeInTheDocument();
    // The cited chunk's text is passed through for in-PDF highlighting.
    expect(view).toHaveTextContent("Expert collapse is mitigated.");
  });

  it("defaults to the current session's paper after a session switch (not the previous one)", async () => {
    const { container } = render(<CitationCanvas />);
    // Open a citation in session A → shows paper 7.
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper A body",
      ),
    );
    // Switch to a different session (B) whose references are a different paper.
    act(() => {
      const sidB = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(sidB, 100);
      useChatStore.getState().setReferences(100, [
        ref({ papers_id: 9, paper_content_id: 9, title: "Paper C" }),
      ]);
    });
    // The panel must show session B's paper (9), not the leftover paper 7.
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper C body",
      ),
    );
  });

  it("views a toggled-off reference's cited paper view-only (Add affordance, enabled untouched)", async () => {
    // Paper A (7) — the cited chunk's source — is toggled OFF; only Paper B is
    // an active reference. Clicking the citation must still SHOW Paper A.
    useChatStore.getState().setReferences(99, [
      ref({ papers_id: 1, paper_content_id: 7, title: "Paper A", enabled: false }),
      ref({ papers_id: 2, paper_content_id: 8, title: "Paper B", enabled: true }),
    ]);
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));

    // The disabled paper is fetched on demand and displayed.
    await waitFor(() =>
      expect(activeHtmlView(container)?.getAttribute("srcdoc")).toContain(
        "Paper A body",
      ),
    );
    // A view-only "Add" affordance is offered...
    expect(screen.getByRole("button", { name: /Add/ })).toBeInTheDocument();
    // ...and the reference's enabled state was NOT changed by merely viewing it.
    const refs = useChatStore.getState().referencesBySession[99] ?? [];
    expect(refs.find((r) => r.papers_id === 1)?.enabled).toBe(false);
  });

  it("Add promotes the view-only paper to an enabled reference", async () => {
    let patched: { enabled: boolean } | null = null;
    server.use(
      http.patch(`${API_BASE_URL}/papers/1`, async ({ request }) => {
        patched = (await request.json()) as { enabled: boolean };
        return HttpResponse.json({ enabled: true });
      }),
    );
    useChatStore.getState().setReferences(99, [
      ref({ papers_id: 1, paper_content_id: 7, title: "Paper A", enabled: false }),
      ref({ papers_id: 2, paper_content_id: 8, title: "Paper B", enabled: true }),
    ]);
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));

    const addBtn = await screen.findByRole("button", { name: /Add/ });
    await userEvent.click(addBtn);

    // Backend told to enable, and the store now has Paper A enabled → the
    // transient "Add" affordance is gone (it became a normal reference tab).
    await waitFor(() => expect(patched).toEqual({ enabled: true }));
    expect(
      (useChatStore.getState().referencesBySession[99] ?? []).find(
        (r) => r.papers_id === 1,
      )?.enabled,
    ).toBe(true);
    expect(screen.queryByRole("button", { name: /Add/ })).not.toBeInTheDocument();
  });

  it("shows a stale notice when getChunk 404s, no toast.error", async () => {
    server.use(
      http.get(`${API_BASE_URL}/chunks/42`, () =>
        HttpResponse.json({ detail: "no chunk 42" }, { status: 404 }),
      ),
    );
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    expect(
      await screen.findByText(/no longer available|re-indexed/i),
    ).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("close hides the panel (aria-hidden) and clears open", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() => expect(activeHtmlView(container)).not.toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(useCanvasStore.getState().open).toBe(false);
    expect(
      container.querySelector('aside[aria-label="Citation Canvas"]'),
    ).toHaveAttribute("aria-hidden", "true");
  });
});
