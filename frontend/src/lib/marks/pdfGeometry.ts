/** Geometry for marks on PDF pages (Plan H5b.1).
 *
 * Everything is in page fractions `[x0, y0, x1, y1]` (0..1, top-left
 * origin), the unit Marker's blocks come in (`GET …/blocks`) and marks are
 * stored in, so a mark keeps its place at any zoom. Text marks take their
 * line boxes from the rendered text layer (react-pdf's spans); the Marker
 * block that holds a selection's centre gives its block id and section —
 * the two layouts can disagree by a little, so no exact match is needed.
 */
import type { MarkPiece, PaperPageBlock } from "@/types/notes";

export type Frac = [number, number, number, number];

/** The text layer react-pdf renders on each page. */
export const TEXT_LAYER = ".react-pdf__Page__textContent, .textLayer";
/** Marker blocks a mark can take whole (the corner mark button). */
export const FIGURE_TYPES = new Set(["Figure", "Picture"]);
export const EQUATION_TYPE = "Equation";
/** A box covering more than this share of an equation becomes that equation
 * (exactly half, give or take floating-point noise, does not). */
export const EQUATION_SNAP = 0.5;
/** A box this much inside a figure (and smaller than it) is one of its panels. */
const PANEL_INSIDE = 0.9;
const PANEL_MAX_SHARE = 0.85;
/** Line boxes closer than this (page widths) on one line join. */
const LINE_GAP = 0.02;

const round = (v: number) => Math.round(v * 10000) / 10000;
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** A client rect as fractions of the page's client rect. */
export function toFrac(r: Box, page: Box): Frac {
  const w = page.right - page.left || 1;
  const h = page.bottom - page.top || 1;
  return [
    round(clamp01((r.left - page.left) / w)),
    round(clamp01((r.top - page.top) / h)),
    round(clamp01((r.right - page.left) / w)),
    round(clamp01((r.bottom - page.top) / h)),
  ];
}

export function area(b: Frac): number {
  return Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
}

export function intersection(a: Frac, b: Frac): number {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 ? w * h : 0;
}

/** The share of `target` that `box` covers. */
export function coverage(box: Frac, target: Frac): number {
  const a = area(target);
  return a > 0 ? intersection(box, target) / a : 0;
}

export function union(boxes: readonly Frac[]): Frac | null {
  if (!boxes.length) return null;
  return [
    Math.min(...boxes.map((b) => b[0])),
    Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])),
    Math.max(...boxes.map((b) => b[3])),
  ];
}

const centre = (b: Frac): [number, number] => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
const inside = (b: Frac, x: number, y: number) => x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3];

/** One box per line: boxes on the same line that touch are joined. */
export function lineBoxes(boxes: readonly Frac[]): Frac[] {
  const out: Frac[] = [];
  for (const b of boxes) {
    if (b[2] <= b[0] || b[3] <= b[1]) continue;
    const line = out.find((o) => {
      const overlap = Math.min(o[3], b[3]) - Math.max(o[1], b[1]);
      const minH = Math.min(o[3] - o[1], b[3] - b[1]);
      return overlap > minH / 2 && b[0] <= o[2] + LINE_GAP && b[2] >= o[0] - LINE_GAP;
    });
    if (line) {
      line[0] = Math.min(line[0], b[0]);
      line[1] = Math.min(line[1], b[1]);
      line[2] = Math.max(line[2], b[2]);
      line[3] = Math.max(line[3], b[3]);
    } else {
      out.push([...b]);
    }
  }
  return out.map((b) => b.map(round) as Frac);
}

/** The client rects of characters `[start, end)` of a text-layer node: the
 * browser's own when it lays them out, else the span's box cut by the share
 * of characters (pdf.js stretches each span to its text's width). */
export function nodeRects(node: Text, start: number, end: number): Box[] {
  const doc = node.ownerDocument;
  const sub = doc.createRange();
  sub.setStart(node, start);
  sub.setEnd(node, end);
  const own = typeof sub.getClientRects === "function" ? Array.from(sub.getClientRects()) : [];
  const real = own.filter((r) => r.width > 0 && r.height > 0);
  if (real.length) return real;
  const span = node.parentElement;
  if (!span) return [];
  const r = span.getBoundingClientRect();
  const len = node.data.length || 1;
  const w = r.right - r.left;
  return [{ left: r.left + (w * start) / len, right: r.left + (w * end) / len, top: r.top, bottom: r.bottom }];
}

/** The Marker block holding a point: the smallest that contains it, else
 * the nearest (the text layer and Marker's layout differ a little). */
