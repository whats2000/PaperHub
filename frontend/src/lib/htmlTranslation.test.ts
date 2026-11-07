import { afterEach, describe, expect, it, vi } from "vitest";

import {
  TR_STYLE_ID,
  attachHtmlTranslation,
  extractBlocks,
  hasTranslatableBlocks,
  removeTranslations,
  renderTranslation,
} from "@/lib/htmlTranslation";
import type { TranslateRequest, TranslateResponse } from "@/types/translation";

function makeDoc(body: string): Document {
  const doc = document.implementation.createHTMLDocument("paper");
  doc.body.innerHTML = body;
  return doc;
}

const ok = (key: string, text: string) => ({
  key,
  status: "ok" as const,
  text,
  model: "m",
  stale_terminology: false,
});

afterEach(() => {
  vi.useRealTimers();
});

describe("extractBlocks", () => {
  it("replaces inline math with a placeholder and keeps the node", () => {
    const doc = makeDoc(
      '<p>Let <span class="math inline">\\(x\\)</span> be the input.</p>',
    );
    const [b] = extractBlocks(doc);
    expect(b!.key).toBe("h:0");
    expect(b!.text).toBe("Let ⟦0⟧ be the input.");
    expect(b!.protectedNodes[0]).toBe(doc.querySelector("span.math"));
  });

  it("protects reference anchors, citations and inline code in order", () => {
    const doc = makeDoc(
      '<p>See <a href="#sec:2" data-reference-type="ref">2</a> and ' +
        '<span class="citation" data-cites="v17">[1]</span> using <code>f()</code>.</p>',
    );
    const [b] = extractBlocks(doc);
    expect(b!.text).toBe("See ⟦0⟧ and ⟦1⟧ using ⟦2⟧.");
    expect(b!.protectedNodes.map((n) => (n as Element).tagName)).toEqual([
      "A",
      "SPAN",
      "CODE",
    ]);
  });

  it("drops chunk anchors and PHCHUNKANCHOR remnants from the text", () => {
    const doc = makeDoc(
      '<p>Alpha <span id="phchunk-3"></span>beta PHCHUNKANCHOR3END gamma 12END delta.</p>',
    );
    const [b] = extractBlocks(doc);
    expect(b!.text).toBe("Alpha beta gamma delta.");
    expect(b!.text).not.toMatch(/phchunk|PHCHUNKANCHOR|END/);
    expect(b!.protectedNodes).toEqual([]);
  });

  it("yields one block for a nested li > p", () => {
    const doc = makeDoc("<ul><li><p>Only one block here.</p></li></ul>");
    const blocks = extractBlocks(doc);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.el.tagName).toBe("LI");
    expect(blocks[0]!.text).toBe("Only one block here.");
  });

  it("skips equation-only paragraphs (pandoc display math), pre and math-only blocks", () => {
    const doc = makeDoc(
      '<h1 id="s1">Intro</h1>' +
        '<p><span class="math display">\\[\\mathrm{Attention}(Q, K, V) = \\mathrm{softmax}(QK^T)V\\]</span></p>' +
        "<pre><p>inside pre</p></pre>" +
        '<p><span class="math inline">\\(y\\)</span></p>' +
        "<figcaption>A caption.</figcaption>",
    );
    const blocks = extractBlocks(doc);
    expect(blocks.map((b) => b.text)).toEqual(["Intro", "A caption."]);
    // Keys index document order among candidate blocks, so they stay stable.
    expect(blocks.map((b) => b.key)).toEqual(["h:0", "h:3"]);
  });

  it("protects display math inside a mixed paragraph", () => {
    const doc = makeDoc(
      '<p>where <span class="math display">\\[\\mathrm{FFN}(x) = \\max(0, xW_1)\\]</span> holds.</p>',
    );
    const [b] = extractBlocks(doc);
    expect(b!.text).toBe("where ⟦0⟧ holds.");
    expect(b!.text).not.toMatch(/\\|FFN/);
    expect((b!.protectedNodes[0] as Element).className).toBe("math display");
  });

  it("strips bare <n>END remnants only at a word start", () => {
    const doc = makeDoc("<p>Model X2END wins 3END here.</p>");
    expect(extractBlocks(doc)[0]!.text).toBe("Model X2END wins here.");
  });

  it("ignores previously inserted translation nodes", () => {
    const doc = makeDoc("<p>One.</p><p>Two.</p>");
    const first = extractBlocks(doc);
    renderTranslation(first[0]!, ok("h:0", "Uno."));
    const again = extractBlocks(doc);
    expect(again.map((b) => [b.key, b.text])).toEqual([
      ["h:0", "One."],
      ["h:1", "Two."],
    ]);
  });
});

