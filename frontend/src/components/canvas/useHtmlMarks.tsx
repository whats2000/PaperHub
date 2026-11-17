/** Marks in the HTML reader (Plan H5b.1): draws the paper's marks over the
 * loaded document and turns a selection into a mark through the popover
 * under it; a figure or a display equation is marked whole from the button
 * at its corner. Returns the popover and the corner buttons to render over
 * the iframe. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { MarkPopover, type SelectionBox } from "@/components/canvas/MarkPopover";
import { ObjectMarkButton } from "@/components/canvas/ObjectMarkButton";
import { openMarkInNotes, useMarkSave, type MarkGroup } from "@/components/canvas/useMarkSave";
import { groupBySection, selectionToPieces, type ChunkMap, type HtmlPiece } from "@/lib/marks/anchor";
import { attachHtmlMarks, markableObjects, type HtmlMarksHandle, type HtmlObject } from "@/lib/marks/htmlMarks";
import { useMarkFocusIds, useMarksStore } from "@/store/marks";
import type { Mark, MarkColor } from "@/types/notes";

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
  /** A figure or equation taken whole (its corner button). */
  object?: HtmlObject;
}

/** How long a corner button stays after the pointer leaves its object. */
const OBJECT_HIDE_MS = 120;

interface PlacedObject {
  obj: HtmlObject;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface MathJaxStartup {
  MathJax?: { startup?: { promise?: Promise<unknown> } };
}

/** The paper's figures and display equations in view, placed in the
 * iframe's coordinates, and which one the pointer is over. */
function useHtmlObjects(doc: Document | null, enabled: boolean) {
  // Placed for one document: another document starts with none.
  const [placed, setPlaced] = useState<{ doc: Document; list: PlacedObject[] } | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled || !doc?.body) return;
    const win = doc.defaultView;
    let list = markableObjects(doc);
    const place = () => {
      const height = win?.innerHeight || Infinity;
      setPlaced({
        doc,
        list: list
          .map((obj) => {
            const r = obj.el.getBoundingClientRect();
            return { obj, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
          })
          .filter((p) => p.right > p.left && p.bottom > p.top && p.bottom > 0 && p.top < height),
      });
    };
    let frame = 0;
    const schedule = () => {
      if (frame || !win) return;
      frame = win.requestAnimationFrame(() => {
        frame = 0;
        place();
      });
    };
    let hideTimer: ReturnType<typeof setTimeout> | undefined;
    const onMove = (e: MouseEvent) => {
      // The document's own Node (an iframe has its own globals).
      const target = e.target as Node | null;
      const hit = target ? list.find((o) => o.el.contains(target)) : undefined;
      clearTimeout(hideTimer);
      if (hit) setHovered(hit.key);
      else hideTimer = setTimeout(() => setHovered(null), OBJECT_HIDE_MS);
    };
    const onLeave = () => {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => setHovered(null), OBJECT_HIDE_MS);
    };
    place();
    // MathJax typesets display math after load: find the objects again.
    void (win as (Window & MathJaxStartup) | null)?.MathJax?.startup?.promise
      ?.then(() => {
        list = markableObjects(doc);
        schedule();
      })
      .catch(() => undefined);
    win?.addEventListener("scroll", schedule, { passive: true });
    win?.addEventListener("resize", schedule);
    doc.addEventListener("mousemove", onMove);
    doc.documentElement.addEventListener("mouseleave", onLeave);
    return () => {
      clearTimeout(hideTimer);
      if (frame && win) win.cancelAnimationFrame(frame);
      win?.removeEventListener("scroll", schedule);
      win?.removeEventListener("resize", schedule);
      doc.removeEventListener("mousemove", onMove);
      doc.documentElement.removeEventListener("mouseleave", onLeave);
    };
  }, [doc, enabled]);
  return { placed: enabled && placed?.doc === doc ? placed.list : [], hovered };
}

