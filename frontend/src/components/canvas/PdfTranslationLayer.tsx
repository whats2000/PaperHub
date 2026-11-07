import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import {
  TranslationCard,
  type CardScroll,
  type TranslationCardStatus,
} from "@/components/canvas/TranslationCard";
import { usePresence } from "@/hooks/usePresence";
import { getPdfParagraphs } from "@/lib/api";
import { bboxToRect, markerPageToPageNumber } from "@/lib/bboxHighlight";
import { describeBreak, placeCard, type CardPlacement } from "@/lib/cardPlacement";
import { mathToKatexHtml } from "@/lib/pdfTranslation";
import { createTranslationQueue, type TranslationQueue } from "@/lib/translationQueue";
import { cn } from "@/lib/utils";
import { useTranslationStore } from "@/store/translation";
import type {
  ParagraphsResponse,
  PdfParagraph,
  TranslateResult,
} from "@/types/translation";

/** Grace period for the pointer to travel from a paragraph into the card. */
const HOVER_HIDE_MS = 250;
/** A press that travels this far is a drag (text selection), not a pin. */
const CLICK_SLOP_PX = 4;
/** Space between the card and the reader / the window's left edge. */
const CARD_GAP = 12;
/** How long the one-off first-use hint stays up. */
const HINT_MS = 2800;
const HINT_EXIT_MS = 200;
/** The hint's text goes into its live region this long after it mounts. */
const HINT_ANNOUNCE_MS = 50;
/** A move to another paragraph / fragment / side glides this long. */
const GLIDE_MS = 200;

const BREAK = /⟦BR⟧/g;

type PlacedCard = CardPlacement & { glide: boolean };

interface PageFragment {
  key: string;
  index: number;
  bbox: readonly number[];
}

type FragmentState = "hover" | "pinned" | undefined;

/** Stable for the layer's lifetime, so page observers/listeners never re-bind on hover. */
interface LayerActions {
  setPageVisible: (pageNumber: number, visible: boolean) => void;
  /** The pointer moved over a page (shows the first-use hint once). */
  onPagePointer: () => void;
  onEnter: (key: string) => void;
  onLeave: () => void;
  onClick: (key: string) => void;
  /** A page re-laid out its rects (zoom / width change): re-place the card. */
  relayout: () => void;
}

/** Per-hover state. */
interface LayerState {
  enabled: boolean;
  fragmentsByPage: Map<number, PageFragment[]>;
  hoverKey: string | null;
  pinnedKey: string | null;
}

const ActionsCtx = createContext<LayerActions | null>(null);
const StateCtx = createContext<LayerState | null>(null);

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

interface Props {
  paperId: number;
  language: string;
  fullPaper: boolean;
  enabled: boolean;
  /** The PDF reading area (scroll container): the card sits to its left. */
  readerRef: RefObject<HTMLElement | null>;
  /** Paragraphs already fetched by the caller; omitted → fetched here. */
  paragraphs?: PdfParagraph[];
  /** Bumped to re-translate the in-view stale paragraphs with current terminology. */
  retranslateNonce?: number;
  /** Whether an in-view translation predates the current terminology memory. */
  onStaleChange?: (stale: boolean) => void;
  /** This layer's own paragraphs fetch finished: the response, or null on failure. */
  onParagraphsLoaded?: (res: ParagraphsResponse | null) => void;
  children: ReactNode;
}

/**
 * Owns PDF translation for one paper: fetches the paragraph geometry, runs the
 * translation queue, tracks hover/pin, and renders the `TranslationCard`.
 * `PdfTranslationPage` (one per react-pdf page) draws the paragraph highlight
 * and hit-tests the pointer on its page.
 *
 * Interaction is pointer-first by decision: there is no keyboard path to a
 * paragraph's translation yet (a page has dozens of paragraphs; a tab stop per
 * paragraph would swamp keyboard navigation of the reader). Once a card is
 * open, its pin/close buttons and Esc are keyboard reachable.
 */
