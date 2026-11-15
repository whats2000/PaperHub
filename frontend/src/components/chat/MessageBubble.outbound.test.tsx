import { useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatThread } from "@/components/chat/ChatThread";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { useChatStore } from "@/store/chat";
import type { AgentUiEvent, ChatMessage, ChatSession } from "@/types/domain";

Element.prototype.scrollIntoView = () => undefined;

const streamChat = vi.hoisted(() => vi.fn());
vi.mock("@/lib/sse", () => ({ streamChat }));
const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: toastError }),
}));
const allowSearch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  allowSearch,
}));

const held = {
  held_id: "h1",
  held_ids: ["h1"],
  text: "wafer sparsity",
  reason: "private setup",
  destination: "public-web-search",
  status: "pending" as const,
};

const msg: ChatMessage = {
  role: "assistant",
  content: "Here is the answer.",
  run_id: 5,
  status: "ok",
  agent_events: [
    { event: "outbound_held", data: held },
    {
      event: "outbound_rewritten",
      data: {
        log_id: 1,
        sent_text: "sparsity segmentation",
        original_text: "KESTREL sparsity segmentation",
        reason: "removed a name",
        destination: "api.semanticscholar.org",
      },
    },
    {
      event: "outbound_blocked",
      data: { log_id: 2, text: "KESTREL v3", reason: "term", term: "KESTREL" },
    },
  ],
};

describe("MessageBubble outbound elements", () => {
  it("renders held, rewritten and blocked in event order, before the answer", () => {
    render(<MessageBubble message={msg} />);
    const card = screen.getByTestId("held-card");
    const chip = screen.getByTestId("outbound-chip");
    const blocked = screen.getByTestId("outbound-blocked");
    const answer = screen.getByText("Here is the answer.");
    const before = (a: Node, b: Node) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before(card, chip)).toBe(true);
    expect(before(chip, blocked)).toBe(true);
    expect(before(blocked, answer)).toBe(true);
  });

  it("renders two blocked and two rewritten events with a null log id, no duplicate keys", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const nulls: ChatMessage = {
      ...msg,
      agent_events: [
        { event: "outbound_blocked", data: { log_id: null, text: "x one", reason: "r" } },
        { event: "outbound_blocked", data: { log_id: null, text: "y two", reason: "r" } },
        {
          event: "outbound_rewritten",
          data: { log_id: null, sent_text: "a", original_text: "b", reason: "r", destination: "d" },
        },
        {
          event: "outbound_rewritten",
          data: { log_id: null, sent_text: "c", original_text: "b", reason: "r", destination: "d" },
        },
      ],
    };
    try {
      render(<MessageBubble message={nulls} />);
      expect(screen.getAllByTestId("outbound-blocked")).toHaveLength(2);
      expect(screen.getAllByTestId("outbound-chip")).toHaveLength(2);
      const dupes = errors.mock.calls.filter((c) => String(c[0]).includes("same key"));
      expect(dupes).toEqual([]);
    } finally {
      errors.mockRestore();
    }
  });

  it("renders the stored 'Sent search:' user message", () => {
    render(
      <MessageBubble
        message={{ role: "user", content: "Sent search: wafer sparsity", run_id: 6, status: "ok" }}
      />,
    );
    expect(screen.getByText("Sent search: wafer sparsity")).toBeInTheDocument();
  });
});

