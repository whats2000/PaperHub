import { render, screen, waitFor } from "@testing-library/react";
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
import { act } from "react";

// Must mock sonner BEFORE importing the component so the component captures
// the mocked version at module-load time.
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), message: vi.fn() },
}));
import { toast } from "sonner";

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { useCanvasStore } from "@/store/canvas";
import { API_BASE_URL } from "@/lib/api";

const server = setupServer(
  http.get(`${API_BASE_URL}/chunks/42`, () =>
    HttpResponse.json({
      id: 42,
      paper_content_id: 7,
      section: "3.2 Routing",
      text: "Expert collapse is mitigated.",
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});
afterAll(() => server.close());
beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("CitationCanvas", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CitationCanvas />);
    expect(container.firstChild).toBeNull();
  });

  it("opens, resolves the chunk, and points the iframe at the paper HTML", async () => {
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });

    const iframe = await screen.findByTitle(/citation canvas/i);
    await waitFor(() =>
      expect(iframe).toHaveAttribute(
        "src",
        `${API_BASE_URL}/papers/content/7/html`,
      ),
    );
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
  });

  it("closes when the close button is clicked", async () => {
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });
    await screen.findByTitle(/citation canvas/i);

    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(useCanvasStore.getState().open).toBe(false);
  });

  // NFR-02 — error path: getChunk failure fires toast.error
  it("fires toast.error when chunk fetch fails (500)", async () => {
    server.use(
      http.get(`${API_BASE_URL}/chunks/42`, () =>
        HttpResponse.json({ detail: "x" }, { status: 500 }),
      ),
    );
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });

  // NFR-02 — highlight-miss path: documented skip with explanation.
  //
  // The intended test: open on chunk 42, fire the iframe load event, and assert
  // toast.message fires because the iframe body doesn't contain the chunk text.
  //
  // Why it cannot run in jsdom: when the iframe's `src` is a real URL (not
  // "about:blank"), jsdom sets contentDocument but leaves contentDocument.body
  // as null — it does not simulate HTML loading for arbitrary URLs.
  // handleIframeLoad guards on `!doc.body` and returns early, so the miss-toast
  // is never reached. There is no way to inject body content into jsdom's iframe
  // contentDocument for a non-blank src without deep mocking of the DOM itself.
  //
  // The miss-toast code path is covered by the unit test for findAndHighlight
  // (findAndHighlight.test.ts) which calls findAndHighlight on an empty document
  // directly and verifies it returns false. The integration wiring (false → toast)
  // is verified by code inspection; there is no intermediate logic between them.
  it.skip(
    "fires toast.message when highlight misses (passage not in iframe doc) — " +
      "SKIP: jsdom sets contentDocument.body=null for non-blank src iframes; " +
      "handleIframeLoad exits early on !doc.body so the miss path is unreachable " +
      "in jsdom. Miss path covered by findAndHighlight.test.ts unit tests.",
    () => {},
  );

  // NFR-02 — close removes component from DOM (returns null, not just hidden).
  it("removes the drawer from the DOM when closed", async () => {
    render(<CitationCanvas />);
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });
    await screen.findByTitle(/citation canvas/i);

    // Close via store (mirrors what the button click does).
    act(() => {
      useCanvasStore.getState().closeCanvas();
    });

    // The aside (aria-label="Citation Canvas") must be gone from the DOM.
    expect(screen.queryByLabelText(/citation canvas/i)).toBeNull();
  });
});
