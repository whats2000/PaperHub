import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { MemoryManager } from "@/components/chat/MemoryManager";
import { useMemoriesStore } from "@/store/memories";
import { API_BASE_URL } from "@/lib/api";
import type { MemoryItem } from "@/types/domain";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const sessionMemory: MemoryItem = {
  id: 1,
  scope: "session",
  session_id: 7,
  content: "User prefers concise answers.",
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  status: "active",
  supersedes: null,
  superseded_by: null,
};

const supersededMemory: MemoryItem = {
  id: 2,
  scope: "global",
  session_id: null,
  content: "User prefers verbose answers.",
  created_at: "2026-05-21T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
  status: "superseded",
  supersedes: null,
  superseded_by: 1,
};

// ---------------------------------------------------------------------------
// MSW server
// ---------------------------------------------------------------------------

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  server.resetHandlers();
  // Reset the Zustand store between tests — it's a module singleton and would
  // otherwise leak memoriesBySession across test cases.
  useMemoriesStore.setState({ memoriesBySession: {} });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("MemoryManager", () => {
  it("renders 'User (global)' + 'Project (session)' group labels + 'active' + 'superseded' badges", async () => {
    server.use(
      http.get(`${API_BASE_URL}/memories`, () =>
        HttpResponse.json([sessionMemory, supersededMemory]),
      ),
    );

    render(<MemoryManager sessionId={7} />);

    // Section headings (FP#2 — exact strings)
    expect(await screen.findByText("Project (session)")).toBeInTheDocument();
    expect(screen.getByText("User (global)")).toBeInTheDocument();

    // Status badges
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("superseded")).toBeInTheDocument();

    // Content is rendered
    expect(
      screen.getByText("User prefers concise answers."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("User prefers verbose answers."),
    ).toBeInTheDocument();
  });

  it("delete button triggers DELETE /memories/:id", async () => {
    let deleteCalled = false;

    server.use(
      http.get(`${API_BASE_URL}/memories`, () =>
        HttpResponse.json([sessionMemory]),
      ),
      http.delete(`${API_BASE_URL}/memories/1`, ({ request }) => {
        // Ownership header must be present
        expect(request.headers.get("x-paperhub-session-id")).toBe("7");
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    render(<MemoryManager sessionId={7} />);

    // Wait for the memory row to appear
    await screen.findByText("User prefers concise answers.");

    const deleteBtn = screen.getByRole("button", { name: "delete" });
    await userEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });

    // Row is removed from the UI
    expect(
      screen.queryByText("User prefers concise answers."),
    ).not.toBeInTheDocument();
  });

  it("'deactivate' toggle button sends PATCH with {status:'superseded'}", async () => {
    let capturedBody: Record<string, unknown> | undefined;

    const patchedMemory: MemoryItem = {
      ...sessionMemory,
      status: "superseded",
      superseded_by: null,
    };

    server.use(
      http.get(`${API_BASE_URL}/memories`, () =>
        HttpResponse.json([sessionMemory]),
      ),
      http.patch(`${API_BASE_URL}/memories/1`, async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(patchedMemory);
      }),
    );

    render(<MemoryManager sessionId={7} />);

    await screen.findByText("User prefers concise answers.");

    // Active memory shows a "deactivate" toggle
    const deactivateBtn = screen.getByRole("button", { name: "deactivate" });
    await userEvent.click(deactivateBtn);

    await waitFor(() => {
      expect(capturedBody).toBeDefined();
    });

    expect(capturedBody).toEqual({ status: "superseded" });
  });

  it("renders empty-state when GET /memories returns []", async () => {
    server.use(
      http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
    );

    render(<MemoryManager sessionId={7} />);

    expect(await screen.findByText(/no memories/i)).toBeInTheDocument();

    // Section headings must NOT appear when empty
    expect(screen.queryByText("Project (session)")).not.toBeInTheDocument();
    expect(screen.queryByText("User (global)")).not.toBeInTheDocument();
  });
});
