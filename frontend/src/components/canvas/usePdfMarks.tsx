/** A PDF reader's marks (Plan H5b.1): the paper's marks and Marker blocks,
 * the popover (a text selection, a figure or equation taken whole, a box),
 * saving, and Follow — the section of the Marker block at the top of the
 * view. Each react-pdf page draws its share with `PdfMarksPage`, which
 * reads this through `PdfMarksProvider`.
 *
 * A text mark keeps its line boxes as page fractions, taken from the
 * rendered text layer, and the Marker block that holds the selection's
 * centre (its `section` is where the mark files). The PDF is never changed.
 *
 * With the highlighter on, a drag on the text layer marks at once in the
 * pen colour (joining the pen session's mark in the same Marker block), a
 * click on a figure or an equation marks it whole, a box drawn on a scanned
 * page marks at once, and a click on a mark erases the piece under it.
 *
 * A selection in the translation card (`openCard`) marks the translated
 * words of the card's paragraph the same way (at once with the
 * highlighter); such marks show in the card while it shows their
 * paragraph in their language, and as a 文 marker on the paragraph's
 * Marker block otherwise.
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
import { useMarkSave, usePenSave, type MarkGroup } from "@/components/canvas/useMarkSave";
import { usePdfBlocks } from "@/components/canvas/usePdfBlocks";
import { usePdfFollow } from "@/components/canvas/usePdfFollow";
import {
  locateBox,
  objectPiece,
  storedPiece,
  usePdfPen,
  type Located,
  type PlaceText,
} from "@/components/canvas/usePdfPen";
import { locateText } from "@/lib/api";
import { MAX_MARK_PARAGRAPHS } from "@/lib/marks/limits";
import {
  boxMark,
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
import { translationPlace } from "@/lib/marks/pieces";
import type { TranslationPiece } from "@/lib/marks/translationMarks";
import { prefersReducedMotion } from "@/lib/motion";
import { useCanvasStore } from "@/store/canvas";
import { activateMark, hoverMark, registerMarkAnchors } from "@/store/markNav";
import { useMarkFocusIds, useMarksStore } from "@/store/marks";
import { REVEAL_WAIT_MS, useTranslationStore } from "@/store/translation";
import type { Mark, MarkColor, MarkColorInfo, PaperPageBlock } from "@/types/notes";

const NO_MARKS: Mark[] = [];
const NO_BLOCKS: PaperPageBlock[] = [];
const FLASH_MS = 1600;
/** How long the reader looks for a mark it was asked to show, once its page
 * has rendered, before it tries the HTML view (or says it can't). */
const NOT_HERE_MS = 1500;

