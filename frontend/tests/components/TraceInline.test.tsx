import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TraceInline } from "@/components/chat/TraceInline";
import { fetchRunTrace } from "@/lib/api";
import type { ToolCallRecord } from "@/types/domain";

// TraceInline lazily fetches a replayed turn's trace on first expand. Partial
// mock — keep the real module (the chat store imports createBackendSession from
// it) and override only fetchRunTrace.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchRunTrace: vi.fn(),
}));

const sampleTrace: ToolCallRecord[] = [
  {
    run_id: 1, branch: "", step_index: 0, parent_step: null,
    agent: "router", tool: "classify", model: "gemini/x",
    args_redacted_json: null, result_summary_json: null,
    latency_ms: 12, token_in: null, token_out: null,
    status: "ok", error: null,
  },
  {
    run_id: 1, branch: "", step_index: 1, parent_step: null,
    agent: "chitchat", tool: "generate", model: "gemini/x",
    args_redacted_json: null, result_summary_json: null,
    latency_ms: 240, token_in: null, token_out: null,
    status: "ok", error: null,
  },
];

// Helper: render with the (now-required) sessionId/runId props.
const renderTrace = (trace: ToolCallRecord[]) =>
  render(<TraceInline trace={trace} sessionId={7} runId={1} />);

beforeEach(() => {
  vi.mocked(fetchRunTrace).mockReset();
});

