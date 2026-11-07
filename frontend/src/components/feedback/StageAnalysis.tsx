import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { BarChart3, Check, Loader2, Table2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { StageCases } from "@/components/feedback/StageCases";
import {
  Sparkline,
  StackedBar,
  Swatch,
  VIZ_EXPLICIT,
  VIZ_IMPLICIT,
} from "@/components/feedback/charts";
import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { getFeedbackSummary, listFeedbackPresets, postFeedbackPreset } from "@/lib/api";
import { prefersReducedMotion, revealInScroll } from "@/lib/motion";
import { cn } from "@/lib/utils";
import {
  STAGES,
  type FeedbackSummary,
  type OtherReason,
  type Stage,
  type StageSummary,
  type Unattributed,
} from "@/types/feedback";

const SWAP_MS = 130;

/**
 * Show `target` after the old content has faded out: `fading` is true while
 * the previous value is still on screen, leaving.
 */
function useSwap<T>(target: T): { shown: T; fading: boolean } {
  const [shown, setShown] = useState(target);
  useEffect(() => {
    if (Object.is(shown, target)) return;
    const id = setTimeout(() => setShown(target), prefersReducedMotion() ? 0 : SWAP_MS);
    return () => clearTimeout(id);
  }, [target, shown]);
  return { shown, fading: !Object.is(shown, target) };
}

const swapClass = (fading: boolean) =>
  cn(
    "transition-opacity duration-150 ease-out motion-reduce:transition-none",
    fading ? "opacity-0" : "animate-in fade-in-0 duration-150 motion-reduce:animate-none",
  );

const total = (s: StageSummary) => s.explicit + s.implicit;

/** Week-over-week change for the table: a signed number with a real minus. */
const signed = (d: number) => (d > 0 ? `+${d}` : d < 0 ? `−${-d}` : "0");

function Kpi({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-(--radius) border border-border bg-card px-3.5 py-2.5">
      <div className="text-[22px] font-semibold tracking-tight tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

function KpiRow({ kpis }: { kpis: FeedbackSummary["kpis"] }) {
  const { t } = useTranslation("feedback");
  const rate = kpis.approval_rate;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Kpi value={String(kpis.answers_with_feedback)} label={t("analysis.kpi.answers")} />
      <Kpi
        value={rate === null ? "—" : `${Math.round(rate * 100)}%`}
        label={t("analysis.kpi.approval")}
      />
      <Kpi
        value={String(kpis.negatives_explicit + kpis.negatives_implicit)}
        label={t("analysis.kpi.negatives", {
          explicit: kpis.negatives_explicit,
          implicit: kpis.negatives_implicit,
        })}
      />
      <Kpi value={String(kpis.pending)} label={t("analysis.kpi.pending")} />
    </div>
  );
}

function Delta({ d }: { d: number }) {
  const { t } = useTranslation("feedback");
  if (d === 0) return <>{t("analysis.flat")}</>;
  return d > 0 ? (
    <>
      <b className="font-semibold text-foreground">↑{d}</b> {t("analysis.vsLastWeek")}
    </>
  ) : (
    <>
      ↓{-d} {t("analysis.vsLastWeek")}
    </>
  );
}

/** The "no stage" row: negatives whose reasons point at no stage. Not a
 * stage, so it has no cases pane and is not selectable. */
function NoStageRow({ u, max }: { u: Unattributed; max: number }) {
  const { t } = useTranslation("feedback");
  return (
    <div
      data-testid="no-stage-row"
      className="grid grid-cols-[minmax(7rem,10rem)_1fr_2.25rem_auto] items-center gap-3 px-2 py-2 text-[13px] text-muted-foreground sm:grid-cols-[minmax(7rem,10rem)_1fr_2.25rem_60px_5.5rem]"
    >
      <span className="truncate" title={t("analysis.noStageHint")}>
        {t("analysis.noStage")}
      </span>
      <div className="py-1">
        <StackedBar
          max={max}
          segments={[
            { key: "explicit", value: u.explicit, color: VIZ_EXPLICIT },
            { key: "implicit", value: u.implicit, color: VIZ_IMPLICIT },
          ]}
        />
      </div>
      <span className="text-right tabular-nums">{u.explicit + u.implicit}</span>
    </div>
  );
}

function StageList({
  stages,
  unattributed,
  selected,
  onSelect,
}: {
  stages: StageSummary[];
  unattributed: Unattributed;
  selected: Stage | undefined;
  onSelect: (s: Stage) => void;
}) {
  const { t } = useTranslation("feedback");
  const max = Math.max(1, ...stages.map(total), unattributed.explicit + unattributed.implicit);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const i = stages.findIndex((s) => s.stage === selected);
    const next = stages[(i + (e.key === "ArrowDown" ? 1 : -1) + stages.length) % stages.length];
    if (!next) return;
    onSelect(next.stage);
    e.currentTarget.querySelector<HTMLElement>(`[data-stage="${next.stage}"]`)?.focus();
  };

  return (
    <TooltipProvider>
      <div role="listbox" aria-label={t("analysis.stagesLabel")} onKeyDown={onKey}>
        {stages.map((s) => {
          const on = s.stage === selected;
          const name = t(`stage.${s.stage}`);
          return (
            <div
              key={s.stage}
              role="option"
              data-stage={s.stage}
              aria-selected={on}
              tabIndex={on ? 0 : -1}
              onClick={() => onSelect(s.stage)}
              className={cn(
                "grid cursor-pointer grid-cols-[minmax(7rem,10rem)_1fr_2.25rem_auto] items-center gap-3 rounded-[calc(var(--radius)*0.8)] px-2 py-2 text-[13px] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring sm:grid-cols-[minmax(7rem,10rem)_1fr_2.25rem_60px_5.5rem]",
                on ? "bg-accent" : "hover:bg-muted",
              )}
            >
              <span className="truncate">{name}</span>
              <Tooltip>
                <TooltipTrigger render={<div className="py-1" />}>
                  <StackedBar
                    max={max}
                    segments={[
                      { key: "explicit", value: s.explicit, color: VIZ_EXPLICIT },
                      { key: "implicit", value: s.implicit, color: VIZ_IMPLICIT },
                    ]}
                  />
                </TooltipTrigger>
                <TooltipContent>
                  {t("analysis.barTip", {
                    stage: name,
                    explicit: s.explicit,
                    implicit: s.implicit,
                  })}
                </TooltipContent>
              </Tooltip>
              <span className="text-right text-muted-foreground tabular-nums">{total(s)}</span>
              <Tooltip>
                <TooltipTrigger render={<div className="hidden sm:block" />}>
                  <Sparkline values={s.weekly} />
                </TooltipTrigger>
                <TooltipContent>
                  {t("analysis.lastWeeks", { weeks: s.weekly.join(" → ") })}
                </TooltipContent>
              </Tooltip>
              <span className="text-right text-xs whitespace-nowrap text-muted-foreground">
                <Delta d={s.delta} />
              </span>
            </div>
          );
        })}
        <NoStageRow u={unattributed} max={max} />
      </div>
    </TooltipProvider>
  );
}

function StageTable({
  stages,
  unattributed,
}: {
  stages: StageSummary[];
  unattributed: Unattributed;
}) {
  const { t } = useTranslation("feedback");
  const th =
    "border-b border-border px-1 py-1.5 text-left font-medium whitespace-nowrap text-muted-foreground";
  const td = "border-b border-border px-1 py-1.5 text-right whitespace-nowrap tabular-nums";
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr>
            <th className={th}>{t("analysis.col.stage")}</th>
            <th className={cn(th, "text-right")}>{t("analysis.col.explicit")}</th>
            <th className={cn(th, "text-right")}>{t("analysis.col.implicit")}</th>
            <th className={cn(th, "text-right")}>{t("analysis.col.total")}</th>
            <th className={cn(th, "text-right")}>{t("analysis.col.weeks")}</th>
            <th className={cn(th, "text-right")}>{t("analysis.col.delta")}</th>
          </tr>
        </thead>
        <tbody>
          {stages.map((s) => (
            <tr key={s.stage}>
              <td className="border-b border-border px-1 py-1.5">{t(`stage.${s.stage}`)}</td>
              <td className={td}>{s.explicit}</td>
              <td className={td}>{s.implicit}</td>
              <td className={td}>{total(s)}</td>
              <td className={td}>{s.weekly.join(" → ")}</td>
              <td className={td}>{signed(s.delta)}</td>
            </tr>
          ))}
          <tr className="text-muted-foreground">
            <td className="border-b border-border px-1 py-1.5">{t("analysis.noStage")}</td>
            <td className={td}>{unattributed.explicit}</td>
            <td className={td}>{unattributed.implicit}</td>
            <td className={td}>{unattributed.explicit + unattributed.implicit}</td>
            <td className={td}>—</td>
            <td className={td}>—</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function OtherReasonRow({
  reason,
  added,
  onAdded,
}: {
  reason: OtherReason;
  added: boolean;
  onAdded: () => void;
}) {
  const { t } = useTranslation("feedback");
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);

  const promote = async (stage: Stage) => {
    setSaving(true);
    try {
      await postFeedbackPreset({ label: reason.text, stage });
      toast.success(t("analysis.promoted", { label: reason.text }));
      setPicking(false);
      onAdded();
    } catch (e) {
      const dup = e instanceof Error && e.message.startsWith("API 409");
      toast.error(dup ? t("analysis.promoteDuplicate") : t("analysis.promoteFailed"));
      if (dup) {
        setPicking(false);
        onAdded();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <li className="border-t border-border py-2 text-[13px] first:border-t-0">
      <div className="flex items-start gap-2.5">
        <span className="min-w-0 flex-1 break-words">{reason.text}</span>
        <span className="text-xs whitespace-nowrap text-muted-foreground">
          {t("analysis.times", { count: reason.count })}
        </span>
        {added ? (
          <span className="inline-flex h-6 items-center gap-1 text-xs text-muted-foreground">
            <Check className="size-3.5" />
            {t("analysis.added")}
          </span>
        ) : (
          <Button
            variant="outline"
            size="xs"
            aria-expanded={picking}
            onClick={() => setPicking((p) => !p)}
          >
            {t("analysis.promote")}
          </Button>
        )}
      </div>
      <Collapsible open={picking && !added} onExpanded={(el) => revealInScroll(el)}>
        <div className="flex flex-wrap items-center gap-1.5 pt-2">
          <span className="text-xs text-muted-foreground">{t("analysis.pickStage")}</span>
          {STAGES.map((st) => (
            <Button
              key={st}
              variant="secondary"
              size="xs"
              disabled={saving}
              onClick={() => void promote(st)}
            >
              {t(`stage.${st}`)}
            </Button>
          ))}
          <Button variant="ghost" size="xs" onClick={() => setPicking(false)}>
            {t("popover.cancel")}
          </Button>
        </div>
      </Collapsible>
    </li>
  );
}

function OtherReasons({ reasons }: { reasons: OtherReason[] }) {
  const { t } = useTranslation("feedback");
  const [added, setAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    listFeedbackPresets().then(
      (ps) => {
        if (alive) setAdded((prev) => new Set([...prev, ...ps.map((p) => p.label)]));
      },
      () => {
        /* Not knowing the presets only hides the "added" marks. */
      },
    );
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="rounded-(--radius) border border-border bg-card px-4 py-3.5">
      <h4 className="mb-1 text-sm font-semibold">{t("analysis.otherTitle")}</h4>
      <div className="mb-2 text-xs text-muted-foreground">{t("analysis.otherHint")}</div>
      {reasons.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("analysis.otherEmpty")}</p>
      ) : (
        <ul>
          {reasons.map((r) => (
            <OtherReasonRow
              key={r.text}
              reason={r}
              added={added.has(r.text)}
              onAdded={() => setAdded((prev) => new Set([...prev, r.text]))}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Feedback page, part 2: where the system goes wrong. KPIs, a ranked stage
 * list (stacked explicit/implicit bars, 4-week sparkline, week-over-week
 * delta) with a table view, the "other" texts to promote, and a permanent
 * detail pane listing the selected stage's cases. `refreshKey` refetches
 * (bumped after the confirmation queue submits).
 */
export function StageAnalysis({ refreshKey }: { refreshKey: number }) {
  const { t } = useTranslation("feedback");
  const [summary, setSummary] = useState<FeedbackSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const [picked, setPicked] = useState<Stage | null>(null);
  const [table, setTable] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let alive = true;
    getFeedbackSummary().then(
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
  }, [refreshKey, retry]);

  // Ranked by total, most frequent first; ties keep the backend's stage order.
  const ranked = useMemo(
    () => [...(summary?.stages ?? [])].sort((a, b) => total(b) - total(a)),
    [summary],
  );
  const top = ranked[0]?.stage;
  const selected = picked ?? top;
  const detail = useSwap(selected);
  const view = useSwap(table);
  const unattributed = summary?.unattributed ?? { explicit: 0, implicit: 0 };
  const shownStage = ranked.find((s) => s.stage === detail.shown);

  if (!summary) {
    return (
      <div className="flex min-h-0 flex-1 items-start px-5 pt-3.5 text-sm text-muted-foreground">
        {failed ? (
          <p className="flex items-center gap-2 text-destructive">
            {t("analysis.loadFailed")}
            <Button variant="outline" size="xs" onClick={() => setRetry((n) => n + 1)}>
              {t("analysis.retry")}
            </Button>
          </p>
        ) : (
          <p className="flex items-center gap-1.5">
            <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" />
            {t("analysis.loading")}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col animate-in fade-in-0 duration-150 motion-reduce:animate-none md:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3.5 overflow-auto px-5 pt-3.5 pb-5">
        <KpiRow kpis={summary.kpis} />
        <section className="rounded-(--radius) border border-border bg-card px-4 py-3.5">
          <div className="mb-1 flex items-center gap-2">
            <h4 className="flex-1 text-sm font-semibold">{t("analysis.title")}</h4>
            <Button
              variant="ghost"
              size="xs"
              aria-pressed={table}
              onClick={() => setTable((v) => !v)}
            >
              {table ? <BarChart3 className="size-3.5" /> : <Table2 className="size-3.5" />}
              {table ? t("analysis.chart") : t("analysis.table")}
            </Button>
          </div>
          <div className="mb-2.5 text-xs text-muted-foreground">{t("analysis.hint")}</div>
          <div className={swapClass(view.fading)}>
            {view.shown ? (
              <StageTable stages={ranked} unattributed={unattributed} />
            ) : (
              <StageList
                stages={ranked}
                unattributed={unattributed}
                selected={selected}
                onSelect={setPicked}
              />
            )}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{t("analysis.stageNote")}</p>
          <div className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Swatch color={VIZ_EXPLICIT} />
              {t("analysis.legend.explicit")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Swatch color={VIZ_IMPLICIT} />
              {t("analysis.legend.implicit")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg width="26" height="10" aria-hidden="true">
                <polyline
                  points="1,8 9,6 17,4 25,2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                />
              </svg>
              {t("analysis.legend.trend")}
            </span>
          </div>
        </section>
        <OtherReasons reasons={summary.other_reasons} />
      </div>
      <section
        aria-label={t("analysis.casesLabel")}
        className="flex min-h-0 flex-1 flex-col border-t border-border bg-background md:w-[400px] md:flex-none md:border-t-0 md:border-l"
      >
        {shownStage && (
          <div className={cn("flex min-h-0 flex-1 flex-col", swapClass(detail.fading))}>
            <StageCases
              key={`${shownStage.stage}-${refreshKey}`}
              stage={shownStage}
              top={shownStage.stage === top}
            />
          </div>
        )}
      </section>
    </div>
  );
}
