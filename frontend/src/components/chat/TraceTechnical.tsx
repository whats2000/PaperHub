import { ChevronDown, ChevronRight, Clock, Loader2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { RejectionPill } from "@/components/states/RejectionPill";
import { cn } from "@/lib/utils";
import type { PausedCard, ToolCallRecord } from "@/types/domain";

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

export function TraceArgs({ args }: { args: Record<string, unknown> | string | null | undefined }) {
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
          <span className="not-italic font-medium">Why:</span> {renderVal(reasonVal)}
        </p>
      )}
      {ARGS_KNOWN_KEYS.map((k) => {
        if (!(k in parsed)) return null;
        const v = parsed[k];
        return (
          <div key={k}>
            <span className="font-medium">{k}:</span> {renderVal(v)}
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
const RESULT_KNOWN_KEYS = ["count", "title", "cache_hit", "papers_id", "paper_content_id"] as const;

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
    ([k]) => !RESULT_KNOWN_KEYS.includes(k as (typeof RESULT_KNOWN_KEYS)[number]) && k !== "error",
  );

  const errorVal = source["error"];

  return (
    <div className="space-y-0.5">
      {errorVal != null && (
        <div className="text-destructive">
          <span className="font-medium">error:</span> {renderVal(errorVal)}
        </div>
      )}
      {RESULT_KNOWN_KEYS.map((k) => {
        if (!(k in source)) return null;
        const v = source[k];
        return (
          <div key={k}>
            <span className="font-medium">{k}:</span> {renderVal(v)}
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
// TraceTechnical — the raw step list (FR-02's "Technical view"): one row per
// tool_calls record, `args_redacted` + `result_summary` on expand, rejections
// yellow, errors red. The Feedback page embeds it as is.
// ---------------------------------------------------------------------------
export function TraceTechnical({
  rows: shown,
  paused,
}: {
  rows: ToolCallRecord[];
  /** H5a: the cards the run is paused on; each waiting call shows a
   *  "paused · waiting for your OK" row in place of the live phase. */
  paused?: PausedCard[] | null;
}) {
  const { t } = useTranslation("chat");
  // Per-row toggle: which rows are expanded
  const [openSteps, setOpenSteps] = useState<Set<string>>(new Set());
  const pausedCalls = [...new Set((paused ?? []).map((c) => c.tool || c.kind))];
  // An agent turn's rows after a "Step N" row (its tool calls and their
  // sub-agent / pipeline rows, and the live beat) nest under that step.
  const firstStep = shown.findIndex((r) => r.tool === "agent:step");
  const nested = shown.map((r, i) => firstStep >= 0 && i > firstStep && r.tool !== "agent:step");
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
    <ul className="mt-1 space-y-0.5 font-mono">
      {shown.map((r, i) => {
        // Nested rows: indented under their step, with a guide line.
        const nest = nested[i] ? "ml-3 border-l border-border pl-1" : "";
        // Live heartbeat — a single in-progress phase indicator, not a
        // clickable step. No model/latency/status/JSON; just a spinner,
        // the phase name, and elapsed seconds.
        if (r.step_index < 0) {
          const rs = r.result_summary_json;
          const elapsed = rs && typeof rs.elapsed_s === "number" ? rs.elapsed_s : null;
          return (
            <li
              key="live-stage"
              data-stage="live"
              data-nested={nested[i] || undefined}
              className={cn("flex items-center gap-2 px-2 py-1 text-muted-foreground", nest)}
            >
              <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden />
              <span className="font-medium text-foreground/80">
                {stageLabel(r.tool)}
                {"…"}
              </span>
              {elapsed != null && elapsed > 0 && (
                <span className="tabular-nums text-[11px] opacity-60">{elapsed}s</span>
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
              <RowIcon className="h-3 w-3 shrink-0" />[{r.branch || "main"}#{r.step_index}]{" "}
              {r.tool === "agent:step" ? (
                <span data-row="agent-step">
                  {t("trace.agentStep", { n: stepNumber(r) ?? r.step_index })}
                  {" → "}
                  {stepToolNames(r.result_summary_json).join(", ") || t("trace.answer")} (
                  {r.model ?? "-"})
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
      {pausedCalls.map((tool) => (
        <li
          key={`paused-${tool}`}
          data-stage="paused"
          className={cn(
            "flex items-center gap-2 px-2 py-1 text-muted-foreground animate-in fade-in-0 duration-200 motion-reduce:animate-none",
            firstStep >= 0 && "ml-3 border-l border-border pl-1",
          )}
        >
          <span className="font-medium text-foreground/80">{tool}</span>
          <span>{t("trace.paused", "paused · waiting for your OK")}</span>
          <Clock
            className="ml-auto h-3 w-3 shrink-0 text-amber-600 motion-safe:animate-pulse dark:text-amber-400"
            aria-hidden
          />
        </li>
      ))}
    </ul>
  );
}
