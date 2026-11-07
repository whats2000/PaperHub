import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { CitationCanvas, type ReaderPaper } from "@/components/canvas/CitationCanvas";
import { usePresence } from "@/hooks/usePresence";
import { cn } from "@/lib/utils";

const EXIT_MS = 200;

/** A popup open above the reader (a menu, the language list): Escape is its own. */
const OPEN_POPUP =
  '[data-open][role="dialog"], [data-open][role="alertdialog"], [data-open][role="menu"], [data-open][role="listbox"]';

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || target.closest("input, textarea, select") !== null;
}

/**
 * The Ideas page's paper reader: the Citation Canvas reader in a wide drawer
 * that slides in from the right over the graph (and the detail drawer), and
 * back out on close, ✕ or Escape. It reads one document without a chat.
 */
export function PaperReaderDrawer({
  paper,
  onClose,
}: {
  paper: ReaderPaper | null;
  onClose: () => void;
}) {
  const { t } = useTranslation("ideas");
  const open = paper !== null;
  const { mounted } = usePresence(open, EXIT_MS);
  // Keep showing the last paper while sliding out.
  const [shown, setShown] = useState<ReaderPaper | null>(paper);
  if (paper !== null && paper !== shown) setShown(paper);
  // Mount off-screen, then slide in on the next frame.
  const [entered, setEntered] = useState(false);
  if (!mounted && entered) setEntered(false);
  useEffect(() => {
    if (!mounted || entered) return;
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [mounted, entered]);

  // Escape closes the reader first: captured before the detail drawer's
  // listener, which skips a key another layer already took.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
      if (isEditable(e.target) || document.querySelector(OPEN_POPUP)) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!mounted || shown === null) return null;
  const slidIn = open && entered;
  return (
    <div
      data-testid="paper-reader-drawer"
      role="region"
      aria-label={t("ideas:reader.aria", "Paper reader")}
      inert={!open}
      // Clicks inside the reader never reach the graph behind it.
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "absolute top-0 right-0 bottom-0 z-30 w-[min(820px,calc(100%-3rem))]",
        "transition-transform motion-reduce:transition-none",
        slidIn
          ? "translate-x-0 shadow-xl duration-[240ms] ease-[cubic-bezier(.2,.8,.2,1)]"
          : "translate-x-full duration-200 ease-[cubic-bezier(.4,0,.6,1)]",
      )}
    >
      <CitationCanvas key={shown.paper_content_id} standalone={{ papers: [shown], onClose }} />
    </div>
  );
}
