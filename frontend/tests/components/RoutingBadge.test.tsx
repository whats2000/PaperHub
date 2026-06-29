import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoutingBadge } from "@/components/chat/RoutingBadge";

describe("RoutingBadge", () => {
  it("renders intent label + confidence but NOT the tier", () => {
    render(
      <RoutingBadge
        decision={{
          intent: "paper_qa",
          confidence: 0.92, reasoning: "asks about a paper",
        }}
      />,
    );
    expect(screen.getByText(/paper q&a/i)).toBeInTheDocument();
    expect(screen.getByText(/92/)).toBeInTheDocument();
    expect(screen.queryByText(/flagship/i)).toBeNull();
  });

  it("flags low-confidence (<0.5) with data-conf=\"low\"", () => {
    const { container } = render(
      <RoutingBadge
        decision={{
          intent: "chitchat",
          confidence: 0.32, reasoning: "uncertain",
        }}
      />,
    );
    expect(container.querySelector('[data-conf="low"]')).not.toBeNull();
  });

  it("flags high-confidence (>=0.8) with data-conf=\"high\"", () => {
    const { container } = render(
      <RoutingBadge
        decision={{
          intent: "chitchat",
          confidence: 0.85, reasoning: "clear greeting",
        }}
      />,
    );
    expect(container.querySelector('[data-conf="high"]')).not.toBeNull();
  });
});
