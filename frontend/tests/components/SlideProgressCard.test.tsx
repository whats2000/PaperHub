import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SlideProgressCard } from "@/components/chat/SlideProgressCard";
import type { ToolCallRecord } from "@/types/domain";

function rec(tool: string, step_index = 0): ToolCallRecord {
  return {
    run_id: 1,
    branch: "",
    step_index,
    parent_step: null,
    agent: "report",
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

describe("SlideProgressCard", () => {
  it("renders the build heading and a status role", () => {
    render(<SlideProgressCard />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText(/building your slide deck/i)).toBeInTheDocument();
  });

  it("sets expectations that building can take a few minutes", () => {
    render(<SlideProgressCard />);
    expect(screen.getByText(/can take a few minutes/i)).toBeInTheDocument();
  });

  it("falls back to a warming-up message before any steps", () => {
    render(<SlideProgressCard trace={[]} />);
    expect(screen.getByText(/warming up/i)).toBeInTheDocument();
  });

  it("derives the live stage label from the most-recent trace step", () => {
    render(<SlideProgressCard trace={[rec("report:understand", 1)]} />);
    expect(screen.getByText(/studying the papers/i)).toBeInTheDocument();
  });

  it("reports the narrate stage as outlining the talk", () => {
    render(<SlideProgressCard trace={[rec("report:narrate", 2)]} />);
    expect(screen.getByText(/outlining the talk/i)).toBeInTheDocument();
  });

  it("reports the compile stage as compiling the deck", () => {
    render(<SlideProgressCard trace={[rec("report:compile", 9)]} />);
    expect(screen.getByText(/compiling the deck/i)).toBeInTheDocument();
  });

  it("counts drafted slides during the draft fan-out", () => {
    render(
      <SlideProgressCard
        trace={[
          rec("report:understand", 1),
          rec("report:narrate", 2),
          rec("report:draft", 3),
          rec("report:draft", 4),
          rec("report:draft", 5),
        ]}
      />,
    );
    expect(screen.getByText(/drafting slide frames/i)).toBeInTheDocument();
    expect(screen.getByText(/3 slides drafted so far/i)).toBeInTheDocument();
  });

  it("shows a generic step count outside the draft stage", () => {
    render(
      <SlideProgressCard trace={[rec("report:narrate", 1), rec("report:coherence", 2)]} />,
    );
    expect(screen.getByText(/2 steps so far/i)).toBeInTheDocument();
  });
});
