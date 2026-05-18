import { useEffect, useState } from "react";
import { BookMarked, ExternalLink, Trash2, X } from "lucide-react";

import type { ReferenceItem } from "@/types/domain";
import { listSessionReferences, toggleReference, removeReference } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { LibraryBrowserModal } from "./LibraryBrowserModal";

interface Props {
  backendSessionId: number | null;
}

export function ReferenceSourcesDrawer({ backendSessionId }: Props) {
  const [open, setOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);

  const setReferences = useChatStore((s) => s.setReferences);
  const patchReferenceEnabled = useChatStore((s) => s.patchReferenceEnabled);
  const removeReferenceLocal = useChatStore((s) => s.removeReferenceLocal);
  const referencesBySession = useChatStore((s) => s.referencesBySession);

  const refs: ReferenceItem[] =
    backendSessionId !== null
      ? (referencesBySession[backendSessionId] ?? [])
      : [];

  async function refreshRefs() {
    if (backendSessionId === null) return;
    try {
      const items = await listSessionReferences(backendSessionId);
      setReferences(backendSessionId, items);
    } catch {
      // ignore
    }
  }

  // Refresh when session changes or drawer opens
  useEffect(() => {
    if (backendSessionId !== null) {
      void refreshRefs();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendSessionId]);

  useEffect(() => {
    if (open && backendSessionId !== null) {
      void refreshRefs();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function handleToggle(ref: ReferenceItem, enabled: boolean) {
    if (backendSessionId === null) return;
    // Optimistic update
    patchReferenceEnabled(backendSessionId, ref.papers_id, enabled);
    try {
      await toggleReference(ref.papers_id, enabled);
    } catch {
      // Revert on error
      patchReferenceEnabled(backendSessionId, ref.papers_id, !enabled);
    }
  }

  async function handleRemove(ref: ReferenceItem) {
    if (backendSessionId === null) return;
    // Optimistic remove
    removeReferenceLocal(backendSessionId, ref.papers_id);
    try {
      await removeReference(ref.papers_id);
    } catch {
      // Revert on error by refreshing
      void refreshRefs();
    }
  }

  // Hide entirely when no active backend session
  if (backendSessionId === null) return null;

  return (
    <>
      {/* Trigger button — fixed top-right */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`References (${refs.length})`}
        className="fixed right-4 top-4 z-10 inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-sm shadow-sm hover:bg-muted transition-colors"
      >
        <BookMarked className="h-4 w-4" />
        <span className="tabular-nums">{refs.length}</span>
      </button>

      {/* Drawer overlay */}
      {open && (
        <div className="fixed inset-0 z-40" aria-modal="true" role="dialog" aria-label="Reference Sources">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setOpen(false)}
          />

          {/* Panel */}
          <aside className="absolute right-0 top-0 h-full w-80 bg-background border-l border-border shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h2 className="font-semibold text-sm">Reference Sources</h2>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setOpen(false)}
                aria-label="Close references panel"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="px-4 py-2 border-b border-border">
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => setLibraryOpen(true)}
              >
                Add from library
              </Button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {refs.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8 px-4">
                  No papers attached to this session yet.
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {refs.map((ref) => (
                    <li key={ref.papers_id} className="flex items-start gap-2 px-4 py-3">
                      <Switch
                        checked={ref.enabled}
                        onCheckedChange={(checked) => void handleToggle(ref, checked)}
                        aria-label={`Toggle ${ref.title}`}
                      />
                      <div className="flex-1 min-w-0">
                        {ref.arxiv_id ? (
                          <a
                            href={`https://arxiv.org/abs/${ref.arxiv_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs font-medium hover:underline flex items-center gap-1 leading-snug line-clamp-2"
                          >
                            {ref.title}
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        ) : (
                          <span className="text-xs font-medium leading-snug line-clamp-2">
                            {ref.title}
                          </span>
                        )}
                        <div className="mt-0.5 flex items-center gap-1">
                          {ref.year && (
                            <span className="text-xs text-muted-foreground tabular-nums">
                              {ref.year}
                            </span>
                          )}
                          <Badge variant="outline" className="text-xs px-1 h-4">
                            {ref.kind}
                          </Badge>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => void handleRemove(ref)}
                        aria-label={`Remove ${ref.title}`}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>
      )}

      {/* Library Browser Modal */}
      <LibraryBrowserModal
        open={libraryOpen}
        onClose={() => setLibraryOpen(false)}
        backendSessionId={backendSessionId}
        onAttached={() => void refreshRefs()}
      />
    </>
  );
}
