/** Marks on a translation (Plan H5b.1, H1 bilingual reading).
 *
 * A piece marked on a translation keeps the translated words (`quote`),
 * the language (`lang`), the translation block it was read from
 * (`source_ref`: `h:<n>` under an HTML paragraph, `p:<block>` for a PDF
 * paragraph) and the paragraph that block translates (`orig`). It carries
 * that paragraph's own anchor too — chunk ids, context and span in the
 * reading text (HTML), or its page and Marker block (PDF) — so it files
 * under the paragraph's section and is re-anchored by the paragraph, never
 * by the translated words (there is no reliable word alignment). `tr_start`
 * / `tr_end` and `tr_prefix` / `tr_suffix` place the words in the
 * translation.
 *
 * Where the mark's language is on screen its words are highlighted in the
 * translation; otherwise the paragraph shows a 文 marker. */
import {
  buildTextIndexIn,
  findPiece,
  rangeSpan,
  selectionToPieces,
  type ChunkMap,
  type HtmlPiece,
  type TextIndex,
} from "@/lib/marks/anchor";
import { TR_CLASS, TR_LANG_ATTR } from "@/lib/htmlTranslation";
import { sameLanguage } from "@/lib/language";
import type { Mark, MarkPiece } from "@/types/notes";

/** A translation under an HTML paragraph (`lib/htmlTranslation.ts`). */
export const TR_BOX = `.${TR_CLASS}`;
const TR_INNER = ".ph-tr-inner";
const TR_CONTEXT = 16;
/** Not the translation's words: the spinner, Retry, overlays. */
const BOX_SKIP = "script, style, noscript, template, [data-ph-overlay], .ph-tr-spin, .ph-tr-retry";
/** The PDF translation card: KaTeX formulas read as their TeX, the "continued
 * on page N" dividers left out. */
const CARD_SKIP = "script, style, noscript, template, [data-ph-overlay], [data-break]";
const CARD_MATH = ".math, mjx-container, math, .katex";
/** The paragraph elements the HTML overlay translates. */
const PARAGRAPH = "p, li, figcaption, h1, h2, h3, h4, h5, h6";

/** A piece marked on a translation (see the module comment). */
export interface TranslationPiece extends MarkPiece {
  quote: string;
  orig: string;
  lang: string;
  source_ref: string;
  tr_start: number;
  tr_end: number;
  tr_prefix: string;
  tr_suffix: string;
}

/** Whether a mark was made on a translation. */
export const isTranslationMark = (m: Pick<Mark, "lang">) => typeof m.lang === "string" && m.lang !== "";

/** The translated words of a selection in an index: its span (blank ends
 * trimmed) and context. Null when it holds none. */
function wordsIn(index: TextIndex, range: Range) {
  let [s, e] = rangeSpan(index, range);
  while (s < e && index.text[s] === " ") s++;
  while (e > s && index.text[e - 1] === " ") e--;
  if (e <= s) return null;
  return {
    quote: index.text.slice(s, e),
    tr_start: s,
    tr_end: e,
    tr_prefix: index.text.slice(Math.max(0, s - TR_CONTEXT), s),
    tr_suffix: index.text.slice(e, e + TR_CONTEXT),
  };
}

// ---- HTML: the translation under each paragraph ------------------------

/** Whether a node is inside a translation box (in any state). */
export function inTranslation(node: Node | null): boolean {
  const el = node ? (node.nodeType === 1 ? (node as Element) : node.parentElement) : null;
  return !!el?.closest(TR_BOX);
}

/** The live, filled translation box a node sits in (null: none, or one
 * still loading or leaving). */
export function translationBoxOf(node: Node | null): HTMLElement | null {
  const el = node ? (node.nodeType === 1 ? (node as Element) : node.parentElement) : null;
  const box = el?.closest(TR_BOX) ?? null;
  return box && isLiveBox(box) ? (box as HTMLElement) : null;
}

function isLiveBox(box: Element): boolean {
  return !box.hasAttribute("data-ph-tr-leaving") && box.getAttribute("data-ph-tr-state") === "ok";
}

/** The paragraph a translation box translates: just before it (a box still
 * collapsing away may sit between them). */
export function sourceOf(box: Element): Element | null {
  let el = box.previousElementSibling;
  while (el?.matches(TR_BOX)) el = el.previousElementSibling;
  return el;
}

/** The live, filled translation under a paragraph, in `lang` (null: none on
 * screen in that language). */
export function translationUnder(block: Element, lang: string): HTMLElement | null {
  const next = block.nextElementSibling;
  if (!next?.matches(TR_BOX) || !isLiveBox(next)) return null;
  return sameLanguage(next.getAttribute(TR_LANG_ATTR), lang) ? (next as HTMLElement) : null;
}

