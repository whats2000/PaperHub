import { prefersReducedMotion } from "@/lib/motion";
import { createTranslationQueue } from "@/lib/translationQueue";
import type {
  TranslateRequest,
  TranslateResponse,
  TranslateResult,
} from "@/types/translation";

/**
 * Plan H1 HTML overlay: a translation under each paragraph of a paper rendered
 * in the Citation Canvas iframe.
 *
 * OVERLAY ONLY. `source.html` and the chunks are never touched: the only DOM
 * writes are sibling `div.ph-tr` nodes inserted after a block and one
 * `<style id="ph-tr-style">`. Protected inline content (math, reference
 * anchors, inline code) travels to the server as `⟦n⟧` placeholders and comes
 * back as CLONES of the live nodes, so MathJax output and links are reused.
 */

export const TR_STYLE_ID = "ph-tr-style";
export const TR_CLASS = "ph-tr";

const BLOCK_SELECTOR = "p, li, figcaption, h1, h2, h3, h4, h5, h6";
const EXCLUDED_ANCESTOR = `math, .math.display, pre, .${TR_CLASS}`;
// Pandoc wraps display math in `<p><span class="math display">`: protecting it
// keeps the equation out of the text (an equation-only paragraph then has no
// letters and is skipped; a mixed one reuses the typeset equation).
const PROTECTED_SELECTOR =
  'span.math.inline, .math.display, a.citation, a[href^="#"], span.citation, code';
const SKIPPED_ELEMENT = `script, style, .${TR_CLASS}`;
// Ingest sentinels that may survive rendering as text: the whole token, or a
// bare "<n>END" when the "PHCHUNKANCHOR" part was split off. The bare form
// needs a left boundary so prose like "X2END" survives.
const ANCHOR_REMNANT = /PHCHUNKANCHOR\d+END|(?<![A-Za-z0-9])\d+END/g;
// Mirrors the H0 Collapsible (duration-200 plus a margin).
const COLLAPSE_FALLBACK_MS = 250;
// A block is worth translating only if it has letters outside placeholders.
const HAS_LETTER = /\p{L}/u;

export interface HtmlBlock {
  key: string;
  el: Element;
  text: string;
  protectedNodes: Node[];
}

export interface TranslationLabels {
  pending: string;
  unavailable: string;
  retry: string;
}

const DEFAULT_LABELS: TranslationLabels = {
  pending: "Translating…",
  unavailable: "Translation unavailable",
  retry: "Retry",
};

// App tokens (index.css light theme): --muted-foreground and --border. The dark
// theme's `invert` filter on <html> inverts these with the rest of the page.
const STYLE = `
.${TR_CLASS} {
  display: grid; grid-template-rows: 0fr; opacity: 0; margin: 0;
  content-visibility: visible; contain-intrinsic-size: none;
  transition: grid-template-rows 200ms ease-out, opacity 200ms ease-out,
    margin 200ms ease-out;
}
.${TR_CLASS}[data-open] { grid-template-rows: 1fr; opacity: 1; margin: .25em 0 1em; }
.ph-tr-inner {
  min-height: 0; overflow: hidden;
  border-left: 2px solid oklch(0.922 0 0); padding-left: .75em;
  color: oklch(0.556 0 0);
  font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans TC",
    "Microsoft JhengHei", "PingFang TC", "Noto Sans JP", sans-serif;
  font-size: .92em; line-height: 1.75;
}
.ph-tr-muted { font-style: italic; }
.ph-tr-retry {
  margin-left: .5em; padding: 0; border: 0; background: none; cursor: pointer;
  color: inherit; font: inherit; text-decoration: underline;
  text-underline-offset: 2px;
}
.ph-tr-spin {
  display: inline-block; width: .8em; height: .8em; margin-right: .45em;
  vertical-align: -.1em; box-sizing: border-box; border-radius: 50%;
  border: 2px solid oklch(0.922 0 0); border-top-color: oklch(0.556 0 0);
  animation: ph-tr-spin .8s linear infinite;
}
@keyframes ph-tr-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .${TR_CLASS} { transition: none; }
  .ph-tr-spin { animation: none; }
}
`;

