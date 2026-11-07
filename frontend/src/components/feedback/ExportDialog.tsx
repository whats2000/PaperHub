import { Dialog } from "@base-ui/react/dialog";
import { Download, Loader2, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePresence } from "@/hooks/usePresence";
import { exportFileUrl, getExportPreview, postExport } from "@/lib/api";
import type { ExportPreview, ExportRequest, ExportResult } from "@/types/feedback";

interface ExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const INPUT =
  "h-8 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50";

/** "Export training data": Hugging Face SFT/DPO files, with a live count. */
export function ExportDialog({ open, onOpenChange }: ExportDialogProps) {
  const { t } = useTranslation(["feedback", "common"]);
  // Stay mounted through the exit so Base UI's own (instant) unmount doesn't
  // cut the fade + zoom; `state` drives the data-[state=*] classes below.
  const { mounted, state } = usePresence(open, 150);
  const uid = useId();

  const [implicit, setImplicit] = useState(false);
  const [priv, setPriv] = useState(true);
  const [citedOnly, setCitedOnly] = useState(false);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [model, setModel] = useState("");

  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [result, setResult] = useState<ExportResult | null>(null);
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const seq = useRef(0);
  const exportSeq = useRef(0);

  const opts: ExportRequest = {
    include_implicit: implicit,
    include_private: priv,
    cited_only: citedOnly,
    since: since || null,
    until: until || null,
    model: model.trim() || null,
  };

  // Closed and fully exited: forget the last run so a reopen starts clean.
  if (!mounted && (preview || result || previewFailed || previewKey)) {
    setPreview(null);
    setResult(null);
    setPreviewFailed(false);
    setPreviewKey(null);
    setExporting(false);
  }
  const key = JSON.stringify(opts);
  // An inverted range matches nothing (the backend answers 422): say so and
  // never ask. A settled count of zero samples would write empty files.
  const rangeInvalid = !!since && !!until && since > until;
  const pending = open && !rangeInvalid && previewKey !== key;
  const nothing = !!preview && !pending && preview.sft + preview.dpo === 0;

  // Live summary: debounce 300 ms after the last option change; a stale
  // response (an older request resolving late) is dropped.
  useEffect(() => {
    if (!open) {
      seq.current++; // drop any preview still in flight
      exportSeq.current++; // and any export result
      return;
    }
    const mine = ++seq.current;
    if (rangeInvalid) return;
    const timer = setTimeout(() => {
      getExportPreview({
        include_implicit: implicit,
        include_private: priv,
        cited_only: citedOnly,
        since: since || null,
        until: until || null,
        model: model.trim() || null,
      })
        .then((p) => {
          if (seq.current !== mine) return;
          setPreview(p);
          setPreviewKey(JSON.stringify({
            include_implicit: implicit,
            include_private: priv,
            cited_only: citedOnly,
            since: since || null,
            until: until || null,
            model: model.trim() || null,
          }));
          setPreviewFailed(false);
        })
        .catch(() => {
          if (seq.current === mine) setPreviewFailed(true);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [open, implicit, priv, citedOnly, since, until, model, rangeInvalid]);

  // Options changed after an export: its file list no longer matches them.
  const change =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      exportSeq.current++;
      setResult(null);
      setExporting(false);
    };

  const doExport = async () => {
    const mine = ++exportSeq.current;
    setExporting(true);
    try {
      const r = await postExport(opts);
      if (exportSeq.current === mine) setResult(r);
    } catch {
      if (exportSeq.current === mine) {
        toast.error(t("feedback:export.failed", "Couldn't export the training data. Try again."));
      }
    } finally {
      if (exportSeq.current === mine) setExporting(false);
    }
  };

  const row = (
    id: string,
    label: string,
    hint: string,
    checked: boolean,
    set: (v: boolean) => void,
  ) => (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <label htmlFor={`${uid}-${id}`} className="min-w-0 flex-1 text-sm">
        {label}
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </label>
      <Switch id={`${uid}-${id}`} checked={checked} onCheckedChange={change(set)} />
    </div>
  );

  return (
    <Dialog.Root open={mounted} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop
          data-state={state}
          className="fixed inset-0 z-40 bg-black/40 duration-150 motion-reduce:animate-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:fill-mode-forwards"
        />
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center p-4">
          <Dialog.Popup
            data-state={state}
            aria-labelledby={`${uid}-title`}
            className="pointer-events-auto flex max-h-[90vh] w-[520px] max-w-[92vw] flex-col overflow-hidden rounded-lg border bg-background shadow-xl duration-150 motion-reduce:animate-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:fill-mode-forwards"
          >
            <div className="flex items-start justify-between gap-3 border-b px-5 py-3">
              <div className="min-w-0">
                <h2 id={`${uid}-title`} className="text-base font-semibold">
                  {t("feedback:export.title", "Export training data")}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {t(
                    "feedback:export.subtitle",
                    "Hugging Face format (JSONL + dataset card), written under the backend's exports folder.",
                  )}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("common:close")}
                title={t("common:close")}
                onClick={() => onOpenChange(false)}
              >
                <X />
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
              {row(
                "implicit",
                t("feedback:export.implicit", "Include implicit feedback"),
                t("feedback:export.implicitHint", "Reviewer-confirmed mistakes, not only thumbs."),
                implicit,
                setImplicit,
              )}
              {row(
                "private",
                t("feedback:export.private", "Include private documents"),
                t("feedback:export.privateHint", "Samples that cite your uploaded PDFs."),
                priv,
                setPriv,
              )}
              {row(
                "cited",
                t("feedback:export.citedOnly", "Cited answers only"),
                t("feedback:export.citedOnlyHint", "Skip answers that cite no passage."),
                citedOnly,
                setCitedOnly,
              )}

              <div className="mt-2 grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor={`${uid}-since`} className="text-xs font-medium text-muted-foreground">
                    {t("feedback:export.since", "From")}
                  </label>
                  <input
                    id={`${uid}-since`}
                    type="date"
                    value={since}
                    max={until || undefined}
                    onChange={(e) => change(setSince)(e.target.value)}
                    className={INPUT}
                  />
                </div>
                <div>
                  <label htmlFor={`${uid}-until`} className="text-xs font-medium text-muted-foreground">
                    {t("feedback:export.until", "To")}
                  </label>
                  <input
                    id={`${uid}-until`}
                    type="date"
                    value={until}
                    min={since || undefined}
                    onChange={(e) => change(setUntil)(e.target.value)}
                    className={INPUT}
                  />
                </div>
                <div className="col-span-2">
                  <label htmlFor={`${uid}-model`} className="text-xs font-medium text-muted-foreground">
                    {t("feedback:export.model", "Answer model")}
                  </label>
                  <input
                    id={`${uid}-model`}
                    value={model}
                    placeholder={t("feedback:export.modelPlaceholder", "All models")}
                    onChange={(e) => change(setModel)(e.target.value)}
                    className={INPUT}
                  />
                </div>
              </div>

              <div
                aria-live="polite"
                aria-busy={pending}
                className="mt-4 flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-sm"
              >
                <div className={pending && preview ? "opacity-60 transition-opacity" : "transition-opacity"}>
                {rangeInvalid ? (
                  <span className="text-destructive">
                    {t("feedback:export.rangeInvalid", "“From” is after “To”.")}
                  </span>
                ) : previewFailed ? (
                  <span className="text-destructive">
                    {t("feedback:export.previewFailed", "Couldn't count the samples.")}
                  </span>
                ) : preview ? (
                  <span>
                    {t("feedback:export.willExport", "Will export")} <b>{preview.sft}</b>{" "}
                    {t("feedback:export.sftUnit", "SFT samples")}, <b>{preview.dpo}</b>{" "}
                    {t("feedback:export.dpoUnit", "DPO pairs")}
                    {nothing && (
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {t(
                          "feedback:export.nothing",
                          "Nothing matches these options, so there is nothing to export.",
                        )}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <Loader2 className="size-3.5 animate-spin" />
                    {t("feedback:export.counting", "Counting…")}
                  </span>
                )}
                </div>
                <span className="flex size-4 shrink-0 items-center justify-center" aria-hidden="true">
                  {pending && preview && (
                    <Loader2 className="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" />
                  )}
                </span>
              </div>
              {preview?.includes_private && (
                <p className="mt-2 text-xs text-muted-foreground">
                  {t(
                    "feedback:export.privateWarning",
                    "The data contains the text of private documents. Confirm you may share it before uploading outside your organization.",
                  )}
                </p>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                {t(
                  "feedback:export.agentNote",
                  "Agent-step samples are exported from the dev environment with paperhub-eval.",
                )}
              </p>

              {result && (
                <div className="mt-4">
                  <p className="text-xs font-medium text-muted-foreground">
                    {t("feedback:export.done", "Exported to {{dir}}", { dir: result.dir })}
                  </p>
                  <ul className="mt-1 space-y-1">
                    {result.files.map((f) => (
                      <li key={f}>
                        <a
                          href={exportFileUrl(result.dir, f)}
                          download
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
                        >
                          <Download className="size-3.5" aria-hidden="true" />
                          {f}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 border-t px-5 py-3">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {result ? t("common:close") : t("feedback:export.cancel", "Cancel")}
              </Button>
              <Button onClick={() => void doExport()} disabled={exporting || rangeInvalid || nothing}>
                {exporting ? <Loader2 className="animate-spin" /> : <Download />}
                {t("feedback:export.go", "Export")}
              </Button>
            </div>
          </Dialog.Popup>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
