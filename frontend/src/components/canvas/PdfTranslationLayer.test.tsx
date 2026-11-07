import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PdfTranslationLayer, PdfTranslationPage } from "@/components/canvas/PdfTranslationLayer";
import { getPdfParagraphs, translateBlocks } from "@/lib/api";
import { mathToKatexHtml } from "@/lib/pdfTranslation";
import { useTranslationStore } from "@/store/translation";
import type { ParagraphsResponse, PdfFragment, TranslateRequest } from "@/types/translation";

vi.mock("@/lib/api", () => ({
  getPdfParagraphs: vi.fn(),
  translateBlocks: vi.fn(),
}));

const mockedParagraphs = vi.mocked(getPdfParagraphs);
const mockedTranslate = vi.mocked(translateBlocks);

const P1 = "p:/page/0/Text/1";
const P2 = "p:/page/1/Text/4";

const READY: ParagraphsResponse = {
  status: "ready",
  paragraphs: [
    {
      key: P1,
      text: "Let ⟦0⟧ be the loss.⟦BR⟧It is convex.",
      math: ["x^2"],
      // Rendered at half scale: page 1 (25,300)-(275,380); page 2 (25,20)-(275,45).
      fragments: [
        { page: 0, bbox: [50, 600, 550, 760] },
        { page: 1, bbox: [50, 40, 550, 90] },
      ],
    },
    {
      key: P2,
      text: "A second paragraph.",
      math: [],
      // Page 2 (25,60)-(275,100).
      fragments: [{ page: 1, bbox: [50, 120, 550, 200] }],
    },
    {
      key: "p:/page/1/Text/9",
      text: "No geometry.",
      math: [],
      fragments: [],
    },
  ],
};

/** Inside P1 on page 1 / inside P2 on page 2 (jsdom rects are at 0,0). */
const AT_P1 = { clientX: 100, clientY: 320 };
const AT_P2 = { clientX: 100, clientY: 80 };
const BLANK = { clientX: 5, clientY: 5 };

function Harness({
  language = "繁體中文",
  renderedWidth = 300,
}: {
  language?: string;
  renderedWidth?: number;
}) {
  const readerRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={readerRef} data-testid="reader">
      <PdfTranslationLayer
        paperId={7}
        language={language}
        fullPaper={false}
        enabled
        readerRef={readerRef}
      >
        <div data-testid="page-1">
          {/* Stand-in for react-pdf's text layer: events bubble to the page. */}
          <span data-testid="text-1">text</span>
          <a href="#ref-3" data-testid="link-1" className="linkAnnotation">
            [3]
          </a>
          <PdfTranslationPage pageNumber={1} renderedWidth={renderedWidth} originalWidth={600} />
        </div>
        <div data-testid="page-2">
          <span data-testid="text-2">text</span>
          <PdfTranslationPage pageNumber={2} renderedWidth={renderedWidth} originalWidth={600} />
        </div>
      </PdfTranslationLayer>
    </div>
  );
}

const fragmentsOf = (key: string) =>
  Array.from(document.querySelectorAll<HTMLElement>(`[data-paragraph-key="${CSS.escape(key)}"]`));

const textOf = (page: 1 | 2) => screen.getByTestId(`text-${page}`);
const hoverAt = (page: 1 | 2, at: { clientX: number; clientY: number }) =>
  fireEvent.pointerMove(textOf(page), at);
const clickAt = (page: 1 | 2, at: { clientX: number; clientY: number }) => {
  fireEvent.pointerDown(textOf(page), at);
  fireEvent.click(textOf(page), at);
};

async function renderReady(language?: string) {
  const utils = render(<Harness language={language} />);
  await waitFor(() => expect(fragmentsOf(P1)).toHaveLength(2));
  return utils;
}

