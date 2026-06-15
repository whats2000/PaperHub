import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { useTranslation } from "react-i18next";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileCode,
  Loader2,
  Pencil,
  PencilLine,
  Presentation,
  X,
} from "lucide-react";

import {
  useSlidesStore,
  FILMSTRIP_MIN_WIDTH,
  FILMSTRIP_MAX_WIDTH,
  NOTE_MIN_HEIGHT,
} from "@/store/slides";
import { useChatStore } from "@/store/chat";
import {
  fetchDeckPdfData,
  deckPdfUrl,
  deckTexUrl,
  getDeckSlides,
  getDeckTexText,
  putFrameTex,
  putDeckTex,
  putSlideSources,
} from "@/lib/api";
import { pushWidth } from "@/lib/stableWidth";
import { Button } from "@/components/ui/button";
import { usePresentation } from "@/hooks/usePresentation";
import { PresenterControls } from "@/components/slides/PresenterControls";
import { SlideLatexEditor } from "@/components/slides/SlideLatexEditor";
import { SourcesStrip } from "@/components/slides/SourcesStrip";

// pdf.js needs a worker; resolve it from the installed pdfjs-dist via Vite's
// import.meta.url so the worker is bundled + served from the app origin.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/** Horizontal chrome (rail padding + thumbnail border) subtracted from the
 *  rail width to get the thumbnail render width. Matches the 80→64 default. */
const FILMSTRIP_THUMB_INSET = 16;

interface Props {
  sessionId: number;
  speakerNotes: Record<string, string>;
  /** A slides generate/edit turn is in flight for this session. While true the
   *  canvas is masked and the PDF is NOT refetched (the recompile isn't ready);
   *  on completion the revision bump triggers a cache-busted reload. */
  busy?: boolean;
  /** Present-tense status shown on the mask (live slide-agent stage). */
  stage?: string;
  /** Persist a manual speaker-note edit for `page`. When omitted the note pane
   *  stays read-only (no Edit affordance). */
  onSaveNote?: (page: number, text: string) => Promise<void>;
}

/** Sentinel the backend writes on pages a multi-page slide spills onto; its
 *  real note lives on the slide's first page, so we don't offer to edit it. */
const CONTINUED_NOTE = "(continued)";

/**
 * SlidesPanel — renders the compiled deck PDF with:
 * - A left filmstrip rail of thumbnail pages (clickable, active page marked),
 *   with a draggable vertical divider to resize the rail.
 * - A main slide area showing the current page at container width.
 * - A header with title, prev/next navigation, page counter, and download links.
 * - A resizable speaker note pane below the slide (draggable horizontal divider).
 * - Keyboard navigation: ArrowLeft/ArrowRight change page.
 */
