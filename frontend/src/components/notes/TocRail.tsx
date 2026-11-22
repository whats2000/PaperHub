/** The floating table of contents (as in Notion): with two or more headings
 * or sections, a slim rail at the notes pane's right edge, inside the note's
 * scroll area and clear of its scrollbar, one short line per entry (shorter
 * by level), over the content so it takes no width; its lines close up so it
 * fits half the pane. Resting on it (150 ms; passing over does nothing), a
 * click or a tap, or Enter / Space on it opens the list: each line grows
 * leftward into its own row while the card grows from the right edge, the
 * current row at its line's height. Leaving (after 150 ms; coming back
 * cancels) or Esc closes it, the exact reverse. A click on an entry glides to
 * it and flashes it, opening a folded part; the list stays until the pointer
 * leaves. Scrolling the note highlights the entry being read: the spy only
 * reads the scroll position, so Follow reading's own scrolling just moves the
 * highlight. Off by the notes ⋯ menu (remembered). */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";

import { prefersReducedMotion } from "@/lib/motion";
import { storedNoteOutline, type OutlineEntry } from "@/lib/notes/outline";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { cn } from "@/lib/utils";
import { useNotesStore } from "@/store/notes";

import { useTocRows } from "./blocks/useTocRows";
import { listLayout, railGap, railRight, scrollRowIntoList, TOC_ROW_PX } from "./tocRailLayout";

/** How long the pointer rests on the rail before the list opens. */
export const TOC_RAIL_OPEN_MS = 150;
/** How long the list stays after the pointer leaves. */
export const TOC_RAIL_CLOSE_MS = 150;
/** The list's opening (and, reversed, its closing). */
const LIST_MOTION_MS = 200;
const LIST_FADE_MS = 120;
/** The rail's top, below the pane's top edge. */
const RAIL_TOP_PX = 24;
/** A row's indent per level. */
const INDENT_PX = 14;
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

type Phase = "closed" | "opening" | "open" | "closing";

