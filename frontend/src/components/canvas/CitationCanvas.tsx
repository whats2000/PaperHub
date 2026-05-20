import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";

import { useCanvasStore } from "@/store/canvas";
import { getChunk, API_BASE_URL } from "@/lib/api";
import { findAndHighlight } from "@/lib/findAndHighlight";
import { Button } from "@/components/ui/button";
import type { ChunkResolution } from "@/types/domain";

export function CitationCanvas() {
  const open = useCanvasStore((s) => s.open);
  const chunkId = useCanvasStore((s) => s.chunkId);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);

  const [chunk, setChunk] = useState<ChunkResolution | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  // Tracks the src URL that the iframe has *actually* finished loading.
  // Updated in onLoad before highlighting, so we never search a stale document.
  const loadedSrcRef = useRef<string | null>(null);

  // Fetch the chunk whenever the canvas opens with a new chunkId.
  // Cancel stale requests so a quick open→open→close doesn't clobber state.
  useEffect(() => {
    if (!open || chunkId == null) return;
    let cancelled = false;
    getChunk(chunkId)
      .then((c) => {
        if (!cancelled) setChunk(c);
      })
      .catch(() => {
        if (!cancelled) toast.error("Couldn't load the cited paper");
      });
    return () => {
      cancelled = true;
    };
  }, [open, chunkId]);

  // Highlight effect keyed on chunk.
  //
  // Two cases:
  //   A) Cross-paper navigation: the iframe src changes → onLoad fires → we
  //      highlight in the onLoad handler below. The [chunk] effect must NOT
  //      run here: between the src change and the new onLoad, contentDocument
  //      is still the OLD paper's fully-loaded DOM, so findAndHighlight would
  //      search the wrong document and fire a spurious miss-toast.
  //   B) Same-paper navigation: src is unchanged, onLoad does NOT re-fire.
  //      loadedSrcRef.current === src is already true (set by the previous
  //      onLoad), so we detect this and call findAndHighlight directly.
  //
  // findAndHighlight always calls clearHighlight first (idempotent), so calling
  // it from both this effect and onLoad is safe — a double-call just re-marks
  // the same spot, which is a no-op from the user's perspective.
  useEffect(() => {
    if (!chunk) return;
    // Guard: only highlight when the iframe has actually loaded THIS paper's src.
    // If loadedSrcRef.current !== src, the iframe is mid-navigation and still
    // showing a stale document — onLoad will handle highlighting once it fires.
    const targetSrc = `${API_BASE_URL}/papers/content/${chunk.paper_content_id}/html`;
    if (loadedSrcRef.current !== targetSrc) return;
    const doc = iframeRef.current?.contentDocument;
    if (!doc || doc.readyState !== "complete" || !doc.body) return;
    const found = findAndHighlight(doc, chunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  }, [chunk]);

  if (!open) return null;

  const src =
    chunk == null
      ? undefined
      : `${API_BASE_URL}/papers/content/${chunk.paper_content_id}/html`;

  const handleIframeLoad = (): void => {
    if (chunk == null) return;
    const doc = iframeRef.current?.contentDocument;
    if (!doc || !doc.body) return;
    // Record that the iframe has now loaded the target src BEFORE highlighting,
    // so the [chunk] effect's guard (loadedSrcRef.current === targetSrc) becomes
    // true for any same-paper re-highlight that follows.
    loadedSrcRef.current = src ?? null;
    // findAndHighlight is idempotent (clears then re-adds), so calling it here
    // AND in the [chunk] effect above for the same chunk is harmless.
    const found = findAndHighlight(doc, chunk.text);
    if (!found) toast.message("Couldn't locate this passage in the paper");
  };

  return (
    <aside
      className="fixed right-0 top-0 z-40 flex h-full w-[min(560px,45vw)] flex-col border-l border-border bg-card shadow-xl"
      aria-label="Citation Canvas"
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="truncate text-sm font-medium">
          {chunk?.section ? `§ ${chunk.section}` : "Cited passage"}
        </span>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-7 w-7"
          aria-label="Close canvas"
          onClick={closeCanvas}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>
      {src && (
        <iframe
          ref={iframeRef}
          title="Citation Canvas"
          src={src}
          onLoad={handleIframeLoad}
          sandbox="allow-scripts allow-same-origin"
          className="h-full w-full flex-1 bg-white"
        />
      )}
    </aside>
  );
}