describe("MessageBubble paused turn (H5a)", () => {
  const paused: ChatMessage = {
    role: "assistant",
    content: "",
    run_id: 9,
    status: "streaming",
    paused: [
      { kind: "held", id: "h1", tool: "search_papers" },
      { kind: "action", id: "a1", tool: "remove_paper" },
    ],
    agent_events: [
      { event: "outbound_held", data: held },
      {
        event: "confirm_request",
        data: {
          action_id: "a1",
          tool: "remove_paper",
          args_summary: "x",
          reason: "r",
          target: { kind: "paper", id: 3, title: "GISC" },
          status: "pending",
        },
      },
    ],
  };

  it("shows the paused line after the cards, in place of the typing dots", () => {
    render(<MessageBubble message={paused} />);
    const line = screen.getByTestId("paused-line");
    expect(line).toHaveTextContent(
      "Paused · 2 items wait for your OK. I'll continue as soon as you decide.",
    );
    const before = (a: Node, b: Node) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before(screen.getByTestId("held-card"), screen.getByTestId("confirm-card"))).toBe(true);
    expect(before(screen.getByTestId("confirm-card"), line)).toBe(true);
    expect(screen.queryByLabelText(/streaming|Generating|Thinking/i)).toBeNull();
  });

  it("an Ask-first search shows the Allow-this-search card, not the action card", () => {
    render(
      <MessageBubble
        message={{
          ...paused,
          paused: [{ kind: "search", id: "s1", tool: "search_papers" }],
          agent_events: [
            {
              event: "confirm_request",
              data: {
                action_id: "s1",
                tool: "search_papers",
                args_summary: "q",
                reason: "Ask first is on for this chat",
                reason_kind: "ask_first",
                target: { kind: "search", query: "q", destinations: [] },
                status: "pending",
              },
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("ask-card")).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-card")).toBeNull();
    expect(screen.getByTestId("paused-line")).toHaveTextContent("Paused · 1 item waits");
  });

  it("Allow searches in this chat also settles the turn's other search cards", async () => {
    const card = (id: string, query: string): AgentUiEvent => ({
      event: "confirm_request",
      data: {
        action_id: id,
        tool: "search_papers",
        args_summary: query,
        reason: "Ask first is on for this chat",
        reason_kind: "ask_first",
        target: { kind: "search", query, destinations: [] },
        status: "pending",
      },
    });
    allowSearch.mockResolvedValue({
      status: "allowed",
      text: "q1",
      edited: false,
      allow_chat: true,
      resumed: true,
      also_allowed: [{ action_id: "s2", text: "q2" }],
    });
    function Harness() {
      const [m, setM] = useState<ChatMessage>({
        ...paused,
        paused: [
          { kind: "search", id: "s1", tool: "search_papers" },
          { kind: "search", id: "s2", tool: "search_papers" },
        ],
        agent_events: [card("s1", "q1"), card("s2", "q2")],
      });
      return (
        <MessageBubble
          message={m}
          onPatchEvent={(index, patch) =>
            setM((cur) => ({
              ...cur,
              agent_events: (cur.agent_events ?? []).map((e, i) =>
                i === index ? ({ ...e, data: { ...e.data, ...patch } } as AgentUiEvent) : e,
              ),
            }))
          }
        />
      );
    }
    render(<Harness />);
    const [first] = screen.getAllByTestId("ask-card");
    await userEvent.setup().click(
      within(first!).getByRole("button", {
        name: "Allow searches in this chat · also allows 1 waiting",
      }),
    );
    await waitFor(() => expect(screen.getAllByTestId("ask-line")).toHaveLength(2));
    const lines = screen.getAllByTestId("ask-line");
    expect(lines[1]).toHaveTextContent("Allowed: q2 · searches are allowed in this chat");
  });

  it("collapses the paused line when the run resumes", () => {
    vi.useFakeTimers();
    const { rerender } = render(<MessageBubble message={paused} />);
    rerender(<MessageBubble message={{ ...paused, paused: null }} />);
    expect(screen.getByTestId("paused-line")).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByTestId("paused-line")).toBeNull();
    vi.useRealTimers();
  });
});

describe("store: outbound events", () => {
  beforeEach(() => {
    useChatStore.setState({
      sessions: [
        {
          id: 1,
          title: "t",
          backend_session_id: 1,
          messages: [{ role: "assistant", content: "", run_id: 5, status: "streaming" }],
        },
      ],
    });
  });

  it("a repeat held event with more ids replaces the card (R30)", () => {
    const s = useChatStore.getState();
    s.applyAgentEvent(1, 5, "outbound_held", { ...held });
    s.applyAgentEvent(1, 5, "outbound_rewritten", { log_id: 1 });
    s.applyAgentEvent(1, 5, "outbound_held", { ...held, held_ids: ["h1", "h2"] });
    const events = useChatStore.getState().sessions[0]!.messages[0]!.agent_events!;
    expect(events.map((e) => e.event)).toEqual(["outbound_held", "outbound_rewritten"]);
    expect((events[0]!.data as { held_ids: string[] }).held_ids).toEqual(["h1", "h2"]);
  });
});

describe("ChatThread held follow-up", () => {
  beforeEach(() => {
    streamChat.mockReset();
    streamChat.mockResolvedValue(undefined);
  });

  function session(status: "pending" | "sent", extra: ChatMessage[] = []): ChatSession {
    const ev: AgentUiEvent = { event: "outbound_held", data: { ...held, status } };
    return {
      id: 1,
      title: "t",
      backend_session_id: 1,
      messages: [
        { role: "user", content: "find", run_id: 5, status: "ok" },
        { ...msg, agent_events: [ev] },
        ...extra,
      ],
    };
  }

  it("a sent card whose follow-up never ran starts it with approved_held_id", async () => {
    const s = session("sent");
    useChatStore.setState({ sessions: [s], activeSessionId: 1 });
    render(<ChatThread session={s} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run the search" }));
    expect(streamChat).toHaveBeenCalledOnce();
    const body = streamChat.mock.calls[0]![0] as Record<string, unknown>;
    expect(body.approved_held_id).toBe("h1");
    expect(body.user_message).toBe("");
    // The follow-up's user bubble shows the sent search.
    const last = useChatStore.getState().sessions[0]!.messages;
    expect(last.some((m) => m.role === "user" && m.content === "Sent search: wafer sparsity")).toBe(
      true,
    );
  });

  it("a later 'Sent search:' turn means it ran: no Run button", () => {
    const later: ChatMessage[] = [
      { role: "user", content: "Sent search: wafer sparsity", run_id: 6, status: "ok" },
      { role: "assistant", content: "found", run_id: 6, status: "ok" },
    ];
    render(<ChatThread session={session("sent", later)} />);
    expect(screen.queryByRole("button", { name: "Run the search" })).toBeNull();
    expect(screen.getByTestId("held-line").textContent).toBe("Sent: wafer sparsity");
  });
});

describe("ChatThread held follow-up failures", () => {
  const sentSession = (): ChatSession => {
    const ev: AgentUiEvent = { event: "outbound_held", data: { ...held, status: "sent" } };
    return {
      id: 1,
      title: "t",
      backend_session_id: 1,
      messages: [
        { role: "user", content: "find", run_id: 5, status: "ok" },
        { ...msg, agent_events: [ev] },
      ],
    };
  };
  function Live() {
    const s = useChatStore((st) => st.sessions[0]);
    return <ChatThread session={s ?? null} />;
  }
  beforeEach(() => {
    streamChat.mockReset();
    toastError.mockReset();
    useChatStore.setState({ sessions: [sentSession()], activeSessionId: 1 });
  });

  it("a network failure leaves no stray bubbles and the card offers Run again", async () => {
    streamChat.mockRejectedValue(new Error("network down"));
    render(<Live />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run the search" }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(useChatStore.getState().sessions[0]!.messages).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run the search" })).toBeInTheDocument();
  });

  it("a 409 settles the card on Sent and leaves no stray bubbles", async () => {
    streamChat.mockRejectedValue(new Error("POST /chat failed: 409 Conflict"));
    render(<Live />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Run the search" }));
    await waitFor(() =>
      expect(screen.getByTestId("held-line").textContent).toBe("Sent: wafer sparsity"),
    );
    expect(useChatStore.getState().sessions[0]!.messages).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Run the search" })).toBeNull();
  });

  it("Retry posts the turn's own approved_held_id when two cards share text", async () => {
    const s2 = sentSession();
    const card = (id: string): AgentUiEvent => ({
      event: "outbound_held",
      data: { ...held, held_id: id, held_ids: [id], status: "sent" },
    });
    s2.messages[1] = { ...msg, agent_events: [card("h1"), card("h2")] };
    s2.messages.push(
      {
        role: "user",
        content: "Sent search: wafer sparsity",
        run_id: 6,
        status: "ok",
        approved_held_id: "h2",
      },
      { role: "assistant", content: "", run_id: 6, status: "error", error: "boom" },
    );
    useChatStore.setState({ sessions: [s2], activeSessionId: 1 });
    streamChat.mockResolvedValue(undefined);
    render(<Live />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(streamChat).toHaveBeenCalledOnce();
    const body = streamChat.mock.calls[0]![0] as Record<string, unknown>;
    expect(body.approved_held_id).toBe("h2");
    expect(body.user_message).toBe("");
  });

  it("Retry without a recorded approval never resends plain text", async () => {
    const s2 = sentSession();
    s2.messages.push(
      { role: "user", content: "Sent search: wafer sparsity", run_id: 6, status: "ok" },
      { role: "assistant", content: "", run_id: 6, status: "error", error: "boom" },
    );
    useChatStore.setState({ sessions: [s2], activeSessionId: 1 });
    render(<Live />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(streamChat).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
    // The stray pair is gone; the card's Run button is back.
    expect(useChatStore.getState().sessions[0]!.messages).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run the search" })).toBeInTheDocument();
  });
});
