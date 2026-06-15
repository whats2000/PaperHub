import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useEffect } from "react";

import { useSlidesStore } from "@/store/slides";
import type { DeckSlideDetail } from "@/types/domain";

// react-pdf pulls a worker URL + canvas APIs jsdom lacks — stub it.
vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children?: React.ReactNode;
    onLoadSuccess?: (pdf: { numPages: number }) => void;
  }) => {
    useEffect(() => onLoadSuccess?.({ numPages: 2 }), [onLoadSuccess]);
    return <div data-testid="pdf-document">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid={`pdf-page-${pageNumber}`} />
  ),
}));

// CodeMirror → textarea (DOM-layout-free).
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

vi.mock("@/hooks/usePresentation", () => ({
  usePresentation: () => ({
    presenting: false,
    audienceConnected: false,
    present: () => {},
    stop: () => {},
  }),
}));

const SLIDES: DeckSlideDetail[] = [
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

const mocks = vi.hoisted(() => ({
  fetchDeckPdfData: vi.fn(),
  getDeckSlides: vi.fn(),
  getDeckTexText: vi.fn(),
  putFrameTex: vi.fn(),
  putDeckTex: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  deckPdfUrl: (sid: number) => `/sessions/${sid}/deck/pdf`,
  deckTexUrl: (sid: number) => `/sessions/${sid}/deck/tex`,
  fetchDeckPdfData: mocks.fetchDeckPdfData,
  getDeckSlides: mocks.getDeckSlides,
  getDeckTexText: mocks.getDeckTexText,
  putFrameTex: mocks.putFrameTex,
  putDeckTex: mocks.putDeckTex,
}));

import { SlidesPanel } from "./SlidesPanel";

const SID = 1;

function seedDeck() {
  useSlidesStore.setState({
    deckBySession: {
      [SID]: {
        deck_id: 1,
        session_id: SID,
        page_count: 2,
        title: "T",
        status: "ok",
        contributing_papers: [],
        has_notes: false,
      },
    },
    deckRevisionBySession: { [SID]: 0 },
    currentPageBySession: { [SID]: 1 },
    editorModeBySession: {},
    slidesSourcesBySession: {},
  });
}

describe("SlidesPanel — manual editing + Sources strip", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchDeckPdfData.mockResolvedValue(new Uint8Array([1, 2, 3]));
    mocks.getDeckSlides.mockResolvedValue(SLIDES);
    mocks.getDeckTexText.mockResolvedValue("\\documentclass{beamer}...");
    mocks.putFrameTex.mockResolvedValue({ ok: true, status: "ok", page_count: 2 });
    mocks.putDeckTex.mockResolvedValue({ ok: true, status: "ok", page_count: 2 });
    seedDeck();
  });

  it("renders both edit affordances enabled for an ok deck", async () => {
    render(<SlidesPanel sessionId={SID} speakerNotes={{}} />);
    expect(
      await screen.findByRole("button", { name: /Edit all deck/i }),
    ).toBeEnabled();
    expect(
      await screen.findByRole("button", { name: /Edit this frame/i }),
    ).toBeEnabled();
  });

  it("frame toggle opens the editor with the current frame; modes are exclusive", async () => {
    render(<SlidesPanel sessionId={SID} speakerNotes={{}} />);
    // Wait for slides detail to load (enables the frame toggle).
    const frameBtn = await screen.findByRole("button", { name: /Edit this frame/i });
    await waitFor(() => expect(mocks.getDeckSlides).toHaveBeenCalled());
    fireEvent.click(frameBtn);
    const ta = await screen.findByLabelText("latex-source");
    expect(ta).toHaveValue("\\begin{frame}{One}a\\end{frame}");
    // Switching to deck mode exits frame mode (mutually exclusive).
    fireEvent.click(screen.getByRole("button", { name: /Edit all deck/i }));
    await waitFor(() =>
      expect(screen.getByText(/Editing the whole deck/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Editing this frame/i)).not.toBeInTheDocument();
  });

  it("saving a frame edit recompiles and exits the editor (stays on page)", async () => {
    render(<SlidesPanel sessionId={SID} speakerNotes={{}} />);
    const frameBtn = await screen.findByRole("button", { name: /Edit this frame/i });
    await waitFor(() => expect(mocks.getDeckSlides).toHaveBeenCalled());
    fireEvent.click(frameBtn);
    await screen.findByLabelText("latex-source");
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(mocks.putFrameTex).toHaveBeenCalledWith(SID, 1, expect.any(String)));
    // Editor closed; current page unchanged.
    await waitFor(() =>
      expect(screen.queryByLabelText("latex-source")).not.toBeInTheDocument(),
    );
    expect(useSlidesStore.getState().currentPageBySession[SID]).toBe(1);
  });

  it("a compile failure keeps the editor open with the log", async () => {
    mocks.putFrameTex.mockResolvedValue({
      ok: false,
      status: "error",
      log: "! Undefined control sequence.",
    });
    render(<SlidesPanel sessionId={SID} speakerNotes={{}} />);
    const frameBtn = await screen.findByRole("button", { name: /Edit this frame/i });
    await waitFor(() => expect(mocks.getDeckSlides).toHaveBeenCalled());
    fireEvent.click(frameBtn);
    await screen.findByLabelText("latex-source");
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(
      await screen.findByText(/Undefined control sequence/),
    ).toBeInTheDocument();
    // Still in the editor.
    expect(screen.getByLabelText("latex-source")).toBeInTheDocument();
  });

  it("renders the Sources strip for the current page", async () => {
    render(<SlidesPanel sessionId={SID} speakerNotes={{}} />);
    // Page 1's source chip (Introduction) resolves.
    expect(
      await screen.findByRole("button", { name: /Introduction/ }),
    ).toBeInTheDocument();
  });
});
