import { act, render, screen, waitFor } from "@testing-library/react";
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
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), message: vi.fn() } }));

// react-pdf doesn't run under jsdom: record what each PDF view was asked to show.
interface PdfProps {
  data: Uint8Array;
  bboxPage?: number | null;
  bbox?: number[] | null;
}
const pdfProps: { current: PdfProps | null } = { current: null };
vi.mock("@/components/canvas/PdfView", () => ({
  PdfView: (props: PdfProps) => {
    pdfProps.current = props;
    return <div data-testid="pdf-view" />;
  },
}));

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import type { ReferenceItem } from "@/types/domain";

const LATEX_PAPER = 7;
const PDF_PAPER = 8;
const OWN_PDF = [37, 80, 68, 70, 1];
const PAGE_PDF = [37, 80, 68, 70, 2];

const PAPER_BODY =
  "<p>Intro.</p><p><span id=\"phfig-003\"></span></p>" +
  "<div class=\"figure\"><img src=\"a.png\" alt=\"a\"></div><p>After.</p>";

function ref(over: Partial<ReferenceItem>): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: LATEX_PAPER,
    enabled: true,
    added_at: "2024-01-01",
    arxiv_id: null,
    title: "Paper",
    year: 2024,
    kind: "arxiv",
    is_private: false,
    ...over,
  };
}

const figure = (pid: number, fid: string, over: object = {}) => ({
  paper_content_id: pid,
  id: fid,
  caption: "Figure 3: Plot.",
  page: 1,
  title: "Paper",
  has_image: true,
  html_anchor: null,
  bbox: null,
  ...over,
});

const server = setupServer(
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/document`, () =>
    HttpResponse.json({ mode: "html", page_pdf: true }),
  ),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/html`, () =>
    HttpResponse.text("<!DOCTYPE html><html><body></body></html>"),
  ),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/page-pdf`, () =>
    HttpResponse.arrayBuffer(new Uint8Array(PAGE_PDF).buffer),
  ),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/figures/fig-003`, () =>
    HttpResponse.json(figure(LATEX_PAPER, "fig-003", {
      html_anchor: "phfig-003", bbox: [100, 200, 500, 400],
    })),
  ),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/figures/fig-404`, () =>
    HttpResponse.json({ detail: "gone" }, { status: 404 }),
  ),
  http.get(`${API_BASE_URL}/papers/content/:pid/view-region`, ({ request }) => {
    // A 612x792 page: 0-1000 box -> PDF points, 1-based page -> 0-based.
    const url = new URL(request.url);
    const page = Number(url.searchParams.get("page"));
    const [x0, y0, x1, y1] = (url.searchParams.get("box") ?? "").split(",").map(Number);
    return HttpResponse.json({
      page: page - 1,
      bbox: [(x0 ?? 0) * 0.612, (y0 ?? 0) * 0.792, (x1 ?? 0) * 0.612, (y1 ?? 0) * 0.792]
        .map((v) => Math.round(v * 10) / 10),
      source: "compiled",
    });
  }),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/figures/fig-007`, () =>
    HttpResponse.json(figure(LATEX_PAPER, "fig-007", { page: 2, bbox: null })),
  ),
  http.get(`${API_BASE_URL}/papers/content/${LATEX_PAPER}/figures/fig-008`, () =>
    HttpResponse.json(figure(LATEX_PAPER, "fig-008", { page: null, bbox: null })),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/document`, () =>
    HttpResponse.json({ mode: "pdf", page_pdf: true }),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/pdf`, () =>
    HttpResponse.arrayBuffer(new Uint8Array(OWN_PDF).buffer),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/paragraphs`, () =>
    HttpResponse.json({ status: "no_blocks", paragraphs: [] }),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/figures/fig-002`, () =>
    HttpResponse.json(figure(PDF_PAPER, "fig-002", { page: 4, bbox: [50, 60, 300, 280] })),
  ),
  http.get(`${API_BASE_URL}/translations/config`, () =>
    HttpResponse.json({ full_paper: false }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  pdfProps.current = null;
  vi.mocked(toast.message).mockClear();
  useCanvasStore.setState({
    open: false,
    requestedChunkId: null,
    requestedFigure: null,
    requestedView: null,
    requestNonce: 0,
    requestedPaperId: null,
    paperRequestNonce: 0,
  });
  useChatStore.getState().reset();
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 99);
  useChatStore.getState().setReferences(99, [
    ref({ papers_id: 1, paper_content_id: LATEX_PAPER, title: "LaTeX paper" }),
    ref({ papers_id: 2, paper_content_id: PDF_PAPER, title: "PDF paper" }),
  ]);
});

