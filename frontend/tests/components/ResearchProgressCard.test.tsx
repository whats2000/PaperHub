import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResearchProgressCard } from "@/components/chat/ResearchProgressCard";
import type { ToolCallRecord } from "@/types/domain";

function rec(tool: string, step_index = 0): ToolCallRecord {
  return {
    run_id: 1,
    branch: "",
    step_index,
    parent_step: null,
    agent: "research",
    tool,
    model: "m",
    args_redacted_json: null,
    result_summary_json: null,
    latency_ms: 100,
    token_in: null,
    token_out: null,
    status: "ok",
    error: null,
  };
}

describe("ResearchProgressCard", () => {
  it("renders the search heading and a status role", () => {
    render(<ResearchProgressCard intent="paper_search" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText(/conducting deep research/i)).toBeInTheDocument();
  });

  it("sets expectations that deep search can take a few minutes", () => {
    render(<ResearchProgressCard intent="paper_search" />);
    expect(screen.getByText(/can take a few minutes/i)).toBeInTheDocument();
  });

  it("uses a recommendation heading for paper_suggest", () => {
    render(<ResearchProgressCard intent="paper_suggest" />);
    expect(screen.getByText(/curating recommendations/i)).toBeInTheDocument();
  });

  it("derives the live stage label from the most-recent trace step", () => {
    render(
      <ResearchProgressCard
        intent="paper_search"
        trace={[rec("paper_search:discover_plan", 1), rec("paper_search:paperhub.search_web", 2)]}
      />,
    );
    expect(screen.getByText(/searching the web/i)).toBeInTheDocument();
  });

  it("reports a long arXiv fetch as the current stage", () => {
    render(
      <ResearchProgressCard
        intent="paper_search"
        trace={[rec("paper_search:arxiv_ingest", 5)]}
      />,
    );
    expect(screen.getByText(/fetching full text from arxiv/i)).toBeInTheDocument();
  });

  it("shows a step count once steps have completed", () => {
    render(
      <ResearchProgressCard
        intent="paper_search"
        trace={[rec("paper_search:parse", 1), rec("paper_search:discover_plan", 2)]}
      />,
    );
    expect(screen.getByText(/2 steps so far/i)).toBeInTheDocument();
  });

  it("falls back to a warming-up message before any steps", () => {
    render(<ResearchProgressCard intent="paper_search" trace={[]} />);
    expect(screen.getByText(/warming up/i)).toBeInTheDocument();
  });
});