/** A translation box's words, as one text (the same rule as the paper's). */
export function translationIndex(box: Element): TextIndex {
  return buildTextIndexIn(box.querySelector(TR_INNER) ?? box, { skip: BOX_SKIP });
}

/** A marked translation's piece, with the paragraph it translates. */
export interface PlacedTranslationPiece {
  piece: TranslationPiece & HtmlPiece;
  /** The paragraph translated (the highlighter's place). */
  block: Element;
  section: string | null;
}

/** The translated words a selection holds: one piece per translated
 * paragraph it touches (the paper's own text it holds is the paper's
 * pieces, `selectionToPieces`). Empty when it holds none. */
export function translationSelection(doc: Document, range: Range, chunks: ChunkMap | null): PlacedTranslationPiece[] {
  const out: PlacedTranslationPiece[] = [];
  for (const box of Array.from(doc.querySelectorAll(TR_BOX))) {
    if (!isLiveBox(box) || !range.intersectsNode(box)) continue;
    const lang = box.getAttribute(TR_LANG_ATTR);
    const key = box.getAttribute("data-ph-tr-key");
    const source = sourceOf(box);
    const words = wordsIn(translationIndex(box), range);
    if (!lang || !key || !source || !words) continue;
    const whole = doc.createRange();
    whole.selectNodeContents(source);
    const orig = selectionToPieces(doc, whole, chunks);
    const first = orig[0];
    const last = orig[orig.length - 1];
    if (!first || !last) continue;
    const piece: TranslationPiece & HtmlPiece = {
      ...words,
      orig: orig.map((p) => p.quote).join(" "),
      lang,
      source_ref: key,
      chunk_start: first.chunk_start,
      chunk_end: last.chunk_end,
      prefix: first.prefix,
      suffix: last.suffix,
      text_start: first.text_start,
      text_end: last.text_end,
    };
    const section = (first.chunk_start !== null && chunks?.byId.get(first.chunk_start)?.section) || null;
    out.push({ piece, block: source, section });
  }
  return out;
}

/** The paragraph a mark's piece translates, found by that paragraph's own
 * text (`orig`) in the paper's reading text. */
export function sourceParagraph(index: TextIndex, piece: MarkPiece, chunks: ChunkMap | null): Element | null {
  const orig = typeof piece.orig === "string" ? piece.orig : "";
  if (!orig) return null;
  const range = findPiece(index, { ...piece, quote: orig }, chunks);
  const start = range?.startContainer;
  const el = start ? (start.nodeType === 1 ? (start as Element) : start.parentElement) : null;
  return el?.closest(PARAGRAPH) ?? null;
}

/** Where a piece's words are in a translation (null: not there — the
 * paragraph was translated again since). */
export function findInTranslation(index: TextIndex, piece: MarkPiece): Range | null {
  return findPiece(
    index,
    {
      quote: piece.quote,
      prefix: typeof piece.tr_prefix === "string" ? piece.tr_prefix : "",
      suffix: typeof piece.tr_suffix === "string" ? piece.tr_suffix : "",
    },
    null,
  );
}

// ---- PDF: the hover / pin translation card ------------------------------

/** A PDF paragraph's own text, as the agent and the note read it: its
 * formulas as `$TeX$`, page breaks as a space. */
export function paragraphText(text: string, math: readonly string[]): string {
  return text
    .replace(/⟦(\w+)⟧/g, (all, token: string) => {
      if (token === "BR") return " ";
      const tex = math[Number(token)];
      return tex === undefined ? all : `$${tex}$`;
    })
    .replace(/\s+/g, " ")
    .trim();
}

/** The translation card's words, as one text. */
export function cardIndex(content: Element): TextIndex {
  return buildTextIndexIn(content, { skip: CARD_SKIP, math: CARD_MATH });
}

/** The first Marker block of a PDF paragraph (its key is `p:<block>`). */
export function paragraphBlock(key: string): string | null {
  return key.startsWith("p:") && key.length > 2 ? key.slice(2) : null;
}

/** A selection in the translation card of a PDF paragraph: one piece, or
 * null when it holds no translated words. `page` is the paragraph's first
 * page (0-based) and `box` its first Marker block's box, when known. */
export function cardSelection(
  content: Element,
  range: Range,
  para: { key: string; text: string; math: readonly string[]; page: number },
  lang: string,
  box: [number, number, number, number] | null,
): TranslationPiece | null {
  const words = wordsIn(cardIndex(content), range);
  if (!words) return null;
  const piece: TranslationPiece = {
    ...words,
    orig: paragraphText(para.text, para.math),
    lang,
    source_ref: para.key,
    page: para.page,
  };
  const block = paragraphBlock(para.key);
  if (block) piece.block_id = block;
  if (box) piece.box = box;
  return piece;
}
