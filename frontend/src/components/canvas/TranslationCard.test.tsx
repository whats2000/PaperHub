import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TranslationCard, type CardScroll } from "@/components/canvas/TranslationCard";
import type { CardPlacement } from "@/lib/cardPlacement";

const PLACEMENT: CardPlacement = {
  side: "left",
  top: 100,
  left: 100,
  width: 400,
  maxHeight: 300,
  arrowY: 20,
  arrowX: 0,
  activeFragment: 0,
  scrollFraction: 0.5,
};

const noop = () => undefined;

function Card({ placement, scroll }: { placement: CardPlacement; scroll: CardScroll }) {
  return (
    <TranslationCard
      open
      contentKey="p:1"
      language="繁體中文"
      status="ok"
      html="<p>long translation</p>"
      pinned
      placement={placement}
      scroll={scroll}
      onTogglePin={noop}
      onClose={noop}
      onRetry={noop}
      onMouseEnter={noop}
      onMouseLeave={noop}
    />
  );
}

/** jsdom has no layout: a 1000px-tall content in a 200px viewport, real scrollTop. */
const scrollTops = new WeakMap<Element, number>();
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(1000);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(200);
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get(this: Element) {
      return scrollTops.get(this) ?? 0;
    },
    set(this: Element, v: number) {
      scrollTops.set(this, v);
    },
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  delete (HTMLElement.prototype as { scrollTop?: number }).scrollTop;
});

const content = () =>
  screen.getByRole("dialog").querySelector<HTMLElement>("[data-card-content]")!;
const at = (fraction: number): CardScroll => ({ fraction, activeFragment: 0, perFragment: true });

describe("TranslationCard scroll sync", () => {
  it("scrolls the content to the reader's progress", () => {
    render(<Card placement={PLACEMENT} scroll={at(0.5)} />);
    expect(content().scrollTop).toBe(400); // half of the 800px range
  });

  it("a same-fraction re-placement doesn't reset a manual scroll", () => {
    const { rerender } = render(<Card placement={PLACEMENT} scroll={at(0.5)} />);
    // The user scrolls the card by hand.
    fireEvent.wheel(content());
    content().scrollTop = 120;
    // The reader scrolls a hair / resizes: new placement objects, same progress.
    rerender(<Card placement={{ ...PLACEMENT, top: 101 }} scroll={at(0.505)} />);
    rerender(<Card placement={{ ...PLACEMENT, top: 102 }} scroll={at(0.51)} />);
    expect(content().scrollTop).toBe(120);
    // Real reading progress (> 2 %) resumes the sync.
    rerender(<Card placement={{ ...PLACEMENT, top: 103 }} scroll={at(0.6)} />);
    expect(content().scrollTop).toBe(480);
  });

  it("doesn't re-apply an unchanged sync over a manual scroll even without a wheel", () => {
    const { rerender } = render(<Card placement={PLACEMENT} scroll={at(0.5)} />);
    content().scrollTop = 50; // e.g. a scrollbar drag
    rerender(<Card placement={{ ...PLACEMENT, top: 104 }} scroll={at(0.5)} />);
    expect(content().scrollTop).toBe(50);
  });
});
