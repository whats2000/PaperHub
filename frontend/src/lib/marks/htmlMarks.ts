/** Marks drawn over a paper rendered as HTML (Plan H5b.1).
 *
 * OVERLAY ONLY: the paper's nodes are never touched. One layer (`div.ph-mk-
 * layer`, behind the text) holds a box per line of each marked passage and a
 * margin icon per marked line; one `<style id="ph-mk-style">` styles them.
 * The layer takes no pointer events: clicks and hovers are hit-tested
 * against the drawn boxes, so the text under a mark stays selectable.
 *
 * A highlight is a box in its colour, a quote a thin neutral underline, and
 * every marked line has a margin icon (with a count when several marks share
 * it). Hidden colours are not drawn; "Show my marks" off fades the layer out.
 */
import { prefersReducedMotion } from "@/lib/motion";
import { buildTextIndex, findPiece, type ChunkMap, type TextIndex } from "@/lib/marks/anchor";
import type { Mark, MarkColor, MarkColorInfo } from "@/types/notes";

export const MK_STYLE_ID = "ph-mk-style";
const LAYER_CLASS = "ph-mk-layer";
const HOT_ATTR = "data-ph-mk-hot";
/** Matches the 200 ms fades below plus a margin. */
const LEAVE_MS = 220;
const FLASH_MS = 1600;
const ICON_SIZE = 16;
const ICON_GAP = 6;

export const MARK_COLORS: readonly MarkColor[] = ["green", "blue", "pink", "purple"];

/** The palette (index.css `--hl-*`), used when the app's tokens can't be read. */
const FALLBACK: Record<"light" | "dark", Record<MarkColor, string>> = {
  light: { green: "#2f9e63", blue: "#3b7dd8", pink: "#d6457e", purple: "#8b5cd6" },
  dark: { green: "#36a067", blue: "#4b86dc", pink: "#d9578a", purple: "#9670dc" },
};

