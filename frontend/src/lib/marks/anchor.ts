/** Anchors for marks on a paper rendered as HTML (Plan H5b.1).
 *
 * A piece stores its quoted text, the chunks it sits in (chunk ids, from the
 * nearest `phchunk-N` spans) and up to 32 characters of text on each side.
 * Everything here reads the document through one text index: the paper's
 * text with whitespace runs collapsed, translations, math and PaperHub's
 * own overlays left out, so a quote taken from a selection is found again
 * the same way. The document itself is never changed.
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
}

const CONTEXT = 32;
const CHUNK_PREFIX = "phchunk-";
/** Not the paper's own reading text: translations (H1), math (its glyphs
 * are drawn, not text), PaperHub overlays, scripts. */
const SKIPPED = "script, style, noscript, template, .ph-tr, .math, mjx-container, math, [data-ph-overlay]";
/** A piece never crosses one of these. */
const BLOCK =
  "p, li, figcaption, h1, h2, h3, h4, h5, h6, td, th, dt, dd, pre, blockquote, caption, " +
  "div, section, article, header, footer, figure, table, ul, ol, body";

const HEADING = /^H[1-6]$/;

export function normaliseText(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/** The paper's text, whitespace-collapsed, with a map back to the DOM. */
export interface TextIndex {
  doc: Document;
  text: string;
  nodes: Text[];
  /** Per character of `text`: its node (index into `nodes`) and offset; a
   * paragraph break is a space at the end of the node before it (offset -1). */
  nodeOf: number[];
  offsetOf: number[];
  /** The block element each node sits in. */
  blockOf: Element[];
  /** Where each chunk span sits in `text` (the index of the next character). */
  spans: { domId: string; at: number }[];
}

export function buildTextIndex(doc: Document): TextIndex {
  const index: TextIndex = { doc, text: "", nodes: [], nodeOf: [], offsetOf: [], blockOf: [], spans: [] };
  const root = doc.body ?? doc.documentElement;
  const chars: string[] = [];
  let prevSpace = true; // no leading space
  let prevBlock: Element | null = null;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.nodeType === 1 && (n as Element).matches(SKIPPED) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === 1) {
      const id = (n as Element).id;
      if (id.startsWith(CHUNK_PREFIX)) index.spans.push({ domId: id, at: chars.length });
      continue;
    }
    const node = n as Text;
    const block = node.parentElement?.closest(BLOCK) ?? root;
    if (prevBlock !== null && block !== prevBlock && !prevSpace) {
      // A paragraph break reads as a space.
      chars.push(" ");
      index.nodeOf.push(index.nodes.length - 1);
      index.offsetOf.push(-1);
      prevSpace = true;
    }
    prevBlock = block;
    const ni = index.nodes.length;
    index.nodes.push(node);
    index.blockOf.push(block);
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
      index.nodeOf.push(ni);
      index.offsetOf.push(i);
    }
  }
  index.text = chars.join("");
  return index;
}

/** The DOM point just before character `i`. */
function pointBefore(index: TextIndex, i: number): [Node, number] {
  const node = index.nodes[index.nodeOf[i]!]!;
  const off = index.offsetOf[i]!;
  return off < 0 ? [node, node.length] : [node, off];
}

/** The DOM point just after character `i`. */
function pointAfter(index: TextIndex, i: number): [Node, number] {
  const node = index.nodes[index.nodeOf[i]!]!;
  const off = index.offsetOf[i]!;
  return off < 0 ? [node, node.length] : [node, off + 1];
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

/** The characters of `index` inside `range`, as [start, end). */
function rangeSpan(index: TextIndex, range: Range): [number, number] {
  const n = index.text.length;
  const start = firstTrue(n, (i) => range.comparePoint(...pointBefore(index, i)) >= 0);
  const end = firstTrue(n, (i) => range.comparePoint(...pointAfter(index, i)) > 0);
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

function blockAt(index: TextIndex, i: number): Element | null {
  return index.offsetOf[i]! < 0 ? null : index.blockOf[index.nodeOf[i]!]!;
}

/** One piece per paragraph the selection touches (a stroke never crosses a
 * paragraph break); empty when it holds no paper text. */
export function selectionToPieces(doc: Document, range: Range, chunks: ChunkMap | null): HtmlPiece[] {
  return piecesIn(buildTextIndex(doc), range, chunks);
}

export function piecesIn(index: TextIndex, range: Range, chunks: ChunkMap | null): HtmlPiece[] {
  const [start, end] = rangeSpan(index, range);
  const pieces: (HtmlPiece & { heading: boolean })[] = [];
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
      pieces.push({
        quote: index.text.slice(s, e),
        chunk_start: chunkAt(index, s, chunks),
        chunk_end: chunkAt(index, e - 1, chunks),
        prefix: index.text.slice(Math.max(0, s - CONTEXT), s),
        suffix: index.text.slice(e, e + CONTEXT),
        heading: HEADING.test(block.tagName),
      });
    }
    a = b;
  }
  // A heading caught between paragraphs is not what was marked (and it sits
  // before its section's first chunk span); a heading alone is kept.
  const text = pieces.filter((p) => !p.heading);
  return (text.length ? text : pieces).map(({ heading: _heading, ...p }) => p);
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
  const quote = normaliseText(String(piece.quote ?? ""));
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
