/** Anchors for marks on a paper rendered as HTML (Plan H5b.1).
 *
 * A piece stores its quoted text, the chunks it sits in (chunk ids, from the
 * nearest `phchunk-N` spans) and up to 32 characters of text on each side.
 * Everything here reads the document through one text index, the paper's
 * reading text: whitespace runs collapsed, math as its TeX (`$…$`),
 * translations and PaperHub's own overlays left out. The backend re-anchors
 * with the same rule (`notes/html_text.py`; shared fixture
 * `frontend/src/lib/__fixtures__/mark_text`). The document itself is never changed.
 */
import type { MarkPiece } from "@/types/notes";

/** A chunk with a span in the rendered HTML (`GET …/chunk-anchors`). */
export interface ChunkAnchor {
  id: number;
  dom_id: string;
  section: string | null;
}

export interface ChunkMap {
  byDom: Map<string, ChunkAnchor>;
  byId: Map<number, ChunkAnchor>;
}

export function chunkMap(anchors: readonly ChunkAnchor[]): ChunkMap {
  return {
    byDom: new Map(anchors.map((a) => [a.dom_id, a])),
    byId: new Map(anchors.map((a) => [a.id, a])),
  };
}

/** An HTML piece as the backend stores it. */
export interface HtmlPiece extends MarkPiece {
  quote: string;
  chunk_start: number | null;
  chunk_end: number | null;
  prefix: string;
  suffix: string;
  text_start?: number;
  text_end?: number;
}

const CONTEXT = 32;
const CHUNK_PREFIX = "phchunk-";
/** Not the paper's own reading text: translations (H1), PaperHub overlays,
 * scripts. */
const SKIPPED = "script, style, noscript, template, .ph-tr, [data-ph-overlay]";
/** Math reads as its TeX source, `$…$`, one unit that is never split. */
const MATH = ".math, mjx-container, math";
/** A piece never crosses one of these. */
const BLOCK =
  "p, li, figcaption, h1, h2, h3, h4, h5, h6, td, th, dt, dd, pre, blockquote, caption, " +
  "div, section, article, header, footer, figure, table, ul, ol, body";
/** Source delimiters around a math element's TeX. */
const DELIMS: [string, string][] = [
  ["\\(", "\\)"],
  ["\\[", "\\]"],
  ["$$", "$$"],
  ["$", "$"],
];

const HEADING = /^H[1-6]$/;

