import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import type { PdfTranslationOptions } from "@/components/canvas/PdfView";
import type { ParagraphsResponse, TranslateRequest } from "@/types/translation";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), message: vi.fn() } }));

// react-pdf doesn't run under jsdom: record the translation options instead.
const pdfTranslation: { current: PdfTranslationOptions | undefined } = {
  current: undefined,
};
vi.mock("@/components/canvas/PdfView", () => ({
  PdfView: ({ translation }: { translation?: PdfTranslationOptions }) => {
    pdfTranslation.current = translation;
    return <div data-testid="pdf-view" />;
  },
}));

import { CitationCanvas } from "@/components/canvas/CitationCanvas";
import { API_BASE_URL } from "@/lib/api";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useSettingsStore } from "@/store/settings";
import type { SettingsConfig } from "@/lib/api";
import { useTranslationStore } from "@/store/translation";
import type { ReferenceItem } from "@/types/domain";

const HTML_PAPER = 7;
const PDF_PAPER = 8;

function ref(over: Partial<ReferenceItem>): ReferenceItem {
  return {
    papers_id: 1,
    paper_content_id: HTML_PAPER,
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

const PAPER_BODY =
  "<p>The encoder maps tokens.</p><p>The decoder emits words.</p>";

const requests: TranslateRequest[] = [];
// Server state: the cached translations predate the terminology memory until
// a forced re-translate runs with the current one.
let forcedReply: "fresh" | "fail" = "fresh";
let paragraphs: ParagraphsResponse = { status: "no_blocks", paragraphs: [] };
// Set to hold the paragraphs response until released.
let paragraphsGate: Promise<void> | null = null;
let paragraphRequests = 0;
let fullPaperConfig = true;

const server = setupServer(
  http.get(`${API_BASE_URL}/papers/content/${HTML_PAPER}/document`, () =>
    HttpResponse.json({ mode: "html" }),
  ),
  http.get(`${API_BASE_URL}/papers/content/${HTML_PAPER}/html`, () =>
    HttpResponse.text("<!DOCTYPE html><html><body></body></html>"),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/document`, () =>
    HttpResponse.json({ mode: "pdf" }),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/pdf`, () =>
    HttpResponse.arrayBuffer(new Uint8Array([37, 80, 68, 70]).buffer),
  ),
  http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/paragraphs`, async () => {
    paragraphRequests++;
    await paragraphsGate;
    return HttpResponse.json(paragraphs);
  }),
  http.get(`${API_BASE_URL}/translations/config`, () =>
    HttpResponse.json({ full_paper: fullPaperConfig }),
  ),
  http.post(`${API_BASE_URL}/translations`, async ({ request }) => {
    const body = (await request.json()) as TranslateRequest;
    requests.push(body);
    if (body.force && forcedReply === "fail") {
      // Ruling K: a failed forced re-translate returns the old cached text.
      return HttpResponse.json({
        results: body.blocks.map((b) => ({
          key: b.key,
          status: "ok",
          text: `舊:${b.text}`,
          model: "m",
          stale_terminology: true,
        })),
      });
    }
    return HttpResponse.json({
      results: body.blocks.map((b) => ({
        key: b.key,
        status: "ok",
        text: `${body.force ? "新" : "舊"}:${b.text}`,
        model: "m",
        stale_terminology: !body.force,
      })),
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  requests.length = 0;
  forcedReply = "fresh";
  paragraphs = { status: "no_blocks", paragraphs: [] };
  paragraphsGate = null;
  paragraphRequests = 0;
  fullPaperConfig = true;
  pdfTranslation.current = undefined;
  useTranslationStore.setState({
    language: "繁體中文",
    recent: [],
    enabledByPaper: {},
    hintSeen: true,
  });
  useCanvasStore.setState({
    open: false,
    requestedChunkId: null,
    requestNonce: 0,
    requestedPaperId: null,
    paperRequestNonce: 0,
  });
  useChatStore.getState().reset();
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 99);
  useChatStore.getState().setReferences(99, [
    ref({ papers_id: 1, paper_content_id: HTML_PAPER, title: "HTML paper" }),
    ref({ papers_id: 2, paper_content_id: PDF_PAPER, title: "PDF paper" }),
  ]);
});

/** jsdom doesn't parse `srcdoc`: fill the iframe and fire its load. */
async function loadHtmlPaper(
  container: HTMLElement,
  body: string = PAPER_BODY,
): Promise<Document> {
  let iframe: HTMLIFrameElement | null = null;
  await waitFor(() => {
    iframe = container.querySelector<HTMLIFrameElement>(
      'div:not([hidden]) > iframe[title="Citation Canvas"]',
    );
    expect(iframe).not.toBeNull();
  });
  const doc = iframe!.contentDocument!;
  doc.body.innerHTML = body;
  act(() => {
    iframe!.dispatchEvent(new Event("load"));
  });
  return doc;
}

const box = (doc: Document, key: string) =>
  doc.querySelector<HTMLElement>(`.ph-tr[data-ph-tr-key="${key}"]`);

const openMenu = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(
    await screen.findByRole("button", { name: /Translation language/ }),
  );
};

describe("CitationCanvas bilingual reading (HTML)", () => {
  it("toggle on → blocks requested → rendered → stale → forced re-translate", async () => {
    const user = userEvent.setup();
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    const doc = await loadHtmlPaper(container);
    expect(requests).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Translate paper" }));
    expect(useTranslationStore.getState().enabledByPaper[HTML_PAPER]).toBe(true);

    await waitFor(() =>
      expect(box(doc, "h:0")?.textContent).toBe("舊:The encoder maps tokens."),
    );
    expect(requests[0]).toMatchObject({
      paper_content_id: HTML_PAPER,
      language: "繁體中文",
    });
    expect(requests[0]!.force).toBeUndefined();
    expect(requests[0]!.blocks.map((b) => b.key)).toEqual(["h:0", "h:1"]);

    // The stale results surface the re-translate action in the menu.
    await openMenu(user);
    await user.click(
      await screen.findByRole("button", {
        name: "Re-translate with current terminology",
      }),
    );
    await waitFor(() =>
      expect(box(doc, "h:0")?.textContent).toBe("新:The encoder maps tokens."),
    );
    const forced = requests.filter((r) => r.force === true);
    expect(forced).toHaveLength(1);
    expect(forced[0]!.blocks.map((b) => b.key)).toEqual(["h:0", "h:1"]);

    // Fresh results: the action is gone.
    await openMenu(user);
    expect(
      screen.queryByRole("button", {
        name: "Re-translate with current terminology",
      }),
    ).toBeNull();
  });

  it("a failed forced re-translate keeps the old text and the action", async () => {
    forcedReply = "fail";
    useTranslationStore.setState({ enabledByPaper: { [HTML_PAPER]: true } });
    const user = userEvent.setup();
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    const doc = await loadHtmlPaper(container);
    await waitFor(() =>
      expect(box(doc, "h:1")?.textContent).toBe("舊:The decoder emits words."),
    );

    await openMenu(user);
    await user.click(
      await screen.findByRole("button", {
        name: "Re-translate with current terminology",
      }),
    );
    await waitFor(() =>
      expect(requests.some((r) => r.force === true)).toBe(true),
    );
    expect(box(doc, "h:1")?.getAttribute("data-ph-tr-state")).toBe("ok");
    expect(box(doc, "h:1")?.textContent).toBe("舊:The decoder emits words.");
    await openMenu(user);
    expect(
      await screen.findByRole("button", {
        name: "Re-translate with current terminology",
      }),
    ).toBeTruthy();
  });

  it("remembers on/off per paper across a paper switch", async () => {
    paragraphs = {
      status: "ready",
      paragraphs: [
        { key: "p:k", text: "Hi.", math: [], fragments: [{ page: 0, bbox: [0, 0, 1, 1] }] },
      ],
    };
    const user = userEvent.setup();
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    await loadHtmlPaper(container);
    await user.click(screen.getByRole("button", { name: "Translate paper" }));
    expect(useTranslationStore.getState().enabledByPaper).toEqual({
      [HTML_PAPER]: true,
    });

    // The PDF paper stays off.
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Translate paper" })
          .getAttribute("aria-pressed"),
      ).toBe("false"),
    );
    expect(pdfTranslation.current?.enabled).toBe(false);

    // Back to the HTML paper: still on.
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Translate paper" })
          .getAttribute("aria-pressed"),
      ).toBe("true"),
    );
    expect(useTranslationStore.getState().enabledByPaper).toEqual({
      [HTML_PAPER]: true,
    });
  });
});

describe("CitationCanvas bilingual reading (HTML, nothing to translate)", () => {
  it("disables the pill with a reason for a raw <pre> render (QA B2)", async () => {
    const { container } = render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    await loadHtmlPaper(container, "<pre>\\section{Intro} raw LaTeX</pre>");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Translate paper" })).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
    expect(screen.getByRole("button", { name: "Translate paper" })).toHaveAccessibleDescription(
      /can't be translated/,
    );
    expect(requests).toHaveLength(0);
  });
});

describe("CitationCanvas bilingual reading (PDF)", () => {
  it("disables the pill with the reason when the PDF has no paragraph layout", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    await waitFor(() =>
      // Disabled: the container is the focusable control (aria-disabled).
      expect(
        screen
          .getByRole("button", { name: "Translate paper" })
          .getAttribute("aria-disabled"),
      ).toBe("true"),
    );
    expect(
      await screen.findByText("This PDF has no paragraph layout yet"),
    ).toBeTruthy();
    const pill = screen.getByRole("button", { name: "Translate paper" });
    expect(pill.getAttribute("aria-busy")).toBeNull();
    expect(pill.getAttribute("aria-describedby")).not.toBeNull();
  });

  it("passes the paragraphs, language and full-paper switch to the PDF view", async () => {
    paragraphs = {
      status: "ready",
      paragraphs: [
        {
          key: "p:/page/0/Text/1",
          text: "Hello.",
          math: [],
          fragments: [{ page: 0, bbox: [0, 0, 10, 10] }],
        },
      ],
    };
    useTranslationStore.setState({ enabledByPaper: { [PDF_PAPER]: true } });
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await waitFor(() =>
      expect(pdfTranslation.current?.paragraphs).toEqual(paragraphs.paragraphs),
    );
    expect(pdfTranslation.current).toMatchObject({
      enabled: true,
      language: "繁體中文",
      paperId: PDF_PAPER,
      fullPaper: true,
    });
    expect(
      screen
        .getByRole("button", { name: "Translate paper" })
        .getAttribute("aria-disabled"),
    ).not.toBe("true");
  });

  it("keeps the pill non-interactive while the paragraphs load, then enables it", async () => {
    let release!: () => void;
    paragraphsGate = new Promise<void>((r) => {
      release = r;
    });
    paragraphs = {
      status: "ready",
      paragraphs: [
        { key: "p:k", text: "Hi.", math: [], fragments: [{ page: 0, bbox: [0, 0, 1, 1] }] },
      ],
    };
    useTranslationStore.setState({ enabledByPaper: { [PDF_PAPER]: true } });
    const user = userEvent.setup();
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");

    const pill = screen.getByRole("button", { name: "Translate paper" });
    expect(pill.getAttribute("aria-disabled")).toBe("true");
    expect(pill.getAttribute("aria-busy")).toBe("true");
    // No reason text: the state is unknown, not unavailable.
    expect(pill.getAttribute("aria-describedby")).toBeNull();
    expect(screen.queryByText("This PDF has no paragraph layout yet")).toBeNull();
    // The layer gets none (not undefined), so it doesn't fetch on its own.
    expect(pdfTranslation.current?.paragraphs).toEqual([]);
    expect(pdfTranslation.current?.paragraphs).not.toBeUndefined();

    // A click while pending changes nothing.
    useTranslationStore.setState({ enabledByPaper: {} });
    await user.click(pill);
    expect(useTranslationStore.getState().enabledByPaper).toEqual({});

    release();
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "Translate paper" })
          .getAttribute("aria-disabled"),
      ).not.toBe("true"),
    );
    await user.click(screen.getByRole("button", { name: "Translate paper" }));
    expect(useTranslationStore.getState().enabledByPaper).toEqual({
      [PDF_PAPER]: true,
    });
  });

  it("hands the fetch to the PDF layer when the paragraphs request fails", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/paragraphs`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    await waitFor(() => {
      expect(pdfTranslation.current?.paragraphs).toBeUndefined();
      expect(
        screen
          .getByRole("button", { name: "Translate paper" })
          .getAttribute("aria-disabled"),
      ).not.toBe("true");
    });
  });
});