beforeEach(() => {
  stubLayout();
  useTranslationStore.setState({ hintSeen: false });
  mockedParagraphs.mockResolvedValue(READY);
  mockedTranslate.mockImplementation((req: TranslateRequest) =>
    Promise.resolve({
      results: req.blocks.map((b) => ({
        key: b.key,
        status: "ok" as const,
        text: b.key === P1 ? `設 ⟦0⟧ 為損失（${req.language}）。⟦BR⟧它是凸的。` : "第二段。",
        model: "m",
        stale_terminology: false,
      })),
    }),
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("mathToKatexHtml", () => {
  it("renders placeholders with KaTeX and escapes the text", () => {
    const html = mathToKatexHtml("a <b> ⟦0⟧ c", ["x^2"]);
    expect(html).toContain("a &lt;b&gt; ");
    expect(html).toContain('class="katex"');
    expect(html).not.toContain("⟦0⟧");
  });

  it("renders escaped text-mode reference markers", () => {
    const html = mathToKatexHtml("see ⟦0⟧", ["\\text{[3]\\^{}}"]);
    expect(html).toContain('class="katex"');
  });

  it("falls back to the raw source when KaTeX can't parse it", () => {
    const html = mathToKatexHtml("bad ⟦0⟧", ["\\frac{<x"]);
    expect(html).toContain("\\frac{&lt;x");
    expect(html).not.toContain('class="katex"');
  });

  it("keeps an unknown placeholder visible and marks page breaks", () => {
    const html = mathToKatexHtml("a ⟦5⟧ b⟦BR⟧c", []);
    expect(html).toContain("⟦5⟧");
    expect(html).toContain("data-break");
  });

  it("labels each page break in order, escaped", () => {
    const html = mathToKatexHtml("a⟦BR⟧b⟦BR⟧c", [], ["continued on page 2", "<x>"]);
    expect(html).toContain(">continued on page 2</span>");
    expect(html).toContain(">&lt;x&gt;</span>");
  });
});

describe("PdfTranslationLayer", () => {
  it("keeps the rects visual only so the text layer stays selectable", async () => {
    await renderReady();
    for (const f of document.querySelectorAll<HTMLElement>("[data-paragraph-key]")) {
      expect(f.className).not.toContain("pointer-events-auto");
      expect(f.closest(".pointer-events-none")).not.toBeNull();
    }
  });

  it("shows the card with translated text and KaTeX math on hover by coordinates", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    const card = await screen.findByRole("dialog");
    await waitFor(() => expect(card).toHaveTextContent("設"));
    expect(card.querySelector(".katex")).not.toBeNull();
  });

  describe("request priority", () => {
    let observers: { cb: IntersectionObserverCallback; el?: Element }[];
    beforeEach(() => {
      observers = [];
      vi.stubGlobal(
        "IntersectionObserver",
        class {
          private o: { cb: IntersectionObserverCallback; el?: Element };
          constructor(cb: IntersectionObserverCallback) {
            this.o = { cb };
            observers.push(this.o);
          }
          observe(el: Element) {
            this.o.el = el;
          }
          disconnect() {}
        },
      );
    });
    const markPage1Visible = () => {
      const page1 = observers.find((o) => screen.getByTestId("page-1").contains(o.el ?? null));
      page1!.cb(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    };

    it("requests the paragraphs on visible pages", async () => {
      await renderReady();
      act(markPage1Visible);
      await waitFor(() => expect(mockedTranslate).toHaveBeenCalled());
      expect(mockedTranslate.mock.calls[0]![0].blocks[0]!.key).toBe(P1);
    });

    it("requests a hovered paragraph ahead of the visible pages", async () => {
      await renderReady();
      act(() => {
        markPage1Visible(); // alone, this would send P1 first
        hoverAt(2, AT_P2);
      });
      await waitFor(() => expect(mockedTranslate).toHaveBeenCalled());
      expect(mockedTranslate.mock.calls[0]![0].blocks[0]!.key).toBe(P2);
    });
  });

  it("scales fragments to the page and skips paragraphs with no geometry", async () => {
    await renderReady();
    const [r] = fragmentsOf(P2);
    expect(r!.style.left).toBe("25px");
    expect(r!.style.top).toBe("60px");
    expect(r!.style.width).toBe("250px");
    expect(r!.style.height).toBe("40px");
    expect(fragmentsOf("p:/page/1/Text/9")).toHaveLength(0);
    expect(screen.getByTestId("page-1").querySelectorAll("[data-paragraph-key]")).toHaveLength(1);
  });

  it("gives every fragment of the hovered paragraph the hover state", async () => {
    await renderReady();
    hoverAt(2, { clientX: 100, clientY: 30 }); // P1's fragment on page 2
    for (const f of fragmentsOf(P1)) {
      expect(f).toHaveAttribute("data-state", "hover");
      expect(f.className).toContain("bg-black/[9%]");
    }
    expect(fragmentsOf(P2)[0]).not.toHaveAttribute("data-state");
  });

  it("pins on a plain click so leaving no longer hides the card", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);
    for (const f of fragmentsOf(P1)) {
      expect(f).toHaveAttribute("data-state", "pinned");
      expect(f.className).toContain("bg-black/[16%]");
    }

    vi.useFakeTimers();
    hoverAt(1, BLANK);
    fireEvent.pointerLeave(screen.getByTestId("page-1"));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "open");
  });

  it("does not pin after a drag that selects text", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
    } as Selection);
    fireEvent.pointerDown(textOf(1), AT_P1);
    hoverAt(1, { clientX: 160, clientY: 340 });
    fireEvent.pointerUp(textOf(1), { clientX: 160, clientY: 340 });
    fireEvent.click(textOf(1), { clientX: 160, clientY: 340 });
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "hover");

    // A press that leaves a selection behind doesn't pin either, even in place.
    clickAt(1, AT_P1);
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "hover");
  });

  it("ignores a press that moves past the slop even without a selection", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    fireEvent.pointerDown(textOf(1), AT_P1);
    fireEvent.click(textOf(1), { clientX: AT_P1.clientX + 6, clientY: AT_P1.clientY });
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "hover");
  });

  it("a pinned card ignores hover on another paragraph", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);
    hoverAt(2, AT_P2);
    expect(fragmentsOf(P2)[0]).not.toHaveAttribute("data-state");
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "pinned");
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("設"));
  });

  it("the pin button toggles pin and unpin", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    fireEvent.click(screen.getByRole("button", { name: /^pin translation/i }));
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "pinned");
    const unpin = screen.getByRole("button", { name: /unpin translation/i });
    expect(unpin).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(unpin);
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "hover");
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "open");
  });

  it("hides an unpinned card after the pointer leaves", async () => {
    await renderReady();
    hoverAt(2, AT_P2);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    vi.useFakeTimers();
    hoverAt(2, BLANK);
    act(() => {
      vi.advanceTimersByTime(250); // hover grace period
    });
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(150); // exit animation
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on Esc with an exit animation", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);

    vi.useFakeTimers();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
    expect(fragmentsOf(P1)[0]).not.toHaveAttribute("data-state");
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves Esc to a text field unless the card is pinned, and to handled events", async () => {
    await renderReady();
    const input = document.createElement("input");
    document.body.appendChild(input);
    hoverAt(1, AT_P1);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "open");

    clickAt(1, AT_P1);
    const handled = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    handled.preventDefault();
    act(() => {
      document.dispatchEvent(handled);
    });
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "open");

    fireEvent.keyDown(input, { key: "Escape" }); // pinned → closes
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
    input.remove();
  });

  it("closes from the ✕ button", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a language change disposes the queue and re-requests the open paragraph", async () => {
    const { rerender } = await renderReady("繁體中文");
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("繁體中文"));

    mockedTranslate.mockClear();
    rerender(<Harness language="日本語" />);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("（日本語）"));
    expect(mockedTranslate.mock.calls[0]![0]).toMatchObject({ language: "日本語" });
    expect(mockedTranslate.mock.calls[0]![0].blocks[0]!.key).toBe(P1);
    expect(mockedParagraphs).toHaveBeenCalledTimes(1); // geometry stays cached
  });

  it("renders no hit-rects when formulas couldn't be read", async () => {
    mockedParagraphs.mockResolvedValue({
      status: "no_inline_math_llm",
      paragraphs: READY.paragraphs,
    });
    render(<Harness />);
    await waitFor(() => expect(mockedParagraphs).toHaveBeenCalledWith(7));
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.querySelectorAll("[data-paragraph-key]")).toHaveLength(0);
    expect(mockedTranslate).not.toHaveBeenCalled();
  });

  it("offers a retry when the translation is unavailable", async () => {
    mockedTranslate.mockImplementationOnce((req: TranslateRequest) =>
      Promise.resolve({
        results: req.blocks.map((b) => ({
          key: b.key,
          status: "unavailable" as const,
          text: null,
          model: null,
          stale_terminology: false,
        })),
      }),
    );
    await renderReady();
    hoverAt(2, AT_P2);
    const retry = await screen.findByRole("button", { name: /retry/i });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("第二段。"));
    expect(mockedTranslate).toHaveBeenCalledTimes(2);
  });
});

