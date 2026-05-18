import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";

// Mock clipboard API — keep a reference to the spy outside navigator so lint
// doesn't flag navigator.clipboard.writeText as an unbound method access.
const writeTextMock = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
  writeTextMock.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: writeTextMock },
    configurable: true,
  });
});

describe("MessageBubble", () => {
  it("renders a user message right-aligned", () => {
    render(
      <MessageBubble message={{ role: "user", content: "hello", run_id: null }} />,
    );
    const node = screen.getByText("hello");
    expect(node.closest("article")).toHaveAttribute("data-role", "user");
  });

  it("renders streaming state for an in-flight assistant message", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Hi th", run_id: 1, status: "streaming",
        }}
      />,
    );
    expect(screen.getByText(/hi th/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/streaming/i)).toBeInTheDocument();
  });

  it("renders an error message with the error string", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Provider 500",
        }}
      />,
    );
    expect(screen.getByText(/provider 500/i)).toBeInTheDocument();
  });

  it("shows Retry button on error message when onRetry is provided", async () => {
    const onRetry = vi.fn();
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Something failed",
        }}
        onRetry={onRetry}
      />,
    );
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    expect(retryBtn).toBeInTheDocument();
    await userEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not show Retry button on error message when onRetry is absent", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "Something failed",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("renders Copy button on completed assistant messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Here is the answer", run_id: 1, status: "ok",
        }}
      />,
    );
    expect(screen.getByRole("button", { name: /copy message/i })).toBeInTheDocument();
  });

  it("copy button calls clipboard.writeText with message content", async () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "Clipboard text", run_id: 1, status: "ok",
        }}
      />,
    );
    const copyBtn = screen.getByRole("button", { name: /copy message/i });
    await userEvent.click(copyBtn);
    expect(writeTextMock).toHaveBeenCalledWith("Clipboard text");
  });

  it("does not show Copy button on streaming messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "partial", run_id: 1, status: "streaming",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /copy message/i })).toBeNull();
  });

  it("does not show Copy button on error messages", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant", content: "", run_id: 1,
          status: "error", error: "oops",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /copy message/i })).toBeNull();
  });

  it("renders user content as plain text (no HTML execution)", () => {
    render(
      <MessageBubble
        message={{
          role: "user",
          content: "<img src=x onerror=alert(1)>",
          run_id: null,
        }}
      />,
    );
    // The literal angle brackets must be present in textContent — no <img> element.
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    const article = screen.getByText(/<img/).closest("article");
    expect(article?.querySelector("img")).toBeNull();
  });

  it("renders assistant raw HTML as escaped text (no script execution)", () => {
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Result: <img src=x onerror=alert(1)>",
          run_id: 1,
          status: "ok",
        }}
      />,
    );
    const article = screen.getByText(/result/i).closest("article");
    // No <img> element should exist — react-markdown renders it as text.
    expect(article?.querySelector("img")).toBeNull();
    // The literal characters should appear (react-markdown shows them as text).
    expect(article?.textContent).toContain("<img");
  });
});