export function PdfTranslationLayer({
  paperId,
  language,
  fullPaper,
  enabled,
  readerRef,
  paragraphs: givenParagraphs,
  retranslateNonce = 0,
  onStaleChange,
  onParagraphsLoaded,
  children,
}: Props) {
  const { t } = useTranslation("canvas");
  // Fetched paragraphs per paper, kept across translation toggles.
  const [cache, setCache] = useState<Record<number, PdfParagraph[]>>({});
  const [results, setResults] = useState<Record<string, TranslateResult>>({});
  const [hover, setHover] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [placement, setPlacement] = useState<PlacedCard | null>(null);
  // The active paragraph has no fragment in view: the card steps aside.
  const [outOfView, setOutOfView] = useState(false);
  const [visiblePages, setVisiblePages] = useState<ReadonlySet<number>>(() => new Set());
  // Last paragraph shown, so the card keeps its content through the exit.
  const [shownKey, setShownKey] = useState<string | null>(null);
  const [hint, setHint] = useState<{ right: number; bottom: number } | null>(null);
  const [hintOpen, setHintOpen] = useState(false);

  const queueRef = useRef<TranslationQueue | null>(null);
  const wantedRef = useRef<string[]>([]);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  // Handlers read the pin through a ref so they stay stable across hovers.
  const pinnedKeyRef = useRef<string | null>(null);
  const activeKeyRef = useRef<string | null>(null);
  // The paragraph under the pointer, tracked even while a card is pinned.
  const pointerKeyRef = useRef<string | null>(null);
  const overCardRef = useRef(false);
  const hasParagraphsRef = useRef(false);
  // Last (paragraph, fragment, side) placed, and until when moves glide.
  const lastAnchorRef = useRef<string | null>(null);
  const glideUntilRef = useRef(0);

  const cached = givenParagraphs ?? cache[paperId];
  const isCached = cached !== undefined;
  const paragraphs = useMemo(() => (enabled && cached ? cached : []), [enabled, cached]);
  const byKey = useMemo(() => new Map(paragraphs.map((p) => [p.key, p])), [paragraphs]);
  useLayoutEffect(() => {
    hasParagraphsRef.current = paragraphs.some((p) => p.fragments.length > 0);
  }, [paragraphs]);

  const onLoadedRef = useRef(onParagraphsLoaded);
  useEffect(() => {
    onLoadedRef.current = onParagraphsLoaded;
  }, [onParagraphsLoaded]);

  useEffect(() => {
    if (!enabled || isCached) return;
    let cancelled = false;
    getPdfParagraphs(paperId).then(
      (res) => {
        if (cancelled) return;
        // Not "ready" → no paragraph layout or unreadable formulas: no rects.
        setCache((prev) => ({
          ...prev,
          [paperId]: res.status === "ready" ? res.paragraphs : [],
        }));
        onLoadedRef.current?.(res);
      },
      // A failed fetch isn't cached, so re-enabling translation retries it;
      // the caller is told so the pill can say why.
      () => {
        if (!cancelled) onLoadedRef.current?.(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [enabled, paperId, isCached]);

  // One queue per (paper, language, paragraph set).
  useEffect(() => {
    if (!paragraphs.length) return;
    const q = createTranslationQueue({ paperId, language, fullPaper });
    queueRef.current = q;
    const off = q.onResult((r) => setResults((prev) => ({ ...prev, [r.key]: r })));
    q.setBlocks(
      paragraphs.map((p) => p.key),
      Object.fromEntries(paragraphs.map((p) => [p.key, p.text])),
    );
    q.setVisible(wantedRef.current);
    return () => {
      off();
      q.dispose();
      if (queueRef.current === q) queueRef.current = null;
      setResults({});
    };
  }, [paragraphs, paperId, language, fullPaper]);

  // Turning translation off drops hover/pin (derive-during-render).
  const [wasEnabled, setWasEnabled] = useState(enabled);
  if (wasEnabled !== enabled) {
    setWasEnabled(enabled);
    if (!enabled) {
      setHover(null);
      setPinned(null);
    }
  }

  const activeKey = pinned ?? hover;
  useLayoutEffect(() => {
    pinnedKeyRef.current = pinned;
    activeKeyRef.current = activeKey;
  }, [pinned, activeKey]);

  if (activeKey !== null && activeKey !== shownKey) setShownKey(activeKey);

  // The active paragraph goes first (top priority); otherwise the visible pages.
  const visibleKeysSig = useMemo(() => {
    if (activeKey) return activeKey;
    return paragraphs
      .filter((p) => p.fragments.some((f) => visiblePages.has(markerPageToPageNumber(f.page))))
      .map((p) => p.key)
      .join("\n");
  }, [activeKey, paragraphs, visiblePages]);
  useEffect(() => {
    wantedRef.current = visibleKeysSig ? visibleKeysSig.split("\n") : [];
    queueRef.current?.setVisible(wantedRef.current);
  }, [visibleKeysSig]);

  // Stale terminology: an in-view (or the active) paragraph's translation.
  const staleKeys = useMemo(
    () =>
      paragraphs
        .filter(
          (p) =>
            results[p.key]?.stale_terminology === true &&
            (p.key === activeKey ||
              p.fragments.some((f) => visiblePages.has(markerPageToPageNumber(f.page)))),
        )
        .map((p) => p.key),
    [paragraphs, results, activeKey, visiblePages],
  );
  const staleKeysRef = useRef(staleKeys);
  const onStaleRef = useRef(onStaleChange);
  useLayoutEffect(() => {
    staleKeysRef.current = staleKeys;
    onStaleRef.current = onStaleChange;
  }, [staleKeys, onStaleChange]);
  const anyStale = staleKeys.length > 0;
  useEffect(() => {
    onStaleRef.current?.(anyStale);
  }, [anyStale]);
  // A bump (never the initial value) re-sends them with `force`; the card
  // keeps its text until the new (or, on failure, the old) text comes back.
  const seenRetranslate = useRef(retranslateNonce);
  useEffect(() => {
    if (seenRetranslate.current === retranslateNonce) return;
    seenRetranslate.current = retranslateNonce;
    if (staleKeysRef.current.length) queueRef.current?.retranslate(staleKeysRef.current);
  }, [retranslateNonce]);

  const clearHideTimer = useCallback(() => {
    if (hideTimer.current !== null) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  }, []);
  useEffect(() => clearHideTimer, [clearHideTimer]);
  useEffect(
    () => () => {
      if (hintTimer.current !== null) clearTimeout(hintTimer.current);
    },
    [],
  );

  /**
   * Place the card for paragraph `key` from its fragments' on-screen rects.
   * A change of paragraph, fragment or side glides; continuous scroll
   * tracking (`tracking`) never does.
   */
  const place = useCallback(
    (key: string, tracking = false) => {
      const reader = readerRef.current;
      if (!reader) return;
      const frags = Array.from(
        reader.querySelectorAll<HTMLElement>(`[data-paragraph-key="${CSS.escape(key)}"]`),
      )
        .map((el) => ({
          index: Number(el.dataset.fragmentIndex),
          rect: el.getBoundingClientRect(),
        }))
        .sort((a, b) => a.index - b.index);
      const box = reader.getBoundingClientRect();
      const next = frags.length
        ? placeCard({
            paraRects: frags.map((f) => f.rect),
            viewport: { top: box.top, bottom: box.bottom },
            gutter: { left: CARD_GAP, right: box.left - CARD_GAP },
            cardHeight: cardRef.current?.offsetHeight ?? 0,
            reader: { left: box.left, right: box.right },
          })
        : null;
      if (!next) {
        setOutOfView(true);
        return;
      }
      setOutOfView(false);
      // Back from `paraRects` order to the paragraph's fragment index.
      const fragment = frags[next.activeFragment]!.index;
      const anchor = `${key}
${fragment}
${next.side}`;
      const now = performance.now();
      if (anchor !== lastAnchorRef.current) {
        lastAnchorRef.current = anchor;
        glideUntilRef.current = now + GLIDE_MS;
      }
      setPlacement({
        ...next,
        activeFragment: fragment,
        glide: !tracking && now < glideUntilRef.current,
      });
    },
    [readerRef],
  );
  const reflow = useCallback(() => {
    const key = activeKeyRef.current;
    if (key !== null) place(key);
  }, [place]);

  const close = useCallback(() => {
    clearHideTimer();
    pinnedKeyRef.current = null;
    setPinned(null);
    setHover(null);
  }, [clearHideTimer]);

  const scheduleHide = useCallback(() => {
    clearHideTimer();
    hideTimer.current = setTimeout(() => {
      hideTimer.current = null;
      setHover(null);
    }, HOVER_HIDE_MS);
  }, [clearHideTimer]);

  const showHint = useCallback(() => {
    const store = useTranslationStore.getState();
    if (store.hintSeen || !hasParagraphsRef.current) return;
    store.markHintSeen();
    const box = readerRef.current?.getBoundingClientRect();
    setHint({
      right: box ? Math.max(0, window.innerWidth - box.right) + 16 : 16,
      bottom: box ? Math.max(0, window.innerHeight - box.bottom) + 56 : 56,
    });
    setHintOpen(true);
    hintTimer.current = setTimeout(() => {
      hintTimer.current = null;
      setHintOpen(false);
    }, HINT_MS);
  }, [readerRef]);

  const actions = useMemo<LayerActions>(
    () => ({
      setPageVisible: (pageNumber, visible) =>
        setVisiblePages((prev) => {
          if (prev.has(pageNumber) === visible) return prev;
          const next = new Set(prev);
          if (visible) next.add(pageNumber);
          else next.delete(pageNumber);
          return next;
        }),
      onPagePointer: showHint,
      onEnter: (key) => {
        pointerKeyRef.current = key;
        clearHideTimer();
        if (pinnedKeyRef.current !== null) return; // a pinned card ignores hover
        setHover(key);
        place(key);
      },
      onLeave: () => {
        pointerKeyRef.current = null;
        if (pinnedKeyRef.current === null) scheduleHide();
      },
      onClick: (key) => {
        clearHideTimer();
        if (pinnedKeyRef.current === key) {
          close();
          return;
        }
        pinnedKeyRef.current = key;
        setPinned(key);
        setHover(key);
        place(key);
      },
      relayout: reflow,
    }),
    [clearHideTimer, scheduleHide, close, place, reflow, showHint],
  );

  /** Unpin, then follow the pointer: its paragraph, the card, or nothing. */
  const unpin = useCallback(() => {
    pinnedKeyRef.current = null;
    setPinned(null);
    const under = pointerKeyRef.current;
    if (under !== null) {
      clearHideTimer();
      setHover(under);
      place(under);
    } else if (!overCardRef.current) {
      scheduleHide();
    } // over the card: leaving it schedules the hide
  }, [clearHideTimer, place, scheduleHide]);

  const fragmentsByPage = useMemo(() => {
    const byPage = new Map<number, PageFragment[]>();
    paragraphs.forEach((p) =>
      p.fragments.forEach((f, index) => {
        const page = markerPageToPageNumber(f.page);
        const list = byPage.get(page) ?? [];
        list.push({ key: p.key, index, bbox: f.bbox });
        byPage.set(page, list);
      }),
    );
    return byPage;
  }, [paragraphs]);
  const state = useMemo<LayerState>(
    () => ({ enabled, fragmentsByPage, hoverKey: hover, pinnedKey: pinned }),
    [enabled, fragmentsByPage, hover, pinned],
  );

  const open = enabled && activeKey !== null && byKey.has(activeKey);

  // Esc unpins and closes; the card follows its paragraph on scroll/resize.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Esc in a text field belongs to that field unless a card is pinned.
      if (isEditable(e.target) && pinnedKeyRef.current === null) return;
      close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);
  useEffect(() => {
    if (!open) return;
    const reader = readerRef.current;
    // At most one re-placement per frame while scrolling / resizing.
    let frame = 0;
    const track = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const key = activeKeyRef.current;
        if (key !== null) place(key, true);
      });
    };
    reader?.addEventListener("scroll", track, { passive: true });
    window.addEventListener("resize", track);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      reader?.removeEventListener("scroll", track);
      window.removeEventListener("resize", track);
    };
  }, [open, place, readerRef]);

  const shown = shownKey !== null ? byKey.get(shownKey) : undefined;
  const result = shown ? results[shown.key] : undefined;
  const status: TranslationCardStatus =
    result?.status === "ok" && result.text !== null
      ? "ok"
      : result?.status === "unavailable"
        ? "unavailable"
        : "loading";
  // The dividers line up with the fragments only when there is one `⟦BR⟧`
  // per fragment boundary (a zero-size fragment may have been dropped).
  const fragmentCount = shown?.fragments.length ?? 0;
  const spansMatch =
    status === "ok" && fragmentCount === (result?.text?.match(BREAK)?.length ?? 0) + 1;
  const breakLabels = useMemo(
    () =>
      spansMatch && shown
        ? shown.fragments.slice(1).map((next, i) => {
            const b = describeBreak(shown.fragments[i]!, next);
            if (b.kind === "column") {
              return t("translationCard.continuedColumn", "continued in the right column");
            }
            if (b.kind === "page") {
              return t("translationCard.continuedPage", "continued on page {{page}}", {
                page: b.page,
              });
            }
            return undefined; // a same-column bridge: a plain divider
          })
        : [],
    [spansMatch, shown, t],
  );
  const scroll = useMemo<CardScroll | null>(() => {
    if (!placement) return null;
    if (spansMatch) {
      return {
        fraction: placement.scrollFraction,
        activeFragment: placement.activeFragment,
        perFragment: true,
      };
    }
    // Whole-content progress: fragments counted as equal shares.
    return {
      fraction:
        (placement.activeFragment + placement.scrollFraction) / Math.max(1, fragmentCount),
      activeFragment: 0,
      perFragment: false,
    };
  }, [placement, spansMatch, fragmentCount]);
  const html =
    status === "ok" && shown && result?.text
      ? mathToKatexHtml(result.text, shown.math, breakLabels)
      : null;

  const hintPresence = usePresence(hintOpen, HINT_EXIT_MS);

  return (
    <ActionsCtx.Provider value={actions}>
      <StateCtx.Provider value={state}>{children}</StateCtx.Provider>
      <TranslationCard
        open={open && !outOfView}
        contentKey={shown?.key ?? null}
        language={language}
        status={status}
        html={html}
        pinned={pinned !== null}
        placement={placement}
        scroll={scroll}
        glide={placement?.glide ?? false}
        cardRef={cardRef}
        onResize={reflow}
        onTogglePin={() => {
          if (pinned !== null) unpin();
          else if (hover !== null) {
            pinnedKeyRef.current = hover;
            setPinned(hover);
          }
        }}
        onClose={close}
        onRetry={() => {
          if (!shown) return;
          setResults((prev) => {
            const next = { ...prev };
            delete next[shown.key];
            return next;
          });
          queueRef.current?.retry([shown.key]);
        }}
        onMouseEnter={() => {
          overCardRef.current = true;
          clearHideTimer();
        }}
        onMouseLeave={() => {
          overCardRef.current = false;
          if (pinned === null) scheduleHide();
        }}
      />
      {hintPresence.mounted &&
        hint &&
        createPortal(
          <div
            role="status"
            data-state={hintPresence.state}
            style={{ right: hint.right, bottom: hint.bottom }}
            className={cn(
              "pointer-events-none fixed z-50 max-w-xs rounded-md bg-foreground px-3 py-1.5 text-xs text-background",
              "duration-200 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-bottom-1",
              "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-bottom-1",
              "motion-reduce:animate-none",
            )}
          >
            <HintText
              text={t("translationCard.firstUseHint", "Hover a paragraph to see its translation")}
            />
          </div>,
          document.body,
        )}
    </ActionsCtx.Provider>
  );
}