export function normaliseText(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/** `$TeX$` for a math element's source ("" when it has none). Mirrors the
 * backend's `notes/html_text.tex_text`. */
export function texText(source: string): string {
  let tex = normaliseText(source);
  for (const [a, b] of DELIMS) {
    if (tex.startsWith(a) && tex.endsWith(b) && tex.length >= a.length + b.length) {
      tex = normaliseText(tex.slice(a.length, tex.length - b.length));
      break;
    }
  }
  return tex ? `$${tex}$` : "";
}

interface MathJaxLike {
  startup?: { document?: { getMathItemsWithin?: (el: Element) => { math?: unknown }[] } };
}

/** A math element's TeX source: its TeX annotation, else the source MathJax
 * typeset it from, else (not typeset yet) its text. */
function mathSource(el: Element): string {
  const annotation = el.querySelector('annotation[encoding="application/x-tex"]');
  if (annotation) return annotation.textContent ?? "";
  const mj = (el.ownerDocument.defaultView as (Window & { MathJax?: MathJaxLike }) | null)?.MathJax;
  try {
    const item = mj?.startup?.document?.getMathItemsWithin?.(el)?.[0];
    if (typeof item?.math === "string") return item.math;
  } catch {
    /* MathJax not ready: fall through */
  }
  if (el.matches("mjx-container") || el.querySelector("mjx-container")) return "";
  return el.textContent ?? "";
}

/** A run of the index: a text node, or a math element read as one unit. */
type Unit = { node: Text; math: null; block: Element } | { node: null; math: Element; block: Element };

/** The paper's reading text, with a map back to the DOM. */
export interface TextIndex {
  doc: Document;
  text: string;
  units: Unit[];
  /** Per character of `text`: its unit and offset in it; a paragraph break
   * is a space at the end of the unit before it (offset -1). */
  unitOf: number[];
  offsetOf: number[];
  /** Where each chunk span sits in `text` (the index of the next character). */
  spans: { domId: string; at: number }[];
}

export function buildTextIndex(doc: Document): TextIndex {
  return buildTextIndexIn(doc.body ?? doc.documentElement);
}

/** What an index of part of a page leaves out, and reads as math. */
export interface TextIndexOptions {
  /** Left out (default: translations, overlays, scripts). */
  skip?: string;
  /** Each read as one `$TeX$` unit (default: MathJax and MathML math). */
  math?: string;
}

/** The reading text of one element: the paper's body, or a translation
 * (which the paper's own index leaves out). */
export function buildTextIndexIn(root: Element, opts: TextIndexOptions = {}): TextIndex {
  const skip = opts.skip ?? SKIPPED;
  const mathSel = opts.math ?? MATH;
  const doc = root.ownerDocument;
  const index: TextIndex = { doc, text: "", units: [], unitOf: [], offsetOf: [], spans: [] };
  const chars: string[] = [];
  let prevSpace = true; // no leading space
  let prevBlock: Element | null = null;

  const enterBlock = (block: Element) => {
    if (prevBlock !== null && block !== prevBlock && !prevSpace) {
      // A paragraph break reads as a space.
      chars.push(" ");
      index.unitOf.push(index.units.length - 1);
      index.offsetOf.push(-1);
      prevSpace = true;
    }
    prevBlock = block;
  };

  const visit = (el: Element) => {
    for (let n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) {
        const node = n as Text;
        if (!node.data) continue;
        const block = node.parentElement?.closest(BLOCK) ?? root;
        enterBlock(block);
        const ui = index.units.length;
        index.units.push({ node, math: null, block });
        const data = node.data;
        for (let i = 0; i < data.length; i++) {
          const c = data[i]!;
          if (/\s/.test(c)) {
            if (prevSpace) continue;
            chars.push(" ");
            prevSpace = true;
          } else {
            chars.push(c);
            prevSpace = false;
          }
          index.unitOf.push(ui);
          index.offsetOf.push(i);
        }
        continue;
      }
      if (n.nodeType !== 1) continue;
      const child = n as Element;
      if (child.matches(skip)) continue;
      if (child.matches(mathSel)) {
        const tex = texText(mathSource(child));
        if (!tex) continue;
        const block = child.closest(BLOCK) ?? root;
        enterBlock(block);
        const ui = index.units.length;
        index.units.push({ node: null, math: child, block });
        for (let i = 0; i < tex.length; i++) {
          chars.push(tex[i]!);
          index.unitOf.push(ui);
          index.offsetOf.push(i);
        }
        prevSpace = false;
        continue;
      }
      if (child.id.startsWith(CHUNK_PREFIX)) index.spans.push({ domId: child.id, at: chars.length });
      visit(child);
    }
  };
  visit(root);
  index.text = chars.join("");
  return index;
}

type Point = [Node, number];

/** The DOM points before and after a math element. */
function aroundMath(el: Element): [Point, Point] {
  const parent = el.parentNode!;
  const at = Array.prototype.indexOf.call(parent.childNodes, el);
  return [
    [parent, at],
    [parent, at + 1],
  ];
}

function mathOf(index: TextIndex, i: number): Element | null {
  return index.offsetOf[i]! >= 0 ? index.units[index.unitOf[i]!]!.math : null;
}

/** The DOM point just before character `i` (a math unit: before it). */
function pointBefore(index: TextIndex, i: number): Point {
  const unit = index.units[index.unitOf[i]!]!;
  const off = index.offsetOf[i]!;
  if (unit.math) return aroundMath(unit.math)[off < 0 ? 1 : 0];
  return off < 0 ? [unit.node, unit.node.length] : [unit.node, off];
}

