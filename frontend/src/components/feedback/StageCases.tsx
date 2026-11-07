import { useEffect, useState } from "react";
import { FlaskConical, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { TraceInline } from "@/components/chat/TraceInline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { fetchRunTrace, getFeedbackCases, listSessions, postG1Inbox } from "@/lib/api";
import { revealInScroll } from "@/lib/motion";
import { useChatStore } from "@/store/chat";
import type { ToolCallRecord } from "@/types/domain";
import type { FeedbackCase, StageSummary } from "@/types/feedback";

type TraceState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; records: ToolCallRecord[] };

/** One case: the question, its kind, and the links back to its run. */
function CaseItem({
  c,
  checked,
  onCheck,
}: {
  c: FeedbackCase;
  checked: boolean;
  onCheck: (on: boolean) => void;
}) {
  const { t, i18n } = useTranslation("feedback");
  const [traceOpen, setTraceOpen] = useState(false);
  const [trace, setTrace] = useState<TraceState | null>(null);

  const viewConversation = async () => {
    const findLocal = () =>
      useChatStore.getState().sessions.find((s) => s.backend_session_id === c.session_id)?.id;
    let local = findLocal();
    if (local === undefined) {
      // Not mirrored yet: refresh the session list once. A session that still
      // isn't there was deleted; never invent a local one for it.
      try {
        useChatStore.getState().reconcileBackendSessions(await listSessions());
        local = findLocal();
      } catch {
        /* fall through to the unavailable toast */
      }
    }
    if (local === undefined) {
      toast.error(t("analysis.conversationGone"));
      return;
    }
    useChatStore.getState().selectSession(local);
  };

  const loadTrace = async () => {
    setTrace({ status: "loading" });
    try {
      setTrace({ status: "ready", records: await fetchRunTrace(c.session_id, c.run_id) });
    } catch {
      setTrace({ status: "error" });
    }
  };

  const toggleTrace = () => {
    const next = !traceOpen;
    setTraceOpen(next);
    if (next && (trace === null || trace.status === "error")) void loadTrace();
  };

  const date = new Date(c.created_at);
  return (
    <li className="rounded-[calc(var(--radius)*0.8)] border border-border px-3 py-2 text-sm">
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <div className="break-words">{c.question}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <Badge
              variant={c.kind === "implicit" ? "outline" : "secondary"}
              className={c.kind === "implicit" ? "border-dashed" : undefined}
            >
              {t(`analysis.kind.${c.kind}`)}
            </Badge>
            {c.in_g1_inbox && (
              <Badge variant="outline" className="border-primary/40 text-primary">
                {t("analysis.inInbox")}
              </Badge>
            )}
            {!Number.isNaN(date.getTime()) && <span>{date.toLocaleDateString(i18n.language)}</span>}
            <span>{t("queue.run", { id: c.run_id })}</span>
            <button
              type="button"
              onClick={() => void viewConversation()}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {t("analysis.viewConversation")}
            </button>
            <button
              type="button"
              onClick={toggleTrace}
              aria-expanded={traceOpen}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {traceOpen ? t("analysis.hideTrace") : t("analysis.viewTrace")}
            </button>
          </div>
        </div>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onCheck(e.target.checked)}
          disabled={c.in_g1_inbox}
          aria-label={t("analysis.include", { question: c.question })}
          className="mt-0.5 size-4 shrink-0 accent-primary"
        />
      </div>
      <Collapsible open={traceOpen} onExpanded={(el) => revealInScroll(el)}>
        <div className="pt-1">
          {trace?.status === "loading" && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin motion-reduce:animate-none" />
              {t("analysis.traceLoading")}
            </p>
          )}
          {trace?.status === "error" && (
            <p className="mt-2 text-xs text-destructive">{t("analysis.traceFailed")}</p>
          )}
          {trace?.status === "ready" && (
            <TraceInline trace={trace.records} sessionId={c.session_id} runId={c.run_id} embedded />
          )}
        </div>
      </Collapsible>
    </li>
  );
}

/**
 * The detail pane of the stage analysis: the selected stage's cases, each
 * linking back to its conversation and trace, and a send-to-G1 action for
 * the checked ones.
 */
export function StageCases({ stage, top }: { stage: StageSummary; top: boolean }) {
  const { t } = useTranslation("feedback");
  const [cases, setCases] = useState<FeedbackCase[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [unchecked, setUnchecked] = useState<Set<number>>(new Set());
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let alive = true;
    getFeedbackCases(stage.stage).then(
      (cs) => {
        if (alive) setCases(cs);
      },
      () => {
        if (alive) setFailed(true);
      },
    );
    return () => {
      alive = false;
    };
  }, [stage.stage]);

  const checkedIds = (cases ?? [])
    .filter((c) => !unchecked.has(c.feedback_id) && !c.in_g1_inbox)
    .map((c) => c.feedback_id);

  const setChecked = (id: number, on: boolean) =>
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (on) next.delete(id);
      else next.add(id);
      return next;
    });

  const send = async () => {
    if (!checkedIds.length || sending) return;
    setSending(true);
    try {
      const res = await postG1Inbox({ feedback_ids: checkedIds });
      if (res.appended === 0) toast.info(t("analysis.g1NoneNew"));
      else toast.success(t("analysis.g1Sent", { count: res.appended }));
      // Sent: mark them as in the inbox so the count excludes them and a
      // reload shows the same state (the backend reports `in_g1_inbox`).
      const sent = new Set(checkedIds);
      setCases((prev) =>
        (prev ?? []).map((c) => (sent.has(c.feedback_id) ? { ...c, in_g1_inbox: true } : c)),
      );
    } catch {
      toast.error(t("analysis.g1Failed"));
    } finally {
      setSending(false);
    }
  };

  const stageName = t(`stage.${stage.stage}`);
  const total = stage.explicit + stage.implicit;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mb-1.5 text-xs text-muted-foreground">
          {t("analysis.detailMeta", { count: total })}
          {top && total > 0 && ` · ${t("analysis.mostFrequent")}`}
        </div>
        <h3 className="mb-1 text-[15px] font-semibold">{stageName}</h3>
        <div className="mb-3 text-xs text-muted-foreground">
          {t("analysis.lastWeeks", { weeks: stage.weekly.join(" → ") })}
        </div>
        <div className="mb-1.5 text-xs text-muted-foreground">{t("analysis.casesHint")}</div>
        {failed && <p className="text-sm text-destructive">{t("analysis.casesFailed")}</p>}
        {cases === null && !failed && (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" />
            {t("analysis.loading")}
          </p>
        )}
        {cases !== null && cases.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("analysis.noCases")}</p>
        )}
        {cases !== null && cases.length > 0 && (
          <ul className="flex flex-col gap-2">
            {cases.map((c) => (
              <CaseItem
                key={c.feedback_id}
                c={c}
                checked={!unchecked.has(c.feedback_id) && !c.in_g1_inbox}
                onCheck={(on) => setChecked(c.feedback_id, on)}
              />
            ))}
          </ul>
        )}
      </div>
      <div className="flex-none border-t border-border px-4 pt-3 pb-4">
        <Button
          className="w-full"
          disabled={!checkedIds.length || sending}
          onClick={() => void send()}
        >
          {sending ? (
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
          ) : (
            <FlaskConical className="size-4" />
          )}
          {t("analysis.sendG1", { count: checkedIds.length })}
        </Button>
        <div className="mt-1.5 text-xs text-muted-foreground">
          {t("analysis.g1Hint", { stage: stageName })}
        </div>
      </div>
    </div>
  );
}
