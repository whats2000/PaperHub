/**
 * The activity timeline (FR-02, H5a wave 3): the mapping from a turn's
 * `tool_calls` rows to plain-language nodes, ported from the signed-off
 * prototype's `buildTimeline` / `nodeView` / `queries`
 * (docs/superpowers/designs/2026-11-13-plan-h5a/h5a_prototype.html).
 *
 * - A `tool:<name>` row starts a node. The other rows (`paper_search:*`,
 *   `outbound:review`, `agent:await_approval`, `agent:approve`, …) belong to
 *   the call whose id they record (`tool_call_id`); rows recorded before that
 *   column existed fall back to the latest node (positional grouping).
 * - `agent:step` rows are not nodes: their step and model go to the next
 *   nodes' Details; the final step (an `answer`) is "Wrote the answer".
 * - A running call has no row until it closes: the live beat lists the calls
 *   in flight, each a running node (waiting when a paused card names it).
 * - A sub-step failure never makes the node red; only a failed tool call does.
 *
 * Pure data: the component turns it into localized text.
 */
import type { AgentSummary, PausedCard, ToolCallRecord } from "@/types/domain";

export type NodeState = "done" | "running" | "waiting" | "skipped" | "failed";

type Obj = Record<string, unknown>;

export interface ToolNode {
  kind: "tool";
  key: string;
  /** The tool's name (`search_papers`), the row's `tool` without `tool:`. */
  name: string;
  callId: string | null;
  args: Obj;
  result: Obj;
  /** null while the call runs (its row is written when it closes). */
  row: ToolCallRecord | null;
  children: ToolCallRecord[];
  /** The `agent:step` row that called it, when recorded. */
  step: ToolCallRecord | null;
  state: NodeState;
  /** Why a node is skipped: the user declined / dismissed it, or Stop. */
  skippedBy?: "declined" | "dismissed" | "stopped" | "pending";
  /** The cards this call waits on (paused run). */
  cards: PausedCard[];
}

export interface AnswerNode {
  kind: "answer";
  key: string;
  row: ToolCallRecord | null;
  /** Live: the model is thinking / writing (no answer row yet). */
  running: boolean;
}

export type TimelineNode = ToolNode | AnswerNode;

function obj(v: unknown): Obj {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
}

const isStep = (r: ToolCallRecord) => r.tool === "agent:step";
const isTool = (r: ToolCallRecord) => r.tool.startsWith("tool:");
const isAnswerStep = (r: ToolCallRecord) => typeof obj(r.result_summary_json).answer === "string";

/** The live beat's calls in flight (`{id, name, args}`), if any. */
function beatCalls(beat: ToolCallRecord | undefined): { id: string; name: string; args: Obj }[] {
  const calls = obj(beat?.result_summary_json).calls;
  if (!Array.isArray(calls)) return [];
  return calls
    .map((c) => obj(c))
    .filter((c) => typeof c.id === "string" && typeof c.name === "string")
    .map((c) => ({ id: c.id as string, name: c.name as string, args: obj(c.args) }));
}

function closedState(row: ToolCallRecord): Pick<ToolNode, "state" | "skippedBy"> {
  const result = obj(row.result_summary_json);
  if (row.status === "error" && row.error === "cancelled") {
    return { state: "skipped", skippedBy: "stopped" };
  }
  if (row.status !== "ok") return { state: "failed" };
  if (result.declined === true) return { state: "skipped", skippedBy: "declined" };
  if (obj(result.confirm).decision === "dismissed") {
    return { state: "skipped", skippedBy: "dismissed" };
  }
  if (result.pending_action_id) return { state: "skipped", skippedBy: "pending" };
  return { state: "done" };
}

/**
 * The nodes of one turn, in call order. `paused` are the cards the live run
 * waits on; `live` says whether the turn is still running (a thinking /
 * writing node then closes the list until the answer row arrives).
 */
