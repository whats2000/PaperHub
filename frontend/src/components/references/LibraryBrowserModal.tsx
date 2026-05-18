import { useState, useEffect, useCallback, useRef } from "react";
import { X } from "lucide-react";

import type { LibraryItem } from "@/types/domain";
import { listLibrary, attachFromLibrary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

interface Props {
  open: boolean;
  onClose: () => void;
  backendSessionId: number;
  onAttached: () => void;
}

export function LibraryBrowserModal({
  open,
  onClose,
  backendSessionId,
  onAttached,
}: Props) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [attachingId, setAttachingId] = useState<number | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevOpenRef = useRef(false);

  const fetchLibrary = useCallback(
    async (query: string) => {
      setLoading(true);
      try {
        const results = await listLibrary(backendSessionId, query || undefined);
        setItems(results);
      } catch {
        // silently ignore fetch errors in the modal
      } finally {
        setLoading(false);
      }
    },
    [backendSessionId],
  );

  // Debounced search on q/open change; reset q+items when modal first opens
  useEffect(() => {
    const justOpened = open && !prevOpenRef.current;
    prevOpenRef.current = open;

    if (!open) return;

    const searchQuery = justOpened ? "" : q;

    if (justOpened) {
      setQ("");
      setItems([]);
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(
      () => {
        void fetchLibrary(searchQuery);
      },
      justOpened ? 0 : 300,
    );

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q, open, fetchLibrary]);

  async function handleAttach(item: LibraryItem) {
    setAttachingId(item.paper_content_id);
    try {
      await attachFromLibrary(backendSessionId, item.paper_content_id);
      onAttached();
      onClose();
    } catch {
      // ignore
    } finally {
      setAttachingId(null);
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      aria-modal="true"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Modal panel */}
      <div
        role="dialog"
        aria-label="Add from Library"
        className="relative z-10 bg-background rounded-xl border border-border shadow-xl p-6 w-full max-w-lg max-h-[80vh] flex flex-col gap-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold leading-none tracking-tight">
            Add from Library
          </h2>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="Close library browser"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <Input
          placeholder="Search title or abstract…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search library"
        />

        <div
          className="overflow-y-auto flex-1 space-y-2 min-h-0"
          aria-live="polite"
        >
          {loading && (
            <p className="text-sm text-muted-foreground text-center py-4">
              Loading…
            </p>
          )}
          {!loading && items.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">
              {q ? "No results found." : "Library is empty for this session."}
            </p>
          )}
          {items.map((item) => (
            <div
              key={item.paper_content_id}
              className="flex items-start gap-2 rounded-lg border border-border p-3"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium leading-snug line-clamp-2">
                  {item.title}
                </p>
                <div className="mt-1 flex flex-wrap gap-1 items-center">
                  {item.year && (
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {item.year}
                    </span>
                  )}
                  {item.arxiv_id && (
                    <Badge variant="outline" className="text-xs">
                      arXiv
                    </Badge>
                  )}
                </div>
              </div>
              <Button
                size="sm"
                variant="secondary"
                disabled={attachingId === item.paper_content_id}
                onClick={() => void handleAttach(item)}
                aria-label={`Attach ${item.title}`}
              >
                {attachingId === item.paper_content_id ? "Adding…" : "Attach"}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
