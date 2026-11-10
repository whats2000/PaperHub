import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/types/domain";

describe("MessageBubble user surface", () => {
  // The user bubble is a soft grey, not the primary (near-white in dark,
  // near-black in light) button colour.
  it("draws the user's bubble on the secondary grey", () => {
    const msg: ChatMessage = { role: "user", content: "my prompt", run_id: 1 };
    render(<MessageBubble message={msg} />);
    const bubble = screen.getByText("my prompt").closest("div")!;
    expect(bubble.className).toContain("bg-secondary");
    expect(bubble.className).toContain("text-secondary-foreground");
    expect(bubble.className).not.toContain("bg-primary");
  });
});

describe("MessageBubble stopped turn (H4 review #5)", () => {
  it("shows a stopped note and keeps the turn's chips", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "",
      run_id: 3,
      status: "stopped",
      agent_events: [
        {
          event: "paper_added",
          data: { papers_id: 1, paper_content_id: 2, title: "Paper X", undoable: true },
        },
      ],
    };
    render(<MessageBubble message={msg} backendSessionId={5} />);
    expect(screen.getByTestId("stopped-turn")).toBeInTheDocument();
    expect(screen.getByText(/Paper X/)).toBeInTheDocument();
  });
});

describe("MessageBubble stopping turn (concern 5)", () => {
  it("shows a stopping note while the backend decides", () => {
    const msg: ChatMessage = {
      role: "assistant", content: "partial", run_id: 3, status: "streaming", stopping: true,
    };
    render(<MessageBubble message={msg} backendSessionId={5} />);
    expect(screen.getByTestId("stopping-turn")).toHaveTextContent("Stopping…");
  });
});
