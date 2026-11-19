import { afterEach, describe, expect, it, vi } from "vitest";

import { scrollIntoViewStable } from "@/lib/findAndHighlight";

function rect(top: number, height: number): DOMRect {
  return { top, height, bottom: top + height, left: 0, right: 100, width: 100, x: 0, y: top, toJSON: () => ({}) };
}

/** Manual rAF: frames run only when the test says so. */
function manualFrames(win: Window): () => number {
  let queue: FrameRequestCallback[] = [];
  vi.spyOn(win, "requestAnimationFrame").mockImplementation((cb) => {
    queue.push(cb);
    return queue.length;
  });
  vi.spyOn(win, "cancelAnimationFrame").mockImplementation(() => {
    queue = [];
  });
  return () => {
    let n = 0;
    while (queue.length && n < 500) {
      const q = queue;
      queue = [];
      q.forEach((cb) => cb(0));
      n += 1;
    }
    return n;
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  delete (document as { scrollingElement?: Element }).scrollingElement;
});

describe("scrollIntoViewStable", () => {
  it("centres the target where it ends up when a placeholder above it resolves mid-glide", () => {
    const run = manualFrames(window);
    Object.defineProperty(document, "scrollingElement", { value: document.documentElement, configurable: true });
    const sc = document.scrollingElement!;
    sc.scrollTop = 0;
    const el = document.createElement("p");
    document.body.appendChild(el);
    let docTop = 2000;
    let calls = 0;
    el.getBoundingClientRect = () => {
      calls += 1;
      if (calls === 4) docTop = 2500; // a content-visibility placeholder above rendered
      return rect(docTop - sc.scrollTop, 20);
    };
    scrollIntoViewStable(el, "smooth");
    run();
    expect(Math.abs(docTop - sc.scrollTop + 10 - window.innerHeight / 2)).toBeLessThanOrEqual(1);
  });

  it("moves only an inner scroller's scrollTop, never sideways or by scrollIntoView", () => {
    const run = manualFrames(window);
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    document.body.innerHTML = `<div id="sc" style="overflow-y:auto"><span id="t">x</span></div>`;
    const sc = document.getElementById("sc")!;
    const t = document.getElementById("t")!;
    sc.scrollLeft = 0;
    sc.getBoundingClientRect = () => rect(100, 400);
    let docTop = 900;
    let calls = 0;
    t.getBoundingClientRect = () => {
      calls += 1;
      if (calls === 3) docTop = 1300;
      return rect(100 + docTop - sc.scrollTop, 20);
    };
    scrollIntoViewStable(t, "smooth", { scroller: sc });
    run();
    // the target's centre sits at the scroller's centre (100 + 200)
    expect(Math.abs(100 + docTop - sc.scrollTop + 10 - 300)).toBeLessThanOrEqual(1);
    expect(sc.scrollLeft).toBe(0);
    expect(spy).not.toHaveBeenCalled();
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });

  it("with reduced motion jumps straight, then keeps correcting until the target holds still", () => {
    const run = manualFrames(window);
    document.body.innerHTML = `<div id="sc" style="overflow-y:auto"><span id="t">x</span></div>`;
    const sc = document.getElementById("sc")!;
    const t = document.getElementById("t")!;
    sc.getBoundingClientRect = () => rect(0, 400);
    let docTop = 900;
    const seen: number[] = [];
    let calls = 0;
    t.getBoundingClientRect = () => {
      calls += 1;
      if (calls === 2) docTop = 1500; // shifts right after the jump
      return rect(docTop - sc.scrollTop, 20);
    };
    const onFrame = () => seen.push(sc.scrollTop);
    scrollIntoViewStable(t, "auto", { scroller: sc, reduced: true, onFrame });
    run();
    // first correction is a full jump (no easing)
    expect(seen[1]).toBe(900 + 10 - 200);
    expect(sc.scrollTop).toBe(1500 + 10 - 200);
  });
});
