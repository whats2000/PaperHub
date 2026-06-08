import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink, Loader2, PanelRight, Trash2 } from "lucide-react";

import type { ReferenceItem } from "@/types/domain";
import {
  listSessionReferences,
  removeReference,
  toggleReference,
} from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { LibraryBrowserModal } from "./LibraryBrowserModal";

interface Props {
  /** Frontend session id whose references should be shown. */
  frontendSessionId: number | null;
}

/**
 * Inline panel rendering the active session's reference list.  Designed to
 * live inside the left Sidebar's References tab; carries no drawer chrome of
 * its own.  The right edge of the screen is reserved for Plan D's Citation
 * Canvas, Plan F's Slide Preview, and Plan G's Compare Split.
 */
export function ReferenceSourcesPanel({ frontendSessionId }: Props) {
  const { t } = useTranslation("references");
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);

  const ensureBackendSession = useChatStore((s) => s.ensureBackendSession);
  const setReferences = useChatStore((s) => s.setReferences);
  const patchReferenceEnabled = useChatStore((s) => s.patchReferenceEnabled);
  const removeReferenceLocal = useChatStore((s) => s.removeReferenceLocal);
  const referencesBySession = useChatStore((s) => s.referencesBySession);
  const sessions = useChatStore((s) => s.sessions);
  const openPaperInCanvas = useCanvasStore((s) => s.openPaper);
  // The paper currently shown on the canvas (null when the canvas is closed),
  // so the matching row reads as active.
  const activePaperId = useCanvasStore((s) => s.activePaperId);

  const activeSession =
    frontendSessionId !== null
      ? (sessions.find((s) => s.id === frontendSessionId) ?? null)
      : null;
  const backendSessionId = activeSession?.backend_session_id ?? null;

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

  // Refresh whenever this session's backend id is known.
  useEffect(() => {
    if (backendSessionId !== null) {
      void refreshRefs();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendSessionId]);

  // Lazy-create the backend session the first time the user wants to add a
  // paper.  Most paths reach here only after a chat turn has already created
  // it, but the "Add from library" affordance must work before the user has
  // sent any message.
  async function handleOpenLibrary() {
    if (frontendSessionId === null) return;
    setSessionLoading(true);
    try {
      await ensureBackendSession(frontendSessionId);
      setLibraryOpen(true);
    } finally {
      setSessionLoading(false);
    }
  }

  async function handleToggle(ref: ReferenceItem, enabled: boolean) {
    if (backendSessionId === null) return;
    patchReferenceEnabled(backendSessionId, ref.papers_id, enabled);
    try {
      await toggleReference(ref.papers_id, enabled);
    } catch {
      patchReferenceEnabled(backendSessionId, ref.papers_id, !enabled);
    }
  }

  async function handleRemove(ref: ReferenceItem) {
    if (backendSessionId === null) return;
    removeReferenceLocal(backendSessionId, ref.papers_id);
    try {
      await removeReference(ref.papers_id);
    } catch {
      void refreshRefs();
    }
  }

  if (activeSession === null) {
    return (
      <p className="text-xs text-muted-foreground text-center px-4 py-8">
        {t("panel.noSession")}
      </p>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Add-from-library affordance */}
      <div className="px-3 py-2 border-b border-border">
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => void handleOpenLibrary()}
          disabled={sessionLoading}
        >
          {sessionLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
          ) : null}
          {t("panel.addFromLibrary")}
        </Button>
      </div>

      {/* Reference list */}
      <div className="flex-1 overflow-y-auto">
        {refs.length === 0 ? (
          <p className="text-xs text-muted-foreground text-center px-4 py-8">
            {t("panel.empty")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {refs.map((ref) => {
              const isActiveOnCanvas =
                activePaperId === ref.paper_content_id;
              return (
              <li
                key={ref.papers_id}
                className={
                  "flex items-start gap-2 px-3 py-2.5 transition-colors" +
                  (isActiveOnCanvas ? " bg-primary/5" : "")
                }
              >
                <Switch
                  checked={ref.enabled}
                  onCheckedChange={(checked) => void handleToggle(ref, checked)}
                  aria-label={t("panel.toggleAria", { title: ref.title })}
                />
                <div className="flex-1 min-w-0">
                  {ref.arxiv_id ? (
                    <a
                      href={`https://arxiv.org/abs/${ref.arxiv_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium hover:underline flex items-start gap-1 leading-snug line-clamp-2"
                    >
                      <span className="min-w-0">{ref.title}</span>
                      <ExternalLink className="h-3 w-3 shrink-0 mt-0.5" />
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
                {/* Actions stacked in a narrow column so they don't steal the
                    title's horizontal room (which would force it to wrap +
                    grow the row height). */}
                <div className="flex shrink-0 flex-col gap-0.5">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => openPaperInCanvas(ref.paper_content_id)}
                    aria-pressed={isActiveOnCanvas}
                    aria-label={
                      isActiveOnCanvas
                        ? t("panel.openInCanvasActiveAria", { title: ref.title })
                        : t("panel.openInCanvasAria", { title: ref.title })
                    }
                    title={
                      isActiveOnCanvas
                        ? t("panel.showingInCanvas")
                        : t("panel.openInCanvas")
                    }
                    className={
                      isActiveOnCanvas
                        ? "bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }
                  >
                    <PanelRight className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => void handleRemove(ref)}
                    aria-label={t("panel.removeAria", { title: ref.title })}
                    title={t("panel.remove")}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Library Browser stays a centred modal — same on both old and new layout. */}
      {backendSessionId !== null && (
        <LibraryBrowserModal
          open={libraryOpen}
          onClose={() => setLibraryOpen(false)}
          backendSessionId={backendSessionId}
          onAttached={() => void refreshRefs()}
        />
      )}
    </div>
  );
}