export function buildTimeline(
  rows: ToolCallRecord[],
  paused: PausedCard[] | null | undefined = null,
  live = false,
): TimelineNode[] {
  const real = rows.filter((r) => r.step_index >= 0).sort((a, b) => a.step_index - b.step_index);
  const beat = rows.filter((r) => r.step_index < 0).at(-1);
  const nodes: TimelineNode[] = [];
  const byCall = new Map<string, ToolNode>();
  const orphans = new Map<string, ToolCallRecord[]>();
  let cur: ToolNode | null = null;
  let step: ToolCallRecord | null = null;
  for (const r of real) {
    if (isStep(r)) {
      if (isAnswerStep(r)) {
        cur = null;
        nodes.push({ kind: "answer", key: `answer-${r.step_index}`, row: r, running: false });
      } else {
        step = r;
      }
      continue;
    }
    if (isTool(r)) {
      const args = obj(r.args_redacted_json);
      const callId = r.tool_call_id ?? (typeof args.call_id === "string" ? args.call_id : null);
      const node: ToolNode = {
        kind: "tool",
        key: `tool-${r.step_index}`,
        name: r.tool.slice("tool:".length),
        callId,
        args,
        result: obj(r.result_summary_json),
        row: r,
        children: callId ? (orphans.get(callId) ?? []) : [],
        step,
        cards: [],
        ...closedState(r),
      };
      if (callId) {
        orphans.delete(callId);
        byCall.set(callId, node);
      }
      nodes.push(node);
      cur = node;
      continue;
    }
    // An internal row: its call's node, or (no id recorded) the latest one.
    const owner = r.tool_call_id ? byCall.get(r.tool_call_id) : cur;
    if (owner) owner.children.push(r);
    else if (r.tool_call_id) {
      // Its call is still running (rows close before their call does).
      orphans.set(r.tool_call_id, [...(orphans.get(r.tool_call_id) ?? []), r]);
    }
  }
  // Running calls: named by the live beat, before their rows exist.
  const calls = beatCalls(beat);
  for (const c of calls) {
    if (byCall.has(c.id)) continue;
    const node: ToolNode = {
      kind: "tool",
      key: `call-${c.id}`,
      name: c.name,
      callId: c.id,
      args: c.args,
      result: {},
      row: null,
      children: orphans.get(c.id) ?? [],
      step,
      state: "running",
      cards: [],
    };
    byCall.set(c.id, node);
    nodes.push(node);
  }
  // Waiting: the cards a paused run waits on name their call (older streams
  // without call ids match a running call by tool name).
  for (const card of paused ?? []) {
    const node =
      (card.call_id ? byCall.get(card.call_id) : undefined) ??
      nodes.find((n): n is ToolNode => n.kind === "tool" && n.row === null && n.name === card.tool);
    if (node && node.row === null) {
      node.cards.push(card);
      node.state = "waiting";
    }
  }
  // Between steps (or writing): the model is working on the next step.
  const answered = nodes.some((n) => n.kind === "answer");
  const running = nodes.some((n) => n.kind === "tool" && n.row === null);
  if (live && !answered && !running && (paused ?? []).length === 0) {
    nodes.push({ kind: "answer", key: "thinking", row: null, running: true });
  }
  return nodes;
}

// ------------------------------------------------------------ search queries

export type QueryOutcome =
  | "sent"
  | "rewritten"
  | "held"
  | "declined"
  | "stopped"
  | "blocked"
  | "timeout";

export interface SearchQuery {
  /** What the search wrote. */
  text: string;
  destination: string;
  /** What left (a rewrite or the user's edit), when it differs. */
  sent: string | null;
  outcome: QueryOutcome;
  /** Rewritten: the guard's reason. Held: why the user is asked. */
  reason: string | null;
  /** Blocked by a sensitive term (the term itself). */
  term: string | null;
  /** Sent after the user's OK on its held card (edited: after an edit). */
  approved?: "ok" | "edited";
  /** Its held card, while it waits. */
  heldId?: string;
}

/**
 * Every query a search sent or tried to send, with its outcome, from the
 * search node's internal rows; exact-ID lookups are listed apart (they are
 * sent without review).
 */
export function searchQueries(
  children: ToolCallRecord[],
  stopped: boolean,
): { queries: SearchQuery[]; lookups: string[] } {
  const queries: SearchQuery[] = [];
  const lookups: string[] = [];
  for (const r of children) {
    if (r.tool !== "outbound:review") continue;
    const res = obj(r.result_summary_json);
    const request = obj(obj(r.args_redacted_json).request);
    const text = typeof request.text === "string" ? request.text : "";
    const destination = typeof request.destination === "string" ? request.destination : "";
    if (res.stage === "exact_id") {
      lookups.push(text);
      continue;
    }
    if (res.stage === "user") continue; // the approved re-send of a held query
    const sentText = typeof res.sent_text === "string" ? res.sent_text : null;
    const q: SearchQuery = {
      text,
      destination,
      sent: null,
      outcome: "sent",
      reason: typeof res.reason === "string" ? res.reason : null,
      term: typeof res.term === "string" ? res.term : null,
    };
    const decision = res.decision;
    if (decision === "block") {
      q.outcome = "blocked";
    } else if (decision === "ask" || decision === "hold") {
      const heldId = typeof res.held_id === "string" ? res.held_id : null;
      const wait = children.find(
        (c) => c.tool === "agent:await_approval" && obj(c.args_redacted_json).id === heldId,
      );
      const waitResult = obj(wait?.result_summary_json);
      if (!wait) {
        q.outcome = stopped ? "stopped" : "held";
        if (heldId) q.heldId = heldId;
      } else if (waitResult.decision === "approved") {
        const resent = children.find(
          (c) => c.tool === "outbound:review" && obj(c.result_summary_json).stage === "user",
        );
        const resentText = obj(resent?.result_summary_json).sent_text;
        q.sent = typeof resentText === "string" && resentText !== text ? resentText : null;
        q.approved = waitResult.edited === true ? "edited" : "ok";
      } else if (waitResult.decision === "stopped") {
        q.outcome = "stopped";
      } else {
        q.outcome = "declined";
      }
    } else if (sentText !== null && sentText !== text) {
      q.outcome = "rewritten";
      q.sent = sentText;
    }
    if (q.outcome === "sent" || q.outcome === "rewritten") {
      const left = q.sent ?? text;
      const failed = children.find(
        (c) =>
          c.tool.endsWith("search_web") &&
          c.status === "error" &&
          obj(c.args_redacted_json).built_query === left,
      );
      if (failed) q.outcome = "timeout";
    }
    queries.push(q);
  }
  return { queries, lookups };
}