/** The chunk of the last `phchunk` span before an element. */
function chunkBefore(el: Element, map: ChunkMap): number | null {
  let found: number | null = null;
  for (const span of Array.from(el.ownerDocument.querySelectorAll('span[id^="phchunk-"]'))) {
    if (!(span.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
    found = map.byDom.get(span.id)?.id ?? found;
  }
  return found;
}

/** The one piece of a figure or equation taken whole: its caption or TeX
 * as the quote (anchored like a passage), and its figure id or LaTeX (the
 * paper's own source, not a model's transcription). */
function objectPiece(doc: Document, obj: HtmlObject, map: ChunkMap): HtmlPiece | null {
  const range = doc.createRange();
  range.selectNode(obj.el);
  const pieces = selectionToPieces(doc, range, map);
  if (obj.kind === "equation") {
    const p = pieces[0];
    if (!p || p.chunk_start === null) return null;
    return { ...p, latex: p.quote.replace(/^\$|\$$/g, ""), latex_from: "source" };
  }
  const first = pieces[0];
  const last = pieces[pieces.length - 1];
  const chunk = first?.chunk_start ?? chunkBefore(obj.el, map);
  if (chunk === null) return null;
  return {
    quote: pieces.map((p) => p.quote).join(" "),
    chunk_start: chunk,
    chunk_end: last?.chunk_end ?? chunk,
    prefix: first?.prefix ?? "",
    suffix: last?.suffix ?? "",
    figure_id: obj.figureId,
  };
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
  const { t } = useTranslation("notes");
  const marks = useMarksStore((s) => (paperId === null ? NO_MARKS : (s.marksByPaper[paperId] ?? NO_MARKS)));
  const chunks = useMarksStore((s) => (paperId === null ? null : (s.chunksByPaper[paperId] ?? null)));
  const colors = useMarksStore((s) => s.colors);
  const visible = useMarksStore((s) => s.showMarks);
  const loadPaper = useMarksStore((s) => s.loadPaper);
  const loadColors = useMarksStore((s) => s.loadColors);
  const ensureChunks = useMarksStore((s) => s.ensureChunks);
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
        if (pid !== null) openMarkInNotes(pid, mark);
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

  const { busy, save: saveMarks } = useMarkSave(paperId);
  const save = useCallback(
    async (kind: "highlight" | "quote", color: MarkColor | null, addNote: boolean) => {
      if (!selected || paperId === null || !doc) return;
      const obj = selected.object;
      const ok = await saveMarks({
        kind: obj ? obj.kind : kind,
        color,
        view: "html",
        addNote,
        groups: async () => {
          // A mark needs its chunk ids and section: wait for them (or load
          // them again) rather than save it unanchored.
          let map;
          try {
            map = await ensureChunks(paperId);
          } catch {
            toast.error(t("marks.anchorsFailed", "Couldn't load where this passage sits in the paper. Try again."));
            return null;
          }
          if (obj) {
            const piece = objectPiece(doc, obj, map);
            if (!piece) {
              toast.error(t("marks.noAnchor", "This passage isn't tied to the paper's text, so it can't be marked."));
              return null;
            }
            const section = (piece.chunk_start !== null && map.byId.get(piece.chunk_start)?.section) || null;
            const groups: MarkGroup[] = [{ section, pieces: [piece] }];
            return groups;
          }
          const pieces = selectionToPieces(doc, selected.range, map);
          if (!pieces.length || pieces.some((p) => p.chunk_start === null)) {
            toast.error(t("marks.noAnchor", "This passage isn't tied to the paper's text, so it can't be marked."));
            return null;
          }
          // A selection across a section break: one mark per section.
          return groupBySection(pieces, map);
        },
      });
      if (!ok) return;
      doc.getSelection()?.removeAllRanges();
      setSelected(null);
    },
    [selected, paperId, doc, ensureChunks, saveMarks, t],
  );

  const objects = useHtmlObjects(doc, paperId !== null);
  const [overButton, setOverButton] = useState<string | null>(null);
  const openObject = (obj: HtmlObject) => {
    if (!doc) return;
    const win = doc.defaultView;
    const range = doc.createRange();
    range.selectNode(obj.el);
    doc.getSelection()?.removeAllRanges();
    setBounds({ width: win?.innerWidth ?? 0, height: win?.innerHeight ?? 0 });
    const r = obj.el.getBoundingClientRect();
    setSelected({
      doc,
      range,
      box: { top: r.top, bottom: r.bottom, left: r.left, right: r.right },
      pieces: [],
      keyboard: false,
      object: obj,
    });
  };

  if (paperId === null) return null;
  return (
    <>
      {objects.placed.map(({ obj, top, right }) => (
        <ObjectMarkButton
          key={obj.key}
          kind={obj.kind}
          shown={objects.hovered === obj.key || overButton === obj.key}
          style={{
            left: right,
            top,
            transform: obj.kind === "figure" ? "translate(calc(-100% - 6px), 6px)" : "translate(-100%, calc(-100% - 2px))",
          }}
          onHoverChange={(over) => setOverButton(over ? obj.key : null)}
          onActivate={() => openObject(obj)}
        />
      ))}
      <MarkPopover
        open={selected !== null}
        anchor={selected?.box ?? null}
        bounds={bounds}
        colors={colors}
        busy={busy}
        target={selected?.object?.kind ?? "selection"}
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
    </>
  );
}