export function blockAt(blocks: readonly PaperPageBlock[], x: number, y: number): PaperPageBlock | null {
  const holding = blocks.filter((b) => inside(b.box, x, y));
  if (holding.length) return holding.reduce((a, b) => (area(b.box) < area(a.box) ? b : a));
  let best: PaperPageBlock | null = null;
  let bestD = 0.05; // further than this is no block of this text
  for (const b of blocks) {
    const dx = Math.max(b.box[0] - x, 0, x - b.box[2]);
    const dy = Math.max(b.box[1] - y, 0, y - b.box[3]);
    const d = Math.hypot(dx, dy);
    if (d < bestD) {
      bestD = d;
      best = b;
    }
  }
  return best;
}

/** One text-layer span on a page, as fractions. */
export interface LayerSpan {
  text: string;
  node: Text;
  box: Frac;
}

/** The text-layer spans of a page, in reading (DOM) order. */
export function layerSpans(page: Element): LayerSpan[] {
  const layer = page.querySelector(TEXT_LAYER);
  if (!layer) return [];
  const pb = page.getBoundingClientRect();
  const out: LayerSpan[] = [];
  const walker = page.ownerDocument.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const node = n as Text;
    if (!node.data.trim() || !node.parentElement) continue;
    const r = node.parentElement.getBoundingClientRect();
    out.push({ text: node.data, node, box: toFrac(r, pb) });
  }
  return out;
}

const tidy = (s: string) => s.replace(/\s+/g, " ").trim();

/** The text of the spans under a box, in reading order; of a span the box
 * cuts across, the words whose middle is inside it (pdf.js stretches a
 * span to its text's width, so a character's place is its share). */
export function textUnderBox(spans: readonly LayerSpan[], box: Frac): string {
  const parts: string[] = [];
  let lastBottom: number | null = null;
  for (const s of spans) {
    const cy = (s.box[1] + s.box[3]) / 2;
    if (cy < box[1] || cy > box[3]) continue;
    if (s.box[2] < box[0] || s.box[0] > box[2]) continue;
    const w = s.box[2] - s.box[0];
    const len = s.text.length || 1;
    let text = "";
    for (const m of s.text.matchAll(/\S+|\s+/g)) {
      if (!m[0].trim()) {
        text += m[0];
        continue;
      }
      const mid = s.box[0] + (w * (m.index + m[0].length / 2)) / len;
      if (mid >= box[0] && mid <= box[2]) text += m[0];
    }
    if (!text.trim()) continue;
    if (lastBottom !== null && s.box[1] >= lastBottom - 0.002) parts.push(" ");
    parts.push(text);
    lastBottom = s.box[3];
  }
  return tidy(parts.join(""));
}

/** The text Marker's OCR read inside a box (a scanned page): the blocks
 * mostly inside it, or whose middle it holds. */
export function ocrTextUnderBox(blocks: readonly PaperPageBlock[], box: Frac): string {
  return tidy(
    blocks
      .filter((b) => b.text.trim() && (coverage(box, b.box) > 0.5 || inside(box, ...centre(b.box))))
      .map((b) => b.text)
      .join(" "),
  );
}

/** The equation a box covers more than half of (the most covered). */
export function snapEquation(blocks: readonly PaperPageBlock[], box: Frac): PaperPageBlock | null {
  let best: PaperPageBlock | null = null;
  let bestC = EQUATION_SNAP;
  for (const b of blocks) {
    if (b.type !== EQUATION_TYPE) continue;
    const c = coverage(box, b.box);
    if (c > bestC + 1e-9) {
      bestC = c;
      best = b;
    }
  }
  return best;
}

/** The figure a box lies inside and is smaller than: the box is a panel. */
export function figureAround(blocks: readonly PaperPageBlock[], box: Frac): PaperPageBlock | null {
  const a = area(box);
  if (a <= 0) return null;
  return (
    blocks.find(
      (b) =>
        FIGURE_TYPES.has(b.type) &&
        intersection(box, b.box) / a >= PANEL_INSIDE &&
        a < PANEL_MAX_SHARE * area(b.box),
    ) ?? null
  );
}

/** The section of the block a box covers most (blocks with a section). */
export function sectionForBox(blocks: readonly PaperPageBlock[], box: Frac): string | null {
  let best: string | null = null;
  let bestA = 0;
  for (const b of blocks) {
    if (!b.section) continue;
    const a = intersection(box, b.box);
    if (a > bestA) {
      bestA = a;
      best = b.section;
    }
  }
  return best;
}

