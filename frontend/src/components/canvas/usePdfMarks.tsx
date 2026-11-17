/** A PDF reader's marks (Plan H5b.1): the paper's marks and Marker blocks,
 * the popover (a text selection, a figure or equation taken whole, a box),
 * saving, and Follow — the section of the Marker block at the top of the
 * view. Each react-pdf page draws its share with `PdfMarksPage`, which
 * reads this through `PdfMarksProvider`.
 *
 * A text mark keeps its line boxes as page fractions, taken from the
 * rendered text layer, and the Marker block that holds the selection's
 * centre (its `section` is where the mark files). The PDF is never changed.
 */
import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { MarkPopover, type SelectionBox } from "@/components/canvas/MarkPopover";
import type { MarkableObject } from "@/components/canvas/ObjectMarkButton";
import { openMarkInNotes, useMarkSave, type MarkGroup } from "@/components/canvas/useMarkSave";
import { usePdfBlocks } from "@/components/canvas/usePdfBlocks";
import { usePdfFollow } from "@/components/canvas/usePdfFollow";
import { locateText, type LocateResult } from "@/lib/api";
import { MAX_MARK_PARAGRAPHS } from "@/lib/marks/limits";
import {
  boxMark,
  captionFor,
  FIGURE_TYPES,
  layerSpans,
  PAGE,
  piecesFromFragments,
  selectionFragments,
  type BoxResult,
  type Frac,
  type PdfFragment,
  type PdfPiece,
} from "@/lib/marks/pdfGeometry";
import { useMarkFocusIds, useMarksStore } from "@/store/marks";
import type { Mark, MarkColor, MarkColorInfo, MarkPiece, PaperPageBlock } from "@/types/notes";

const NO_MARKS: Mark[] = [];
const NO_BLOCKS: PaperPageBlock[] = [];
const FLASH_MS = 1600;

/** A piece placed in the paper's reading text (a page with no Marker
 * blocks): its chunks and its span there. */
type Located = NonNullable<LocateResult["match"]>;

/** Pieces as stored (the section rides on the mark), grouped by section:
 * a mark files under one. */
function groupPieces(pieces: readonly { piece: PdfPiece; at?: Located }[]): MarkGroup[] {
  const groups: MarkGroup[] = [];
  for (const { piece: p, at } of pieces) {
    const section = at ? at.section : p.section;
    let piece: MarkPiece = { quote: p.quote, page: p.page, rects: p.rects };
    if (p.block_id) piece.block_id = p.block_id;
    if (at) {
      piece = {
        ...piece,
        chunk_start: at.chunk_start,
        chunk_end: at.chunk_end,
        text_start: at.text_start,
        text_end: at.text_end,
      };
    }
    const last = groups[groups.length - 1];
    if (last && last.section === section) last.pieces.push(piece);
    else groups.push({ section, pieces: [piece] });
  }
  return groups;
}

type Target =
  | {
      type: "text";
      range: Range;
      /** What was selected, read when it was: saved even if the text layer
       * re-renders (a resize) before the choice. */
      fragments: PdfFragment[];
      keyboard: boolean;
      box: SelectionBox;
    }
  | { type: "object"; kind: MarkableObject; page: number; block: PaperPageBlock; box: SelectionBox }
  | { type: "box"; page: number; frac: Frac; scanned: boolean; result: BoxResult | null; box: SelectionBox };

export interface PdfMarksApi {
  paperId: number;
  marks: Mark[];
  colors: readonly MarkColorInfo[];
  visible: boolean;
  focusIds: ReadonlySet<string> | null;
  boxOn: boolean;
  blocks: Readonly<Record<number, PaperPageBlock[]>>;
  loadBlocks: (page: number) => Promise<PaperPageBlock[]>;
  hot: string | null;
  setHot: (id: string | null) => void;
  flashId: string | null;
  /** A click on a mark. */
  openMark: (markId: string) => void;
  openObject: (page: number, block: PaperPageBlock) => void;
  openBox: (page: number, box: Frac, pageEl: Element, scanned: boolean) => void;
  /** The box waiting for its popover choice (drawn on its page). */
  pendingBox: { page: number; frac: Frac } | null;
}