// The layer sits behind the text (z-index -1: above the page background,
// below the words). In the dark theme the whole page is inverted
// (applyIframeTheme); the layer is inverted back, like the figures, so the
// dark palette shows as itself.
const STYLE = `
.${LAYER_CLASS} {
  position: absolute; left: 0; top: 0; width: 0; height: 0; z-index: -1;
  pointer-events: none; transition: opacity 200ms ease-out;
  --mk-neutral: oklch(0.55 0 0); --mk-ring: oklch(0.55 0 0);
}
.${LAYER_CLASS}[data-hidden] { opacity: 0; }
.${LAYER_CLASS}[data-dark] { filter: invert(1) hue-rotate(180deg); --mk-ring: oklch(0.75 0 0); }
.ph-mk {
  position: absolute; border-radius: 2px;
  transition: opacity 200ms ease-out, box-shadow 150ms ease-out, background-color 200ms ease-out;
  animation: ph-mk-in 200ms ease-out;
}
.ph-mk[data-kind="highlight"] { background: color-mix(in srgb, var(--mk-c) var(--mk-alpha), transparent); }
.ph-mk[data-kind="quote"] { border-bottom: 1px solid var(--mk-neutral); border-radius: 0; }
${MARK_COLORS.map((c) => `.ph-mk[data-color="${c}"] { --mk-c: var(--mk-${c}); }`).join("\n")}
.ph-mk[data-dim] { opacity: .25; }
.ph-mk[data-hot] { box-shadow: 0 0 0 1px var(--mk-ring); }
.ph-mk[data-flash] { box-shadow: 0 0 0 3px var(--mk-ring); }
.ph-mk-icon {
  position: absolute; display: inline-flex; align-items: center; gap: 1px;
  height: ${ICON_SIZE}px; padding: 0 2px; border-radius: 4px; color: var(--mk-neutral);
  font: 500 10.5px/1 system-ui, -apple-system, "Segoe UI", sans-serif;
  transition: opacity 200ms ease-out, color 150ms ease-out, background-color 150ms ease-out;
  animation: ph-mk-in 200ms ease-out;
}
.ph-mk-icon svg { width: 12px; height: 12px; }
.ph-mk-icon[data-hot] { color: oklch(0.2 0 0); background: oklch(0 0 0 / 6%); }
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
  onClick?: (markId: string) => void;
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
  ranges: Range[];
  els: HTMLElement[];
  boxes: Box[];
}

interface Icon {
  el: HTMLElement;
  box: Box;
  marks: string[];
}

/** The pieces a mark shows on the paper (not erased, moved or lost). */
function livePieces(m: Mark) {
  return m.pieces.filter((p) => !p.erased && !p.removed && !p.lost);
}

/** Whether the HTML reader draws this mark (translation marks come later). */
function drawable(m: Mark, hidden: ReadonlySet<string>): boolean {
  if (m.deleted_at !== null || m.lang) return false;
  if (m.kind === "quote") return true;
  return m.kind === "highlight" && (m.color === null || !hidden.has(m.color));
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
  const fallback = FALLBACK[dark ? "dark" : "light"];
  const out = { ...fallback };
  try {
    const css = getComputedStyle(document.documentElement);
    for (const c of MARK_COLORS) {
      const v = css.getPropertyValue(`--hl-${c}`).trim();
      if (v) out[c] = v;
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
    setFlag(layer, "data-dark", o.dark);
    layer.style.setProperty("--mk-alpha", o.dark ? "32%" : "22%");
    const p = palette(o.dark);
    for (const c of MARK_COLORS) layer.style.setProperty(`--mk-${c}`, p[c]);
  };

  /** Positions every drawn mark and the margin icons from the live layout. */
  const layout = () => {
    const at = origin();
    const lines = new Map<string, { box: Box; marks: string[] }>();
    const viewRight = (doc.documentElement.clientWidth || win?.innerWidth || 0) - at.left;
    for (const [id, d] of drawn) {
      const boxes: Box[] = [];
      for (const range of d.ranges) {
        const rangeLines = lineBoxes(Array.from(range.getClientRects()));
        for (const r of rangeLines) {
          boxes.push({ left: r.left - at.left, top: r.top - at.top, width: r.width, height: r.height });
        }
        // The margin icon of the passage's first line.
        const first = rangeLines[0];
        const block = (range.startContainer.parentElement ?? null)?.closest("p, li, figcaption, td, th, dd, blockquote, div");
        if (first && block) {
          const right = block.getBoundingClientRect().right - at.left + ICON_GAP;
          const left = Math.max(0, viewRight > 0 ? Math.min(right, viewRight - ICON_SIZE - 4) : right);
          const top = first.top - at.top + (first.height - ICON_SIZE) / 2;
          const key = `${Math.round(left)}:${Math.round(first.top - at.top)}`;
          const line = lines.get(key) ?? { box: { left, top, width: ICON_SIZE + 10, height: ICON_SIZE }, marks: [] };
          if (!line.marks.includes(id)) line.marks.push(id);
          lines.set(key, line);
        }
      }
      d.boxes = boxes;
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
        if (m.kind === "highlight" && m.color) el.dataset.color = m.color;
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
      const key = JSON.stringify(livePieces(m));
      const prev = drawn.get(id);
      if (prev?.key === key) continue;
      index ??= buildTextIndex(doc);
      const ranges = livePieces(m)
        .map((p) => findPiece(index!, p, o.chunks))
        .filter((r): r is Range => r !== null);
      if (!ranges.length) {
        prev?.els.forEach(leave);
        drawn.delete(id);
        continue;
      }
      drawn.set(id, { key, ranges, els: prev?.els ?? [], boxes: [] });
    }
    index = null; // the document may change before the next sync
    setFlag(layer, "data-hidden", !o.visible);
    layout();
  };

  const hit = (x: number, y: number): string | null => {
    if (!o.visible) return null;
    const at = origin();
    const lx = x - at.left;
    const ly = y - at.top;
    for (const icon of icons.values()) if (contains(icon.box, lx, ly)) return icon.marks[0] ?? null;
    let found: string | null = null;
    for (const [id, d] of drawn) if (d.boxes.some((b) => contains(b, lx, ly))) found = id;
    return found;
  };

  const setHot = (id: string | null) => {
    if (id === hot) return;
    hot = id;
    setFlag(doc.documentElement, HOT_ATTR, id !== null);
    paint();
    o.onHover?.(id);
  };

  const onMove = (e: MouseEvent) => setHot(hit(e.clientX, e.clientY));
  const onLeave = () => setHot(null);
  const onClick = (e: MouseEvent) => {
    const sel = doc.getSelection();
    if (sel && !sel.isCollapsed) return; // the end of a drag that selected text
    const id = hit(e.clientX, e.clientY);
    if (id !== null) o.onClick?.(id);
  };
  doc.addEventListener("mousemove", onMove);
  doc.addEventListener("click", onClick);
  doc.documentElement.addEventListener("mouseleave", onLeave);

  // Text moves when the paper reflows (a resize, translations opening under
  // paragraphs, math typesetting): redraw on the next frame.
  let frame = 0;
  const schedule = () => {
    if (frame || !win) return;
    frame = win.requestAnimationFrame(() => {
      frame = 0;
      layout();
    });
  };
  const RO = (win as (Window & { ResizeObserver?: typeof ResizeObserver }) | null)?.ResizeObserver ?? globalThis.ResizeObserver;
  const observer = RO ? new RO(schedule) : null;
  observer?.observe(root);
  win?.addEventListener("resize", schedule);

  theme();
  sync();

  return {
    update: (next) => {
      const themeChanged = next.dark !== undefined && next.dark !== o.dark;
      o = { ...o, ...next };
      if (themeChanged) theme();
      sync();
    },
    flash: (markId) => {
      const d = drawn.get(markId);
      if (!d?.els.length) return false;
      for (const el of d.els) el.setAttribute("data-flash", "");
      later(() => d.els.forEach((el) => el.removeAttribute("data-flash")), FLASH_MS);
      const box = d.boxes[0];
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
      doc.removeEventListener("mousemove", onMove);
      doc.removeEventListener("click", onClick);
      doc.documentElement.removeEventListener("mouseleave", onLeave);
      observer?.disconnect();
      win?.removeEventListener("resize", schedule);
      if (frame && win) win.cancelAnimationFrame(frame);
      timers.forEach(clearTimeout);
      timers.clear();
      layer.remove();
      style.remove();
      doc.documentElement.removeAttribute(HOT_ATTR);
      drawn.clear();
      icons.clear();
    },
  };
}