/** A figure's caption: the Caption block just below it (else just above)
 * that shares its columns; "" when there is none. */
export function captionFor(blocks: readonly PaperPageBlock[], figure: PaperPageBlock): string {
  const f = figure.box;
  const caps = blocks.filter(
    (b) => b.type === "Caption" && Math.min(b.box[2], f[2]) - Math.max(b.box[0], f[0]) > 0,
  );
  const gap = (b: PaperPageBlock) => (b.box[1] >= f[3] - 0.01 ? b.box[1] - f[3] : f[1] - b.box[3] + 0.05);
  const near = caps.filter((b) => gap(b) < 0.08).sort((a, b) => gap(a) - gap(b));
  return near[0] ? tidy(near[0].text) : "";
}

/** Pages in the reader (react-pdf puts the 1-based number on each). */
export const PAGE = "[data-page-number]";

/** A piece of a text selection on a PDF page (one per Marker block). */
export interface PdfPiece {
  quote: string;
  /** The text as the page lays it out, a line break between lines (what
   * the paper's reading text is searched with). */
  raw: string;
  /** 0-based. */
  page: number;
  block_id: string | null;
  section: string | null;
  /** Line boxes, page fractions. */
  rects: Frac[];
  /** The page text just before / after the selection (on its first / last
   * piece): which occurrence of a few common words it is. */
  before?: string;
  after?: string;
}

/** The selected part of one text-layer node, read off the page when the
 * selection was made (the text layer may re-render before it is saved). */
export interface PdfFragment {
  page: number;
  text: string;
  rects: Frac[];
  /** On the selection's first / last fragment: the page text around it. */
  before?: string;
  after?: string;
}

/** Page text kept on each side of a selection. */
export const CONTEXT_CHARS = 80;

/** The text of a page's text layer on one side of a point: before it (the
 * last CONTEXT_CHARS) or after it (the first). A line break in the layer
 * reads as one. */
function textBeside(layer: Element, node: Node, offset: number, side: "before" | "after"): string {
  const doc = layer.ownerDocument;
  const at = doc.createRange();
  at.setStart(node, offset);
  const wanted = side === "before" ? -1 : 1;
  const walker = doc.createTreeWalker(layer, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  let out = "";
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === Node.ELEMENT_NODE) {
      if ((n as Element).tagName === "BR" && at.comparePoint(n, 0) === wanted) out += "\n";
      continue;
    }
    const t = n as Text;
    const startCmp = at.comparePoint(t, 0);
    const endCmp = at.comparePoint(t, t.length);
    if (side === "before") {
      if (endCmp <= 0) out += t.data;
      else if (startCmp < 0 && t === node) out += t.data.slice(0, offset);
    } else if (startCmp >= 0) out += t.data;
    else if (endCmp > 0 && t === node) out += t.data.slice(offset);
  }
  return side === "before" ? out.slice(-CONTEXT_CHARS) : out.slice(0, CONTEXT_CHARS);
}

/** What a selection covers on the reader's PDF pages, node by node. */
export function selectionFragments(range: Range, root: Element): PdfFragment[] {
  const out: PdfFragment[] = [];
  const doc = root.ownerDocument;
  // Only the part of the reader the selection spans (not every page).
  const common = range.commonAncestorContainer;
  const scope = common.nodeType === Node.TEXT_NODE ? common.parentNode : common;
  const walker = doc.createTreeWalker(scope && root.contains(scope) ? scope : root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const node = n as Text;
    if (!range.intersectsNode(node)) continue;
    const span = node.parentElement;
    const pageEl = span?.closest(PAGE);
    if (!span || !pageEl || !span.closest(TEXT_LAYER) || !root.contains(pageEl)) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.data.length;
    if (end <= start || !node.data.slice(start, end).trim()) continue;
    const pageBox = pageEl.getBoundingClientRect();
    const rects = nodeRects(node, start, end).map((r) => toFrac(r, pageBox));
    if (!rects.length) continue;
    out.push({ page: Number(pageEl.getAttribute("data-page-number")) - 1, text: node.data.slice(start, end), rects });
  }
  const layerOf = (n: Node) =>
    (n.nodeType === Node.TEXT_NODE ? n.parentElement : (n as Element))?.closest(TEXT_LAYER) ?? null;
  const first = out[0];
  const last = out[out.length - 1];
  const startLayer = layerOf(range.startContainer);
  const endLayer = layerOf(range.endContainer);
  if (first && startLayer) first.before = textBeside(startLayer, range.startContainer, range.startOffset, "before");
  if (last && endLayer) last.after = textBeside(endLayer, range.endContainer, range.endOffset, "after");
  return out;
}

