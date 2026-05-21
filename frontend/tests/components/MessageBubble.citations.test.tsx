import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { useCanvasStore } from "@/store/canvas";
import type { ChatMessage } from "@/types/domain";

function assistantMsg(content: string): ChatMessage {
  return { role: "assistant", content, run_id: 1, status: "ok" };
}

beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("MessageBubble citation markers", () => {
  it("renders [chunk:id] as a deduped superscript ordinal", () => {
    render(
      <MessageBubble
        message={assistantMsg("Collapse is mitigated[chunk:50] though[chunk:12], see[chunk:50].")}
      />,
    );
    const ones = screen.getAllByRole("button", { name: /citation 1/i });
    expect(ones).toHaveLength(2);
    expect(screen.getByRole("button", { name: /citation 2/i })).toBeInTheDocument();
    expect(ones[0]).toHaveTextContent("1");
  });

  it("clicking a marker opens the canvas on that chunk", async () => {
    render(<MessageBubble message={assistantMsg("balanced[chunk:77].")} />);
    await userEvent.click(screen.getByRole("button", { name: /citation 1/i }));
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.requestedChunkId).toBe(77);
  });

  it("leaves text without markers untouched", () => {
    render(<MessageBubble message={assistantMsg("plain answer, no citations")} />);
    expect(screen.queryByRole("button", { name: /citation/i })).toBeNull();
    expect(screen.getByText(/plain answer/i)).toBeInTheDocument();
  });
});
