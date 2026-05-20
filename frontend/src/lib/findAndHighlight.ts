export const HIGHLIGHT_CLASS = "ph-cite-hl";

/**
 * Maximum characters of the (normalized) needle to attempt first.
 * Rendering (math, ligatures, figure captions) often mangles the tail of
 * dense passages, so we cap the match target and fall back to shorter
 * prefixes when the capped version is not found. See `buildTargets`.
 */
const PREFIX_LEN = 150;

/**
 * Minimum characters for any accepted prefix match.
 * Every candidate target must be at least this long (or be the whole needle
 * when the needle itself is shorter). A 30-char threshold makes an accidental
 * match against a generic sentence opener unlikely — though not impossible —
 * which is a demo-acceptable trade-off. Short needles match in full only.
 */
const MIN_MATCH = 30;

const HIGHLIGHT_MS = 2500;

const normalize = (s: string): string => s.replace(/\s+/g, " ").trim();

interface NodeSpan {
  node: Text;
  start: number; // index into the concatenated normalized string
  end: number;
}

/**
 * Build a prioritized list of search targets derived from `needle`.
 *
 * The challenge: a stored chunk may end with LaTeX math or a figure caption
 * that the HTML renderer drops or transforms. The head of the passage is
 * reliable; the tail is not. So we try progressively shorter leading
 * substrings until we find one in the DOM.
 *
 * Every candidate must be at least MIN_MATCH characters long, making
 * accidental matches against generic short openers unlikely (though not
 * impossible) — a demo-acceptable trade-off. The one exception: when the
 * whole normalized needle is shorter than MIN_MATCH, the only candidate is
 * the entire needle (a short chunk must match in full; no sub-floor).
 *
 * Priority order (longest-first):
 *   1. needle normalized and capped at PREFIX_LEN
 *   2. needle up to the last sentence boundary (". ") before PREFIX_LEN,
 *      only if that boundary is at or beyond MIN_MATCH
 *   3. needle up to the last word boundary (" ") near the midpoint,
 *      only if that boundary is at or beyond MIN_MATCH
 *   4. needle up to MIN_MATCH chars (floor), only if needle >= MIN_MATCH
 */
function buildTargets(needle: string): string[] {
  const norm = normalize(needle);
  if (!norm) return [];

  // Short needle: must match in full — no sub-floor candidates.
  if (norm.length < MIN_MATCH) {
    return [norm];
  }

  const cap = Math.min(norm.length, PREFIX_LEN);
  const targets: string[] = [norm.slice(0, cap)];

  // Sentence boundary before PREFIX_LEN (keep the ".")
  const dot = norm.lastIndexOf(". ", cap);
  if (dot + 1 >= MIN_MATCH) {
    const sentenceTarget = norm.slice(0, dot + 1);
    if (!targets.includes(sentenceTarget)) targets.push(sentenceTarget);
  }

  // Word boundary near the midpoint
  const mid = Math.max(MIN_MATCH, Math.floor(cap / 2));
  const sp = norm.lastIndexOf(" ", mid);
  if (sp >= MIN_MATCH) {
    const wordTarget = norm.slice(0, sp);
    if (!targets.includes(wordTarget)) targets.push(wordTarget);
  }

  // Floor: MIN_MATCH chars
  const floorTarget = norm.slice(0, MIN_MATCH);
  if (!targets.includes(floorTarget)) targets.push(floorTarget);

  // De-dup while preserving order (longest-first)
  return [...new Set(targets)];
}

/**
 * Locate `needle` (by normalized prefix) inside `doc`, scroll it into view,
 * and apply a transient highlight. Returns whether a match was found.
 *
 * Decoupled from the iframe + from layout: `scrollIntoView` is feature-detected
 * so this runs under jsdom. The highlight is applied as a class on the start
 * node's parent element (robust across node boundaries without fragile Range
 * surgery).
 */
export function findAndHighlight(doc: Document, needle: string): boolean {
  const targets = buildTargets(needle);
  if (targets.length === 0) return false;

  clearHighlight(doc);

  // Build a concatenated normalized string with a node->offset index.
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const spans: NodeSpan[] = [];
  let combined = "";
  let cursor: Node | null = walker.nextNode();
  while (cursor) {
    const textNode = cursor as Text;
    const norm = normalize(textNode.data);
    if (norm) {
      // Join with a single space so adjacent block elements don't fuse words.
      const prefix = combined.length > 0 ? " " : "";
      const start = combined.length + prefix.length;
      combined += prefix + norm;
      spans.push({ node: textNode, start, end: combined.length });
    }
    cursor = walker.nextNode();
  }

  // Try each target (longest first) until one is found in the combined text.
  let hitIndex = -1;
  for (const target of targets) {
    hitIndex = combined.indexOf(target);
    if (hitIndex >= 0) break;
  }
  if (hitIndex < 0) return false;

  const span = spans.find((s) => hitIndex >= s.start && hitIndex < s.end);
  if (!span) return false;

  const el = span.node.parentElement;
  if (el) {
    el.classList.add(HIGHLIGHT_CLASS);
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const win = doc.defaultView;
    const setTimeoutFn = win?.setTimeout ?? globalThis.setTimeout;
    setTimeoutFn(() => el.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
  }
  return true;
}

function clearHighlight(doc: Document): void {
  doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`).forEach((el) => {
    el.classList.remove(HIGHLIGHT_CLASS);
  });
}
