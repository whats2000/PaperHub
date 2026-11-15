import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TraceInline } from "@/components/chat/TraceInline";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import type { ReferenceItem, ToolCallRecord } from "@/types/domain";

let idx = 0;
function R(
  tool: string,
  args: Record<string, unknown> | null,
  result: Record<string, unknown> | null,
  opts: Partial<ToolCallRecord> = {},
): ToolCallRecord {
  return {
    run_id: 1, branch: "", step_index: idx++, parent_step: null,
    agent: tool.split(":")[0]!, tool, model: null,
    args_redacted_json: args, result_summary_json: result,
    latency_ms: 400, token_in: null, token_out: null, status: "ok", error: null,
    ...opts,
  };
}

const ref = (id: number, title: string): ReferenceItem => ({
  papers_id: id, paper_content_id: id, enabled: true, added_at: "", arxiv_id: null,
  title, year: null, kind: "pdf_upload", is_private: false,
});

beforeEach(() => {
  idx = 0;
  useChatStore.setState({
    referencesBySession: { 5: [ref(12, "TR-0412 Defect segmentation report"), ref(7, "GISC")] },
  });
});
afterEach(() => useNavStore.getState().setFocusHeldId(null));

const open = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { expanded: false, name: /Agent/ }));
  return user;
};