export function ensureTranslationStyle(doc: Document): void {
  if (doc.getElementById(TR_STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = TR_STYLE_ID;
  style.textContent = STYLE;
  (doc.head ?? doc.documentElement).appendChild(style);
}

function isElement(n: Node): n is Element {
  return n.nodeType === 1;
}

/** Protected nodes with nothing visible (e.g. pandoc's empty citation spans)
 *  are dropped rather than sent as a placeholder the model must carry. */
function isEmptyInline(el: Element): boolean {
  return (
    (el.textContent ?? "").trim() === "" &&
    el.querySelector("mjx-container, img, svg") === null
  );
}

function collect(node: Node, parts: string[], prot: Node[]): void {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === 3) {
      parts.push((child as Text).data);
      continue;
    }
    if (!isElement(child) || child.matches(SKIPPED_ELEMENT)) continue;
    if (child.matches(PROTECTED_SELECTOR)) {
      if (isEmptyInline(child)) continue;
      parts.push(`⟦${prot.length}⟧`);
      prot.push(child);
      continue;
    }
    if (child.tagName === "BR") {
      parts.push(" ");
      continue;
    }
    // Chunk anchors (`[id^="phchunk-"]`) are empty spans and citation-highlight
    // wrappers hold plain text; both recurse to nothing / their text.
    collect(child, parts, prot);
  }
}

/** Translatable blocks of a rendered paper, in document order. Keys are
 *  `h:<index>` over candidate blocks (before the empty filter), so a block's
 *  key doesn't depend on whether MathJax has typeset its neighbours yet. */
export function extractBlocks(doc: Document): HtmlBlock[] {
  const root = doc.body ?? doc.documentElement;
  const candidates = Array.from(root.querySelectorAll(BLOCK_SELECTOR)).filter(
    (el) =>
      el.closest(EXCLUDED_ANCESTOR) === null &&
      el.parentElement?.closest(BLOCK_SELECTOR) == null,
  );
  const blocks: HtmlBlock[] = [];
  candidates.forEach((el, index) => {
    const parts: string[] = [];
    const protectedNodes: Node[] = [];
    collect(el, parts, protectedNodes);
    const text = parts
      .join("")
      .replace(ANCHOR_REMNANT, "")
      .replace(/\s+/g, " ")
      .trim();
    if (!HAS_LETTER.test(text.replace(/⟦\d+⟧/g, ""))) return;
    blocks.push({ key: `h:${index}`, el, text, protectedNodes });
  });
  return blocks;
}

/** The live (not leaving) translation of `block`. A fresh box is always
 *  inserted directly after the block, ahead of any box still collapsing away,
 *  so the immediate sibling is the only candidate (O(1), no document query).
 *  No `instanceof`: the iframe's nodes belong to another realm. */
function liveBox(block: HtmlBlock): HTMLElement | null {
  const next = block.el.nextElementSibling;
  return next?.classList.contains(TR_CLASS) &&
    next.getAttribute("data-ph-tr-key") === block.key &&
    !next.hasAttribute("data-ph-tr-leaving")
    ? (next as HTMLElement)
    : null;
}

interface MathJaxLike {
  typesetPromise?: (els: Element[]) => Promise<unknown>;
}

/** A protected node's clone, with ids and citation-highlight wrappers removed
 *  so the overlay never duplicates a chunk anchor or carries a stale highlight. */
function cloneProtected(node: Node): Node {
  const c = node.cloneNode(true);
  if (!isElement(c)) return c;
  c.removeAttribute("id");
  c.querySelectorAll("[id]").forEach((e) => e.removeAttribute("id"));
  c.querySelectorAll("[data-ph-cite]").forEach((w) => {
    w.replaceWith(...Array.from(w.childNodes));
  });
  c.querySelectorAll(".ph-cite-hl").forEach((e) => e.classList.remove("ph-cite-hl"));
  return c;
}

