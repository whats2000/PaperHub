import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";

import { CitationCanvas, type ReaderPaper } from "@/components/canvas/CitationCanvas";
import { usePresence } from "@/hooks/usePresence";
import { cn } from "@/lib/utils";

const EXIT_MS = 200;
const DEFAULT_WIDTH = 820;
const MIN_WIDTH = 360;
const WIDTH_KEY = "paperhub.ideas.readerWidth";

function storedWidth(): number {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(n) && n >= MIN_WIDTH ? n : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

/** Pointer-downs that began inside the reader, portalled popups included (React bubbles through portals). */
const insideReader = new WeakSet<Event>();

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

  // Drag the left edge to resize, like the chat page's canvas; the width is remembered.
  const [width, setWidth] = useState(storedWidth);
  const [resizing, setResizing] = useState(false);
  const onEdgeDown = useCallback((e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    setResizing(true);
    let last = startW;
    const onMove = (ev: PointerEvent) => {
      const max = Math.round(window.innerWidth * 0.9);
      last = Math.min(Math.max(startW + (startX - ev.clientX), MIN_WIDTH), max);
      setWidth(last);
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      try {
        localStorage.setItem(WIDTH_KEY, String(last));
      } catch {
        /* the width just isn't remembered */
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [width]);

  // A press outside the reader closes it, and only it: the click that follows
  // is swallowed so it doesn't also select or deselect on the graph beneath.
  const swallowClick = useRef(false);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      swallowClick.current = false;
      if (insideReader.has(e)) return;
      swallowClick.current = true;
      onClose();
    };
    const onClick = (e: MouseEvent) => {
      if (!swallowClick.current) return;
      swallowClick.current = false;
      e.stopPropagation();
      e.preventDefault();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("click", onClick, true);
    };
  }, [open, onClose]);

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
      onPointerDownCapture={(e) => insideReader.add(e.nativeEvent)}
      style={{ "--reader-w": `${width}px` } as CSSProperties}
      className={cn(
        "absolute top-0 right-0 bottom-0 z-30 w-[min(var(--reader-w),calc(100%-3rem))]",
        "transition-transform motion-reduce:transition-none",
        slidIn
          ? "translate-x-0 shadow-xl duration-[240ms] ease-[cubic-bezier(.2,.8,.2,1)]"
          : "translate-x-full duration-200 ease-[cubic-bezier(.4,0,.6,1)]",
      )}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={t("ideas:reader.resize", "Resize reader")}
        onPointerDown={onEdgeDown}
        className="absolute top-0 left-0 z-10 h-full w-1.5 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
      />
      {/* No pointer events while dragging, so the paper's iframe can't swallow the moves. */}
      <div className={cn("h-full", resizing && "pointer-events-none")}>
        <CitationCanvas key={shown.paper_content_id} standalone={{ papers: [shown], onClose }} />
      </div>
    </div>
  );
}
