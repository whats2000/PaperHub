import { describe, expect, it } from "vitest";

import {
  buildTimeline,
  formatDuration,
  searchQueries,
  summarizeTurn,
  type ToolNode,
} from "@/lib/activityTimeline";
import type { ToolCallRecord } from "@/types/domain";

let idx = 0;
function R(
  tool: string,
  args: Record<string, unknown> | null,
  result: Record<string, unknown> | null,
  opts: Partial<ToolCallRecord> = {},
): ToolCallRecord {
  return {
    run_id: 1,
    branch: "",
    step_index: idx++,
    parent_step: null,
    agent: tool.split(":")[0]!,
    tool,
    model: null,
    args_redacted_json: args,
    result_summary_json: result,
    latency_ms: 100,
    token_in: null,
    token_out: null,
    status: "ok",
    error: null,
    ...opts,
  };
}
const rev = (text: string, destination: string, res: Record<string, unknown>, call = "c3") =>
  R("outbound:review", { request: { text, destination } }, res, { tool_call_id: call });

const tools = (nodes: ReturnType<typeof buildTimeline>) =>
  nodes.filter((n): n is ToolNode => n.kind === "tool");

describe("buildTimeline", () => {
  it("groups each internal row with the call it names, even interleaved", () => {
    idx = 0;
    const rows = [
      R("agent:step", { step: 1 }, { model: "m", tool_calls: [{ name: "search_papers" }] }),
      // Parallel calls: their rows interleave; a call's row closes last.
      R("paper_search:parse", {}, {}, { tool_call_id: "s1" }),
      R("paper_search:parse", {}, {}, { tool_call_id: "s2" }),
      R(
        "outbound:review",
        { request: { text: "a", destination: "x" } },
        { stage: "review", decision: "send", sent_text: "a" },
        { tool_call_id: "s2" },
      ),
      R(
        "tool:search_papers",
        { query: "one", call_id: "s1" },
        { candidates: [] },
        { tool_call_id: "s1" },
      ),
      R(
        "tool:search_papers",
        { query: "two", call_id: "s2" },
        { candidates: [] },
        { tool_call_id: "s2" },
      ),
      R("agent:step", { step: 2 }, { model: "m", answer: "done" }, { latency_ms: 4200 }),
    ];
    const nodes = buildTimeline(rows);
    const [one, two] = tools(nodes);
    expect(one!.args.query).toBe("one");
    expect(one!.children.map((c) => c.tool)).toEqual(["paper_search:parse"]);
    expect(two!.children.map((c) => c.tool)).toEqual(["paper_search:parse", "outbound:review"]);
    expect(one!.step?.step_index).toBe(0);
    expect(nodes.at(-1)).toMatchObject({ kind: "answer", running: false });
  });

  it("falls back to positional grouping for rows without a call id", () => {
    idx = 0;
    const rows = [
      R("agent:step", { step: 1 }, { model: "m", tool_calls: [] }),
      R("tool:read_section", { paper: 12, section: "2" }, { chunk_ids: [1, 2] }),
      R("tool:search_papers", { query: "q" }, { candidates: [] }),
      R("paper_search:parse", {}, {}),
      R("outbound:review", { request: { text: "q", destination: "x" } }, { decision: "send" }),
    ];
    const [read, search] = tools(buildTimeline(rows));
    expect(read!.children).toEqual([]);
    expect(search!.children.map((c) => c.tool)).toEqual(["paper_search:parse", "outbound:review"]);
  });

  it("shows running calls from the live beat, waiting ones from the paused cards", () => {
    idx = 0;
    const rows = [
      R("agent:step", { step: 1 }, { model: "m" }),
      R(
        "tool:read_section",
        { paper: 1, section: "s", call_id: "r1" },
        { chunk_ids: [1] },
        { tool_call_id: "r1" },
      ),
      // An internal row of a call still running.
      R("paper_search:parse", {}, {}, { tool_call_id: "s1" }),
      R(
        "agent:search_papers, remove_paper",
        null,
        {
          stage: true,
          elapsed_s: 0,
          calls: [
            { id: "r1", name: "read_section", args: {} },
            { id: "s1", name: "search_papers", args: { query: "q" } },
            { id: "x1", name: "remove_paper", args: { paper: 7 } },
          ],
        },
        { step_index: -1 },
      ),
    ];
    const nodes = buildTimeline(
      rows,
      [{ kind: "action", id: "a1", tool: "remove_paper", call_id: "x1" }],
      true,
    );
    const ts = tools(nodes);
    expect(ts.map((n) => [n.name, n.state])).toEqual([
      ["read_section", "done"],
      ["search_papers", "running"],
      ["remove_paper", "waiting"],
    ]);
    expect(ts[1]!.children.map((c) => c.tool)).toEqual(["paper_search:parse"]);
    expect(ts[2]!.cards[0]!.id).toBe("a1");
    // Paused: no thinking node.
    expect(nodes.some((n) => n.kind === "answer")).toBe(false);
  });

  it("a live turn between steps shows a thinking node", () => {
    idx = 0;
    const nodes = buildTimeline(
      [R("agent:thinking", null, { stage: true, elapsed_s: 0 }, { step_index: -1 })],
      null,
      true,
    );
    expect(nodes).toEqual([{ kind: "answer", key: "thinking", row: null, running: true }]);
  });

  it("marks declined, dismissed, stopped and failed calls", () => {
    idx = 0;
    const nodes = tools(
      buildTimeline([
        R("tool:search_papers", { query: "q" }, { declined: true, note: "n" }),
        R("tool:remove_paper", { paper: 7 }, { confirm: { decision: "dismissed" } }),
        R("tool:gather", {}, {}, { status: "error", error: "cancelled" }),
        R("tool:read_section", {}, { error: "boom" }, { status: "error", error: "boom" }),
      ]),
    );
    expect(nodes.map((n) => [n.state, n.skippedBy])).toEqual([
      ["skipped", "declined"],
      ["skipped", "dismissed"],
      ["skipped", "stopped"],
      ["failed", undefined],
    ]);
  });
});