/** The DOM point just after character `i` (a math unit: after it). */
function pointAfter(index: TextIndex, i: number): Point {
  const unit = index.units[index.unitOf[i]!]!;
  const off = index.offsetOf[i]!;
  if (unit.math) return aroundMath(unit.math)[1];
  return off < 0 ? [unit.node, unit.node.length] : [unit.node, off + 1];
}

/** First index in [0, n) where `pred` holds, for a predicate that is false
 * then true along the text. */
function firstTrue(n: number, pred: (i: number) => boolean): number {
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (pred(mid)) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

const same = (p: Point, node: Node, off: number) => p[0] === node && p[1] === off;

/** The characters of `index` inside `range`, as [start, end). A math unit
 * the range overlaps at all is taken whole. */
export function rangeSpan(index: TextIndex, range: Range): [number, number] {
  const n = index.text.length;
  const start = firstTrue(n, (i) => {
    const math = mathOf(index, i);
    if (!math) return range.comparePoint(...pointBefore(index, i)) >= 0;
    // In unless the math ends where the range starts (or before).
    const after = aroundMath(math)[1];
    return range.comparePoint(...after) >= 0 && !same(after, range.startContainer, range.startOffset);
  });
  const end = firstTrue(n, (i) => {
    const math = mathOf(index, i);
    if (!math) return range.comparePoint(...pointAfter(index, i)) > 0;
    // Out once the math starts where the range ends (or after).
    const before = aroundMath(math)[0];
    return range.comparePoint(...before) > 0 || same(before, range.endContainer, range.endOffset);
  });
  return [start, Math.max(start, end)];
}

/** The chunk span last opened at or before character `at`. */
function chunkAt(index: TextIndex, at: number, chunks: ChunkMap | null): number | null {
  if (!chunks) return null;
  let found: number | null = null;
  for (const s of index.spans) {
    if (s.at > at) break;
    const anchor = chunks.byDom.get(s.domId);
    if (anchor) found = anchor.id;
  }
  return found;
}

/** The first chunk span opened at or after character `at` (a heading's
 * section starts after it). */
function chunkAfter(index: TextIndex, at: number, chunks: ChunkMap | null): number | null {
  if (!chunks) return null;
  for (const s of index.spans) {
    if (s.at < at) continue;
    const anchor = chunks.byDom.get(s.domId);
    if (anchor) return anchor.id;
  }
  return null;
}

function blockAt(index: TextIndex, i: number): Element | null {
  return index.offsetOf[i]! < 0 ? null : index.units[index.unitOf[i]!]!.block;
}

/** One piece per paragraph the selection touches (a stroke never crosses a
 * paragraph break); empty when it holds no paper text. */
export function selectionToPieces(doc: Document, range: Range, chunks: ChunkMap | null): HtmlPiece[] {
  return piecesIn(buildTextIndex(doc), range, chunks);
}

export function piecesIn(index: TextIndex, range: Range, chunks: ChunkMap | null): HtmlPiece[] {
  return placedPiecesIn(index, range, chunks).map((p) => p.piece);
}

/** A selection's pieces with the paragraph element each sits in (the
 * highlighter joins strokes of one paragraph). */
export function selectionToPlacedPieces(
  doc: Document,
  range: Range,
  chunks: ChunkMap | null,
): { piece: HtmlPiece; block: Element }[] {
  return placedPiecesIn(buildTextIndex(doc), range, chunks);
}

function placedPiecesIn(index: TextIndex, range: Range, chunks: ChunkMap | null): { piece: HtmlPiece; block: Element }[] {
  const [start, end] = rangeSpan(index, range);
  const pieces: (HtmlPiece & { heading: boolean; block: Element })[] = [];
  let a = start;
  while (a < end) {
    const block = blockAt(index, a);
    let b = a;
    while (b < end && blockAt(index, b) === block && block !== null) b++;
    if (block === null) {
      a += 1; // a paragraph break
      continue;
    }
    let s = a;
    let e = b;
    while (s < e && index.text[s] === " ") s++;
    while (e > s && index.text[e - 1] === " ") e--;
    if (e > s) {
      const heading = HEADING.test(block.tagName);
      // A heading files under its own section: the chunk after it.
      const first = heading ? (chunkAfter(index, e, chunks) ?? chunkAt(index, s, chunks)) : chunkAt(index, s, chunks);
      pieces.push({
        quote: index.text.slice(s, e),
        chunk_start: first,
        chunk_end: heading ? first : chunkAt(index, e - 1, chunks),
        prefix: index.text.slice(Math.max(0, s - CONTEXT), s),
        suffix: index.text.slice(e, e + CONTEXT),
        // Its span in the reading text (the backend's re-anchor keeps it
        // current): the order pieces read in, and a hint among repeats.
        text_start: s,
        text_end: e,
        heading,
        block,
      });
    }
    a = b;
  }
  // A heading caught between paragraphs is not what was marked; a heading
  // alone is kept.
  const text = pieces.filter((p) => !p.heading);
  return (text.length ? text : pieces).map(({ heading: _heading, block, ...p }) => ({ piece: p, block }));
}

/** Consecutive pieces under the same section (a mark files under one). */
export function groupBySection(
  pieces: readonly HtmlPiece[],
  chunks: ChunkMap | null,
): { section: string | null; pieces: HtmlPiece[] }[] {
  const groups: { section: string | null; pieces: HtmlPiece[] }[] = [];
  for (const p of pieces) {
    const section = (p.chunk_start !== null && chunks?.byId.get(p.chunk_start)?.section) || null;
    const last = groups[groups.length - 1];
    if (last && last.section === section) last.pieces.push(p);
    else groups.push({ section, pieces: [p] });
  }
  return groups;
}

function commonSuffix(a: string, b: string): number {
  let n = 0;
  while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++;
  return n;
}

function commonPrefix(a: string, b: string): number {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

/** Where a stored piece is in the document now: its quote (blind to
 * whitespace), the repeat whose context matches best, then the one nearest
 * its chunk. Null when the quote is not in the paper. */
export function findPiece(index: TextIndex, piece: MarkPiece, chunks: ChunkMap | null): Range | null {
  const quote = normaliseText(typeof piece.quote === "string" ? piece.quote : "");
  if (!quote) return null;
  const starts: number[] = [];
  for (let s = index.text.indexOf(quote); s >= 0; s = index.text.indexOf(quote, s + 1)) starts.push(s);
  if (!starts.length) return null;
  const prefix = normaliseText(typeof piece.prefix === "string" ? piece.prefix : "");
  const suffix = normaliseText(typeof piece.suffix === "string" ? piece.suffix : "");
  const chunkId = typeof piece.chunk_start === "number" ? piece.chunk_start : null;
  const domId = chunkId !== null ? chunks?.byId.get(chunkId)?.dom_id : undefined;
  const hint = index.spans.find((s) => s.domId === domId)?.at ?? 0;
  const score = (s: number): [number, number] => {
    const before = index.text.slice(Math.max(0, s - prefix.length - 1), s).trimEnd();
    const after = index.text.slice(s + quote.length, s + quote.length + suffix.length + 1).trimStart();
    const ctx = (prefix ? commonSuffix(before, prefix.trimEnd()) : 0) + (suffix ? commonPrefix(after, suffix.trimStart()) : 0);
    return [ctx, -Math.abs(s - hint)];
  };
  let best = starts[0]!;
  let bestScore = score(best);
  for (const s of starts.slice(1)) {
    const sc = score(s);
    if (sc[0] > bestScore[0] || (sc[0] === bestScore[0] && sc[1] > bestScore[1])) {
      best = s;
      bestScore = sc;
    }
  }
  const range = index.doc.createRange();
  range.setStart(...pointBefore(index, best));
  range.setEnd(...pointAfter(index, best + quote.length - 1));
  return range;
}

/** The range of characters `[start, end)` of the reading text (a span the
 * backend found there for a mark made on the PDF); null when the text is
 * shorter (a different render). */
export function rangeAt(index: TextIndex, start: number, end: number): Range | null {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > index.text.length) {
    return null;
  }
  const range = index.doc.createRange();
  range.setStart(...pointBefore(index, start));
  range.setEnd(...pointAfter(index, end - 1));
  return range;
}
