import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import {
  getChunk,
  getDocumentMode,
  fetchPaperHtml,
  fetchPaperPdfData,
  toggleReference,
  API_BASE_URL,
} from "@/lib/api";
import {
  withBaseHref,
  stripDeadCdnScripts,
  injectPerfStyle,
  localizeMathjax,
} from "@/lib/withBaseHref";
import { resolveNeedle } from "@/lib/resolveNeedle";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HtmlView } from "@/components/canvas/HtmlView";
import { PdfView } from "@/components/canvas/PdfView";
import { DeferredRemount } from "@/components/canvas/DeferredRemount";
import { ImageLightbox } from "@/components/canvas/ImageLightbox";
import type { ChunkResolution, ReferenceItem } from "@/types/domain";

const MAX_VISIBLE_TABS = 3;

interface DocEntry {
  mode: "pdf" | "html";
  status: "ready" | "error";
  html?: string;
  pdfData?: Uint8Array;
}

export function CitationCanvas() {
  const { t } = useTranslation("canvas");
  const open = useCanvasStore((s) => s.open);
  const requestedChunkId = useCanvasStore((s) => s.requestedChunkId);
  const requestedEndChunkId = useCanvasStore((s) => s.requestedEndChunkId);
  const requestNonce = useCanvasStore((s) => s.requestNonce);
  const requestAnimateScroll = useCanvasStore((s) => s.requestAnimateScroll);
  const consumeCitation = useCanvasStore((s) => s.consumeCitation);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const setActivePaperId = useCanvasStore((s) => s.setActivePaperId);

  // Derive the active session's enabled references (mirror ReferenceSourcesPanel)
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const referencesBySession = useChatStore((s) => s.referencesBySession);
  const patchReferenceEnabled = useChatStore((s) => s.patchReferenceEnabled);

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const activeSession =
    activeSessionId !== null
      ? (sessions.find((s) => s.id === activeSessionId) ?? null)
      : null;
  const backendSessionId = activeSession?.backend_session_id ?? null;
  const allRefs: ReferenceItem[] =
    backendSessionId !== null
      ? (referencesBySession[backendSessionId] ?? [])
      : [];
  const refs = allRefs.filter((r) => r.enabled);

  const [displayedPaperId, setDisplayedPaperId] = useState<number | null>(null);
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
  const [overflowOpen, setOverflowOpen] = useState(false);
  // The figure the reader clicked to inspect full-screen (null = no lightbox).
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(
    null,
  );

  // Papers we've already kicked off a fetch for (prefetch dedupe).
  const fetchedDocs = useRef<Set<number>>(new Set());

  // Fetch one paper's document (mode + content) once, populating docByPaper.
  // Used by the background prefetch (enabled refs) AND on-demand when a citation
  // resolves to a toggled-off paper that was never prefetched. Deduped on
  // `fetchedDocs`, so calling it for an already-loaded paper is a no-op.
  const ensureDoc = useCallback((pid: number) => {
    if (fetchedDocs.current.has(pid)) return;
    fetchedDocs.current.add(pid);
    void (async () => {
      try {
        const mode = await getDocumentMode(pid);
        const entry: DocEntry =
          mode === "pdf"
            ? { mode, status: "ready", pdfData: await fetchPaperPdfData(pid) }
            : {
                mode,
                status: "ready",
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
  const validDisplayedId =
    displayedPaperId !== null && allRefIds.includes(displayedPaperId)
      ? displayedPaperId
      : null;
  const effectivePaperId =
    validDisplayedId ?? firstEnabledRef?.paper_content_id ?? null;
  const activeDoc =
    effectivePaperId != null ? (docByPaper[effectivePaperId] ?? null) : null;

  // The displayed paper when it is NOT an enabled reference — reached by
  // clicking a citation whose source the user toggled off. Shown view-only via
  // a transient tab; the reference's enabled state is left untouched (the
  // citation is historical evidence, separate from future agent context).
  const transientRef =
    effectivePaperId !== null && !refIds.includes(effectivePaperId)
      ? (allRefs.find((r) => r.paper_content_id === effectivePaperId) ?? null)
      : null;

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
        setActiveChunk(c);
        setEndDomId(endDom);
        setDisplayedPaperId(c.paper_content_id);
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
        if (!cancelled) consumeCitation();
      });
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
  const prevPaperNonceRef = useRef(useCanvasStore.getState().paperRequestNonce);
  useEffect(() => {
    return useCanvasStore.subscribe((state) => {
      if (state.paperRequestNonce === prevPaperNonceRef.current) return;
      prevPaperNonceRef.current = state.paperRequestNonce;
      const pid = state.requestedPaperId;
      if (pid == null) return;
      ensureDoc(pid);
      setDisplayedPaperId(pid);
      setActiveChunk(null);
      setStale(false);
      useCanvasStore.getState().consumePaperRequest();
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
    setActivePaperId(open ? effectivePaperId : null);
  }, [open, effectivePaperId, setActivePaperId]);

  const handleTabClick = (pid: number) => {
    // The PDF view is wrapped in <DeferredRemount> (keyed by paper), which
    // unmounts the old reader and mounts the new one across a task boundary —
    // so this swap can stay a plain synchronous state update without freezing.
    setDisplayedPaperId(pid);
    setActiveChunk(null);
    setStale(false);
    setOverflowOpen(false);
  };

  // Promote the view-only transient paper into an enabled reference (the user
  // opted in via the "+ Add" affordance). Optimistic, with rollback on failure —
  // mirrors ReferenceSourcesPanel's toggle. The paper is already displayed +
  // cached, so it simply gains a persistent tab (transientRef → null).
  const enableReference = async (): Promise<void> => {
    if (transientRef === null || backendSessionId === null) return;
    const papersId = transientRef.papers_id;
    patchReferenceEnabled(backendSessionId, papersId, true);
    try {
      await toggleReference(papersId, true);
    } catch {
      patchReferenceEnabled(backendSessionId, papersId, false);
      toast.error(t("toast.addPaperFailed"));
    }
  };

  // Stay mounted while there are references to prefetch (even when closed) so
  // their content loads + caches for the session; render nothing only when
  // closed AND there's nothing to prefetch.
  if (!open && refs.length === 0) return null;

  const visibleTabs = refs.slice(0, MAX_VISIBLE_TABS);
  const overflowTabs = refs.slice(MAX_VISIBLE_TABS);
  const hasOverflow = overflowTabs.length > 0;

  // HTML papers stay mounted (hidden) for instant switching; PDF papers render
  // only when active (react-pdf is heavy). Content is cached either way. Include
  // the view-only transient paper so a citation to a toggled-off source renders.
  const renderableIds =
    transientRef !== null ? [...refIds, transientRef.paper_content_id] : refIds;
  const htmlPapers = renderableIds.filter(
    (pid) => docByPaper[pid]?.mode === "html",
  );

  return (
    <aside
      aria-label={t("panel.label")}
      aria-hidden={!open}
      inert={!open ? true : undefined}
      className="flex h-full w-full flex-col border-l border-border bg-card"
    >
      {/* Header: paper switcher + close */}
      <header className="flex items-center justify-between border-b border-border px-2 py-1">
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
          {visibleTabs.map((r) => (
            <button
              key={r.paper_content_id}
              type="button"
              onClick={() => handleTabClick(r.paper_content_id)}
              className={
                "truncate rounded px-2 py-1 text-xs font-medium transition-colors " +
                (effectivePaperId === r.paper_content_id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground")
              }
              title={r.title}
            >
              {r.title}
            </button>
          ))}
          {hasOverflow && (
            <Popover open={overflowOpen} onOpenChange={setOverflowOpen}>
              <PopoverTrigger
                render={
                  <button
                    type="button"
                    className="rounded px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={t("panel.morePapers")}
                  />
                }
              >
                …
              </PopoverTrigger>
              <PopoverContent side="bottom" align="start" className="w-56 p-1">
                <div className="flex flex-col gap-0.5">
                  {overflowTabs.map((r) => (
                    <button
                      key={r.paper_content_id}
                      type="button"
                      onClick={() => handleTabClick(r.paper_content_id)}
                      className={
                        "w-full truncate rounded px-2 py-1.5 text-left text-xs font-medium transition-colors " +
                        (effectivePaperId === r.paper_content_id
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground")
                      }
                      title={r.title}
                    >
                      {r.title}
                    </button>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          )}

          {/* View-only tab for a citation whose source reference is toggled off.
              Distinct dashed style + an "add" affordance; viewing it does NOT
              change the reference's enabled state (agent context is untouched). */}
          {transientRef !== null && (
            <span className="flex shrink-0 items-center gap-1 rounded border border-dashed border-border bg-muted/40 pl-2 text-xs">
              <span
                className="max-w-[9rem] truncate italic text-muted-foreground"
                title={t("panel.viewingTitle", { title: transientRef.title })}
              >
                {transientRef.title}
              </span>
              <button
                type="button"
                onClick={() => void enableReference()}
                className="flex items-center gap-0.5 rounded px-1.5 py-1 font-medium text-primary hover:bg-primary/10"
                title={t("panel.addToReferences")}
              >
                <Plus className="h-3 w-3" />
                {t("panel.add")}
              </button>
            </span>
          )}
        </div>

        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0"
          aria-label={t("panel.close")}
          onClick={closeCanvas}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>

      {/* Body */}
      <div className="relative flex flex-1 flex-col overflow-hidden">
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

        {/* HTML papers: kept mounted (hidden) for instant switching. */}
        {htmlPapers.map((pid) => {
          const doc = docByPaper[pid];
          if (doc?.status !== "ready" || doc.html == null) return null;
          const isActive = pid === effectivePaperId;
          return (
            <div
              key={pid}
              hidden={!isActive}
              className="flex h-full w-full flex-1 flex-col"
            >
              <HtmlView
                html={doc.html}
                isDark={isDark}
                scrollBehavior={scrollAnimate ? "smooth" : "instant"}
                nonce={
                  isActive && activeChunk?.paper_content_id === pid
                    ? highlightNonce
                    : 0
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
              />
            </div>
          );
        })}

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
                highlightText={
                  activeChunk?.paper_content_id === effectivePaperId
                    ? resolveNeedle(activeChunk)
                    : null
                }
                bboxPage={
                  activeChunk?.paper_content_id === effectivePaperId
                    ? activeChunk.page
                    : null
                }
                bbox={
                  activeChunk?.paper_content_id === effectivePaperId
                    ? activeChunk.bbox
                    : null
                }
                nonce={
                  activeChunk?.paper_content_id === effectivePaperId
                    ? highlightNonce
                    : 0
                }
                onHighlightMiss={() =>
                  toast.message(t("toast.passageNotFoundPdf"))
                }
              />
            </DeferredRemount>
          )}
      </div>

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
