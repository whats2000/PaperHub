import { afterEach, describe, expect, it, vi } from "vitest";
import { resetHorizontalScroll, scrollWithin } from "./scrollWithin";

function rect(top: number, height: number): DOMRect {
  return { top, height, bottom: top + height, left: 0, right: 100, width: 100, x: 0, y: top, toJSON: () => ({}) };
}

afterEach(() => {
  document.body.innerHTML = "";
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

describe("scrollWithin", () => {
  it("moves only the nearest vertical scroller, never scrollIntoView", () => {
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    document.body.innerHTML = `
      <div id="clip" style="overflow:hidden"><div id="track">
        <div id="sc" style="overflow-y:auto"><div><span id="t">x</span></div></div>
      </div></div>`;
    const clip = document.getElementById("clip")!;
    const sc = document.getElementById("sc")!;
    const t = document.getElementById("t")!;
    sc.getBoundingClientRect = () => rect(100, 400);
    t.getBoundingClientRect = () => rect(900, 20);
    sc.scrollTop = 50;
    scrollWithin(t, { block: "center", behavior: "auto" });
    // 50 + (900 - 100) - (400 - 20) / 2
    expect(sc.scrollTop).toBe(660);
    expect(spy).not.toHaveBeenCalled();
    expect(clip.scrollLeft).toBe(0);
  });

  it("block start puts the target at the scroller's top", () => {
    document.body.innerHTML = `<div id="sc" style="overflow:auto"><p id="t">x</p></div>`;
    const sc = document.getElementById("sc")!;
    const t = document.getElementById("t")!;
    sc.getBoundingClientRect = () => rect(0, 300);
    t.getBoundingClientRect = () => rect(500, 10);
    scrollWithin(t, { block: "start", behavior: "auto" });
    expect(sc.scrollTop).toBe(500);
  });
});

describe("resetHorizontalScroll", () => {
  it("puts the element and its ancestors back at scrollLeft 0", () => {
    document.body.innerHTML = `<div id="a"><div id="b"><div id="c"></div></div></div>`;
    const a = document.getElementById("a")!;
    const c = document.getElementById("c")!;
    a.scrollLeft = 300;
    c.scrollLeft = 40;
    resetHorizontalScroll(c);
    expect(a.scrollLeft).toBe(0);
    expect(c.scrollLeft).toBe(0);
  });
});
