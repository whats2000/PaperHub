import { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Loader2, ShieldQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ActionNotPending, confirmAction, dismissAction, getAction } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import type { ConfirmRequestData } from "@/types/domain";

// Matches duration-200 plus a margin: unmount even if no transitionend fires.
const EXIT_FALLBACK_MS = 250;
/** How often a `running` card re-reads its action (another tab executing). */
export const RUNNING_POLL_MS = 2000;
const TERMINAL = new Set(["done", "failed", "dismissed"]);

type TFn = ReturnType<typeof useTranslation>["t"];

/** The action in the user's language, from the structured target; the
 *  backend's one-line summary only for cards replayed without one. */
function actionText(data: ConfirmRequestData, t: TFn): string {
  const target = data.target;
  if (target?.kind === "paper") {
    return t("confirm.action.removePaper", 'Remove "{{title}}" from this chat', {
      title: target.title,
    });
  }
  if (target?.kind === "memory") {
    return target.scope === "global"
      ? t("confirm.action.forgetGlobal", 'Forget this memory (all chats): "{{content}}"', {
          content: target.content,
        })
      : t("confirm.action.forgetSession", 'Forget this memory (this chat): "{{content}}"', {
          content: target.content,
        });
  }
  return data.args_summary;
}

/** What ran, in the user's language (never the model-facing English text). */
function resultText(data: ConfirmRequestData, t: TFn): string {
  const record = (data.result?.record ?? {});
  const target = data.target;
  if (data.tool === "remove_paper") {
    const title =
      typeof record.title === "string" ? record.title : target?.kind === "paper" ? target.title : null;
    if (title) return t("confirm.result.removePaper", 'Removed "{{title}}" from this chat.', { title });
  }
  if (data.tool === "forget") {
    const content =
      typeof record.content === "string"
        ? record.content
        : target?.kind === "memory"
          ? target.content
          : null;
    if (content) return t("confirm.result.forget", 'Forgot "{{content}}".', { content });
  }
  return t("confirm.done");
}

function reasonText(data: ConfirmRequestData, t: TFn): string | null {
  if (data.reason_kind === "classifier") {
    return t(
      "confirm.reasonClassifier",
      "Auto-approve wasn't sure this is exactly what you asked for.",
    );
  }
  if (data.reason_kind === "classifier_failed") {
    return t("confirm.reasonCheckFailed", "The auto-approve check failed, so you're asked.");
  }
  return null;
}

/**
 * A confirm-tier action the agent asked the user to approve (H4): the action
 * summary and the agent's reason, with Confirm / Dismiss. Confirm runs the
 * tool directly (no LLM) and the card then shows the result; Dismiss
 * collapses the card into a muted "Dismissed: …" line. Enters with fade +
 * zoom; exits by collapsing its height so the content below glides up.
 */
