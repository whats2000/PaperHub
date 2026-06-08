import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Trash2, X } from "lucide-react";
import { toast } from "sonner";

import type { LibraryItem } from "@/types/domain";
import {
  attachFromLibrary,
  deleteLibraryPaper,
  listLibrary,
  PaperInUseByOtherSessions,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

interface Props {
  open: boolean;
  onClose: () => void;
  backendSessionId: number;
  onAttached: () => void;
}

/** Two-stage destructive confirm for a single library row.
 *  - "prompt"    → first click on trash; show "Delete? [Yes][Cancel]"
 *  - "force"     → API returned 409; show "Still attached to N sessions. Force?"
 *                  Stores the session_count so the message is honest.
 *  - null        → idle. */
type DeleteState =
  | { stage: "prompt"; pcId: number }
  | { stage: "force"; pcId: number; sessionCount: number }
  | null;

export function LibraryBrowserModal({
  open,
  onClose,
  backendSessionId,
  onAttached,
}: Props) {
  const { t } = useTranslation("references");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [attachingId, setAttachingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteState, setDeleteState] = useState<DeleteState>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevOpenRef = useRef(false);

  // Close modal on Escape key — but only when no inline confirm is open
  // (otherwise Escape should cancel the confirm without closing the whole modal).
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (deleteState !== null) {
        setDeleteState(null);
        return;
      }
      onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose, deleteState]);

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
      setDeleteState(null);
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
      // Drop the just-attached row locally — it matches what
      // GET /papers/library would return next (the endpoint excludes
      // papers already in this session). Keeping the modal open lets
      // the user attach several refs in one visit; they close with X /
      // backdrop / Escape when done.
      setItems((prev) =>
        prev.filter((x) => x.paper_content_id !== item.paper_content_id),
      );
      onAttached();
      toast.success(t("toast.attached", { title: item.title }));
    } catch (err) {
      toast.error(
        t("toast.attachFailed", {
          title: item.title,
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setAttachingId(null);
    }
  }

  /**
   * Test-friendly destructive purge of paper_content + chunks + Chroma + cache.
   * Two-stage inline confirm — never window.confirm/alert. The proper UX
   * (batch ops, undo window) lands in a later phase when user-driven uploads
   * make this a routine operation.
   */
  async function performDelete(pcId: number, force: boolean) {
    setDeletingId(pcId);
    setDeleteState(null);
    try {
      await deleteLibraryPaper(pcId, force);
      setItems((prev) => prev.filter((x) => x.paper_content_id !== pcId));
      toast.success(
        force ? t("toast.deletedForced") : t("toast.deleted"),
      );
    } catch (err) {
      if (err instanceof PaperInUseByOtherSessions) {
        // Promote to force-confirm stage; keep the row visible.
        setDeleteState({
          stage: "force",
          pcId,
          sessionCount: err.session_count,
        });
        return;
      }
      toast.error(t("toast.deleteFailed"), {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDeletingId(null);
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
        aria-label={t("library.title")}
        className="relative z-10 bg-background rounded-xl border border-border shadow-xl p-6 w-full max-w-lg max-h-[80vh] flex flex-col gap-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold leading-none tracking-tight">
            {t("library.title")}
          </h2>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label={t("library.close")}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <Input
          placeholder={t("library.searchPlaceholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t("library.searchAria")}
        />

        <div
          className="overflow-y-auto flex-1 space-y-2 min-h-0"
          aria-live="polite"
        >
          {loading && (
            <p className="text-sm text-muted-foreground text-center py-4">
              {t("library.loading")}
            </p>
          )}
          {!loading && items.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-4">
              {q ? t("library.noResults") : t("library.emptyForSession")}
            </p>
          )}
          {items.map((item) => {
            const isConfirming =
              deleteState !== null &&
              deleteState.pcId === item.paper_content_id;
            const isDeleting = deletingId === item.paper_content_id;
            return (
              <div
                key={item.paper_content_id}
                className="rounded-lg border border-border p-3"
              >
                <div className="flex items-start gap-2">
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
                    disabled={attachingId === item.paper_content_id || isConfirming || isDeleting}
                    onClick={() => void handleAttach(item)}
                    aria-label={t("library.attachAria", { title: item.title })}
                  >
                    {attachingId === item.paper_content_id
                      ? t("library.adding")
                      : t("library.attach")}
                  </Button>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    disabled={isDeleting || isConfirming}
                    onClick={() =>
                      setDeleteState({ stage: "prompt", pcId: item.paper_content_id })
                    }
                    aria-label={t("library.deleteAria", { title: item.title })}
                    title={t("library.deleteTitle")}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>

                {isConfirming && deleteState.stage === "prompt" && (
                  <div
                    role="alertdialog"
                    aria-label={t("library.confirmDeleteAria")}
                    className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs space-y-2"
                  >
                    <p className="text-foreground">
                      {t("library.confirmDeleteBody")}
                    </p>
                    <div className="flex justify-end gap-2">
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => setDeleteState(null)}
                      >
                        {t("library.cancel")}
                      </Button>
                      <Button
                        size="xs"
                        variant="destructive"
                        onClick={() => void performDelete(item.paper_content_id, false)}
                      >
                        {t("library.delete")}
                      </Button>
                    </div>
                  </div>
                )}

                {isConfirming && deleteState.stage === "force" && (
                  <div
                    role="alertdialog"
                    aria-label={t("library.confirmForceDeleteAria")}
                    className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs space-y-2"
                  >
                    <p className="text-foreground">
                      {t("library.forceDeleteBody", {
                        count: deleteState.sessionCount,
                      })}
                    </p>
                    <div className="flex justify-end gap-2">
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => setDeleteState(null)}
                      >
                        {t("library.cancel")}
                      </Button>
                      <Button
                        size="xs"
                        variant="destructive"
                        onClick={() => void performDelete(item.paper_content_id, true)}
                      >
                        {t("library.forceDelete")}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
