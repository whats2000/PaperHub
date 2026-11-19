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
