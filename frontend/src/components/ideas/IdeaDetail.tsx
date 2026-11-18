import { useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  CircleCheck,
  CircleX,
  EyeOff,
  FlaskConical,
  MessageSquare,
  MessagesSquare,
  Star,
  Undo2,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useLeaving } from "@/components/canvas/useLeaving";
import { DismissForm } from "@/components/ideas/DismissForm";
import { Button } from "@/components/ui/button";
import { NoteEditor } from "@/components/notes/NoteEditor";
import { openIdeaChat, sectionLabel, useReasonText } from "@/lib/ideaActions";
import { prefersReducedMotion } from "@/lib/motion";
import { blockText } from "@/lib/notes/ops";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";
import { useIdeationStore, matchesFilter } from "@/store/ideation";
import { useNotesStore } from "@/store/notes";
import type { DismissReason, Idea, IdeaStatus } from "@/types/ideation";

const STATUS_BUTTONS: { status: IdeaStatus; icon: LucideIcon; fallback: string }[] = [
  { status: "saved", icon: Star, fallback: "Save" },
  { status: "trying", icon: FlaskConical, fallback: "Trying" },
  { status: "worked", icon: CircleCheck, fallback: "Worked" },
  { status: "failed", icon: CircleX, fallback: "Didn't work" },
];

const STATUS_FALLBACK: Record<IdeaStatus, string> = {
  new: "New",
  saved: "Saved",
  trying: "Trying",
  worked: "Worked",
  failed: "Failed",
  dismissed: "Dismissed",
};

/** The open "Dismissed" toast per idea, so the banner's Undo can retire it. */
const undoToasts = new Map<number, string | number>();

function SectionLink({
  section,
  chunkIds,
  onOpen,
  inReader = false,
}: {
  section: string;
  chunkIds: number[];
  onOpen: () => void;
  /** Opens in the page's reader rather than in the idea's chat. */
  inReader?: boolean;
}) {
  const { t } = useTranslation("ideas");
  if (chunkIds.length === 0) return null;
  const label = sectionLabel(section);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={
        inReader
          ? t("detail.readSection", "Opens {{section}} in the reader", { section: label })
          : t("detail.openSection", "Opens the idea's chat with {{section}} in the Citation Canvas", { section: label })
      }
      className="inline-flex max-w-full items-center gap-1 rounded border border-border bg-card px-1.5 py-0.5 text-[11.5px] transition-colors hover:border-primary hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      <BookOpen className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </button>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="mt-3 mb-1.5 text-[11.5px] text-muted-foreground">{children}</div>;
}

/**
 * The idea drawer's management block: cited-section links, the status
 * buttons, the autosaving note, opening a chat and dismissing with reasons.
 */
