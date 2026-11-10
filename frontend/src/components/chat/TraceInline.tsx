import { useState, type ReactNode } from "react";

import { usePresence } from "@/hooks/usePresence";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchRunTrace } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";
import type { ToolCallRecord } from "@/types/domain";
import { RejectionPill } from "@/components/states/RejectionPill";
import { JsonTree } from "./JsonTree";

// ---------------------------------------------------------------------------
// Render a known-key value: scalars inline, objects/arrays as a JsonTree so
// nested payloads stay readable instead of escaping into `\"`/`\n` blobs.
// ---------------------------------------------------------------------------
function renderVal(v: unknown): ReactNode {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") {
    // A scalar string may still be embedded JSON (e.g. a serialized list) —
    // hand it to JsonTree, which deep-parses and renders raw text otherwise.
    return <JsonTree value={v} />;
  }
  if (typeof v === "number" || typeof v === "boolean" || typeof v === "bigint") {
    return String(v);
  }
  // object / array — structural render
  return <JsonTree value={v} />;
}

// ---------------------------------------------------------------------------
// Helper: parse a value that may be a JSON string or already an object.
// Returns null when input is null/undefined/empty string.
// Returns the original string (not null) when it's a non-JSON string, so
// callers can still render it.
// ---------------------------------------------------------------------------
function parseJsonField(
  raw: Record<string, unknown> | string | null | undefined,
): Record<string, unknown> | string | null {
  if (raw == null) return null;
  if (typeof raw === "string") {
    if (raw.trim() === "") return null;
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
      // JSON parsed but not an object (e.g. a bare number) — return as string
      return raw;
    } catch {
      // Malformed JSON — render as plain string
      return raw;
    }
  }
  return raw;
}

// ---------------------------------------------------------------------------
// TraceArgs — renders args_redacted_json with `reason` first
// ---------------------------------------------------------------------------
const ARGS_KNOWN_KEYS = ["query", "paper_id", "arxiv_id", "mode", "max_results"] as const;

