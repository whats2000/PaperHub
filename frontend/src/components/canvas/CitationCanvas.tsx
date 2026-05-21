import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "next-themes";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import {
  getChunk,
  getDocumentMode,
  fetchPaperHtml,
  fetchPaperPdfData,
  API_BASE_URL,
} from "@/lib/api";
import { withBaseHref, stripDeadCdnScripts } from "@/lib/withBaseHref";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HtmlView } from "@/components/canvas/HtmlView";
import { PdfView } from "@/components/canvas/PdfView";
import type { ChunkResolution, ReferenceItem } from "@/types/domain";

const MAX_VISIBLE_TABS = 3;

interface DocEntry {
  mode: "pdf" | "html";
  status: "ready" | "error";
  html?: string;
  pdfData?: Uint8Array;
}

export function CitationCanvas() {
  const open = useCanvasStore((s) => s.open);
  const requestedChunkId = useCanvasStore((s) => s.requestedChunkId);
  const requestNonce = useCanvasStore((s) => s.requestNonce);
  const consumeCitation = useCanvasStore((s) => s.consumeCitation);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);

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
  const allRefs: ReferenceItem[] =
    backendSessionId !== null
      ? (referencesBySession[backendSessionId] ?? [])
      : [];
  const refs = allRefs.filter((r) => r.enabled);

  const [displayedPaperId, setDisplayedPaperId] = useState<number | null>(null);
  const [activeChunk, setActiveChunk] = useState<ChunkResolution | null>(null);
  const [stale, setStale] = useState(false);
  // Per-paper fetched document content. Survives the whole session (the canvas
  // stays mounted) so re-opening / tab-switching never re-fetches.
  const [docByPaper, setDocByPaper] = useState<Record<number, DocEntry>>({});
  const [overflowOpen, setOverflowOpen] = useState(false);

  // Papers we've already kicked off a fetch for (prefetch dedupe).
  const fetchedDocs = useRef<Set<number>>(new Set());

  const refIds = refs.map((r) => r.paper_content_id);
  const refIdsKey = refIds.join(",");

  const firstEnabledRef = refs.length > 0 ? refs[0] : null;
  const effectivePaperId =
    displayedPaperId ?? firstEnabledRef?.paper_content_id ?? null;
  const activeDoc =
    effectivePaperId != null ? (docByPaper[effectivePaperId] ?? null) : null;

  // Resolve a clicked citation → its paper + highlight target. Keyed on
  // requestNonce so the same chunk re-clicked re-resolves; consumes the request
  // so a later browse-mode open doesn't re-jump here.
  useEffect(() => {
    if (!open || requestedChunkId == null) return;
    let cancelled = false;
    getChunk(requestedChunkId)
      .then((c) => {
        if (cancelled) return;
        setActiveChunk(c);
        setDisplayedPaperId(c.paper_content_id);
        setStale(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        if (/\b404\b/.test(msg)) {
          setActiveChunk(null);
          setStale(true);
        } else {
          toast.error("Couldn't load the cited paper");
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

  // Background prefetch: fetch the document (mode + content) for EVERY enabled
  // reference when the session's reference set changes, so each paper is ready
  // before the user opens it. `fetchedDocs` dedupes so each paper loads once.
  //
  // NOTE: deliberately NO per-effect `cancelled` flag. Under React StrictMode
  // the effect runs setup→cleanup→setup on the same instance; a `cancelled`
  // guard from the first setup would discard the in-flight fetch's result while
  // the dedup ref blocks the second setup from re-fetching — leaving the paper
  // stuck "Loading…". Letting `setDocByPaper` always run (a no-op if unmounted
  // in React 18+) + deduping on the ref is StrictMode-safe.
  useEffect(() => {
    for (const pid of refIds) {
      if (fetchedDocs.current.has(pid)) continue;
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
                  // Strip the dead polyfill.io/html5shiv scripts (they stall
                  // the load), then inject <base> so the paper's relative asset
                  // URLs (`asset/...`, served by the backend) resolve to the
                  // backend, not the app origin (srcdoc's default base).
                  html: withBaseHref(
                    stripDeadCdnScripts(await fetchPaperHtml(pid)),
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
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refIdsKey]);

  const handleTabClick = (pid: number) => {
    setDisplayedPaperId(pid);
    setActiveChunk(null);
    setStale(false);
    setOverflowOpen(false);
  };

  // Stay mounted while there are references to prefetch (even when closed) so
  // their content loads + caches for the session; render nothing only when
  // closed AND there's nothing to prefetch.
  if (!open && refs.length === 0) return null;

  const visibleTabs = refs.slice(0, MAX_VISIBLE_TABS);
  const overflowTabs = refs.slice(MAX_VISIBLE_TABS);
  const hasOverflow = overflowTabs.length > 0;

  // HTML papers stay mounted (hidden) for instant switching; PDF papers render
  // only when active (react-pdf is heavy). Content is cached either way.
  const htmlPapers = refIds.filter((pid) => docByPaper[pid]?.mode === "html");

  return (
    <aside
      aria-label="Citation Canvas"
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
                    aria-label="More papers"
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
        </div>

        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0"
          aria-label="Close canvas"
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
            This citation&apos;s passage is no longer available — the paper may
            have been re-indexed.
          </div>
        )}

        {activeChunk &&
          activeDoc?.mode === "pdf" &&
          activeChunk.paper_content_id === effectivePaperId && (
            <div
              role="status"
              className="m-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
            >
              Showing the source PDF — passage highlighting isn&apos;t available
              for PDF papers.
            </div>
          )}

        {/* Loading / error for the active paper */}
        {effectivePaperId != null && activeDoc == null && !stale && (
          <div className="p-4 text-xs text-muted-foreground">Loading paper…</div>
        )}
        {activeDoc?.status === "error" && (
          <div className="p-4 text-xs text-destructive">
            Couldn&apos;t load this paper.
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
                highlightDomId={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? activeChunk.dom_id
                    : null
                }
                highlightText={
                  isActive &&
                  activeChunk &&
                  activeChunk.paper_content_id === pid
                    ? activeChunk.text
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
                  toast.message("Couldn't locate this passage in the paper")
                }
              />
            </div>
          );
        })}

        {/* PDF papers: render only the active one (react-pdf is heavy). */}
        {effectivePaperId != null &&
          activeDoc?.mode === "pdf" &&
          activeDoc.status === "ready" &&
          activeDoc.pdfData != null && <PdfView data={activeDoc.pdfData} />}
      </div>
    </aside>
  );
}