/**
 * The hint's words, inserted shortly after its live region mounts: a region
 * that arrives already filled is often not announced by screen readers.
 */
function HintText({ text }: { text: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setShown(true), HINT_ANNOUNCE_MS);
    return () => clearTimeout(id);
  }, []);
  return shown ? <>{text}</> : null;
}

interface PageProps {
  /** 1-based react-pdf page number. */
  pageNumber: number;
  /** CSS width the page is rendered at. */
  renderedWidth: number;
  /** The page's native width in PDF points. */
  originalWidth: number;
}

interface HitRect {
  key: string;
  id: string;
  index: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * One page's paragraph highlight. The rects are purely visual
 * (`pointer-events-none`) so react-pdf's text layer keeps its text cursor,
 * selection and copy. Hover and click are hit-tested instead: listeners on the
 * page element (the overlay's parent; events from text-layer spans bubble up
 * to it) compare the page-relative pointer position with the scaled fragment
 * rects. A press that drags or leaves a selection never pins, and neither does
 * a click on a link. Only translatable blocks have fragments, so figures are
 * never covered.
 */
export function PdfTranslationPage({ pageNumber, renderedWidth, originalWidth }: PageProps) {
  const actions = useContext(ActionsCtx);
  const state = useContext(StateCtx);
  const rootRef = useRef<HTMLDivElement>(null);
  const enabled = state?.enabled ?? false;
  const pageFragments = state?.fragmentsByPage.get(pageNumber);

  const rects = useMemo<HitRect[]>(() => {
    if (!pageFragments || renderedWidth <= 0 || originalWidth <= 0) return [];
    return pageFragments.map((f) => ({
      key: f.key,
      id: `${f.key}#${f.index}`,
      index: f.index,
      ...bboxToRect(f.bbox, originalWidth, renderedWidth),
    }));
  }, [pageFragments, renderedWidth, originalWidth]);
  // Layout effects: the hit-test data and listeners are in place by the time
  // the rects are on screen, so a first pointer event is never missed.
  const rectsRef = useRef<HitRect[]>([]);
  useLayoutEffect(() => {
    rectsRef.current = rects;
  }, [rects]);
  // A zoom / width change moves the rects without a scroll: re-place the card.
  useLayoutEffect(() => {
    if (rects.length) actions?.relayout();
  }, [rects, actions]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !enabled || !actions || typeof IntersectionObserver === "undefined") {
      return;
    }
    const io = new IntersectionObserver((entries) =>
      entries.forEach((e) => actions.setPageVisible(pageNumber, e.isIntersecting)),
    );
    io.observe(el);
    return () => {
      io.disconnect();
      actions.setPageVisible(pageNumber, false);
    };
  }, [pageNumber, actions, enabled]);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const page = root?.parentElement;
    if (!root || !page || !enabled || !actions) return;
    let hovered: string | null = null;
    let down: { x: number; y: number } | null = null;

    const hit = (e: MouseEvent): string | null => {
      const box = page.getBoundingClientRect();
      const x = e.clientX - box.left;
      const y = e.clientY - box.top;
      const r = rectsRef.current.find(
        (c) => x >= c.left && x <= c.left + c.width && y >= c.top && y <= c.top + c.height,
      );
      return r?.key ?? null;
    };
    const onMove = (e: PointerEvent) => {
      actions.onPagePointer();
      const key = hit(e);
      if (key === hovered) return;
      hovered = key;
      if (key !== null) actions.onEnter(key);
      else actions.onLeave();
    };
    const onLeave = () => {
      if (hovered === null) return;
      hovered = null;
      actions.onLeave();
    };
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const onClick = (e: MouseEvent) => {
      const start = down;
      down = null;
      // A link (e.g. a react-pdf annotation) does its own thing.
      if (e.target instanceof Element && e.target.closest("a")) return;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) >= CLICK_SLOP_PX) {
        return; // a drag: the user is selecting text
      }
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      const key = hit(e);
      if (key !== null) actions.onClick(key);
    };
    page.addEventListener("pointermove", onMove);
    page.addEventListener("pointerleave", onLeave);
    page.addEventListener("pointerdown", onDown);
    page.addEventListener("click", onClick);
    return () => {
      page.removeEventListener("pointermove", onMove);
      page.removeEventListener("pointerleave", onLeave);
      page.removeEventListener("pointerdown", onDown);
      page.removeEventListener("click", onClick);
    };
  }, [actions, enabled]);

  if (!state?.enabled) return null;
  // The root always renders so the page's visibility is tracked even before
  // the paragraphs arrive; rects only once the page has a real size.
  return (
    <div ref={rootRef} className="pointer-events-none absolute inset-0 z-[5]">
      {rects.map((r) => {
        const fragState: FragmentState =
          state.pinnedKey !== null
            ? r.key === state.pinnedKey
              ? "pinned"
              : undefined
            : r.key === state.hoverKey
              ? "hover"
              : undefined;
        return (
          <div
            key={r.id}
            data-paragraph-key={r.key}
            data-fragment-id={r.id}
            data-fragment-index={r.index}
            data-state={fragState}
            className={cn(
              "absolute rounded-[2px] transition-colors duration-150 motion-reduce:transition-none",
              fragState === "hover" && "bg-black/[9%]",
              fragState === "pinned" && "bg-black/[16%]",
            )}
            style={{ left: r.left, top: r.top, width: r.width, height: r.height }}
          />
        );
      })}
    </div>
  );
}
