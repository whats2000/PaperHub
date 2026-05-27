export const HIGHLIGHT_CLASS = "ph-cite-hl";

const HIGHLIGHT_STYLE_ID = "ph-cite-hl-style";

/**
 * Inject a one-time <style> rule into `doc` so `.ph-cite-hl` is visible
 * inside style-isolated contexts (e.g. an iframe whose parent Tailwind/CSS
 * cannot reach inside). Safe to call repeatedly — creates the element only
 * once per document.
 */
function ensureHighlightStyle(doc: Document): void {
  if (doc.getElementById(HIGHLIGHT_STYLE_ID) !== null) return;
  const style = doc.createElement("style");
  style.id = HIGHLIGHT_STYLE_ID;
  style.textContent =
    `.${HIGHLIGHT_CLASS} { background-color: #fde68a; color: inherit; ` +
    `border-radius: 2px; padding: 0 1px; ` +
    `transition: background-color 0.3s ease; }`;
  (doc.head ?? doc.documentElement).appendChild(style);
}

// Marker we tag chunk-range wrappers with, so cleanup can unwrap exactly the
// spans we injected (vs class-only highlights from the text/section fallbacks).
const WRAP_ATTR = "data-ph-cite";

// One in-flight glide per document, so a new citation cancels the previous one
// instead of two rAF loops fighting over scrollTop.
const activeGlide = new WeakMap<Document, number>();

/**
 * Smoothly scroll `el` to the vertical center, tracking it as the layout settles.
 *
 * The Citation-Canvas iframe lays out lazily (`content-visibility: auto`), so an
 * off-screen block reports a placeholder height until it actually renders — the
 * target's true position is unknown until you scroll near it. Native smooth
 * `scrollIntoView` animates toward the GUESSED offset and then snaps when the
 * in-between blocks render (the "teleport").
 *
 * So we drive the glide ourselves: each frame we re-measure the target and ease
 * scrollTop a fraction of the REMAINING distance toward centering it. As the
 * lazy blocks render and shift the target, the glide simply tracks the moving
 * target and converges — one continuous smooth scroll that lands exactly, no
 * snap. The fractional step is a natural ease-out; we stop within 1px or at a
 * safety cap. `behavior` is accepted for call-site compatibility. Falls back to
 * a single `scrollIntoView` under jsdom / detached documents (no window).
 */
export function scrollIntoViewStable(
  el: Element,
  _behavior: ScrollBehavior = "auto",
): void {
  if (typeof el.scrollIntoView !== "function") return;
  const doc = el.ownerDocument;
  const win = doc?.defaultView;
  const scroller = doc?.scrollingElement;
  if (!win?.requestAnimationFrame || !scroller) {
    el.scrollIntoView({ block: "center" });
    return;
  }
  const prev = activeGlide.get(doc);
  if (prev != null) win.cancelAnimationFrame(prev);

  let frames = 0;
  const tick = (): void => {
    const rect = el.getBoundingClientRect();
    // Distance from the target's center to the viewport's center (>0 ⇒ below).
    const delta = rect.top + rect.height / 2 - win.innerHeight / 2;
    frames += 1;
    if (Math.abs(delta) <= 1 || frames > 90) {
      activeGlide.delete(doc);
      return; // centered, or safety cap (~1.5s) so a never-settling page can't loop
    }
    scroller.scrollTop += delta * 0.2; // ease-out: close 20% of the gap per frame
    activeGlide.set(doc, win.requestAnimationFrame(tick));
  };
  activeGlide.set(doc, win.requestAnimationFrame(tick));
}

function scheduleClear(doc: Document, fn: () => void): void {
  const win = doc.defaultView;
  const setTimeoutFn = win?.setTimeout ?? globalThis.setTimeout;
  setTimeoutFn(fn, HIGHLIGHT_MS);
}

/**
 * Collect the text nodes that fall strictly between two chunk sentinels —
 * `start` (this chunk's `<span id="phchunk-N">`) and `next` (the next sentinel
 * in document order, regardless of ordinal, since math-skipped chunks leave
 * gaps). When there's no next sentinel (last chunk), bound the scan to the
 * enclosing section so we don't run to the end of the document.
 */
function collectChunkTextNodes(
  doc: Document,
  start: Element,
  next: Element | null,
): Text[] {
  const root = next ? doc.body : (start.closest("section") ?? doc.body);
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let node = walker.nextNode();
  while (node) {
    const t = node as Text;
    const afterStart =
      (start.compareDocumentPosition(t) &
        Node.DOCUMENT_POSITION_FOLLOWING) !==
      0;
    const beforeNext =
      next === null ||
      (next.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_PRECEDING) !==
        0;
    if (afterStart && beforeNext && t.data.trim() !== "") nodes.push(t);
    node = walker.nextNode();
  }
  return nodes;
}

/** Wrap each text node in a highlight span (markers are element boundaries, so
 *  the chunk edges fall between nodes — no offset splitting needed). */
function wrapTextNodes(doc: Document, nodes: Text[]): HTMLElement[] {
  const wrappers: HTMLElement[] = [];
  for (const t of nodes) {
    const parent = t.parentNode;
    if (!parent) continue;
    const span = doc.createElement("span");
    span.className = HIGHLIGHT_CLASS;
    span.setAttribute(WRAP_ATTR, "1");
    parent.replaceChild(span, t);
    span.appendChild(t);
    wrappers.push(span);
  }
  return wrappers;
}

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