// ------------------------------------------------------------ header summary

export type TurnPhase = "paused" | "stopped" | "working" | "worked";

export interface TurnSummary {
  phase: TurnPhase;
  /** Cards waiting (paused). */
  waiting: number;
  /** Wall time of the turn, when known. */
  durationMs: number | null;
  sectionsRead: number;
  searches: number;
}

/** The toggle's summary: "Worked 10.6 s: read 2 sections, ran 1 search". */
export function summarizeTurn({
  nodes,
  trace,
  summary,
  paused,
  status,
}: {
  nodes: TimelineNode[];
  trace: ToolCallRecord[];
  summary?: AgentSummary | null;
  paused?: PausedCard[] | null;
  status?: string;
}): TurnSummary {
  const tools = nodes.filter((n): n is ToolNode => n.kind === "tool");
  const loaded = trace.some((r) => r.step_index >= 0);
  const stepTime = trace
    .filter((r) => r.step_index >= 0 && isStep(r))
    .reduce((s, r) => s + r.latency_ms, 0);
  const waiting = (paused ?? []).length;
  const phase: TurnPhase =
    status === "stopped"
      ? "stopped"
      : waiting > 0
        ? "paused"
        : status === "streaming" || status === "processing"
          ? "working"
          : "worked";
  return {
    phase,
    waiting,
    durationMs: summary?.duration_ms ?? (loaded && stepTime > 0 ? stepTime : null),
    sectionsRead: loaded
      ? tools.filter((n) => n.name === "read_section").length
      : (summary?.sections_read ?? 0),
    searches: loaded
      ? tools.filter((n) => n.name === "search_papers").length
      : (summary?.searches ?? 0),
  };
}

/** "10.6 s" / "350 ms" / "" (no time). */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

/** react-i18next's `t` (kept structural so this module stays React-free). */
type TFn = (key: string, fallback: string, values?: Record<string, unknown>) => string;

/** The toggle's text: "Worked 10.6 s: read 2 sections, ran 1 search". */
export function turnSummaryText(s: TurnSummary, t: TFn, writing = false): string {
  if (s.phase === "paused") {
    return t("trace.tl.head.paused", "Paused, waiting for your OK on {{count}} items", {
      count: s.waiting,
    });
  }
  if (s.phase === "stopped") {
    return t("trace.tl.head.stopped", "Stopped. Nothing waiting was sent or run.");
  }
  if (s.phase === "working") {
    return writing
      ? t("trace.tl.writing", "Writing the answer…")
      : t("trace.tl.head.working", "Working…");
  }
  const parts: string[] = [];
  if (s.sectionsRead > 0) {
    parts.push(
      t("trace.tl.head.readSections", "read {{count}} sections", { count: s.sectionsRead }),
    );
  }
  if (s.searches > 0) {
    parts.push(t("trace.tl.head.searches", "ran {{count}} searches", { count: s.searches }));
  }
  const duration = formatDuration(s.durationMs);
  const joined = parts.join(", ");
  if (duration) {
    return joined
      ? t("trace.tl.head.workedParts", "Worked {{duration}}: {{parts}}", {
          duration,
          parts: joined,
        })
      : t("trace.tl.head.worked", "Worked {{duration}}", { duration });
  }
  return joined
    ? t("trace.tl.head.workedNoTimeParts", "Worked: {{parts}}", { parts: joined })
    : t("trace.tl.head.workedNoTime", "Worked");
}