function fill(
  doc: Document,
  inner: HTMLElement,
  block: HtmlBlock,
  text: string,
): void {
  const out: Node[] = [];
  const re = /⟦(\d+)⟧/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(doc.createTextNode(text.slice(last, m.index)));
    const src = block.protectedNodes[Number(m[1])];
    out.push(src ? cloneProtected(src) : doc.createTextNode(m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(doc.createTextNode(text.slice(last)));
  inner.replaceChildren(...out);
  // A clone taken before MathJax typeset still holds raw TeX: typeset it.
  if (/\\\(|\\\[/.test(inner.textContent ?? "")) {
    const mj = (doc.defaultView as (Window & { MathJax?: MathJaxLike }) | null)
      ?.MathJax;
    mj?.typesetPromise?.([inner]).catch(() => undefined);
  }
}

// Fresh boxes waiting to open, per document: opened together in one frame
// after a single layout read, instead of one forced reflow per box.
const opening = new WeakMap<Document, Set<HTMLElement>>();

function scheduleOpen(box: HTMLElement): void {
  if (prefersReducedMotion()) {
    box.setAttribute("data-open", "");
    return;
  }
  const doc = box.ownerDocument;
  const pending = opening.get(doc);
  if (pending) {
    pending.add(box);
    return;
  }
  const batch = new Set([box]);
  opening.set(doc, batch);
  const win = doc.defaultView ?? window;
  const frame =
    typeof win.requestAnimationFrame === "function"
      ? (cb: () => void) => win.requestAnimationFrame(cb)
      : (cb: () => void) => win.setTimeout(cb, 16);
  frame(() => {
    opening.delete(doc);
    // One layout read so every box's closed (0fr) state is computed, then
    // open them all: each transitions from 0fr to 1fr.
    void doc.body?.offsetHeight;
    batch.forEach((b) => {
      if (b.isConnected && !b.hasAttribute("data-ph-tr-leaving")) {
        b.setAttribute("data-open", "");
      }
    });
  });
}

/** Insert or update the translation shown under `block`. `onRetry` adds a
 *  Retry button to the unavailable state. */
export function renderTranslation(
  block: HtmlBlock,
  result: TranslateResult | "pending" | "unavailable",
  labels: TranslationLabels = DEFAULT_LABELS,
  onRetry?: () => void,
): HTMLElement {
  const doc = block.el.ownerDocument;
  ensureTranslationStyle(doc);
  let box = liveBox(block);
  const fresh = box === null;
  if (!box) {
    box = doc.createElement("div");
    box.className = TR_CLASS;
    box.setAttribute("data-ph-tr-key", block.key);
    const inner = doc.createElement("div");
    inner.className = "ph-tr-inner";
    box.appendChild(inner);
    block.el.after(box);
  }
  const inner = box.firstElementChild as HTMLElement;

  const state =
    result === "pending" || result === "unavailable"
      ? result
      : result.status === "ok" && result.text != null
        ? "ok"
        : "unavailable";
  box.setAttribute("data-ph-tr-state", state);
  if (state === "ok") {
    fill(doc, inner, block, (result as TranslateResult).text!);
  } else if (state === "pending") {
    const spin = doc.createElement("span");
    spin.className = "ph-tr-spin";
    spin.setAttribute("aria-hidden", "true");
    const label = doc.createElement("span");
    label.textContent = labels.pending;
    inner.replaceChildren(spin, label);
  } else {
    const label = doc.createElement("span");
    label.className = "ph-tr-muted";
    label.textContent = labels.unavailable;
    inner.replaceChildren(label);
    if (onRetry) {
      const retry = doc.createElement("button");
      retry.type = "button";
      retry.className = "ph-tr-retry";
      retry.textContent = labels.retry;
      retry.addEventListener("click", onRetry, { once: true });
      inner.appendChild(retry);
    }
  }

  if (fresh) scheduleOpen(box);
  return box;
}

/** Collapse every translation, then detach it. */
export function removeTranslations(doc: Document): void {
  const reduced = prefersReducedMotion();
  doc
    .querySelectorAll<HTMLElement>(`.${TR_CLASS}:not([data-ph-tr-leaving])`)
    .forEach((box) => {
      box.setAttribute("data-ph-tr-leaving", "");
      box.removeAttribute("data-open");
      if (reduced) {
        box.remove();
        return;
      }
      const win = doc.defaultView ?? window;
      const done = () => {
        win.clearTimeout(timer);
        box.removeEventListener("transitionend", onEnd);
        box.remove();
      };
      const onEnd = (e: Event) => {
        if (
          e.target === box &&
          (e as TransitionEvent).propertyName === "grid-template-rows"
        ) {
          done();
        }
      };
      const timer = win.setTimeout(done, COLLAPSE_FALLBACK_MS);
      box.addEventListener("transitionend", onEnd);
    });
}

export interface HtmlTranslationOptions {
  paperId: number;
  language: string;
  fullPaper: boolean;
  labels?: TranslationLabels;
  send?: (req: TranslateRequest) => Promise<TranslateResponse>;
  /** Injected for tests; defaults to the document's own window's. */
  IntersectionObserver?: typeof IntersectionObserver;
  /** Whether any visible block's translation predates the current
   *  terminology memory; called on every change. */
  onStaleChange?: (stale: boolean) => void;
}

/** Detach function (collapses the overlay), plus re-translate. */
export type HtmlTranslationHandle = (() => void) & {
  /** Re-send the visible stale blocks with `force: true`. Their current text
   *  stays on the page until the new one arrives (or the old one comes back). */
  retranslate: () => void;
};

/**
 * Drive the overlay for one loaded document: extract blocks, feed the visible
 * ones to the translation queue, show a spinner under a visible block until
 * its result arrives, and render every result. Returns a detach function that
 * stops the queue and collapses all translations away.
 */
export function attachHtmlTranslation(
  doc: Document,
  opts: HtmlTranslationOptions,
): HtmlTranslationHandle {
  const labels = opts.labels ?? DEFAULT_LABELS;
  const blocks = extractBlocks(doc);
  const byKey = new Map(blocks.map((b) => [b.key, b]));
  const keyOf = new Map<Element, string>(blocks.map((b) => [b.el, b.key]));
  const resolved = new Set<string>();
  // Latest result per key and the blocks in view (all, without an observer).
  const results = new Map<string, TranslateResult>();
  let visibleKeys: ReadonlySet<string> = new Set();
  let disposed = false;
  let lastStale = false;
  const staleVisible = () =>
    [...visibleKeys].filter((k) => results.get(k)?.stale_terminology === true);
  const reportStale = () => {
    const now = staleVisible().length > 0;
    if (now === lastStale) return;
    lastStale = now;
    opts.onStaleChange?.(now);
  };

  const queue = createTranslationQueue({
    paperId: opts.paperId,
    language: opts.language,
    fullPaper: opts.fullPaper,
    send: opts.send,
  });
  const retry = (block: HtmlBlock) => () => {
    if (disposed) return;
    resolved.delete(block.key);
    renderTranslation(block, "pending", labels);
    queue.retry([block.key]);
  };
  const unsubscribe = queue.onResult((r) => {
    const block = byKey.get(r.key);
    if (disposed || !block) return;
    resolved.add(r.key);
    results.set(r.key, r);
    renderTranslation(block, r, labels, retry(block));
    reportStale();
  });
  queue.setBlocks(
    blocks.map((b) => b.key),
    Object.fromEntries(blocks.map((b) => [b.key, b.text])),
  );

  const IO =
    opts.IntersectionObserver ?? doc.defaultView?.IntersectionObserver;
  let observer: IntersectionObserver | null = null;
  if (IO) {
    const visible = new Set<string>();
    observer = new IO(
      (entries) => {
        if (disposed) return;
        for (const entry of entries) {
          const key = keyOf.get(entry.target);
          if (!key) continue;
          if (entry.isIntersecting) {
            visible.add(key);
            if (!resolved.has(key) && !liveBox(byKey.get(key)!)) {
              renderTranslation(byKey.get(key)!, "pending", labels);
            }
          } else {
            visible.delete(key);
          }
        }
        visibleKeys = visible;
        reportStale();
        queue.setVisible([...visible]);
      },
      { rootMargin: "200px 0px" },
    );
    blocks.forEach((b) => observer!.observe(b.el));
  } else {
    // No observer (non-browser host): treat every block as in view.
    visibleKeys = new Set(blocks.map((b) => b.key));
    queue.setVisible(blocks.map((b) => b.key));
  }

  const detach = () => {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    queue.dispose();
    observer?.disconnect();
    removeTranslations(doc);
  };
  return Object.assign(detach, {
    retranslate: () => {
      if (disposed) return;
      const keys = staleVisible();
      if (keys.length) queue.retranslate(keys);
    },
  });
}