export function TraceArgs({
  args,
}: {
  args: Record<string, unknown> | string | null | undefined;
}) {
  const parsed = parseJsonField(args);
  if (parsed === null) return null;

  // Couldn't parse into an object — still deep-render via JsonTree (handles
  // embedded JSON strings + raw prose without escaping).
  if (typeof parsed === "string") {
    return (
      <div className="text-[11px] text-muted-foreground">
        <JsonTree value={parsed} />
      </div>
    );
  }

  const reasonVal = parsed["reason"];

  // Build unknown-keys fallback object
  const unknownEntries: [string, unknown][] = Object.entries(parsed).filter(
    ([k]) => !ARGS_KNOWN_KEYS.includes(k as (typeof ARGS_KNOWN_KEYS)[number]) && k !== "reason",
  );

  return (
    <div className="space-y-0.5">
      {reasonVal != null && (
        <p className="italic">
          <span className="not-italic font-medium">Why:</span>{" "}
          {renderVal(reasonVal)}
        </p>
      )}
      {ARGS_KNOWN_KEYS.map((k) => {
        if (!(k in parsed)) return null;
        const v = parsed[k];
        return (
          <div key={k}>
            <span className="font-medium">{k}:</span>{" "}
            {renderVal(v)}
          </div>
        );
      })}
      {unknownEntries.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <JsonTree value={Object.fromEntries(unknownEntries)} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TraceResult — renders result_summary_json, unpacking .summary if present
// ---------------------------------------------------------------------------
const RESULT_KNOWN_KEYS = [
  "count",
  "title",
  "cache_hit",
  "papers_id",
  "paper_content_id",
] as const;

export function TraceResult({
  result,
}: {
  result: Record<string, unknown> | string | null | undefined;
}) {
  const parsed = parseJsonField(result);
  if (parsed === null) return null;

  // Couldn't parse into an object — deep-render via JsonTree.
  if (typeof parsed === "string") {
    return (
      <div className="text-[11px] text-muted-foreground">
        <JsonTree value={parsed} />
      </div>
    );
  }

  // Unpack .summary if present
  const source: Record<string, unknown> =
    "summary" in parsed &&
    parsed["summary"] !== null &&
    typeof parsed["summary"] === "object" &&
    !Array.isArray(parsed["summary"])
      ? (parsed["summary"] as Record<string, unknown>)
      : parsed;

  const unknownEntries: [string, unknown][] = Object.entries(source).filter(
    ([k]) =>
      !RESULT_KNOWN_KEYS.includes(k as (typeof RESULT_KNOWN_KEYS)[number]) &&
      k !== "error",
  );

  const errorVal = source["error"];

  return (
    <div className="space-y-0.5">
      {errorVal != null && (
        <div className="text-destructive">
          <span className="font-medium">error:</span>{" "}
          {renderVal(errorVal)}
        </div>
      )}
      {RESULT_KNOWN_KEYS.map((k) => {
        if (!(k in source)) return null;
        const v = source[k];
        return (
          <div key={k}>
            <span className="font-medium">{k}:</span>{" "}
            {renderVal(v)}
          </div>
        );
      })}
      {unknownEntries.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <JsonTree value={Object.fromEntries(unknownEntries)} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row status → className helper (extracted to keep JSX readable)
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Heartbeat rows (step_index < 0) are synthetic, live-only "stage" beats — not
// real tool_calls. Humanize the tool id into a phase label: "report:planning"
// → "Planning". Derived from the literal tool id (a cosmetic transform), with
// the raw id as the fallback.
// ---------------------------------------------------------------------------
function stageLabel(tool: string): string {
  const name = tool.includes(":") ? tool.split(":").pop()! : tool;
  if (!name) return tool;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// ---------------------------------------------------------------------------
// Agent turns (H4): an `agent:step` row reads "Step n → tools called"; a
// `tool:<name>` row reads the tool name plus a short argument summary. Tool
// names and argument keys stay literal (they are ids, not prose).
// ---------------------------------------------------------------------------
const BRIEF_CHARS = 60;
/** Matches the list's closed-state duration-200 collapse. */
const TRACE_EXIT_MS = 200;

function quoteArg(v: unknown): string {
  return JSON.stringify(v) ?? "";
}

/** `paper=3 · section="Method" · query="…" · brief="…" · ids=4`. */
function toolArgSummary(args: Record<string, unknown> | null | undefined): string {
  const parsed = parseJsonField(args);
  if (parsed === null || typeof parsed === "string") return "";
  const parts: string[] = [];
  for (const key of ["paper", "section", "query"] as const) {
    if (parsed[key] !== undefined && parsed[key] !== null) {
      parts.push(`${key}=${quoteArg(parsed[key])}`);
    }
  }
  const brief = parsed["brief"];
  if (typeof brief === "string" && brief) {
    const cut = brief.length > BRIEF_CHARS ? `${brief.slice(0, BRIEF_CHARS)}…` : brief;
    parts.push(`brief=${JSON.stringify(cut)}`);
  }
  const ids = parsed["ids"];
  if (Array.isArray(ids)) parts.push(`ids=${ids.length}`);
  return parts.join(" · ");
}

/** The tool names an `agent:step` row called, in order. */
function stepToolNames(result: Record<string, unknown> | null | undefined): string[] {
  const parsed = parseJsonField(result);
  if (parsed === null || typeof parsed === "string") return [];
  const calls = parsed["tool_calls"];
  if (!Array.isArray(calls)) return [];
  return calls
    .map((c) => (c && typeof c === "object" ? (c as { name?: unknown }).name : null))
    .filter((n): n is string => typeof n === "string");
}

function stepNumber(r: ToolCallRecord): number | null {
  const args = parseJsonField(r.args_redacted_json);
  if (args === null || typeof args === "string") return null;
  return typeof args["step"] === "number" ? args["step"] : null;
}

function rowClasses(status: ToolCallRecord["status"]): string {
  return `px-2 py-0.5 rounded ${
    status === "error"
      ? "bg-destructive/10 text-destructive"
      : status === "rejected"
        ? "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-900 dark:text-yellow-200"
        : "text-muted-foreground"
  }`;
}

// ---------------------------------------------------------------------------
// TraceInline — main export
//
// Always renders a "Trace" toggle for an assistant turn that has a run_id.
// Live-streamed turns already carry `trace`; replayed turns arrive with an
// empty `trace`, so the first expand lazily fetches the run's tool_calls
// (GET …/runs/{id}/trace) and caches them onto the message via the store.
// ---------------------------------------------------------------------------
export function TraceInline({
  trace,
  sessionId,
  runId,
  embedded = false,
  badge,
}: {
  trace: ToolCallRecord[];
  sessionId: number;
  runId: number;
  /** Replaces the "Trace" button as the list's toggle (the agent turn
   *  summary badge). */
  badge?: (ctl: { open: boolean; toggle: () => void }) => ReactNode;
  /** Under a caller's own toggle (the Feedback page's "View trace"): no
   *  header, always open, and `trace` is the already-fetched result, so an
   *  empty one shows the empty state. */
  embedded?: boolean;
}) {
  const { t } = useTranslation("chat");
  // Outer toggle: show/hide the whole step list
  const [open, setOpen] = useState(embedded);
  // Per-row toggle: which rows are expanded
  const [openSteps, setOpenSteps] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Whether a fetch has completed (or the message already carried a trace) —
  // distinguishes "not fetched yet" from "fetched, genuinely empty".
  const [fetched, setFetched] = useState(embedded || trace.length > 0);
  // Records fetched on demand (replayed turn); also cached to the store so a
  // remount sees them via the message's `trace` prop.
  const [records, setRecords] = useState<ToolCallRecord[]>([]);
  const setMessageTrace = useChatStore((s) => s.setMessageTrace);

  // Prefer the streamed/replayed prop; fall back to what we fetched here.
  const shown = trace.length > 0 ? trace : records;
  const hasShown = shown.length > 0;
  // Live heartbeat beats (step_index < 0) are not "steps" — count only the real
  // tool_calls rows so the toggle reads "3 steps", not "4" with a beat in flight.
  const realCount = shown.filter((r) => r.step_index >= 0).length;
  // An agent turn's rows after a "Step N" row (its tool calls and their
  // sub-agent / pipeline rows, and the live beat) nest under that step.
  const firstStep = shown.findIndex((r) => r.tool === "agent:step");
  const nested = shown.map(
    (r, i) => firstStep >= 0 && i > firstStep && r.tool !== "agent:step",
  );

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
    if (next && !hasShown && !fetched && !loading) void loadTrace();
  };

  const OuterIcon = open ? ChevronDown : ChevronRight;
  const list = usePresence(open, TRACE_EXIT_MS);

  const toggleStep = (key: string) =>
    setOpenSteps((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });

  return (
    <div className="mt-2 text-xs">
      {/* One toggle for the list: the agent turn summary when there is one
          (it takes the Trace button's place, fading in when the summary
          arrives at the end of a live turn), else the Trace button. */}
      {badge && !embedded && (
        <div className="animate-in fade-in-0 duration-200 motion-reduce:animate-none">
          {badge({ open, toggle: handleToggle })}
        </div>
      )}
      {!badge && !embedded && (
        <button
          type="button"
          onClick={handleToggle}
          className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          aria-expanded={open}
        >
          <OuterIcon className="h-3 w-3" /> {t("trace.label")}
          {realCount > 0 && (
            <>
              {" "}
              · {t("trace.step", { count: realCount })}
            </>
          )}
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
            {loading && (
              <p className="mt-1 text-muted-foreground">{t("trace.loading")}</p>
            )}
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
              <ul className="mt-1 space-y-0.5 font-mono">
                {shown.map((r, i) => {
                  // Nested rows: indented under their step, with a guide line.
                  const nest = nested[i]
                    ? "ml-3 border-l border-border pl-1"
                    : "";
                  // Live heartbeat — a single in-progress phase indicator, not a
                  // clickable step. No model/latency/status/JSON; just a spinner,
                  // the phase name, and elapsed seconds.
                  if (r.step_index < 0) {
                    const rs = r.result_summary_json;
                    const elapsed =
                      rs && typeof rs.elapsed_s === "number" ? rs.elapsed_s : null;
                    return (
                      <li
                        key="live-stage"
                        data-stage="live"
                        data-nested={nested[i] || undefined}
                        className={cn(
                          "flex items-center gap-2 px-2 py-1 text-muted-foreground",
                          nest,
                        )}
                      >
                        <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden />
                        <span className="font-medium text-foreground/80">
                          {stageLabel(r.tool)}
                          {"…"}
                        </span>
                        {elapsed != null && elapsed > 0 && (
                          <span className="tabular-nums text-[11px] opacity-60">
                            {elapsed}s
                          </span>
                        )}
                      </li>
                    );
                  }
                  const key = `${r.branch}-${r.step_index}`;
                  const isOpen = openSteps.has(key);
                  const RowIcon = isOpen ? ChevronDown : ChevronRight;
                  return (
                    <li
                      key={key}
                      data-status={r.status}
                      data-nested={nested[i] || undefined}
                      className={cn(rowClasses(r.status), nest)}
                    >
                      {/* Per-row disclosure button */}
                      <button
                        type="button"
                        onClick={() => toggleStep(key)}
                        aria-expanded={isOpen}
                        className="w-full text-left flex items-center gap-1"
                      >
                        <RowIcon className="h-3 w-3 shrink-0" />
                        [{r.branch || "main"}#{r.step_index}]{" "}
                        {r.tool === "agent:step" ? (
                          <span data-row="agent-step">
                            {t("trace.agentStep", { n: stepNumber(r) ?? r.step_index })}
                            {" → "}
                            {stepToolNames(r.result_summary_json).join(", ") ||
                              t("trace.answer")}{" "}
                            ({r.model ?? "-"})
                          </span>
                        ) : r.tool.startsWith("tool:") ? (
                          <span data-row="agent-tool">
                            <span className="font-medium text-foreground/80">
                              {r.tool.slice("tool:".length)}
                            </span>
                            {toolArgSummary(r.args_redacted_json) &&
                              ` ${toolArgSummary(r.args_redacted_json)}`}
                          </span>
                        ) : (
                          <>
                            {r.agent} · {r.tool} ({r.model ?? "-"})
                          </>
                        )}{" "}
                        {r.latency_ms}ms{" "}
                        {/* ok/error stay inline; a rejection's reason can be long, so
                            it drops to its own line below as a RejectionPill. */}
                        {r.status !== "rejected" && (
                          <>
                            {r.status}
                            {r.error && ` — ${r.error}`}
                          </>
                        )}
                      </button>

                      {/* Rejection — a deliberate policy stop, on its own line so the
                          full reason reads cleanly instead of wrapping by the metadata. */}
                      {r.status === "rejected" && (
                        <div className="mt-1 mb-1.5 ml-4">
                          <RejectionPill reason={r.error ?? ""} />
                        </div>
                      )}

                      {/* Expanded detail */}
                      {isOpen && (
                        <div className="mt-1 ml-4 space-y-1 text-xs">
                          <TraceArgs args={r.args_redacted_json} />
                          <TraceResult result={r.result_summary_json} />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