const clampIndex = (i: number, n: number) => Math.max(0, Math.min(n - 1, i));

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

  const { current, pin, arm } = useScrollSpy(root, outline, mounted, foldKey);
  const currentIndex = Math.max(
    0,
    rows.findIndex((r) => r.blockId === current),
  );
  const count = rows.length;

  const railRef = useRef<HTMLButtonElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLElement>(null);

  // Where the rail sits: clear of the note's scrollbar, its gap fitting half
  // the pane. Measured again when the pane or the note resizes.
  const [place, setPlace] = useState({ right: railRight(null), gap: 8 });
  useLayoutEffect(() => {
    if (!root || !mounted) return;
    const scroller = root.querySelector<HTMLElement>('[data-testid="note-editor"]');
    const measure = () => {
      const next = { right: railRight(scroller), gap: railGap(count, root.clientHeight) };
      setPlace((p) => (p.right === next.right && p.gap === next.gap ? p : next));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    if (scroller) ro.observe(scroller);
    return () => ro.disconnect();
  }, [root, mounted, count]);

  // The list's motion: React holds the phase, the animations live in refs.
  const [phase, setPhaseState] = useState<Phase>("closed");
  const phaseRef = useRef<Phase>("closed");
  const anims = useRef<Animation[]>([]);
  const token = useRef(0);
  const pending = useRef<"open" | "close" | null>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hovered = useRef(false);
  const geometry = useRef({ count, currentIndex, root });
  useLayoutEffect(() => {
    geometry.current = { count, currentIndex, root };
  });

  const motion = useMemo(() => {
    const setPhase = (p: Phase) => {
      phaseRef.current = p;
      setPhaseState(p);
    };
    const clearTimers = () => {
      if (openTimer.current) clearTimeout(openTimer.current);
      if (closeTimer.current) clearTimeout(closeTimer.current);
      openTimer.current = closeTimer.current = null;
    };
    const dropAnims = () => {
      anims.current.forEach((a) => a.cancel());
      anims.current = [];
    };
    /** When the running animations end (none: at once, `later` false),
     * `then`, unless a newer open / close came first. */
    const settle = (then: (later: boolean) => void) => {
      const mine = ++token.current;
      const run = anims.current;
      if (run.length === 0) {
        then(false);
        return;
      }
      Promise.all(run.map((a) => a.finished)).then(
        () => {
          if (mine === token.current) then(true);
        },
        () => {},
      );
    };
    /** Ends the motion in phase `p`. The phase reaches the DOM before the
     * animations let go of their end frame: cancelled first, a closing
     * list would stand at full size, visible, for the frame before React
     * renders it hidden. (`later` false: inside a layout effect, where
     * React renders the update before the paint anyway.) */
    const finish = (p: "open" | "closed") => (later: boolean) => {
      if (later) flushSync(() => setPhase(p));
      else setPhase(p);
      dropAnims();
    };
    const fades = () => {
      const list = listRef.current;
      if (!list) return;
      const max = list.scrollHeight - list.clientHeight;
      list.style.setProperty("--ft", list.scrollTop > 1 ? "22px" : "0px");
      list.style.setProperty("--fb", list.scrollTop < max - 1 ? "22px" : "0px");
    };
    /** A line's centre, in the rail wrapper's coordinates. */
    const lineY = (i: number) => {
      const rail = railRef.current;
      const line = rail?.querySelectorAll<HTMLElement>("[data-toc-line]")[i];
      if (!rail || !line) return 0;
      return rail.offsetTop + line.offsetTop + line.offsetHeight / 2;
    };
    /** Sizes and places the card and the list: the current row lands at
     * its line's height. */
    const layout = () => {
      const { count: n, currentIndex: cur, root: pane } = geometry.current;
      const card = cardRef.current;
      const list = listRef.current;
      if (!pane || !card || !list) return;
      const l = listLayout({
        count: n,
        current: cur,
        lineY,
        paneTop: -RAIL_TOP_PX,
        paneHeight: pane.clientHeight,
        paneWidth: pane.clientWidth,
      });
      for (const el of [card, list]) {
        el.style.top = `${l.top}px`;
        el.style.height = `${l.height}px`;
        el.style.width = `${l.width}px`;
      }
      list.scrollTop = l.scrollTop;
      fades();
    };
    /** Each row starts as its line (size, place, colour) and grows leftward
     * into the full row, its label fading in, while the card grows from the
     * right edge; closing plays the same in reverse. Reduced motion: a short
     * fade. */
    const animate = (dir: "open" | "close"): Animation[] => {
      const card = cardRef.current;
      const list = listRef.current;
      const rail = railRef.current;
      if (!card || !list || !rail || typeof card.animate !== "function") return [];
      const reduced = prefersReducedMotion();
      const o: KeyframeAnimationOptions = {
        duration: reduced ? LIST_FADE_MS : LIST_MOTION_MS,
        easing: dir === "open" ? "ease-out" : "ease-in",
        fill: "both",
        direction: dir === "open" ? "normal" : "reverse",
      };
      const fade = [{ opacity: 0 }, { opacity: 1 }];
      if (reduced) return [card.animate(fade, o), list.animate(fade, o)];
      const out = [
        card.animate(
          [{ transform: "scaleX(.06)", opacity: 0 }, { opacity: 1, offset: 0.35 }, { transform: "none", opacity: 1 }],
          o,
        ),
      ];
      const lines = rail.querySelectorAll<HTMLElement>("[data-toc-line]");
      list.querySelectorAll<HTMLElement>("[data-toc-row]").forEach((row, i) => {
        const bar = row.querySelector<HTMLElement>("[data-toc-bar]");
        const label = row.querySelector<HTMLElement>("[data-toc-label]");
        const line = lines[i];
        if (!bar || !label || !line) return;
        const b = bar.getBoundingClientRect();
        const x = line.getBoundingClientRect();
        if (!b.width || !b.height) return;
        const from =
          `translate(${x.right - b.right}px, ${x.top + x.height / 2 - (b.top + b.height / 2)}px) ` +
          `scale(${x.width / b.width}, ${x.height / b.height})`;
        out.push(
          bar.animate(
            [
              { transform: from, backgroundColor: getComputedStyle(line).backgroundColor },
              { transform: "none", backgroundColor: getComputedStyle(bar).backgroundColor },
            ],
            o,
          ),
          label.animate(
            [
              { opacity: 0, transform: "translateX(10px)" },
              { opacity: 0, transform: "translateX(8px)", offset: 0.3 },
              { opacity: 1, transform: "none" },
            ],
            o,
          ),
        );
      });
      return out;
    };

    const open = () => {
      clearTimers();
      const p = phaseRef.current;
      if (p === "open" || p === "opening") return;
      if (p === "closing" && anims.current.length) {
        // Coming back while it closes: the same motion turns around.
        anims.current.forEach((a) => a.reverse());
        setPhase("opening");
        settle(finish("open"));
        return;
      }
      pending.current = "open";
      setPhase("opening");
    };
    /** Closes the list (the reverse of its opening; `instant`: at once).
     * Focus in it goes back to the rail first: a hidden, inert list must
     * not hold the focus. */
    const close = (instant = false) => {
      clearTimers();
      const p = phaseRef.current;
      if (p === "closed" || (p === "closing" && !instant)) return;
      if (listRef.current?.contains(document.activeElement)) railRef.current?.focus({ preventScroll: true });
      if (instant) {
        ++token.current;
        pending.current = null;
        finish("closed")(true);
        return;
      }
      if (p === "opening" && anims.current.length) {
        anims.current.forEach((a) => a.reverse());
        setPhase("closing");
        settle(finish("closed"));
        return;
      }
      pending.current = "close";
      setPhase("closing");
    };
    /** Runs a requested open / close once its phase has rendered (the
     * list is in the DOM, visible). */
    const run = () => {
      const dir = pending.current;
      if (!dir) return;
      pending.current = null;
      dropAnims();
      if (dir === "open") layout();
      anims.current = animate(dir);
      settle(finish(dir === "open" ? "open" : "closed"));
    };
    const reset = () => {
      clearTimers();
      dropAnims();
      pending.current = null;
      phaseRef.current = "closed";
    };
    return { open, close, run, fades, clearTimers, reset };
  }, []);

  useLayoutEffect(() => motion.run());
  useEffect(() => motion.reset, [motion]);
  // A resize of the pane moves the rail and the rows: the list closes at once.
  useEffect(() => {
    if (!root || typeof ResizeObserver === "undefined") return;
    let first = true;
    const ro = new ResizeObserver(() => {
      if (first) first = false;
      else if (phaseRef.current !== "closed") motion.close(true);
    });
    ro.observe(root);
    return () => ro.disconnect();
  }, [root, motion]);
  // Turned off (or down to one entry): the list goes with the rail.
  if (!visible && phase !== "closed") setPhaseState("closed");
  useEffect(() => {
    if (!visible) motion.reset();
  }, [visible, motion]);

  const go = (blockId: string) => {
    if (!root || noteId === null) return;
    pin(blockId);
    void revealBlock(root, blockId, { unfold: unfoldInNote(noteId) }).then(() => arm(blockId));
  };
  const rowEls = () => [...(listRef.current?.querySelectorAll<HTMLElement>("[data-toc-row]") ?? [])];
  /** Moves the focus to row `i`, scrolling it into the list. */
  const focusRow = (i: number) => {
    const list = listRef.current;
    const row = rowEls()[i];
    if (!list || !row) return;
    row.focus({ preventScroll: true });
    list.scrollTop = scrollRowIntoList({
      rowTop: row.offsetTop,
      rowHeight: row.offsetHeight,
      scrollTop: list.scrollTop,
      clientHeight: list.clientHeight,
    });
    motion.fades();
  };
  // Enter / Space on the rail: once the rows are reachable, the current one
  // takes the focus.
  const focusCurrent = useRef(false);
  useEffect(() => {
    if (!focusCurrent.current || phase === "closed" || phase === "closing") return;
    focusCurrent.current = false;
    focusRow(currentIndex);
  });

  if (!mounted) return null;
  const label = t("notes:toc.title", "Table of contents");
  const isOpen = phase === "open" || phase === "opening";
  const showsList = phase !== "closed";

  return (
    <div
      data-testid="toc-rail"
      data-state={shown ? "open" : "closed"}
      data-list={phase}
      className="absolute z-20 transition-opacity duration-200 ease-out data-[state=closed]:opacity-0 motion-reduce:transition-none"
      style={{ top: RAIL_TOP_PX, right: place.right }}
      onPointerEnter={() => {
        hovered.current = true;
        if (closeTimer.current) clearTimeout(closeTimer.current);
        closeTimer.current = null;
        if (phaseRef.current === "closing") motion.open();
      }}
      onPointerLeave={() => {
        hovered.current = false;
        if (openTimer.current) clearTimeout(openTimer.current);
        openTimer.current = null;
        const p = phaseRef.current;
        // A list moved through by key stays until Esc or a jump.
        if ((p === "open" || p === "opening") && !listRef.current?.querySelector(":focus-visible"))
          closeTimer.current = setTimeout(() => motion.close(), TOC_RAIL_CLOSE_MS);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && (phaseRef.current === "open" || phaseRef.current === "opening")) {
          e.preventDefault();
          e.stopPropagation();
          motion.close();
        }
      }}
    >
      {/* The card under the rows grows from the rail's right edge. */}
      <div
        ref={cardRef}
        aria-hidden
        className={cn(
          "absolute right-0 z-[1] origin-right rounded-[10px] border border-border bg-popover shadow-md",
          !showsList && "invisible",
        )}
      />
      {/* Content being read: each line's own row, over the card. */}
      <nav
        ref={listRef}
        id={listId}
        data-testid="toc-rail-list"
        data-state={isOpen ? "open" : "closed"}
        aria-label={label}
        aria-hidden={!isOpen || undefined}
        inert={!isOpen || undefined}
        className={cn(
          "toc-rail-list absolute right-0 z-[2] overflow-y-auto overscroll-contain p-1.5 text-popover-foreground",
          !showsList && "invisible",
        )}
        onScroll={motion.fades}
        // A mouse click leaves the focus where it was: the list stays open
        // until the pointer leaves.
        onMouseDown={(e) => e.preventDefault()}
        onKeyDown={(e) => {
          const all = rowEls();
          const i = all.indexOf(document.activeElement as HTMLElement);
          if (i < 0) return;
          const to = ({ ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1 } as Record<string, number>)[
            e.key
          ];
          if (to === undefined) return;
          e.preventDefault();
          focusRow(clampIndex(to, all.length));
        }}
        onBlur={(e) => {
          const to = e.relatedTarget as Node | null;
          if (!e.currentTarget.contains(to) && to !== railRef.current && !hovered.current) motion.close();
        }}
      >
        <ul className="m-0 list-none p-0">
          {rows.map((r) => (
            <li key={r.blockId} data-state={r.state} aria-hidden={r.state === "out" ? true : undefined}>
              <button
                type="button"
                data-toc-row
                tabIndex={-1}
                aria-current={r.blockId === current ? "location" : undefined}
                onClick={(e) => {
                  go(r.blockId);
                  // By key (Enter): jump, and the focus goes back to the rail.
                  if (e.detail === 0) motion.close();
                }}
                className={cn(
                  "group relative block w-full text-left text-[12.5px] whitespace-nowrap text-muted-foreground outline-none",
                  r.level === 1 && "text-foreground",
                  r.blockId === current && "font-bold text-foreground",
                )}
                style={{ height: TOC_ROW_PX, opacity: r.state === "open" ? 1 : 0 }}
              >
                <span
                  data-toc-bar
                  aria-hidden
                  className={cn(
                    "pointer-events-none absolute inset-x-0 inset-y-px origin-right rounded-[5px] group-hover:bg-accent group-focus-visible:shadow-[inset_0_0_0_2px_var(--ring)]",
                    r.blockId === current && "bg-muted",
                  )}
                />
                <span
                  data-toc-label
                  className="relative block truncate pr-2"
                  style={{ lineHeight: `${TOC_ROW_PX}px`, paddingLeft: 8 + (r.level - 1) * INDENT_PX }}
                >
                  {r.text}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
      {/* The rail, a control over the note: one line per entry. Its lines
          give way to the rows they grow into while the list is out. */}
      <button
        ref={railRef}
        type="button"
        aria-label={label}
        aria-expanded={isOpen}
        aria-controls={listId}
        title={label}
        onPointerEnter={(e) => {
          if (e.pointerType === "touch" || phaseRef.current !== "closed") return;
          if (openTimer.current) clearTimeout(openTimer.current);
          openTimer.current = setTimeout(motion.open, TOC_RAIL_OPEN_MS);
        }}
        onPointerLeave={() => {
          if (phaseRef.current !== "closed") return;
          if (openTimer.current) clearTimeout(openTimer.current);
          openTimer.current = null;
        }}
        onClick={() => {
          const p = phaseRef.current;
          if (p === "closed" || p === "closing") motion.open();
          else motion.close();
        }}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " " && e.key !== "ArrowDown") return;
          e.preventDefault();
          focusCurrent.current = true;
          motion.open();
          if (phaseRef.current === "open") focusRow(currentIndex);
        }}
        // (Space would click on its key-up: the key-down above opened.)
        onKeyUp={(e) => {
          if (e.key === " ") e.preventDefault();
        }}
        className="relative z-[3] flex flex-col items-end rounded-md py-1.5 pl-3.5 outline-none focus-visible:shadow-[0_0_0_2px_var(--ring)]"
        style={{ gap: place.gap }}
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
            style={{
              width: LINE_WIDTH[r.level],
              opacity: r.state === "open" ? 1 : 0,
              visibility: showsList ? "hidden" : undefined,
            }}
          />
        ))}
      </button>
    </div>
  );
}
