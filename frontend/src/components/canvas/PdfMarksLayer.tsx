/** Marks on a PDF page (Plan H5b.1): `PdfMarksPage`, one per react-pdf
 * `<Page>`, draws the page's marks on a layer that takes no pointer events
 * — clicks and hovers are hit-tested on the page, so the text under a mark
 * stays selectable — and holds the corner mark buttons of the page's
 * figures and equations and the Box tool's drag surface. The reader's side
 * (marks, blocks, popover, saving, Follow) is `usePdfMarks`. */
import { useContext, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { DraftBox } from "@/components/canvas/BoxTool";
import { ObjectMarkButton } from "@/components/canvas/ObjectMarkButton";
import { useBoxDrag } from "@/components/canvas/useBoxDrag";
import { PdfMarksContext } from "@/components/canvas/usePdfMarks";
import { MARK_PALETTE } from "@/lib/marks/htmlMarks";
import { EQUATION_TYPE, FIGURE_TYPES, type Frac } from "@/lib/marks/pdfGeometry";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { Mark, MarkColor, PaperPageBlock } from "@/types/notes";

const NO_MARKS: Mark[] = [];
const NO_BLOCKS: PaperPageBlock[] = [];
const LEAVE_MS = 200;
/** A press that travels this far is a drag (a selection), not a click. */
const CLICK_SLOP_PX = 4;
/** Slop around a mark's boxes for a click (page fractions). */
const HIT_SLOP = 0.004;
/** The quote underline and the hover / flash ring (the page is always white). */
const NEUTRAL = "#7b7b7b";
const RING = "#8f8f8f";

const pct = (v: number) => `${Math.round(v * 100000) / 1000}%`;

interface Drawn {
  id: string;
  kind: Mark["kind"];
  color: MarkColor | null;
  boxes: Frac[];
}

/** The page's marks, plus those just removed while they fade out. */
function useLeaving(items: Drawn[]): (Drawn & { leaving?: boolean })[] {
  const [state, setState] = useState<{ items: Drawn[]; leaving: Drawn[] }>({ items, leaving: [] });
  // A mark gone from the list stays, fading, for the exit (adjusted while
  // rendering: the list changed).
  if (state.items !== items) {
    const ids = new Set(items.map((i) => i.id));
    const gone = prefersReducedMotion() ? [] : state.items.filter((p) => !ids.has(p.id));
    setState({ items, leaving: [...state.leaving.filter((x) => !ids.has(x.id)), ...gone] });
  }
  const { leaving } = state;
  useEffect(() => {
    if (!leaving.length) return;
    const timer = setTimeout(
      () => setState((s) => ({ ...s, leaving: s.leaving.filter((x) => !leaving.includes(x)) })),
      LEAVE_MS,
    );
    return () => clearTimeout(timer);
  }, [leaving]);
  return useMemo(() => [...items, ...leaving.map((l) => ({ ...l, leaving: true }))], [items, leaving]);
}

function markStyle(d: Drawn, box: Frac): CSSProperties {
  const c = d.color ? MARK_PALETTE.light[d.color] : null;
  const base: CSSProperties = { left: pct(box[0]), top: pct(box[1]), width: pct(box[2] - box[0]), height: pct(box[3] - box[1]) };
  if (d.kind === "highlight") return { ...base, background: `color-mix(in srgb, ${c ?? NEUTRAL} 22%, transparent)` };
  if (d.kind === "quote") return { ...base, borderBottom: `1px solid ${NEUTRAL}` };
  return { ...base, outline: `1.5px solid color-mix(in srgb, ${c ?? NEUTRAL} 70%, transparent)`, outlineOffset: 1 };
}

function boxesOn(m: Mark, page: number): Frac[] {
  const out: Frac[] = [];
  for (const p of m.pieces) {
    if (p.erased || p.removed || p.lost || p.page !== page) continue;
    if (Array.isArray(p.rects)) out.push(...(p.rects as Frac[]));
    else if (p.box) out.push(p.box);
  }
  return out;
}

const within = (b: Frac, x: number, y: number) =>
  x >= b[0] - HIT_SLOP && x <= b[2] + HIT_SLOP && y >= b[1] - HIT_SLOP && y <= b[3] + HIT_SLOP;

/** One page's marks layer: the marks, the corner mark buttons, the Box
 * tool's drag surface. Rendered inside react-pdf's `<Page>`. */
export function PdfMarksPage({ pageNumber, hasTextLayer = true }: { pageNumber: number; hasTextLayer?: boolean }) {
  const ctx = useContext(PdfMarksContext);
  const page = pageNumber - 1;
  const rootRef = useRef<HTMLDivElement>(null);
  const blocks = ctx?.blocks[page] ?? NO_BLOCKS;
  const scanned = !hasTextLayer;
  const boxActive = !!ctx && (ctx.boxOn || scanned);
  const loadBlocks = ctx?.loadBlocks;

  // The page's blocks load once it comes into view.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !loadBlocks) return;
    if (typeof IntersectionObserver === "undefined") {
      void loadBlocks(page);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        void loadBlocks(page);
        io.disconnect();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [loadBlocks, page]);

  const drawn = useMemo<Drawn[]>(
    () =>
      (ctx?.marks ?? NO_MARKS)
        .map((m) => ({ id: m.id, kind: m.kind, color: m.color, boxes: boxesOn(m, page) }))
        .filter((d) => d.boxes.length > 0),
    [ctx?.marks, page],
  );
  const shown = useLeaving(drawn);
  const objects = useMemo(
    () => blocks.filter((b) => FIGURE_TYPES.has(b.type) || b.type === EQUATION_TYPE),
    [blocks],
  );

  const [hoverObject, setHoverObject] = useState<string | null>(null);
  const [overButton, setOverButton] = useState<string | null>(null);

  const { live, handlers } = useBoxDrag(boxActive, (box) => {
    const el = rootRef.current?.parentElement;
    if (el && ctx) ctx.openBox(page, box, el, scanned);
  });

  // Hit-testing on the page: the layer itself takes no pointer events.
  const latest = useRef({ drawn, objects, ctx, boxActive });
  useEffect(() => {
    latest.current = { drawn, objects, ctx, boxActive };
  }, [drawn, objects, ctx, boxActive]);
  useEffect(() => {
    const el = rootRef.current?.parentElement;
    if (!el || !ctx) return;
    let down: { x: number; y: number } | null = null;
    const at = (e: MouseEvent): [number, number] => {
      const r = el.getBoundingClientRect();
      return [(e.clientX - r.left) / (r.width || 1), (e.clientY - r.top) / (r.height || 1)];
    };
    const markAt = (x: number, y: number): string | null => {
      const { drawn: list, ctx: c } = latest.current;
      if (!c?.visible) return null;
      let found: string | null = null;
      for (const d of list) if (d.boxes.some((b) => within(b, x, y))) found = d.id;
      return found;
    };
    const onMove = (e: PointerEvent) => {
      const { ctx: c, objects: objs, boxActive: drawing } = latest.current;
      if (!c || drawing) return;
      const [x, y] = at(e);
      const id = markAt(x, y);
      if (id !== c.hot) c.setHot(id);
      setHoverObject(objs.find((b) => within(b.box, x, y))?.block_id ?? null);
    };
    const onLeave = () => {
      latest.current.ctx?.setHot(null);
      setHoverObject(null);
    };
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const onClick = (e: MouseEvent) => {
      const start = down;
      down = null;
      const { ctx: c, boxActive: drawing } = latest.current;
      if (!c || drawing) return;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) >= CLICK_SLOP_PX) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return; // the end of a drag that selected text
      const id = markAt(...at(e));
      if (id !== null) c.openMark(id);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("click", onClick);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("click", onClick);
    };
  }, [ctx !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ctx) return null;
  const pending = ctx.pendingBox?.page === page ? ctx.pendingBox.frac : null;
  return (
    <div
      ref={rootRef}
      data-pdf-marks
      data-hidden={!ctx.visible ? "true" : undefined}
      className={cn(
        "absolute inset-0 z-[6]",
        boxActive ? "pointer-events-auto cursor-crosshair touch-none select-none" : "pointer-events-none",
      )}
      {...handlers}
    >
      <div
        className={cn(
          "pointer-events-none absolute inset-0 transition-opacity duration-200 ease-out motion-reduce:transition-none",
          !ctx.visible && "opacity-0",
        )}
      >
        {shown.map((d) =>
          d.boxes.map((b, i) => {
            const dim = ctx.focusIds !== null && !ctx.focusIds.has(d.id);
            const ring = ctx.flashId === d.id ? `0 0 0 3px ${RING}` : ctx.hot === d.id ? `0 0 0 1px ${RING}` : undefined;
            return (
              <div
                key={`${d.id}:${i}`}
                data-mark={d.id}
                data-kind={d.kind}
                data-leaving={d.leaving ? "" : undefined}
                style={{ ...markStyle(d, b), boxShadow: ring, opacity: d.leaving ? 0 : dim ? 0.25 : undefined }}
                className="absolute rounded-[2px] transition-[opacity,box-shadow] duration-200 ease-out animate-in fade-in-0 motion-reduce:animate-none motion-reduce:transition-none"
              />
            );
          }),
        )}
      </div>
      {objects.map((b) => (
        <ObjectMarkButton
          key={b.block_id}
          kind={FIGURE_TYPES.has(b.type) ? "figure" : "equation"}
          shown={hoverObject === b.block_id || overButton === b.block_id}
          disabled={boxActive}
          style={{
            left: pct(b.box[2]),
            top: pct(b.box[1]),
            transform: FIGURE_TYPES.has(b.type) ? "translate(calc(-100% - 6px), 6px)" : "translate(-100%, calc(-100% - 2px))",
          }}
          onHoverChange={(over) => setOverButton(over ? b.block_id : null)}
          onActivate={() => ctx.openObject(page, b)}
        />
      ))}
      <DraftBox box={live ?? pending} />
    </div>
  );
}