// Keep the highlight up long enough to survive the smooth-scroll AND give the
// reader time to find + read the passage (2.5s often expired mid-scroll). The
// PDF overlay persists until the next citation; this is the HTML counterpart.
const HIGHLIGHT_MS = 10000;

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
export function buildTargets(needle: string): string[] {
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
export function findAndHighlight(
  doc: Document,
  needle: string,
  behavior: ScrollBehavior = "smooth",
): boolean {
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
    ensureHighlightStyle(doc);
    el.classList.add(HIGHLIGHT_CLASS);
    scrollIntoViewStable(el, behavior);
    const win = doc.defaultView;
    const setTimeoutFn = win?.setTimeout ?? globalThis.setTimeout;
    setTimeoutFn(() => el.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
  }
  return true;
}

export function clearHighlight(doc: Document): void {
  // Unwrap chunk-range wrappers (restore the original text nodes).
  doc.querySelectorAll(`[${WRAP_ATTR}]`).forEach((span) => {
    const parent = span.parentNode;
    if (!parent) return;
    while (span.firstChild) parent.insertBefore(span.firstChild, span);
    parent.removeChild(span);
    if (typeof parent.normalize === "function") parent.normalize();
  });
  // Remove class-only highlights (text-search / section fallbacks).
  doc.querySelectorAll(`.${HIGHLIGHT_CLASS}`).forEach((el) => {
    el.classList.remove(HIGHLIGHT_CLASS);
  });
}

/**
 * Deterministically locate a chunk by its ingest-time anchor (`<span id>`
 * injected at the chunk's start) and highlight the block it begins, scrolling
 * it into view. Returns whether the anchor element exists. This is the
 * preferred resolver (no text matching); callers fall back to
 * `findAndHighlight` when there's no `dom_id` or the anchor is absent (e.g. a
 * chunk whose sentinel landed in math and was skipped at ingest).
 */
export function highlightChunkRange(
  doc: Document,
  domId: string,
  behavior: ScrollBehavior = "smooth",
): boolean {
  const start = doc.getElementById(domId);
  if (!start) return false;
  clearHighlight(doc);
  ensureHighlightStyle(doc);

  // The chunk runs from its own sentinel to the NEXT sentinel IN DOCUMENT ORDER
  // — not `phchunk-N+1`, because chunks whose sentinel landed in math keep no
  // anchor and leave ordinal gaps. Querying the live anchors and taking the one
  // after this gives the true chunk boundary.
  //
  // Several chunks of ONE table all anchor just before it (a table is atomic —
  // its rows can't hold a sentinel), so consecutive anchors can sit adjacent
  // with no text between them. Taking the immediate next sentinel would then
  // give an EMPTY range (scroll, but nothing highlighted). So advance to the
  // next sentinel that actually has text in between — a clustered table-chunk
  // citation then highlights the table itself.
  const markers = Array.from(doc.querySelectorAll('[id^="phchunk-"]'));
  const idx = markers.findIndex((m) => m.id === domId);
  let nodes: Text[] = [];
  for (let j = idx + 1; idx >= 0 && j <= markers.length; j++) {
    const next = markers[j] ?? null;
    nodes = collectChunkTextNodes(doc, start, next);
    if (nodes.length > 0 || next === null) break;
  }

  // Wrap the text nodes between the two sentinels — the exact chunk, across
  // blocks, never the whole paragraph.
  const wrappers = wrapTextNodes(doc, nodes);

  const anchor = wrappers[0] ?? start;
  scrollIntoViewStable(anchor, behavior);
  scheduleClear(doc, () => clearHighlight(doc));
  return true;
}

/**
 * Last-resort resolver: scroll to the heading that matches a chunk's section
 * title and flash it. Used when neither the deterministic anchor nor the
 * text-search located the exact passage (e.g. a math/table-heavy chunk) — so a
 * citation always lands at least at the right section instead of dead-ending.
 * Returns whether a matching heading was found.
 */
export function scrollToSection(
  doc: Document,
  sectionTitle: string,
  behavior: ScrollBehavior = "smooth",
): boolean {
  const target = normalize(sectionTitle);
  if (!target) return false;
  const headings = doc.querySelectorAll("h1,h2,h3,h4,h5,h6");
  for (const h of Array.from(headings)) {
    const ht = normalize(h.textContent ?? "");
    // Pandoc headings may carry a section number prefix, so match loosely.
    if (ht && (ht === target || ht.includes(target) || target.includes(ht))) {
      clearHighlight(doc);
      ensureHighlightStyle(doc);
      const el = h as HTMLElement;
      el.classList.add(HIGHLIGHT_CLASS);
      scrollIntoViewStable(el, behavior);
      const win = doc.defaultView;
      const setTimeoutFn = win?.setTimeout ?? globalThis.setTimeout;
      setTimeoutFn(() => el.classList.remove(HIGHLIGHT_CLASS), HIGHLIGHT_MS);
      return true;
    }
  }
  return false;
}