describe("CitationCanvas translate pill: load failures", () => {
  const pillOf = () => screen.getByRole("button", { name: "Translate paper" });

  it("says why when the paper's document fails to load", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/document`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await waitFor(() => expect(pillOf().getAttribute("aria-disabled")).toBe("true"));
    await waitFor(() => expect(pillOf().getAttribute("aria-busy")).toBeNull());
    expect(
      await screen.findByText("This paper didn't load, so it can't be translated"),
    ).toBeTruthy();
    expect(pillOf().getAttribute("aria-describedby")).not.toBeNull();
  });

  it("says why when the PDF layer's own paragraphs fetch fails too", async () => {
    server.use(
      http.get(`${API_BASE_URL}/papers/content/${PDF_PAPER}/paragraphs`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    await waitFor(() => expect(pdfTranslation.current?.paragraphs).toBeUndefined());
    act(() => pdfTranslation.current?.onParagraphsLoaded?.(null));
    await waitFor(() => expect(pillOf().getAttribute("aria-disabled")).toBe("true"));
    expect(
      await screen.findByText("This paper didn't load, so it can't be translated"),
    ).toBeTruthy();
    // The layer is not asked to fetch again on its own.
    expect(pdfTranslation.current?.paragraphs).toEqual([]);
  });
});

describe("CitationCanvas translate pill: paragraph status refresh", () => {
  const pillOf = () => screen.getByRole("button", { name: "Translate paper" });
  const READY_ONE: ParagraphsResponse = {
    status: "ready",
    paragraphs: [
      {
        key: "p:/page/0/Text/1",
        text: "Hello.",
        math: [],
        fragments: [{ page: 0, bbox: [0, 0, 10, 10] }],
      },
    ],
  };

  it("re-checks a no_blocks PDF on hover of the disabled pill", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByText("This PDF has no paragraph layout yet");
    expect(paragraphRequests).toBe(1);

    // Marker writes blocks.json later: a hover re-checks.
    paragraphs = READY_ONE;
    fireEvent.pointerEnter(pillOf());
    await waitFor(() =>
      expect(pillOf().getAttribute("aria-disabled")).not.toBe("true"),
    );
    expect(paragraphRequests).toBe(2);
  });

  it("does not re-check more often than every 5 s", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    try {
      render(<CitationCanvas />);
      act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
      await screen.findByText("This PDF has no paragraph layout yet");
      fireEvent.pointerEnter(pillOf());
      fireEvent.focus(pillOf());
      await waitFor(() => expect(paragraphRequests).toBe(2));
      now.mockReturnValue(1_004_000);
      fireEvent.pointerEnter(pillOf());
      now.mockReturnValue(1_006_000);
      fireEvent.pointerEnter(pillOf());
      await waitFor(() => expect(paragraphRequests).toBe(3));
    } finally {
      now.mockRestore();
    }
  });

  it("re-checks a non-ready PDF when it becomes active again, but not a ready one", async () => {
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByText("This PDF has no paragraph layout yet");
    paragraphs = READY_ONE;
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await waitFor(() => expect(paragraphRequests).toBe(2));
    await waitFor(() =>
      expect(pillOf().getAttribute("aria-disabled")).not.toBe("true"),
    );
    act(() => useCanvasStore.getState().openPaper(HTML_PAPER));
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    expect(paragraphRequests).toBe(2);
  });
});

describe("CitationCanvas translate pill: empty PDF", () => {
  it("treats a ready PDF with no translatable paragraphs as no_blocks", async () => {
    paragraphs = {
      status: "ready",
      paragraphs: [{ key: "p:x", text: "Orphan.", math: [], fragments: [] }],
    };
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    expect(
      await screen.findByText("This PDF has no paragraph layout yet"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Translate paper" })
        .getAttribute("aria-disabled"),
    ).toBe("true");
  });
});

describe("CitationCanvas translation config", () => {
  it("re-reads the full-paper switch when the Settings config changes", async () => {
    fullPaperConfig = false;
    paragraphs = {
      status: "ready",
      paragraphs: [
        { key: "p:k", text: "Hi.", math: [], fragments: [{ page: 0, bbox: [0, 0, 1, 1] }] },
      ],
    };
    render(<CitationCanvas />);
    act(() => useCanvasStore.getState().openPaper(PDF_PAPER));
    await screen.findByTestId("pdf-view");
    await waitFor(() => expect(pdfTranslation.current?.fullPaper).toBe(false));

    // A Settings save refreshes the store's config.
    fullPaperConfig = true;
    act(() => useSettingsStore.setState({ config: {} as SettingsConfig }));
    await waitFor(() => expect(pdfTranslation.current?.fullPaper).toBe(true));
  });
});
