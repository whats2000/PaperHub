import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";
import { SlidesPanel } from "@/components/slides/SlidesPanel";
import * as api from "@/lib/api";
import type { DeckSlideDetail } from "@/types/domain";

// Per-page detail seeded into the store + returned by getDeckSlides (session 7).
const SLIDES7: DeckSlideDetail[] = [
  {
    slide_index: 0,
    page_start: 1,
    page_end: 1,
    frame_tex: "\\begin{frame}{One}a\\end{frame}",
    source_sections: [
      { paper_id: 7, section_name: "Introduction", chunk_ids: [101] },
    ],
  },
  {
    slide_index: 1,
    page_start: 2,
    page_end: 2,
    frame_tex: "\\begin{frame}{Two}b\\end{frame}",
    source_sections: [],
  },
];

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
  Page: ({
    pageNumber,
    onRenderSuccess,
  }: {
    pageNumber: number;
    onRenderSuccess?: () => void;
  }) => {
    // Mirror react-pdf's contract: onRenderSuccess fires once the canvas has
    // rasterized. SlidesPanel uses this to drop its "Updating slides…" mask,
    // so the mock has to invoke it (synchronously is fine for the test).
    onRenderSuccess?.();
    return <div data-testid={`page-${pageNumber}`}>page {pageNumber}</div>;
  },
}));
// CodeMirror → textarea (DOM-layout-free) for the manual editor.
vi.mock("@uiw/react-codemirror", () => ({
  default: ({
    value,
    onChange,
  }: {
    value: string;
    onChange?: (v: string) => void;
  }) => (
    <textarea
      aria-label="latex-source"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));
vi.mock("@/lib/api", () => ({
  fetchDeckPdfData: vi.fn(() => Promise.resolve(new Uint8Array([1, 2, 3]))),
  deckTexUrl: () => "http://x/tex",
  deckPdfUrl: () => "http://x/pdf",
  getDeckSlides: vi.fn(() => Promise.resolve([])),
  getDeckTexText: vi.fn(() => Promise.resolve("\\documentclass{beamer}...")),
  putFrameTex: vi.fn(() => Promise.resolve({ ok: true, status: "ok", page_count: 5 })),
  putDeckTex: vi.fn(() => Promise.resolve({ ok: true, status: "ok", page_count: 5 })),
}));

describe("SlidesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDeckSlides).mockResolvedValue(SLIDES7);
    vi.mocked(api.getDeckTexText).mockResolvedValue("\\documentclass{beamer}...");
    vi.mocked(api.putFrameTex).mockResolvedValue({ ok: true, status: "ok", page_count: 5 });
    vi.mocked(api.putDeckTex).mockResolvedValue({ ok: true, status: "ok", page_count: 5 });
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
      editorModeBySession: {},
      slidesSourcesBySession: { 7: SLIDES7 },
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

  it("reserves the scrollbar gutter on the scroll areas (issue #6 flap guard)", async () => {
    // scrollbar-gutter: stable keeps clientWidth constant when the vertical
    // scrollbar toggles, so the ResizeObserver-driven page width can't
    // oscillate between two layouts at threshold widths.
    const { container } = render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    await waitFor(() => {
      expect(container.querySelectorAll("[data-page]")).toHaveLength(5);
    });
    // The main slide scroll area is the parent of the per-page wrappers.
    const mainArea = container.querySelector<HTMLElement>('[data-page="1"]')
      ?.parentElement;
    expect(mainArea?.style.scrollbarGutter).toBe("stable");
    // The filmstrip rail reserves it too.
    const filmstrip = container.querySelector<HTMLElement>(".overflow-y-auto");
    expect(filmstrip?.style.scrollbarGutter).toBe("stable");
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

  // ── F6.2 manual editing + Sources strip ──────────────────────────────

  it("renders both edit affordances enabled for an ok deck", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    expect(
      await screen.findByRole("button", { name: /Edit all deck/i }),
    ).toBeEnabled();
    expect(
      await screen.findByRole("button", { name: /Edit this frame/i }),
    ).toBeEnabled();
  });

  it("frame toggle opens the editor with the current frame; modes are exclusive", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit this frame/i }));
    expect(await screen.findByLabelText("latex-source")).toHaveValue(
      "\\begin{frame}{One}a\\end{frame}",
    );
    fireEvent.click(screen.getByRole("button", { name: /Edit all deck/i }));
    await waitFor(() =>
      expect(screen.getByText(/Editing the whole deck/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Editing this frame/i)).not.toBeInTheDocument();
  });

  it("saving a frame edit recompiles and exits the editor, staying on the page", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit this frame/i }));
    await screen.findByLabelText("latex-source");
    fireEvent.click(screen.getByRole("button", { name: /save & recompile/i }));
    await waitFor(() =>
      expect(api.putFrameTex).toHaveBeenCalledWith(7, 1, expect.any(String)),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("latex-source")).not.toBeInTheDocument(),
    );
    expect(useSlidesStore.getState().currentPageBySession[7]).toBe(1);
  });

  it("a compile failure keeps the editor open with the log", async () => {
    vi.mocked(api.putFrameTex).mockResolvedValue({
      ok: false,
      status: "error",
      log: "! Undefined control sequence.",
    });
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit this frame/i }));
    await screen.findByLabelText("latex-source");
    fireEvent.click(screen.getByRole("button", { name: /save & recompile/i }));
    expect(await screen.findByText(/Undefined control sequence/)).toBeInTheDocument();
    expect(screen.getByLabelText("latex-source")).toBeInTheDocument();
  });

  it("the active 'Edit all deck' button toggles the editor closed", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    const deckBtn = await screen.findByRole("button", { name: /Edit all deck/i });
    fireEvent.click(deckBtn);
    expect(await screen.findByLabelText("latex-source")).toBeInTheDocument();
    // Clicking it again (now active) exits the editor.
    fireEvent.click(deckBtn);
    await waitFor(() =>
      expect(screen.queryByLabelText("latex-source")).not.toBeInTheDocument(),
    );
  });

  it("frame editor follows the active page when navigating", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Edit this frame/i }));
    const ta = await screen.findByLabelText("latex-source");
    expect(ta).toHaveValue("\\begin{frame}{One}a\\end{frame}");
    // Navigate to the next page → the editor reloads with page 2's frame.
    fireEvent.click(screen.getByRole("button", { name: /next slide/i }));
    await waitFor(() =>
      expect(screen.getByLabelText("latex-source")).toHaveValue(
        "\\begin{frame}{Two}b\\end{frame}",
      ),
    );
  });

  it("renders the Sources strip chip for the current page", async () => {
    render(<SlidesPanel sessionId={7} speakerNotes={{}} />);
    expect(
      await screen.findByRole("button", { name: /Introduction/ }),
    ).toBeInTheDocument();
  });
});
