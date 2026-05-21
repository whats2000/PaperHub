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

const activeIframe = (c: HTMLElement): HTMLIFrameElement | null =>
  c.querySelector('iframe[data-active="true"]');

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

describe("CitationCanvas reading panel", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("opening via citation resolves the chunk and shows that paper (html)", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() =>
      expect(activeIframe(container)).toHaveAttribute(
        "src",
        "/papers/content/7/html",
      ),
    );
    expect(activeIframe(container)).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-same-origin",
    );
  });

  it("renders a switcher tab per enabled reference; clicking switches the paper and keeps both iframes alive", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().toggleCanvas());
    await waitFor(() =>
      expect(activeIframe(container)).toHaveAttribute(
        "src",
        "/papers/content/7/html",
      ),
    );
    expect(screen.getByRole("button", { name: /Paper A/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Paper B/ }));
    await waitFor(() =>
      expect(activeIframe(container)).toHaveAttribute(
        "src",
        "/papers/content/8/html",
      ),
    );
    // Keep-alive: the previously-viewed Paper A iframe is still mounted.
    expect(container.querySelectorAll("iframe")).toHaveLength(2);
  });

  it("shows the source PDF (not html) for a pdf-rendered paper", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/content/7/document`, () =>
        HttpResponse.json({ mode: "pdf" }),
      ),
    );
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() =>
      expect(activeIframe(container)).toHaveAttribute(
        "src",
        "/papers/content/7/pdf",
      ),
    );
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

  it("close removes the panel", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openCitation(42));
    await waitFor(() => expect(activeIframe(container)).not.toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByLabelText(/citation canvas/i)).toBeNull();
  });
});
