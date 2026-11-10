import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatThread } from "@/components/chat/ChatThread";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ProposalButtons } from "@/components/chat/ProposalButtons";
import type { ChatMessage, ChatSession } from "@/types/domain";

// jsdom has no scrollIntoView; ChatThread pins the view to the newest message.
Element.prototype.scrollIntoView = () => undefined;

describe("ProposalButtons", () => {
  it("sends the proposal text as a new message", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ProposalButtons proposals={["Compare with BERT", "Make slides"]} onSend={onSend} />);
    await user.click(screen.getByRole("button", { name: "Make slides" }));
    expect(onSend).toHaveBeenCalledWith("Make slides");
  });

  it("renders nothing without proposals", () => {
    const { container } = render(<ProposalButtons proposals={[]} onSend={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("MessageBubble proposals", () => {
  const msg: ChatMessage = {
    role: "assistant",
    content:
      "Transformers use attention.\n\n<proposal>Compare with RNNs</proposal>\n<proposal>Make slides on this</proposal>",
    run_id: 3,
    status: "ok",
  };

  it("strips the proposal lines from the answer and shows buttons", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<MessageBubble message={msg} onSend={onSend} />);
    expect(screen.getByText("Transformers use attention.")).toBeInTheDocument();
    expect(screen.queryByText(/<proposal>/)).toBeNull();
    const group = screen.getByRole("group", { name: "Suggested follow-ups" });
    expect(group).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Compare with RNNs" }));
    expect(onSend).toHaveBeenCalledWith("Compare with RNNs");
  });

  it("disables the buttons while another turn runs", () => {
    render(<MessageBubble message={msg} onSend={vi.fn()} sendDisabled />);
    expect(screen.getByRole("button", { name: "Make slides on this" })).toBeDisabled();
  });

  it("shows no buttons while the answer is still streaming", () => {
    render(
      <MessageBubble message={{ ...msg, status: "streaming" }} onSend={vi.fn()} />,
    );
    expect(screen.queryByRole("group", { name: "Suggested follow-ups" })).toBeNull();
    expect(screen.queryByText(/proposal/)).toBeNull();
  });
});

describe("ChatThread proposals", () => {
  it("shows proposal buttons only on the latest assistant message", () => {
    const answer = (text: string, run: number): ChatMessage => ({
      role: "assistant",
      content: `${text}
<proposal>Follow up ${run}</proposal>`,
      run_id: run,
      status: "ok",
    });
    const session: ChatSession = {
      id: 1,
      title: "t",
      backend_session_id: null,
      messages: [
        { role: "user", content: "q1", run_id: 1, status: "ok" },
        answer("first", 1),
        { role: "user", content: "q2", run_id: 2, status: "ok" },
        answer("second", 2),
      ],
    };
    render(<ChatThread session={session} onSend={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Follow up 1" })).toBeNull();
    expect(screen.getByRole("button", { name: "Follow up 2" })).toBeInTheDocument();
  });
});
