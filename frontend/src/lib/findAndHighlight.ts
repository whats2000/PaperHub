export const HIGHLIGHT_CLASS = "ph-cite-hl";

/**
 * Maximum characters of the (normalized) needle to attempt first.
 * Rendering (math, ligatures, figure captions) often mangles the tail of
 * dense passages, so we cap the match target and fall back to shorter
 * prefixes when the capped version is not found. See `buildTargets`.
 */
const PREFIX_LEN = 150;

/** Minimum characters required to call a prefix match valid. */
const MIN_PREFIX = 20;

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
 * substrings until we find one in the DOM, stopping at MIN_PREFIX chars.
 *
 * Priority order (longest-first so we don't accidentally match a 20-char
 * prefix that appears in an unrelated sentence):
 *   1. needle normalized and capped at PREFIX_LEN
 *   2. needle up to the last sentence boundary (". ") before PREFIX_LEN
 *   3. needle up to the last word boundary (" ") before PREFIX_LEN / 2
 *   4. needle up to MIN_PREFIX chars (hard floor)
 */
function buildTargets(needle: string): string[] {
  const full = normalize(needle);
  const capped = full.slice(0, PREFIX_LEN);

  const targets: string[] = [capped];

  // Sentence boundary before PREFIX_LEN
  const sentenceEnd = capped.lastIndexOf(". ");
  if (sentenceEnd >= MIN_PREFIX) {
    const sentenceTarget = capped.slice(0, sentenceEnd + 1); // keep the "."
    if (sentenceTarget !== capped) targets.push(sentenceTarget);
  }

  // Word boundary before PREFIX_LEN / 2
  const halfLen = Math.floor(PREFIX_LEN / 2);
  const halfCapped = full.slice(0, halfLen);
  const wordEnd = halfCapped.lastIndexOf(" ");
  if (wordEnd >= MIN_PREFIX) {
    const wordTarget = halfCapped.slice(0, wordEnd);
    if (!targets.includes(wordTarget)) targets.push(wordTarget);
  }

  // Hard floor
  const floorTarget = full.slice(0, MIN_PREFIX);
  if (floorTarget.length >= MIN_PREFIX && !targets.includes(floorTarget)) {
    targets.push(floorTarget);
  }

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
  if (!targets[0]) return false;

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
