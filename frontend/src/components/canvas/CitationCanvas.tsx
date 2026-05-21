import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { getChunk, getDocumentMode, API_BASE_URL } from "@/lib/api";
import { findAndHighlight } from "@/lib/findAndHighlight";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { ChunkResolution, ReferenceItem } from "@/types/domain";

const MAX_VISIBLE_TABS = 3;

export function CitationCanvas() {
  const open = useCanvasStore((s) => s.open);
  const requestedChunkId = useCanvasStore((s) => s.requestedChunkId);
  const requestNonce = useCanvasStore((s) => s.requestNonce);
  const consumeCitation = useCanvasStore((s) => s.consumeCitation);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);

  // Derive active session's enabled references (mirror ReferenceSourcesPanel)
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const referencesBySession = useChatStore((s) => s.referencesBySession);

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

  // Local state
  const [displayedPaperId, setDisplayedPaperId] = useState<number | null>(null);
  const [activeChunk, setActiveChunk] = useState<ChunkResolution | null>(null);
  const [stale, setStale] = useState(false);
  const [mode, setMode] = useState<"pdf" | "html" | null>(null);
  const [overflowOpen, setOverflowOpen] = useState(false);

  const iframeRef = useRef<HTMLIFrameElement>(null);
  // Tracks the src URL that the iframe has *actually* finished loading.
  const loadedSrcRef = useRef<string | null>(null);

  const firstEnabledRef = refs.length > 0 ? refs[0] : null;
  const effectivePaperId = displayedPaperId ?? firstEnabledRef?.paper_content_id ?? null;

  // Resolve effect — keyed on requestNonce so same chunk re-clicked re-resolves
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
        // Clear the request so a later browse-mode open (References button,
        // which doesn't set a new request) doesn't re-resolve this chunk and
        // jump back to it — even though the canvas remounts on each open.
        if (!cancelled) consumeCitation();
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestNonce]);

  // Mode effect — keyed on effectivePaperId
  useEffect(() => {
    if (effectivePaperId == null) return;
    let cancelled = false;
    getDocumentMode(effectivePaperId)
      .then((m) => {
        if (!cancelled) setMode(m);
      })
      .catch(() => {
        if (!cancelled) setMode("html");
      });
    return () => {
      cancelled = true;
      // Reset mode so the next paper doesn't briefly show the old mode's iframe.
      setMode(null);
    };
  }, [effectivePaperId]);

  const src =
    effectivePaperId != null && mode != null
      ? `${API_BASE_URL}/papers/content/${effectivePaperId}/${mode === "pdf" ? "pdf" : "html"}`
      : undefined;

  // Highlight effect — same-paper re-highlight when the iframe is already loaded
  useEffect(() => {
    if (!activeChunk || mode !== "html") return;
    if (activeChunk.paper_content_id !== effectivePaperId) return;
    if (!src || loadedSrcRef.current !== src) return;
    const doc = iframeRef.current?.contentDocument;
    if (!doc || doc.readyState !== "complete" || !doc.body) return;
    const found = findAndHighlight(doc, activeChunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  }, [activeChunk, effectivePaperId, mode, src]);

  const handleIframeLoad = (): void => {
    if (src == null) return;
    loadedSrcRef.current = src;
    if (mode !== "html" || !activeChunk) return;
    if (activeChunk.paper_content_id !== effectivePaperId) return;
    const doc = iframeRef.current?.contentDocument;
    if (!doc || !doc.body) return;
    const found = findAndHighlight(doc, activeChunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  };

  const handleTabClick = (pcid: number) => {
    setDisplayedPaperId(pcid);
    setActiveChunk(null);
    setStale(false);
    setOverflowOpen(false);
  };

  if (!open) return null;

  const visibleTabs = refs.slice(0, MAX_VISIBLE_TABS);
  const overflowTabs = refs.slice(MAX_VISIBLE_TABS);
  const hasOverflow = overflowTabs.length > 0;

  return (
    <aside
      aria-label="Citation Canvas"
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
        {/* Stale/404 notice */}
        {stale && (
          <div
            role="status"
            className="m-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          >
            This citation&apos;s passage is no longer available — the paper may
            have been re-indexed.
          </div>
        )}

        {/* PDF citation notice */}
        {activeChunk &&
          mode === "pdf" &&
          activeChunk.paper_content_id === effectivePaperId && (
            <div
              role="status"
              className="m-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
            >
              Showing the source PDF — passage highlighting isn&apos;t available
              for PDF papers.
            </div>
          )}

        {/* iframe — only when we know the mode */}
        {src != null && (
          <iframe
            ref={iframeRef}
            title="Citation Canvas"
            src={src}
            onLoad={handleIframeLoad}
            sandbox="allow-scripts allow-same-origin"
            className="h-full w-full flex-1 bg-white"
          />
        )}
      </div>
    </aside>
  );
}
