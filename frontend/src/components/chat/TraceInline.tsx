import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { fetchRunTrace } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import type { ToolCallRecord } from "@/types/domain";

// ---------------------------------------------------------------------------
// Safe string rendering for unknown values — avoids [object Object] output.
// ---------------------------------------------------------------------------
function renderVal(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean" || typeof v === "bigint") {
    return String(v);
  }
  // object, symbol, or anything else — JSON fallback
  return JSON.stringify(v) ?? "";
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

  // Render as plain pre if we couldn't parse it into an object
  if (typeof parsed === "string") {
    return (
      <pre className="text-[10px] text-muted-foreground overflow-x-auto whitespace-pre-wrap break-all">
        {parsed}
      </pre>
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
        <pre className="text-[10px] text-muted-foreground overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify(Object.fromEntries(unknownEntries), null, 2)}
        </pre>
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

  // Render as plain pre if we couldn't parse it into an object
  if (typeof parsed === "string") {
    return (
      <pre className="text-[10px] text-muted-foreground overflow-x-auto whitespace-pre-wrap break-all">
        {parsed}
      </pre>
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
        <pre className="text-[10px] text-muted-foreground overflow-x-auto whitespace-pre-wrap break-all">
          {JSON.stringify(Object.fromEntries(unknownEntries), null, 2)}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row status → className helper (extracted to keep JSX readable)
// ---------------------------------------------------------------------------
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
}: {
  trace: ToolCallRecord[];
  sessionId: number;
  runId: number;
}) {
  // Outer toggle: show/hide the whole step list
  const [open, setOpen] = useState(false);
  // Per-row toggle: which rows are expanded
  const [openSteps, setOpenSteps] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Whether a fetch has completed (or the message already carried a trace) —
  // distinguishes "not fetched yet" from "fetched, genuinely empty".
  const [fetched, setFetched] = useState(trace.length > 0);
  // Records fetched on demand (replayed turn); also cached to the store so a
  // remount sees them via the message's `trace` prop.
  const [records, setRecords] = useState<ToolCallRecord[]>([]);
  const setMessageTrace = useChatStore((s) => s.setMessageTrace);

  // Prefer the streamed/replayed prop; fall back to what we fetched here.
  const shown = trace.length > 0 ? trace : records;
  const hasShown = shown.length > 0;

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
      {/* Outer toggle — controls visibility of the entire list */}
      <button
        type="button"
        onClick={handleToggle}
        className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
        aria-expanded={open}
      >
        <OuterIcon className="h-3 w-3" /> Trace
        {hasShown && (
          <>
            {" "}
            · {shown.length} {shown.length === 1 ? "step" : "steps"}
          </>
        )}
      </button>

      {open && loading && (
        <p className="mt-1 text-muted-foreground">Loading trace…</p>
      )}
      {open && error && (
        <p className="mt-1 text-destructive">
          {error}{" "}
          <button type="button" onClick={() => void loadTrace()} className="underline">
            retry
          </button>
        </p>
      )}
      {open && !loading && !error && !hasShown && fetched && (
        <p className="mt-1 italic text-muted-foreground">No steps recorded.</p>
      )}

      {open && hasShown && (
        <ul className="mt-1 space-y-0.5 font-mono">
          {shown.map((r) => {
            const key = `${r.branch}-${r.step_index}`;
            const isOpen = openSteps.has(key);
            const RowIcon = isOpen ? ChevronDown : ChevronRight;
            return (
              <li key={key} data-status={r.status} className={rowClasses(r.status)}>
                {/* Per-row disclosure button */}
                <button
                  type="button"
                  onClick={() => toggleStep(key)}
                  aria-expanded={isOpen}
                  className="w-full text-left flex items-center gap-1"
                >
                  <RowIcon className="h-3 w-3" />
                  [{r.branch || "main"}#{r.step_index}] {r.agent} · {r.tool}{" "}
                  ({r.model ?? "-"}) {r.latency_ms}ms {r.status}
                  {r.error && ` — ${r.error}`}
                </button>

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
  );
}
