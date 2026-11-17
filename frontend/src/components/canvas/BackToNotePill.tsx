/** "↩ back to note" (Plan H5b.1): after a note's source chip opened the
 * reader at its mark, a pill at the top of the reader returns to the note
 * block in one click. It fades in when it appears, and fades out once the
 * user has scrolled about a screen away from the mark (or opened another
 * paper). */
import { useEffect, useRef, useState } from "react";
import { CornerUpLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import { usePresence } from "@/hooks/usePresence";
import { cn } from "@/lib/utils";
import { markAnchor, onMarksMoved, useMarkNavStore, type BackToNote } from "@/store/markNav";
import { useCanvasStore } from "@/store/canvas";
import { useNotesPaneStore } from "@/store/notesPane";
import { useTranslationStore } from "@/store/translation";

/** How far from the mark (in screens) the user may scroll before the pill
 * goes. */
const AWAY_SCREENS = 1;
/** After a reader stops trying to show the mark, how long it may take to
 * draw it before the pill counts the mark as not shown. */
const GAVE_UP_MS = 2000;

/** Whether the mark is still within about a screen of the reader's view;
 * null while it isn't drawn (yet). */
function nearMark(back: BackToNote): boolean | null {
  const a = markAnchor(back.paperId, back.markId);
  if (!a) return null;
  const v = a.viewport;
  const r = a.rects[0]!;
  const mid = (r.top + r.bottom) / 2;
  const span = (v.bottom - v.top) * AWAY_SCREENS;
  return mid > v.top - span && mid < v.bottom + span;
}

export function BackToNotePill({ paperId }: { paperId: number | null }) {
  const { t } = useTranslation("notes");
  const back = useMarkNavStore((s) => s.back);
  const clearBack = useMarkNavStore((s) => s.clearBack);
  const ours = back !== null && back.paperId === paperId;
  // The last pill stays shown through its fade-out.
  const [last, setLast] = useState<BackToNote | null>(back);
  if (back && back !== last) setLast(back);
  const { mounted, state } = usePresence(ours, 200);
  // The view on screen: its marks are the ones measured.
  const readerView = useCanvasStore((s) => s.readerView);

  // Gone once the user scrolls about a screen away from the mark: armed
  // only after the reader has reached it (it scrolls there first).
  useEffect(() => {
    if (!back || back.paperId !== paperId) return;
    let reached = false;
    let frame = 0;
    const check = () => {
      frame = 0;
      const near = nearMark(back);
      if (near === null) return;
      if (near) reached = true;
      else if (reached) clearBack();
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(check);
    };
    window.addEventListener("scroll", schedule, { capture: true, passive: true });
    const off = onMarksMoved(back.paperId, schedule);
    // The reader may still be loading the paper: look again until it's there.
    const timer = setInterval(() => {
      if (!reached) schedule();
      else clearInterval(timer);
    }, 250);
    // The reader gave up showing the mark (not drawn, or the request was
    // never taken up): with nothing reached, the way back goes too.
    let gaveUp: ReturnType<typeof setTimeout> | undefined;
    const offReveal = useTranslationStore.subscribe((s, prev) => {
      const was = prev.reveal;
      if (!was || was.markId !== back.markId || s.reveal?.markId === back.markId) return;
      clearTimeout(gaveUp);
      gaveUp = setTimeout(() => {
        if (!reached && nearMark(back) !== true) clearBack();
      }, GAVE_UP_MS);
    });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule, { capture: true });
      off();
      offReveal();
      clearTimeout(gaveUp);
      clearInterval(timer);
    };
  }, [back, paperId, clearBack, readerView]);

  // The reader left the mark's paper for another: the way back is gone.
  const prevPaper = useRef(paperId);
  useEffect(() => {
    const was = prevPaper.current;
    prevPaper.current = paperId;
    const b = useMarkNavStore.getState().back;
    if (b && was !== paperId && was === b.paperId) clearBack();
  }, [paperId, clearBack]);

  if (!mounted || !last) return null;
  return (
    <button
      type="button"
      data-state={state}
      data-testid="back-to-note"
      onClick={() => {
        useNotesPaneStore
          .getState()
          .openAt({ kind: "paper", id: last.paperId, noteId: last.noteId, blockId: last.blockId });
        clearBack();
      }}
      className={cn(
        "absolute top-2 left-1/2 z-20 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-popover px-3 py-1 text-xs text-popover-foreground shadow-md transition-colors hover:bg-accent",
        "duration-200 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-1",
        "data-[state=closed]:pointer-events-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-1",
        "motion-reduce:animate-none",
      )}
    >
      <CornerUpLeft className="size-3.5" aria-hidden />
      {t("backPill", "Back to note")}
    </button>
  );
}