export function ConfirmActionCard({
  data,
  onChange,
  onDone,
}: {
  data: ConfirmRequestData;
  /** Persist a status/result change on the message's event. */
  onChange: (patch: Partial<ConfirmRequestData>) => void;
  /** The action ran: refresh whatever it changed (references, memories). */
  onDone?: () => void;
}) {
  const { t } = useTranslation("chat");
  const [busy, setBusy] = useState<"confirm" | "dismiss" | null>(null);
  const [closing, setClosing] = useState(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finished = useRef(false);
  // One request at a time, even if a second click lands before the
  // disabled state renders.
  const inFlight = useRef(false);
  const status = data.status ?? "pending";
  // The latest status, read after an await: a late 409 must never
  // overwrite a result this card already shows.
  const statusRef = useRef(status);
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(
    () => () => {
      if (exitTimer.current !== null) clearTimeout(exitTimer.current);
    },
    [],
  );

  // `running`: another tab (or a crashed request) holds the action. Re-read it
  // until it ends, so the spinner never outlives the action.
  const onChangeRef = useRef(onChange);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onChangeRef.current = onChange;
    onDoneRef.current = onDone;
  }, [onChange, onDone]);
  useEffect(() => {
    if (status !== "running") return;
    let stopped = false;
    const timer = setInterval(() => {
      void getAction(data.action_id)
        .then((state) => {
          if (stopped || state.status === "running" || state.status === "pending") return;
          if (state.status === "done") {
            onChangeRef.current({ status: "done", result: state.result ?? null });
            onDoneRef.current?.();
          } else if (state.status === "failed") {
            onChangeRef.current({ status: "failed", error: state.error });
          } else {
            onChangeRef.current({ status: state.status });
          }
        })
        .catch(() => undefined); // keep polling; a blip is not an answer
    }, RUNNING_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [status, data.action_id]);

  const summary = actionText(data, t);
  const reason = reasonText(data, t);

  if (status === "dismissed" && !closing) {
    return (
      <p
        data-testid="confirm-dismissed"
        className="not-prose mt-2 text-xs italic text-muted-foreground animate-in fade-in-0 slide-in-from-top-1 duration-200 motion-reduce:animate-none"
      >
        {t("confirm.dismissed", { summary })}
      </p>
    );
  }

  const finishExit = () => {
    if (finished.current) return;
    finished.current = true;
    if (exitTimer.current !== null) clearTimeout(exitTimer.current);
    onChange({ status: "dismissed" });
    setClosing(false);
  };

  // Confirm/Dismiss answered 409: the action was handled elsewhere (another
  // tab or device). Show its real state instead of guessing.
  const handleStale = async () => {
    let next: Partial<ConfirmRequestData> = { status: "unavailable" };
    try {
      const state = await getAction(data.action_id);
      if (state.status === "done") next = { status: "done", result: state.result ?? null };
      else if (state.status === "failed") next = { status: "failed", error: state.error };
      else if (state.status === "dismissed" || state.status === "running") {
        next = { status: state.status };
      }
    } catch {
      // Unknown action: leave it unavailable.
    }
    // A late answer never overwrites a result the card already shows
    // (done / failed / dismissed, or the dismiss exit already playing).
    if (TERMINAL.has(statusRef.current) || finished.current) return;
    onChange(next);
    if (next.status === "unavailable") toast.error(t("confirm.stale"));
  };

  const confirm = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy("confirm");
    try {
      const res = await confirmAction(data.action_id);
      if (res.status === "done") {
        onChange({ status: "done", result: res.result });
        onDone?.();
      } else {
        onChange({ status: "failed", error: res.error });
      }
    } catch (err) {
      if (err instanceof ActionNotPending) await handleStale();
      else toast.error(t("confirm.requestFailed"));
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const dismiss = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy("dismiss");
    try {
      await dismissAction(data.action_id);
      setClosing(true);
      if (prefersReducedMotion()) finishExit();
      else exitTimer.current = setTimeout(finishExit, EXIT_FALLBACK_MS);
    } catch (err) {
      if (err instanceof ActionNotPending) await handleStale();
      else toast.error(t("confirm.requestFailed"));
      setBusy(null);
    } finally {
      inFlight.current = false;
    }
  };

  return (
    <div
      data-testid="confirm-card"
      data-state={closing ? "closed" : "open"}
      onTransitionEnd={(e) => {
        if (
          closing &&
          e.target === e.currentTarget &&
          e.propertyName === "grid-template-rows"
        ) {
          finishExit();
        }
      }}
      className="grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
    >
      <div className="min-h-0 overflow-hidden">
        <div
          role="group"
          aria-label={t("confirm.title")}
          className="not-prose mt-2 origin-top rounded-lg border border-border bg-card p-3 text-sm shadow-sm animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none"
        >
          <div className="flex items-center gap-2 font-medium">
            <ShieldQuestion className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            {t("confirm.title")}
          </div>
          <p className="mt-1 break-words text-xs">{summary}</p>
          {reason && (
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="font-medium">{t("confirm.reason")}</span> {reason}
            </p>
          )}

          {status === "pending" && (
            <div className="mt-3 flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={busy !== null || closing}
                onClick={() => void confirm()}
                className="gap-1.5"
              >
                {busy === "confirm" && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                )}
                {t("confirm.confirm")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy !== null || closing}
                onClick={() => void dismiss()}
                className="gap-1.5"
              >
                {busy === "dismiss" && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                )}
                {t("confirm.dismiss")}
              </Button>
            </div>
          )}
          {status === "running" && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("confirm.running")}
            </p>
          )}
          {status === "done" && (
            <p
              data-result="done"
              className="mt-3 flex items-start gap-1.5 text-xs animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              <Check
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400"
                aria-hidden
              />
              <span>{resultText(data, t)}</span>
            </p>
          )}
          {status === "failed" && (
            <p
              data-result="failed"
              className="mt-3 flex items-start gap-1.5 text-xs text-destructive animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>
                {data.error?.startsWith("interrupted")
                  ? t("confirm.interrupted", "The server stopped while this ran; it may not have finished.")
                  : t("confirm.failedGeneric", "The action failed.")}
                {data.error && !data.error.startsWith("interrupted") && (
                  // The backend's own reason, as a detail (like Settings).
                  <span className="mt-0.5 block font-normal text-muted-foreground">
                    {data.error}
                  </span>
                )}
              </span>
            </p>
          )}
          {status === "unavailable" && (
            <p className="mt-3 text-xs italic text-muted-foreground animate-in fade-in-0 duration-200 motion-reduce:animate-none">
              {t("confirm.unavailable")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
