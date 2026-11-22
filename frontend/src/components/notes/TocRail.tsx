/** The floating table of contents (as in Notion): with two or more headings
 * or sections, a slim rail at the notes pane's right edge, one short line
 * per entry (shorter by level), over the content so it takes no width.
 * Hovering, focusing or tapping it slides the full list out over the note;
 * leaving (or Esc) retracts it; a click glides to the entry and flashes it,
 * opening a folded part. Scrolling the pane highlights the entry being read:
 * the spy only reads the scroll position, so Follow reading's own scrolling
 * just moves the highlight. Off by the notes ⋯ menu (remembered). */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { storedNoteOutline, type OutlineEntry } from "@/lib/notes/outline";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { cn } from "@/lib/utils";
import { useNotesStore } from "@/store/notes";

import { TocList } from "./blocks/TocList";
import { useTocRows } from "./blocks/useTocRows";

/** How long the list stays after the pointer leaves. */
export const TOC_RAIL_CLOSE_MS = 180;
/** How long the rail takes to leave (toggled off, or down to one entry). */
const RAIL_EXIT_MS = 200;
/** An entry is current once its heading reaches this far below the pane's top. */
export const TOC_SPY_OFFSET_PX = 90;
/** A clicked entry stays current until its reveal has scrolled and then been
 * still this long. */
export const TOC_PIN_SETTLE_MS = 150;

const LINE_WIDTH: Record<1 | 2 | 3, number> = { 1: 16, 2: 11, 3: 7 };

const blockEl = (root: HTMLElement, id: string) =>
  root.querySelector<HTMLElement>(`.bn-block-outer[data-id="${CSS.escape(id)}"]`);

const sameOutline = (a: OutlineEntry[], b: OutlineEntry[]) =>
  a.length === b.length &&
  a.every((e, i) => e.blockId === b[i]!.blockId && e.text === b[i]!.text && e.level === b[i]!.level);

/** The note's outline, the same array while its entries stay the same (typing
 * in a paragraph does not touch the list). */
function useOutline(noteId: number | null): OutlineEntry[] {
  const blocks = useNotesStore((s) => (noteId === null ? undefined : s.notes[noteId]?.blocks));
  const fresh = useMemo(() => storedNoteOutline(blocks ?? []), [blocks]);
  const [kept, setKept] = useState(fresh);
  if (kept !== fresh && !sameOutline(kept, fresh)) setKept(fresh);
  return kept;
}

interface Pin {
  id: string;
  /** The reveal has found its block and started scrolling to it. */
  armed: boolean;
  /** Since then the pane has been still for TOC_PIN_SETTLE_MS. */
  settled: boolean;
  timer: ReturnType<typeof setTimeout> | null;
}

/** The entry being read: the last one whose heading is at or above the pane's
 * top + 90 px. A clicked entry is current at once and stays so through its
 * reveal (which centres it, below that line), however long the reveal waits
 * for its block: the settle window opens when the reveal scrolls, and the
 * first scroll after it settles (the user's, or Follow reading's) goes back
 * to the rule. A fold (once the note has redrawn) and a resize of the note
 * re-read it too. */
function useScrollSpy(root: HTMLElement | null, outline: OutlineEntry[], active: boolean, foldKey: string) {
  const [current, setCurrent] = useState<string | null>(null);
  const pin = useRef<Pin | null>(null);

  const compute = useCallback(() => {
    if (!root) return;
    if (pin.current) {
      setCurrent(pin.current.id);
      return;
    }
    const line = root.getBoundingClientRect().top + TOC_SPY_OFFSET_PX;
    let found: string | null = null;
    for (const e of outline) {
      const el = blockEl(root, e.blockId);
      // A folded part's heading has no place on screen.
      if (!el || el.getAttribute("data-folded") === "true") continue;
      if (el.getBoundingClientRect().top <= line) found = e.blockId;
      else break;
    }
    setCurrent(found);
  }, [root, outline]);

  const settleLater = useCallback(() => {
    const p = pin.current;
    if (!p) return;
    if (p.timer) clearTimeout(p.timer);
    p.timer = setTimeout(() => {
      p.timer = null;
      p.settled = true;
    }, TOC_PIN_SETTLE_MS);
  }, []);

  useEffect(() => {
    if (!root || !active) return;
    let frame = 0;
    const later = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        compute();
      });
    };
    const onScroll = () => {
      const p = pin.current;
      if (p && !p.settled) {
        // Still the reveal: waiting for its block, or its own scroll.
        if (p.armed) settleLater();
      } else if (p) pin.current = null;
      later();
    };
    compute();
    // Captured: the pane itself never scrolls (it clips), the note editor
    // inside it does, and a scroll event does not bubble.
    root.addEventListener("scroll", onScroll, { passive: true, capture: true });
    // The note content resizing (a narrower pane wraps it) moves the
    // headings; the rail's own zero-height layer is left out. The content
    // element is replaced on a note switch, so new children are watched too.
    let ro: ResizeObserver | null = null;
    let mo: MutationObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      const resize = new ResizeObserver(later);
      ro = resize;
      resize.observe(root);
      const watch = (el: Element) => {
        if (!el.hasAttribute("data-toc-layer")) resize.observe(el);
      };
      Array.from(root.children).forEach(watch);
      mo = new MutationObserver((records) => {
        for (const r of records) r.addedNodes.forEach((n) => n instanceof Element && watch(n));
      });
      mo.observe(root, { childList: true });
    }
    return () => {
      root.removeEventListener("scroll", onScroll, { capture: true });
      ro?.disconnect();
      mo?.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [root, active, compute, settleLater]);

  // A part folded or opened moves the headings below it, but only once the
  // note has redrawn its parts (its own effect, after this commit): measure
  // on the next frame.
  useEffect(() => {
    if (!root || !active) return;
    const frame = requestAnimationFrame(compute);
    return () => cancelAnimationFrame(frame);
  }, [root, active, compute, foldKey]);

  useEffect(
    () => () => {
      if (pin.current?.timer) clearTimeout(pin.current.timer);
    },
    [],
  );

  const pinTo = (blockId: string) => {
    if (pin.current?.timer) clearTimeout(pin.current.timer);
    pin.current = { id: blockId, armed: false, settled: false, timer: null };
    setCurrent(blockId);
  };
  /** The reveal of `blockId` is under way (or gave up): its scroll settles. */
  const arm = (blockId: string) => {
    if (pin.current?.id !== blockId) return;
    pin.current.armed = true;
    settleLater();
  };
  return { current, pin: pinTo, arm };
}

