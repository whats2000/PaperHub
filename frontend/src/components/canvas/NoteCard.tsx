/** The note card (Plan H5b.1): a click on a mark with the notes pane hidden
 * opens the paper's note at that mark in a card in the gutter left of the
 * reader, its arrow at the mark, always on screen (it docks to the reader's
 * edge when the mark scrolls away), stacked above the PDF translation card.
 *
 * The note of the section that quotes the mark is editable; the section's
 * other notes are folded to one line each ("Note 1 · 2 blocks · …", click to
 * open one). Only the clicked reference is tinted; every other reference is
 * a Show ↗ jumper that scrolls the paper to its mark and re-points the card.
 * The trash deletes the mark (Undo, then Delete again). Escape, a click
 * outside or the close button closes it: it shrinks back toward the mark. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, Languages, NotebookPen, PanelRightOpen, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { langShort, sectionLabel } from "@/components/notes/blocks/markProps";
import { partsOfNote, type Part } from "@/components/notes/parts";
import { usePresence } from "@/hooks/usePresence";
import { placeCard, type CardPlacement } from "@/lib/cardPlacement";
import { sameLanguage } from "@/lib/language";
import { prefersReducedMotion } from "@/lib/motion";
import { truncatedTitle } from "@/lib/truncatedTitle";
import { cn } from "@/lib/utils";
import { jumpToMark, markAnchor, onMarksMoved, useMarkNavStore, type NoteCardTarget } from "@/store/markNav";
import { useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import { useTranslationStore } from "@/store/translation";
import type { NoteBlock } from "@/types/notes";

/** The card's exit (it shrinks back toward the mark). */
const EXIT_MS = 150;
/** Gap between the card and the reader. */
const CARD_GAP = 12;
/** How long the card follows its mark frame by frame after it opens or
 * re-points (the reader is still scrolling there). */
const FOLLOW_MS = 1200;
/** A re-pointed card glides to its new mark. */
const GLIDE_MS = 200;
/** What else is part of the card: menus and toasts it opens, the
 * translation card it stacks with. A click there is not "outside". */
const NOT_OUTSIDE = [
  ".bn-container",
  ".bn-ui-container",
  "[data-base-ui-portal]",
  '[role="menu"]',
  '[role="listbox"]',
  '[role="dialog"]',
  "[data-sonner-toaster]",
].join(", ");
/** A layer inside the card that takes Escape first. */
const OWN_LAYER = '.bn-suggestion-menu, [role="menu"]:not([data-closed]), [role="listbox"]:not([data-closed])';

export function NoteCard() {
  const card = useMarkNavStore((s) => s.card);
  // The last target stays shown through the exit.
  const [last, setLast] = useState<NoteCardTarget | null>(card);
  if (card && card !== last) setLast(card);
  const { mounted, state } = usePresence(card !== null, EXIT_MS);
  if (!mounted || !last) return null;
  return <NoteCardBody target={last} open={card !== null} state={state} />;
}

interface SectionNotes {
  sectionKey: string | null;
  parts: Part<NoteBlock>[];
  /** The part quoting the mark. */
  index: number;
}

/** The notes (parts) of the section holding `blockId`. */
function sectionNotes(blocks: NoteBlock[], blockId: string): SectionNotes | null {
  const all = partsOfNote(blocks);
  const at = all.findIndex((p) => p.blocks.some((b) => b.id === blockId));
  if (at < 0) return null;
  // A section's first part starts at its heading; the next ones at dividers.
  let start = at;
  while (start > 0 && all[start]!.dividerId !== null) start--;
  let end = at + 1;
  while (end < all.length && all[end]!.dividerId !== null) end++;
  const parts = all.slice(start, end);
  const head = parts[0]?.blocks[0];
  return {
    sectionKey: head?.type === "section" ? (head.section_key ?? null) : null,
    parts,
    index: at - start,
  };
}

