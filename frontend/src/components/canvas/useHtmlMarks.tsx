/** Marks in the HTML reader (Plan H5b.1): draws the paper's marks over the
 * loaded document and turns a selection into a mark through the popover
 * under it. Returns the popover to render over the iframe. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { MarkPopover, type SelectionBox } from "@/components/canvas/MarkPopover";
import { groupBySection, selectionToPieces, type HtmlPiece } from "@/lib/marks/anchor";
import { attachHtmlMarks, type HtmlMarksHandle } from "@/lib/marks/htmlMarks";
import { markHint, toastSaved, useMarkFocusIds, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkColor, MarkCreated } from "@/types/notes";

const NO_MARKS: Mark[] = [];
/** The most paragraphs one mark takes (a select-all is not a quote). */
export const MAX_MARK_PARAGRAPHS = 12;

interface Selected {
  /** The document it was made in (a new document drops it). */
  doc: Document;
  range: Range;
  box: SelectionBox;
  pieces: HtmlPiece[];
  /** Made from the keyboard: the popover takes focus. */
  keyboard: boolean;
}

/** What one selection has saved so far: a retry after a failure saves only
 * the sections still missing. Keyed by what is selected (each piece's chunk
 * range and quote, and the action's kind and colour), not by the Range
 * object: re-reading the same selection (a key press, a new mouseup) makes a
 * new Range over the same text. Another action starts fresh; the marks it
 * already saved stay as they are. */
interface Progress {
  key: string;
  saved: Map<string, MarkCreated>;
}

const selectionKey = (pieces: readonly HtmlPiece[], kind: string, color: string | null) =>
  JSON.stringify([kind, color, pieces.map((p) => [p.chunk_start, p.chunk_end, p.quote])]);

function boxOf(range: Range): SelectionBox {
  const r = range.getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
}

const groupKey = (g: { section: string | null; pieces: HtmlPiece[] }) =>
  `${g.section ?? ""}\u0000${g.pieces.map((p) => p.quote).join("\u0001")}`;

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
  const { t } = useTranslation("notes");
  const marks = useMarksStore((s) => (paperId === null ? NO_MARKS : (s.marksByPaper[paperId] ?? NO_MARKS)));
  const chunks = useMarksStore((s) => (paperId === null ? null : (s.chunksByPaper[paperId] ?? null)));
  const colors = useMarksStore((s) => s.colors);
  const visible = useMarksStore((s) => s.showMarks);
  const loadPaper = useMarksStore((s) => s.loadPaper);
  const loadColors = useMarksStore((s) => s.loadColors);
  const ensureChunks = useMarksStore((s) => s.ensureChunks);
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
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  useEffect(() => {
    if (paperId === null || !doc?.body) return;
    const win = doc.defaultView;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let capped = "";
    const read = (keyboard: boolean) => {
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
        if (pieces.length > MAX_MARK_PARAGRAPHS) {
          setSelected(null);
          const key = `${pieces.length}:${pieces[0]!.quote}`;
          if (key !== capped) {
            capped = key;
            toast.message(
              tRef.current("marks.tooLong", {
                defaultValue: "A mark holds up to {{count}} paragraphs. Select less to mark it.",
                count: MAX_MARK_PARAGRAPHS,
              }),
            );
          }
          return;
        }
        capped = "";
        setBounds({ width: win?.innerWidth ?? 0, height: win?.innerHeight ?? 0 });
        setSelected({ doc, range: range.cloneRange(), box: boxOf(range), pieces, keyboard });
      }, 0);
    };
    const onMouseUp = () => read(false);
    const onDown = () => setSelected(null);
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== "Escape") read(true);
    };
    // The popover follows its selection; scrolled out of view, it closes.
    const onScroll = () =>
      setSelected((cur) => {
        if (!cur) return cur;
        const box = boxOf(cur.range);
        const height = win?.innerHeight ?? 0;
        return box.bottom < 0 || box.top > height ? null : { ...cur, box };
      });
    doc.addEventListener("mouseup", onMouseUp);
    doc.addEventListener("mousedown", onDown);
    doc.addEventListener("keydown", onKeyDown);
    doc.addEventListener("keyup", onKeyUp);
    win?.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      clearTimeout(timer);
      doc.removeEventListener("mouseup", onMouseUp);
      doc.removeEventListener("mousedown", onDown);
      doc.removeEventListener("keydown", onKeyDown);
      doc.removeEventListener("keyup", onKeyUp);
      win?.removeEventListener("scroll", onScroll);
    };
  }, [doc, paperId]);

  const progress = useRef<Progress | null>(null);
  const [busy, setBusy] = useState(false);
  const save = useCallback(
    async (kind: "highlight" | "quote", color: MarkColor | null, addNote: boolean) => {
      if (!selected || paperId === null || !doc || busy) return;
      setBusy(true);
      try {
        // A mark needs its chunk ids and section: wait for them (or load
        // them again) rather than save it unanchored.
        let map;
        try {
          map = await ensureChunks(paperId);
        } catch {
          toast.error(t("marks.anchorsFailed", "Couldn't load where this passage sits in the paper. Try again."));
          return;
        }
        const pieces = selectionToPieces(doc, selected.range, map);
        if (!pieces.length || pieces.some((p) => p.chunk_start === null)) {
          toast.error(t("marks.noAnchor", "This passage isn't tied to the paper's text, so it can't be marked."));
          return;
        }
        const key = selectionKey(pieces, kind, color);
        if (progress.current?.key !== key) progress.current = { key, saved: new Map() };
        const done = progress.current.saved;
        const hint = markHint(paperId);
        // A mark files under one section: a selection across a section
        // break becomes one mark per section. A retry after a failure saves
        // only the sections still missing.
        for (const group of groupBySection(pieces, map)) {
          const key = groupKey(group);
          if (done.has(key)) continue;
          const res = await create(
            {
              paper_content_id: paperId,
              kind,
              color,
              view: "html",
              pieces: group.pieces,
              section_key: group.section,
              hint_block_id: hint,
            },
            { quiet: true },
          );
          done.set(key, res);
        }
        const results = [...done.values()];
        progress.current = null;
        toastSaved(paperId, results);
        doc.getSelection()?.removeAllRanges();
        setSelected(null);
        const last = results[results.length - 1];
        if (addNote && last) {
          const target = { noteId: last.saved_to.note_id, blockId: last.mark.block_id };
          useNotesStore.getState().setCaretAfter(target);
          useNotesPaneStore.getState().openAt({ kind: "paper", id: paperId, ...target });
        }
      } catch {
        // The store said so; the selection stays to try again.
      } finally {
        setBusy(false);
      }
    },
    [selected, paperId, doc, busy, ensureChunks, create, t],
  );

  if (paperId === null) return null;
  return (
    <MarkPopover
      open={selected !== null}
      anchor={selected?.box ?? null}
      bounds={bounds}
      colors={colors}
      busy={busy}
      autoFocus={selected?.keyboard ?? false}
      onHighlight={(c) => void save("highlight", c, false)}
      onQuote={() => void save("quote", null, false)}
      onAddNote={() => void save("quote", null, true)}
      onClose={(opts) => {
        setSelected(null);
        // Escape from the popover: back to the reader, selection kept.
        if (opts?.returnFocus) doc?.defaultView?.focus();
      }}
    />
  );
}
