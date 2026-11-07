import { afterEach, describe, expect, it, vi } from "vitest";

import { prefersReducedMotion, revealInScroll } from "./motion";

const original = window.matchMedia;

function stubReduced(reduced: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduced,
    media: query,
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => {
  window.matchMedia = original;
});

describe("motion", () => {
  it("revealInScroll uses smooth unless reduced motion", () => {
    const scroll = vi.fn();
    const el = { scrollIntoView: scroll } as unknown as Element;
    stubReduced(false);
    revealInScroll(el);
    expect(scroll).toHaveBeenLastCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
    stubReduced(true);
    revealInScroll(el, "end");
    expect(scroll).toHaveBeenLastCalledWith({
      block: "end",
      behavior: "auto",
    });
  });

  it("prefersReducedMotion is false without matchMedia", () => {
    (window as unknown as { matchMedia?: unknown }).matchMedia = undefined;
    expect(prefersReducedMotion()).toBe(false);
  });
});
