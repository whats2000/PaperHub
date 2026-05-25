import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";
import { SlidesPanel } from "@/components/slides/SlidesPanel";

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
});