/** Fragments as pieces: split by the Marker block (per page) whose box
 * holds each fragment's middle. */
export function piecesFromFragments(
  fragments: readonly PdfFragment[],
  blocksByPage: Readonly<Record<number, readonly PaperPageBlock[]>>,
): PdfPiece[] {
  const out: (PdfPiece & { all: Frac[] })[] = [];
  let lastBottom: number | null = null;
  for (const f of fragments) {
    const all = union(f.rects)!;
    const block = blockAt(blocksByPage[f.page] ?? [], (all[0] + all[2]) / 2, (all[1] + all[3]) / 2);
    const blockId = block?.block_id ?? null;
    const last = out[out.length - 1];
    if (last && last.page === f.page && last.block_id === blockId) {
      // A new line reads as a line break; spans on one line join as they are.
      if (lastBottom !== null && all[1] >= lastBottom - 0.002) last.raw += "\n";
      last.raw += f.text;
      last.all.push(...f.rects);
      if (f.after !== undefined) last.after = f.after;
    } else {
      out.push({
        quote: "", raw: f.text, page: f.page, block_id: blockId, section: block?.section ?? null, rects: [], all: [...f.rects],
        ...(f.before !== undefined ? { before: f.before } : {}),
        ...(f.after !== undefined ? { after: f.after } : {}),
      });
    }
    lastBottom = all[3];
  }
  return out.map(({ all, ...p }) => ({ ...p, quote: provisionalQuote(p.raw), rects: lineBoxes(all) }));
}

/** A quote to show until the server has it: a word broken across lines
 * keeps its hyphen, without the line break. Whether it is one word
 * (`effective`) or a compound (`self-supervised`) only the paper's own
 * words tell, and the server reads those (`notes/dehyphen.py`). */
export function provisionalQuote(raw: string): string {
  return tidy(raw.replace(/(\w)-[ \t]*\n\s*(?=\w)/g, "$1-"));
}

const HYPHENS = new Set(["-", "‐", "‑"]);

/** `s` without its inner hyphens (one between two word characters), and
 * for each character kept its index in `s`. Text read off a page cannot
 * tell a broken word's hyphen from a compound's, so matching ignores both. */
export function withoutInnerHyphens(s: string): { text: string; at: number[] } {
  let text = "";
  const at: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    if (HYPHENS.has(ch) && i > 0 && /\w/.test(s[i - 1]!) && /\w/.test(s[i + 1] ?? "")) continue;
    text += ch;
    at.push(i);
  }
  return { text, at };
}

/** The pieces of a selection on the reader's PDF pages. */
export function selectionToPdfPieces(
  range: Range,
  root: Element,
  blocksByPage: Readonly<Record<number, readonly PaperPageBlock[]>>,
): PdfPiece[] {
  return piecesFromFragments(selectionFragments(range, root), blocksByPage);
}


export interface BoxInput {
  /** 0-based page. */
  page: number;
  box: Frac;
  blocks: readonly PaperPageBlock[];
  spans: readonly LayerSpan[];
  /** The page has no text layer: the text is Marker's OCR. */
  scanned: boolean;
}

export interface BoxResult {
  kind: "equation" | "region";
  section: string | null;
  piece: MarkPiece;
}

/** What a box drawn on a page marks: the equation it covers more than half
 * of (LaTeX + crop), else a region whose text is the text layer under it
 * (Marker's OCR on a scanned page) — one panel of a figure it lies in. */
export function boxMark({ page, box, blocks, spans, scanned }: BoxInput): BoxResult {
  const eq = snapEquation(blocks, box);
  if (eq) {
    return {
      kind: "equation",
      section: eq.section ?? sectionForBox(blocks, box),
      piece: { quote: eq.text, latex: eq.text, page, block_id: eq.block_id, box: eq.box },
    };
  }
  const quote = scanned ? ocrTextUnderBox(blocks, box) : textUnderBox(spans, box);
  const piece: MarkPiece = { quote, page, box, ocr: scanned };
  const figure = figureAround(blocks, box);
  if (figure) {
    // One panel of the figure (Fig. 3A): tied to it.
    return {
      kind: "region",
      section: figure.section ?? sectionForBox(blocks, box),
      piece: { ...piece, block_id: figure.block_id, panel: true },
    };
  }
  return { kind: "region", section: sectionForBox(blocks, box), piece };
}

