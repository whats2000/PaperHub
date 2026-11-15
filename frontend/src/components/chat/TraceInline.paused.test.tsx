import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TraceInline } from "@/components/chat/TraceInline";
import type { ToolCallRecord } from "@/types/domain";

const beat: ToolCallRecord = {
  run_id: 1, step_index: -1, branch: "", parent_step: null, agent: "agent",
  tool: "agent:search_papers, remove_paper", model: "", args_redacted_json: null,
  result_summary_json: { stage: true, elapsed_s: 0 }, latency_ms: 0, token_in: 0,
  token_out: 0, status: "ok", error: null,
};

describe("TraceInline paused rows (H5a)", () => {
  it("each waiting call reads 'paused · waiting for your OK' in place of the live beat", async () => {
    render(
      <TraceInline
        trace={[beat]}
        sessionId={1}
        runId={1}
        paused={[
          { kind: "held", id: "h1", tool: "search_papers" },
          { kind: "held", id: "h2", tool: "search_papers" },
          { kind: "action", id: "a1", tool: "remove_paper" },
        ]}
      />,
    );
    await userEvent.setup().click(screen.getByRole("button", { name: /Trace/ }));
    const rows = document.querySelectorAll('[data-stage="paused"]');
    expect([...rows].map((r) => r.textContent)).toEqual([
      "search_paperspaused · waiting for your OK",
      "remove_paperpaused · waiting for your OK",
    ]);
    expect(document.querySelector('[data-stage="live"]')).toBeNull();
  });
});
