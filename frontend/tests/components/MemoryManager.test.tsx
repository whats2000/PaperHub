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
// Additional fixture for "add memory" tests
// ---------------------------------------------------------------------------

const createdGlobalMemory: MemoryItem = {
  id: 99,
  scope: "global",
  session_id: null,
  content: "Always respond in English.",
  created_at: "2026-05-22T10:00:00Z",
  updated_at: "2026-05-22T10:00:00Z",
  status: "active",
  supersedes: null,
  superseded_by: null,
};

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

    // Wait for memory content to confirm the data has loaded.
    await screen.findByText("User prefers concise answers.");

    // Section headings (FP#2 — exact strings). The scope-toggle buttons in
    // AddMemoryComposer also render "Project (session)" / "User (global)", so
    // assert there is at least one h3 element with that label text.
    const projectHeadings = screen.getAllByText("Project (session)");
    expect(projectHeadings.some((el) => el.tagName === "H3")).toBe(true);
    const globalHeadings = screen.getAllByText("User (global)");
    expect(globalHeadings.some((el) => el.tagName === "H3")).toBe(true);

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
    let capturedSessionHeader: string | null = null;

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
        capturedSessionHeader = request.headers.get("x-paperhub-session-id");
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
    // Ownership header must be present (mirrors the DELETE test assertion)
    expect(capturedSessionHeader).toBe("7");
  });

  it("edit button opens textarea, save sends PATCH with updated content", async () => {
    let capturedBody: Record<string, unknown> | undefined;

    const updatedMemory: MemoryItem = {
      ...sessionMemory,
      content: "User prefers bullet-point answers.",
    };

    server.use(
      http.get(`${API_BASE_URL}/memories`, () =>
        HttpResponse.json([sessionMemory]),
      ),
      http.patch(`${API_BASE_URL}/memories/1`, async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(updatedMemory);
      }),
    );

    render(<MemoryManager sessionId={7} />);

    await screen.findByText("User prefers concise answers.");

    // Enter edit mode
    const editBtn = screen.getByRole("button", { name: "edit" });
    await userEvent.click(editBtn);

    // Textarea should appear seeded with the current content
    const textarea = screen.getByRole("textbox", {
      name: "Edit memory content",
    });
    expect(textarea).toHaveValue("User prefers concise answers.");

    // Change the value
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "User prefers bullet-point answers.");

    // Save
    const saveBtn = screen.getByRole("button", { name: "save" });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(capturedBody).toBeDefined();
    });

    // PATCH body must carry the edited text
    expect(capturedBody).toEqual({
      content: "User prefers bullet-point answers.",
    });

    // Row should now show the updated content (server response applied)
    await screen.findByText("User prefers bullet-point answers.");
  });

  it("renders empty-state when GET /memories returns []", async () => {
    server.use(
      http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
    );

    render(<MemoryManager sessionId={7} />);

    expect(await screen.findByText(/no memories/i)).toBeInTheDocument();

    // Section headings (h3) must NOT appear when empty. The scope-toggle
    // buttons in AddMemoryComposer still render the same text as plain
    // <button> elements, so we check no h3 carries those labels.
    const h3s = document.querySelectorAll("h3");
    const h3Texts = Array.from(h3s).map((el) => el.textContent ?? "");
    expect(h3Texts).not.toContain("Project (session)");
    expect(h3Texts).not.toContain("User (global)");
  });

  it("add-memory: POST /memories with body + session header; new item appears in list", async () => {
    let capturedBody: Record<string, unknown> | undefined;
    let capturedSessionHeader: string | null = null;

    server.use(
      http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/memories`, async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        capturedSessionHeader = request.headers.get("x-paperhub-session-id");
        return HttpResponse.json(createdGlobalMemory, { status: 201 });
      }),
    );

    render(<MemoryManager sessionId={7} />);

    // Wait for component to settle (empty-state)
    expect(await screen.findByText(/no memories/i)).toBeInTheDocument();

    // Type content in the add-memory textarea
    const textarea = screen.getByRole("textbox", { name: /new memory content/i });
    await userEvent.type(textarea, "Always respond in English.");

    // Switch scope to "User (global)"
    await userEvent.click(screen.getByRole("button", { name: /user \(global\)/i }));

    // Click Add
    await userEvent.click(screen.getByRole("button", { name: /add memory/i }));

    await waitFor(() => {
      expect(capturedBody).toBeDefined();
    });

    // Assert POST body and ownership header
    expect(capturedBody).toEqual({
      content: "Always respond in English.",
      scope: "global",
    });
    expect(capturedSessionHeader).toBe("7");

    // New item must appear in the list
    expect(
      await screen.findByText("Always respond in English."),
    ).toBeInTheDocument();

    // Textarea is cleared after success
    expect(textarea).toHaveValue("");
  });

  it("no backend session: lists global-only (no session_id query), disables Project scope, shows hint", async () => {
    let capturedUrl: string | undefined;

    server.use(
      http.get(`${API_BASE_URL}/memories`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json([supersededMemory]);
      }),
    );

    render(<MemoryManager sessionId={null} />);

    await screen.findByText("User prefers verbose answers.");

    // GET must omit the session_id query param (global-only listing).
    expect(capturedUrl).toBeDefined();
    expect(new URL(capturedUrl!).searchParams.has("session_id")).toBe(false);

    // The Project (session) scope toggle is disabled until a message is sent.
    const projectToggle = screen
      .getAllByRole("button", { name: /project \(session\)/i })
      .find((el) => (el as HTMLButtonElement).disabled);
    expect(projectToggle).toBeDefined();

    // Explanatory hint is visible.
    expect(
      screen.getByText(/send at least one message to enable project/i),
    ).toBeInTheDocument();
  });

  it("no backend session: add global memory POSTs without an ownership header", async () => {
    let capturedSessionHeader: string | null = "unset";

    server.use(
      http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/memories`, ({ request }) => {
        capturedSessionHeader = request.headers.get("x-paperhub-session-id");
        return HttpResponse.json(createdGlobalMemory, { status: 201 });
      }),
    );

    render(<MemoryManager sessionId={null} />);

    await screen.findByText(/no memories/i);

    const textarea = screen.getByRole("textbox", { name: /new memory content/i });
    await userEvent.type(textarea, "Always respond in English.");
    // Scope is forced to global; click Add directly.
    await userEvent.click(screen.getByRole("button", { name: /add memory/i }));

    expect(
      await screen.findByText("Always respond in English."),
    ).toBeInTheDocument();

    // No ownership header sent (backend treats absence as global-only access).
    expect(capturedSessionHeader).toBeNull();
  });

  it("add-memory: 422 gate refusal shows inline error, no item added", async () => {
    server.use(
      http.get(`${API_BASE_URL}/memories`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/memories`, () =>
        HttpResponse.json(
          { detail: "sensitive personal information detected" },
          { status: 422 },
        ),
      ),
    );

    render(<MemoryManager sessionId={7} />);

    await screen.findByText(/no memories/i);

    const textarea = screen.getByRole("textbox", { name: /new memory content/i });
    await userEvent.type(textarea, "my secret password is abc123");

    await userEvent.click(screen.getByRole("button", { name: /add memory/i }));

    // Inline error must appear
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).toMatch(/couldn't save/i);

    // The textarea content is preserved (not cleared on error)
    expect(textarea).toHaveValue("my secret password is abc123");

    // The item must NOT appear as a memory row (<p> element in the list).
    // (The text still lives in the textarea itself, so we cannot use
    //  queryByText directly — instead assert no <p> carries it.)
    const paragraphs = document.querySelectorAll("p");
    const pTexts = Array.from(paragraphs).map((el) => el.textContent ?? "");
    expect(pTexts).not.toContain("my secret password is abc123");
  });
});
