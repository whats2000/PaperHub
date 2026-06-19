import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/types/domain";

const interruptedMsg: ChatMessage = {
  role: "assistant",
  content: "",
  run_id: 7,
  status: "interrupted",
};

const processingMsg: ChatMessage = {
  role: "assistant",
  content: "",
  run_id: 8,
  status: "processing",
};

const processingWithContentMsg: ChatMessage = {
  role: "assistant",
  content: "Partial answer so far",
  run_id: 9,
  status: "processing",
};

describe("MessageBubble interrupted state", () => {
  it("renders the interrupted text for an interrupted message", () => {
    render(<MessageBubble message={interruptedMsg} />);
    // The i18n fallback text for bubble.interrupted
    expect(
      screen.getByText(/generation was interrupted/i),
    ).toBeInTheDocument();
  });

  it("renders a Retry button when onRetry is provided", () => {
    render(<MessageBubble message={interruptedMsg} onRetry={vi.fn()} />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("does NOT render a Retry button when onRetry is absent", () => {
    render(<MessageBubble message={interruptedMsg} />);
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("calls onRetry when the Retry button is clicked", async () => {
    const onRetry = vi.fn();
    render(<MessageBubble message={interruptedMsg} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does NOT render the interrupted text for an ok message", () => {
    render(
      <MessageBubble
        message={{ role: "assistant", content: "Done", run_id: 1, status: "ok" }}
      />,
    );
    expect(
      screen.queryByText(/generation was interrupted/i),
    ).toBeNull();
  });
});

describe("MessageBubble processing state", () => {
  it("shows the in-progress (streaming) UI for an empty processing message", () => {
    render(<MessageBubble message={processingMsg} />);
    // LoadingDots is rendered with an aria-label containing "streaming"
    expect(screen.getByLabelText(/streaming/i)).toBeInTheDocument();
  });

  it("shows partial content for a processing message that has content", () => {
    render(<MessageBubble message={processingWithContentMsg} />);
    expect(screen.getByText(/partial answer so far/i)).toBeInTheDocument();
    // Still shows the streaming indicator alongside partial content
    expect(screen.getByLabelText(/streaming/i)).toBeInTheDocument();
  });

  it("does NOT render the ok/done state for a processing message", () => {
    render(<MessageBubble message={processingWithContentMsg} />);
    // Copy button only appears on completed (ok) messages
    expect(screen.queryByRole("button", { name: /copy message/i })).toBeNull();
  });
});
