import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/types/domain";

const userMsg: ChatMessage = { role: "user", content: "my prompt", run_id: 5 };
const asstMsg: ChatMessage = {
  role: "assistant", content: "answer", run_id: 5, status: "ok",
};

describe("MessageBubble fork control", () => {
  it("renders a rewind control on a user message when onFork is given", () => {
    render(<MessageBubble message={userMsg} onFork={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /fork|rewind|branch/i }),
    ).toBeInTheDocument();
  });

  it("does not render the rewind control on an assistant message", () => {
    render(<MessageBubble message={asstMsg} onFork={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /fork|rewind|branch/i }),
    ).not.toBeInTheDocument();
  });

  it("does not render the rewind control without onFork", () => {
    render(<MessageBubble message={userMsg} />);
    expect(
      screen.queryByRole("button", { name: /fork|rewind|branch/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onFork when clicked", async () => {
    const onFork = vi.fn();
    render(<MessageBubble message={userMsg} onFork={onFork} />);
    await userEvent.click(
      screen.getByRole("button", { name: /fork|rewind|branch/i }),
    );
    expect(onFork).toHaveBeenCalledTimes(1);
  });
});
