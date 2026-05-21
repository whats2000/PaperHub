import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "next-themes";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { getChunk, getDocumentMode } from "@/lib/api";
import { findAndHighlight } from "@/lib/findAndHighlight";
import { applyIframeTheme } from "@/lib/applyIframeTheme";
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
  // Per-paper resolved view mode. This Record IS the keep-alive cache: every
  // paper we resolve a mode for gets an iframe that stays mounted, so switching
  // back is instant (no re-parse / MathJax re-render).
  const [modeByPaper, setModeByPaper] = useState<Record<number, "pdf" | "html">>(
    {},
  );
  const [overflowOpen, setOverflowOpen] = useState(false);

  // paper_content_id -> its mounted iframe element
  const iframeEls = useRef<Map<number, HTMLIFrameElement>>(new Map());

  const firstEnabledRef = refs.length > 0 ? refs[0] : null;
  const effectivePaperId =
    displayedPaperId ?? firstEnabledRef?.paper_content_id ?? null;
  const activeMode =
    effectivePaperId != null ? (modeByPaper[effectivePaperId] ?? null) : null;

  const titleFor = (pid: number): string =>
    allRefs.find((r) => r.paper_content_id === pid)?.title ?? `Paper ${pid}`;
  // SAME-ORIGIN, relative URL (proxied by Vite to the backend). A cross-origin
  // iframe (backend :8000 vs app :5173) has a null contentDocument, which
  // breaks highlighting + dark-mode injection. The relative path keeps the
  // iframe same-origin so we can read its document.
  const srcFor = (pid: number, m: "pdf" | "html"): string =>
    `/papers/content/${pid}/${m === "pdf" ? "pdf" : "html"}`;

  // Resolve a clicked citation → its paper + highlight target. Keyed on
  // requestNonce so the same chunk re-clicked re-resolves. Clears the request
  // when done so a later browse-mode open doesn't re-jump here.
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

  // Resolve the document mode for the displayed paper once (cached in
  // modeByPaper). Re-runs when modeByPaper changes but early-returns if already
  // known, so it never loops or re-fetches.
  useEffect(() => {
    if (effectivePaperId == null || modeByPaper[effectivePaperId] != null) return;
    const pid = effectivePaperId;
    let cancelled = false;
    getDocumentMode(pid)
      .then((m) => {
        if (!cancelled) setModeByPaper((prev) => ({ ...prev, [pid]: m }));
      })
      .catch(() => {
        if (!cancelled) setModeByPaper((prev) => ({ ...prev, [pid]: "html" }));
      });
    return () => {
      cancelled = true;
    };
  }, [effectivePaperId, modeByPaper]);

  // Re-apply the dark/light treatment to every loaded HTML iframe when the
  // theme toggles (or new papers mount).
  useEffect(() => {
    for (const [pid, el] of iframeEls.current) {
      const doc = el.contentDocument;
      if (doc?.body && modeByPaper[pid] === "html") {
        applyIframeTheme(doc, isDark);
      }
    }
  }, [isDark, modeByPaper]);

  // Highlight the active paper's passage when it (or the active paper) changes
  // and that iframe is already loaded (same-paper re-click / mode-resolve).
  useEffect(() => {
    if (
      !activeChunk ||
      activeMode !== "html" ||
      effectivePaperId == null ||
      activeChunk.paper_content_id !== effectivePaperId
    ) {
      return;
    }
    const doc = iframeEls.current.get(effectivePaperId)?.contentDocument;
    if (!doc || doc.readyState !== "complete" || !doc.body) return;
    const found = findAndHighlight(doc, activeChunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  }, [activeChunk, effectivePaperId, activeMode]);

  const handleIframeLoad = (pid: number): void => {
    const doc = iframeEls.current.get(pid)?.contentDocument;
    if (!doc) return;
    if (modeByPaper[pid] === "html") applyIframeTheme(doc, isDark);
    // Highlight only when this is the active paper opened from a citation.
    if (
      pid !== effectivePaperId ||
      modeByPaper[pid] !== "html" ||
      !activeChunk ||
      activeChunk.paper_content_id !== pid ||
      !doc.body
    ) {
      return;
    }
    const found = findAndHighlight(doc, activeChunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  };

  const handleTabClick = (pid: number) => {
    setDisplayedPaperId(pid);
    setActiveChunk(null);
    setStale(false);
    setOverflowOpen(false);
  };

  if (!open) return null;

  const visibleTabs = refs.slice(0, MAX_VISIBLE_TABS);
  const overflowTabs = refs.slice(MAX_VISIBLE_TABS);
  const hasOverflow = overflowTabs.length > 0;

  // Every paper we've resolved a mode for keeps its iframe mounted.
  const mountedPapers = Object.keys(modeByPaper).map(Number);

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
          activeMode === "pdf" &&
          activeChunk.paper_content_id === effectivePaperId && (
            <div
              role="status"
              className="m-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
            >
              Showing the source PDF — passage highlighting isn&apos;t available
              for PDF papers.
            </div>
          )}

        {/* Keep-alive iframes: one per visited paper, only the active visible. */}
        {mountedPapers.map((pid) => {
          const m = modeByPaper[pid];
          if (m == null) return null;
          const isActive = pid === effectivePaperId;
          return (
            <iframe
              key={pid}
              ref={(el) => {
                if (el) iframeEls.current.set(pid, el);
                else iframeEls.current.delete(pid);
              }}
              title={`Citation Canvas — ${titleFor(pid)}`}
              data-active={isActive}
              src={srcFor(pid, m)}
              onLoad={() => handleIframeLoad(pid)}
              // HTML: sandbox (allow-scripts for MathJax, allow-same-origin so
              // we can read the doc to highlight). PDF: no sandbox — the
              // browser's native PDF viewer can be blocked by the sandbox.
              sandbox={m === "pdf" ? undefined : "allow-scripts allow-same-origin"}
              hidden={!isActive}
              className="h-full w-full flex-1 bg-white"
            />
          );
        })}
      </div>
    </aside>
  );
}
