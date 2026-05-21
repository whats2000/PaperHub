import { useEffect, useRef } from "react";

import { applyIframeTheme } from "@/lib/applyIframeTheme";
import { findAndHighlight } from "@/lib/findAndHighlight";

interface Props {
  /** The paper's rendered HTML, embedded via `srcdoc` (same-origin). */
  html: string;
  isDark: boolean;
  /** When set, the passage to scroll-to + highlight once the doc is ready. */
  highlightText: string | null;
  /** Called when `highlightText` couldn't be located in the document. */
  onHighlightMiss?: () => void;
}

/**
 * Renders a paper's HTML inside an iframe via `srcdoc`. Because the content is
 * embedded (not loaded from a cross-origin URL), the iframe document is
 * SAME-ORIGIN — so we can read its DOM to inject the dark-mode stylesheet and
 * to text-search + highlight the cited passage. `allow-scripts` lets MathJax
 * run; figures are data-URI inlined by the renderer.
 */
export function HtmlView({ html, isDark, highlightText, onHighlightMiss }: Props) {
  const ref = useRef<HTMLIFrameElement>(null);

  const apply = (): void => {
    const doc = ref.current?.contentDocument;
    if (!doc?.body) return;
    applyIframeTheme(doc, isDark);
    if (highlightText) {
      const found = findAndHighlight(doc, highlightText);
      if (!found) onHighlightMiss?.();
    }
  };

  // Re-apply when the theme toggles or the target passage changes (the iframe
  // is already loaded in those cases, so onLoad won't fire again).
  useEffect(() => {
    apply();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, highlightText]);

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