/**
 * jsdom has no layout: give the reader a box (x readerLeft..+700, y 0-800, so
 * a gutter of readerLeft-24 on its left) and place every inline-positioned
 * element (the paragraph rects) from its style, page-relative at readerLeft.
 */
function stubLayout(readerLeft = 600, readerTop = 0) {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
    this: Element,
  ) {
    if (this.getAttribute("data-testid") === "reader") {
      // A reader further down the page = its paragraphs scrolled out above.
      return new DOMRect(readerLeft, readerTop, 700, 800);
    }
    const s = (this as HTMLElement).style;
    if (this instanceof HTMLElement && this.dataset.paragraphKey && s.top) {
      return new DOMRect(
        readerLeft + parseFloat(s.left),
        parseFloat(s.top),
        parseFloat(s.width),
        parseFloat(s.height),
      );
    }
    return new DOMRect(0, 0, 0, 0);
  });
}

const card = () => screen.getByRole("dialog");

describe("PdfTranslationLayer — long and split paragraphs", () => {
  it("labels a page break inside the paragraph with the next fragment's page", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    await waitFor(() => expect(card()).toHaveTextContent("它是凸的"));
    expect(card().querySelector("[data-break]")).toHaveTextContent("continued on page 2");
  });

  /** Render one two-fragment paragraph with its translation, hovered. */
  async function hoverSplit(
    fragments: PdfFragment[],
    translated: string,
  ) {
    mockedParagraphs.mockResolvedValue({
      status: "ready",
      paragraphs: [{ key: P1, text: "A.⟦BR⟧B.", math: [], fragments }],
    });
    mockedTranslate.mockImplementation((req: TranslateRequest) =>
      Promise.resolve({
        results: req.blocks.map((b) => ({
          key: b.key,
          status: "ok" as const,
          text: translated,
          model: "m",
          stale_terminology: false,
        })),
      }),
    );
    render(<Harness />);
    await waitFor(() => expect(fragmentsOf(P1)).toHaveLength(fragments.length));
    hoverAt(1, AT_P1);
    await waitFor(() => expect(card()).toHaveTextContent("尾"));
  }

  it("leaves a same-column bridge on the same page unlabelled", async () => {
    // Resumes below a display equation, same column, same page.
    await hoverSplit(
      [
        { page: 0, bbox: [50, 600, 290, 700] },
        { page: 0, bbox: [52, 720, 290, 760] },
      ],
      "首。⟦BR⟧尾。",
    );
    const divider = card().querySelector("[data-break]")!;
    expect(divider).toBeInTheDocument();
    expect(divider).toHaveTextContent("");
  });

  it("draws unlabelled dividers when fragments and breaks don't line up", async () => {
    // Two breaks but only two fragments (a zero-size fragment was dropped).
    await hoverSplit(
      [
        { page: 0, bbox: [50, 600, 550, 760] },
        { page: 1, bbox: [50, 40, 550, 90] },
      ],
      "首。⟦BR⟧中。⟦BR⟧尾。",
    );
    const dividers = card().querySelectorAll("[data-break]");
    expect(dividers).toHaveLength(2);
    dividers.forEach((d) => expect(d).toHaveTextContent(""));
  });

  it("labels a column break with the right column", async () => {
    mockedParagraphs.mockResolvedValue({
      status: "ready",
      paragraphs: [
        {
          key: P1,
          text: "Left part.⟦BR⟧Right part.",
          math: [],
          fragments: [
            { page: 0, bbox: [50, 600, 290, 760] },
            { page: 0, bbox: [310, 60, 550, 120] },
          ],
        },
      ],
    });
    mockedTranslate.mockImplementation((req: TranslateRequest) =>
      Promise.resolve({
        results: req.blocks.map((b) => ({
          key: b.key,
          status: "ok" as const,
          text: "左半。⟦BR⟧右半。",
          model: "m",
          stale_terminology: false,
        })),
      }),
    );
    render(<Harness />);
    await waitFor(() => expect(fragmentsOf(P1)).toHaveLength(2));
    hoverAt(1, AT_P1);
    await waitFor(() => expect(card()).toHaveTextContent("右半"));
    expect(card().querySelector("[data-break]")).toHaveTextContent(
      "continued in the right column",
    );
  });

  it("places the card left of the reader, aligned with the paragraph", async () => {
    await renderReady();
    hoverAt(2, AT_P2);
    expect(card().style.top).toBe("60px"); // P2's top at width 300
    expect(card().style.width).toBe("460px");
    expect(card()).toHaveAttribute("data-side", "left");
  });

  it("re-places the card when the page width changes, without a scroll", async () => {
    const { rerender } = await renderReady();
    hoverAt(2, AT_P2);
    expect(card().style.top).toBe("60px");
    rerender(<Harness renderedWidth={450} />);
    expect(card().style.top).toBe("90px"); // P2's top at width 450
  });

  it("drops below the paragraph when the gutter is too narrow", async () => {
    await renderReady();
    stubLayout(200); // a 176px gutter
    hoverAt(2, AT_P2);
    expect(card()).toHaveAttribute("data-side", "below");
    expect(card().style.top).toBe("110px"); // P2's bottom (100) + 10
  });
});

