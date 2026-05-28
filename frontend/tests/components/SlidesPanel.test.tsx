import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";
import { SlidesPanel } from "@/components/slides/SlidesPanel";
import * as api from "@/lib/api";

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: { workerSrc: "" } },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children: React.ReactNode;
    onLoadSuccess?: (pdf: { numPages: number }) => void;
  }) => {
    onLoadSuccess?.({ numPages: 5 });
    return <div data-testid="doc">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid={`page-${pageNumber}`}>page {pageNumber}</div>
  ),
}));
vi.mock("@/lib/api", () => ({
  fetchDeckPdfData: vi.fn(() => Promise.resolve(new Uint8Array([1, 2, 3]))),
  deckTexUrl: () => "http://x/tex",
  deckPdfUrl: () => "http://x/pdf",
}));

describe("SlidesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSlidesStore.setState({
      open: true,
      deckBySession: {
        7: {
          deck_id: 1,
          session_id: 7,
          page_count: 5,
          title: "MoE",
          status: "ok",
          contributing_papers: [],
          has_notes: true,
        },
      },
      deckRevisionBySession: { 7: 1 },
      currentPageBySession: { 7: 1 },
    });
  });

  it("renders the current slide and speaker note, and navigates", async () => {
    render(
      <SlidesPanel
        sessionId={7}
        speakerNotes={{ "1": "First note", "2": "Second note" }}
      />,
    );
    expect(await screen.findByText("First note")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /next slide/i }));
    expect(useSlidesStore.getState().currentPageBySession[7]).toBe(2);
  });

  it("renders a draggable divider to resize the filmstrip rail", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    const divider = await screen.findByRole("separator", {
      name: /resize filmstrip/i,
    });
    expect(divider).toHaveAttribute("aria-orientation", "vertical");
  });

  it("masks the canvas and does NOT refetch while a turn is in flight", () => {
    render(
      <SlidesPanel
        sessionId={7}
        speakerNotes={{}}
        busy
        stage="Compiling the deck (LaTeX)…"
      />,
    );
    expect(screen.getByText(/updating slides/i)).toBeInTheDocument();
    expect(screen.getByText(/compiling the deck/i)).toBeInTheDocument();
    // The recompiled PDF isn't ready mid-edit — must not fetch.
    expect(api.fetchDeckPdfData).not.toHaveBeenCalled();
  });

  it("lets the user edit a speaker note and saves it", async () => {
    const onSaveNote = vi.fn(() => Promise.resolve());
    render(
      <SlidesPanel
        sessionId={7}
        speakerNotes={{ "1": "Original note" }}
        onSaveNote={onSaveNote}
      />,
    );
    await screen.findByText("Original note");
    await userEvent.click(screen.getByRole("button", { name: /edit speaker note/i }));
    const box = screen.getByRole("textbox", { name: /speaker note/i });
    expect(box).toHaveValue("Original note");
    await userEvent.clear(box);
    await userEvent.type(box, "My revised note");
    await userEvent.click(screen.getByRole("button", { name: /save speaker note/i }));
    expect(onSaveNote).toHaveBeenCalledWith(1, "My revised note");
  });

  it("offers no note Edit affordance while a deck edit is in flight", () => {
    render(
      <SlidesPanel
        sessionId={7}
        speakerNotes={{ "1": "Original note" }}
        onSaveNote={vi.fn()}
        busy
      />,
    );
    expect(
      screen.queryByRole("button", { name: /edit speaker note/i }),
    ).not.toBeInTheDocument();
  });

  it("does not offer to edit a '(continued)' continuation page", () => {
    useSlidesStore.setState({ currentPageBySession: { 7: 3 } });
    render(
      <SlidesPanel
        sessionId={7}
        speakerNotes={{ "2": "Real note", "3": "(continued)" }}
        onSaveNote={vi.fn()}
      />,
    );
    expect(screen.getByText("(continued)")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /edit speaker note/i }),
    ).not.toBeInTheDocument();
  });

  it("mounts every page once in the main viewer and toggles visibility on swap", async () => {
    // Live-preview snappiness contract: clicking next must NOT re-rasterise.
    // We mount one wrapper per page (data-page="N") and hide the inactive
    // ones with `hidden`. Switching pages becomes a CSS toggle, not a fresh
    // pdf.js render — that's the perf fix for the laggy page-swap.
    const { container } = render(
      <SlidesPanel sessionId={7} speakerNotes={{}} />,
    );

    // All 5 pages must be present in the DOM from the first render.
    await waitFor(() => {
      expect(container.querySelectorAll("[data-page]")).toHaveLength(5);
    });

    const wrap = (n: number) =>
      container.querySelector<HTMLElement>(`[data-page="${n}"]`);

    // Only the active page is visible.
    expect(wrap(1)?.hidden).toBe(false);
    expect(wrap(2)?.hidden).toBe(true);
    expect(wrap(5)?.hidden).toBe(true);

    // Swap → the previously active page MUST remain mounted (proving the
    // rendered canvas survives the swap and isn't a re-render every click).
    await userEvent.click(screen.getByRole("button", { name: /next slide/i }));
    expect(wrap(1)).not.toBeNull();
    expect(wrap(1)?.hidden).toBe(true);
    expect(wrap(2)?.hidden).toBe(false);
  });

  it("reloads the deck (cache-busted by revision) when the edit completes", async () => {
    useSlidesStore.setState({ deckRevisionBySession: { 7: 4 } });
    const { rerender } = render(
      <SlidesPanel sessionId={7} speakerNotes={{}} busy />,
    );
    expect(api.fetchDeckPdfData).not.toHaveBeenCalled();

    // Edit completes: busy clears → fetch the freshly compiled PDF for rev 4.
    rerender(<SlidesPanel sessionId={7} speakerNotes={{}} busy={false} />);
    await waitFor(() =>
      expect(api.fetchDeckPdfData).toHaveBeenCalledWith(7, 4),
    );
    await waitFor(() =>
      expect(screen.queryByText(/updating slides/i)).not.toBeInTheDocument(),
    );
  });
});