describe("searchQueries", () => {
  it("lists every query with its outcome; a sub-step failure is a timeout, not red", () => {
    idx = 0;
    const children = [
      R("paper_search:parse", {}, {}),
      rev("q arxiv", "public-web-search", {
        stage: "review",
        decision: "send",
        sent_text: "q arxiv",
      }),
      rev("dark-field TR-0412 setup", "public-web-search", {
        stage: "review",
        decision: "send",
        sent_text: "micro-defect sparsity",
        reason: "Removed the setup.",
      }),
      R(
        "paper_search:paperhub.search_web",
        { built_query: "micro-defect sparsity" },
        { summary: { error: "timed out" } },
        { status: "error" },
      ),
      rev("KESTREL defect", "public-web-search", {
        stage: "terms",
        decision: "block",
        term: "KESTREL",
      }),
      rev("held one", "public-web-search", {
        stage: "review",
        decision: "ask",
        reason: "private",
        held_id: "h1",
      }),
      R("agent:await_approval", { kind: "held", id: "h1" }, { decision: "approved", edited: true }),
      rev("held one edited", "public-web-search", {
        stage: "user",
        decision: "send",
        sent_text: "held one edited",
      }),
      rev("held two", "public-web-search", {
        stage: "review",
        decision: "ask",
        reason: "r",
        held_id: "h2",
      }),
      rev("arXiv:2403.01234", "api.semanticscholar.org", { stage: "exact_id", decision: "send" }),
    ];
    const { queries, lookups } = searchQueries(children, false);
    expect(queries.map((q) => [q.text, q.outcome])).toEqual([
      ["q arxiv", "sent"],
      ["dark-field TR-0412 setup", "timeout"],
      ["KESTREL defect", "blocked"],
      ["held one", "sent"],
      ["held two", "held"],
    ]);
    expect(queries[1]!.sent).toBe("micro-defect sparsity");
    expect(queries[2]!.term).toBe("KESTREL");
    expect(queries[3]).toMatchObject({ approved: "edited", sent: "held one edited" });
    expect(queries[4]!.heldId).toBe("h2");
    expect(lookups).toEqual(["arXiv:2403.01234"]);
    // A stopped run: a query still held was never sent.
    expect(searchQueries(children, true).queries[4]!.outcome).toBe("stopped");
  });
});

describe("summarizeTurn", () => {
  it("counts reads and searches from the trace, else from the summary", () => {
    idx = 0;
    const trace = [
      R("agent:step", { step: 1 }, { model: "m" }, { latency_ms: 3000 }),
      R("tool:read_section", {}, {}),
      R("tool:read_section", {}, {}),
      R("tool:search_papers", {}, {}),
      R("agent:step", { step: 2 }, { answer: "a" }, { latency_ms: 4000 }),
    ];
    const nodes = buildTimeline(trace);
    expect(summarizeTurn({ nodes, trace })).toEqual({
      phase: "worked",
      waiting: 0,
      durationMs: 7000,
      sectionsRead: 2,
      searches: 1,
    });
    expect(
      summarizeTurn({
        nodes: [],
        trace: [],
        summary: {
          papers_read: 1,
          steps: 2,
          tools: [],
          sections_read: 3,
          searches: 0,
          duration_ms: 10600,
        },
      }),
    ).toMatchObject({ durationMs: 10600, sectionsRead: 3, searches: 0 });
    expect(
      summarizeTurn({
        nodes: [],
        trace: [],
        paused: [{ kind: "search", id: "s", tool: "search_papers" }],
        status: "streaming",
      }),
    ).toMatchObject({ phase: "paused", waiting: 1 });
    expect(summarizeTurn({ nodes: [], trace: [], status: "stopped" }).phase).toBe("stopped");
  });

  it("formats durations", () => {
    expect(formatDuration(10600)).toBe("10.6 s");
    expect(formatDuration(350)).toBe("350 ms");
    expect(formatDuration(null)).toBe("");
  });
});
