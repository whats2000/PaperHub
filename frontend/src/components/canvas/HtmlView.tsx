import { useEffect, useRef } from "react";

import { applyIframeTheme } from "@/lib/applyIframeTheme";
import {
  clearHighlight,
  findAndHighlight,
  highlightChunkRange,
  scrollToSection,
} from "@/lib/findAndHighlight";

interface Props {
  /** The paper's rendered HTML, embedded via `srcdoc` (same-origin). */
  html: string;
  isDark: boolean;
  /** Deterministic anchor (`phchunk-N` span) for the cited chunk, when ingest
   *  placed one. Preferred over text-search. */
  highlightDomId: string | null;
  /** Fallback passage text to locate when there's no anchor (or it's absent). */
  highlightText: string | null;
  /** Last-resort: the chunk's section title, to scroll to its heading when the
   *  anchor + text-search both miss (so a citation never dead-ends). */
  sectionTitle: string | null;
  /** Called when anchor, text, and section all failed to locate anything. */
  onHighlightMiss?: () => void;
  /** Bumped per resolved citation so re-clicking the SAME chunk re-fires the
   *  highlight + scroll even though the target values are unchanged. */
  nonce: number;
  /** How the scroll-to-passage moves. "smooth" (animate) when the canvas was
   *  already open — the glide shows the passage's relative position; "instant"
   *  when this click also opened the canvas (layout isn't settled, so animating
   *  would track a shifting target). */
  scrollBehavior?: ScrollBehavior;
}

/**
 * Renders a paper's HTML inside an iframe via `srcdoc`. Because the content is
 * embedded (not loaded from a cross-origin URL), the iframe document is
 * SAME-ORIGIN — so we can read its DOM to inject the dark-mode stylesheet and
 * to text-search + highlight the cited passage. `allow-scripts` lets MathJax
 * run; figures are data-URI inlined by the renderer.
 */
export function HtmlView({
  html,
  isDark,
  highlightDomId,
  highlightText,
  sectionTitle,
  onHighlightMiss,
  nonce,
  scrollBehavior = "smooth",
}: Props) {
  const ref = useRef<HTMLIFrameElement>(null);
  // Whether the iframe's srcdoc has finished loading. The highlight effect can
  // fire between mount and load (e.g. a freshly-fetched paper opened by a
  // citation) when the document is still empty `about:blank` — a "not found"
  // there is spurious, since onLoad re-runs apply() once the content is parsed.
  const loadedRef = useRef(false);
  // Cancels a still-queued deferred highlight (see apply): set when one is
  // scheduled, called to drop it before scheduling another or on unmount.
  const cancelPending = useRef<(() => void) | null>(null);

  const apply = (): void => {
    const doc = ref.current?.contentDocument;
    if (!doc?.body) return;
    applyIframeTheme(doc, isDark);
    if (!highlightDomId && !highlightText && !sectionTitle) return;

    // Unwrap the PREVIOUS highlight NOW, but defer wrapping + scrolling to the
    // NEW target to a later macrotask. Doing both in one synchronous click
    // flush mutates a (figure) subtree and scrolls it out of view at the same
    // time; under the injected `content-visibility: auto`, that locks/unlocks a
    // heavyweight image block mid-mutation and trips Chromium's display-lock
    // CHECK (STATUS_BREAKPOINT) — reliably when both the old and new targets
    // are figures. Splitting them across a task boundary (as DeferredRemount
    // does for the PDF swap) lets the figure's layout settle before the next
    // scroll moves it.
    clearHighlight(doc);
    cancelPending.current?.();

    const win = doc.defaultView ?? window;
    const id = win.setTimeout(() => {
      cancelPending.current = null;
      const live = ref.current?.contentDocument;
      if (!live?.body) return;
      // Resolution order, best → last-resort: deterministic anchor → text-
      // search → section heading (so a citation always lands somewhere useful).
      const found =
        (highlightDomId !== null &&
          highlightChunkRange(live, highlightDomId, scrollBehavior)) ||
        (highlightText !== null &&
          findAndHighlight(live, highlightText, scrollBehavior)) ||
        (sectionTitle !== null &&
          scrollToSection(live, sectionTitle, scrollBehavior));
      // Only report a real miss once the document is loaded (see loadedRef).
      if (!found && loadedRef.current) onHighlightMiss?.();
    }, 0);
    cancelPending.current = () => win.clearTimeout(id);
  };

  const handleLoad = (): void => {
    loadedRef.current = true;
    apply();
  };

  // Re-apply when the theme toggles or the target changes (the iframe is
  // already loaded in those cases, so onLoad won't fire again).
  useEffect(() => {
    apply();
    return () => cancelPending.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, highlightDomId, highlightText, sectionTitle, nonce]);

  return (
    <iframe
      ref={ref}
      title="Citation Canvas"
      srcDoc={html}
      onLoad={handleLoad}
      sandbox="allow-scripts allow-same-origin"
      className="h-full w-full flex-1 bg-white dark:bg-[#0f1115]"
    />
  );
}