/** Pieces as stored, grouped by section: a mark files under one. */
function groupPieces(pieces: readonly { piece: PdfPiece; at?: Located }[]): MarkGroup[] {
  const groups: MarkGroup[] = [];
  for (const { piece: p, at } of pieces) {
    const section = at ? at.section : p.section;
    const piece = storedPiece(p, at);
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
  | { type: "box"; page: number; frac: Frac; scanned: boolean; result: BoxResult | null; box: SelectionBox }
  | { type: "translation"; piece: TranslationPiece; keyboard: boolean; box: SelectionBox };

/** A selection in the translation card: its piece, where it is on screen,
 * and whether it was made from the keyboard. */
export interface CardSelection {
  piece: TranslationPiece;
  rect: { top: number; bottom: number; left: number; right: number };
  keyboard: boolean;
}

const NO_IDS: ReadonlySet<string> = new Set();

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
  /** The highlighter's colour while it is on (null: off). */
  pen: MarkColor | null;
  /** A click on a mark. */
  openMark: (markId: string) => void;
  /** A page drew its marks (again): a card pointing at one re-places. */
  marksMoved: () => void;
  /** A pen click on a mark: erases the piece under it. */
  erasePiece: (markId: string, piece: number) => void;
  /** A pen click on a figure or equation: marks it whole at once. */
  penObject: (page: number, block: PaperPageBlock) => void;
  openObject: (page: number, block: PaperPageBlock) => void;
  openBox: (page: number, box: Frac, pageEl: Element, scanned: boolean) => void;
  /** The box waiting for its popover choice (drawn on its page). */
  pendingBox: { page: number; frac: Frac } | null;
  /** Marks made on the translation card's text (shown in the card, or as
   * a 文 marker on their paragraph). */
  trMarks: Mark[];
  /** The marks the open card shows in their words (no 文 marker then). */
  cardShown: ReadonlySet<string>;
  setCardShown: (ids: ReadonlySet<string>) => void;
  /** A selection in the translation card: the popover (or, with the
   * highlighter, a mark at once). */
  openCard: (sel: CardSelection) => void;
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
  const penOn = useMarksStore((s) => s.penOn);
  const penColor = useMarksStore((s) => s.penColor);
  const pen = paperId !== null && penOn ? penColor : null;
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
      (m) =>
        // Marks made in the HTML view of the paper show on its PDF too
        // (passages only; the page finds them by their quote).
        (m.view === "pdf" || m.kind === "highlight" || m.kind === "quote") &&
        m.deleted_at === null &&
        !m.lang &&
        !(m.color !== null && hidden.has(m.color)),
    );
  }, [allMarks, colors]);

  // Marks on the translation card's text (PDF paragraphs).
  const trMarks = useMemo(() => {
    const hidden = new Set(colors.filter((c) => !c.visible).map((c) => c.color));
    return allMarks.filter(
      (m) => !!m.lang && m.view === "pdf" && m.deleted_at === null && !(m.color !== null && hidden.has(m.color)),
    );
  }, [allMarks, colors]);
  const [cardShownIn, setCardShownIn] = useState<ReadonlySet<string>>(NO_IDS);
  const setCardShown = useCallback(
    (ids: ReadonlySet<string>) =>
      setCardShownIn((cur) => (cur.size === ids.size && [...ids].every((i) => cur.has(i)) ? cur : ids)),
    [],
  );
  const cardShown = cardShownIn;

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
  const penRef = useRef(pen);
  useEffect(() => {
    boxOnRef.current = boxOn;
    penRef.current = pen;
  }, [boxOn, pen]);
  // Turning Box on drops a text selection's popover; turning the pen on
  // drops any (strokes need none).
  const [seenBoxOn, setSeenBoxOn] = useState(boxOn);
  if (seenBoxOn !== boxOn) {
    setSeenBoxOn(boxOn);
    if (boxOn && target?.type === "text") setTarget(null);
  }
  const [seenPen, setSeenPen] = useState(pen);
  if (seenPen !== pen) {
    setSeenPen(pen);
    if (pen && target) setTarget(null);
  }
  const strokeRef = useRef<(range: Range, fragments: PdfFragment[]) => void>(() => undefined);
  useEffect(() => {
    const root = readerRef.current;
    if (paperId === null || !root) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let capped = "";
    const read = (keyboard: boolean) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (boxOnRef.current) return;
        // The highlighter marks what the pointer drags; a keyboard
        // selection waits for the pen to go off.
        if (penRef.current && keyboard) return;
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
        if (penRef.current) {
          strokeRef.current(range.cloneRange(), fragments);
          return;
        }
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
        // The card stays where it is while the reader scrolls.
        if (!cur || cur.type === "translation") return cur;
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

  /** The blocks of these pages, or null when a page's could not be loaded
   * (said in a toast; the popover stays to try again). A failed load is
   * never taken for a page without blocks. */
  const loadPages = useCallback(
    async (pages: number[]): Promise<PaperPageBlock[][] | null> => {
      try {
        return await Promise.all(pages.map((p) => loadBlocks(p)));
      } catch {
        toast.error(t("marks.layoutFailed", "Couldn't load this page's layout, so the mark can't be filed yet. Try again."));
        return null;
      }
    },
    [loadBlocks, t],
  );
  /** A text selection's pieces, each placed: by its Marker block, or on a
   * page without blocks (the LaTeX-compiled PDF of an HTML paper) by
   * finding its text in the paper's reading text (`missed`: not found).
   * Null when a page's layout can't be loaded (said in a toast). */
  const placeText = useCallback<PlaceText>(
    async (fragments) => {
      const pages = [...new Set(fragments.map((f) => f.page))];
      const loaded = await loadPages(pages);
      if (!loaded) return null;
      const byPage = Object.fromEntries(pages.map((p, i) => [p, loaded[i]!]));
      const pieces = piecesFromFragments(fragments, byPage);
      if (!pieces.length || paperId === null) return null;
      let missed = false;
      const placed = await Promise.all(
        pieces.map(async (piece) => {
          if (piece.block_id !== null || (byPage[piece.page]?.length ?? 0) > 0) return { piece };
          const context = piece.before || piece.after ? { before: piece.before ?? "", after: piece.after ?? "" } : undefined;
          const at = (await (context ? locateText(paperId, piece.raw, context) : locateText(paperId, piece.raw)).catch(() => null))?.match ?? undefined;
          if (!at) missed = true;
          return { piece, at };
        }),
      );
      return { placed, missed };
    },
    [loadPages, paperId],
  );
  const notLocated = useCallback(
    () => toast.message(t("marks.notLocated", "Saved without a section: this passage wasn't found in the paper's text.")),
    [t],
  );
  const penTools = usePdfPen({ paperId, placeText, loadPages, blocksRef, notLocated });
  useEffect(() => {
    strokeRef.current = penTools.stroke;
  }, [penTools.stroke]);
  const openBox = useCallback(
    (page: number, frac: Frac, el: Element, scanned: boolean) => {
      const spans = scanned ? [] : layerSpans(el);
      if (penRef.current) {
        // The highlighter: the box marks at once in the pen colour.
        penTools.box(page, frac, spans, scanned);
        return;
      }
      const origin = measure();
      if (!origin) return;
      const next: Target = {
        type: "box",
        page,
        frac,
        scanned,
        result: null,
        box: boxOf(fracToClient(frac, el.getBoundingClientRect()), origin),
      };
      setTarget(next);
      void loadBlocks(page).then(
        (list) => {
          const result = boxMark({ page, box: frac, blocks: list, spans, scanned });
          setTarget((cur) => (cur === next ? { ...next, result } : cur));
        },
        () => undefined, // worked out (and the failure said) when it is saved
      );
    },
    [measure, loadBlocks, penTools],
  );

  /** A card piece's section: its paragraph's Marker block's (null when its
   * page's layout can't be had: said in a toast). */
  const cardSection = useCallback(
    async (piece: TranslationPiece): Promise<{ section: string | null } | null> => {
      const loaded = await loadPages([typeof piece.page === "number" ? piece.page : 0]);
      if (!loaded) return null;
      return { section: loaded[0]!.find((b) => b.block_id === piece.block_id)?.section ?? null };
    },
    [loadPages],
  );
  const penSave = usePenSave(paperId);
  const openCard = useCallback(
    (sel: CardSelection) => {
      const lang = sel.piece.lang;
      if (penRef.current) {
        // The highlighter: marked at once; it joins only marks on the same
        // translated paragraph.
        penSave({
          view: "pdf",
          pieces: async () => {
            const at = await cardSection(sel.piece);
            if (!at) return null;
            return [{ piece: sel.piece, section: at.section, place: translationPlace(lang, sel.piece.source_ref) }];
          },
          done: (ok) => {
            if (ok) window.getSelection()?.removeAllRanges();
          },
        });
        return;
      }
      // In the card, outside the reader: placed under it in the window.
      // Copied field by field: a DOMRect's sides are prototype getters, which
      // a spread drops.
      const { top, bottom, left, right } = sel.rect;
      setTarget({ type: "translation", piece: sel.piece, keyboard: sel.keyboard, box: { top, bottom, left, right } });
    },
    [penSave, cardSection],
  );

  // Follow: the section of the Marker block at the top of the view.
  usePdfFollow({ paperId, readerRef, blocks, blocksRef, onReadingHeading });

  const [hot, setHotIn] = useState<string | null>(null);
  // The notes pane tints the hovered mark's block.
  const setHot = useCallback((id: string | null) => {
    setHotIn(id);
    hoverMark(id);
  }, []);
  const [flashId, setFlashId] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(flashTimer.current), []);
  const openMark = useCallback(
    (markId: string) => {
      if (paperId === null) return;
      setFlashId(markId);
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlashId(null), FLASH_MS);
      // Its block in the visible notes pane, else its note card.
      activateMark(paperId, markId);
    },
    [paperId],
  );

  // Where each mark is on screen, for the note card's arrow.
  const movedListeners = useRef(new Set<() => void>());
  const marksMoved = useCallback(() => movedListeners.current.forEach((f) => f()), []);
  useEffect(() => {
    if (paperId === null) return;
    return registerMarkAnchors(paperId, {
      kind: "pdf",
      viewport: () => readerRef.current?.getBoundingClientRect() ?? null,
      find: (markId) => {
        const root = readerRef.current;
        if (!root) return null;
        const id = CSS.escape(markId);
        const els = root.querySelectorAll(
          `[data-pdf-marks] [data-mark="${id}"]:not([data-leaving]), [data-tr-marker="${id}"]:not([data-leaving])`,
        );
        return { rects: Array.from(els, (el) => el.getBoundingClientRect()), viewport: root.getBoundingClientRect() };
      },
      subscribe: (onMove) => {
        // Scrolling, the reader's box resizing, and a page drawing its marks
        // again (a re-render on zoom or width change).
        const root = readerRef.current;
        root?.addEventListener("scroll", onMove, { passive: true });
        const ro = root && typeof ResizeObserver !== "undefined" ? new ResizeObserver(onMove) : null;
        if (root) ro?.observe(root);
        movedListeners.current.add(onMove);
        return () => {
          root?.removeEventListener("scroll", onMove);
          ro?.disconnect();
          movedListeners.current.delete(onMove);
        };
      },
    });
  }, [paperId, readerRef]);

  // Asked to show a mark (a source chip, My marks, a jumper): scroll to it
  // and flash it. A mark on a translation is the translation layer's; one
  // this view doesn't draw (made in the HTML, its text not found here) is
  // tried in the HTML view.
  const reveal = useTranslationStore((s) => s.reveal);
  const onScreen = useCanvasStore(
    (s) => s.readerView === null || (s.readerView.paperId === paperId && s.readerView.kind === "pdf"),
  );
  useEffect(() => {
    if (!reveal || paperId === null || reveal.paperId !== paperId || !onScreen) return;
    const known = useMarksStore.getState().marksByPaper[paperId]?.find((m) => m.id === reveal.markId);
    if (known?.lang) return;
    useTranslationStore.getState().claimReveal(reveal.n);
    const started = Date.now();
    // When the page that should draw it has rendered (its text layer and
    // marks layer are there): only then does "not drawn" mean anything.
    let renderedAt: number | null = null;
    let toPage = false;
    const done = (): true => {
      useTranslationStore.getState().revealed(reveal.n);
      return true;
    };
    const tick = (): boolean => {
      const root = readerRef.current;
      const mark = useMarksStore.getState().marksByPaper[paperId]?.find((m) => m.id === reveal.markId);
      const el = root?.querySelector<HTMLElement>(
        `[data-pdf-marks] [data-mark="${CSS.escape(reveal.markId)}"]:not([data-leaving])`,
      );
      if (el) {
        if (typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
        }
        setFlashId(reveal.markId);
        clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setFlashId(null), FLASH_MS);
        return done();
      }
      // Its page first: the page draws it once its text has laid out.
      const page = mark?.pieces.find((p) => typeof p.page === "number" && !p.erased && !p.removed)?.page;
      if (!toPage && mark?.view === "pdf" && typeof page === "number") {
        const pageNode = pageEl(page);
        if (pageNode && typeof pageNode.scrollIntoView === "function") {
          pageNode.scrollIntoView({ block: "start", behavior: "auto" });
          toPage = true;
        }
      }
      const host = mark?.view === "pdf" && typeof page === "number" ? pageEl(page) : root;
      if (renderedAt === null && mark && host?.querySelector("[data-pdf-marks]") && host.querySelector(".textLayer")) {
        renderedAt = Date.now();
      }
      if (renderedAt === null) {
        // Still rendering (or its marks still loading): within reason.
        if (Date.now() - started < REVEAL_WAIT_MS) return false;
        if (mark) toast.message(tRef.current("marks.notFound", "This mark isn't drawn in the paper here."));
        return done();
      }
      if (Date.now() - renderedAt < NOT_HERE_MS) return false;
      // Drawn here it isn't. A passage may be found in the HTML; a box,
      // figure or equation made on a page is drawn on the page only.
      if (mark && reveal.fallback && (mark.kind === "highlight" || mark.kind === "quote")) {
        useCanvasStore.getState().requestReaderTab(paperId, "html");
        useTranslationStore.getState().requestReveal(paperId, reveal.markId);
        return done();
      }
      if (mark) toast.message(tRef.current("marks.notFound", "This mark isn't drawn in the paper here."));
      return done();
    };
    if (tick()) return;
    const timer = setInterval(() => {
      if (tick()) clearInterval(timer);
    }, 150);
    return () => clearInterval(timer);
  }, [reveal, paperId, onScreen, readerRef, pageEl]);

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
            const r = await placeText(cur.fragments);
            if (!r) return null;
            missed = r.missed;
            return groupPieces(r.placed);
          },
        });
        if (ok) window.getSelection()?.removeAllRanges();
        if (ok && missed) notLocated();
      } else if (cur.type === "translation") {
        const piece = cur.piece;
        ok = await saveMarks({
          kind: color ? "highlight" : "quote",
          color,
          view: "pdf",
          addNote,
          groups: async () => {
            const at = await cardSection(piece);
            if (!at) return null;
            return [{ section: at.section, pieces: [piece], lang: piece.lang, source_ref: piece.source_ref }];
          },
        });
        if (ok) window.getSelection()?.removeAllRanges();
      } else if (cur.type === "object") {
        const b = cur.block;
        const piece = objectPiece(cur.kind, cur.page, b, blocksRef.current[cur.page] ?? NO_BLOCKS);
        ok = await saveMarks({
          kind: cur.kind,
          color,
          view: "pdf",
          addNote,
          groups: () => [{ section: b.section, pieces: [piece] }],
        });
      } else {
        // A box over most of an equation is that equation; else a region.
        // A result already in hand needs no fetch: a failed one must not
        // stop the colour click (the blocks cached with it are used).
        const blocks = cur.result
          ? [blocksRef.current[cur.page] ?? NO_BLOCKS]
          : await loadPages([cur.page]);
        if (!blocks || paperId === null) return;
        const pageBlocks = blocks[0]!;
        const base =
          cur.result ??
          boxMark({
            page: cur.page,
            box: cur.frac,
            blocks: pageBlocks,
            spans: cur.scanned ? [] : layerSpans(pageEl(cur.page) ?? root),
            scanned: cur.scanned,
          });
        let missed = false;
        ok = await saveMarks({
          kind: base.kind,
          openCard: false, // Box is a rapid tool
          color,
          view: "pdf",
          addNote,
          groups: async () => {
            // On a page without Marker blocks the box's words find its section.
            const placed = await locateBox(paperId, base, pageBlocks);
            missed = placed.missed;
            return [{ section: placed.result.section, pieces: [placed.result.piece] }];
          },
        });
        if (ok && missed) notLocated();
      }
      if (ok) setTarget(null);
    },
    [target, readerRef, saveMarks, loadPages, pageEl, blocksRef, placeText, notLocated, cardSection, paperId],
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
            pen,
            openMark,
            marksMoved,
            erasePiece: penTools.erase,
            penObject: penTools.object,
            openObject,
            openBox,
            pendingBox: target?.type === "box" ? { page: target.page, frac: target.frac } : null,
            trMarks,
            cardShown,
            setCardShown,
            openCard,
          },
    [
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
      pen,
      openMark,
      marksMoved,
      penTools.erase,
      penTools.object,
      openObject,
      openBox,
      target,
      trMarks,
      cardShown,
      setCardShown,
      openCard,
    ],
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
        bounds={target?.type === "translation" ? { width: window.innerWidth, height: window.innerHeight } : bounds}
        fixed={target?.type === "translation"}
        colors={colors}
        busy={busy}
        target={popoverTarget}
        autoFocus={target?.type === "text" || target?.type === "translation" ? target.keyboard : false}
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

