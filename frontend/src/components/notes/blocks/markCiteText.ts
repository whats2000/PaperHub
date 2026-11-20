/** The default alias of an inline citation of a mark. */

/** How many characters of the quote the default alias shows. */
export const CITE_DEFAULT_CHARS = 24;

/** An inline citation's text when the user gave none: the quote's first
 * characters (whitespace collapsed), then "…". The backend's
 * `cite_default_text` (notes/export.py) reads the same. */
export function citeDefaultText(quote: string): string {
  const text = quote.split(/\s+/).filter(Boolean).join(" ");
  if (text.length <= CITE_DEFAULT_CHARS) return text;
  return `${text.slice(0, CITE_DEFAULT_CHARS).trimEnd()}…`;
}

/** The longest quote, in user-perceived characters after trimming, that a
 * new text mark puts in the note as an inline citation (longer: a
 * reference block). The backend's `INLINE_CITE_MAX_GRAPHEMES`
 * (notes/inline_cite.py) holds the same value. */
export const INLINE_CITE_MAX_GRAPHEMES = 24;

const segmenter =
  typeof Intl !== "undefined" && "Segmenter" in Intl ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null;

/** User-perceived characters (grapheme clusters). */
export function graphemeCount(text: string): number {
  if (!segmenter) return Array.from(text).length;
  return Array.from(segmenter.segment(text)).length;
}

/** Whether a new text mark with this quote lands as an inline citation. */
export function isShortQuote(quote: string): boolean {
  const text = quote.trim();
  return text !== "" && graphemeCount(text) <= INLINE_CITE_MAX_GRAPHEMES;
}
