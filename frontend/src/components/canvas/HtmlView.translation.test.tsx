import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TranslateRequest } from "@/types/translation";

const translateBlocks = vi.fn((req: TranslateRequest) =>
  Promise.resolve({
    results: req.blocks.map((b) => ({
      key: b.key,
      status: "ok" as const,
      text: `譯:${b.text}`,
      model: "m",
      stale_terminology: false,
    })),
  }),
);
vi.mock("@/lib/api", () => ({
  translateBlocks: (req: TranslateRequest) => translateBlocks(req),
}));

import { HtmlView } from "@/components/canvas/HtmlView";

const PAPER =
  '<h1 id="s1">Intro</h1>' +
  '<p><span id="phchunk-0"></span>Let <span class="math inline">x</span> be.</p>' +
  "<ul><li><p>An item.</p></li></ul>";

function renderView(translation?: {
  enabled: boolean;
  language: string;
  paperId: number;
  fullPaper: boolean;
}) {
  const utils = render(
    <HtmlView
      html="<html><body></body></html>"
      isDark={false}
      highlightDomId={null}
      highlightText={null}
      sectionTitle={null}
      nonce={0}
      translation={translation}
    />,
  );
  const iframe = utils.container.querySelector("iframe")!;
  const doc = iframe.contentDocument!;
  doc.body.innerHTML = PAPER;
  return { ...utils, iframe, doc };
}

const load = async (iframe: HTMLIFrameElement) => {
  act(() => {
    iframe.dispatchEvent(new Event("load"));
  });
  // jsdom may also fire its own srcdoc load; let any re-attach settle and the
  // superseded overlay finish collapsing.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 300));
  });
};

const flushCollapse = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 300));
  });
};

const tr = { enabled: true, language: "繁體中文", paperId: 3, fullPaper: false };

beforeEach(() => {
  translateBlocks.mockClear();
});

describe("HtmlView translation overlay", () => {
  it("stays inert when translation is disabled", async () => {
    const { iframe, doc } = renderView({ ...tr, enabled: false });
    await load(iframe);
    expect(translateBlocks).not.toHaveBeenCalled();
    expect(doc.querySelector(".ph-tr")).toBeNull();
    expect(doc.getElementById("ph-tr-style")).toBeNull();
  });

  it("translates the blocks and renders each under its block", async () => {
    const { iframe, doc } = renderView(tr);
    const before = Array.from(doc.querySelectorAll("h1, p, li")).map(
      (e) => e.outerHTML,
    );
    await load(iframe);
    expect(translateBlocks).toHaveBeenCalledTimes(1);
    const req = translateBlocks.mock.calls[0]![0];
    expect(req.paper_content_id).toBe(3);
    expect(req.language).toBe("繁體中文");
    expect(req.blocks).toEqual([
      { key: "h:0", text: "Intro" },
      { key: "h:1", text: "Let ⟦0⟧ be." },
      { key: "h:2", text: "An item." },
    ]);
    const boxes = Array.from(doc.querySelectorAll(".ph-tr"));
    expect(boxes.map((b) => b.previousElementSibling!.tagName)).toEqual([
      "H1",
      "P",
      "LI",
    ]);
    expect(boxes[1]!.querySelector("span.math.inline")!.textContent).toBe("x");
    // The paper's own nodes are untouched — only siblings were inserted.
    expect(
      Array.from(doc.querySelectorAll("h1, p, li"))
        .filter((e) => !e.closest(".ph-tr"))
        .map((e) => e.outerHTML),
    ).toEqual(before);
  });

  it("collapses the translations away when turned off", async () => {
    const { iframe, doc, rerender } = renderView(tr);
    await load(iframe);
    expect(doc.querySelectorAll(".ph-tr")).toHaveLength(3);
    rerender(
      <HtmlView
        html="<html><body></body></html>"
        isDark={false}
        highlightDomId={null}
        highlightText={null}
        sectionTitle={null}
        nonce={0}
        translation={{ ...tr, enabled: false }}
      />,
    );
    // Collapsing (data-open removed) before it detaches.
    const leaving = Array.from(doc.querySelectorAll(".ph-tr"));
    expect(leaving).toHaveLength(3);
    leaving.forEach((b) => expect(b.hasAttribute("data-open")).toBe(false));
    await flushCollapse();
    expect(doc.querySelector(".ph-tr")).toBeNull();
    expect(doc.body.innerHTML).toBe(PAPER);
  });

  it("never attaches to a superseded document when html and language change together", async () => {
    const { iframe, doc, rerender } = renderView(tr);
    await load(iframe);
    translateBlocks.mockClear();
    rerender(
      <HtmlView
        html="<html><body><p>Another paper.</p></body></html>"
        isDark={false}
        highlightDomId={null}
        highlightText={null}
        sectionTitle={null}
        nonce={0}
        translation={{ ...tr, language: "日本語", paperId: 4 }}
      />,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    // Nothing was requested for paper 4 against the OLD paper's blocks.
    const oldDocCalls = translateBlocks.mock.calls.filter(
      ([req]) => req.paper_content_id === 4 && req.blocks.some((b) => b.text === "An item."),
    );
    expect(oldDocCalls).toHaveLength(0);
    expect(doc.querySelector('.ph-tr:not([data-ph-tr-leaving])')).toBeNull();
  });
});
