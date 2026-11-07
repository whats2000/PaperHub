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