/** The text of the first lines of a page's text layer at or below a
 * client y (the top of the reader's view), a line break between lines. */
export function linesBelow(page: Element, clientY: number, lines = 2): string {
  const pb = page.getBoundingClientRect();
  const y = (clientY - pb.top) / (pb.height || 1);
  const rows: { top: number; bottom: number; text: string }[] = [];
  for (const s of layerSpans(page)) {
    if (s.box[3] <= y) continue;
    const row = rows.find((r) => Math.min(r.bottom, s.box[3]) - Math.max(r.top, s.box[1]) > (s.box[3] - s.box[1]) / 2);
    if (row) row.text += s.text;
    else rows.push({ top: s.box[1], bottom: s.box[3], text: s.text });
  }
  return rows
    .sort((a, b) => a.top - b.top)
    .slice(0, lines)
    .map((r) => r.text.trim())
    .filter(Boolean)
    .join("\n");
}

/** A page's text-layer text, normalised the way the paper's reading text
 * is matched (ligatures unfolded, a word broken across lines by a hyphen
 * joined, whitespace collapsed), with each character's source. */
export interface PageText {
  page: Element;
  text: string;
  /** Per character: the text node and offset it came from (null: a space
   * between lines). */
  at: ({ node: Text; offset: number } | null)[];
}

export function pageText(page: Element): PageText {
  const out: PageText = { page, text: "", at: [] };
  const push = (ch: string, src: { node: Text; offset: number } | null) => {
    if (/\s/.test(ch)) {
      if (!out.text || out.text.endsWith(" ")) return;
      out.text += " ";
      out.at.push(src);
      return;
    }
    out.text += ch;
    out.at.push(src);
  };
  let lastBottom: number | null = null;
  for (const s of layerSpans(page)) {
    if (lastBottom !== null && s.box[1] >= lastBottom - 0.002) {
      // A new line: a hyphen at the end of the last one joins the word.
      const trimmed = out.text.replace(/ $/, "");
      if (/\w-$/.test(trimmed)) {
        out.text = trimmed.slice(0, -1);
        out.at.length = out.text.length;
      } else {
        push(" ", null);
      }
    }
    for (let i = 0; i < s.node.data.length; i++) {
      for (const ch of s.node.data[i]!.normalize("NFKC")) push(ch, { node: s.node, offset: i });
    }
    lastBottom = s.box[3];
  }
  return out;
}

/** How far apart (characters) two math-free runs of a quote may be on the
 * page: the glyphs of the formula between them. */
const MATH_GAP = 160;

/** The line boxes of a quote from the paper's reading text on a page, or
 * null when it is not there. Math (`$…$`) in the quote is a gap: the runs
 * between formulas are found in order, close together. */
export function findInPage(pt: PageText, quote: string): Frac[] | null {
  const runs = quote
    .normalize("NFKC")
    .split(/\$[^$]*\$/)
    .map((r) => withoutInnerHyphens(r.replace(/\s+/g, " ").trim()).text)
    .filter((r) => r.length >= 2);
  if (!runs.length) return null;
  // Both sides without inner hyphens: the page joins a line-end hyphen
  // (`self-` / `supervised` reads `selfsupervised`), the quote may keep it.
  const bare = withoutInnerHyphens(pt.text);
  const text = bare.text;
  for (let first = text.indexOf(runs[0]!); first >= 0; first = text.indexOf(runs[0]!, first + 1)) {
    let end = first + runs[0]!.length;
    let ok = true;
    for (const run of runs.slice(1)) {
      const at = text.indexOf(run, end);
      if (at < 0 || at - end > MATH_GAP) {
        ok = false;
        break;
      }
      end = at + run.length;
    }
    if (ok) return rectsOfSpan(pt, bare.at[first]!, bare.at[end - 1]! + 1);
  }
  return null;
}

function rectsOfSpan(pt: PageText, start: number, end: number): Frac[] {
  const pageBox = pt.page.getBoundingClientRect();
  const boxes: Frac[] = [];
  let run: { node: Text; from: number; to: number } | null = null;
  const flush = () => {
    if (run) boxes.push(...nodeRects(run.node, run.from, run.to + 1).map((r) => toFrac(r, pageBox)));
    run = null;
  };
  for (let i = start; i < end; i++) {
    const src = pt.at[i];
    if (!src) continue;
    if (run && run.node === src.node) run.to = Math.max(run.to, src.offset);
    else {
      flush();
      run = { node: src.node, from: src.offset, to: src.offset };
    }
  }
  flush();
  return lineBoxes(boxes);
}
