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
  http.get(`${API_BASE_URL}/papers/content/${LIBRARY}/info`, () =>
    HttpResponse.json({ id: LIBRARY, title: "LoRA: Low-Rank Adaptation" }),
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
