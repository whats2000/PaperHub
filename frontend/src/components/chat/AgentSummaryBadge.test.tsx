import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

// jsdom has no scrollIntoView; ChatThread pins the view to the newest message.
Element.prototype.scrollIntoView = () => undefined;

import { AgentSummaryBadge } from "@/components/chat/AgentSummaryBadge";
import { ChatThread } from "@/components/chat/ChatThread";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { TraceInline } from "@/components/chat/TraceInline";
import { API_BASE_URL } from "@/lib/api";
import type {
  ChatMessage,
  ChatSession,
  SearchResultCandidate,
  ToolCallRecord,
} from "@/types/domain";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  server.resetHandlers(
    http.get(`${API_BASE_URL}/feedback/runs/:runId`, () => HttpResponse.json(null)),
    http.get(`${API_BASE_URL}/feedback/sessions/:id`, () => HttpResponse.json([])),
    http.get(`${API_BASE_URL}/feedback/runs`, () => HttpResponse.json([])),
  );
});

function rec(over: Partial<ToolCallRecord>): ToolCallRecord {
  return {
    run_id: 9,
    branch: "",
    step_index: 0,
    parent_step: null,
    agent: "agent",
    tool: "agent:step",
    model: "gemini/flash",
    args_redacted_json: null,
    result_summary_json: null,
    latency_ms: 120,
    token_in: null,
    token_out: null,
    status: "ok",
    error: null,
    ...over,
  };
}

const agentTrace: ToolCallRecord[] = [
  rec({
    step_index: 0,
    args_redacted_json: { step: 1, forced: false },
    result_summary_json: {
      step: 1,
      tool_calls: [
        { name: "read_section", args: {} },
        { name: "gather", args: {} },
      ],
    },
  }),
  rec({
    step_index: 1,
    tool: "tool:read_section",
    model: null,
    args_redacted_json: { paper: 3, section: "Method", call_id: "c0" },
  }),
  rec({
    step_index: 2,
    tool: "tool:gather",
    model: null,
    args_redacted_json: {
      brief: "Collect every reported BLEU score for the base and big Transformer models",
      ids: [1, 2, 3, 4],
    },
  }),
  rec({
    step_index: 3,
    args_redacted_json: { step: 2, forced: false },
    result_summary_json: { step: 2, answer: "done" },
  }),
];

describe("AgentSummaryBadge", () => {
  it("reads 'Agent · read n papers · s steps', pluralised", () => {
    const { rerender } = render(
      <AgentSummaryBadge
        summary={{ papers_read: 2, steps: 4, tools: ["read_section"] }}
        open={false}
        onToggle={() => undefined}
      />,
    );
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("read 2 papers · 4 steps")).toBeInTheDocument();
    rerender(
      <AgentSummaryBadge
        summary={{ papers_read: 1, steps: 1, tools: [] }}
        open={false}
        onToggle={() => undefined}
      />,
    );
    expect(screen.getByText("read 1 paper · 1 step")).toBeInTheDocument();
  });

  it("clicking it toggles the turn's trace, with agent step and tool rows", async () => {
    const user = userEvent.setup();
    render(
      <TraceInline
        trace={agentTrace}
        sessionId={1}
        runId={9}
        badge={({ open, toggle }) => (
          <AgentSummaryBadge
            summary={{ papers_read: 1, steps: 2, tools: ["read_section", "gather"] }}
            open={open}
            onToggle={toggle}
          />
        )}
      />,
    );
    const badge = screen.getByRole("button", { name: /read 1 paper/ });
    expect(badge).toHaveAttribute("aria-expanded", "false");
    // The summary IS the trace toggle: no second "Trace" button beside it.
    expect(screen.getAllByRole("button", { expanded: false })).toEqual([badge]);
    expect(screen.queryByRole("button", { name: /^Trace/ })).toBeNull();
    expect(screen.queryByText(/Step 1/)).toBeNull();

    await user.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Step 1 → read_section, gather/)).toBeInTheDocument();
    expect(screen.getByText(/Step 2 → answer/)).toBeInTheDocument();
    expect(screen.getByText('paper=3 · section="Method"', { exact: false })).toBeInTheDocument();
    const gatherRow = screen.getByText("gather", { selector: "span" }).closest("span[data-row]")!;
    expect(gatherRow.textContent).toContain(
      'brief="Collect every reported BLEU score for the base and big Trans…"',
    );
    expect(gatherRow.textContent).toContain("ids=4");

    // A step's tool calls sit indented under it; the step rows do not.
    const rowOf = (text: RegExp | string) =>
      screen.getByText(text, { selector: "span" }).closest("li")!;
    expect(rowOf(/Step 1 → /)).not.toHaveAttribute("data-nested");
    expect(rowOf(/Step 2 → /)).not.toHaveAttribute("data-nested");
    expect(rowOf("read_section")).toHaveAttribute("data-nested");
    expect(gatherRow.closest("li")).toHaveAttribute("data-nested");

    // Closing collapses the list back into the toggle, then unmounts it.
    await user.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "false");
    const list = screen.getByTestId("trace-list");
    expect(list).toHaveAttribute("data-state", "closed");
    expect(list.className).toContain("data-[state=closed]:grid-rows-[0fr]");
    await waitFor(() => expect(screen.queryByText(/Step 1/)).toBeNull());
  });
});

function cand(id: string, title: string): SearchResultCandidate {
  return {
    paper_id: id,
    title,
    authors: [],
    year: 2024,
    abstract: null,
    arxiv_id: null,
    has_open_pdf: false,
    reason: "relevant",
    finalize: false,
    auto_added: false,
    papers_id: null,
    error: null,
    already_in_session: false,
  };
}

describe("agent turns in the thread", () => {
  it("two search batches render two result lists", () => {
    const a = cand("arxiv:1", "Paper One");
    const b = cand("arxiv:2", "Paper Two");
    const msg: ChatMessage = {
      role: "assistant",
      content: "Two lists.",
      run_id: 9,
      status: "ok",
      search_results: [a, b],
      search_result_batches: [[a], [b]],
    };
    render(<MessageBubble message={msg} backendSessionId={1} />);
    const lists = screen.getAllByText("Found papers");
    expect(lists).toHaveLength(2);
    expect(screen.getByText("Paper One")).toBeInTheDocument();
    expect(screen.getByText("Paper Two")).toBeInTheDocument();
  });

  it("a replayed pipeline message keeps its RoutingBadge; an agent one shows the summary", () => {
    const session: ChatSession = {
      id: 1,
      title: "t",
      backend_session_id: 5,
      messages: [
        { role: "user", content: "old q", run_id: 1, status: "ok" },
        {
          role: "assistant",
          content: "old answer",
          run_id: 1,
          status: "ok",
          routing_decision: { intent: "paper_qa", confidence: 0.9, reasoning: "asks" },
        },
        { role: "user", content: "new q", run_id: 2, status: "ok" },
        {
          role: "assistant",
          content: "new answer",
          run_id: 2,
          status: "ok",
          agent_summary: { papers_read: 2, steps: 3, tools: ["read_section"] },
        },
      ],
    };
    render(<ChatThread session={session} />);
    expect(screen.getByText("Paper Q&A")).toBeInTheDocument();
    expect(screen.getAllByText("Agent")).toHaveLength(1);
    expect(screen.getByText("read 2 papers · 3 steps")).toBeInTheDocument();
    const newAnswer = screen.getByText("new answer").closest("div.space-y-1")!;
    expect(within(newAnswer as HTMLElement).queryByText("Paper Q&A")).toBeNull();
  });
});
