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
  PdfView: ({ data }: { data: Uint8Array }) => (
    <div data-testid="pdf-view">pdf:{data.length}</div>
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
    expect(await screen.findByTestId("pdf-view")).toBeInTheDocument();
    expect(
      await screen.findByText(/source PDF|isn't available for PDF/i),
    ).toBeInTheDocument();
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
