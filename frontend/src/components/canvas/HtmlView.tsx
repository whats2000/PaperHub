import { useEffect, useRef } from "react";

import { applyIframeTheme } from "@/lib/applyIframeTheme";
import {
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
}: Props) {
  const ref = useRef<HTMLIFrameElement>(null);

  const apply = (): void => {
    const doc = ref.current?.contentDocument;
    if (!doc?.body) return;
    applyIframeTheme(doc, isDark);
    if (!highlightDomId && !highlightText && !sectionTitle) return;
    // Resolution order, best → last-resort: deterministic anchor → text-search
    // → section heading (so a citation always lands somewhere useful).
    const found =
      (highlightDomId !== null && highlightChunkRange(doc, highlightDomId)) ||
      (highlightText !== null && findAndHighlight(doc, highlightText)) ||
      (sectionTitle !== null && scrollToSection(doc, sectionTitle));
    if (!found) onHighlightMiss?.();
  };

  // Re-apply when the theme toggles or the target changes (the iframe is
  // already loaded in those cases, so onLoad won't fire again).
  useEffect(() => {
    apply();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, highlightDomId, highlightText, sectionTitle, nonce]);

  return (
    <iframe
      ref={ref}
      title="Citation Canvas"
      srcDoc={html}
      onLoad={apply}
      sandbox="allow-scripts allow-same-origin"
      className="h-full w-full flex-1 bg-white dark:bg-[#0f1115]"
    />
  );
}
