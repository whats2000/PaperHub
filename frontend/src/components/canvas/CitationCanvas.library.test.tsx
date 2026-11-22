import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), message: vi.fn(), success: vi.fn() } }));
vi.mock("@/components/canvas/PdfView", () => ({ PdfView: () => <div data-testid="pdf-view" /> }));

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import type { ReferenceItem } from "@/types/domain";

const IN_CHAT = 7;
const LIBRARY = 9;
const OTHER = 10;
const TITLES: Record<number, string> = { [LIBRARY]: "LoRA: Low-Rank Adaptation", [OTHER]: "Attention Is All You Need" };

function ref(over: Partial<ReferenceItem>): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: IN_CHAT,
    enabled: true,
    added_at: "2024-01-01",
    arxiv_id: null,
    title: "Chat paper",
    year: 2024,
    kind: "arxiv",
    is_private: false,
    ...over,
  };
}

const attached: unknown[] = [];
const server = setupServer(
  http.get(`${API_BASE_URL}/papers/content/:pid/document`, () =>
    HttpResponse.json({ mode: "html", page_pdf: false }),
  ),
  http.get(`${API_BASE_URL}/papers/content/:pid/html`, () =>
    HttpResponse.text("<!DOCTYPE html><html><body></body></html>"),
  ),
  http.get(`${API_BASE_URL}/papers/content/:pid/info`, ({ params }) =>
    HttpResponse.json({ id: Number(params.pid), title: TITLES[Number(params.pid)] ?? "Paper" }),
  ),
  http.post(`${API_BASE_URL}/papers/from-library`, async ({ request }) => {
    attached.push(await request.json());
    return HttpResponse.json({ papers_id: 5, paper_content_id: LIBRARY, title: "LoRA", cache_hit: true });
  }),
  http.get(`${API_BASE_URL}/papers`, () =>
    HttpResponse.json([
      ref({}),
      ref({ papers_id: 5, paper_content_id: LIBRARY, title: "LoRA: Low-Rank Adaptation" }),
    ]),
  ),
  http.get(`${API_BASE_URL}/translations/config`, () => HttpResponse.json({ full_paper: false })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

let chat = 0;
beforeEach(() => {
  attached.length = 0;
  useCanvasStore.setState({
    open: false,
    requestedChunkId: null,
    requestedPaperId: null,
    paperRequestNonce: 0,
    activePaperId: null,
    outsideByChat: {},
    closedTab: null,
  });
  useChatStore.getState().reset();
  chat = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(chat, 99);
  useChatStore.getState().setReferences(99, [ref({})]);
});

describe("CitationCanvas — a library paper the chat does not have", () => {
  it("opens it view-only, named from the library, with Add to bring it into the chat", async () => {
    const user = userEvent.setup();
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(LIBRARY));
    expect(await screen.findByText("LoRA: Low-Rank Adaptation")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Add/ }));
    await waitFor(() => expect(attached).toEqual([{ session_id: 99, paper_content_id: LIBRARY }]));
    await waitFor(() =>
      expect(useChatStore.getState().referencesBySession[99]?.map((r) => r.paper_content_id)).toEqual([
        IN_CHAT,
        LIBRARY,
      ]),
    );
  });

  it("does not follow the user into another chat", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(LIBRARY));
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 98);
      useChatStore.getState().setReferences(98, [ref({ papers_id: 3 })]);
      useChatStore.getState().selectSession(other);
    });
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(IN_CHAT));
  });
});

describe("CitationCanvas — paper tabs shared with the notes pane", () => {
  const dashed = () => document.querySelectorAll('[data-paper-tab][data-dashed="true"]:not([data-leaving])');
  const shell = (name: string) => screen.getByRole("tab", { name }).closest("[data-paper-tab]") as HTMLElement;

  it("draws the chat's papers as tabs of a Papers tablist", async () => {
    useChatStore.getState().setReferences(99, [ref({}), ref({ papers_id: 2, paper_content_id: 8, title: "Mamba" })]);
    const user = userEvent.setup();
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(IN_CHAT));
    const list = await screen.findByRole("tablist", { name: "Papers" });
    expect(screen.getByRole("tab", { name: "Chat paper" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Mamba" }));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(8));
    expect(list).toContainElement(screen.getByRole("tab", { name: "Mamba" }));
  });

  it("a paper opened by a link is a temporary tab; the next one replaces it", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    expect(await screen.findByRole("tab", { name: TITLES[LIBRARY] })).toBeInTheDocument();
    expect(shell(TITLES[LIBRARY]!)).toHaveAttribute("data-preview", "true");
    act(() => useCanvasStore.getState().openPaper(OTHER));
    expect(await screen.findByRole("tab", { name: TITLES[OTHER] })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("tab", { name: TITLES[LIBRARY] })).toBeNull());
    expect(dashed()).toHaveLength(1);
    expect(useCanvasStore.getState().outsideByChat[chat]).toEqual([{ paperId: OTHER, preview: true }]);
  });

  it("a double click keeps the tab: the next link opens beside it", async () => {
    const user = userEvent.setup();
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await user.dblClick(await screen.findByRole("tab", { name: TITLES[LIBRARY] }));
    expect(shell(TITLES[LIBRARY]!)).toHaveAttribute("data-preview", "false");
    act(() => useCanvasStore.getState().openPaper(OTHER));
    expect(await screen.findByRole("tab", { name: TITLES[OTHER] })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: TITLES[LIBRARY] })).toBeInTheDocument();
  });

  it("× closes the temporary tab and lands on the neighbouring paper", async () => {
    const user = userEvent.setup();
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await screen.findByRole("tab", { name: TITLES[LIBRARY] });
    await user.click(screen.getByRole("button", { name: `Close ${TITLES[LIBRARY]}` }));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(IN_CHAT));
    expect(useCanvasStore.getState().outsideByChat[chat]).toEqual([]);
    expect(useCanvasStore.getState().closedTab).toMatchObject({ paperId: LIBRARY, neighbour: IN_CHAT });
  });

  it("a tab closed in the notes pane closes here too", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(LIBRARY));
    act(() => useCanvasStore.getState().closeOutside(chat, LIBRARY, IN_CHAT));
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(IN_CHAT));
    await waitFor(() => expect(dashed()).toHaveLength(0));
  });

  it("the notes pane picking a paper moves the reader without opening it", async () => {
    useChatStore.getState().setReferences(99, [ref({}), ref({ papers_id: 2, paper_content_id: 8, title: "Mamba" })]);
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().selectPaper(8));
    expect(useCanvasStore.getState().open).toBe(false);
    act(() => {
      useCanvasStore.setState({ open: true });
    });
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(8));
  });

  it("a temporary tab does not come back after a chat switch", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LIBRARY));
    await screen.findByRole("tab", { name: TITLES[LIBRARY] });
    act(() => {
      const other = useChatStore.getState().newSession();
      useChatStore.getState().patchSessionBackendId(other, 98);
      useChatStore.getState().setReferences(98, [ref({ papers_id: 3 })]);
      useChatStore.getState().selectSession(other);
      useCanvasStore.getState().resetForSessionChange();
    });
    act(() => useChatStore.getState().selectSession(chat));
    act(() => {
      useCanvasStore.setState({ open: true });
    });
    await waitFor(() => expect(useCanvasStore.getState().activePaperId).toBe(IN_CHAT));
    expect(dashed()).toHaveLength(0);
  });
});
