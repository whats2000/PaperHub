import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { API_BASE_URL } from "@/lib/api";
import { useChatStore } from "@/store/chat";

// Hoisted mock for sonner — toast.success/error/info are no-ops we can spy on.
const { toastSuccess, toastError, toastInfo } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: toastInfo },
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  server.resetHandlers();
  toastSuccess.mockReset();
  toastError.mockReset();
  toastInfo.mockReset();
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

  it("disables both inputs and shows a hint when backend_session_id is null", async () => {
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "No backend yet",
          messages: [],
          backend_session_id: null,
        },
      ],
      activeSessionId: 1,
    });

    render(<AttachPaperMenu />);
    await openMenu();

    expect(
      screen.getByText(/send a message first to create a session/i),
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
});