describe("PdfTranslationLayer — pinned card stays (QA F5)", () => {
  it("docks a pinned card to the top edge when its paragraph scrolls out", async () => {
    await renderReady();
    hoverAt(2, AT_P2);
    clickAt(2, AT_P2);
    expect(card()).toHaveAttribute("data-state", "open");
    vi.restoreAllMocks();
    stubLayout(600, 2000); // P2 (top 60) is now far above the reading area
    fireEvent.scroll(screen.getByTestId("reader"));
    await waitFor(() => expect(card().style.top).toBe("2008px"));
    expect(card()).toHaveAttribute("data-state", "open");
  });

  it("an unpinned card still steps aside", async () => {
    await renderReady();
    hoverAt(2, AT_P2);
    expect(card()).toHaveAttribute("data-state", "open");
    vi.restoreAllMocks();
    stubLayout(600, 2000);
    fireEvent.scroll(screen.getByTestId("reader"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")?.getAttribute("data-state") ?? "closed").toBe("closed"),
    );
  });
});

describe("PdfTranslationLayer — first-use hint", () => {
  const HINT = "Hover a paragraph to see its translation";

  it("shows once, on the first hover ever, then fades out", async () => {
    const first = await renderReady();
    expect(screen.queryByText(HINT)).toBeNull();
    vi.useFakeTimers();
    hoverAt(1, BLANK);
    // The live region mounts empty; the text goes in after mount so it's announced.
    const region = screen.getByRole("status");
    expect(region).toHaveTextContent("");
    act(() => {
      vi.advanceTimersByTime(100);
    });
    const hint = screen.getByText(HINT);
    expect(hint.closest("[data-state]")).toHaveAttribute("data-state", "open");
    expect(useTranslationStore.getState().hintSeen).toBe(true);
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.getByText(HINT).closest("[data-state]")).toHaveAttribute(
      "data-state",
      "closed",
    );
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByText(HINT)).toBeNull();
    vi.useRealTimers();
    first.unmount();

    // A fresh reader (another paper, a reload) never shows it again.
    await renderReady();
    hoverAt(1, AT_P1);
    hoverAt(2, AT_P2);
    expect(screen.queryByText(HINT)).toBeNull();
  });
});