describe("ActivityTimeline", () => {
  it("a paused turn: the waiting search reads plainly and jumps to its card", async () => {
    const trace = [
      R("agent:step", { step: 1 }, { model: "gemini/flash" }),
      R("tool:read_section", { paper: 12, section: "2 Inspection setup", call_id: "r1" },
        { chunk_ids: [812, 813] }, { tool_call_id: "r1" }),
      R("agent:search_papers", null, {
        stage: true, elapsed_s: 0,
        calls: [
          { id: "r1", name: "read_section", args: {} },
          { id: "s1", name: "search_papers", args: { query: "sparse priors" } },
        ],
      }, { step_index: -1 }),
    ];
    render(
      <TraceInline
        trace={trace} sessionId={5} runId={1}
        paused={[{ kind: "search", id: "a9", tool: "search_papers", call_id: "s1" }]}
        agent={{ status: "streaming" }}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Paused, waiting for your OK on 1 item/ }),
    ).toBeInTheDocument();
    const user = await open();
    const timeline = screen.getByTestId("activity-timeline");
    const read = within(timeline).getByText("Read “2 Inspection setup” in TR-0412 Defect segmentation report");
    expect(read.closest("li")).toHaveAttribute("data-state", "done");
    expect(within(read.closest("li")!).getByText("2 passages")).toBeInTheDocument();
    const search = within(timeline).getByText("Search papers for “sparse priors”").closest("li")!;
    expect(search).toHaveAttribute("data-state", "waiting");
    expect(
      within(search).getByText("Waiting for your OK before anything is sent."),
    ).toBeInTheDocument();
    await user.click(within(search).getByRole("button", { name: "Go to card" }));
    expect(useNavStore.getState().focusHeldId).toBe("a9");
  });

  it("a search lists its queries and outcomes; a timed-out query never turns it red", async () => {
    const trace = [
      R("agent:step", { step: 1 }, { model: "gemini/flash" }),
      R("tool:search_papers", { query: "q", call_id: "s1" },
        { query: "q", candidates: [{}, {}, {}] }, { tool_call_id: "s1", latency_ms: 3400 }),
      R("outbound:review", { request: { text: "q arxiv", destination: "public-web-search" } },
        { stage: "review", decision: "send", sent_text: "q arxiv" }, { tool_call_id: "s1" }),
      R("outbound:review", { request: { text: "TR-0412 setup", destination: "api.semanticscholar.org" } },
        { stage: "review", decision: "send", sent_text: "defect sparsity", reason: "Removed the setup." },
        { tool_call_id: "s1" }),
      R("paper_search:paperhub.search_web", { built_query: "defect sparsity" },
        { summary: { error: "timed out" } }, { tool_call_id: "s1", status: "error" }),
      R("outbound:review", { request: { text: "KESTREL", destination: "public-web-search" } },
        { stage: "terms", decision: "block", term: "KESTREL" }, { tool_call_id: "s1" }),
      R("outbound:review", { request: { text: "arXiv:2403.01234", destination: "api.semanticscholar.org" } },
        { stage: "exact_id", decision: "send" }, { tool_call_id: "s1" }),
      R("agent:step", { step: 2 }, { model: "gemini/flash", answer: "a" }, { latency_ms: 4200 }),
    ];
    render(<TraceInline trace={trace} sessionId={5} runId={1} agent={{ status: "ok" }} />);
    const user = await open();
    const node = screen.getByText("Searched papers for “q”").closest("li")!;
    expect(node).toHaveAttribute("data-state", "done");
    expect(node).toHaveTextContent(
      "Found 3 papers. 2 queries sent (1 rewritten first), 1 not sent, 1 timed out.",
    );
    expect(within(node).getByText("3.4 s")).toBeInTheDocument();
    await user.click(within(node).getByRole("button", { name: "Queries and details" }));
    const items = within(node).getByTestId("node-queries").querySelectorAll("li");
    expect([...items].map((li) => li.getAttribute("data-o"))).toEqual(["sent", "timeout", "blocked"]);
    expect(items[1]!.querySelector("del")).toHaveTextContent("TR-0412 setup");
    expect(items[2]).toHaveTextContent("Not sent: contains the sensitive term KESTREL");
    expect(node).toHaveTextContent("Also looked up 1 found paper by ID (arXiv:2403.01234).");
    // Details: the technical record.
    expect(node).toHaveTextContent("tool:search_papers");
    expect(node).toHaveTextContent("agent step 1 · gemini/flash");
    expect(screen.getByText("Wrote the answer").closest("li")).toHaveTextContent("4.2 s");
  });

  it("a declined search keeps the user's note; a stopped turn says so", () => {
    const trace = [
      R("agent:step", { step: 1 }, { model: "m" }),
      R("tool:search_papers", { query: "q" },
        { query: "q", declined: true, note: "Only use papers in this chat." }),
      R("tool:remove_paper", { paper: 7 }, {}, { status: "error", error: "cancelled" }),
    ];
    render(<TraceInline trace={trace} sessionId={5} runId={1} agent={{ status: "stopped" }} />);
    expect(
      screen.getByRole("button", { name: /Stopped. Nothing waiting was sent or run./ }),
    ).toBeInTheDocument();
  });

  it("declined and stopped nodes are skipped, with plain reasons", async () => {
    const trace = [
      R("agent:step", { step: 1 }, { model: "m" }),
      R("tool:search_papers", { query: "q" },
        { query: "q", declined: true, note: "Only use papers in this chat." }),
      R("tool:remove_paper", { paper: 7 }, {}, { status: "error", error: "cancelled" }),
    ];
    render(<TraceInline trace={trace} sessionId={5} runId={1} agent={{ status: "ok" }} />);
    await open();
    const search = screen.getByText("Search papers for “q”").closest("li")!;
    expect(search).toHaveAttribute("data-state", "skipped");
    expect(search).toHaveTextContent("Not searched. You told the agent: “Only use papers in this chat.”");
    const remove = screen.getByText("Remove “GISC” from this chat").closest("li")!;
    expect(remove).toHaveAttribute("data-state", "skipped");
    expect(remove).toHaveTextContent("Not run: the turn was stopped.");
  });

  it("a tool not in the label map keeps its name; a failed call is red", async () => {
    render(
      <TraceInline
        trace={[R("tool:mystery", {}, { error: "boom" }, { status: "error", error: "boom" })]}
        sessionId={5} runId={1} agent={{ status: "ok" }}
      />,
    );
    await open();
    expect(screen.getByText("mystery").closest("li")).toHaveAttribute("data-state", "failed");
  });
});