export function SlidesPanel({
  sessionId,
  speakerNotes,
  busy = false,
  stage,
  onSaveNote,
}: Props) {
  const { t } = useTranslation("slides");
  const deck = useSlidesStore((s) => s.deckBySession[sessionId]);
  const revision = useSlidesStore((s) => s.deckRevisionBySession[sessionId] ?? 0);
  // True while a DeckChip "Switch to this version" round-trip is in flight.
  // We fold this into ``masked`` so the old PDF doesn't sit on screen during
  // the restore POST + getDeck handshake (mirrors how ``busy`` masks a
  // chat-turn edit). The post-setDeck refetch is still covered by
  // ``reloading`` below, so the mask stays continuous through to fresh bytes.
  const restoringVersion = useSlidesStore(
    (s) => s.restoringBySession[sessionId] ?? false,
  );
  const currentPage = useSlidesStore(
    (s) => s.currentPageBySession[sessionId] ?? 1,
  );
  const setCurrentPage = useSlidesStore((s) => s.setCurrentPage);
  const presentStartedAt = useSlidesStore(
    (s) => s.presentStartedAtBySession[sessionId] ?? 0,
  );
  const { presenting, audienceConnected, present, stop } = usePresentation(
    sessionId,
    currentPage,
  );
  // Layout dimensions live in the store so they survive panel close/reopen
  // (the panel unmounts on close) and reload (persisted to localStorage).
  const noteHeight = useSlidesStore((s) => s.noteHeight);
  const setNoteHeight = useSlidesStore((s) => s.setNoteHeight);
  const filmstripWidth = useSlidesStore((s) => s.filmstripWidth);
  const setFilmstripWidth = useSlidesStore((s) => s.setFilmstripWidth);

  // --- F6.2 manual editing + Sources strip ---
  const editorMode = useSlidesStore(
    (s) => s.editorModeBySession[sessionId] ?? "off",
  );
  const setEditorMode = useSlidesStore((s) => s.setEditorMode);
  const slidesSources = useSlidesStore(
    (s) => s.slidesSourcesBySession[sessionId],
  );
  const setSlidesSources = useSlidesStore((s) => s.setSlidesSources);
  const bumpDeckRevision = useSlidesStore((s) => s.bumpDeckRevision);
  // paper_content_id → title for the Sources chips, from the session's refs.
  const references = useChatStore((s) => s.referencesBySession[sessionId]);
  const titleByPaperId = useMemo(() => {
    const m = new Map<number, string>();
    for (const r of references ?? []) m.set(r.paper_content_id, r.title);
    return m;
  }, [references]);
  // The Add-source picker offers the session's papers (its references) — the
  // reliable, always-populated list. The backend validates the same scope.
  const pickerPapers = useMemo(
    () =>
      (references ?? []).map((r) => ({
        paper_content_id: r.paper_content_id,
        title: r.title,
      })),
    [references],
  );

  // Local editor draft + state (the persisted bits — mode + sources — live in
  // the store so a panel remount during Q&A doesn't lose them).
  const [editorDraft, setEditorDraft] = useState("");
  const [editorErrorLog, setEditorErrorLog] = useState<string | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  // Currently-loaded PDF bytes + the (session, revision) they belong to. We
  // keep the last good bytes on screen while a newer revision of the SAME
  // session is fetching so an edit-complete reload swaps in under the mask
  // rather than flashing blank.
  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  // Tracks the fetchKey whose pdf.js Document has finished PARSING and whose
  // current Page has finished rasterizing. The mask covers the panel until
  // this matches the current fetchKey — without it, the mask drops the moment
  // the bytes load, but pdf.js still spends ~1-2 s parsing + rasterizing the
  // new deck, so the stale PDF flashes underneath before the new one paints.
  const [renderedKey, setRenderedKey] = useState<string | null>(null);
  const [mainWidth, setMainWidth] = useState(0);
  // Defer the width used for pdf.js rasterisation so a rapid resize-drag
  // doesn't fire N renders per pixel across every mounted page. React holds
  // the previous value during high-priority renders and settles to the
  // latest when idle — single-pointer-event rasterise instead of per-pixel.
  const renderWidth = useDeferredValue(mainWidth);
  // Measured thumbnail render width — derived from the rail's actual inner
  // width (excludes the scrollbar), NOT the style width, so a thumbnail never
  // overflows + gets clipped on the right when the rail scrolls.
  const [thumbWidth, setThumbWidth] = useState(
    filmstripWidth - FILMSTRIP_THUMB_INSET,
  );

  const panelRef = useRef<HTMLDivElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);
  const filmRoRef = useRef<ResizeObserver | null>(null);
  // Recent-applied width buffers feeding pushWidth's flap guard (issue #6) — one
  // per measured element. Survive re-renders so the guard sees prior samples.
  const mainWidthRecent = useRef<number[]>([]);
  const thumbWidthRecent = useRef<number[]>([]);

  // Measure the main slide area via a CALLBACK ref so it fires the instant the
  // element mounts — which is when the <Document> renders (file != null), not
  // on first component mount. This avoids the page rendering at its intrinsic
  // ~362pt Beamer width and leaving the panel half-blank. A ResizeObserver
  // keeps the width correct through the panel open-animation + divider drags.
  const measureMainArea = useCallback((el: HTMLDivElement | null) => {
    roRef.current?.disconnect();
    roRef.current = null;
    if (!el) return;
    const measure = () => {
      // While the panel is hidden (kept mounted on a Canvas swap) clientWidth is
      // 0; ignore it and KEEP the last good width so the rasterized pages aren't
      // re-rendered to 0 and back — the swap stays instant (no reload flash).
      if (el.clientWidth === 0) return;
      const next = Math.max(0, el.clientWidth - 16);
      // Flap guard: reject a width that bounces back across a scrollbar
      // threshold so the layout can't oscillate forever (issue #6).
      const step = pushWidth(mainWidthRecent.current, next);
      mainWidthRecent.current = step.recent;
      if (step.apply) setMainWidth(next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return; // jsdom guard
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    roRef.current = ro;
  }, []);

  // Measure the filmstrip rail's inner content width via a callback ref. We use
  // clientWidth (excludes border + scrollbar) minus padding (p-1 = 8px) and the
  // thumbnail button's border (border-2 = 4px), so the thumbnail always fits.
  const measureFilmstrip = useCallback((el: HTMLDivElement | null) => {
    filmRoRef.current?.disconnect();
    filmRoRef.current = null;
    if (!el) return;
    const measure = () => {
      if (el.clientWidth === 0) return; // hidden — keep the last width (see main area)
      const next = Math.max(32, el.clientWidth - 12);
      const step = pushWidth(thumbWidthRecent.current, next);
      thumbWidthRecent.current = step.recent;
      if (step.apply) setThumbWidth(next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return; // jsdom guard
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    filmRoRef.current = ro;
  }, []);

  // Disconnect any live observer on unmount.
  useEffect(
    () => () => {
      roRef.current?.disconnect();
      filmRoRef.current?.disconnect();
    },
    [],
  );

  // Identity of the bytes currently loaded, parsed from loadedKey.
  const [loadedSid, loadedRev] = loadedKey
    ? loadedKey.split(":").map(Number)
    : [null, null];

  // Fetch PDF bytes for the current (session, revision). Gated on !busy: while a
  // generate/edit turn is streaming the recompiled PDF isn't ready, so we hold
  // the current bytes under the mask and don't refetch. When the turn finishes
  // the revision has bumped (setDeck) and busy clears → this refetches the fresh
  // deck (cache-busted by revision). State is set only in the async callback, so
  // the effect never updates state synchronously.
  const fetchKey = `${sessionId}:${revision}`;
  useEffect(() => {
    if (busy) return; // mid-edit: keep current bytes, mask covers the panel
    if (loadedKey === fetchKey) return; // already showing this revision
    let cancelled = false;
    fetchDeckPdfData(sessionId, revision)
      .then((b) => {
        if (cancelled) return;
        setBytes(b);
        setLoadedKey(fetchKey);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, revision, busy, fetchKey, loadedKey]);

  // Fetch per-slide detail (frame source + grounding) for the editor + Sources
  // strip, keyed on (session, revision) so a recompile refreshes it. Gated on
  // !busy: mid-edit the deck_slides rows are being rewritten. A 404 (no deck)
  // clears it.
  useEffect(() => {
    if (busy) return;
    let cancelled = false;
    getDeckSlides(sessionId)
      .then((rows) => {
        if (!cancelled) setSlidesSources(sessionId, rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, revision, busy, setSlidesSources]);

  // pdfjs TRANSFERS (detaches) the ArrayBuffer it's given to its worker, so
  // pass a fresh COPY each time (same pattern as PdfView.tsx). Only render the
  // bytes if they belong to THIS session (on a session switch the old session's
  // bytes are ignored until the new ones load). Memoized so the Document
  // doesn't reload on unrelated re-renders.
  const file = useMemo(
    () => (bytes && loadedSid === sessionId ? { data: bytes.slice() } : null),
    [bytes, loadedSid, sessionId],
  );

  // Mask the canvas while a turn is in flight, while a version-switch
  // restore is mid-handshake, OR while a completed edit/restore's fresh deck
  // is still loading (same session, newer revision than what's on screen) OR
  // while pdf.js is still parsing/rasterizing the just-loaded bytes.
  // ``renderedKey`` is bumped only in the active Page's ``onRenderSuccess``,
  // so a fresh fetchKey naturally fails this comparison until the new page
  // has actually painted — no reset effect needed (the inequality IS the
  // reset). Without this gate the mask drops the moment the fetch resolves
  // but pdf.js still spends ~1-2 s parsing + rasterizing, flashing the stale
  // PDF through.
  const reloading =
    !busy && bytes !== null && loadedSid === sessionId && loadedRev !== revision;
  const pdfRendering = file !== null && renderedKey !== fetchKey;
  const masked = busy || restoringVersion || reloading || pdfRendering;
  // The mask heading reflects WHY it's up: a version restore, a genuine
  // update/reload (an edit recompiled → new bytes), or just the first-load
  // parse/raster of an UNCHANGED deck (``pdfRendering`` alone). The last case
  // is a plain "Loading", not "Updating" — saying "Updating slides…" when
  // nothing changed is misleading.
  const updating = busy || reloading;
  const maskHeading = restoringVersion
    ? t("panel.mask.restoring")
    : updating
      ? t("panel.mask.updating")
      : t("panel.mask.loading", "Loading slides…");
  // Restore wins as the stage label when both are present so the user knows
  // why the panel masked (version switch vs. chat-turn edit). The chat-turn's
  // ``stage`` prop carries live slide-agent stage names when ``busy`` is set.
  const stageLabel = restoringVersion
    ? t("panel.mask.restoring")
    : stage;

  // Navigate to a page (clamped). In frame-edit mode the editor FOLLOWS the
  // active page: it loads that page's frame source, discarding an unsaved draft
  // (the editor tracks whichever slide you're on). No-op for the draft when not
  // editing a frame. Shared by the keyboard, the header arrows, and the
  // filmstrip so every navigation path keeps the editor in sync.
  const goTo = useCallback(
    (page: number) => {
      const clamped = Math.min(Math.max(page, 1), numPages || 1);
      setCurrentPage(sessionId, clamped);
      if (editorMode === "frame") {
        const tgt = (slidesSources ?? []).find(
          (s) => s.page_start <= clamped && clamped <= s.page_end,
        );
        // Load the CONTENT (cite markers stripped) — the editor is content-only.
        setEditorDraft(tgt?.content_tex ?? tgt?.frame_tex ?? "");
        setEditorErrorLog(null);
      }
    },
    [numPages, sessionId, editorMode, slidesSources, setCurrentPage],
  );

  // Keyboard navigation.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") goTo(currentPage - 1);
      else if (e.key === "ArrowRight") goTo(currentPage + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [currentPage, goTo]);

  // Draggable vertical divider — mirrors useCanvasResize but on the Y axis.
  const onDividerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = noteHeight;

      const onMove = (ev: PointerEvent) => {
        const panel = panelRef.current;
        const maxH = panel ? Math.round(panel.clientHeight * 0.6) : 400;
        // Drag down → smaller note; drag up → larger note.
        const next = Math.min(
          Math.max(startHeight + (startY - ev.clientY), NOTE_MIN_HEIGHT),
          maxH,
        );
        setNoteHeight(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [noteHeight, setNoteHeight],
  );

  // Draggable vertical divider between the filmstrip rail and the slide area.
  // Drag right → wider filmstrip; clamped to [MIN, MAX].
  const onFilmstripDividerPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = filmstripWidth;

      const onMove = (ev: PointerEvent) => {
        const next = Math.min(
          Math.max(startWidth + (ev.clientX - startX), FILMSTRIP_MIN_WIDTH),
          FILMSTRIP_MAX_WIDTH,
        );
        setFilmstripWidth(next);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [filmstripWidth, setFilmstripWidth],
  );

  const speakerNote = speakerNotes[String(currentPage)];

  // The slide whose page span covers the current page (the owner of a
  // continuation page) — drives the frame editor + the Sources strip.
  const currentSlide = useMemo(
    () =>
      (slidesSources ?? []).find(
        (s) => s.page_start <= currentPage && currentPage <= s.page_end,
      ) ?? null,
    [slidesSources, currentPage],
  );
  const currentSources = currentSlide?.source_sections ?? [];

  const canEdit = deck?.status === "ok" && !busy;
  const editing = editorMode !== "off";

  // Enter "edit current frame" — load the slide CONTENT only (cite markers
  // stripped; grounding is managed via the Sources reference editor below).
  const beginEditFrame = () => {
    if (!currentSlide) return;
    setEditorErrorLog(null);
    setEditorDraft(currentSlide.content_tex ?? currentSlide.frame_tex);
    setEditorMode(sessionId, "frame");
  };

  // Persist the current slide's grounding from the Sources reference editor —
  // deterministic, NO recompile (the % cite: marker is a LaTeX comment). Then
  // refresh the per-slide detail so the strip + chips reflect the new sources.
  const setCurrentSlideSources = async (
    pairs: { paper_id: number; section_name: string }[],
  ) => {
    await putSlideSources(sessionId, currentPage, pairs);
    try {
      setSlidesSources(sessionId, await getDeckSlides(sessionId));
    } catch {
      /* a transient refetch failure self-heals on the next load */
    }
  };
  // Enter "edit all deck" — fetch the whole deck.tex as text.
  const beginEditDeck = async () => {
    setEditorErrorLog(null);
    try {
      setEditorDraft(await getDeckTexText(sessionId));
      setEditorMode(sessionId, "deck");
    } catch {
      setEditorErrorLog("failed to load deck source");
    }
  };
  const cancelEdit = () => {
    setEditorMode(sessionId, "off");
    setEditorErrorLog(null);
  };
  // Save → recompile in the background → reload the deck, staying on the
  // current page. A compile failure keeps the editor open with the log.
  const saveEdit = async () => {
    setSavingEdit(true);
    setEditorErrorLog(null);
    try {
      const res =
        editorMode === "frame"
          ? await putFrameTex(sessionId, currentPage, editorDraft)
          : await putDeckTex(sessionId, editorDraft);
      if (res.ok) {
        setEditorMode(sessionId, "off");
        // Force a cache-busted PDF refetch + refresh the per-slide detail; the
        // current page is left untouched (clamped on PDF load if the deck shrank).
        bumpDeckRevision(sessionId);
        try {
          setSlidesSources(sessionId, await getDeckSlides(sessionId));
        } catch {
          /* a transient slides refetch failure self-heals on the next load */
        }
      } else {
        setEditorErrorLog(res.log ?? "compile failed");
      }
    } catch (e) {
      setEditorErrorLog(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingEdit(false);
    }
  };

  // --- manual speaker-note editing ---
  // Track WHICH page is being edited (not a bare bool) so navigating to another
  // page auto-exits edit mode by derivation — no setState-in-effect needed.
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const editingNote = editingPage === currentPage;
  // A continuation page ("(continued)") and an in-flight deck edit both block
  // editing; the real note lives on the owning slide's first page.
  const noteEditable =
    !!onSaveNote && !busy && speakerNote !== CONTINUED_NOTE;

  const beginEditNote = () => {
    setNoteDraft(speakerNote && speakerNote !== CONTINUED_NOTE ? speakerNote : "");
    setEditingPage(currentPage);
  };
  const cancelEditNote = () => setEditingPage(null);
  const saveNote = async () => {
    if (!onSaveNote) return;
    setSavingNote(true);
    try {
      await onSaveNote(currentPage, noteDraft);
      setEditingPage(null);
    } finally {
      setSavingNote(false);
    }
  };

  return (
    <div
      ref={panelRef}
      className="flex flex-col h-full bg-card text-foreground overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
        <span className="text-xs font-semibold truncate flex-1 text-foreground">
          {deck?.title ?? t("panel.title")}
        </span>

        {/* Status indicator */}
        {deck?.status === "ok" ? (
          <span className="text-xs text-green-600 dark:text-green-400 shrink-0">
            {t("panel.status.ready")}
          </span>
        ) : deck?.status === "error" ? (
          <span className="text-xs text-destructive shrink-0">
            {t("panel.status.error")}
          </span>
        ) : null}

        {/* Navigation */}
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          aria-label={t("panel.nav.previous")}
          disabled={currentPage <= 1}
          onClick={() => goTo(currentPage - 1)}
        >
          <ChevronLeft className="h-3 w-3" />
        </Button>
        <span className="text-xs text-muted-foreground tabular-nums shrink-0">
          {t("panel.nav.pageCounter", {
            current: currentPage,
            total: numPages || deck?.page_count || "–",
          })}
        </span>
        <Button
          type="button"
          size="icon-xs"
          variant="ghost"
          aria-label={t("panel.nav.next")}
          disabled={numPages > 0 && currentPage >= numPages}
          onClick={() => goTo(currentPage + 1)}
        >
          <ChevronRight className="h-3 w-3" />
        </Button>

        {/* Edit all deck (whole deck.tex) — left of Present */}
        <Button
          type="button"
          size="icon-xs"
          variant={editorMode === "deck" ? "default" : "ghost"}
          aria-label={t("editor.editDeck", "Edit all deck LaTeX")}
          aria-pressed={editorMode === "deck"}
          disabled={!canEdit}
          onClick={() =>
            editorMode === "deck" ? cancelEdit() : void beginEditDeck()
          }
          title={t("editor.editDeck", "Edit all deck LaTeX")}
        >
          <FileCode className="h-3 w-3" />
        </Button>

        {/* Present button */}
        <Button
          type="button"
          size="icon-xs"
          variant={presenting ? "default" : "ghost"}
          aria-label={
            presenting ? t("panel.present.presenting") : t("panel.present.present")
          }
          aria-pressed={presenting}
          disabled={presenting || numPages === 0 || deck?.status !== "ok"}
          onClick={present}
          title={
            presenting
              ? t("panel.present.presentingHint")
              : t("panel.present.openAudience")
          }
        >
          <Presentation className="h-3 w-3" />
        </Button>

        {/* Download links */}
        <a
          href={deckPdfUrl(sessionId)}
          download
          aria-label={t("panel.download.pdf")}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <Download className="h-3 w-3" />
        </a>
        <a
          href={deckTexUrl(sessionId)}
          download
          aria-label={t("panel.download.latex")}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {t("panel.download.tex")}
        </a>

        {/* Edit current frame — top-right corner, mirroring the note pencil */}
        <Button
          type="button"
          size="icon-xs"
          variant={editorMode === "frame" ? "default" : "ghost"}
          aria-label={t("editor.editFrame", "Edit this frame's LaTeX")}
          aria-pressed={editorMode === "frame"}
          disabled={!canEdit || currentSlide === null}
          onClick={() =>
            editorMode === "frame" ? cancelEdit() : beginEditFrame()
          }
          title={t("editor.editFrame", "Edit this frame's LaTeX")}
        >
          <PencilLine className="h-3 w-3" />
        </Button>
      </div>

      {/* Body: single Document wraps filmstrip rail + main slide area.
          react-pdf shares one parsed PDF across all child <Page> components via
          context — two Documents would transfer (detach) the ArrayBuffer to the
          pdfjs worker on the first mount, leaving the second with a zero-length
          buffer and a blank render. Wrapped in a relative container so the
          editing mask can overlay the whole slide region. */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
      {file ? (
        <Document
          file={file}
          onLoadSuccess={(pdf) => {
            setNumPages(pdf.numPages);
            // After a manual recompile the deck may have fewer pages; keep the
            // user on the highest valid page instead of a now-empty index
            // (A1 — "stays on the current active page").
            if (currentPage > pdf.numPages) {
              setCurrentPage(sessionId, pdf.numPages);
            }
          }}
          className="flex flex-1 min-h-0 overflow-hidden"
        >
          {/* Left filmstrip rail */}
          <div
            ref={measureFilmstrip}
            className="flex flex-col gap-1 p-1 overflow-y-auto bg-muted/30 shrink-0"
            // scrollbar-gutter: stable reserves the scrollbar's space whether or
            // not it's shown, so clientWidth stays constant when the scrollbar
            // toggles — removing the ResizeObserver flap at its source (issue #6).
            style={{ width: filmstripWidth, scrollbarGutter: "stable" }}
          >
            {Array.from(
              { length: numPages || deck?.page_count || 0 },
              (_, i) => {
                const pageNum = i + 1;
                const isActive = pageNum === currentPage;
                return (
                  <button
                    key={pageNum}
                    type="button"
                    aria-label={t("panel.filmstrip.slide", { n: pageNum })}
                    onClick={() => goTo(pageNum)}
                    className={`w-full rounded overflow-hidden border-2 transition-colors shrink-0 ${
                      isActive
                        ? "border-primary"
                        : "border-transparent hover:border-border"
                    }`}
                  >
                    <Page pageNumber={pageNum} width={thumbWidth} />
                    <span className="block text-center text-xs text-muted-foreground py-0.5">
                      {pageNum}
                    </span>
                  </button>
                );
              },
            )}
          </div>

          {/* Draggable vertical divider between filmstrip + slide area */}
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("panel.filmstrip.resize")}
            onPointerDown={onFilmstripDividerPointerDown}
            className="w-1.5 cursor-col-resize bg-border hover:bg-primary/40 transition-colors shrink-0"
          />

          {/* Main content column: ALL pages mounted, only the current one
              visible (display:none on the rest). pdf.js rasterises each <Page>
              exactly once on mount and keeps the rendered canvas in the DOM —
              page-switching becomes a CSS class toggle, with zero re-render
              (the live-preview snappiness this UX needs). Memory cost is
              bounded by the deck budget (8–30 slides) at ~2 MB per rasterised
              canvas. Width drives all pages, but is deferred (useDeferredValue)
              so a resize-drag doesn't rasterise N pages per pixel.

              While the manual editor is open it REPLACES this column (the
              filmstrip stays so the user can still navigate between frames). */}
          {editing ? (
            <SlideLatexEditor
              value={editorDraft}
              onChange={setEditorDraft}
              onSave={() => void saveEdit()}
              onCancel={cancelEdit}
              scope={editorMode === "frame" ? "frame" : "deck"}
              saving={savingEdit}
              errorLog={editorErrorLog}
            />
          ) : (
          <div
            ref={measureMainArea}
            className="flex-1 min-h-0 overflow-auto bg-neutral-100 dark:bg-neutral-900 p-2"
            // Reserve the scrollbar gutter so clientWidth doesn't change when the
            // vertical scrollbar appears/disappears — the root cause of the
            // layout-switching loop at threshold widths (issue #6).
            style={{ scrollbarGutter: "stable" }}
          >
            {presenting && (
              <PresenterControls
                startedAt={presentStartedAt}
                currentPage={currentPage}
                numPages={numPages}
                audienceConnected={audienceConnected}
                onStop={stop}
                nextPreview={
                  currentPage < numPages ? (
                    <Page pageNumber={currentPage + 1} width={64} />
                  ) : undefined
                }
              />
            )}
            {Array.from({ length: numPages }, (_, i) => {
              const pageNum = i + 1;
              const isActive = pageNum === currentPage;
              return (
                <div
                  key={pageNum}
                  // `hidden` (display:none) keeps the page in the DOM (canvas
                  // stays rendered) but takes it out of the flow — no scroll,
                  // no overlap, no interaction.
                  hidden={!isActive}
                  aria-hidden={!isActive}
                  data-page={pageNum}
                >
                  <Page
                    pageNumber={pageNum}
                    width={renderWidth > 0 ? renderWidth : undefined}
                    className="mx-auto shadow"
                    // Flip the "rendered" marker only when the CURRENTLY
                    // visible page has actually rasterized for THIS fetchKey.
                    // The mask uses this to wait for real pixel paint instead
                    // of just bytes-loaded, so a version switch keeps the
                    // overlay on through pdf.js's ~1-2 s parse + first-paint
                    // window instead of dropping early and flashing the old
                    // deck. Inactive pages also fire onRenderSuccess as they
                    // rasterize in the background, but we only care about the
                    // active one for the mask handoff.
                    onRenderSuccess={
                      isActive
                        ? () => setRenderedKey(fetchKey)
                        : undefined
                    }
                  />
                </div>
              );
            })}
          </div>
          )}
        </Document>
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden" />
      )}

        {/* Editing mask — covers the slide region while a generate/edit turn is
            in flight or the post-edit reload is fetching. The current deck stays
            underneath (non-interactive) so the swap-in is seamless. */}
        {masked && (
          // Solid ~95% card overlay (no ``backdrop-blur``): the blur shares a
          // GPU layer with whatever sits on top and re-composes every frame,
          // which contends with pdf.js's heavy main-thread paint while a deck
          // is being parsed. The spinner was getting starved of frames and
          // appearing stuck. A solid overlay hides the underlying PDF just as
          // well at zero compositor cost.
          //
          // Three pulsing dots replace the rotating Loader2: `animate-pulse`
          // is opacity-only (the cheapest animation a compositor can run) so
          // it stays smooth even under canvas-paint backpressure. The
          // staggered delays read as "working…" without needing rotate.
          <div
            role="status"
            aria-live="polite"
            className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-card/95"
          >
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-muted-foreground motion-safe:animate-pulse" />
              <span className="h-2 w-2 rounded-full bg-muted-foreground motion-safe:animate-pulse [animation-delay:200ms]" />
              <span className="h-2 w-2 rounded-full bg-muted-foreground motion-safe:animate-pulse [animation-delay:400ms]" />
            </div>
            <span className="text-sm font-medium text-foreground">
              {maskHeading}
            </span>
            {stageLabel && !restoringVersion && (
              <span className="px-4 text-center text-xs text-muted-foreground">
                {stageLabel}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Sources (this page). Read mode (not editing): chips → Citation Canvas.
          Editing the current frame: a deterministic reference editor (× / + Add).
          Editing the whole deck: source editing is per-slide, so it's disabled
          with a hint. */}
      {file && editorMode !== "deck" && (
        <SourcesStrip
          sources={currentSources}
          titleByPaperId={titleByPaperId}
          editable={editorMode === "frame"}
          references={pickerPapers}
          onSetSources={setCurrentSlideSources}
        />
      )}
      {file && editorMode === "deck" && (
        <div className="shrink-0 border-t border-border bg-muted/10 px-3 py-1.5">
          <span className="text-[11px] italic text-muted-foreground">
            {t(
              "sources.deckEditDisabled",
              "Source editing is per-slide — use Edit current frame",
            )}
          </span>
        </div>
      )}

      {/* Draggable divider (outside Document — no pdfjs dependency) */}
      <div
        role="separator"
        aria-orientation="horizontal"
        onPointerDown={onDividerPointerDown}
        className="h-1.5 cursor-row-resize bg-border hover:bg-primary/40 transition-colors shrink-0"
      />

      {/* Speaker note pane (outside Document — no pdfjs dependency). Fixed-height
          flex column: the header stays put and the body (text or textarea) fills
          the rest and scrolls internally, so entering edit mode never grows the
          pane (a flex item's min-height:auto would otherwise stretch it to the
          textarea's intrinsic row height). */}
      <div
        className="shrink-0 flex flex-col min-h-0 border-t border-border bg-muted/20 px-3 py-2"
        style={{ height: noteHeight }}
      >
        <div className="mb-1 flex items-center gap-1 shrink-0">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
            {t("panel.note.heading")}
          </p>
          {/* Edit / Save+Cancel controls live in the header (always visible) so
              a tall note textarea never pushes them out of the scroll area. */}
          {editingNote ? (
            <div className="ml-auto flex items-center gap-1">
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label={t("panel.note.cancel")}
                onClick={cancelEditNote}
                disabled={savingNote}
              >
                <X className="h-3 w-3" />
              </Button>
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label={t("panel.note.save")}
                className="text-primary"
                onClick={() => void saveNote()}
                disabled={savingNote}
              >
                {savingNote ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Check className="h-3 w-3" />
                )}
              </Button>
            </div>
          ) : (
            noteEditable && (
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label={t("panel.note.edit")}
                className="ml-auto"
                onClick={beginEditNote}
              >
                <Pencil className="h-3 w-3" />
              </Button>
            )
          )}
        </div>

        {editingNote ? (
          <textarea
            aria-label={t("panel.note.field")}
            className="flex-1 min-h-0 w-full resize-none rounded border border-border bg-background p-2 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary"
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            disabled={savingNote}
            autoFocus
          />
        ) : speakerNote ? (
          <p className="flex-1 min-h-0 overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap">
            {speakerNote}
          </p>
        ) : (
          <p className="flex-1 min-h-0 text-xs text-muted-foreground italic">
            {t("panel.note.empty")}
          </p>
        )}
      </div>

      {/* F4.5: standalone version-history drawer removed — version switching
          now lives on per-turn DeckChip cards in the chat history, so each
          generate/edit message offers its own "Switch to this version"
          affordance and the deck chip in the panel header has no separate
          drawer to mirror. */}
    </div>
  );
}
