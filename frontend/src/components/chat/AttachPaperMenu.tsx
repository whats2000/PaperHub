import { useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ingestPaper, parseArxivId, uploadPdf } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { IngestResult, ReferenceItem } from "@/types/domain";

/** Snapshot the currently-active backend_session_id from the store. Reading
 * via getState() (not the render closure) so handlers see the freshest value
 * before AND after the network call — used to detect a session-switch race. */
function currentBackendSessionId(): number | null {
  const state = useChatStore.getState();
  const active = state.sessions.find((s) => s.id === state.activeSessionId);
  return active?.backend_session_id ?? null;
}

/** Build a ReferenceItem from an ingest/upload response. */
function buildReference(
  result: IngestResult,
  kind: "arxiv" | "pdf_upload",
  arxivId: string | null,
): ReferenceItem {
  return {
    papers_id: result.papers_id,
    paper_content_id: result.paper_content_id,
    enabled: true,
    added_at: new Date().toISOString(),
    arxiv_id: arxivId,
    title: result.title,
    year: null,
    kind,
  };
}

export function AttachPaperMenu() {
  const { t } = useTranslation("chat");
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const appendReferenceLocal = useChatStore((s) => s.appendReferenceLocal);

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? null;
  const backendSessionId = activeSession?.backend_session_id ?? null;
  const hasBackendSession = backendSessionId !== null;

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [arxivInput, setArxivInput] = useState("");
  const [arxivError, setArxivError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Reset transient state when the menu is closed.
  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setArxivInput("");
      setArxivError(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handlePdfChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    // Snapshot the active session at the start. If it changes before the
    // upload resolves, discard the result so we don't poison the wrong bucket.
    const startedAt = currentBackendSessionId();
    if (startedAt === null) return;
    setBusy(true);
    try {
      const result = await uploadPdf(startedAt, file);
      if (currentBackendSessionId() !== startedAt) {
        toast.info(t("toast.sessionChanged"));
        return;
      }
      const ref = buildReference(result, "pdf_upload", null);
      appendReferenceLocal(startedAt, ref);
      toast.success(result.cache_hit ? t("toast.reattached") : t("toast.added"), {
        description: result.title,
      });
      handleOpenChange(false);
    } catch (err) {
      toast.error(t("toast.uploadFailed"), {
        description: err instanceof Error ? err.message : String(err),
      });
      if (fileInputRef.current) fileInputRef.current.value = "";
    } finally {
      setBusy(false);
    }
  }

  async function handleArxivSubmit() {
    setArxivError(null);
    const canonical = parseArxivId(arxivInput);
    if (canonical === null) {
      setArxivError(t("attach.invalidArxiv"));
      return;
    }
    // Snapshot the active session at the start. If it changes before the
    // request resolves, discard the result.
    const startedAt = currentBackendSessionId();
    if (startedAt === null) return;
    setBusy(true);
    try {
      const result = await ingestPaper(startedAt, canonical);
      if (currentBackendSessionId() !== startedAt) {
        toast.info(t("toast.sessionChanged"));
        return;
      }
      // canonical is "arxiv:<id>" — strip the prefix for ReferenceItem.arxiv_id.
      const arxivId = canonical.replace(/^arxiv:/i, "");
      const ref = buildReference(result, "arxiv", arxivId);
      appendReferenceLocal(startedAt, ref);
      toast.success(result.cache_hit ? t("toast.reattached") : t("toast.added"), {
        description: result.title,
      });
      handleOpenChange(false);
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err);
      // Strip an `API <code>: ` prefix so the inline error stays terse —
      // the toast keeps the unedited message for parity with PDF uploads.
      const inlineMessage = raw.replace(/^API \d+:\s*/, "");
      setArxivError(inlineMessage);
      toast.error(t("toast.importFailed"), { description: raw });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Tooltip>
      <Popover open={open} onOpenChange={handleOpenChange}>
        <TooltipTrigger
          render={
            <PopoverTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-muted-foreground hover:text-foreground"
                  aria-label={t("attach.trigger")}
                />
              }
            >
              <Paperclip className="h-4 w-4" />
            </PopoverTrigger>
          }
        />

        <PopoverContent
        side="top"
        align="start"
        className="w-80"
        // The popover doesn't manage focus on a file input cleanly; let
        // initial focus land where Base UI defaults (first tabbable).
      >
        <Tabs defaultValue="pdf">
          <TabsList>
            <TabsTrigger value="pdf">{t("attach.tabPdf")}</TabsTrigger>
            <TabsTrigger value="arxiv">{t("attach.tabArxiv")}</TabsTrigger>
          </TabsList>

          {!hasBackendSession && (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("attach.needSession")}
            </p>
          )}

          <TabsContent value="pdf">
            <div className="flex flex-col gap-2 text-sm">
              <span className="text-xs text-muted-foreground">
                {t("attach.pdfHint")}
              </span>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf"
                onChange={(e) => void handlePdfChange(e)}
                disabled={busy || !hasBackendSession}
                aria-label={t("attach.pdfFileAria")}
                className="block w-full text-xs file:mr-2 file:rounded-md file:border file:border-input file:bg-background file:px-2 file:py-1 file:text-xs file:font-medium hover:file:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
          </TabsContent>

          <TabsContent value="arxiv">
            <div className="flex flex-col gap-2">
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-xs text-muted-foreground">
                  {t("attach.arxivHint")}
                </span>
                <Input
                  type="text"
                  value={arxivInput}
                  onChange={(e) => {
                    setArxivInput(e.target.value);
                    if (arxivError) setArxivError(null);
                  }}
                  placeholder={t("attach.arxivPlaceholder")}
                  disabled={busy || !hasBackendSession}
                  aria-label={t("attach.arxivInputAria")}
                  aria-invalid={arxivError !== null}
                />
              </label>
              {arxivError && (
                <p
                  role="alert"
                  className="text-xs text-destructive"
                >
                  {arxivError}
                </p>
              )}
              <div className="flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || !hasBackendSession || arxivInput.trim() === ""}
                  onClick={() => void handleArxivSubmit()}
                >
                  {t("attach.import")}
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </PopoverContent>
      </Popover>
      <TooltipContent side="top">
        {t("attach.tooltip")}
      </TooltipContent>
    </Tooltip>
  );
}