describe("PdfTranslationLayer — interaction fixes", () => {
  it("unpinning from the card shows the paragraph under the pointer", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1); // pinned P1
    hoverAt(2, AT_P2); // pointer now over P2; the pinned card stays on P1
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "pinned");
    fireEvent.click(screen.getByRole("button", { name: /unpin translation/i }));
    expect(fragmentsOf(P2)[0]).toHaveAttribute("data-state", "hover");
    expect(fragmentsOf(P1)[0]).not.toHaveAttribute("data-state");
    await waitFor(() => expect(card()).toHaveTextContent("第二段。"));
  });

  it("unpinning with the pointer on no paragraph schedules the hide", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    clickAt(1, AT_P1);
    hoverAt(1, BLANK);
    fireEvent.pointerLeave(screen.getByTestId("page-1"));
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: /unpin translation/i }));
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(card()).toHaveAttribute("data-state", "closed");
  });

  it("a click on a link inside the page does not pin", async () => {
    await renderReady();
    hoverAt(1, AT_P1);
    const link = screen.getByTestId("link-1");
    fireEvent.pointerDown(link, AT_P1);
    fireEvent.click(link, AT_P1);
    expect(fragmentsOf(P1)[0]).toHaveAttribute("data-state", "hover");
  });
});

describe("PdfTranslationLayer caching", () => {
  function Toggle({ enabled }: { enabled: boolean }) {
    const readerRef = useRef<HTMLDivElement>(null);
    return (
      <div ref={readerRef}>
        <PdfTranslationLayer
          paperId={7}
          language="繁體中文"
          fullPaper={false}
          enabled={enabled}
          readerRef={readerRef}
        >
          <div>
            <PdfTranslationPage pageNumber={1} renderedWidth={300} originalWidth={600} />
          </div>
        </PdfTranslationLayer>
      </div>
    );
  }

  it("fetches a paper's paragraphs once across translation toggles", async () => {
    const { rerender } = render(<Toggle enabled />);
    await waitFor(() => expect(fragmentsOf(P1)).toHaveLength(1));
    rerender(<Toggle enabled={false} />);
    expect(fragmentsOf(P1)).toHaveLength(0);
    rerender(<Toggle enabled />);
    expect(fragmentsOf(P1)).toHaveLength(1);
    expect(mockedParagraphs).toHaveBeenCalledTimes(1);
  });
});

describe("PdfTranslationLayer own paragraphs fetch", () => {
  function Reporting({ onLoaded }: { onLoaded: (r: ParagraphsResponse | null) => void }) {
    const readerRef = useRef<HTMLDivElement>(null);
    return (
      <div ref={readerRef}>
        <PdfTranslationLayer
          paperId={7}
          language="繁體中文"
          fullPaper={false}
          enabled
          readerRef={readerRef}
          onParagraphsLoaded={onLoaded}
        >
          <div />
        </PdfTranslationLayer>
      </div>
    );
  }

  it("reports a failed fetch as null so the pill can say why", async () => {
    mockedParagraphs.mockRejectedValueOnce(new Error("boom"));
    const onLoaded = vi.fn();
    render(<Reporting onLoaded={onLoaded} />);
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith(null));
  });

  it("reports the fetched response", async () => {
    mockedParagraphs.mockResolvedValueOnce(READY);
    const onLoaded = vi.fn();
    render(<Reporting onLoaded={onLoaded} />);
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith(READY));
  });
});
