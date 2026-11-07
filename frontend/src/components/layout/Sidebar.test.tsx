import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import { Sidebar } from "./Sidebar";

function headerRow(): HTMLElement {
  const row = screen.getByRole("button", { name: /expand|collapse/i }).parentElement
    ?.parentElement;
  if (!row) throw new Error("header row not found");
  return row;
}

describe("Sidebar header", () => {
  afterEach(() => useChatStore.setState({ sidebarCollapsed: false }));

  it.each([false, true])(
    "uses the shared header height without vertical padding (collapsed=%s)",
    (collapsed) => {
      useChatStore.setState({ sidebarCollapsed: collapsed });
      render(<Sidebar />);
      const row = headerRow();
      expect(row).toHaveClass("h-(--header-h)");
      expect(row.className).not.toMatch(/(^|\s)(p-\d|py-\d)/);
    },
  );
});

describe("Sidebar feedback badge accessible name", () => {
  afterEach(() => {
    useChatStore.setState({ sidebarCollapsed: false });
    useNavStore.setState({ feedbackBadge: 0 });
  });

  it("announces the count on the collapsed rail button", () => {
    useChatStore.setState({ sidebarCollapsed: true });
    useNavStore.setState({ feedbackBadge: 3 });
    render(<Sidebar />);
    expect(
      screen.getByRole("button", { name: "Feedback, 3 need your confirmation" }),
    ).toBeInTheDocument();
  });

  it("keeps the rail label plain when nothing is pending", () => {
    useChatStore.setState({ sidebarCollapsed: true });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "Feedback" })).toBeInTheDocument();
  });

  it("separates label and count on the expanded entry", () => {
    useNavStore.setState({ feedbackBadge: 3 });
    render(<Sidebar />);
    expect(
      screen.getByRole("button", { name: "Feedback 3 need your confirmation" }),
    ).toBeInTheDocument();
  });
});

describe("Sidebar feedback badge presence", () => {
  afterEach(() => {
    vi.useRealTimers();
    useChatStore.setState({ sidebarCollapsed: false });
    useNavStore.setState({ feedbackBadge: 0 });
  });

  it.each([false, true])(
    "animates the chip out with its last count when the count drops to 0 (collapsed=%s)",
    (collapsed) => {
      vi.useFakeTimers();
      useChatStore.setState({ sidebarCollapsed: collapsed });
      useNavStore.setState({ feedbackBadge: 3 });
      render(<Sidebar />);
      const chip = screen.getByText("3");
      expect(chip).toHaveAttribute("data-state", "open");
      expect(chip).toHaveClass("motion-reduce:animate-none");

      act(() => useNavStore.getState().setFeedbackBadge(0));
      // Still mounted, exiting, and showing the last non-zero count.
      expect(chip).toBeInTheDocument();
      expect(chip).toHaveAttribute("data-state", "closed");
      expect(chip).toHaveTextContent("3");
      expect(screen.queryByText("0")).toBeNull();

      act(() => {
        vi.advanceTimersByTime(150);
      });
      expect(screen.queryByText("3")).toBeNull();
    },
  );
});

describe("Sidebar idea marker", () => {
  afterEach(() => useChatStore.setState({ sessions: [], activeSessionId: null, sidebarTab: "chats" }));

  it("marks chats opened from an idea with a Lightbulb, like the fork marker", () => {
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [
        { id: 1, title: "Anchor boxes as prompts", messages: [], backend_session_id: 901, idea_id: 5 },
        { id: 2, title: "Plain chat", messages: [], backend_session_id: 902, idea_id: null },
      ],
    });
    render(<Sidebar />);
    expect(screen.getByTestId("idea-marker-1")).toHaveClass("lucide-lightbulb");
    expect(screen.queryByTestId("idea-marker-2")).toBeNull();
    expect(screen.getByRole("button", { name: /Anchor boxes as prompts.*Opened from an idea/ })).toBeInTheDocument();
  });
});

describe("Sidebar delete confirmation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useChatStore.setState({ sessions: [], activeSessionId: null });
  });

  it("tells the user the chat's feedback goes with it, only when it has any", async () => {
    const toastMock = vi.fn();
    vi.doMock("sonner", () => ({ toast: Object.assign(toastMock, { error: vi.fn() }) }));
    vi.resetModules();
    const { useChatStore: store } = await import("@/store/chat");
    const { Sidebar: FreshSidebar } = await import("./Sidebar");
    const { default: userEventFresh } = await import("@testing-library/user-event");
    store.setState({
      sessions: [{ id: 5, title: "Mamba chat", messages: [], backend_session_id: 77 }],
      activeSessionId: 5,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        String(url).includes("/feedback/sessions/77/count")
          ? new Response(JSON.stringify({ count: 3 }), { status: 200 })
          : new Response(null, { status: 204 }),
      ),
    );
    render(<FreshSidebar />);
    await userEventFresh.setup().click(screen.getByLabelText(/delete chat/i, { selector: "button" }));
    await vi.waitFor(() => expect(toastMock).toHaveBeenCalled());
    const opts = toastMock.mock.calls[0]![1] as { description: { props: { children: unknown[] } } };
    expect(JSON.stringify(opts.description)).toContain("3 feedback entries");
  });
});