// ---------------------------------------------------------------------------
// Rendering a populated trace (live-streamed turn) — no fetch
// ---------------------------------------------------------------------------
describe("TraceInline — populated trace", () => {
  it("starts collapsed with a step count", () => {
    renderTrace(sampleTrace);
    expect(screen.getByRole("button", { name: /2 steps/i })).toBeInTheDocument();
    expect(screen.queryByText(/router · classify/i)).not.toBeInTheDocument();
  });

  it("expands to show all steps", async () => {
    renderTrace(sampleTrace);
    await userEvent.click(screen.getByRole("button", { name: /2 steps/i }));
    expect(screen.getByText(/router · classify/i)).toBeInTheDocument();
    expect(screen.getByText(/chitchat · generate/i)).toBeInTheDocument();
  });

  it("does not fetch when the turn already carries a trace", async () => {
    renderTrace(sampleTrace);
    await userEvent.click(screen.getByRole("button", { name: /2 steps/i }));
    expect(fetchRunTrace).not.toHaveBeenCalled();
  });

  it("flags an error step with data-status=\"error\"", async () => {
    const errorTrace: ToolCallRecord[] = [
      { ...sampleTrace[0]!, status: "error", error: "boom" },
    ];
    const { container } = renderTrace(errorTrace);
    await userEvent.click(screen.getByRole("button", { name: /step/i }));
    expect(container.querySelector('[data-status="error"]')).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Lazy fetch on a replayed (empty) trace
// ---------------------------------------------------------------------------
describe("TraceInline — lazy fetch", () => {
  it("shows a Trace toggle even with an empty trace (replayed turn)", () => {
    renderTrace([]);
    expect(screen.getByRole("button", { name: /trace/i })).toBeInTheDocument();
  });

  it("fetches and renders steps on first expand", async () => {
    vi.mocked(fetchRunTrace).mockResolvedValue([sampleTrace[0]!]);
    renderTrace([]);
    await userEvent.click(screen.getByRole("button", { name: /trace/i }));
    expect(fetchRunTrace).toHaveBeenCalledWith(7, 1);
    expect(await screen.findByText(/router · classify/i)).toBeInTheDocument();
  });

  it("does not refetch on a second expand (cached)", async () => {
    vi.mocked(fetchRunTrace).mockResolvedValue([sampleTrace[0]!]);
    renderTrace([]);
    const toggle = screen.getByRole("button", { name: /trace/i });
    await userEvent.click(toggle); // open → fetch
    await screen.findByText(/router · classify/i);
    await userEvent.click(toggle); // collapse
    await userEvent.click(toggle); // re-open
    expect(fetchRunTrace).toHaveBeenCalledTimes(1);
  });

  it("shows 'No steps recorded' when the fetched trace is empty", async () => {
    vi.mocked(fetchRunTrace).mockResolvedValue([]);
    renderTrace([]);
    await userEvent.click(screen.getByRole("button", { name: /trace/i }));
    expect(await screen.findByText(/no steps recorded/i)).toBeInTheDocument();
  });

  it("shows an error with retry when the fetch fails, then recovers", async () => {
    vi.mocked(fetchRunTrace)
      .mockRejectedValueOnce(new Error("API 500: boom"))
      .mockResolvedValueOnce([sampleTrace[0]!]);
    renderTrace([]);
    await userEvent.click(screen.getByRole("button", { name: /trace/i }));
    expect(await screen.findByText(/API 500: boom/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/router · classify/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Detail rendering (args/result) — preserved from the original suite
// ---------------------------------------------------------------------------
describe("TraceInline — step detail", () => {
  it("test_expand_reveals_reason_prominently", async () => {
    const trace: ToolCallRecord[] = [
      {
        run_id: 2, branch: "", step_index: 0, parent_step: null,
        agent: "research", tool: "paper_search", model: "gpt-4o",
        args_redacted_json: {
          reason: "match the user's query",
          query: "transformers",
        },
        result_summary_json: null,
        latency_ms: 300, token_in: null, token_out: null,
        status: "ok", error: null,
      },
    ];
    renderTrace(trace);
    await userEvent.click(screen.getByRole("button", { name: /1 step/i }));
    const rowButton = screen.getByRole("button", { name: /paper_search/i });
    await userEvent.click(rowButton);
    expect(screen.getByText(/Why:/i)).toBeInTheDocument();
    expect(screen.getByText(/match the user's query/i)).toBeInTheDocument();
  });

  it("test_expand_reveals_query_and_result_count", async () => {
    const trace: ToolCallRecord[] = [
      {
        run_id: 3, branch: "", step_index: 0, parent_step: null,
        agent: "research", tool: "search_arxiv", model: "gpt-4o",
        args_redacted_json: { query: "transformer" },
        result_summary_json: { summary: { count: 5 } },
        latency_ms: 500, token_in: null, token_out: null,
        status: "ok", error: null,
      },
    ];
    renderTrace(trace);
    await userEvent.click(screen.getByRole("button", { name: /1 step/i }));
    const rowButton = screen.getByRole("button", { name: /search_arxiv/i });
    await userEvent.click(rowButton);
    expect(screen.getByText(/transformer/i)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("test_collapsed_rows_dont_render_args_or_result", async () => {
    const trace: ToolCallRecord[] = [
      {
        run_id: 4, branch: "", step_index: 0, parent_step: null,
        agent: "research", tool: "paper_qa", model: "gpt-4o",
        args_redacted_json: {
          reason: "answer the user question",
          query: "deep learning survey",
        },
        result_summary_json: { summary: { count: 3 } },
        latency_ms: 800, token_in: null, token_out: null,
        status: "ok", error: null,
      },
    ];
    renderTrace(trace);
    await userEvent.click(screen.getByRole("button", { name: /1 step/i }));
    expect(screen.queryByText(/Why:/i)).toBeNull();
    expect(screen.queryByText(/answer the user question/i)).toBeNull();
    expect(screen.queryByText(/deep learning survey/i)).toBeNull();
  });

  it("test_error_row_displays_error_in_red", async () => {
    const errorTrace: ToolCallRecord[] = [
      {
        ...sampleTrace[0]!,
        status: "error",
        error: "connection timeout",
        result_summary_json: { error: "connection timeout" },
      },
    ];
    const { container } = renderTrace(errorTrace);
    await userEvent.click(screen.getByRole("button", { name: /1 step/i }));
    expect(container.querySelector('[data-status="error"]')).not.toBeNull();
    const rowButton = screen.getByRole("button", { name: /classify/i });
    await userEvent.click(rowButton);
    const errorEl = container.querySelector(".text-destructive");
    expect(errorEl).not.toBeNull();
    expect(errorEl!.textContent).toContain("connection timeout");
  });

  it("test_clicking_row_toggles_aria_expanded", async () => {
    const trace: ToolCallRecord[] = [
      {
        run_id: 5, branch: "", step_index: 0, parent_step: null,
        agent: "research", tool: "paper_search", model: "gpt-4o",
        args_redacted_json: { query: "nlp" },
        result_summary_json: null,
        latency_ms: 200, token_in: null, token_out: null,
        status: "ok", error: null,
      },
    ];
    renderTrace(trace);
    await userEvent.click(screen.getByRole("button", { name: /1 step/i }));
    const rowButton = screen.getByRole("button", { name: /paper_search/i });
    expect(rowButton).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(rowButton);
    expect(rowButton).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(rowButton);
    expect(rowButton).toHaveAttribute("aria-expanded", "false");
  });
});
