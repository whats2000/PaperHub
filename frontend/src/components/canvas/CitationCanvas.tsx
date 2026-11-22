import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { resetHorizontalScroll } from "@/lib/scrollWithin";
import { X } from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import {
  getChunk,
  getDocumentInfo,
  getFigureMeta,
  figureImageUrl,
  getViewRegion,
  fetchPagePdfData,
  getPdfParagraphs,
  getTranslateConfig,
  fetchPaperHtml,
  fetchPaperPdfData,
  getPaperInfo,
  API_BASE_URL,
} from "@/lib/api";
import { useSettingsStore } from "@/store/settings";
import { useTranslationStore } from "@/store/translation";
import type { TranslationQueueStatus } from "@/lib/translationQueue";
import type { ParagraphsResponse, PdfParagraph } from "@/types/translation";
import {
  withBaseHref,
  stripDeadCdnScripts,
  injectPerfStyle,
  localizeMathjax,
} from "@/lib/withBaseHref";
import { resolveNeedle } from "@/lib/resolveNeedle";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { addPaperToChat, neighbourOf, roveTabs } from "@/lib/paperTabs";
import { PaperTabs } from "@/components/canvas/PaperTabs";
import type { ClosedTab, OutsideTab } from "@/store/canvas";
import { PaperLoading } from "@/components/canvas/PaperLoading";
import { BoxToolButton } from "@/components/canvas/BoxTool";
import { HighlighterControl } from "@/components/canvas/HighlighterControl";
import { ShowMarksToggle } from "@/components/canvas/ShowMarksToggle";
import { MyMarksMenu } from "@/components/canvas/MyMarksMenu";
import { NoteCard } from "@/components/canvas/NoteCard";
import { HtmlView } from "@/components/canvas/HtmlView";
import { PdfView } from "@/components/canvas/PdfView";
import { DeferredRemount } from "@/components/canvas/DeferredRemount";
import { ImageLightbox } from "@/components/canvas/ImageLightbox";
import {
  TranslatePill,
  type TranslateDisabledReason,
} from "@/components/canvas/TranslatePill";
import type { ChunkResolution, ReferenceItem } from "@/types/domain";

/** Stable "no dashed tabs" (keeps the selector's result stable). */
const NO_OUTSIDE: OutsideTab[] = [];
/** Stable "no paragraphs" for a PDF that isn't ready (keeps the layer's memos). */
const NO_PARAGRAPHS: PdfParagraph[] = [];
/** Minimum gap between re-checks of why the pill is disabled. */
const RECHECK_MS = 5000;

interface DocEntry {
  mode: "pdf" | "html";
  status: "ready" | "error";
  html?: string;
  pdfData?: Uint8Array;
  /** An HTML paper also has a page PDF (compiled from its LaTeX): the reader
   *  offers it as a PDF tab, where [view:] citations open. */
  pagePdf?: boolean;
}

/** Which view of an HTML paper the reader shows. */
type ReaderTab = "html" | "pdf";

/** A figure or page region to highlight on a PDF view (0-based page, PDF points). */
interface RegionTarget {
  paper: number;
  page: number;
  bbox: number[];
}

/** A paper the standalone reader shows. */
export interface ReaderPaper {
  paper_content_id: number;
  title: string;
}

interface CitationCanvasProps {
  /**
   * Standalone reader (the Ideas page): shows these papers instead of the
   * active chat's references, is always open, leaves the chat canvas's
   * citation / paper requests for that canvas, and closes through onClose.
   */
  standalone?: {
    papers: ReaderPaper[];
    onClose: () => void;
    /** A passage to highlight; a new nonce re-highlights the same chunk. */
    citation?: { chunkId: number; endChunkId?: number; nonce: number };
  };
}