/** jsdom doesn't parse `srcdoc`: fill the iframe and fire its load. */
async function loadHtmlPaper(container: HTMLElement): Promise<Document> {
  let iframe: HTMLIFrameElement | null = null;
  await waitFor(() => {
    iframe = container.querySelector<HTMLIFrameElement>(
      'div:not([hidden]) > div > iframe[title="Citation Canvas"]',
    );
    expect(iframe).not.toBeNull();
  });
  const doc = iframe!.contentDocument!;
  doc.body.innerHTML = PAPER_BODY;
  act(() => {
    iframe!.dispatchEvent(new Event("load"));
  });
  return doc;
}

const bytes = (d: Uint8Array | undefined) => (d ? Array.from(d) : null);

describe("CitationCanvas — figure and region citations", () => {
  it("a [fig:] on a LaTeX paper highlights the figure after its HTML anchor", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openFigure(LATEX_PAPER, "fig-003"));
    const doc = await loadHtmlPaper(container);
    await waitFor(() =>
      expect(doc.querySelector("div.figure")?.classList.contains("ph-cite-hl")).toBe(true),
    );
    // Only the figure, not the paragraphs around it.
    expect(doc.querySelectorAll(".ph-cite-hl")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "HTML" })).toHaveAttribute("aria-selected", "true");
    expect(useCanvasStore.getState().requestedFigure).toBeNull(); // consumed
  });

  it("a [fig:] on a PDF paper highlights the figure's box on its page", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openFigure(PDF_PAPER, "fig-002"));
    await waitFor(() => expect(pdfProps.current?.bbox).toEqual([50, 60, 300, 280]));
    expect(pdfProps.current?.bboxPage).toBe(4);
    expect(bytes(pdfProps.current?.data)).toEqual(OWN_PDF);
  });

  it("a [view:] on a LaTeX paper slides to the PDF tab and highlights the region", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openView(LATEX_PAPER, 2, [100, 250, 600, 500]));
    await waitFor(() => expect(pdfProps.current?.bbox).toEqual([61.2, 198, 367.2, 396]));
    expect(pdfProps.current?.bboxPage).toBe(1);
    expect(bytes(pdfProps.current?.data)).toEqual(PAGE_PDF);
    expect(screen.getByRole("tab", { name: "PDF" })).toHaveAttribute("aria-selected", "true");
    const track = container.querySelector("[data-reader-track]");
    expect(track?.className).toContain("transition-transform");
    expect(track?.className).toContain("-translate-x-1/2");

    // Back to the HTML tab slides the other way.
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "HTML" }));
    expect(track?.className).not.toContain("-translate-x-1/2");
  });

  it("a figure known only by its page opens that page in the PDF tab", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openFigure(LATEX_PAPER, "fig-007"));
    await waitFor(() => expect(pdfProps.current?.bboxPage).toBe(2));
    expect(pdfProps.current?.bbox).toEqual([0, 0, 612, 792]);
    expect(screen.getByRole("tab", { name: "PDF" })).toHaveAttribute("aria-selected", "true");
    expect(toast.message).not.toHaveBeenCalled();
  });

  it("a figure with no place in the paper shows its image instead", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openFigure(LATEX_PAPER, "fig-008"));
    const img = await screen.findByRole("img", { name: "Figure 3: Plot." });
    expect(img).toHaveAttribute(
      "src", `${API_BASE_URL}/papers/content/${LATEX_PAPER}/figures/fig-008/image`);
    expect(toast.message).not.toHaveBeenCalled();
  });

  it("reader tabs: arrow keys, panels, collapse on a paper without a page PDF", async () => {
    const user = userEvent.setup();
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(LATEX_PAPER));
    const html = await screen.findByRole("tab", { name: "HTML" });
    const panelId = html.getAttribute("aria-controls");
    expect(panelId && container.querySelector(`#${panelId}`)?.getAttribute("role")).toBe("tabpanel");
    html.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "PDF" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "PDF" })).toHaveFocus();

    // A PDF paper has no tabs: the bar collapses (stays mounted, animates).
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await waitFor(() =>
      expect(container.querySelector("[data-reader-tabs]")?.getAttribute("data-open")).toBe("false"));
    expect(screen.queryByRole("tab", { name: "HTML" })).toBeNull();   // hidden from a11y
  });

  it("the track slides for a tab change, not for a paper switch", async () => {
    const user = userEvent.setup();
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openView(LATEX_PAPER, 2, [100, 250, 600, 500]));
    await waitFor(() => expect(pdfProps.current?.bboxPage).toBe(1));
    const track = () => container.querySelector("[data-reader-track]")?.className ?? "";
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    act(() => useCanvasStore.getState().openPaper(LATEX_PAPER));
    await waitFor(() => expect(track()).toContain("-translate-x-1/2"));
    expect(track()).not.toContain("transition-transform");
    await user.click(screen.getByRole("tab", { name: "HTML" }));
    expect(track()).toContain("transition-transform");
  });

  it("a figure that no longer exists says so", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openFigure(LATEX_PAPER, "fig-404"));
    await waitFor(() =>
      expect(toast.message).toHaveBeenCalledWith(
        "This figure is no longer in the paper.",
      ),
    );
  });
});
