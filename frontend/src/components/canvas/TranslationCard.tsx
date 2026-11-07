import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Loader2, Pin, PinOff, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import "katex/dist/katex.min.css";

import { usePresence } from "@/hooks/usePresence";
import { contentScrollTop, type CardPlacement } from "@/lib/cardPlacement";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

export type TranslationCardStatus = "loading" | "ok" | "unavailable";

/** Where the content should be scrolled for the reader's progress. */
export interface CardScroll {
  /** 0–1 progress through the active fragment (or the whole paragraph). */
  fraction: number;
  activeFragment: number;
  /** The `⟦BR⟧` dividers line up with the fragments: scroll within spans. */
  perFragment: boolean;
}

/** Progress change that resumes the sync after the user scrolled the card. */
const RESUME_SYNC_DELTA = 0.02;

interface Props {
  open: boolean;
  /** Identifies the paragraph shown, so switching paragraphs cross-fades. */
  contentKey: string | null;
  language: string;
  status: TranslationCardStatus;
  /** Rendered translation (from `mathToKatexHtml`) when status is `ok`. */
  html: string | null;
  pinned: boolean;
  /** Last placement; kept while closed so the exit plays where the card was. */
  placement: CardPlacement | null;
  /** Scroll sync target; `null` leaves the content where it is. */
  scroll?: CardScroll | null;
  /** Glide to the new position (a change of paragraph / fragment / side). */
  glide?: boolean;
  /** The card element, measured by the layer for placement. */
  cardRef?: RefObject<HTMLDivElement | null>;
  /** The card resized (content arrived, viewport changed): re-place it. */
  onResize?: () => void;
  onTogglePin: () => void;
  onClose: () => void;
  onRetry: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

/**
 * The PDF paragraph translation card: a Popover-surface panel portalled to the
 * body, beside the reading area (or under the paragraph when the gutter is
 * narrow) with an arrow pointing at the paragraph. Enter/leave fade + zoom
 * from the arrow side (`usePresence`). A long translation scrolls inside the
 * card in step with the reader's progress through the paragraph.
 */
export function TranslationCard({
  open,
  contentKey,
  language,
  status,
  html,
  pinned,
  placement,
  scroll = null,
  glide = false,
  cardRef,
  onResize,
  onTogglePin,
  onClose,
  onRetry,
  onMouseEnter,
  onMouseLeave,
}: Props) {
  const { t } = useTranslation("canvas");
  const { mounted, state } = usePresence(open && placement !== null);
  const contentRef = useRef<HTMLDivElement>(null);
  const side = placement?.side;
  // Under or over the paragraph (inside the reader): the arrow is horizontal.
  const inReader = side === "below" || side === "above";

  // Follow the reader: scroll the content to the active fragment's span, at
  // the share of that fragment already scrolled past the viewport top. Only
  // when the target really changed (not on every re-placement), and paused
  // after the user scrolls the card until reading progress moves on.
  const lastSync = useRef<string | null>(null);
  const paused = useRef<{ key: string | null; fragment: number; fraction: number } | null>(
    null,
  );
  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content || !scroll) return;
    const sig = [
      contentKey,
      scroll.activeFragment,
      Math.round(scroll.fraction * 100),
      html,
    ].join("|");
    if (sig === lastSync.current) return;
    const p = paused.current;
    if (p) {
      const same = p.key === contentKey && p.fragment === scroll.activeFragment;
      if (same && Math.abs(scroll.fraction - p.fraction) <= RESUME_SYNC_DELTA) return;
      paused.current = null;
    }
    const range = content.scrollHeight - content.clientHeight;
    if (range <= 0) return;
    lastSync.current = sig;
    // `offsetTop` within the (positioned) content: unaffected by the card's
    // zoom transform or the current scroll position.
    const breaks = scroll.perFragment
      ? Array.from(content.querySelectorAll<HTMLElement>("[data-break]")).map((b) => b.offsetTop)
      : [];
    content.scrollTop = contentScrollTop({
      fraction: scroll.fraction,
      activeFragment: scroll.perFragment ? scroll.activeFragment : 0,
      breaks,
      range,
    });
  }, [scroll, html, contentKey]);
  const pauseSync = () => {
    if (scroll) {
      paused.current = {
        key: contentKey,
        fragment: scroll.activeFragment,
        fraction: scroll.fraction,
      };
    }
  };

  const onResizeRef = useRef(onResize);
  useLayoutEffect(() => {
    onResizeRef.current = onResize;
  }, [onResize]);
  useEffect(() => {
    const el = cardRef?.current;
    if (!mounted || !el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => onResizeRef.current?.());
    ro.observe(el);
    return () => ro.disconnect();
  }, [mounted, cardRef]);

  if (!mounted || !placement) return null;

  return createPortal(
    <div
      ref={cardRef}
      role="dialog"
      aria-label={t("translationCard.label", "Translation")}
      data-state={state}
      data-side={placement.side}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      style={{
        left: placement.left,
        top: placement.top,
        width: placement.width,
        maxHeight: placement.maxHeight,
        transition:
          glide && !prefersReducedMotion() ? "top 200ms ease-out, left 200ms ease-out" : undefined,
      }}
      className={cn(
        // Its own grey surface + stronger border: the shared popover surface is
        // near-identical to the chat column behind it in dark mode.
        "fixed z-50 flex max-w-[460px] flex-col rounded-md border border-foreground/15 bg-secondary px-3 pt-2.5 pb-2 text-secondary-foreground shadow-md outline-none dark:border-foreground/20",
        side === "below" ? "origin-top-left" : side === "above" ? "origin-bottom-left" : "origin-top-right",
        "duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        "motion-reduce:animate-none",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute size-2.5 rotate-45 border border-foreground/15 bg-secondary dark:border-foreground/20",
          side === "below"
            ? "-top-[6px] border-r-0 border-b-0"
            : side === "above"
              ? "-bottom-[6px] border-t-0 border-l-0"
              : "-right-[6px] border-b-0 border-l-0",
        )}
        style={inReader ? { left: placement.arrowX } : { top: placement.arrowY }}
      />
      <div className="-mt-0.5 -mr-1 mb-1 flex shrink-0 items-center justify-between gap-2 text-xs text-foreground/65">
        <span className="truncate">{language}</span>
        <div className="flex items-center">
          <button
            type="button"
            onClick={onTogglePin}
            aria-pressed={pinned}
            aria-label={
              pinned
                ? t("translationCard.unpin", "Unpin translation")
                : t("translationCard.pin", "Pin translation")
            }
            className="inline-flex size-6 items-center justify-center rounded-md hover:bg-foreground/10 hover:text-foreground"
          >
            {pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("translationCard.close", "Close translation")}
            className="inline-flex size-6 items-center justify-center rounded-md hover:bg-foreground/10 hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        </div>
      </div>
      <div
        key={contentKey ?? ""}
        ref={contentRef}
        data-card-content=""
        tabIndex={0}
        onWheel={pauseSync}
        onTouchMove={pauseSync}
        onKeyDown={pauseSync}
        className="relative min-h-0 flex-1 animate-in rounded-sm outline-none focus-visible:ring-1 focus-visible:ring-ring overflow-y-auto text-[13.5px] leading-[1.75] duration-150 fade-in-0 motion-reduce:animate-none"
      >
        {status === "ok" && html !== null ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : status === "unavailable" ? (
          <div className="flex items-center justify-between gap-2 text-foreground/65">
            <span>{t("translation.unavailable", "Translation unavailable")}</span>
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs hover:bg-foreground/10 hover:text-foreground"
            >
              <RotateCcw className="size-3" />
              {t("translationCard.retry", "Retry")}
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-foreground/65">
            <Loader2 className="size-3 animate-spin motion-reduce:animate-none" />
            {t("translation.pending", "Translating…")}
          </div>
        )}
      </div>
      <div className="mt-1.5 shrink-0 text-[11.5px] text-foreground/65">
        {pinned
          ? t("translationCard.hintPinned", "Pinned · Esc or ✕ to close")
          : t("translationCard.hintHover", "Click the paragraph to pin")}
      </div>
    </div>,
    document.body,
  );
}
