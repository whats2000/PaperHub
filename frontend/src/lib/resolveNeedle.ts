/**
 * Resolve the best text needle for locating a chunk in the PDF/HTML text layer.
 *
 * Marker-ingested chunks (Plan F2+) carry a `match_text` field: a clean,
 * markdown-stripped version of the chunk text that matches the rendered text
 * layer reliably. Older (non-Marker) chunks have `match_text` as null/undefined,
 * so we fall back to the raw `text`.
 *
 * The `??` operator falls back only on null/undefined — not on empty strings —
 * so a defined-but-empty `match_text` is returned as-is.
 */
export function resolveNeedle(chunk: {
  text: string;
  match_text?: string | null;
}): string {
  return chunk.match_text ?? chunk.text;
}
