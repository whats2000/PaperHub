import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ReferenceSourcesPanel } from "@/components/references/ReferenceSourcesPanel";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { API_BASE_URL } from "@/lib/api";
import type { ReferenceItem } from "@/types/domain";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRef(overrides: Partial<ReferenceItem> = {}): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: 1,
    enabled: true,
    added_at: "2024-01-01T00:00:00",
    arxiv_id: "1706.03762",
    title: "Attention Is All You Need",
    year: 2017,
    kind: "arxiv",
    ...overrides,
  };
}

function seedSession(backendId: number | null = null): number {
  const store = useChatStore.getState();
  const frontendId = store.newSession();
  if (backendId !== null) {
    store.patchSessionBackendId(frontendId, backendId);
  }
  return frontendId;
}

// ---------------------------------------------------------------------------
// MSW server
// ---------------------------------------------------------------------------

const sampleRefs = [makeRef()];

const server = setupServer(
  http.get(`${API_BASE_URL}/papers`, () => HttpResponse.json(sampleRefs)),
  http.post(`${API_BASE_URL}/sessions`, () =>
    HttpResponse.json({ session_id: 42 }, { status: 201 }),
  ),
  http.patch(`${API_BASE_URL}/papers/1`, () =>
    HttpResponse.json({ enabled: false }),
  ),
  http.delete(`${API_BASE_URL}/papers/1`, () =>
    new HttpResponse(null, { status: 204 }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  server.resetHandlers(
    http.get(`${API_BASE_URL}/papers`, () => HttpResponse.json(sampleRefs)),
    http.post(`${API_BASE_URL}/sessions`, () =>
      HttpResponse.json({ session_id: 42 }, { status: 201 }),
    ),
    http.patch(`${API_BASE_URL}/papers/1`, () =>
      HttpResponse.json({ enabled: false }),
    ),
    http.delete(`${API_BASE_URL}/papers/1`, () =>
      new HttpResponse(null, { status: 204 }),
    ),
  );
  useChatStore.getState().reset();
  useCanvasStore.setState({
    open: false,
    requestedPaperId: null,
    paperRequestNonce: 0,
    activePaperId: null,
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReferenceSourcesPanel", () => {
  it("renders an empty-session hint when frontendSessionId is null", () => {
    render(<ReferenceSourcesPanel frontendSessionId={null} />);
    expect(
      screen.getByText(/start or pick a chat to manage/i),
    ).toBeInTheDocument();
  });

  it("clicking 'Add from library' lazy-creates the backend session", async () => {
    const frontendId = seedSession(null);
    const patchSpy = vi.spyOn(
      useChatStore.getState(),
      "patchSessionBackendId",
    );

    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);

    const addBtn = screen.getByRole("button", { name: /add from library/i });
    await userEvent.click(addBtn);

    // POST /sessions must have been called and patchSessionBackendId(frontendId, 42).
    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith(frontendId, 42);
    });
  });

  it("loads references and renders the list when backend session exists", async () => {
    const frontendId = seedSession(42);
    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);

    expect(
      await screen.findByText("Attention Is All You Need"),
    ).toBeInTheDocument();
  });

  it("renders empty-list hint when there are zero references", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers`, () => HttpResponse.json([])),
    );
    const frontendId = seedSession(42);
    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);

    expect(
      await screen.findByText(/no papers attached to this session yet/i),
    ).toBeInTheDocument();
  });

  it("toggling the switch fires PATCH and updates store optimistically", async () => {
    const frontendId = seedSession(42);
    let patchSawDisable = false;
    server.use(
      http.patch(`${API_BASE_URL}/papers/1`, async ({ request }) => {
        const body = (await request.json()) as { enabled: boolean };
        if (body.enabled === false) patchSawDisable = true;
        return HttpResponse.json({ enabled: false });
      }),
    );

    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);
    await screen.findByText("Attention Is All You Need");

    const sw = screen.getByRole("switch", { name: /toggle attention/i });
    await userEvent.click(sw);

    await waitFor(() => {
      expect(patchSawDisable).toBe(true);
    });
    // Optimistic store update — references map shows enabled=false for papers_id=1
    const refs = useChatStore.getState().referencesBySession[42] ?? [];
    expect(refs[0]?.enabled).toBe(false);
  });

  it("clicking 'Open in canvas' opens the canvas for that paper", async () => {
    const frontendId = seedSession(42);
    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);
    await screen.findByText("Attention Is All You Need");

    const openBtn = screen.getByRole("button", {
      name: /open attention is all you need in canvas/i,
    });
    await userEvent.click(openBtn);

    // The canvas store now requests this paper (paper_content_id 1) + is open.
    expect(useCanvasStore.getState().open).toBe(true);
    expect(useCanvasStore.getState().requestedPaperId).toBe(1);
  });

  it("marks the row active when its paper is shown on the canvas", async () => {
    const frontendId = seedSession(42);
    useCanvasStore.setState({ open: true, activePaperId: 1 });
    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);
    await screen.findByText("Attention Is All You Need");

    // The active paper's open button reflects the live state (label + pressed).
    const activeBtn = screen.getByRole("button", {
      name: /attention is all you need is open in canvas/i,
    });
    expect(activeBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("clicking trash fires DELETE and removes the row optimistically", async () => {
    const frontendId = seedSession(42);
    let deleteCalled = false;
    server.use(
      http.delete(`${API_BASE_URL}/papers/1`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    render(<ReferenceSourcesPanel frontendSessionId={frontendId} />);
    await screen.findByText("Attention Is All You Need");

    const trash = screen.getByRole("button", { name: /remove attention/i });
    await userEvent.click(trash);

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
    expect(screen.queryByText("Attention Is All You Need")).not.toBeInTheDocument();
  });
});
