/** Marks on a PDF page (Plan H5b.1): `PdfMarksPage`, one per react-pdf
 * `<Page>`, draws the page's marks on a layer that takes no pointer events
 * — clicks and hovers are hit-tested on the page, so the text under a mark
 * stays selectable — and holds the corner mark buttons of the page's
 * figures and equations and the Box tool's drag surface. The reader's side
 * (marks, blocks, popover, saving, Follow) is `usePdfMarks`. With the
 * highlighter on, a click on a mark erases the piece under it (a hovered
 * mark shows a dashed ring) and a click on a figure or equation marks it. */
import { useContext, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { DraftBox } from "@/components/canvas/BoxTool";
import { ObjectMarkButton } from "@/components/canvas/ObjectMarkButton";
import { useBoxDrag } from "@/components/canvas/useBoxDrag";
import { PdfMarksContext } from "@/components/canvas/usePdfMarks";
import { MARK_PALETTE } from "@/lib/marks/htmlMarks";
import { EQUATION_TYPE, FIGURE_TYPES, findInPage, pageText, type Frac } from "@/lib/marks/pdfGeometry";
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
  /** The index in `mark.pieces` each box draws. */
  boxPiece: number[];
}

interface Placed {
  boxes: Frac[];
  boxPiece: number[];
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

function boxesOn(m: Mark, page: number): Placed {
  const out: Placed = { boxes: [], boxPiece: [] };
  m.pieces.forEach((p, i) => {
    if (p.erased || p.removed || p.lost || p.page !== page) return;
    const boxes = Array.isArray(p.rects) ? (p.rects as Frac[]) : p.box ? [p.box] : [];
    for (const b of boxes) {
      out.boxes.push(b);
      out.boxPiece.push(i);
    }
  });
  return out;
}

const within = (b: Frac, x: number, y: number) =>
  x >= b[0] - HIT_SLOP && x <= b[2] + HIT_SLOP && y >= b[1] - HIT_SLOP && y <= b[3] + HIT_SLOP;

/** One page's marks layer: the marks, the corner mark buttons, the Box
 * tool's drag surface. Rendered inside react-pdf's `<Page>`. */
export function PdfMarksPage({
  pageNumber,
  hasTextLayer = true,
  textVersion = 1,
}: {
  pageNumber: number;
  hasTextLayer?: boolean;
  /** Bumped each time react-pdf renders the page's text layer (0: not yet):
   * marks made in the HTML view are found in it again. */
  textVersion?: number;
}) {
  const ctx = useContext(PdfMarksContext);
  const page = pageNumber - 1;
  const rootRef = useRef<HTMLDivElement>(null);
  const blocks = ctx?.blocks[page] ?? NO_BLOCKS;
  const scanned = !hasTextLayer;
  // A drag draws a box with the Box tool on, and always on a scan (there is
  // no text to select); the corner buttons and mark clicks give way only to
  // the Box tool.
  const boxActive = !!ctx && (ctx.boxOn || scanned);
  const boxTool = !!ctx?.boxOn;
  const loadBlocks = ctx?.loadBlocks;

  // The page's blocks load once it comes into view.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !loadBlocks) return;
    if (typeof IntersectionObserver === "undefined") {
      void loadBlocks(page).catch(() => undefined); // loaded again when needed
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        void loadBlocks(page).catch(() => undefined); // loaded again when needed
        io.disconnect();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [loadBlocks, page]);

  // Marks made in the HTML view of the paper show here wherever their
  // quote is in the page's text layer (not found: not drawn).
  const htmlMarks = useMemo(() => (ctx?.marks ?? NO_MARKS).filter((m) => m.view === "html"), [ctx?.marks]);
  const [found, setFound] = useState<Readonly<Record<string, Placed>>>({});
  useEffect(() => {
    const el = rootRef.current?.parentElement;
    if (!el || !htmlMarks.length || !textVersion) return;
    // After the text layer has laid out.
    const frame = requestAnimationFrame(() => {
      const text = pageText(el);
      const next: Record<string, Placed> = {};
      for (const m of htmlMarks) {
        const placed: Placed = { boxes: [], boxPiece: [] };
        m.pieces.forEach((p, i) => {
          if (p.erased || p.removed || p.lost) return;
          for (const b of findInPage(text, String(p.quote ?? "")) ?? []) {
            placed.boxes.push(b);
            placed.boxPiece.push(i);
          }
        });
        if (placed.boxes.length) next[m.id] = placed;
      }
      setFound(next);
    });
    return () => cancelAnimationFrame(frame);
  }, [htmlMarks, textVersion]);

  const drawn = useMemo<Drawn[]>(
    () =>
      (ctx?.marks ?? NO_MARKS)
        .map((m) => ({
          id: m.id,
          kind: m.kind,
          color: m.color,
          ...(m.view === "html" ? (found[m.id] ?? { boxes: [], boxPiece: [] }) : boxesOn(m, page)),
        }))
        .filter((d) => d.boxes.length > 0),
    [ctx?.marks, page, found],
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
  const latest = useRef({ drawn, objects, ctx, boxTool });
  useEffect(() => {
    latest.current = { drawn, objects, ctx, boxTool };
  }, [drawn, objects, ctx, boxTool]);
  useEffect(() => {
    const el = rootRef.current?.parentElement;
    if (!el || !ctx) return;
    let down: { x: number; y: number } | null = null;
    const at = (e: MouseEvent): [number, number] => {
      const r = el.getBoundingClientRect();
      return [(e.clientX - r.left) / (r.width || 1), (e.clientY - r.top) / (r.height || 1)];
    };
    const markAt = (x: number, y: number): { id: string; piece: number | null } | null => {
      const { drawn: list, ctx: c } = latest.current;
      if (!c?.visible) return null;
      let hit: { id: string; piece: number | null } | null = null;
      for (const d of list) {
        const i = d.boxes.findIndex((b) => within(b, x, y));
        if (i >= 0) hit = { id: d.id, piece: d.boxPiece[i] ?? null };
      }
      return hit;
    };
    const onMove = (e: PointerEvent) => {
      const { ctx: c, objects: objs, boxTool: drawing } = latest.current;
      if (!c || drawing) return;
      const [x, y] = at(e);
      const id = markAt(x, y)?.id ?? null;
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
      const { ctx: c, boxTool: drawing } = latest.current;
      if (!c || drawing) return;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) >= CLICK_SLOP_PX) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return; // the end of a drag that selected text
      const [x, y] = at(e);
      const hit = markAt(x, y);
      if (!c.pen) {
        if (hit) c.openMark(hit.id);
        return;
      }
      // The highlighter: a mark's piece is erased; a figure or an equation
      // is marked whole.
      if (hit) {
        if (hit.piece !== null) c.erasePiece(hit.id, hit.piece);
        return;
      }
      const obj = latest.current.objects.find((b) => within(b.box, x, y));
      if (obj) c.penObject(page, obj);
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
  }, [ctx !== null, page]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ctx) return null;
  const pending = ctx.pendingBox?.page === page ? ctx.pendingBox.frac : null;
  // With the pen on, the figure or equation under the pointer is what a
  // click marks: ringed in the pen colour.
  const penTarget = ctx.pen && !shown.some((d) => d.id === ctx.hot) ? objects.find((b) => b.block_id === hoverObject) : undefined;
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
            const hot = ctx.hot === d.id;
            const ring = ctx.flashId === d.id ? `0 0 0 3px ${RING}` : hot && !ctx.pen ? `0 0 0 1px ${RING}` : undefined;
            // With the pen on, a hovered mark is what a click erases.
            const erase = hot && ctx.pen ? { outline: "1.5px dashed #404040", outlineOffset: 1 } : undefined;
            return (
              <div
                key={`${d.id}:${i}`}
                data-mark={d.id}
                data-kind={d.kind}
                data-erase={erase ? "true" : undefined}
                data-leaving={d.leaving ? "" : undefined}
                style={{ ...markStyle(d, b), ...erase, boxShadow: ring, opacity: d.leaving ? 0 : dim ? 0.25 : undefined }}
                className="absolute rounded-[2px] transition-[opacity,box-shadow] duration-200 ease-out animate-in fade-in-0 motion-reduce:animate-none motion-reduce:transition-none"
              />
            );
          }),
        )}
      </div>
      <PenObjectRing box={penTarget?.box ?? null} color={ctx.pen} />
      {objects.map((b) => (
        <ObjectMarkButton
          key={b.block_id}
          kind={FIGURE_TYPES.has(b.type) ? "figure" : "equation"}
          shown={hoverObject === b.block_id || overButton === b.block_id}
          // The highlighter marks an object with a click on it instead.
          disabled={boxTool || ctx.pen !== null}
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

/** The ring round the figure or equation a pen click would mark; it fades
 * in and out where it is. */
function PenObjectRing({ box, color }: { box: Frac | null; color: MarkColor | null }) {
  const [last, setLast] = useState<{ box: Frac; color: MarkColor } | null>(null);
  if (box && color && (last?.box !== box || last.color !== color)) setLast({ box, color });
  const on = box !== null && color !== null;
  if (!last) return null;
  const b = last.box;
  return (
    <div
      aria-hidden
      data-pen-target={on ? "true" : "false"}
      style={{
        left: pct(b[0]),
        top: pct(b[1]),
        width: pct(b[2] - b[0]),
        height: pct(b[3] - b[1]),
        boxShadow: `0 0 0 2px ${MARK_PALETTE.light[last.color]}`,
      }}
      className={cn(
        "pointer-events-none absolute rounded-[3px] transition-opacity duration-150 ease-out motion-reduce:transition-none",
        on ? "opacity-100" : "opacity-0",
      )}
    />
  );
}
