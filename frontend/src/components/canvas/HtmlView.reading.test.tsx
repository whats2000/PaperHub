import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HtmlView } from "@/components/canvas/HtmlView";

const PAPER =
  '<h2 id="a">1 Introduction</h2><p>One.</p><h2 id="b">2 Method</h2><p>Two.</p><h2 id="c">3 Results</h2><p>Three.</p>';

function place(doc: Document, tops: Record<string, number>) {
  for (const [id, top] of Object.entries(tops)) {
    doc.getElementById(id)!.getBoundingClientRect = () => ({ top, bottom: top + 20 }) as DOMRect;
  }
}

describe("HtmlView reports the section being read (Follow in the notes)", () => {
  it("names the last heading scrolled to, after the scroll settles", async () => {
    const onReadingHeading = vi.fn();
    const { container } = render(
      <HtmlView
        html="<html><body></body></html>"
        isDark={false}
        highlightDomId={null}
        highlightText={null}
        sectionTitle={null}
        nonce={0}
        onReadingHeading={onReadingHeading}
      />,
    );
    const iframe = container.querySelector("iframe")!;
    const doc = iframe.contentDocument!;
    doc.body.innerHTML = PAPER;
    Object.defineProperty(iframe.contentWindow!, "innerHeight", { configurable: true, value: 800 });
    place(doc, { a: -900, b: 100, c: 700 });
    act(() => {
      iframe.dispatchEvent(new Event("load"));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(onReadingHeading).toHaveBeenLastCalledWith("2 Method", []);

    place(doc, { a: -2000, b: -1000, c: 50 });
    act(() => {
      iframe.contentWindow!.dispatchEvent(new Event("scroll"));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(onReadingHeading).toHaveBeenLastCalledWith("3 Results", []);
  });

  it("names the headings a subsection is in, outermost first", async () => {
    const onReadingHeading = vi.fn();
    const { container } = render(
      <HtmlView
        html="<html><body></body></html>"
        isDark={false}
        highlightDomId={null}
        highlightText={null}
        sectionTitle={null}
        nonce={0}
        onReadingHeading={onReadingHeading}
      />,
    );
    const iframe = container.querySelector("iframe")!;
    const doc = iframe.contentDocument!;
    doc.body.innerHTML =
      '<h1 id="a">4 Experiments</h1><h2 id="b">4.1 Setup</h2><h3 id="c">Data</h3><h2 id="d">4.2 Results</h2>';
    Object.defineProperty(iframe.contentWindow!, "innerHeight", { configurable: true, value: 800 });
    place(doc, { a: -900, b: -500, c: 100, d: 700 });
    act(() => {
      iframe.dispatchEvent(new Event("load"));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(onReadingHeading).toHaveBeenLastCalledWith("Data", ["4 Experiments", "4.1 Setup"]);
  });
});