describe("renderTranslation", () => {
  it("inserts a sibling after the block with cloned math", () => {
    const doc = makeDoc(
      '<p id="p1">Let <span class="math inline"><mjx-container>x</mjx-container></span> be.</p><p>Next.</p>',
    );
    const [b] = extractBlocks(doc);
    const box = renderTranslation(b!, ok("h:0", "令 ⟦0⟧ 為輸入。"));
    expect(box.previousElementSibling).toBe(b!.el);
    expect(box.classList.contains("ph-tr")).toBe(true);
    expect(box.getAttribute("data-ph-tr-key")).toBe("h:0");
    const clone = box.querySelector("span.math.inline");
    expect(clone).not.toBeNull();
    expect(clone).not.toBe(b!.protectedNodes[0]);
    expect(clone!.querySelector("mjx-container")!.textContent).toBe("x");
    expect(box.textContent).toBe("令 x 為輸入。");
    expect(doc.getElementById(TR_STYLE_ID)).not.toBeNull();
  });

  it("leaves every original block's markup unchanged", () => {
    const doc = makeDoc(
      '<h2>Sec</h2><p>A <span class="math inline">\\(a\\)</span> ' +
        '<span id="phchunk-1"></span>b <a href="#r1">[1]</a>.</p>' +
        "<ul><li><p>item</p></li></ul><figcaption>cap</figcaption>",
    );
    const blocks = extractBlocks(doc);
    const before = blocks.map((b) => b.el.outerHTML);
    for (const b of blocks) renderTranslation(b, ok(b.key, `T ${b.text}`));
    expect(blocks.map((b) => b.el.outerHTML)).toEqual(before);
    expect(doc.querySelectorAll(".ph-tr")).toHaveLength(blocks.length);
    // Clones never duplicate an id (chunk anchors stay unique).
    expect(doc.querySelectorAll("#phchunk-1")).toHaveLength(1);
  });

  it("renders a spinner while pending, then updates in place", () => {
    const doc = makeDoc("<p>Hello.</p>");
    const [b] = extractBlocks(doc);
    const pending = renderTranslation(b!, "pending", {
      pending: "Translating…",
      unavailable: "Translation unavailable",
      retry: "Retry",
    });
    expect(pending.querySelector(".ph-tr-spin")).not.toBeNull();
    expect(pending.textContent).toContain("Translating…");
    const done = renderTranslation(b!, ok("h:0", "你好。"));
    expect(done).toBe(pending);
    expect(doc.querySelectorAll(".ph-tr")).toHaveLength(1);
    expect(done.textContent).toBe("你好。");
  });

  it("renders the muted unavailable label", () => {
    const doc = makeDoc("<p>Hello.</p>");
    const [b] = extractBlocks(doc);
    const box = renderTranslation(b!, "unavailable");
    expect(box.getAttribute("data-ph-tr-state")).toBe("unavailable");
    expect(box.querySelector(".ph-tr-muted")!.textContent).toBe(
      "Translation unavailable",
    );
  });

  it("treats an ok result with null text as unavailable", () => {
    const doc = makeDoc("<p>Hello.</p>");
    const [b] = extractBlocks(doc);
    const box = renderTranslation(b!, { ...ok("h:0", ""), text: null });
    expect(box.getAttribute("data-ph-tr-state")).toBe("unavailable");
  });

  it("opens fresh boxes together in one frame (collapse motion)", () => {
    vi.useFakeTimers();
    const doc = makeDoc("<p>Hello.</p><p>World.</p>");
    const [a, b] = extractBlocks(doc);
    const boxA = renderTranslation(a!, "pending");
    const boxB = renderTranslation(b!, "pending");
    // Inserted closed (0fr); data-open lands in the next frame for both.
    expect(boxA.hasAttribute("data-open")).toBe(false);
    expect(boxB.hasAttribute("data-open")).toBe(false);
    vi.advanceTimersByTime(20);
    expect(boxA.hasAttribute("data-open")).toBe(true);
    expect(boxB.hasAttribute("data-open")).toBe(true);
    const css = doc.getElementById(TR_STYLE_ID)!.textContent;
    expect(css).toContain("grid-template-rows");
    expect(css).toContain("prefers-reduced-motion");
    expect(css).not.toMatch(/#fde68a|yellow/i);
  });
});

describe("removeTranslations", () => {
  it("collapses, then detaches every translation", () => {
    vi.useFakeTimers();
    const doc = makeDoc("<p>One.</p><p>Two.</p>");
    for (const b of extractBlocks(doc)) renderTranslation(b, ok(b.key, "x"));
    removeTranslations(doc);
    const boxes = doc.querySelectorAll(".ph-tr");
    expect(boxes).toHaveLength(2);
    boxes.forEach((el) => expect(el.hasAttribute("data-open")).toBe(false));
    vi.advanceTimersByTime(300);
    expect(doc.querySelectorAll(".ph-tr")).toHaveLength(0);
    expect(doc.body.innerHTML).toBe("<p>One.</p><p>Two.</p>");
  });

  it("a leaving box is not reused by a new render", () => {
    vi.useFakeTimers();
    const doc = makeDoc("<p>One.</p>");
    const [b] = extractBlocks(doc);
    const old = renderTranslation(b!, ok("h:0", "old"));
    removeTranslations(doc);
    const fresh = renderTranslation(b!, ok("h:0", "new"));
    expect(fresh).not.toBe(old);
    vi.advanceTimersByTime(300);
    expect(doc.querySelectorAll(".ph-tr")).toHaveLength(1);
    expect(doc.querySelector(".ph-tr")!.textContent).toBe("new");
  });
});

describe("attachHtmlTranslation", () => {
  it("feeds visible blocks to the queue and renders results", async () => {
    const doc = makeDoc('<p>Let <span class="math inline">m</span> go.</p><p>Two.</p>');
    const requests: TranslateRequest[] = [];
    const send = (req: TranslateRequest): Promise<TranslateResponse> => {
      requests.push(req);
      return Promise.resolve({
        results: req.blocks.map((b) => ok(b.key, `T(${b.text})`)),
      });
    };
    let ioCb: IntersectionObserverCallback | null = null;
    const observed: Element[] = [];
    class FakeIO {
      constructor(cb: IntersectionObserverCallback) {
        ioCb = cb;
      }
      observe(el: Element) {
        observed.push(el);
      }
      disconnect() {}
      unobserve() {}
    }
    const detach = attachHtmlTranslation(doc, {
      paperId: 7,
      language: "日本語",
      fullPaper: false,
      send,
      IntersectionObserver: FakeIO as unknown as typeof IntersectionObserver,
    });
    expect(observed.map((e) => e.tagName)).toEqual(["P", "P"]);
    expect(requests).toHaveLength(0);
    ioCb!(
      [{ target: observed[0]!, isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
    // Visible block shows a pending spinner right away.
    expect(doc.querySelector('.ph-tr[data-ph-tr-key="h:0"] .ph-tr-spin')).not.toBeNull();
    expect(requests[0]!.paper_content_id).toBe(7);
    expect(requests[0]!.blocks[0]).toEqual({ key: "h:0", text: "Let ⟦0⟧ go." });
    await new Promise((r) => setTimeout(r, 0));
    const box = doc.querySelector('.ph-tr[data-ph-tr-key="h:0"]')!;
    expect(box.textContent).toBe("T(Let m go.)");
    detach();
  });

  it("an unavailable block offers Retry, which re-requests it", async () => {
    const doc = makeDoc("<p>Hello there.</p>");
    let fail = true;
    const requests: TranslateRequest[] = [];
    const send = (req: TranslateRequest): Promise<TranslateResponse> => {
      requests.push(req);
      return Promise.resolve({
        results: req.blocks.map((b) =>
          fail
            ? { key: b.key, status: "unavailable" as const, text: null, model: null, stale_terminology: false }
            : ok(b.key, "你好。"),
        ),
      });
    };
    const detach = attachHtmlTranslation(doc, {
      paperId: 1,
      language: "繁體中文",
      fullPaper: true,
      send,
      labels: { pending: "P", unavailable: "U", retry: "Again" },
    });
    await new Promise((r) => setTimeout(r, 0));
    const box = doc.querySelector<HTMLElement>(".ph-tr")!;
    expect(box.getAttribute("data-ph-tr-state")).toBe("unavailable");
    const button = box.querySelector<HTMLButtonElement>("button.ph-tr-retry")!;
    expect(button.textContent).toBe("Again");
    fail = false;
    button.click();
    expect(box.getAttribute("data-ph-tr-state")).toBe("pending");
    await new Promise((r) => setTimeout(r, 0));
    expect(requests).toHaveLength(2);
    expect(box.textContent).toBe("你好。");
    detach();
  });
});

describe("attachHtmlTranslation: stale terminology", () => {
  it("reports stale visible results and re-translates them with force, keeping text", async () => {
    const doc = makeDoc("<p>One.</p><p>Two.</p>");
    const requests: TranslateRequest[] = [];
    let reply: "stale" | "fresh" | "fail" = "stale";
    const send = (req: TranslateRequest): Promise<TranslateResponse> => {
      requests.push(req);
      if (reply === "fail") return Promise.reject(new Error("down"));
      return Promise.resolve({
        results: req.blocks.map((b) => ({
          ...ok(b.key, `${reply}:${b.text}`),
          stale_terminology: reply === "stale" && b.key === "h:0",
        })),
      });
    };
    const stale: boolean[] = [];
    const handle = attachHtmlTranslation(doc, {
      paperId: 1,
      language: "日本語",
      fullPaper: true,
      send,
      onStaleChange: (s) => stale.push(s),
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(stale).toEqual([true]);
    const box0 = doc.querySelector<HTMLElement>('.ph-tr[data-ph-tr-key="h:0"]')!;
    expect(box0.textContent).toBe("stale:One.");

    // A failed forced re-send keeps the old text on the page, still stale.
    reply = "fail";
    handle.retranslate();
    expect(box0.textContent).toBe("stale:One.");
    await new Promise((r) => setTimeout(r, 0));
    expect(requests.at(-1)!.force).toBe(true);
    expect(requests.at(-1)!.blocks.map((b) => b.key)).toEqual(["h:0"]);
    expect(box0.getAttribute("data-ph-tr-state")).toBe("ok");
    expect(box0.textContent).toBe("stale:One.");
    expect(stale.at(-1)).toBe(true);

    reply = "fresh";
    handle.retranslate();
    await new Promise((r) => setTimeout(r, 0));
    expect(box0.textContent).toBe("fresh:One.");
    expect(stale.at(-1)).toBe(false);
    handle();
  });

  it("only visible blocks count toward stale", async () => {
    const doc = makeDoc("<p>One.</p><p>Two.</p>");
    const send = (req: TranslateRequest): Promise<TranslateResponse> =>
      Promise.resolve({
        results: req.blocks.map((b) => ({ ...ok(b.key, "t"), stale_terminology: b.key === "h:1" })),
      });
    let ioCb: IntersectionObserverCallback | null = null;
    const observed: Element[] = [];
    class FakeIO {
      constructor(cb: IntersectionObserverCallback) {
        ioCb = cb;
      }
      observe(el: Element) {
        observed.push(el);
      }
      disconnect() {}
      unobserve() {}
    }
    const stale: boolean[] = [];
    const handle = attachHtmlTranslation(doc, {
      paperId: 1,
      language: "日本語",
      fullPaper: true,
      send,
      onStaleChange: (s) => stale.push(s),
      IntersectionObserver: FakeIO as unknown as typeof IntersectionObserver,
    });
    const see = (i: number, on: boolean) =>
      ioCb!(
        [{ target: observed[i]!, isIntersecting: on } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    see(0, true);
    await new Promise((r) => setTimeout(r, 0));
    expect(stale.at(-1) ?? false).toBe(false);
    see(1, true);
    expect(stale.at(-1)).toBe(true);
    see(1, false);
    expect(stale.at(-1)).toBe(false);
    handle();
  });
});

describe("extractBlocks: references and bylines (QA F1)", () => {
  it("skips author/date lines, bibliographies and the References section", () => {
    const doc = makeDoc(
      '<h1 class="title">Attention</h1>' +
        '<p class="author">Ashish Vaswani</p>' +
        '<p class="date">June 2017</p>' +
        "<p>Shanghai Institute of Optics, Chinese Academy of Sciences, Shanghai 201800, China</p>" +
        "<p>* Correspondence: sshan@mail.shcnc.ac.cn</p>" +
        "<p>doi:10.3390/app8081379</p>" +
        "<p>Keywords: Deep Learning, Transformer Models</p>" +
        "<h2>Results</h2>" +
        "<p>The model wins on every benchmark we tried.</p>" +
        '<h2 id="references">References</h2>' +
        "<ol><li><p>A. Vaswani et al. Attention is all you need. In NeurIPS, 2017.</p></li></ol>" +
        "<p>Jimmy Ba, Jamie Kiros. Layer normalization. 2016.</p>" +
        "<h2>Appendix</h2>" +
        "<p>Extra tables follow here.</p>" +
        '<div class="thebibliography"><p>Dzmitry Bahdanau and Yoshua Bengio. Neural machine translation by jointly learning to align.</p></div>',
    );
    const texts = extractBlocks(doc).map((b) => b.text);
    expect(texts).toEqual([
      "Attention",
      "Keywords: Deep Learning, Transformer Models",
      "Results",
      "The model wins on every benchmark we tried.",
      "Appendix",
      "Extra tables follow here.",
    ]);
  });

  it("keeps keys stable: a skipped block still takes its index", () => {
    const doc = makeDoc(
      '<p class="author">Yi Tay</p><p>First real paragraph here.</p>',
    );
    expect(extractBlocks(doc).map((b) => b.key)).toEqual(["h:1"]);
  });
});

describe("hasTranslatableBlocks (QA B2)", () => {
  it("is false for a raw-LaTeX <pre> fallback render", () => {
    expect(hasTranslatableBlocks(makeDoc("<pre>\\section{Intro} text</pre>"))).toBe(false);
    expect(hasTranslatableBlocks(makeDoc("<p>Some prose.</p>"))).toBe(true);
  });
});

describe("translation text contrast (QA F4)", () => {
  it("renders the translation darker than the muted pending/unavailable states", () => {
    const doc = makeDoc("<p>Hello.</p>");
    renderTranslation(extractBlocks(doc)[0]!, ok("h:0", "你好。"));
    const css = doc.getElementById(TR_STYLE_ID)!.textContent;
    const inner = css.slice(css.indexOf(".ph-tr-inner {"));
    expect(inner.slice(0, inner.indexOf("}"))).not.toContain("oklch(0.556 0 0)");
    expect(css).toMatch(/data-ph-tr-state="pending"[^{]*\{[^}]*oklch\(0\.556 0 0\)/);
  });
});

describe("scroll position (QA F7)", () => {
  /** A document with a window (an iframe's), as in the Citation Canvas. */
  function frameDoc(body: string): Document {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    const doc = frame.contentDocument!;
    doc.body.innerHTML = body;
    return doc;
  }

  function stubLayout(doc: Document, blockBottom: number) {
    const win = doc.defaultView!;
    const anchor = doc.createElement("p");
    anchor.textContent = "anchor";
    doc.body.appendChild(anchor);
    let anchorTop = 100;
    anchor.getBoundingClientRect = () => ({ top: anchorTop } as DOMRect);
    doc.elementFromPoint = () => anchor;
    const first = doc.body.firstElementChild!;
    first.getBoundingClientRect = () => ({ top: blockBottom - 40, bottom: blockBottom } as DOMRect);
    const scrollBy = vi.fn();
    win.scrollBy = scrollBy;
    return { scrollBy, grow: (px: number) => (anchorTop += px) };
  }

  it("opens a box above the viewport at once and scrolls by the shift", () => {
    const doc = frameDoc("<p>Above the fold.</p>");
    const { scrollBy, grow } = stubLayout(doc, -50);
    const [block] = extractBlocks(doc);
    // The insert pushes the anchor down by 60px; the view follows it.
    const origAfter = block!.el.after.bind(block!.el);
    block!.el.after = (...n: (string | Node)[]) => {
      origAfter(...n);
      grow(60);
    };
    const box = renderTranslation(block!, ok("h:0", "摺疊上方"));
    expect(box.hasAttribute("data-open")).toBe(true);
    expect(scrollBy).toHaveBeenCalledWith(0, 60);
  });

  it("holds an on-screen cited highlight still while boxes above it open", () => {
    const doc = frameDoc('<p>Above the citation.</p><p><span class="ph-cite-hl">cited</span></p>');
    const win = doc.defaultView!;
    const [first] = extractBlocks(doc);
    const hl = doc.querySelector(".ph-cite-hl")!;
    let hlTop = 444;
    hl.getBoundingClientRect = () => ({ top: hlTop, bottom: hlTop + 20 } as DOMRect);
    first!.el.getBoundingClientRect = () => ({ top: 150, bottom: 200 } as DOMRect);
    const scrollBy = vi.fn();
    win.scrollBy = scrollBy;
    const origAfter = first!.el.after.bind(first!.el);
    first!.el.after = (...n: (string | Node)[]) => {
      origAfter(...n);
      hlTop += 306; // the box pushes the highlight down (444 -> 750)
    };
    const box = renderTranslation(first!, ok("h:0", "引文上方"));
    expect(box.hasAttribute("data-open")).toBe(true);
    expect(scrollBy).toHaveBeenCalledWith(0, 306);
  });

  it("animates a box in view and leaves the scroll alone", () => {
    vi.useFakeTimers();
    const doc = frameDoc("<p>In view.</p>");
    const { scrollBy } = stubLayout(doc, 300);
    const box = renderTranslation(extractBlocks(doc)[0]!, ok("h:0", "可見"));
    expect(box.hasAttribute("data-open")).toBe(false);
    expect(scrollBy).not.toHaveBeenCalled();
  });
});

describe("attachHtmlTranslation: retry all failed (QA F2)", () => {
  it("reports failures and retries them all at once", async () => {
    const doc = makeDoc("<p>Hello there.</p><p>Second one here.</p>");
    let fail = true;
    const send = (req: TranslateRequest): Promise<TranslateResponse> =>
      Promise.resolve({
        results: req.blocks.map((b) =>
          fail
            ? { key: b.key, status: "unavailable" as const, text: null, model: null, stale_terminology: false }
            : ok(b.key, "好。"),
        ),
      });
    const statuses: { failed: number; paused: boolean }[] = [];
    const handle = attachHtmlTranslation(doc, {
      paperId: 1,
      language: "繁體中文",
      fullPaper: true,
      send,
      onStatusChange: (s) => statuses.push(s),
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(statuses.at(-1)).toEqual({ failed: 2, paused: false });
    fail = false;
    handle.retryFailed();
    doc.querySelectorAll(".ph-tr").forEach((b) =>
      expect(b.getAttribute("data-ph-tr-state")).toBe("pending"),
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(statuses.at(-1)).toEqual({ failed: 0, paused: false });
    expect([...doc.querySelectorAll(".ph-tr")].map((b) => b.textContent)).toEqual(["好。", "好。"]);
    handle();
  });
});
