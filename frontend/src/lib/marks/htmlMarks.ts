/** Marks drawn over a paper rendered as HTML (Plan H5b.1).
 *
 * OVERLAY ONLY: the paper's nodes are never touched. One layer (`div.ph-mk-
 * layer`, behind the text) holds a box per line of each marked passage and a
 * margin icon for each note with no quote; one `<style id="ph-mk-style">`
 * styles them. The layer takes no pointer events: clicks and hovers are
 * hit-tested against the drawn boxes, so the text under a mark stays
 * selectable.
 *
 * A highlight is a box in its colour, a quote a thin neutral underline, a
 * note on a passage with no quote a margin icon (with a count when several
 * share a line). Hidden colours are not drawn; "Show my marks" off fades the
 * layer out. A mark of several pieces (highlighter strokes joined) draws
 * each piece, the skipped words between them unmarked; a click reports the
 * piece under it. With the highlighter on (`pen`), a selection shows in the
 * pen colour and a hovered mark shows the dashed ring of "click to erase".
 */
import { prefersReducedMotion } from "@/lib/motion";
import { buildTextIndex, findPiece, rangeAt, type ChunkMap, type TextIndex } from "@/lib/marks/anchor";
import type { Mark, MarkColor, MarkColorInfo, MarkPiece } from "@/types/notes";

export const MK_STYLE_ID = "ph-mk-style";
const LAYER_CLASS = "ph-mk-layer";
const HOT_ATTR = "data-ph-mk-hot";
const PEN_ATTR = "data-ph-pen";
/** Matches the 200 ms fades below plus a margin. */
const LEAVE_MS = 220;
const FLASH_MS = 1600;
const ICON_SIZE = 16;
const ICON_GAP = 6;

export const MARK_COLORS: readonly MarkColor[] = ["green", "blue", "pink", "purple"];

/** What a figure citation opens on, after its `phfig-NNN` anchor. */
const FIGURE_SEL = 'figure, div[class^="figure"], img';
/** A display equation (inline math stays part of the sentence's text). */
const DISPLAY_MATH = 'mjx-container[display="true"], .math.display, math[display="block"]';
const NOT_PAPER = ".ph-tr, [data-ph-overlay]";
/** What a highlighter click marks whole (ringed on hover while the pen is on). */
const PEN_OBJECTS = `figure, div[class^="figure"], ${DISPLAY_MATH}`;

/** A figure or display equation a mark can take whole. */
export interface HtmlObject {
  key: string;
  el: HTMLElement;
  kind: "figure" | "equation";
  /** The asset figure id (`fig-001`) of a figure. */
  figureId: string | null;
}

/** The figure after a `phfig-NNN` anchor (as a [fig:] citation finds it). */
function figureAfter(anchor: Element, figures: readonly HTMLElement[]): HTMLElement | null {
  return figures.find((el) => anchor.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) ?? null;
}

function paperFigures(doc: Document): HTMLElement[] {
  return Array.from(doc.querySelectorAll<HTMLElement>(FIGURE_SEL)).filter((el) => !el.closest(NOT_PAPER));
}

/** A figure by its asset id (`fig-001` → after `#phfig-001`). */
export function figureById(doc: Document, figureId: string): HTMLElement | null {
  const anchor = doc.getElementById(`ph${figureId}`);
  return anchor ? figureAfter(anchor, paperFigures(doc)) : null;
}

/** The paper's figures (those with a `phfig` anchor) and display
 * equations, in document order. */
export function markableObjects(doc: Document): HtmlObject[] {
  const out: HtmlObject[] = [];
  const figures = paperFigures(doc);
  const taken = new Set<Element>();
  for (const anchor of Array.from(doc.querySelectorAll('span[id^="phfig-"]'))) {
    const el = figureAfter(anchor, figures);
    if (!el || taken.has(el)) continue;
    taken.add(el);
    out.push({ key: anchor.id, el, kind: "figure", figureId: anchor.id.slice(2) });
  }
  Array.from(doc.querySelectorAll<HTMLElement>(DISPLAY_MATH))
    .filter((el) => !el.closest(NOT_PAPER) && !el.parentElement?.closest(DISPLAY_MATH))
    .forEach((el, i) => out.push({ key: `eq:${i}`, el, kind: "equation", figureId: null }));
  return out.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
}