export const PdfMarksContext = createContext<PdfMarksApi | null>(null);
export const PdfMarksProvider = PdfMarksContext.Provider;

function boxOf(r: { top: number; bottom: number; left: number; right: number }, origin: DOMRect): SelectionBox {
  return { top: r.top - origin.top, bottom: r.bottom - origin.top, left: r.left - origin.left, right: r.right - origin.left };
}

/** A page fraction box in client coordinates. */
function fracToClient(f: Frac, page: DOMRect) {
  return {
    left: page.left + f[0] * page.width,
    right: page.left + f[2] * page.width,
    top: page.top + f[1] * page.height,
    bottom: page.top + f[3] * page.height,
  };
}

export function usePdfMarks({
  paperId,
  readerRef,
  onReadingHeading,
}: {
  /** Null turns marks off (the standalone reader). */
  paperId: number | null;
  /** The reader's scroll container; the popover is placed in its box. */
  readerRef: RefObject<HTMLElement | null>;
  /** Follow: the section of the block at the top of the view. */
  onReadingHeading?: (heading: string | null, within: string[]) => void;
}): { ctx: PdfMarksApi | null; popover: ReactNode } {
  const { t } = useTranslation("notes");
  const allMarks = useMarksStore((s) => (paperId === null ? NO_MARKS : (s.marksByPaper[paperId] ?? NO_MARKS)));
  const colors = useMarksStore((s) => s.colors);
  const visible = useMarksStore((s) => s.showMarks);
  const boxOn = useMarksStore((s) => s.boxOn);
  const loadPaper = useMarksStore((s) => s.loadPaper);
  const loadColors = useMarksStore((s) => s.loadColors);
  const focusIds = useMarkFocusIds(paperId);

  useEffect(() => {
    if (paperId === null) return;
    void loadPaper(paperId).catch(() => undefined);
    void loadColors();
  }, [paperId, loadPaper, loadColors]);

  const marks = useMemo(() => {
    const hidden = new Set(colors.filter((c) => !c.visible).map((c) => c.color));
    return allMarks.filter(
      (m) => m.view === "pdf" && m.deleted_at === null && !m.lang && !(m.color !== null && hidden.has(m.color)),
    );
  }, [allMarks, colors]);

  // The Marker blocks of each page, loaded as pages come into view.
  const { blocks, blocksRef, loadBlocks } = usePdfBlocks(paperId);

  const [target, setTarget] = useState<Target | null>(null);
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  const measure = useCallback(() => {
    const root = readerRef.current;
    if (root) setBounds({ width: root.clientWidth, height: root.clientHeight });
    return root?.getBoundingClientRect() ?? null;
  }, [readerRef]);

  const pageEl = useCallback(
    (page: number) => readerRef.current?.querySelector(`${PAGE.slice(0, -1)}="${page + 1}"]`) ?? null,
    [readerRef],
  );

  // A selection on the text layer opens the popover under it.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  const boxOnRef = useRef(boxOn);
  useEffect(() => {
    boxOnRef.current = boxOn;
  }, [boxOn]);
  // Turning Box on drops a text selection's popover.
  const [seenBoxOn, setSeenBoxOn] = useState(boxOn);
  if (seenBoxOn !== boxOn) {
    setSeenBoxOn(boxOn);
    if (boxOn && target?.type === "text") setTarget(null);
  }
  useEffect(() => {
    const root = readerRef.current;
    if (paperId === null || !root) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let capped = "";
    const read = (keyboard: boolean) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (boxOnRef.current) return;
        const sel = window.getSelection();
        const range = sel && !sel.isCollapsed && sel.rangeCount ? sel.getRangeAt(0) : null;
        const fragments = range && root.contains(range.commonAncestorContainer) ? selectionFragments(range, root) : [];
        const pieces = piecesFromFragments(fragments, blocksRef.current);
        if (!range || !pieces.length) {
          setTarget((cur) => (cur?.type === "text" ? null : cur));
          return;
        }
        if (pieces.length > MAX_MARK_PARAGRAPHS) {
          setTarget(null);
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
        const origin = measure();
        if (!origin) return;
        setTarget({
          type: "text",
          range: range.cloneRange(),
          fragments,
          keyboard,
          box: boxOf(range.getBoundingClientRect(), origin),
        });
      }, 0);
    };
    const onMouseUp = () => read(false);
    const onDown = () => setTarget((cur) => (cur?.type === "text" ? null : cur));
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== "Escape") read(true);
    };
    root.addEventListener("mouseup", onMouseUp);
    root.addEventListener("mousedown", onDown);
    root.addEventListener("keyup", onKeyUp);
    return () => {
      clearTimeout(timer);
      root.removeEventListener("mouseup", onMouseUp);
      root.removeEventListener("mousedown", onDown);
      root.removeEventListener("keyup", onKeyUp);
    };
  }, [paperId, readerRef, measure, blocksRef]);

  // The popover follows what it marks while the reader scrolls; out of
  // view, it closes.
  useEffect(() => {
    const root = readerRef.current;
    if (!root || !target) return;
    const onScroll = () =>
      setTarget((cur) => {
        if (!cur) return cur;
        const origin = root.getBoundingClientRect();
        let client: { top: number; bottom: number; left: number; right: number } | null = null;
        if (cur.type === "text") client = cur.range.getBoundingClientRect();
        else {
          const el = pageEl(cur.page);
          if (el) client = fracToClient(cur.type === "box" ? cur.frac : cur.block.box, el.getBoundingClientRect());
        }
        if (!client) return null;
        const box = boxOf(client, origin);
        return box.bottom < 0 || box.top > origin.height ? null : { ...cur, box };
      });
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => root.removeEventListener("scroll", onScroll);
  }, [readerRef, target, pageEl]);

  const openObject = useCallback(
    (page: number, block: PaperPageBlock) => {
      const origin = measure();
      const el = pageEl(page);
      if (!origin || !el) return;
      window.getSelection()?.removeAllRanges();
      setTarget({
        type: "object",
        kind: FIGURE_TYPES.has(block.type) ? "figure" : "equation",
        page,
        block,
        box: boxOf(fracToClient(block.box, el.getBoundingClientRect()), origin),
      });
    },
    [measure, pageEl],
  );

  const openBox = useCallback(
    (page: number, frac: Frac, el: Element, scanned: boolean) => {
      const origin = measure();
      if (!origin) return;
      const spans = scanned ? [] : layerSpans(el);
      const next: Target = {
        type: "box",
        page,
        frac,
        scanned,
        result: null,
        box: boxOf(fracToClient(frac, el.getBoundingClientRect()), origin),
      };
      setTarget(next);
      void loadBlocks(page).then((list) => {
        const result = boxMark({ page, box: frac, blocks: list, spans, scanned });
        setTarget((cur) => (cur === next ? { ...next, result } : cur));
      });
    },
    [measure, loadBlocks],
  );

  // Follow: the section of the Marker block at the top of the view.
  usePdfFollow({ paperId, readerRef, blocks, blocksRef, onReadingHeading });

  const [hot, setHot] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(flashTimer.current), []);
  const marksRef = useRef(marks);
  useEffect(() => {
    marksRef.current = marks;
  }, [marks]);
  const openMark = useCallback(
    (markId: string) => {
      if (paperId === null) return;
      setFlashId(markId);
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlashId(null), FLASH_MS);
      // Until the note card: the mark's block opens in the notes pane.
      openMarkInNotes(paperId, marksRef.current.find((m) => m.id === markId));
    },
    [paperId],
  );

  const { busy, save: saveMarks } = useMarkSave(paperId);
  const save = useCallback(
    async (color: MarkColor | null, addNote: boolean) => {
      const cur = target;
      const root = readerRef.current;
      if (!cur || !root) return;
      let ok: boolean;
      if (cur.type === "text") {
        let missed = false;
        ok = await saveMarks({
          kind: color ? "highlight" : "quote",
          color,
          view: "pdf",
          addNote,
          groups: async () => {
            // The blocks of every page it is on, loaded by now.
            const pages = [...new Set(cur.fragments.map((f) => f.page))];
            const loaded = await Promise.all(pages.map((p) => loadBlocks(p)));
            const byPage = Object.fromEntries(pages.map((p, i) => [p, loaded[i]!]));
            const pieces = piecesFromFragments(cur.fragments, byPage);
            if (!pieces.length || paperId === null) return null;
            // A page with no Marker blocks (the LaTeX-compiled PDF of an
            // HTML paper): the section and chunks come from finding the
            // text in the paper's reading text.
            const placed = await Promise.all(
              pieces.map(async (piece) => {
                if (piece.block_id !== null || (byPage[piece.page]?.length ?? 0) > 0) return { piece };
                const at = (await locateText(paperId, piece.raw).catch(() => null))?.match ?? undefined;
                if (!at) missed = true;
                return { piece, at };
              }),
            );
            return groupPieces(placed);
          },
        });
        if (ok) window.getSelection()?.removeAllRanges();
        if (ok && missed) {
          toast.message(
            t("marks.notLocated", "Saved without a section: this passage wasn't found in the paper's text."),
          );
        }
      } else if (cur.type === "object") {
        const b = cur.block;
        const piece: MarkPiece =
          cur.kind === "figure"
            ? { quote: captionFor(blocksRef.current[cur.page] ?? NO_BLOCKS, b), page: cur.page, block_id: b.block_id, box: b.box }
            : { quote: b.text, latex: b.text, page: cur.page, block_id: b.block_id, box: b.box };
        ok = await saveMarks({
          kind: cur.kind,
          color,
          view: "pdf",
          addNote,
          groups: () => [{ section: b.section, pieces: [piece] }],
        });
      } else {
        // A box over most of an equation is that equation; else a region.
        const result =
          cur.result ??
          boxMark({
            page: cur.page,
            box: cur.frac,
            blocks: await loadBlocks(cur.page),
            spans: cur.scanned ? [] : layerSpans(pageEl(cur.page) ?? root),
            scanned: cur.scanned,
          });
        ok = await saveMarks({
          kind: result.kind,
          color,
          view: "pdf",
          addNote,
          groups: () => [{ section: result.section, pieces: [result.piece] }],
        });
      }
      if (ok) setTarget(null);
    },
    [target, readerRef, saveMarks, loadBlocks, pageEl, blocksRef, paperId, t],
  );

  const ctx = useMemo<PdfMarksApi | null>(
    () =>
      paperId === null
        ? null
        : {
            paperId,
            marks,
            colors,
            visible,
            focusIds,
            boxOn,
            blocks,
            loadBlocks,
            hot,
            setHot,
            flashId,
            openMark,
            openObject,
            openBox,
            pendingBox: target?.type === "box" ? { page: target.page, frac: target.frac } : null,
          },
    [paperId, marks, colors, visible, focusIds, boxOn, blocks, loadBlocks, hot, flashId, openMark, openObject, openBox, target],
  );

  const popoverTarget =
    target?.type === "object"
      ? target.kind
      : target?.type === "box"
        ? target.result?.kind === "equation"
          ? "equation"
          : "box"
        : "selection";
  const popover =
    paperId === null ? null : (
      <MarkPopover
        open={target !== null}
        anchor={target?.box ?? null}
        bounds={bounds}
        colors={colors}
        busy={busy}
        target={popoverTarget}
        autoFocus={target?.type === "text" ? target.keyboard : false}
        onHighlight={(c) => void save(c, false)}
        onQuote={() => void save(null, false)}
        onAddNote={() => void save(null, true)}
        onClose={(opts) => {
          setTarget(null);
          if (opts?.returnFocus) readerRef.current?.focus();
        }}
      />
    );

  return { ctx, popover };
}

