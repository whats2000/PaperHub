import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ActivityTimeline } from "@/components/chat/ActivityTimeline";
import { AgentSummaryBadge } from "@/components/chat/AgentSummaryBadge";
import { TraceTechnical } from "@/components/chat/TraceTechnical";
import { usePresence } from "@/hooks/usePresence";
import { buildTimeline, summarizeTurn, turnSummaryText } from "@/lib/activityTimeline";
import { fetchRunTrace } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { AgentSummary, PausedCard, ToolCallRecord } from "@/types/domain";

export { TraceArgs, TraceResult } from "@/components/chat/TraceTechnical";

/** Matches the list's closed-state duration-200 collapse. */
const TRACE_EXIT_MS = 200;

/** The agent turn the trace belongs to (H4+): its summary and live state. */
export interface AgentTurn {
  summary?: AgentSummary | null;
  /** The message's status (`streaming`, `stopped`, …). */
  status?: string;
  /** Answer text is streaming in (the header reads "Writing the answer…"). */
  writing?: boolean;
}

// ---------------------------------------------------------------------------
// TraceInline — a turn's trace under its answer.
//
// Agent turns (`agent`) get the activity timeline (FR-02, H5a wave 3): the
// turn summary is the toggle ("Worked 10.6 s: read 2 sections, ran 1
// search"), and a "Technical view" switch shows the raw step list instead.
// Pipeline (pre-H4) turns keep the "Trace" button and the raw list; the
// Feedback page (`embedded`) shows the raw list, always open.
//
// Live-streamed turns already carry `trace`; replayed turns arrive with an
// empty `trace`, so the first expand lazily fetches the run's tool_calls
// (GET …/runs/{id}/trace) and caches them onto the message via the store.
// ---------------------------------------------------------------------------
export function TraceInline({
  trace,
  sessionId,
  runId,
  embedded = false,
  paused,
  agent,
}: {
  trace: ToolCallRecord[];
  /** H5a: the cards the run is paused on. */
  paused?: PausedCard[] | null;
  sessionId: number;
  runId: number;
  /** Under a caller's own toggle (the Feedback page's "View trace"): no
   *  header, always open, the raw list, and `trace` is the already-fetched
   *  result, so an empty one shows the empty state. */
  embedded?: boolean;
  /** An agent turn: the activity timeline and its summary toggle. */
  agent?: AgentTurn;
}) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(embedded);
  const [technical, setTechnical] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Whether a fetch has completed (or the message already carried a trace) —
  // distinguishes "not fetched yet" from "fetched, genuinely empty".
  const [fetched, setFetched] = useState(embedded || trace.length > 0);
  const [records, setRecords] = useState<ToolCallRecord[]>([]);
  const setMessageTrace = useChatStore((s) => s.setMessageTrace);

  const rows = trace.length > 0 ? trace : records;
  const live = agent?.status === "streaming" || agent?.status === "processing";
  const nodes = agent ? buildTimeline(rows, paused, live) : [];
  // The raw list hides the live phase beat while paused (its waiting rows
  // stand in for it).
  const pausedCount = (paused ?? []).length;
  const rawRows = pausedCount > 0 ? rows.filter((r) => r.step_index >= 0) : rows;
  const showTimeline = agent !== undefined && !embedded && !technical;
  const hasShown = showTimeline ? nodes.length > 0 : rawRows.length > 0 || pausedCount > 0;
  const realCount = rawRows.filter((r) => r.step_index >= 0).length;

  const loadTrace = async () => {
    setLoading(true);
    setError(null);
    try {
      const fetchedRecords = await fetchRunTrace(sessionId, runId);
      setRecords(fetchedRecords);
      setMessageTrace(sessionId, runId, fetchedRecords);
      setFetched(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load trace");
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    // Fetch on first expand of a replayed (empty) trace.
    if (next && rows.length === 0 && !fetched && !loading && !live) void loadTrace();
  };

  const OuterIcon = open ? ChevronDown : ChevronRight;
  const list = usePresence(open, TRACE_EXIT_MS);
  const summary = agent
    ? summarizeTurn({
        nodes,
        trace: rows,
        summary: agent.summary,
        paused,
        status: agent.status,
      })
    : null;

  return (
    <div className="mt-2 text-xs">
      {agent && !embedded && summary && (
        <div className="flex items-center gap-2">
          <AgentSummaryBadge
            label={turnSummaryText(summary, t, agent.writing)}
            phase={summary.phase}
            open={open}
            onToggle={handleToggle}
          />
        </div>
      )}
      {!agent && !embedded && (
        <button
          type="button"
          onClick={handleToggle}
          className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          aria-expanded={open}
        >
          <OuterIcon className="h-3 w-3" /> {t("trace.label")}
          {realCount > 0 && <> · {t("trace.step", { count: realCount })}</>}
        </button>
      )}

      {/* The list grows down from the toggle and collapses back into it
          (@starting-style gives the first-mount grow); it stays mounted
          through the exit. */}
      {list.mounted && (
        <div
          data-state={list.state}
          data-testid="trace-list"
          inert={!open}
          className="grid transition-[grid-template-rows,opacity] motion-reduce:transition-none data-[state=open]:grid-rows-[1fr] data-[state=open]:opacity-100 data-[state=open]:duration-200 data-[state=open]:ease-out starting:data-[state=open]:grid-rows-[0fr] starting:data-[state=open]:opacity-0 data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 data-[state=closed]:duration-200 data-[state=closed]:ease-in"
        >
          <div className="min-h-0 overflow-hidden">
            {loading && <p className="mt-1 text-muted-foreground">{t("trace.loading")}</p>}
            {error && (
              <p className="mt-1 text-destructive">
                {error}{" "}
                <button type="button" onClick={() => void loadTrace()} className="underline">
                  {t("trace.retry")}
                </button>
              </p>
            )}
            {!loading && !error && !hasShown && fetched && (
              <p className="mt-1 italic text-muted-foreground">{t("trace.empty")}</p>
            )}
            {hasShown && (
              // Switching views fades the new one in.
              <div
                key={showTimeline ? "timeline" : "technical"}
                className="animate-in fade-in-0 duration-200 motion-reduce:animate-none"
              >
                {showTimeline ? (
                  <ActivityTimeline
                    nodes={nodes}
                    sessionId={sessionId}
                    writing={agent?.writing === true}
                  />
                ) : (
                  <TraceTechnical rows={rawRows} paused={paused} />
                )}
              </div>
            )}
            {/* A last-resort view, so a quiet link after the last step (like a CI
                log's "View raw logs"), not a control on the header row. The way
                back sits in the same spot. */}
            {agent && !embedded && hasShown && (
              <button
                type="button"
                aria-pressed={technical}
                onClick={() => setTechnical((v) => !v)}
                className="mt-2 ml-[30px] text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {technical
                  ? t("trace.tl.backToActivity", "Back to activity view")
                  : t("trace.tl.rawSteps", "View raw steps ({{count}})", { count: realCount })}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
