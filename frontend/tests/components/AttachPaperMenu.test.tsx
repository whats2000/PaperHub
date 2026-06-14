import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";

// Hoisted mock for sonner — toast.success/error/info are no-ops we can spy on.
const { toastSuccess, toastError, toastInfo, toastLoading } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  toastLoading: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
    loading: toastLoading,
  },
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  server.resetHandlers();
  toastSuccess.mockReset();
  toastError.mockReset();
  toastInfo.mockReset();
  toastLoading.mockReset();
  // toast.loading returns the toast id we later resolve by; give it a stable
  // value so handlers thread `{ id }` into the success/error/info update.
  toastLoading.mockReturnValue("toast-id");
  // Seed the store with an active session (id=1, backend_session_id=7).
  useChatStore.setState({
    sessions: [
      {
        id: 1,
        title: "Test session",
        messages: [],
        backend_session_id: 7,
      },
    ],
    activeSessionId: 1,
    _nextId: 2,
    referencesBySession: {},
    composerDraft: "",
  });
});

function makePdfFile(name = "paper.pdf"): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, {
    type: "application/pdf",
  });
}

async function openMenu() {
  const trigger = screen.getByRole("button", { name: /attach paper/i });
  await userEvent.click(trigger);
}

describe("AttachPaperMenu", () => {
  it("uploads a PDF, toasts Added, and appends a ReferenceItem to the store", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.json(
          {
            papers_id: 42,
            paper_content_id: 100,
            cache_hit: false,
            title: "My Uploaded Paper",
          },
          { status: 201 },
        ),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();

    // Default tab should be Upload PDF. Find the file input by its aria-label.
    const fileInput = screen.getByLabelText(/pdf file/i);
    await userEvent.upload(fileInput, makePdfFile());

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Added",
        expect.objectContaining({ description: "My Uploaded Paper" }),
      );
    });

    const refs = useChatStore.getState().referencesBySession[7] ?? [];
    expect(refs).toHaveLength(1);
    expect(refs[0]).toMatchObject({
      papers_id: 42,
      paper_content_id: 100,
      enabled: true,
      kind: "pdf_upload",
      title: "My Uploaded Paper",
    });
  });

  it("toasts 'Re-attached' when the backend returns cache_hit=true", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.json({
          papers_id: 50,
          paper_content_id: 200,
          cache_hit: true,
          title: "Cached Paper",
        }),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    const fileInput = screen.getByLabelText(/pdf file/i);
    await userEvent.upload(fileInput, makePdfFile());

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Re-attached",
        expect.objectContaining({ description: "Cached Paper" }),
      );
    });
  });

  it("toasts an error with the 413 body when the file exceeds the size ceiling", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.text("file exceeds 30 MiB ceiling", { status: 413 }),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    const fileInput = screen.getByLabelText(/pdf file/i);
    await userEvent.upload(fileInput, makePdfFile());

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const lastCall = toastError.mock.calls.at(-1);
    expect(lastCall?.[0]).toBe("Upload failed");
    const opts = lastCall?.[1] as { description?: string } | undefined;
    expect(opts?.description).toContain("30 MiB");
  });

  it("toasts an error on 415 wrong MIME", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.text("unsupported media type", { status: 415 }),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    const fileInput = screen.getByLabelText(/pdf file/i);
    await userEvent.upload(fileInput, makePdfFile("not-a-pdf.txt"));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        "Upload failed",
        expect.anything(),
      );
    });
  });

  it("imports a valid arXiv ID — calls /papers with the canonical paper_id", async () => {
    const receivedBodies: unknown[] = [];
    server.use(
      http.post(`${API_BASE_URL}/papers`, async ({ request }) => {
        receivedBodies.push(await request.json());
        return HttpResponse.json({
          papers_id: 77,
          paper_content_id: 88,
          cache_hit: false,
          title: "Mistral 7B",
        });
      }),
    );

    render(<AttachPaperMenu />);
    await openMenu();

    // Switch to the arXiv tab.
    await userEvent.click(screen.getByRole("tab", { name: /paste arxiv id/i }));

    const arxivInput = screen.getByLabelText(/arxiv identifier/i);
    await userEvent.type(arxivInput, "2310.06825v3");
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Added",
        expect.objectContaining({ description: "Mistral 7B" }),
      );
    });

    expect(receivedBodies).toHaveLength(1);
    expect(receivedBodies[0]).toMatchObject({
      session_id: 7,
      paper_id: "arxiv:2310.06825",
    });

    const refs = useChatStore.getState().referencesBySession[7] ?? [];
    expect(refs[0]).toMatchObject({
      papers_id: 77,
      kind: "arxiv",
      arxiv_id: "2310.06825",
    });
  });

  it("rejects invalid arXiv input inline without calling the API", async () => {
    const handlerSpy = vi.fn();
    server.use(
      http.post(`${API_BASE_URL}/papers`, () => {
        handlerSpy();
        return HttpResponse.json({});
      }),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.click(screen.getByRole("tab", { name: /paste arxiv id/i }));

    const arxivInput = screen.getByLabelText(/arxiv identifier/i);
    await userEvent.type(arxivInput, "foo");
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));

    expect(
      await screen.findByText(/not a valid arxiv identifier or url/i),
    ).toBeInTheDocument();
    expect(handlerSpy).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("discards the result and shows an info toast when the session changes mid-upload", async () => {
    // Seed a second session up front so we can swap to it before the response.
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "Session A",
          messages: [],
          backend_session_id: 7,
        },
        {
          id: 2,
          title: "Session B",
          messages: [],
          backend_session_id: 9,
        },
      ],
      activeSessionId: 1,
      _nextId: 3,
      referencesBySession: {},
      composerDraft: "",
    });

    // Hold the response open until we say so.
    let releaseResponse!: () => void;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, async () => {
        await responseGate;
        return HttpResponse.json({
          papers_id: 42,
          paper_content_id: 100,
          cache_hit: false,
          title: "Late Paper",
        });
      }),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    const fileInput = screen.getByLabelText(/pdf file/i);
    await userEvent.upload(fileInput, makePdfFile());

    // While the upload is in flight, swap the active session.
    useChatStore.setState({ activeSessionId: 2 });

    // Now release the response and wait for the info toast.
    releaseResponse();

    await waitFor(() => {
      expect(toastInfo).toHaveBeenCalledWith(
        "Session changed; the attached paper was discarded.",
        expect.objectContaining({ id: "toast-id" }),
      );
    });

    // Success toast must NOT have fired, and neither session's bucket must
    // have been mutated.
    expect(toastSuccess).not.toHaveBeenCalled();
    const refsBySession = useChatStore.getState().referencesBySession;
    expect(refsBySession[7] ?? []).toHaveLength(0);
    expect(refsBySession[9] ?? []).toHaveLength(0);
  });

  it("renders an inline error and toasts when the arXiv backend call fails", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers`, () =>
        HttpResponse.text("paper not found upstream", { status: 502 }),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.click(screen.getByRole("tab", { name: /paste arxiv id/i }));
    const arxivInput = screen.getByLabelText(/arxiv identifier/i);
    await userEvent.type(arxivInput, "2310.06825");
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        "Import failed",
        expect.anything(),
      );
    });
    // The inline alert should also show the backend's message, stripped of
    // any "API <code>: " prefix.
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/paper not found upstream/i);
    expect(alert.textContent).not.toMatch(/^API \d+:/);
  });

  // A brand-new session has no backend row until the first chat turn. Attaching
  // a paper must still work — the menu lazy-creates the backend session, exactly
  // like the references panel's "Add from library" affordance does.
  it("lazy-creates the backend session, then uploads, when none exists yet", async () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Draft", messages: [], backend_session_id: null },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
      composerDraft: "",
    });

    let createdSessions = 0;
    server.use(
      http.post(`${API_BASE_URL}/sessions`, () => {
        createdSessions += 1;
        return HttpResponse.json({ session_id: 55 }, { status: 201 });
      }),
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.json(
          {
            papers_id: 42,
            paper_content_id: 100,
            cache_hit: false,
            title: "Fresh Paper",
          },
          { status: 201 },
        ),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();

    // The control is usable even though there's no backend session yet.
    const fileInput = screen.getByLabelText(/pdf file/i);
    expect(fileInput).not.toBeDisabled();
    await userEvent.upload(fileInput, makePdfFile());

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Added",
        expect.objectContaining({ description: "Fresh Paper" }),
      );
    });

    // Exactly one session was created, the draft now carries its id, and the
    // reference landed under that newly-minted backend id.
    expect(createdSessions).toBe(1);
    const state = useChatStore.getState();
    expect(state.sessions[0]?.backend_session_id).toBe(55);
    expect(state.referencesBySession[55] ?? []).toHaveLength(1);
  });

  it("lazy-creates the backend session, then imports an arXiv ID, when none exists yet", async () => {
    useChatStore.setState({
      sessions: [
        { id: 1, title: "Draft", messages: [], backend_session_id: null },
      ],
      activeSessionId: 1,
      _nextId: 2,
      referencesBySession: {},
      composerDraft: "",
    });

    let createdSessions = 0;
    const receivedBodies: unknown[] = [];
    server.use(
      http.post(`${API_BASE_URL}/sessions`, () => {
        createdSessions += 1;
        return HttpResponse.json({ session_id: 56 }, { status: 201 });
      }),
      http.post(`${API_BASE_URL}/papers`, async ({ request }) => {
        receivedBodies.push(await request.json());
        return HttpResponse.json({
          papers_id: 77,
          paper_content_id: 88,
          cache_hit: false,
          title: "Mistral 7B",
        });
      }),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.click(screen.getByRole("tab", { name: /paste arxiv id/i }));

    const arxivInput = screen.getByLabelText(/arxiv identifier/i);
    expect(arxivInput).not.toBeDisabled();
    await userEvent.type(arxivInput, "2310.06825v3");
    await userEvent.click(screen.getByRole("button", { name: /^import$/i }));

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Added",
        expect.objectContaining({ description: "Mistral 7B" }),
      );
    });

    expect(createdSessions).toBe(1);
    expect(receivedBodies[0]).toMatchObject({
      session_id: 56,
      paper_id: "arxiv:2310.06825",
    });
    expect(useChatStore.getState().referencesBySession[56] ?? []).toHaveLength(1);
  });

  it("disables inputs and shows a hint only when there is no active session at all", async () => {
    useChatStore.setState({ sessions: [], activeSessionId: null });

    render(<AttachPaperMenu />);
    await openMenu();

    expect(
      screen.getByText(/start a chat to attach papers/i),
    ).toBeInTheDocument();

    const fileInput = screen.getByLabelText(/pdf file/i);
    expect(fileInput).toBeDisabled();

    await userEvent.click(screen.getByRole("tab", { name: /paste arxiv id/i }));
    const arxivInput = screen.getByLabelText(/arxiv identifier/i);
    expect(arxivInput).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /^import$/i }),
    ).toBeDisabled();
  });

  // Ingestion can run for a while (esp. PDFs), so the user must get progress
  // feedback: a persistent loading toast that resolves in place, plus an
  // in-popover spinner while the request is in flight.
  it("opens a loading toast and resolves it to success by the same id", async () => {
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, () =>
        HttpResponse.json(
          {
            papers_id: 1,
            paper_content_id: 2,
            cache_hit: false,
            title: "Slow Paper",
          },
          { status: 201 },
        ),
      ),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.upload(screen.getByLabelText(/pdf file/i), makePdfFile());

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        "Added",
        expect.objectContaining({ id: "toast-id", description: "Slow Paper" }),
      );
    });
    // The loading toast was opened exactly once and then updated (not stacked).
    expect(toastLoading).toHaveBeenCalledTimes(1);
  });

  it("shows an in-popover processing spinner while the attach is in flight", async () => {
    let releaseResponse!: () => void;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    server.use(
      http.post(`${API_BASE_URL}/papers/upload`, async () => {
        await responseGate;
        return HttpResponse.json({
          papers_id: 1,
          paper_content_id: 2,
          cache_hit: false,
          title: "X",
        });
      }),
    );

    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.upload(screen.getByLabelText(/pdf file/i), makePdfFile());

    // In-flight: the contextual processing row is visible.
    expect(await screen.findByText(/^processing…$/i)).toBeInTheDocument();

    releaseResponse();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });
});
