/** Marks in the HTML reader (Plan H5b.1): draws the paper's marks over the
 * loaded document and turns a selection into a mark through the popover
 * under it. Returns the popover to render over the iframe. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { MarkPopover, type SelectionBox } from "@/components/canvas/MarkPopover";
import { groupBySection, selectionToPieces, type HtmlPiece } from "@/lib/marks/anchor";
import { attachHtmlMarks, type HtmlMarksHandle } from "@/lib/marks/htmlMarks";
import { markHint, useMarkFocusIds, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkColor, MarkCreated } from "@/types/notes";

const NO_MARKS: Mark[] = [];

interface Selected {
  /** The document it was made in (a new document drops it). */
  doc: Document;
  range: Range;
  box: SelectionBox;
  pieces: HtmlPiece[];
}

function boxOf(range: Range): SelectionBox {
  const r = range.getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
}

export function useHtmlMarks({
  paperId,
  doc,
  isDark,
}: {
  /** Null turns marks off (the standalone reader). */
  paperId: number | null;
  /** The loaded paper document (null until it has loaded). */
  doc: Document | null;
  isDark: boolean;
}): ReactNode {
  const marks = useMarksStore((s) => (paperId === null ? NO_MARKS : (s.marksByPaper[paperId] ?? NO_MARKS)));
  const chunks = useMarksStore((s) => (paperId === null ? null : (s.chunksByPaper[paperId] ?? null)));
  const colors = useMarksStore((s) => s.colors);
  const visible = useMarksStore((s) => s.showMarks);
  const loadPaper = useMarksStore((s) => s.loadPaper);
  const loadColors = useMarksStore((s) => s.loadColors);
  const create = useMarksStore((s) => s.create);
  const focusIds = useMarkFocusIds(paperId);

  useEffect(() => {
    if (paperId === null) return;
    void loadPaper(paperId).catch(() => undefined);
    void loadColors();
  }, [paperId, loadPaper, loadColors]);

  const latest = useRef({ marks, chunks, paperId });
  useEffect(() => {
    latest.current = { marks, chunks, paperId };
  }, [marks, chunks, paperId]);

  // The overlay, one per loaded document.
  const handleRef = useRef<HtmlMarksHandle | null>(null);
  useEffect(() => {
    if (paperId === null || !doc?.body) return;
    const handle = attachHtmlMarks(doc, {
      marks: [],
      colors: [],
      chunks: null,
      visible: true,
      focusIds: null,
      dark: false,
      // Until the note card: a click opens the mark's block in the notes.
      onClick: (markId) => {
        const { marks: list, paperId: pid } = latest.current;
        const mark = list.find((m) => m.id === markId);
        handleRef.current?.flash(markId);
        if (!mark || pid === null || mark.note_id === undefined) return;
        useNotesPaneStore
          .getState()
          .openAt({ kind: "paper", id: pid, noteId: mark.note_id, blockId: mark.block_id });
      },
    });
    handleRef.current = handle;
    return () => {
      if (handleRef.current === handle) handleRef.current = null;
      handle.detach();
    };
  }, [doc, paperId]);
  useEffect(() => {
    handleRef.current?.update({ marks, colors, chunks, visible, focusIds, dark: isDark });
  }, [doc, marks, colors, chunks, visible, focusIds, isDark]);

  // A selection opens the popover under it.
  const [selectedIn, setSelected] = useState<Selected | null>(null);
  const selected = selectedIn && selectedIn.doc === doc && paperId !== null ? selectedIn : null;
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (paperId === null || !doc?.body) return;
    const win = doc.defaultView;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => {
      clearTimeout(timer);
      // After the browser has settled the selection for this mouseup / key.
      timer = setTimeout(() => {
        const sel = doc.getSelection();
        const range = sel && !sel.isCollapsed && sel.rangeCount ? sel.getRangeAt(0) : null;
        const pieces = range ? selectionToPieces(doc, range, latest.current.chunks) : [];
        if (!range || !pieces.length) {
          setSelected(null);
          return;
        }
        setBounds({ width: win?.innerWidth ?? 0, height: win?.innerHeight ?? 0 });
        setSelected({ doc, range: range.cloneRange(), box: boxOf(range), pieces });
      }, 0);
    };
    const onDown = () => setSelected(null);
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== "Escape") read();
    };
    // The popover follows its selection; scrolled out of view, it closes.
    const onScroll = () =>
      setSelected((cur) => {
        if (!cur) return cur;
        const box = boxOf(cur.range);
        const height = win?.innerHeight ?? 0;
        return box.bottom < 0 || box.top > height ? null : { ...cur, box };
      });
    doc.addEventListener("mouseup", read);
    doc.addEventListener("mousedown", onDown);
    doc.addEventListener("keydown", onKeyDown);
    doc.addEventListener("keyup", onKeyUp);
    win?.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      doc.removeEventListener("mouseup", read);
      doc.removeEventListener("mousedown", onDown);
      doc.removeEventListener("keydown", onKeyDown);
      doc.removeEventListener("keyup", onKeyUp);
      win?.removeEventListener("scroll", onScroll);
    };
  }, [doc, paperId]);

  const [busy, setBusy] = useState(false);
  const save = useCallback(
    async (kind: "highlight" | "quote", color: MarkColor | null, addNote: boolean) => {
      if (!selected || paperId === null || busy) return;
      setBusy(true);
      const hint = markHint(paperId);
      let last: MarkCreated | null = null;
      try {
        // A mark files under one section: a selection across a section
        // break becomes one mark per section.
        for (const group of groupBySection(selected.pieces, chunks)) {
          last = await create({
            paper_content_id: paperId,
            kind,
            color,
            view: "html",
            pieces: group.pieces,
            section_key: group.section,
            hint_block_id: hint,
          });
        }
      } catch {
        return; // the store said so; the selection stays to try again
      } finally {
        setBusy(false);
      }
      doc?.getSelection()?.removeAllRanges();
      setSelected(null);
      if (addNote && last) {
        const target = { noteId: last.saved_to.note_id, blockId: last.mark.block_id };
        useNotesStore.getState().setCaretAfter(target);
        useNotesPaneStore.getState().openAt({ kind: "paper", id: paperId, ...target });
      }
    },
    [selected, paperId, busy, chunks, create, doc],
  );

  if (paperId === null) return null;
  return (
    <MarkPopover
      open={selected !== null}
      anchor={selected?.box ?? null}
      bounds={bounds}
      colors={colors}
      busy={busy}
      onHighlight={(color) => void save("highlight", color, false)}
      onQuote={() => void save("quote", null, false)}
      onAddNote={() => void save("quote", null, true)}
      onClose={() => setSelected(null)}
    />
  );
}
