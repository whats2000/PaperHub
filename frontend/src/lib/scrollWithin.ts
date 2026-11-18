/**
 * Reveal an element by moving ONLY its own vertical scroller.
 *
 * `Element.scrollIntoView` also scrolls every scrollable ancestor, including
 * `overflow: hidden` ones. The reader's HTML and PDF views sit side by side on
 * a 200%-wide track inside an overflow-hidden box; revealing content in the
 * half that is off-screen made the browser scroll that box sideways, and
 * nothing scrolls it back (the reader stayed half HTML, half PDF). So the
 * reader reveals by setting its scroller's `scrollTop` from the rects.
 */

function isScroller(el: Element): boolean {
  const style = el.ownerDocument.defaultView?.getComputedStyle(el);
  const scrolls = (v: string | undefined) => v === "auto" || v === "scroll";
  return scrolls(style?.overflowY) || scrolls(style?.overflow);
}

/** The nearest ancestor that scrolls vertically (else the document's). */
export function verticalScroller(el: Element): Element | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (isScroller(p)) return p;
  }
  return el.ownerDocument.scrollingElement;
}

export function scrollWithin(
  el: Element,
  { block = "center", behavior = "auto" }: { block?: "center" | "start"; behavior?: ScrollBehavior } = {},
): void {
  const sc = verticalScroller(el);
  if (!sc) return;
  const doc = el.ownerDocument;
  const isRoot = sc === doc.scrollingElement;
  const box = isRoot
    ? { top: 0, height: doc.defaultView?.innerHeight ?? sc.clientHeight }
    : sc.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const offset = block === "center" ? (box.height - r.height) / 2 : 0;
  const top = Math.max(0, sc.scrollTop + (r.top - box.top) - offset);
  if (behavior === "smooth" && typeof sc.scrollTo === "function") {
    sc.scrollTo({ top, behavior });
  } else {
    sc.scrollTop = top;
  }
}

/** Undo any sideways scroll of `el` and its ancestors (a box that clips a
 * sliding track must stay at scrollLeft 0). */
export function resetHorizontalScroll(el: Element | null): void {
  for (let p: Element | null = el; p; p = p.parentElement) {
    if (p.scrollLeft !== 0) p.scrollLeft = 0;
  }
}