/** The palette (index.css `--hl-*`), used when the app's tokens can't be read. */
export const MARK_PALETTE: Record<"light" | "dark", Record<MarkColor, string>> = {
  light: { green: "#2f9e63", blue: "#3b7dd8", pink: "#d6457e", purple: "#8b5cd6" },
  dark: { green: "#36a067", blue: "#4b86dc", pink: "#d9578a", purple: "#9670dc" },
};

/** The overlay's greys: the quote underline, the hover / flash ring, a
 * hovered icon's text and background. */
const NEUTRAL = {
  light: { neutral: "#7b7b7b", ring: "#8f8f8f", hotFg: "#262626" },
  dark: { neutral: "#8a8a8a", ring: "#bdbdbd", hotFg: "#f0f0f0" },
};

// The dark reader is the light page put through `invert(0.9)
// hue-rotate(180deg)` (applyIframeTheme), and the layer is part of the page.
// So in the dark theme each colour is drawn as the colour that this filter
// turns into the palette colour: what shows is the validated dark palette at
// its alpha over the dark page.
const HUE_180 = [
  [-0.574, 1.43, 0.144],
  [0.426, 0.43, 0.144],
  [0.426, 1.43, -0.856],
] as const;

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function parseHex(hex: string): [number, number, number] | null {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = Number.parseInt(m[1]!, 16);
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function toHex(c: readonly number[]): string {
  return `#${c.map((v) => Math.round(clamp01(v) * 255).toString(16).padStart(2, "0")).join("")}`;
}

/** The colour that the dark page's filter shows as `hex` (pre-image of
 * `hue-rotate(180deg)` after `invert(0.9)`; clamped to the gamut). */
export function darkPreimage(hex: string): string {
  const c = parseHex(hex);
  if (!c) return hex;
  const h = HUE_180.map((r) => r[0] * c[0] + r[1] * c[1] + r[2] * c[2]);
  return toHex(h.map((y) => (0.9 - y) / 0.8));
}

/** What the dark page's filter shows for a drawn colour. */
export function darkRendered(hex: string): string {
  const c = parseHex(hex);
  if (!c) return hex;
  const inv = c.map((v) => clamp01(0.9 - 0.8 * v));
  return toHex(HUE_180.map((r) => r[0] * inv[0]! + r[1] * inv[1]! + r[2] * inv[2]!));
}

// The layer sits behind the text (z-index -1: above the page background,
// below the words).
const STYLE = `
.${LAYER_CLASS} {
  position: absolute; left: 0; top: 0; width: 0; height: 0; z-index: -1;
  pointer-events: none; transition: opacity 200ms ease-out;
}
.${LAYER_CLASS}[data-hidden] { opacity: 0; }
.ph-mk {
  position: absolute; border-radius: 2px;
  transition: opacity 200ms ease-out, box-shadow 150ms ease-out, background-color 200ms ease-out, outline-color 150ms ease-out;
  animation: ph-mk-in 200ms ease-out;
}
.ph-mk[data-kind="highlight"] { background: color-mix(in srgb, var(--mk-c) var(--mk-alpha), transparent); }
.ph-mk[data-kind="quote"] { border-bottom: 1px solid var(--mk-neutral); border-radius: 0; }
.ph-mk[data-kind="figure"], .ph-mk[data-kind="equation"] {
  outline: 2px solid color-mix(in srgb, var(--mk-c, var(--mk-neutral)) 70%, transparent);
  outline-offset: 3px; border-radius: 4px;
}
${MARK_COLORS.map((c) => `.ph-mk[data-color="${c}"] { --mk-c: var(--mk-${c}); }`).join("\n")}
.ph-mk[data-dim] { opacity: .25; }
.ph-mk[data-hot] { box-shadow: 0 0 0 1px var(--mk-ring); }
/* With the pen on, the hovered mark is what a click erases: a dashed ring
   fades in (and out) round it. */
html[${PEN_ATTR}] .ph-mk:is([data-kind="highlight"], [data-kind="quote"]) { outline: 1.5px dashed transparent; outline-offset: 1px; }
html[${PEN_ATTR}] .ph-mk[data-hot] { box-shadow: none; outline-style: dashed; outline-color: var(--mk-hot-fg); }
.ph-mk[data-flash] { box-shadow: 0 0 0 3px var(--mk-ring); }
.ph-mk-icon {
  position: absolute; display: inline-flex; align-items: center; gap: 1px;
  height: ${ICON_SIZE}px; padding: 0 2px; border-radius: 4px; color: var(--mk-neutral);
  font: 500 10.5px/1 system-ui, -apple-system, "Segoe UI", sans-serif;
  transition: opacity 200ms ease-out, color 150ms ease-out, background-color 150ms ease-out, box-shadow 150ms ease-out;
  animation: ph-mk-in 200ms ease-out;
}
.ph-mk-icon svg { width: 12px; height: 12px; }
.ph-mk-icon[data-hot] { color: var(--mk-hot-fg); background: var(--mk-hot-bg); }
.ph-mk-icon[data-flash] { box-shadow: 0 0 0 2px var(--mk-ring); }
.ph-mk-icon[data-dim] { opacity: .3; }
.ph-mk[data-leaving], .ph-mk-icon[data-leaving] { opacity: 0; animation: none; }
@keyframes ph-mk-in { from { opacity: 0; } }
html[${HOT_ATTR}], html[${HOT_ATTR}] * { cursor: pointer; }
@media (prefers-reduced-motion: reduce) {
  .${LAYER_CLASS}, .ph-mk, .ph-mk-icon { transition: none; animation: none; }
}
`;

export interface HtmlMarksOptions {
  marks: readonly Mark[];
  colors: readonly MarkColorInfo[];
  chunks: ChunkMap | null;
  /** "Show my marks": off hides every mark (nothing is deleted). */
  visible: boolean;
  /** Focus: marks outside this set fade (null: no focus). */
  focusIds: ReadonlySet<string> | null;
  dark: boolean;
  /** The highlighter's colour while it is on (null: off). */
  pen?: MarkColor | null;
  /** A click on a mark, with the index of the piece under it (null: not
   * one piece, e.g. a margin icon). */
  onClick?: (markId: string, piece: number | null) => void;
  onHover?: (markId: string | null) => void;
}

export interface HtmlMarksHandle {
  update: (next: Partial<HtmlMarksOptions>) => void;
  /** Rings a mark for a moment and scrolls it into view; false if not drawn. */
  flash: (markId: string) => boolean;
  /** Removes every overlay node, style and listener. */
  detach: () => void;
}

interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface Drawn {
  key: string;
  /** The marked passages (a quote mark), or none (a note with no quote). */
  ranges: Range[];
  /** The index in `mark.pieces` of each range. */
  rangePiece: number[];
  /** A figure or equation taken whole: one box around each range and each
   * of `targets`. */
  whole: boolean;
  targets: Element[];
  /** Where a note with no quote sits: its chunk spans. */
  spots: Element[];
  els: HTMLElement[];
  boxes: Box[];
  /** The piece each box draws (null: the whole object). */
  boxPiece: (number | null)[];
}

interface Icon {
  el: HTMLElement;
  box: Box;
  marks: string[];
}

const isShown = (p: MarkPiece) => !p.erased && !p.removed && !p.lost;

/** The pieces a mark shows on the paper (not erased, moved or lost). */
function livePieces(m: Mark) {
  return m.pieces.filter(isShown);
}

/** The shown pieces with their index in `mark.pieces`. */
function shownPieces(m: Mark): [MarkPiece, number][] {
  return m.pieces.flatMap((p, i) => (isShown(p) ? [[p, i] as [MarkPiece, number]] : []));
}

const hasQuote = (m: Mark) => livePieces(m).some((p) => typeof p.quote === "string" && p.quote.trim() !== "");

/** A piece of a mark made on the PDF tab, where the reading text holds it
 * (located when it was saved). */
const located = (p: MarkPiece) => typeof p.text_start === "number" && typeof p.text_end === "number";

/** Whether the HTML reader draws this mark (translation marks come later).
 * A mark made on the PDF tab shows here where its text was found. */
function drawable(m: Mark, hidden: ReadonlySet<string>): boolean {
  if (m.deleted_at !== null || m.lang) return false;
  if (m.color !== null && hidden.has(m.color)) return false;
  if (m.view === "pdf") return (m.kind === "quote" || m.kind === "highlight") && livePieces(m).some(located);
  return m.kind === "quote" || m.kind === "highlight" || m.kind === "figure" || m.kind === "equation";
}

/** One box per line: line boxes of the same line that touch are merged. */
function lineBoxes(rects: Iterable<DOMRect>): DOMRect[] {
  const out: DOMRect[] = [];
  for (const r of rects) {
    if (r.width <= 0 || r.height <= 0) continue;
    const last = out[out.length - 1];
    if (last && Math.abs(last.top - r.top) < 1.5 && Math.abs(last.height - r.height) < 1.5 && r.left <= last.right + 2) {
      out[out.length - 1] = new DOMRect(last.left, last.top, Math.max(last.right, r.right) - last.left, last.height);
    } else {
      out.push(new DOMRect(r.left, r.top, r.width, r.height));
    }
  }
  return out;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function iconSvg(doc: Document): SVGElement {
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z", "M13 8H7", "M17 12H7"]) {
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

/** The app's palette for the current theme (its `--hl-*` tokens). */
function palette(dark: boolean): Record<MarkColor, string> {
  const out = { ...MARK_PALETTE[dark ? "dark" : "light"] };
  try {
    const css = getComputedStyle(document.documentElement);
    for (const c of MARK_COLORS) {
      const v = css.getPropertyValue(`--hl-${c}`).trim();
      if (parseHex(v)) out[c] = v;
    }
  } catch {
    /* no app document: the fallback */
  }
  return out;
}

function place(el: HTMLElement, box: Box): void {
  el.style.left = `${box.left}px`;
  el.style.top = `${box.top}px`;
  el.style.width = `${box.width}px`;
  el.style.height = `${box.height}px`;
}

function contains(box: Box, x: number, y: number): boolean {
  return x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

function setFlag(el: Element, name: string, on: boolean): void {
  if (on) el.setAttribute(name, "");
  else el.removeAttribute(name);
}

interface MathJaxStartup {
  MathJax?: { startup?: { promise?: Promise<unknown> } };
}

export function attachHtmlMarks(doc: Document, initial: HtmlMarksOptions): HtmlMarksHandle {
  let o = initial;
  const win = doc.defaultView;
  const root = doc.body ?? doc.documentElement;

  const style = doc.createElement("style");
  style.id = MK_STYLE_ID;
  style.textContent = STYLE;
  (doc.head ?? doc.documentElement).appendChild(style);

  const layer = doc.createElement("div");
  layer.className = LAYER_CLASS;
  layer.setAttribute("data-ph-overlay", "");
  layer.setAttribute("aria-hidden", "true");
  layer.style.pointerEvents = "none";
  root.appendChild(layer);

  const drawn = new Map<string, Drawn>();
  let icons = new Map<string, Icon>();
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let hot: string | null = null;
  let index: TextIndex | null = null;
  let detached = false;
  // A new chunk map re-places every mark (it is the hint among repeats).
  let chunkGen = 0;

  const later = (fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timers.delete(id);
      fn();
    }, ms);
    timers.add(id);
  };

  /** Fades an element out, then removes it. */
  const leave = (el: HTMLElement) => {
    el.setAttribute("data-leaving", "");
    if (prefersReducedMotion()) el.remove();
    else later(() => el.remove(), LEAVE_MS);
  };

  const origin = () => {
    const r = layer.getBoundingClientRect();
    return { left: r.left, top: r.top };
  };

  const theme = () => {
    const mode = o.dark ? "dark" : "light";
    const draw = (hex: string) => (o.dark ? darkPreimage(hex) : hex);
    layer.style.setProperty("--mk-alpha", o.dark ? "32%" : "22%");
    const p = palette(o.dark);
    for (const c of MARK_COLORS) layer.style.setProperty(`--mk-${c}`, draw(p[c]));
    layer.style.setProperty("--mk-neutral", draw(NEUTRAL[mode].neutral));
    layer.style.setProperty("--mk-ring", draw(NEUTRAL[mode].ring));
    layer.style.setProperty("--mk-hot-fg", draw(NEUTRAL[mode].hotFg));
    // A dark veil on the light page; under the dark page's inversion, light.
    layer.style.setProperty("--mk-hot-bg", "rgb(0 0 0 / 7%)");
    // With the highlighter on, a selection shows in the pen colour, and the
    // figure or display equation under the pointer (what a click marks) is
    // ringed in it.
    style.textContent = [
      STYLE,
      `html[${PEN_ATTR}] :is(${PEN_OBJECTS}) { outline: 2px solid transparent; outline-offset: 3px; transition: outline-color 150ms ease-out; }`,
      `@media (prefers-reduced-motion: reduce) { html[${PEN_ATTR}] :is(${PEN_OBJECTS}) { transition: none; } }`,
      ...MARK_COLORS.flatMap((c) => [
        `html[${PEN_ATTR}="${c}"] ::selection { background: color-mix(in srgb, ${draw(p[c])} 45%, transparent); }`,
        `html[${PEN_ATTR}="${c}"] :is(${PEN_OBJECTS}):hover { outline-color: ${draw(p[c])}; cursor: pointer; }`,
      ]),
    ].join("\n");
  };

  /** Positions every drawn mark and the margin icons from the live layout. */
  const layout = () => {
    if (detached) return;
    const at = origin();
    const lines = new Map<string, { box: Box; marks: string[] }>();
    const viewRight = (doc.documentElement.clientWidth || win?.innerWidth || 0) - at.left;
    for (const [id, d] of drawn) {
      const boxes: Box[] = [];
      const boxPiece: (number | null)[] = [];
      if (d.whole) {
        for (const r of [...d.ranges, ...d.targets].map((x) => x.getBoundingClientRect())) {
          if (r.width > 0 && r.height > 0) {
            boxes.push({ left: r.left - at.left, top: r.top - at.top, width: r.width, height: r.height });
            boxPiece.push(null);
          }
        }
      } else {
        d.ranges.forEach((range, ri) => {
          for (const r of lineBoxes(Array.from(range.getClientRects()))) {
            boxes.push({ left: r.left - at.left, top: r.top - at.top, width: r.width, height: r.height });
            boxPiece.push(d.rangePiece[ri] ?? null);
          }
        });
      }
      // A note with no quote: a margin icon on its passage's first line.
      for (const spot of d.spots) {
        const block = spot.parentElement?.closest("p, li, figcaption, td, th, dd, blockquote, h1, h2, h3, h4, h5, h6, div");
        if (!block) continue;
        const line = doc.createRange();
        line.selectNodeContents(block);
        const first = lineBoxes(Array.from(line.getClientRects()))[0] ?? block.getBoundingClientRect();
        const right = block.getBoundingClientRect().right - at.left + ICON_GAP;
        const left = Math.max(0, viewRight > 0 ? Math.min(right, viewRight - ICON_SIZE - 4) : right);
        const top = first.top - at.top + (first.height - ICON_SIZE) / 2;
        const key = `${Math.round(left)}:${Math.round(first.top - at.top)}`;
        const entry = lines.get(key) ?? { box: { left, top, width: ICON_SIZE + 10, height: ICON_SIZE }, marks: [] };
        if (!entry.marks.includes(id)) entry.marks.push(id);
        lines.set(key, entry);
      }
      d.boxes = boxes;
      d.boxPiece = boxPiece;
      // Reuse the mark's boxes (no re-animation on relayout).
      while (d.els.length > boxes.length) d.els.pop()!.remove();
      while (d.els.length < boxes.length) {
        const el = doc.createElement("div");
        el.className = "ph-mk";
        el.dataset.mark = id;
        if (d.els.length > 0) el.style.animation = "none";
        layer.appendChild(el);
        d.els.push(el);
      }
      boxes.forEach((b, i) => place(d.els[i]!, b));
    }
    const next = new Map<string, Icon>();
    for (const [key, line] of lines) {
      const prev = icons.get(key);
      const el = prev?.el ?? doc.createElement("div");
      if (!prev) {
        el.className = "ph-mk-icon";
        el.appendChild(iconSvg(doc));
        el.appendChild(doc.createElement("span"));
        layer.appendChild(el);
      }
      el.dataset.marks = line.marks.join(" ");
      el.lastElementChild!.textContent = line.marks.length > 1 ? String(line.marks.length) : "";
      el.style.left = `${line.box.left}px`;
      el.style.top = `${line.box.top}px`;
      next.set(key, { el, box: line.box, marks: line.marks });
    }
    for (const [key, icon] of icons) if (!next.has(key)) leave(icon.el);
    icons = next;
    paint();
  };

  /** Colour, focus and hover flags. */
  const paint = () => {
    const byId = new Map(o.marks.map((m) => [m.id, m]));
    for (const [id, d] of drawn) {
      const m = byId.get(id);
      if (!m) continue;
      for (const el of d.els) {
        el.dataset.kind = m.kind;
        if (m.kind !== "quote" && m.color) el.dataset.color = m.color;
        else delete el.dataset.color;
        setFlag(el, "data-dim", o.focusIds !== null && !o.focusIds.has(id));
        setFlag(el, "data-hot", hot === id);
      }
    }
    for (const icon of icons.values()) {
      setFlag(icon.el, "data-hot", hot !== null && icon.marks.includes(hot));
      setFlag(icon.el, "data-dim", o.focusIds !== null && !icon.marks.some((m) => o.focusIds!.has(m)));
    }
  };

  /** The chunk spans of a note with no quote. */
  const spotsOf = (m: Mark): Element[] => {
    const out: Element[] = [];
    for (const p of livePieces(m)) {
      const id = typeof p.chunk_start === "number" ? o.chunks?.byId.get(p.chunk_start)?.dom_id : undefined;
      const el = id ? doc.getElementById(id) : null;
      if (el) out.push(el);
    }
    return out;
  };

  /** Which marks are drawn, and where their pieces are in the document. */
  const sync = () => {
    const hidden = new Set(o.colors.filter((c) => !c.visible).map((c) => c.color));
    const want = new Map(o.marks.filter((m) => drawable(m, hidden)).map((m) => [m.id, m]));
    for (const [id, d] of drawn) {
      if (want.has(id)) continue;
      d.els.forEach(leave);
      drawn.delete(id);
    }
    for (const [id, m] of want) {
      const key = `${chunkGen}:${JSON.stringify(livePieces(m))}`;
      const prev = drawn.get(id);
      if (prev?.key === key) continue;
      const ranges: Range[] = [];
      const rangePiece: number[] = [];
      let spots: Element[] = [];
      let targets: Element[] = [];
      const whole = m.kind === "figure" || m.kind === "equation";
      const figureId = livePieces(m).find((p) => typeof p.figure_id === "string")?.figure_id;
      /** Each shown piece's range (one not found is not drawn). */
      const placeEach = (find: (p: MarkPiece) => Range | null) => {
        for (const [p, i] of shownPieces(m)) {
          const r = find(p);
          if (r) {
            ranges.push(r);
            rangePiece.push(i);
          }
        }
      };
      if (m.view === "pdf") {
        index ??= buildTextIndex(doc);
        placeEach((p) => (located(p) ? rangeAt(index!, p.text_start as number, p.text_end as number) : null));
      } else if (m.kind === "figure" && typeof figureId === "string") {
        const el = figureById(doc, figureId);
        targets = el ? [el] : [];
      } else if (hasQuote(m)) {
        index ??= buildTextIndex(doc);
        placeEach((p) => findPiece(index!, p, o.chunks));
      } else {
        spots = spotsOf(m);
      }
      if (whole) spots = [];
      if (!ranges.length && !spots.length && !targets.length) {
        prev?.els.forEach(leave);
        drawn.delete(id);
        continue;
      }
      drawn.set(id, { key, ranges, rangePiece, whole, targets, spots, els: prev?.els ?? [], boxes: [], boxPiece: [] });
    }
    index = null; // the document may change before the next sync
    setFlag(layer, "data-hidden", !o.visible);
    if (o.pen) doc.documentElement.setAttribute(PEN_ATTR, o.pen);
    else doc.documentElement.removeAttribute(PEN_ATTR);
    layout();
  };

  const hitPiece = (x: number, y: number): { id: string; piece: number | null } | null => {
    if (!o.visible) return null;
    const at = origin();
    const lx = x - at.left;
    const ly = y - at.top;
    for (const icon of icons.values()) {
      if (contains(icon.box, lx, ly)) return icon.marks[0] ? { id: icon.marks[0], piece: null } : null;
    }
    let found: { id: string; piece: number | null } | null = null;
    for (const [id, d] of drawn) {
      const i = d.boxes.findIndex((b) => contains(b, lx, ly));
      // A figure or equation taken whole is its one piece.
      if (i >= 0) found = { id, piece: d.whole ? 0 : (d.boxPiece[i] ?? null) };
    }
    return found;
  };
  const hit = (x: number, y: number): string | null => hitPiece(x, y)?.id ?? null;

  const setHot = (id: string | null) => {
    if (id === hot) return;
    hot = id;
    setFlag(doc.documentElement, HOT_ATTR, id !== null);
    paint();
    o.onHover?.(id);
  };

  const onMove = (e: MouseEvent) => setHot(hit(e.clientX, e.clientY));
  const onLeave = () => setHot(null);
  // Capture: a mark over a link or a figure takes the click, so the link is
  // not followed and the figure lightbox does not open as well.
  const onClick = (e: MouseEvent) => {
    const sel = doc.getSelection();
    if (sel && !sel.isCollapsed) return; // the end of a drag that selected text
    const found = hitPiece(e.clientX, e.clientY);
    if (found === null) return;
    e.preventDefault();
    e.stopPropagation();
    o.onClick?.(found.id, found.piece);
  };
  doc.addEventListener("mousemove", onMove);
  doc.addEventListener("click", onClick, true);
  doc.documentElement.addEventListener("mouseleave", onLeave);

  // Text moves when the paper reflows — a resize, translations opening under
  // paragraphs, MathJax typesetting, fonts loading: redraw on the next frame.
  let frame = 0;
  const schedule = () => {
    if (frame || !win || detached) return;
    frame = win.requestAnimationFrame(() => {
      frame = 0;
      layout();
    });
  };
  const RO = (win as (Window & { ResizeObserver?: typeof ResizeObserver }) | null)?.ResizeObserver ?? globalThis.ResizeObserver;
  const resizes = RO ? new RO(schedule) : null;
  resizes?.observe(root);
  const MO = (win as (Window & { MutationObserver?: typeof MutationObserver }) | null)?.MutationObserver ?? globalThis.MutationObserver;
  const mutations = MO
    ? new MO((records) => {
        // Our own boxes and icons are not a reflow of the paper.
        if (records.some((r) => !layer.contains(r.target) && r.target !== style && !style.contains(r.target))) schedule();
      })
    : null;
  mutations?.observe(root, { childList: true, subtree: true, characterData: true });
  win?.addEventListener("resize", schedule);
  void doc.fonts?.ready.then(schedule).catch(() => undefined);
  void (win as (Window & MathJaxStartup) | null)?.MathJax?.startup?.promise?.then(schedule).catch(() => undefined);

  theme();
  sync();

  return {
    update: (next) => {
      const themeChanged = next.dark !== undefined && next.dark !== o.dark;
      if (next.chunks !== undefined && next.chunks !== o.chunks) chunkGen += 1;
      o = { ...o, ...next };
      if (themeChanged) theme();
      sync();
    },
    flash: (markId) => {
      const d = drawn.get(markId);
      const els = d?.els.length ? d.els : [...icons.values()].filter((i) => i.marks.includes(markId)).map((i) => i.el);
      if (!d || !els.length) return false;
      for (const el of els) el.setAttribute("data-flash", "");
      later(() => els.forEach((el) => el.removeAttribute("data-flash")), FLASH_MS);
      const box = d.boxes[0] ?? [...icons.values()].find((i) => i.marks.includes(markId))?.box;
      if (box && win) {
        const top = layer.getBoundingClientRect().top + box.top;
        if (top < 0 || top + box.height > win.innerHeight) {
          win.scrollTo({
            top: win.scrollY + top - win.innerHeight / 3,
            behavior: prefersReducedMotion() ? "auto" : "smooth",
          });
        }
      }
      return true;
    },
    detach: () => {
      detached = true;
      doc.removeEventListener("mousemove", onMove);
      doc.removeEventListener("click", onClick, true);
      doc.documentElement.removeEventListener("mouseleave", onLeave);
      resizes?.disconnect();
      mutations?.disconnect();
      win?.removeEventListener("resize", schedule);
      if (frame && win) win.cancelAnimationFrame(frame);
      timers.forEach(clearTimeout);
      timers.clear();
      layer.remove();
      style.remove();
      doc.documentElement.removeAttribute(HOT_ATTR);
      doc.documentElement.removeAttribute(PEN_ATTR);
      drawn.clear();
      icons.clear();
    },
  };
}
