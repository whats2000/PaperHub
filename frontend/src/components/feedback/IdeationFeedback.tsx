import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { StackedBar, VIZ_EXPLICIT } from "@/components/feedback/charts";
import { Button } from "@/components/ui/button";
import { getIdeationFeedbackSummary, postIdeationFeedbackToG1 } from "@/lib/api";
import type { IdeationFeedbackSummary } from "@/types/ideation";

const pct = (r: number | null) => (r === null ? "—" : `${Math.round(r * 100)}%`);

function Rate({ value, label, testId }: { value: string; label: string; testId: string }) {
  return (
    <div className="rounded-(--radius) border border-border bg-background px-3.5 py-2.5">
      <div data-testid={testId} className="text-[22px] font-semibold tracking-tight tabular-nums">
        {value}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

/**
 * Feedback page, ideation block: how good were the ideas. Save / worked rates,
 * the dismissal reasons as bars, and a button that writes idea outcomes into
 * the ideation eval data (the G1 inbox).
 */
export function IdeationFeedback() {
  const { t } = useTranslation("feedback");
  const [summary, setSummary] = useState<IdeationFeedbackSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let alive = true;
    getIdeationFeedbackSummary().then(
      (s) => {
        if (!alive) return;
        setSummary(s);
        setFailed(false);
      },
      () => {
        if (alive) setFailed(true);
      },
    );
    return () => {
      alive = false;
    };
  }, [retry]);

  const send = async () => {
    setSending(true);
    try {
      const { appended } = await postIdeationFeedbackToG1();
      toast.success(t("ideation.sent", { count: appended }));
    } catch {
      toast.error(t("ideation.sendFailed"));
    } finally {
      setSending(false);
    }
  };

  const max = Math.max(1, ...(summary?.dismiss_reasons.map((r) => r.count) ?? []));

  return (
    <section
      data-testid="ideation-feedback"
      className="mx-5 mb-5 shrink-0 rounded-(--radius) border border-border bg-card px-4 py-3.5 animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none"
    >
      <h4 className="text-sm font-semibold">{t("ideation.title", "How good are the ideas?")}</h4>
      <div className="mb-2.5 text-xs text-muted-foreground">{t("ideation.hint")}</div>
      {!summary ? (
        failed ? (
          <p className="flex items-center gap-2 text-sm text-destructive">
            {t("ideation.loadFailed")}
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                // Back to the loading state while the retry is in flight.
                setFailed(false);
                setRetry((n) => n + 1);
              }}
            >
              {t("ideation.retry")}
            </Button>
          </p>
        ) : (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" />
            {t("ideation.loading")}
          </p>
        )
      ) : (
        <div className="animate-in fade-in-0 duration-150 motion-reduce:animate-none">
          <div className="grid grid-cols-2 gap-3">
            <Rate
              testId="save-rate"
              value={pct(summary.save_rate)}
              label={t("ideation.saveRate")}
            />
            <Rate
              testId="worked-rate"
              value={pct(summary.worked_rate)}
              label={t("ideation.workedRate")}
            />
          </div>
          <h5 className="mt-3 mb-1.5 text-xs font-medium text-muted-foreground">
            {t("ideation.reasonsTitle")}
          </h5>
          {summary.dismiss_reasons.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("ideation.noReasons")}</p>
          ) : (
            <ul>
              {summary.dismiss_reasons.map((r) => (
                <li
                  key={r.reason}
                  className="grid grid-cols-[minmax(7rem,10rem)_1fr_2rem] items-center gap-3 py-1.5 text-[13px]"
                >
                  <span className="truncate">{t(`ideation.reason.${r.reason}`, r.reason)}</span>
                  <StackedBar
                    max={max}
                    segments={[{ key: r.reason, value: r.count, color: VIZ_EXPLICIT }]}
                  />
                  <span className="text-right text-muted-foreground tabular-nums">{r.count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" disabled={sending} onClick={() => void send()}>
          {t("ideation.send", "Write to ideation eval data")}
        </Button>
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">{t("ideation.help")}</span>
      </div>
    </section>
  );
}
