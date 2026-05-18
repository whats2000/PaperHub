import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ReferenceSourcesDrawer } from "@/components/references/ReferenceSourcesDrawer";
import { useChatStore } from "@/store/chat";
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

const sampleRefs = [makeRef()];

/**
 * Seed a frontend session into the store and return its id.
 * Optionally supply a backendId to pre-assign backend_session_id.
 */
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

const server = setupServer(
  http.get(`${API_BASE_URL}/papers`, () =>
    HttpResponse.json(sampleRefs),
  ),
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
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReferenceSourcesDrawer", () => {
  it("renders nothing when frontendSessionId is null", () => {
    const { container } = render(
      <ReferenceSourcesDrawer frontendSessionId={null} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("trigger_renders_without_backend_session_id", () => {
    // Frontend session exists but backend_session_id is null.
    const frontendId = seedSession(null);

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    // Trigger button must be visible even though backend session doesn't exist yet.
    const triggerBtn = screen.getByRole("button", { name: /references/i });
    expect(triggerBtn).toBeInTheDocument();
  });

  it("clicking_trigger_creates_backend_session_when_missing", async () => {
    // Frontend session without backend_session_id.
    const frontendId = seedSession(null);
    const patchSpy = vi.spyOn(useChatStore.getState(), "patchSessionBackendId");

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    const triggerBtn = screen.getByRole("button", { name: /references/i });
    await userEvent.click(triggerBtn);

    // POST /sessions must have been called, and patchSessionBackendId(frontendId, 42).
    await waitFor(() => {
      expect(patchSpy).toHaveBeenCalledWith(frontendId, 42);
    });

    // Drawer body should now render (backend session resolved to 42).
    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /reference sources/i })).toBeInTheDocument();
    });

    patchSpy.mockRestore();
  });

  it("loads and displays references on open", async () => {
    // Pre-assign backend_session_id so no POST /sessions is needed.
    const frontendId = seedSession(42);

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    const triggerBtn = await screen.findByRole("button", {
      name: /references/i,
    });
    await userEvent.click(triggerBtn);

    // Panel opens and shows the reference title
    await waitFor(() => {
      expect(
        screen.getByText("Attention Is All You Need"),
      ).toBeInTheDocument();
    });
  });

  it("removes reference from local store when trash is clicked", async () => {
    const frontendId = seedSession(42);
    // Pre-populate the store so the drawer renders refs immediately
    useChatStore.getState().setReferences(42, sampleRefs);

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    const triggerBtn = screen.getByRole("button", { name: /references/i });
    await userEvent.click(triggerBtn);

    const trashBtn = await screen.findByRole("button", {
      name: /remove attention is all you need/i,
    });
    await userEvent.click(trashBtn);

    await waitFor(() => {
      expect(
        useChatStore.getState().referencesBySession[42],
      ).toHaveLength(0);
    });
  });

  it("toggle_switch_fires_patch_and_updates_optimistically", async () => {
    const frontendId = seedSession(42);
    // Pre-populate the store with one enabled ref
    useChatStore.getState().setReferences(42, sampleRefs);

    // Use a deferred promise so we can check the UI before the network resolves
    let resolvePatch!: () => void;
    const patchPromise = new Promise<void>((res) => { resolvePatch = res; });

    server.use(
      http.patch(`${API_BASE_URL}/papers/1`, async () => {
        await patchPromise;
        return HttpResponse.json({ enabled: false });
      }),
    );

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    const triggerBtn = screen.getByRole("button", { name: /references/i });
    await userEvent.click(triggerBtn);

    // Find the toggle switch (should be checked = enabled)
    const toggle = await screen.findByRole("switch", {
      name: /toggle attention is all you need/i,
    });
    expect(toggle).toBeChecked();

    // Click the toggle — optimistic update should flip it immediately
    await userEvent.click(toggle);

    // The UI should reflect the new state before the PATCH resolves
    expect(toggle).not.toBeChecked();
    expect(useChatStore.getState().referencesBySession[42]?.[0]?.enabled).toBe(false);

    // Resolve the network call
    resolvePatch();
    await waitFor(() => {
      // Toggle remains off after successful PATCH
      expect(toggle).not.toBeChecked();
    });
  });

  it("toggle_switch_reverts_on_error", async () => {
    const frontendId = seedSession(42);
    // Pre-populate the store with one enabled ref
    useChatStore.getState().setReferences(42, sampleRefs);

    server.use(
      http.patch(`${API_BASE_URL}/papers/1`, () =>
        HttpResponse.json({ detail: "server error" }, { status: 500 }),
      ),
    );

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    const triggerBtn = screen.getByRole("button", { name: /references/i });
    await userEvent.click(triggerBtn);

    const toggle = await screen.findByRole("switch", {
      name: /toggle attention is all you need/i,
    });
    expect(toggle).toBeChecked();

    // Click the toggle — MSW 500 resolves quickly, so by the time userEvent.click
    // settles the optimistic flip and revert have both completed.
    await userEvent.click(toggle);

    // After the PATCH rejects, the store reverts to enabled=true
    await waitFor(() => {
      expect(useChatStore.getState().referencesBySession[42]?.[0]?.enabled).toBe(true);
    });

    // The UI also reverts back to checked
    expect(toggle).toBeChecked();
  });

  it("closes on Escape key", async () => {
    const frontendId = seedSession(42);
    useChatStore.getState().setReferences(42, sampleRefs);

    render(<ReferenceSourcesDrawer frontendSessionId={frontendId} />);

    // Open the drawer
    const triggerBtn = screen.getByRole("button", { name: /references/i });
    await userEvent.click(triggerBtn);

    // Drawer panel should be visible
    expect(screen.getByRole("dialog", { name: /reference sources/i })).toBeInTheDocument();

    // Press Escape
    fireEvent.keyDown(window, { key: "Escape" });

    // Drawer should close
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /reference sources/i })).toBeNull();
    });
  });
});
