/** The Box tool's drag on a PDF page (Plan H5b.1): pointer handlers that
 * draw a rectangle while active, in page fractions. */
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { toFrac, type Frac } from "@/lib/marks/pdfGeometry";

/** A drag shorter than this (page fractions, both ways) is a click. */
const MIN_SIDE = 0.005;
const MIN_LONG_SIDE = 0.02;

/** Handlers for the page's drag surface while `active`; `live` is the box
 * under way. `onBox` gets the finished box. Positions are read against the
 * surface's parent, the react-pdf page. */
export function useBoxDrag(active: boolean, onBox: (box: Frac) => void) {
  const start = useRef<{ x: number; y: number; page: DOMRect } | null>(null);
  const [live, setLive] = useState<Frac | null>(null);
  const liveRef = useRef<Frac | null>(null);

  const at = (e: ReactPointerEvent<HTMLElement>, page: DOMRect): [number, number] => {
    const [x, y] = toFrac(
      { left: e.clientX, top: e.clientY, right: e.clientX, bottom: e.clientY },
      { left: page.left, top: page.top, right: page.right, bottom: page.bottom },
    );
    return [x, y];
  };
  const set = (b: Frac | null) => {
    liveRef.current = b;
    setLive(b);
  };

  return {
    live,
    handlers: active
      ? {
          onPointerDown: (e: ReactPointerEvent<HTMLElement>) => {
            if (e.button !== 0) return;
            const page = (e.currentTarget.parentElement ?? e.currentTarget).getBoundingClientRect();
            const [x, y] = at(e, page);
            start.current = { x, y, page };
            e.preventDefault(); // no text selection while drawing
            e.currentTarget.setPointerCapture?.(e.pointerId);
            set(null);
          },
          onPointerMove: (e: ReactPointerEvent<HTMLElement>) => {
            const s = start.current;
            if (!s) return;
            const [x, y] = at(e, s.page);
            set([Math.min(s.x, x), Math.min(s.y, y), Math.max(s.x, x), Math.max(s.y, y)]);
          },
          onPointerUp: () => {
            const b = liveRef.current;
            start.current = null;
            set(null);
            if (!b) return;
            const w = b[2] - b[0];
            const h = b[3] - b[1];
            if (w < MIN_SIDE || h < MIN_SIDE || Math.max(w, h) < MIN_LONG_SIDE) return;
            onBox(b);
          },
          onPointerCancel: () => {
            start.current = null;
            set(null);
          },
        }
      : {},
  };
}