export function CitationCanvas({ standalone }: CitationCanvasProps = {}) {
  const { t } = useTranslation("canvas");
  const storeOpen = useCanvasStore((s) => s.open);
  const open = standalone ? true : storeOpen;
  const storeChunkId = useCanvasStore((s) => s.requestedChunkId);
  const storeEndChunkId = useCanvasStore((s) => s.requestedEndChunkId);
  const storeNonce = useCanvasStore((s) => s.requestNonce);
  // The citation to resolve: the standalone reader's own, else the chat's request.
  const standaloneCitation = standalone?.citation;
  const requestedChunkId = standalone ? (standaloneCitation?.chunkId ?? null) : storeChunkId;
  const requestedEndChunkId = standalone ? (standaloneCitation?.endChunkId ?? null) : storeEndChunkId;
  const requestNonce = standalone ? (standaloneCitation?.nonce ?? 0) : storeNonce;
  // Figure / page-region citations come from the chat only.
  const storeFigure = useCanvasStore((s) => s.requestedFigure);
  const storeView = useCanvasStore((s) => s.requestedView);
  const requestedFigure = standalone ? null : storeFigure;
  const requestedView = standalone ? null : storeView;
  const requestAnimateScroll = useCanvasStore((s) => s.requestAnimateScroll);
  const consumeCitation = useCanvasStore((s) => s.consumeCitation);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const setActivePaperId = useCanvasStore((s) => s.setActivePaperId);
  const setReadingSection = useCanvasStore((s) => s.setReadingSection);
  const setReaderView = useCanvasStore((s) => s.setReaderView);

  // Derive the active session's enabled references (mirror ReferenceSourcesPanel)
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const referencesBySession = useChatStore((s) => s.referencesBySession);

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const activeSession =
    activeSessionId !== null
      ? (sessions.find((s) => s.id === activeSessionId) ?? null)
      : null;
  const backendSessionId = activeSession?.backend_session_id ?? null;
  const standalonePapers = standalone?.papers;
  const allRefs: ReferenceItem[] = useMemo(
    () =>
      standalonePapers
        ? standalonePapers.map((p) => ({
            // Not a chat reference: no papers row, never toggled.
            papers_id: -p.paper_content_id,
            paper_content_id: p.paper_content_id,
            enabled: true,
            added_at: "",
            arxiv_id: null,
            title: p.title,
            year: null,
            kind: "",
            is_private: false,
          }))
        : backendSessionId !== null
          ? (referencesBySession[backendSessionId] ?? [])
          : [],
    [standalonePapers, backendSessionId, referencesBySession],
  );
  const refs = allRefs.filter((r) => r.enabled);

  const [displayedPaperId, setDisplayedPaperId] = useState<number | null>(null);
  // A paper asked for by a link, a citation or a note that is not one of
  // this chat's enabled papers (a switched-off paper, a library paper): a
  // dashed tab of this chat, shared with the notes pane (temporary until
  // kept; the next such paper replaces it).
  const outsideTabs = useCanvasStore((s) =>
    standalone || activeSessionId === null ? NO_OUTSIDE : (s.outsideByChat[activeSessionId] ?? NO_OUTSIDE),
  );
  const showPaper = (pid: number) => {
    setDisplayedPaperId(pid);
    if (standalone) return;
    const chat = useChatStore.getState();
    const bsid = chat.sessions.find((x) => x.id === chat.activeSessionId)?.backend_session_id ?? null;
    const enabled = bsid === null ? undefined : chat.referencesBySession[bsid]?.filter((r) => r.enabled);
    if (chat.activeSessionId !== null && !enabled?.some((r) => r.paper_content_id === pid)) {
      useCanvasStore.getState().showOutside(chat.activeSessionId, pid);
    }
  };
  const [activeChunk, setActiveChunk] = useState<ChunkResolution | null>(null);
  // The dom_id of the LAST chunk of a multi-chunk (section) citation, so the
  // HTML highlight spans the whole cited section, not just its first chunk.
  // Null for a single-chunk `[chunk:N]` citation.
  const [endDomId, setEndDomId] = useState<string | null>(null);
  // Whether the active citation's scroll should animate (smooth) — only when the
  // canvas was already open at click time (see canvas store `requestAnimateScroll`).
  const [scrollAnimate, setScrollAnimate] = useState(false);
  const [stale, setStale] = useState(false);
  // Bumped on every resolved citation so re-clicking the SAME chunk re-fires
  // the highlight + scroll (the view keys on chunk values, which don't change).
  const [highlightNonce, setHighlightNonce] = useState(0);
  // Per-paper fetched document content. Survives the whole session (the canvas
  // stays mounted) so re-opening / tab-switching never re-fetches.
  const [docByPaper, setDocByPaper] = useState<Record<number, DocEntry>>({});
  // The figure the reader clicked to inspect full-screen (null = no lightbox).
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(
    null,
  );
  // A [fig:] citation on an HTML paper: the anchor the figure follows.
  const [figureTarget, setFigureTarget] = useState<{ paper: number; anchor: string } | null>(
    null,
  );
  // A [fig:] on a PDF paper, or a [view:] citation: the region to highlight.
  const [regionTarget, setRegionTarget] = useState<RegionTarget | null>(null);
  // HTML papers with a page PDF: which tab is shown, and the page PDF bytes.
  const [readerTabByPaper, setReaderTabByPaper] = useState<Record<number, ReaderTab>>({});
  // Whether the HTML | PDF track animates: only when the tab changes for the
  // paper already shown — a paper switch jumps straight to that paper's tab.
  const [slideTrack, setSlideTrack] = useState(false);
  const [pagePdfByPaper, setPagePdfByPaper] = useState<
    Record<number, Uint8Array | "error">
  >({});
  const pagePdfFetched = useRef<Set<number>>(new Set());
  const ensurePagePdf = useCallback((pid: number) => {
    if (pagePdfFetched.current.has(pid)) return;
    pagePdfFetched.current.add(pid);
    fetchPagePdfData(pid).then(
      (data) => setPagePdfByPaper((prev) => ({ ...prev, [pid]: data })),
      () => {
        setPagePdfByPaper((prev) => ({ ...prev, [pid]: "error" }));
        pagePdfFetched.current.delete(pid); // retry on the next request
      },
    );
  }, []);

  // Papers we've already kicked off a fetch for (prefetch dedupe).
  const fetchedDocs = useRef<Set<number>>(new Set());
  // The documents as last set (read by store subscriptions), and a request
  // for a paper's view that waits for its document.
  const docByPaperRef = useRef(docByPaper);
  useEffect(() => {
    docByPaperRef.current = docByPaper;
  }, [docByPaper]);
  const pendingTabRef = useRef<{ paperId: number; tab: ReaderTab } | null>(null);
  // Shows a paper in a view it has (a PDF-only paper has one).
  const applyReaderTab = useCallback(
    (pid: number, want: ReaderTab, doc: DocEntry) => {
      if (doc.mode !== "html" || doc.status !== "ready") return;
      const tab: ReaderTab = want === "pdf" && doc.pagePdf ? "pdf" : "html";
      // The track slides only for the paper already shown (a paper switch jumps).
      setSlideTrack(useCanvasStore.getState().activePaperId === pid);
      setReaderTabByPaper((prev) => (prev[pid] === tab ? prev : { ...prev, [pid]: tab }));
      if (tab === "pdf") ensurePagePdf(pid);
    },
    [ensurePagePdf],
  );
  const applyReaderTabRef = useRef(applyReaderTab);
  useEffect(() => {
    applyReaderTabRef.current = applyReaderTab;
  }, [applyReaderTab]);

  // Fetch one paper's document (mode + content) once, populating docByPaper.
  // Used by the background prefetch (enabled refs) AND on-demand when a citation
  // resolves to a toggled-off paper that was never prefetched. Deduped on
  // `fetchedDocs`, so calling it for an already-loaded paper is a no-op.
  const ensureDoc = useCallback((pid: number) => {
    if (fetchedDocs.current.has(pid)) return;
    fetchedDocs.current.add(pid);
    void (async () => {
      try {
        const { mode, page_pdf } = await getDocumentInfo(pid);
        const entry: DocEntry =
          mode === "pdf"
            ? { mode, status: "ready", pdfData: await fetchPaperPdfData(pid) }
            : {
                mode,
                status: "ready",
                pagePdf: page_pdf,
                // Strip the dead polyfill.io/html5shiv scripts (they stall the
                // load), repoint any MathJax loader (Debian-local path OR CDN)
                // at the vendored same-origin build (else math renders as raw
                // \(...\) in the iframe / needs the internet), inject a content-
                // visibility hint (so revealing the canvas doesn't lay out the
                // whole paper at once — the multi-second open/close freeze),
                // then inject <base> so the paper's relative asset URLs
                // (`asset/...`, served by the backend) resolve to the backend,
                // not the app origin.
                html: withBaseHref(
                  injectPerfStyle(
                    localizeMathjax(stripDeadCdnScripts(await fetchPaperHtml(pid))),
                  ),
                  `${API_BASE_URL}/papers/content/${pid}/`,
                ),
              };
        setDocByPaper((prev) => ({ ...prev, [pid]: entry }));
        const waiting = pendingTabRef.current;
        if (waiting?.paperId === pid) {
          pendingTabRef.current = null;
          applyReaderTabRef.current(pid, waiting.tab, entry);
        }
      } catch {
        setDocByPaper((prev) => ({
          ...prev,
          [pid]: { mode: "html", status: "error" },
        }));
        fetchedDocs.current.delete(pid); // allow a retry on a later pass
      }
    })();
  }, []);

  const refIds = refs.map((r) => r.paper_content_id);
  const refIdsKey = refIds.join(",");
  // All of THIS session's papers, enabled or not. Used for displayed-paper
  // validity (a citation to a toggled-off paper is still viewable) — distinct
  // from `refIds`, which is the enabled set that drives the persistent tabs.
  const allRefIds = allRefs.map((r) => r.paper_content_id);

  const firstEnabledRef = refs.length > 0 ? refs[0] : null;
  // Ignore a `displayedPaperId` that isn't among THIS session's papers — the
  // canvas stays mounted across session switches (W4b), so a leftover selection
  // from a previous session would otherwise show the wrong paper. We validate
  // against ALL session papers (not just enabled) so clicking a citation whose
  // source is toggled off can still display that paper (view-only).
  // The dashed tabs: papers this chat shows that are not enabled in it.
  const outsideIds = outsideTabs
    .map((o) => o.paperId)
    .filter((pid) => !refIds.includes(pid));
  const validDisplayedId =
    displayedPaperId !== null &&
    (allRefIds.includes(displayedPaperId) || outsideIds.includes(displayedPaperId))
      ? displayedPaperId
      : null;
  // A library paper's name, looked up once (the notes pane's tab reads it too).
  const paperTitles = useCanvasStore((s) => s.paperTitles);
  const setPaperTitle = useCanvasStore((s) => s.setPaperTitle);
  const untitledKey = outsideIds
    .filter((pid) => !allRefIds.includes(pid) && paperTitles[pid] === undefined)
    .join(",");
  const titleLookups = useRef<Set<number>>(new Set());
  useEffect(() => {
    for (const pid of untitledKey ? untitledKey.split(",").map(Number) : []) {
      if (titleLookups.current.has(pid)) continue;
      titleLookups.current.add(pid);
      getPaperInfo(pid).then(
        (info) => setPaperTitle(pid, info.title),
        () => titleLookups.current.delete(pid), // the tab shows without a name; retried later
      );
    }
  }, [untitledKey, setPaperTitle]);
  const effectivePaperId =
    validDisplayedId ?? firstEnabledRef?.paper_content_id ?? null;
  const activeDoc =
    effectivePaperId != null ? (docByPaper[effectivePaperId] ?? null) : null;

  // Plan H1 bilingual reading. On/off and the language live in the persisted
  // translation store (per paper); the rest is per canvas session.
  const translationLanguage = useTranslationStore((s) => s.language);
  const translationEnabledByPaper = useTranslationStore((s) => s.enabledByPaper);
  const [fullPaper, setFullPaper] = useState(false);
  // PDF paragraph geometry per paper: its status gates the pill (a PDF
  // without blocks stays disabled) and the layer reuses the paragraphs.
  // Absent = loading; "failed" = the layer's own fetch is the fallback;
  // "load_failed" = the layer's fetch failed too (the pill says why).
  const [paragraphsByPaper, setParagraphsByPaper] = useState<
    Record<number, ParagraphsResponse | "failed" | "load_failed">
  >({});
  // Only a "ready" status is final; anything else is re-checked (Marker may
  // write blocks.json later) when the paper becomes active again or the
  // disabled pill is hovered/focused (throttled).
  const readyParagraphs = useRef<Set<number>>(new Set());
  const paragraphsInFlight = useRef<Set<number>>(new Set());
  const lastRecheck = useRef<Map<number, number>>(new Map());
  const [staleByPaper, setStaleByPaper] = useState<Record<number, boolean>>({});
  const [retranslateByPaper, setRetranslateByPaper] = useState<
    Record<number, number>
  >({});
  // Failed translations / a paused full-paper run per paper (QA F2), and a
  // nonce per paper that re-requests every failed block.
  const [statusByPaper, setStatusByPaper] = useState<
    Record<number, TranslationQueueStatus>
  >({});
  const [retryFailedByPaper, setRetryFailedByPaper] = useState<
    Record<number, number>
  >({});
  // HTML papers whose loaded document has nothing to translate (a raw-LaTeX
  // `<pre>` fallback render): the pill is disabled with a reason (QA B2).
  const [htmlUntranslatable, setHtmlUntranslatable] = useState<
    Record<number, boolean>
  >({});

  // Re-read whenever the Settings config is (re)fetched, e.g. after a save,
  // so the full-paper switch applies without remounting the canvas.
  const settingsConfig = useSettingsStore((s) => s.config);
  useEffect(() => {
    let cancelled = false;
    getTranslateConfig().then(
      (c) => {
        if (!cancelled) setFullPaper(c.full_paper);
      },
      () => undefined, // keep the look-ahead default
    );
    return () => {
      cancelled = true;
    };
  }, [settingsConfig]);

  const activePdfId =
    effectivePaperId != null && activeDoc?.mode === "pdf" ? effectivePaperId : null;
  const checkParagraphs = useCallback((pid: number) => {
    if (
      readyParagraphs.current.has(pid) ||
      paragraphsInFlight.current.has(pid)
    ) {
      return;
    }
    paragraphsInFlight.current.add(pid);
    getPdfParagraphs(pid)
      .then(
        (res) => {
          if (res.status === "ready") readyParagraphs.current.add(pid);
          setParagraphsByPaper((prev) => ({ ...prev, [pid]: res }));
        },
        // Failed: hand the fetch to the layer, which retries on each enable.
        // A re-check that fails keeps the last known status.
        () =>
          setParagraphsByPaper((prev) =>
            pid in prev ? prev : { ...prev, [pid]: "failed" },
          ),
      )
      .finally(() => paragraphsInFlight.current.delete(pid));
  }, []);
  useEffect(() => {
    if (activePdfId !== null) checkParagraphs(activePdfId);
  }, [activePdfId, checkParagraphs]);

  // The layer fetched on its own (the canvas fetch had failed).
  const onLayerParagraphs = useCallback(
    (pid: number, res: ParagraphsResponse | null) =>
      setParagraphsByPaper((prev) => ({ ...prev, [pid]: res ?? "load_failed" })),
    [],
  );
  const onStaleChange = useCallback((pid: number, stale: boolean) => {
    setStaleByPaper((prev) =>
      !!prev[pid] === stale ? prev : { ...prev, [pid]: stale },
    );
  }, []);
  const onStatusChange = useCallback(
    (pid: number, status: TranslationQueueStatus) => {
      setStatusByPaper((prev) => {
        const cur = prev[pid];
        return cur?.failed === status.failed && cur.paused === status.paused
          ? prev
          : { ...prev, [pid]: status };
      });
    },
    [],
  );
  const onTranslatableChange = useCallback((pid: number, ok: boolean) => {
    setHtmlUntranslatable((prev) =>
      !!prev[pid] === !ok ? prev : { ...prev, [pid]: !ok },
    );
  }, []);
  // Only the shown paper translates: hidden (kept-mounted) HTML papers stay
  // inert, and switching back re-attaches from the server cache.
  const translationFor = (pid: number) => ({
    enabled:
      !!translationEnabledByPaper[pid] &&
      pid === effectivePaperId &&
      !htmlUntranslatable[pid],
    language: translationLanguage,
    paperId: pid,
    fullPaper,
    retranslateNonce: retranslateByPaper[pid] ?? 0,
    onStaleChange: (stale: boolean) => onStaleChange(pid, stale),
    retryFailedNonce: retryFailedByPaper[pid] ?? 0,
    onStatusChange: (status: TranslationQueueStatus) => onStatusChange(pid, status),
    onTranslatableChange: (ok: boolean) => onTranslatableChange(pid, ok),
  });
  const activeParagraphs =
    activePdfId !== null ? paragraphsByPaper[activePdfId] : undefined;
  const paragraphsReady =
    typeof activeParagraphs === "object" ? activeParagraphs : null;
  const translateDisabledReason: TranslateDisabledReason | null =
    activeDoc?.status === "error" || activeParagraphs === "load_failed"
      ? "load_failed"
      : activeDoc?.mode === "html" &&
          effectivePaperId != null &&
          htmlUntranslatable[effectivePaperId]
        ? "no_text"
        : paragraphsReady && paragraphsReady.status !== "ready"
        ? paragraphsReady.status
        : // Ready but nothing to hover (no paragraph with geometry and text).
          paragraphsReady &&
            !paragraphsReady.paragraphs.some(
              (p) => p.fragments.length > 0 && p.text.trim() !== "",
            )
          ? "no_blocks"
          : null;
  // Whether the shown paper can translate isn't known yet: its document is
  // still loading, or its PDF paragraphs are in flight.
  const translatePending =
    activeDoc == null ||
    (activePdfId !== null && activeParagraphs === undefined);
  // Hovering or focusing the disabled pill re-checks why (at most every 5 s).
  const recheckDisabled = () => {
    const pid = effectivePaperId;
    if (pid == null) return;
    const last = lastRecheck.current.get(pid);
    if (last !== undefined && Date.now() - last < RECHECK_MS) return;
    lastRecheck.current.set(pid, Date.now());
    if (activeDoc?.status === "error") ensureDoc(pid);
    else if (activePdfId !== null) checkParagraphs(pid);
  };
  // Loading → none, so the layer doesn't race the canvas fetch; failed →
  // undefined (the layer fetches); not ready → none (never garbled formulas).
  const pdfParagraphs: PdfParagraph[] | undefined =
    activeParagraphs === "failed"
      ? undefined
      : paragraphsReady?.status === "ready"
        ? paragraphsReady.paragraphs
        : NO_PARAGRAPHS; // loading, not ready, or load_failed

  // The dashed tabs: a citation whose source the user switched off, or a
  // library paper this chat does not have (a link, a note). Viewing one
  // leaves the reference's enabled state untouched (the citation is
  // historical evidence, separate from future agent context); Add brings
  // the paper into the chat.
  const outsideItems = outsideTabs
    .filter((o) => outsideIds.includes(o.paperId))
    .map((o) => ({
      id: o.paperId,
      preview: o.preview,
      title: allRefs.find((r) => r.paper_content_id === o.paperId)?.title ?? paperTitles[o.paperId] ?? "",
    }));
  // The shown paper switched off while on screen takes a dashed tab too.
  useEffect(() => {
    if (standalone || activeSessionId === null || effectivePaperId === null) return;
    if (refIds.includes(effectivePaperId) || outsideIds.includes(effectivePaperId)) return;
    useCanvasStore.getState().showOutside(activeSessionId, effectivePaperId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [standalone, activeSessionId, effectivePaperId, refIdsKey, outsideIds.join(",")]);

  // Resolve a clicked citation → its paper + highlight target. Keyed on
  // requestNonce so the same chunk re-clicked re-resolves; consumes the request
  // so a later browse-mode open doesn't re-jump here.
  useEffect(() => {
    if (!open || requestedChunkId == null) return;
    let cancelled = false;
    getChunk(requestedChunkId)
      .then(async (c) => {
        if (cancelled) return;
        // For a multi-chunk (section) citation, resolve the LAST chunk's dom_id
        // BEFORE bumping the highlight nonce, so the span highlights in one
        // pass (no flash from first-chunk-then-extend). A failed/equal end id
        // degrades to the single-chunk highlight.
        let endDom: string | null = null;
        if (
          requestedEndChunkId != null &&
          requestedEndChunkId !== requestedChunkId
        ) {
          try {
            endDom = (await getChunk(requestedEndChunkId)).dom_id;
          } catch {
            endDom = null;
          }
          if (cancelled) return;
        }
        // Animate only when NO fresh layout happens: the canvas was already open
        // AND the cited chunk lives in the paper already on screen. A paper
        // switch (or a panel open) reveals a mounted-but-hidden iframe that lays
        // out for the first time, so a smooth glide would track a shifting target
        // and land wrong — jump instantly in that case. `effectivePaperId` here
        // is the paper shown BEFORE this resolution switches it.
        const samePaper = c.paper_content_id === effectivePaperId;
        // The cited paper may be a toggled-off reference the prefetch skipped —
        // fetch it on demand so it can be displayed view-only (no-op if cached).
        ensureDoc(c.paper_content_id);
        setFigureTarget(null);
        setRegionTarget(null);
        setSlideTrack(samePaper);
        setReaderTabByPaper((prev) =>
          prev[c.paper_content_id] === "pdf"
            ? { ...prev, [c.paper_content_id]: "html" }
            : prev,
        );
        setActiveChunk(c);
        setEndDomId(endDom);
        showPaper(c.paper_content_id);
        setScrollAnimate(requestAnimateScroll && samePaper);
        setStale(false);
        setHighlightNonce((n) => n + 1);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        if (/\b404\b/.test(msg)) {
          setActiveChunk(null);
          setStale(true);
        } else {
          toast.error(t("toast.loadCitedPaperFailed"));
        }
      })
      .finally(() => {
        // The standalone reader leaves the chat's request slot alone.
        if (!cancelled && !standalone) consumeCitation();
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestNonce]);

  // Resolve a clicked figure / page-region citation → its paper + the target:
  // an HTML paper highlights the figure after its anchor; a PDF paper — or a
  // page region on any paper — highlights the box on the page PDF (an HTML
  // paper switches to its PDF tab for that). Keyed on requestNonce like the
  // chunk resolution above, which ignores these requests.
  useEffect(() => {
    if (!open) return;
    const fig = requestedFigure;
    const view = requestedView;
    if (fig == null && view == null) return;
    const pid = fig ? fig.paper : (view?.paper ?? 0);
    let cancelled = false;
    void (async () => {
      try {
        const info = await getDocumentInfo(pid);
        let anchor: string | null = null;
        let region: { page: number; bbox: number[] } | null = null;
        if (fig) {
          const meta = await getFigureMeta(pid, fig.figureId);
          if (info.mode === "html" && meta.html_anchor) anchor = meta.html_anchor;
          else if (meta.page != null && meta.bbox && info.page_pdf)
            region = { page: meta.page, bbox: meta.bbox };
          else if (meta.page != null && info.page_pdf) {
            // Known page, unknown box: open the page itself.
            const r = await getViewRegion(pid, meta.page + 1, [0, 0, 1000, 1000]);
            region = { page: r.page, bbox: r.bbox };
          } else if (meta.has_image) {
            // No place in the paper: show the cited figure's image.
            if (cancelled) return;
            setLightbox({
              src: figureImageUrl(pid, fig.figureId),
              alt: meta.caption || fig.figureId,
            });
            return;
          } else throw new Error("API 404: figure not locatable");
        } else if (view) {
          const r = await getViewRegion(pid, view.page, view.box);
          region = { page: r.page, bbox: r.bbox };
        }
        if (cancelled) return;
        const samePaper = pid === effectivePaperId;
        ensureDoc(pid);
        setSlideTrack(samePaper);
        if (info.mode === "html") {
          const tab: ReaderTab = anchor ? "html" : "pdf";
          setReaderTabByPaper((prev) => ({ ...prev, [pid]: tab }));
          if (tab === "pdf") ensurePagePdf(pid);
        }
        setActiveChunk(null);
        setEndDomId(null);
        setFigureTarget(anchor ? { paper: pid, anchor } : null);
        setRegionTarget(region ? { paper: pid, ...region } : null);
        showPaper(pid);
        setScrollAnimate(requestAnimateScroll && samePaper);
        setStale(false);
        setHighlightNonce((n) => n + 1);
      } catch (err: unknown) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        if (/\b40[04]\b/.test(msg)) {
          toast.message(t(fig ? "toast.figureGone" : "toast.regionGone"));
        } else {
          toast.error(t("toast.loadCitedPaperFailed"));
        }
      } finally {
        if (!cancelled) consumeCitation();
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestNonce]);

  // Switch the active paper when the References panel asks to "Open in canvas"
  // (no chunk highlight — just a paper swap). We react via Zustand's subscribe
  // API (a ref tracks the last-handled nonce, so re-opening the SAME paper still
  // re-switches) rather than a deps-effect, so the setState runs in a
  // subscription callback — not synchronously in the effect body — satisfying
  // the react-hooks/set-state-in-effect rule (mirrors ChatPage's pattern).
  // ensureDoc covers a toggled-off paper the prefetch skipped (shown view-only
  // via the transient tab); consumePaperRequest stops a later browse-mode reopen
  // from re-jumping here.
  // A paper switch from a tab (here or in the notes pane) slides the new
  // paper in from the side of its tab; a citation jumps (it scrolls to a
  // passage). A tab not drawn yet (a link's new tab) is at the end.
  const [slide, setSlide] = useState<{ pid: number; dir: 1 | -1 } | null>(null);
  const slideTo = (pid: number) => {
    const order = [...refIds, ...outsideIds];
    const from = effectivePaperId === null ? -1 : order.indexOf(effectivePaperId);
    const to = order.indexOf(pid);
    if (from < 0 || pid === effectivePaperId) return;
    setSlide({ pid, dir: to < 0 || to > from ? 1 : -1 });
  };
  const slideClass = (pid: number) =>
    slide?.pid === pid ? (slide.dir > 0 ? "paper-slide-from-right" : "paper-slide-from-left") : undefined;
  // The subscriptions below read the latest render's handlers (set below).
  const latest = useRef<{ slideTo: (pid: number) => void; land: (closed: ClosedTab) => void }>({
    slideTo: () => undefined,
    land: () => undefined,
  });
  const prevClosedRef = useRef(useCanvasStore.getState().closedTab?.n ?? 0);
  useEffect(() => {
    if (standalone) return;
    return useCanvasStore.subscribe((state) => {
      const closed = state.closedTab;
      if (!closed || closed.n === prevClosedRef.current) return;
      prevClosedRef.current = closed.n;
      latest.current.land(closed);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const prevPaperNonceRef = useRef(useCanvasStore.getState().paperRequestNonce);
  useEffect(() => {
    if (standalone) return;
    return useCanvasStore.subscribe((state) => {
      if (state.paperRequestNonce === prevPaperNonceRef.current) return;
      prevPaperNonceRef.current = state.paperRequestNonce;
      const pid = state.requestedPaperId;
      if (pid == null) return;
      ensureDoc(pid);
      setSlideTrack(false);
      latest.current.slideTo(pid);
      showPaper(pid);
      setActiveChunk(null);
      setFigureTarget(null);
      setRegionTarget(null);
      setStale(false);
      useCanvasStore.getState().consumePaperRequest();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A request to show a paper in one of its views (the view a mark was made
  // in): its tab, when the paper has that view. Read in the subscription, so
  // the state updates never run in an effect body.
  const prevTabRequestRef = useRef(useCanvasStore.getState().readerTabRequest?.n ?? 0);
  useEffect(() => {
    if (standalone) return;
    return useCanvasStore.subscribe((state) => {
      const req = state.readerTabRequest;
      if (!req || req.n === prevTabRequestRef.current) return;
      prevTabRequestRef.current = req.n;
      const doc = docByPaperRef.current[req.paperId];
      // A paper not loaded yet takes it once its document arrives.
      if (doc) applyReaderTabRef.current(req.paperId, req.tab, doc);
      else pendingTabRef.current = { paperId: req.paperId, tab: req.tab };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Background prefetch: fetch every enabled reference's document when the
  // session's reference set changes, so each paper is ready before the user
  // opens it. `ensureDoc` dedupes so each paper loads once. (Toggled-off papers
  // are NOT prefetched — they load on demand when a citation targets them.)
  useEffect(() => {
    for (const pid of refIds) ensureDoc(pid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refIdsKey]);

  // Publish the paper this canvas is currently showing (null when closed) so the
  // References panel can mark the active row. `setActivePaperId` is a Zustand
  // action, not a React setter, so calling it in the effect body is fine.
  useEffect(() => {
    if (standalone) return; // not the chat's canvas
    setActivePaperId(open ? effectivePaperId : null);
  }, [standalone, open, effectivePaperId, setActivePaperId]);

  const handleTabClick = (pid: number) => {
    // The PDF view is wrapped in <DeferredRemount> (keyed by paper), which
    // unmounts the old reader and mounts the new one across a task boundary —
    // so this swap can stay a plain synchronous state update without freezing.
    slideTo(pid);
    setDisplayedPaperId(pid);
    setSlideTrack(pid === effectivePaperId);
    setActiveChunk(null);
    setFigureTarget(null);
    setRegionTarget(null);
    setStale(false);
  };

  // A dashed tab closed (here or in the notes pane) while this reader shows
  // it: land on the neighbouring paper.
  const land = (closed: ClosedTab) => {
    if (closed.chat !== activeSessionId || closed.paperId !== effectivePaperId) return;
    if (closed.neighbour !== null) handleTabClick(closed.neighbour);
    else setDisplayedPaperId(null);
  };
  useEffect(() => {
    latest.current = { slideTo, land };
  });

  const switchReaderTab = (tab: ReaderTab) => {
    if (effectivePaperId == null) return;
    const pid = effectivePaperId;
    setSlideTrack(true);
    setReaderTabByPaper((prev) => ({ ...prev, [pid]: tab }));
    if (tab === "pdf") ensurePagePdf(pid);
  };

  // Add on a dashed tab: the paper joins the chat (a switched-off paper is
  // switched back on; a library paper is attached) and its tab turns solid.
  const addOutside = async (pid: number): Promise<void> => {
    const chat = activeSessionId;
    if (backendSessionId === null || chat === null) return;
    if (await addPaperToChat(backendSessionId, pid)) useCanvasStore.getState().forgetOutside(chat, pid);
  };
  // × on a dashed tab closes it here and in the notes pane; both land on
  // the neighbouring paper (the closedTab subscription moves this reader).
  const closeOutside = (pid: number) => {
    if (activeSessionId === null) return;
    const order = [...refIds, ...outsideIds];
    useCanvasStore.getState().closeOutside(activeSessionId, pid, neighbourOf(order, pid));
  };
  const keepOutside = (pid: number) => {
    if (activeSessionId !== null) useCanvasStore.getState().keepOutside(activeSessionId, pid);
  };

  // Stay mounted while there are references to prefetch (even when closed) so
  // their content loads + caches for the session; render nothing only when
  // closed AND there's nothing to prefetch.
  // Publish how the paper is shown: the HTML, or a PDF (a PDF-only paper or
  // the PDF tab), where the reader bar offers the Box tool.
  const readerKind: "html" | "pdf" | null =
    !open || effectivePaperId == null || !activeDoc
      ? null
      : activeDoc.mode === "pdf" ||
          (activeDoc.status === "ready" &&
            activeDoc.pagePdf === true &&
            readerTabByPaper[effectivePaperId] === "pdf")
        ? "pdf"
        : "html";
  useEffect(() => {
    if (standalone) return; // not the chat's reader
    setReaderView(effectivePaperId != null && readerKind ? { paperId: effectivePaperId, kind: readerKind } : null);
  }, [standalone, effectivePaperId, readerKind, setReaderView]);
  useEffect(() => {
    if (standalone) return;
    return () => setReaderView(null);
  }, [standalone, setReaderView]);

  // The HTML/PDF track slides by transform inside an overflow-hidden body.
  // Anything that scrolls that body sideways (a scrollIntoView on content in
  // the off-screen half) leaves the reader half HTML, half PDF: keep it at 0.
  const trackRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    resetHorizontalScroll(trackRef.current);
  }, [readerTabByPaper, effectivePaperId]);

  if (!open && refs.length === 0) return null;


  // HTML papers stay mounted (hidden) for instant switching; PDF papers render
  // only when active (react-pdf is heavy). Content is cached either way. Include
  // the view-only transient paper so a citation to a toggled-off source renders.
  const renderableIds = [...refIds, ...outsideIds];
  const htmlPapers = renderableIds.filter(
    (pid) => docByPaper[pid]?.mode === "html",
  );
  // An HTML paper with a page PDF shows HTML | PDF tabs; the two views sit
  // side by side on a track that slides toward the chosen one.
  const activeHasPagePdf =
    activeDoc?.mode === "html" && activeDoc.status === "ready" && activeDoc.pagePdf === true;
  // Marks are drawn on every paper the chat's reader shows (HTML and PDF);
  // the Box tool is for PDF pages.
  const marksToggleShown = !standalone && activeDoc?.status === "ready";
  const boxToolShown = marksToggleShown && readerKind === "pdf";
  const readerBarOpen = activeHasPagePdf || marksToggleShown;
  const readerTab: ReaderTab =
    effectivePaperId != null && activeHasPagePdf
      ? (readerTabByPaper[effectivePaperId] ?? "html")
      : "html";
  const activePagePdf =
    effectivePaperId != null ? pagePdfByPaper[effectivePaperId] : undefined;
  // What a PDF view of `pid` highlights: a cited chunk, else a figure/region.
  const pdfTarget = (pid: number) =>
    activeChunk?.paper_content_id === pid
      ? { text: resolveNeedle(activeChunk), page: activeChunk.page, bbox: activeChunk.bbox }
      : regionTarget?.paper === pid
        ? { text: null, page: regionTarget.page, bbox: regionTarget.bbox }
        : null;

  return (
    <aside
      aria-label={t("panel.label")}
      aria-hidden={!open}
      inert={!open ? true : undefined}
      className="flex h-full w-full flex-col border-l border-border bg-card"
    >
      {/* Header: paper switcher + close */}
      <header className="flex items-center justify-between border-b border-border px-2 py-1">
        {/* The paper tabs, shared with the notes pane: the chat's papers,
            then a dashed tab for a paper shown but not in the chat. */}
        <div
          role="tablist"
          aria-label={t("paperTabs:readerList", "Papers")}
          onKeyDown={roveTabs}
          className="paper-tabs-scroll flex min-w-0 flex-1 items-center gap-1 overflow-x-auto overflow-y-hidden"
        >
          <PaperTabs
            papers={refs.map((r) => ({ id: r.paper_content_id, title: r.title }))}
            outside={outsideItems}
            selected={effectivePaperId}
            onSelect={handleTabClick}
            onAdd={(pid) => void addOutside(pid)}
            onClose={closeOutside}
            onKeep={keepOutside}
            resetKey={standalone ? "standalone" : activeSessionId}
          />
        </div>

        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0"
          aria-label={standalone ? t("panel.closeReader", "Close reader") : t("panel.close")}
          onClick={standalone ? standalone.onClose : closeCanvas}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>

      {/* The reader bar: HTML | PDF tabs for an HTML paper with a page PDF,
          the Highlighter and "Show my marks" for any paper and Box on a PDF. Always mounted: the bar's
          height opens and closes so the reader below glides. */}
      <div
        data-reader-tabs
        data-open={readerBarOpen ? "true" : "false"}
        aria-hidden={!readerBarOpen}
        inert={!readerBarOpen ? true : undefined}
        className={cn(
          "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
          readerBarOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="@container/readerbar flex items-center gap-1 border-b border-border px-2 py-1">
            <div
              role="tablist"
              aria-label={t("tabs.label")}
              aria-hidden={!activeHasPagePdf}
              inert={!activeHasPagePdf ? true : undefined}
              className={cn(
                "flex gap-1 transition-opacity duration-200 ease-out motion-reduce:transition-none",
                !activeHasPagePdf && "opacity-0",
              )}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const next: ReaderTab = readerTab === "html" ? "pdf" : "html";
                switchReaderTab(next);
                e.currentTarget
                  .querySelector<HTMLButtonElement>(`[data-tab="${next}"]`)
                  ?.focus();
              }}
            >
              {(["html", "pdf"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  data-tab={tab}
                  id={`reader-tab-${tab}`}
                  aria-selected={readerTab === tab}
                  aria-controls={`reader-panel-${tab}`}
                  tabIndex={readerTab === tab ? 0 : -1}
                  onClick={() => switchReaderTab(tab)}
                  className={cn(
                    "rounded px-2 py-0.5 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
                    readerTab === tab
                      ? "bg-foreground/10 text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {t(`tabs.${tab}`)}
                </button>
              ))}
            </div>
            <span className="flex-1" />
            {!standalone && effectivePaperId != null && (
              <MyMarksMenu paperId={effectivePaperId} shown={marksToggleShown} />
            )}
            {!standalone && <BoxToolButton shown={boxToolShown} />}
            {!standalone && <HighlighterControl shown={marksToggleShown} />}
            {!standalone && <ShowMarksToggle shown={marksToggleShown} />}
          </div>
        </div>
      </div>

      {/* Body */}
      <div
        data-reader-body
        className="relative flex flex-1 flex-col overflow-hidden"
        onScroll={(e) => {
          if (e.currentTarget.scrollLeft !== 0) e.currentTarget.scrollLeft = 0;
        }}
      >
        {stale && (
          <div
            role="status"
            className="m-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          >
            {t("stale")}
          </div>
        )}

        {/* Error for the active paper (loading is covered by the swap spinner). */}
        {activeDoc?.status === "error" && (
          <div className="p-4 text-xs text-destructive">
            {t("error.loadPaper")}
          </div>
        )}

        {/* The shown paper is still being fetched. */}
        {effectivePaperId != null && activeDoc === null && <PaperLoading />}

        {/* HTML papers: kept mounted (hidden) for instant switching. Their
            page PDF sits beside them on a track that slides between the two. */}
        <div
          ref={trackRef}
          data-reader-track
          hidden={activeDoc?.mode !== "html"}
          className={cn(
            "flex h-full w-[200%] flex-1",
            slideTrack &&
              "transition-transform duration-200 ease-out motion-reduce:transition-none",
            readerTab === "pdf" && "-translate-x-1/2",
          )}
        >
        <div
          id="reader-panel-html"
          role="tabpanel"
          aria-labelledby="reader-tab-html"
          className="flex h-full w-1/2 flex-col"
          inert={readerTab === "pdf" ? true : undefined}
        >
        {htmlPapers.map((pid) => {
          const doc = docByPaper[pid];
          if (doc?.status !== "ready" || doc.html == null) return null;
          const isActive = pid === effectivePaperId;
          return (
            <div
              key={pid}
              hidden={!isActive}
              className={cn("flex h-full w-full flex-1 flex-col", isActive && slideClass(pid))}
              onAnimationEnd={(e) => {
                if (e.target === e.currentTarget) setSlide(null);
              }}
            >
              <HtmlView
                html={doc.html}
                isDark={isDark}
                onReadingHeading={
                  isActive && !standalone && readerTab === "html"
                    ? (h, within) => setReadingSection(pid, h, within)
                    : undefined
                }
                scrollBehavior={scrollAnimate ? "smooth" : "instant"}
                nonce={
                  isActive &&
                  (activeChunk?.paper_content_id === pid || figureTarget?.paper === pid)
                    ? highlightNonce
                    : 0
                }
                highlightFigureAnchor={
                  isActive && figureTarget?.paper === pid ? figureTarget.anchor : null
                }
                highlightDomId={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? activeChunk.dom_id
                    : null
                }
                highlightEndDomId={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? endDomId
                    : null
                }
                highlightText={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? resolveNeedle(activeChunk)
                    : null
                }
                sectionTitle={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? activeChunk.section
                    : null
                }
                onHighlightMiss={() =>
                  toast.message(t("toast.passageNotFoundHtml"))
                }
                onImageActivate={(src, alt) => setLightbox({ src, alt })}
                translation={translationFor(pid)}
                marks={standalone ? undefined : { paperId: pid }}
              />
            </div>
          );
        })}
        </div>
        <div
          id="reader-panel-pdf"
          role="tabpanel"
          aria-labelledby="reader-tab-pdf"
          className="flex h-full w-1/2 flex-col"
          inert={readerTab !== "pdf" ? true : undefined}
        >
          {effectivePaperId != null &&
            activeHasPagePdf &&
            (readerTab === "pdf" || activePagePdf !== undefined) &&
            (activePagePdf === undefined ? (
              <PaperLoading />
            ) : activePagePdf === "error" ? (
              <div className="p-4 text-xs text-destructive">{t("error.loadPaper")}</div>
            ) : (
              <DeferredRemount swapKey={effectivePaperId}>
                <PdfView
                  data={activePagePdf}
                  highlightText={null}
                  bboxPage={regionTarget?.paper === effectivePaperId ? regionTarget.page : null}
                  bbox={regionTarget?.paper === effectivePaperId ? regionTarget.bbox : null}
                  nonce={regionTarget?.paper === effectivePaperId ? highlightNonce : 0}
                  onHighlightMiss={() => toast.message(t("toast.regionGone"))}
                  marks={standalone ? undefined : { paperId: effectivePaperId }}
                  onReadingHeading={
                    !standalone && readerTab === "pdf"
                      ? (h, within) => setReadingSection(effectivePaperId, h, within)
                      : undefined
                  }
                />
              </DeferredRemount>
            ))}
        </div>
        </div>

        {/* PDF papers: render only the active one (react-pdf is heavy).
            DeferredRemount remounts the reader across a task boundary on a
            paper swap, so the old PDF tears down before the new one mounts. */}
        {effectivePaperId != null &&
          activeDoc?.mode === "pdf" &&
          activeDoc.status === "ready" &&
          activeDoc.pdfData != null && (
            <DeferredRemount swapKey={effectivePaperId}>
              <PdfView
                data={activeDoc.pdfData}
                highlightText={pdfTarget(effectivePaperId)?.text ?? null}
                bboxPage={pdfTarget(effectivePaperId)?.page ?? null}
                bbox={pdfTarget(effectivePaperId)?.bbox ?? null}
                nonce={pdfTarget(effectivePaperId) ? highlightNonce : 0}
                onHighlightMiss={() =>
                  toast.message(t("toast.passageNotFoundPdf"))
                }
                marks={standalone ? undefined : { paperId: effectivePaperId }}
                onReadingHeading={
                  standalone ? undefined : (h, within) => setReadingSection(effectivePaperId, h, within)
                }
                translation={{
                  ...translationFor(effectivePaperId),
                  paragraphs: pdfParagraphs,
                  onParagraphsLoaded: (res) =>
                    onLayerParagraphs(effectivePaperId, res),
                }}
              />
            </DeferredRemount>
          )}


        {/* Floating translate control (HTML and PDF). */}
        {effectivePaperId != null && (
          <TranslatePill
            paperId={effectivePaperId}
            disabledReason={translateDisabledReason}
            pending={translatePending}
            onDisabledInteract={recheckDisabled}
            staleTerminology={
              !!translationEnabledByPaper[effectivePaperId] &&
              !!staleByPaper[effectivePaperId]
            }
            failedCount={statusByPaper[effectivePaperId]?.failed ?? 0}
            paused={statusByPaper[effectivePaperId]?.paused ?? false}
            onRetryFailed={() =>
              setRetryFailedByPaper((prev) => ({
                ...prev,
                [effectivePaperId]: (prev[effectivePaperId] ?? 0) + 1,
              }))
            }
            onRetranslate={() =>
              setRetranslateByPaper((prev) => ({
                ...prev,
                [effectivePaperId]: (prev[effectivePaperId] ?? 0) + 1,
              }))
            }
          />
        )}
      </div>

      {/* The note card a click on a mark opens (the notes pane hidden). */}
      {!standalone && <NoteCard />}

      {/* Full-screen figure previewer (portals to body, covers the viewport). */}
      {lightbox && (
        <ImageLightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </aside>
  );
}