/** A folded note's first words. */
function firstWords(part: Part<NoteBlock>): string {
  const b = part.blocks.find((x) => x.type !== "section" && x.text.trim());
  const text = b?.text.replace(/\s+/g, " ").trim() ?? "";
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

function NoteCardBody({ target, open, state }: { target: NoteCardTarget; open: boolean; state: "open" | "closed" }) {
  const { t } = useTranslation("notes");
  const { paperId, markId } = target;
  const mark = useMarksStore((s) => s.marksByPaper[paperId]?.find((m) => m.id === markId));
  const marksLoaded = useMarksStore((s) => s.marksByPaper[paperId] !== undefined);
  const byTarget = useNotesStore((s) => s.byTarget[`paper:${paperId}`]);
  const noteId = mark?.note_id ?? byTarget;
  const note = useNotesStore((s) => (noteId === undefined ? undefined : s.notes[noteId]));
  const closeCard = useMarkNavStore((s) => s.closeCard);
  const showPart = useMarkNavStore((s) => s.showPart);

  // The paper's note, loaded once.
  useEffect(() => {
    const notes = useNotesStore.getState();
    if (noteId === undefined) void notes.loadTarget("paper", paperId).catch(() => undefined);
    else if (!notes.notes[noteId]) void notes.loadNote(noteId).catch(() => undefined);
  }, [noteId, paperId]);

  // The mark deleted (here or elsewhere): nothing left to show.
  useEffect(() => {
    if (open && marksLoaded && !mark) closeCard();
  }, [open, marksLoaded, mark, closeCard]);

  // Closed: what was typed here is sent, and the note is read again so the
  // notes pane shows it (its editor didn't see these edits).
  const openNoteRef = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (open) {
      openNoteRef.current = noteId;
      return;
    }
    const id = openNoteRef.current;
    if (id === undefined) return;
    openNoteRef.current = undefined;
    // `loadNote` sends the queued edits first.
    void useNotesStore
      .getState()
      .loadNote(id)
      .catch(() => undefined);
  }, [open, noteId]);

  const section = useMemo(
    () => (note && mark ? sectionNotes(note.blocks, mark.block_id) : null),
    [note, mark],
  );
  const current = section ? (section.parts.find((p) => p.id === target.partId) ?? section.parts[section.index]) : null;
  const currentIndex = section && current ? section.parts.indexOf(current) : -1;

  // ---- placement ------------------------------------------------------
  const cardRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<CardPlacement | null>(null);
  const placedRef = useRef(false);
  const setCardBox = useMarkNavStore((s) => s.setCardBox);
  const place = useCallback(() => {
    const anchor = markAnchor(paperId, markId);
    if (!anchor) return;
    const v = anchor.viewport;
    const next = placeCard({
      paraRects: anchor.rects,
      viewport: { top: v.top, bottom: v.bottom },
      gutter: { left: CARD_GAP, right: v.left - CARD_GAP },
      cardHeight: cardRef.current?.offsetHeight ?? 0,
      reader: { left: v.left, right: v.right },
      // Always on screen: docked to the edge the mark left by.
      pinned: true,
    });
    if (next) {
      placedRef.current = true;
      setPlacement(next);
    }
  }, [paperId, markId]);

  // Follows the mark: frame by frame while the reader scrolls to it, then on
  // every scroll and resize.
  // A re-pointed card glides there for a moment (scroll tracking never does).
  const [seenN, setSeenN] = useState(target.n);
  const [glidingN, setGlidingN] = useState<number | null>(null);
  if (seenN !== target.n) {
    setSeenN(target.n);
    setGlidingN(target.n);
  }
  const glide = glidingN === target.n;
  useEffect(() => {
    if (glidingN === null) return;
    const timer = setTimeout(() => setGlidingN(null), GLIDE_MS);
    return () => clearTimeout(timer);
  }, [glidingN]);
  useLayoutEffect(() => {
    if (!open) return;
    place();
    const until = performance.now() + FOLLOW_MS;
    let frame = 0;
    const follow = () => {
      place();
      if (performance.now() < until) frame = requestAnimationFrame(follow);
    };
    frame = requestAnimationFrame(follow);
    // Never placed (the mark isn't drawn where the card could point at it):
    // an unseen card would only hold Escape and clicks, so it goes.
    const giveUp = setTimeout(() => {
      if (!placedRef.current) closeCard();
    }, FOLLOW_MS);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(giveUp);
    };
  }, [open, target.n, place, closeCard]);
  useEffect(() => {
    if (!open) return;
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        place();
      });
    };
    window.addEventListener("scroll", schedule, { capture: true, passive: true });
    window.addEventListener("resize", schedule);
    const off = onMarksMoved(paperId, schedule);
    const el = cardRef.current;
    const ro = el && typeof ResizeObserver !== "undefined" ? new ResizeObserver(schedule) : null;
    if (el) ro?.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
      off();
      ro?.disconnect();
    };
  }, [open, paperId, place]);

  // The translation card stacks under this one.
  useLayoutEffect(() => {
    if (!open || !placement) {
      setCardBox(null);
      return;
    }
    const h = cardRef.current?.offsetHeight ?? 0;
    setCardBox({ top: placement.top, bottom: placement.top + Math.min(h, placement.maxHeight) });
  }, [open, placement, setCardBox]);
  useEffect(() => () => setCardBox(null), [setCardBox]);

  // ---- closing ----------------------------------------------------------
  useEffect(() => {
    if (!open) return;
    let downInside = false;
    const onDown = (e: PointerEvent) => {
      downInside = !!cardRef.current?.contains(e.target as Node);
    };
    const onClick = (e: MouseEvent) => {
      const inside = downInside;
      downInside = false;
      const el = e.target as Element | null;
      if (inside || cardRef.current?.contains(el) || el?.closest?.(NOT_OUTSIDE)) return;
      closeCard();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // The editor's own menus close first.
      if (cardRef.current?.querySelector(OWN_LAYER) || document.querySelector(".bn-suggestion-menu")) return;
      e.preventDefault();
      closeCard();
    };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("click", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, closeCard]);

  // ---- the clicked reference comes into view in the card -------------
  const bodyRef = useRef<HTMLDivElement>(null);
  const loaded = note !== undefined;
  useEffect(() => {
    if (!open || !loaded) return;
    const timer = setTimeout(() => {
      const body = bodyRef.current;
      const el = body?.querySelector<HTMLElement>('[data-target="true"]');
      if (!body || !el) return;
      const delta = el.getBoundingClientRect().top - body.getBoundingClientRect().top;
      if (delta < 0 || delta > body.clientHeight - 24) body.scrollTop += delta - 8;
    }, 60);
    return () => clearTimeout(timer);
  }, [open, loaded, target.n, current?.id]);

  // Another note of the section opened: it glides in where it was folded.
  const editorWrapRef = useRef<HTMLDivElement>(null);
  const shownPart = current?.id ?? null;
  const prevPart = useRef(shownPart);
  useLayoutEffect(() => {
    if (prevPart.current === shownPart) return;
    const had = prevPart.current;
    prevPart.current = shownPart;
    const el = editorWrapRef.current;
    if (had === null || !el || prefersReducedMotion() || typeof el.animate !== "function") return;
    el.animate(
      [
        { opacity: 0, transform: "translateY(-4px)" },
        { opacity: 1, transform: "none" },
      ],
      { duration: 200, easing: "ease-out" },
    );
  }, [shownPart]);

  // ---- a mark on a translation whose words aren't on screen -----------
  const trEnabled = useTranslationStore((s) => !!s.enabledByPaper[paperId]);
  const trLanguage = useTranslationStore((s) => s.language);
  const trOff = !!mark?.lang && !(trEnabled && sameLanguage(trLanguage, mark.lang));

  const onJump = useCallback((id: string) => jumpToMark(paperId, id), [paperId]);
  const editorCard = useMemo(
    () => (current ? { partId: current.id, markId, onJump } : undefined),
    [current, markId, onJump],
  );

  if (!placement) return null;
  const side = placement.side;
  const inReader = side === "below" || side === "above";
  const arrowAt = inReader ? placement.arrowX + 5 : placement.arrowY + 5;
  const origin =
    side === "below" ? `${arrowAt}px top` : side === "above" ? `${arrowAt}px bottom` : `right ${arrowAt}px`;

  const paperTitle = note?.note.title ?? "";
  const count = section?.parts.length ?? 0;
  const where = section?.sectionKey
    ? count > 1
      ? t("noteCard.where", {
          defaultValue: "› {{section}} · note {{index}} of {{count}}",
          section: sectionLabel(section.sectionKey),
          index: currentIndex + 1,
          count,
        })
      : t("noteCard.whereOne", { defaultValue: "› {{section}}", section: sectionLabel(section.sectionKey) })
    : "";
  const fullTitle = [paperTitle, where].filter(Boolean).join(" ");

  const folded = (part: Part<NoteBlock>, i: number) => {
    const n = part.blocks.filter((b) => b.type !== "section").length;
    const words = firstWords(part);
    return (
      <button
        key={part.id}
        type="button"
        data-testid={`note-card-part-${part.id}`}
        aria-expanded={false}
        onClick={() => showPart(part.id)}
        className="flex w-full min-w-0 items-center gap-[5px] rounded-[5px] py-[3px] pr-2 pl-[calc(var(--note-card-pad)-4.5px)] text-left text-[11.5px] whitespace-nowrap text-muted-foreground transition-colors duration-150 animate-in fade-in-0 hover:bg-accent hover:text-foreground motion-reduce:animate-none"
      >
        <ChevronRight className="size-3 shrink-0" aria-hidden />
        <span className="shrink-0">
          {t("noteCard.folded", { defaultValue: "Note {{index}} · {{count}} blocks", index: i + 1, count: n })}
        </span>
        {words && <span className="min-w-0 truncate">· {words}</span>}
      </button>
    );
  };

  return createPortal(
    <div
      ref={cardRef}
      role="dialog"
      aria-label={t("noteCard.label", "Note for this passage")}
      data-testid="note-card"
      data-state={state}
      data-side={side}
      style={{
        left: placement.left,
        top: placement.top,
        width: placement.width,
        maxHeight: placement.maxHeight,
        transformOrigin: origin,
        transition: glide && !prefersReducedMotion() ? "top 200ms ease-out, left 200ms ease-out" : undefined,
      }}
      className={cn(
        "note-card fixed z-50 flex flex-col rounded-md border border-foreground/15 bg-secondary text-[13px] text-secondary-foreground shadow-md outline-none dark:border-foreground/20",
        "duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-90",
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
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border py-1.5 pr-1.5 pl-2.5 font-medium">
        <NotebookPen className="size-3.5 shrink-0" aria-hidden />
        <span data-testid="note-card-title" className="flex min-w-0 flex-1 items-baseline gap-1" {...truncatedTitle(fullTitle)}>
          <span data-truncate className="min-w-0 truncate">
            {paperTitle}
          </span>
          {where && <span className="shrink-0">{where}</span>}
        </span>
        <button
          type="button"
          aria-label={t("noteCard.delete", "Delete this mark")}
          title={t("noteCard.deleteHint", "Delete this mark (your own notes are kept)")}
          onClick={() => {
            void useMarksStore.getState().remove(markId);
            closeCard();
          }}
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("noteCard.close", "Close")}
          onClick={closeCard}
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
      <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {mark?.lang && (
          <div
            data-state={trOff ? "open" : "closed"}
            className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
          >
            <div className="min-h-0 overflow-hidden">
              <p
                data-testid="note-card-translation"
                className="mx-2.5 mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-dashed border-border px-2 py-1 text-xs text-muted-foreground"
              >
                <span>
                  {t("noteCard.trMarkedIn", {
                    defaultValue: "Marked in {{lang}}; the paper is showing {{shown}}",
                    lang: langShort(mark.lang),
                    shown: trEnabled ? langShort(trLanguage) : t("noteCard.trOriginal", "the original"),
                  })}
                </span>
                <button
                  type="button"
                  tabIndex={trOff ? 0 : -1}
                  onClick={() => useTranslationStore.getState().showMark(markId)}
                  className="inline-flex items-center gap-1 rounded border border-border bg-background px-1.5 py-px text-foreground transition-colors hover:bg-accent"
                >
                  <Languages className="size-3" aria-hidden />
                  {t("noteCard.trSwitch", { defaultValue: "Switch to {{lang}}", lang: langShort(mark.lang) })}
                </button>
              </p>
            </div>
          </div>
        )}
        {!note || !section || !current || noteId === undefined ? (
          <p role="status" className="px-3 py-1.5 text-xs text-muted-foreground">
            {t("loading", "Loading the note…")}
          </p>
        ) : (
          <>
            {section.parts.slice(0, currentIndex).map((p, i) => folded(p, i))}
            <div ref={editorWrapRef}>
              <NoteEditor noteId={noteId} variant="card" card={editorCard} />
            </div>
            {section.parts.slice(currentIndex + 1).map((p, i) => folded(p, currentIndex + 1 + i))}
          </>
        )}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border px-2.5 py-1.5 text-[11.5px] text-muted-foreground">
        <span className="min-w-0 truncate">{t("noteCard.footer", "Edit right here · saved automatically")}</span>
        <button
          type="button"
          disabled={noteId === undefined}
          onClick={() => {
            if (noteId === undefined) return;
            useNotesPaneStore
              .getState()
              .openAt({ kind: "paper", id: paperId, noteId, blockId: mark?.block_id ?? null });
            closeCard();
          }}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          <PanelRightOpen className="size-3" aria-hidden />
          {t("noteCard.openInNotes", "Open in Notes")}
        </button>
      </div>
    </div>,
    document.body,
  );
}