export function IdeaDetail({
  idea,
  onReadSection,
}: {
  idea: Idea;
  /** Read a cited section in place (the Ideas page's reader); without it, the idea's chat opens it. */
  onReadSection?: (docId: number, chunkIds: number[]) => void;
}) {
  const { t } = useTranslation("ideas");
  const patchIdea = useIdeationStore((s) => s.patchIdea);
  const dismissIdea = useIdeationStore((s) => s.dismissIdea);
  const reasonText = useReasonText();
  const statusLabel = (s: IdeaStatus) => t(`graph.status.${s}`, STATUS_FALLBACK[s]);

  // ---- Note: the idea's note opens in the notes editor, which syncs its own edits.
  const ideaId = idea.id;
  const loadTarget = useNotesStore((s) => s.loadTarget);
  const [noteLoad, setNoteLoad] = useState<{ id: number; noteId: number | null; failed: boolean } | null>(null);
  const [loadTry, setLoadTry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    loadTarget("idea", ideaId).then(
      (noteId) => {
        if (!cancelled) setNoteLoad({ id: ideaId, noteId, failed: false });
      },
      () => {
        if (!cancelled) setNoteLoad({ id: ideaId, noteId: null, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ideaId, loadTarget, loadTry]);
  const noteId = noteLoad?.id === ideaId ? noteLoad.noteId : null;
  const noteFailed = noteLoad?.id === ideaId && noteLoad.failed;
  // The status line fades out over the editor fading in, then goes (at once under reduced motion).
  const [statusGone, setStatusGone] = useState<number | null>(null);
  // Loading again (another idea, then back): the line fades out again too.
  if (noteId === null && statusGone !== null) setStatusGone(null);
  const showStatus = noteId === null || (statusGone !== noteId && !prefersReducedMotion());
  // "Couldn't load" and "Loading" cross-fade: the one replaced fades out.
  const lineId = showStatus ? (noteFailed ? "failed" : "loading") : null;
  const statusLines = useLeaving(useMemo(() => (lineId ? [{ id: lineId }] : []), [lineId]));
  // The kanban card and the drawer excerpt read `idea.note`: keep it the note's
  // plain text, by the backend's rule (`text or block_text`, joined by blank
  // lines), also when a graph refetch brings an older copy back.
  const blocks = useNotesStore((s) => (noteId === null ? undefined : s.notes[noteId]?.blocks));
  const listedNote = idea.note;
  useEffect(() => {
    if (!blocks) return;
    const text = blocks
      .map((b) => (b.text || blockText(b.type, b.props, b.content)).trim())
      .filter(Boolean)
      .join("\n\n");
    if (text === listedNote) return;
    useIdeationStore.setState((s) =>
      s.graph?.ideas.some((i) => i.id === ideaId && i.note !== text)
        ? { graph: { ...s.graph, ideas: s.graph.ideas.map((i) => (i.id === ideaId ? { ...i, note: text } : i)) } }
        : {},
    );
  }, [blocks, ideaId, listedNote]);

  // ---- Status
  const [busy, setBusy] = useState(false);
  const setStatus = async (status: IdeaStatus) => {
    // Pressing the current status again takes it back to new.
    const next = idea.status === status ? "new" : status;
    setBusy(true);
    try {
      await patchIdea(idea.id, { status: next });
      // Keep the idea on screen when the filter would hide it now.
      const store = useIdeationStore.getState();
      if (!matchesFilter(next, store.filter)) store.setFilter("active");
      toast.success(
        t("detail.statusToast", "Status set to “{{status}}”, recorded as ideation feedback", {
          status: statusLabel(next),
        }),
      );
    } catch {
      toast.error(t("detail.statusFailed", "Couldn't update the idea"));
    } finally {
      setBusy(false);
    }
  };

  // ---- Dismiss + Undo
  const [formOpen, setFormOpen] = useState(false);
  const undoing = useIdeationStore((s) => s.undoingIdeaIds.includes(idea.id));
  // Toast Undo and banner Undo are one action: the first wins, the other goes away or no-ops.
  const undo = (id: number) => {
    const toastId = undoToasts.get(id);
    if (toastId !== undefined) {
      undoToasts.delete(id);
      toast.dismiss(toastId);
    }
    return useIdeationStore
      .getState()
      .undoDismiss(id)
      .catch(() => toast.error(t("detail.undoFailed", "Couldn't undo the dismissal")));
  };
  const confirmDismiss = async (reasons: DismissReason[], dismissNote: string) => {
    setBusy(true);
    try {
      await dismissIdea(idea.id, reasons, dismissNote);
    } catch {
      toast.error(t("dismiss.failed", "Couldn't dismiss the idea"));
      setBusy(false);
      return;
    }
    setBusy(false);
    setFormOpen(false);
    const id = idea.id;
    const toastId = toast(t("dismiss.toast", "Dismissed “{{title}}”", { title: idea.title }), {
      action: {
        label: t("detail.undo", "Undo"),
        onClick: () => {
          undoToasts.delete(id);
          void undo(id);
        },
      },
      duration: 6000,
      onAutoClose: () => undoToasts.delete(id),
      onDismiss: () => undoToasts.delete(id),
    });
    undoToasts.set(id, toastId);
  };

  // ---- Chat + Canvas
  // "Continue" only when the latest linked chat is in the sidebar to reopen;
  // otherwise the button opens (creates) a new one.
  const latestSession = idea.session_ids.at(-1);
  const canContinue = useChatStore(
    (s) => latestSession !== undefined && s.sessions.some((x) => x.backend_session_id === latestSession),
  );
  const [opening, setOpening] = useState(false);
  const openChat = async (reuseLatest: boolean) => {
    setOpening(true);
    try {
      await openIdeaChat(idea, reuseLatest);
    } catch {
      toast.error(t("detail.chatFailed", "Couldn't open the chat"));
    } finally {
      setOpening(false);
    }
  };
  const openSection = async (chunkIds: number[]) => {
    try {
      // The canvas shows a chat session's documents: open the idea's chat first.
      await openIdeaChat(idea, true);
    } catch {
      toast.error(t("detail.chatFailed", "Couldn't open the chat"));
      return;
    }
    // After the session switch has rendered (it closes the canvas of the previous chat).
    const first = chunkIds[0];
    if (first === undefined) return;
    const last = chunkIds[chunkIds.length - 1];
    setTimeout(() => useCanvasStore.getState().openCitation(first, last), 0);
  };

  const dismissed = idea.status === "dismissed";
  const hasLinks = idea.source_chunk_ids.length > 0 || idea.target_chunk_ids.length > 0;

  return (
    <div data-testid="idea-detail">
      {hasLinks && (
        <>
          <Label>{t("detail.sections", "Cited sections")}</Label>
          <div className="flex flex-wrap gap-1.5">
            <SectionLink
              section={idea.source_section}
              chunkIds={idea.source_chunk_ids}
              inReader={!!onReadSection}
              onOpen={() =>
                onReadSection
                  ? onReadSection(idea.source_doc, idea.source_chunk_ids)
                  : void openSection(idea.source_chunk_ids)
              }
            />
            {idea.target_section && idea.target_doc !== null && (
              <SectionLink
                section={idea.target_section}
                chunkIds={idea.target_chunk_ids}
                inReader={!!onReadSection}
                onOpen={() => {
                  const target = idea.target_doc;
                  if (onReadSection && target !== null) onReadSection(target, idea.target_chunk_ids);
                  else void openSection(idea.target_chunk_ids);
                }}
              />
            )}
          </div>
        </>
      )}

      <Label>{t("detail.status", "Status")}</Label>
      {dismissed ? (
        <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-2.5 py-2 text-[13px] animate-in fade-in-0 duration-150 motion-reduce:animate-none">
          <EyeOff className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            {t("detail.dismissed", "Dismissed · {{reasons}}", {
              reasons: idea.dismiss_reasons.length
                ? reasonText(idea.dismiss_reasons)
                : t("detail.noReason", "No reason given"),
            })}
          </span>
          <Button variant="outline" size="sm" disabled={undoing} onClick={() => void undo(idea.id)}>
            <Undo2 aria-hidden="true" />
            {t("detail.undo", "Undo")}
          </Button>
        </div>
      ) : (
        <div
          role="group"
          aria-label={t("detail.statusAria", "Idea status")}
          className="grid grid-cols-4 gap-1 animate-in fade-in-0 duration-150 motion-reduce:animate-none"
        >
          {STATUS_BUTTONS.map(({ status, icon: Icon, fallback }) => {
            const on = idea.status === status;
            return (
              <button
                key={status}
                type="button"
                aria-pressed={on}
                disabled={busy}
                onClick={() => void setStatus(status)}
                className={cn(
                  "flex flex-col items-center gap-0.5 rounded-md border border-border px-1 py-1.5 text-[11.5px] text-muted-foreground transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60",
                  on && "border-foreground bg-accent text-foreground",
                )}
              >
                <Icon className="size-3.5" aria-hidden="true" />
                {t(`detail.setStatus.${status}`, fallback)}
              </button>
            );
          })}
        </div>
      )}

      <Label>{t("detail.note", "Note")}</Label>
      <div data-testid="idea-note" className="relative max-h-80 min-h-16 overflow-auto rounded-md border bg-background">
        {showStatus && (
          <div
            data-testid="idea-note-status"
            data-closed={noteId !== null ? "" : undefined}
            className={cn(
              "transition-opacity duration-200 ease-out data-closed:opacity-0 motion-reduce:transition-none",
              noteId !== null && "pointer-events-none absolute inset-x-0 top-0",
            )}
            onTransitionEnd={(e) => {
              // Its own fade only: a child's transition bubbling up is not it.
              if (e.target === e.currentTarget && noteId !== null) setStatusGone(noteId);
            }}
          >
            {statusLines.map((line) => {
              const motion = line.leaving
                ? "pointer-events-none absolute inset-x-0 top-0 animate-out fade-out-0 fill-mode-forwards duration-200 motion-reduce:animate-none"
                : "animate-in fade-in-0 motion-reduce:animate-none";
              const shared = {
                "aria-hidden": line.leaving || undefined,
                "data-leaving": line.leaving ? "" : undefined,
              };
              return line.id === "failed" ? (
                <div
                  key="failed"
                  role={line.leaving ? undefined : "alert"}
                  {...shared}
                  className={cn("flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground", motion)}
                >
                  <span className="min-w-0 flex-1">{t("detail.noteLoadFailed", "Couldn't load the note.")}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setNoteLoad(null);
                      setLoadTry((n) => n + 1);
                    }}
                  >
                    {t("detail.noteRetry", "Try again")}
                  </Button>
                </div>
              ) : (
                <div
                  key="loading"
                  role={line.leaving ? undefined : "status"}
                  {...shared}
                  className={cn("px-3 py-2 text-sm text-muted-foreground", motion)}
                >
                  {t("detail.noteLoading", "Loading the note…")}
                </div>
              );
            })}
          </div>
        )}
        {noteId !== null && <NoteEditor noteId={noteId} variant="pane" />}
      </div>

      <div className="mt-4 flex gap-2">
        <Button className="flex-1" disabled={opening} onClick={() => void openChat(true)}>
          <MessageSquare aria-hidden="true" />
          {canContinue ? t("detail.continueChat", "Continue chat") : t("detail.openChat", "Open a chat")}
        </Button>
        {canContinue && (
          <Button
            variant="outline"
            disabled={opening}
            onClick={() => void openChat(false)}
            title={t("detail.newChatHint", "Start another chat about this idea")}
            className="animate-in fade-in-0 duration-150 motion-reduce:animate-none"
          >
            <MessagesSquare aria-hidden="true" />
            {t("detail.newChat", "New chat")}
          </Button>
        )}
        {!dismissed && (
          <Button variant="outline" aria-expanded={formOpen} onClick={() => setFormOpen((o) => !o)}>
            <EyeOff aria-hidden="true" />
            {t("detail.dismiss", "Dismiss")}
          </Button>
        )}
      </div>
      {/* Always mounted so closing (cancel or confirm) collapses instead of vanishing. */}
      <DismissForm
        open={formOpen && !dismissed}
        busy={busy}
        onCancel={() => setFormOpen(false)}
        onConfirm={(r, n) => void confirmDismiss(r, n)}
      />
    </div>
  );
}