export function TocRail({ noteId, root }: { noteId: number | null; root: HTMLElement | null }) {
  const { t } = useTranslation();
  const listId = useId();
  const tocOn = useNotesStore((s) => s.tocOn);
  const foldKey = useNotesStore((s) => (noteId === null ? "" : (s.folded[noteId] ?? []).join(",")));
  const outline = useOutline(noteId);
  const rows = useTocRows(outline);
  const visible = tocOn && noteId !== null && outline.length >= 2;

  // The rail fades in (on first show too), and out before it unmounts.
  const [mounted, setMounted] = useState(visible);
  const [shown, setShown] = useState(false);
  if (visible && !mounted) setMounted(true);
  if (!visible && shown) setShown(false);
  useEffect(() => {
    if (visible) {
      const frame = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(frame);
    }
    const timer = setTimeout(() => setMounted(false), RAIL_EXIT_MS);
    return () => clearTimeout(timer);
  }, [visible]);

  const [open, setOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Esc hands focus back to the rail: that focus must not reopen the list.
  const quietFocus = useRef(false);
  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const show = () => {
    cancelClose();
    setOpen(true);
  };
  const railRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLElement>(null);
  /** Closes the list. Focus in it (a clicked entry) goes back to the rail
   * first, quietly: a hidden, inert list must not hold the focus. */
  const close = () => {
    cancelClose();
    if (listRef.current?.contains(document.activeElement)) {
      quietFocus.current = true;
      railRef.current?.focus();
    }
    setOpen(false);
  };
  const hideSoon = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      close();
    }, TOC_RAIL_CLOSE_MS);
  };
  useEffect(() => cancelClose, []);
  if (!visible && open) setOpen(false);

  const { current, pin, arm } = useScrollSpy(root, outline, mounted, foldKey);

  const go = (blockId: string) => {
    if (!root || noteId === null) return;
    pin(blockId);
    void revealBlock(root, blockId, { unfold: unfoldInNote(noteId) }).then(() => arm(blockId));
  };

  if (!mounted) return null;
  const label = t("notes:toc.title", "Table of contents");

  return (
    <div
      data-testid="toc-rail"
      data-state={shown ? "open" : "closed"}
      className="absolute top-2 right-1 z-20 transition-opacity duration-200 ease-out data-[state=closed]:opacity-0 motion-reduce:transition-none"
      onMouseEnter={show}
      onMouseLeave={hideSoon}
      onFocus={() => {
        if (quietFocus.current) quietFocus.current = false;
        else show();
      }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) hideSoon();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          close();
        }
      }}
    >
      {/* A control over the note: half-seen when idle, solid on hover. A
          click (or tap) only opens; Esc and leaving close. */}
      <button
        ref={railRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        title={label}
        onClick={show}
        className="flex max-h-[50vh] flex-col items-end gap-[7px] overflow-hidden rounded-md px-1.5 py-2 opacity-60 transition-opacity duration-150 hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-ring motion-reduce:transition-none"
      >
        {rows.map((r) => (
          <span
            key={r.blockId}
            data-toc-line
            data-current={r.blockId === current ? "true" : undefined}
            aria-hidden
            className={cn(
              "block h-[2px] shrink-0 rounded-full bg-muted-foreground/45 transition-[background-color,opacity] duration-200 ease-out motion-reduce:transition-none",
              r.blockId === current && "bg-foreground",
            )}
            style={{ width: LINE_WIDTH[r.level], opacity: r.state === "open" ? 1 : 0 }}
          />
        ))}
      </button>
      {/* Content being read: opaque, slides in from the rail, back out to it. */}
      <nav
        ref={listRef}
        id={listId}
        data-testid="toc-rail-list"
        data-state={open ? "open" : "closed"}
        aria-label={label}
        aria-hidden={!open || undefined}
        inert={!open || undefined}
        className="absolute top-0 right-0 max-h-[min(60vh,28rem)] w-64 max-w-[calc(100cqw-1.5rem)] origin-top-right overflow-y-auto rounded-[10px] border border-border bg-popover px-2 py-1.5 text-popover-foreground shadow-md transition-[transform,opacity] duration-200 ease-out data-[state=closed]:pointer-events-none data-[state=closed]:translate-x-3 data-[state=closed]:opacity-0 motion-reduce:transition-none"
      >
        <TocList rows={rows} onGo={go} currentId={current} disabled={!open} />
      </nav>
    </div>
  );
}
