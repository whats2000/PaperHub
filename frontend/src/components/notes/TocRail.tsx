/** The floating table of contents (as in Notion): with two or more headings
 * or sections, a slim rail at the notes pane's right edge, one short line
 * per entry (shorter by level), over the content so it takes no width.
 * Hovering (or focusing) it slides the full list out over the note, leaving
 * retracts it after a moment; a click glides to the entry and flashes it,
 * opening a folded part. Scrolling the pane highlights the entry being read:
 * the spy only reads the scroll position, so Follow reading's own scrolling
 * just moves the highlight. Off by the notes ⋯ menu (remembered). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { noteOutline, type OutlineEntry } from "@/lib/notes/outline";
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
  const fresh = useMemo(() => noteOutline(blocks ?? []), [blocks]);
  const [kept, setKept] = useState(fresh);
  if (kept !== fresh && !sameOutline(kept, fresh)) setKept(fresh);
  return kept;
}

/** The entry being read: the last one whose heading is at or above the pane's
 * top + 90 px. A clicked entry stays current while it is on screen (a reveal
 * centres it, below that line). */
function useScrollSpy(root: HTMLElement | null, outline: OutlineEntry[], active: boolean) {
  const [current, setCurrent] = useState<string | null>(null);
  const pinned = useRef<string | null>(null);

  const compute = useCallback(() => {
    if (!root) return;
    const box = root.getBoundingClientRect();
    if (pinned.current) {
      const el = blockEl(root, pinned.current);
      const r = el?.getBoundingClientRect();
      if (r && r.bottom > box.top && r.top < box.bottom) {
        setCurrent(pinned.current);
        return;
      }
      pinned.current = null;
    }
    const line = box.top + TOC_SPY_OFFSET_PX;
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

  useEffect(() => {
    if (!root || !active) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        compute();
      });
    };
    compute();
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      root.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [root, active, compute]);

  const pin = (blockId: string) => {
    pinned.current = blockId;
    setCurrent(blockId);
  };
  return { current, pin };
}

export function TocRail({ noteId, root }: { noteId: number | null; root: HTMLElement | null }) {
  const { t } = useTranslation();
  const tocOn = useNotesStore((s) => s.tocOn);
  const outline = useOutline(noteId);
  const rows = useTocRows(outline);
  const visible = tocOn && noteId !== null && outline.length >= 2;

  // The rail fades in, and out before it unmounts.
  const [mounted, setMounted] = useState(visible);
  const [shown, setShown] = useState(visible);
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
  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const show = () => {
    cancelClose();
    setOpen(true);
  };
  const hideSoon = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      setOpen(false);
    }, TOC_RAIL_CLOSE_MS);
  };
  useEffect(() => cancelClose, []);
  if (!visible && open) setOpen(false);

  const { current, pin } = useScrollSpy(root, outline, mounted);
  const railRef = useRef<HTMLButtonElement>(null);

  const go = (blockId: string) => {
    if (!root || noteId === null) return;
    pin(blockId);
    void revealBlock(root, blockId, { unfold: unfoldInNote(noteId) });
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
      onFocus={show}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) hideSoon();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          cancelClose();
          setOpen(false);
          railRef.current?.focus();
        }
      }}
    >
      {/* A control over the note: half-seen when idle, solid on hover. */}
      <button
        ref={railRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        title={label}
        onClick={() => (open ? setOpen(false) : show())}
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
