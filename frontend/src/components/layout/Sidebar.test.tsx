import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import { useSettingsStore } from "@/store/settings";
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

describe("Sidebar held searches entry", () => {
  afterEach(() => {
    vi.useRealTimers();
    useChatStore.setState({ sidebarCollapsed: false });
    useNavStore.setState({ heldBadge: 0 });
    useSettingsStore.setState({ isOpen: false });
  });

  it("is hidden when nothing is held", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("button", { name: /Held searches/ })).toBeNull();
  });

  it("shows the count and opens Settings on Outbound log > Held", async () => {
    const openAt = vi.fn();
    useSettingsStore.setState({ openAt });
    useNavStore.setState({ heldBadge: 2 });
    render(<Sidebar />);
    const btn = screen.getByRole("button", { name: /Held searches/ });
    expect(btn).toHaveTextContent("2");
    await userEvent.setup().click(btn);
    expect(openAt).toHaveBeenCalledWith("outbound_log", "held");
  });

  it("animates out and unmounts when the count drops to 0", () => {
    vi.useFakeTimers();
    useNavStore.setState({ heldBadge: 2 });
    render(<Sidebar />);
    const btn = screen.getByRole("button", { name: /Held searches/ });
    const wrap = btn.closest("[data-state]");
    expect(wrap).toHaveAttribute("data-state", "open");
    act(() => useNavStore.getState().setHeldBadge(0));
    expect(wrap).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByRole("button", { name: /Held searches/ })).toBeNull();
  });

  it("is on the collapsed rail too", () => {
    useChatStore.setState({ sidebarCollapsed: true });
    useNavStore.setState({ heldBadge: 2 });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /Held searches/ })).toBeInTheDocument();
  });
});

describe("Sidebar chat held marker", () => {
  afterEach(() => {
    vi.useRealTimers();
    useChatStore.setState({ sessions: [], activeSessionId: null, sidebarTab: "chats" });
    useNavStore.setState({ heldBadge: 0, heldBySession: {}, pausedActionsBySession: {} });
  });

  it("marks a chat with searches waiting, and fades it out when they are decided", () => {
    vi.useFakeTimers();
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [
        { id: 1, title: "Wafer chat", messages: [], backend_session_id: 77 },
        { id: 2, title: "Quiet chat", messages: [], backend_session_id: 78 },
      ],
    });
    useNavStore.setState({ heldBadge: 2, heldBySession: { 77: 2 } });
    render(<Sidebar />);
    const mark = screen.getByRole("img", { name: "Paused: 2 items need your OK" });
    expect(mark).toHaveTextContent("2");
    expect(screen.getByRole("button", { name: /^Wafer chat/ })).toContainElement(mark);
    expect(screen.getAllByRole("img", { name: /need/ })).toHaveLength(1);
    act(() => useNavStore.setState({ heldBySession: {} }));
    expect(mark).toHaveAttribute("data-state", "closed");
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByRole("img", { name: /need/ })).toBeNull();
  });

  it("uses the singular for one item", () => {
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [{ id: 1, title: "Wafer chat", messages: [], backend_session_id: 77 }],
    });
    useNavStore.setState({ heldBySession: { 77: 1 } });
    render(<Sidebar />);
    expect(screen.getByRole("img", { name: "Paused: 1 item needs your OK" })).toBeInTheDocument();
  });

  it("counts a paused run's Ask-first actions with its held searches", () => {
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [{ id: 1, title: "Wafer chat", messages: [], backend_session_id: 77 }],
    });
    useNavStore.setState({ heldBySession: { 77: 1 }, pausedActionsBySession: { 77: 1 } });
    render(<Sidebar />);
    expect(screen.getByRole("img", { name: "Paused: 2 items need your OK" })).toHaveTextContent("2");
  });
});

describe("Sidebar chats grouped by time", () => {
  const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();
  const daysAgo = (d: number) => {
    const x = new Date();
    x.setDate(x.getDate() - d);
    x.setHours(12, 0, 0, 0);
    return x.toISOString();
  };
  afterEach(() => {
    useChatStore.setState({ sessions: [], activeSessionId: null, sidebarTab: "chats" });
    try {
      localStorage.removeItem("paperhub-sidebar-collapsed-groups");
    } catch {
      /* ignore */
    }
  });
  const seed = () =>
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [
        { id: 1, title: "Old chat", messages: [], backend_session_id: 1, updated_at: daysAgo(3) },
        { id: 2, title: "Fresh chat", messages: [], backend_session_id: 2, updated_at: hoursAgo(0) },
        { id: 3, title: "Undated chat", messages: [], backend_session_id: 3 },
      ],
      activeSessionId: null,
    });

  it("shows newest first under Today / Previous 7 days / Older headers", () => {
    seed();
    render(<Sidebar />);
    const headers = screen
      .getAllByRole("button", { expanded: true })
      .map((b) => b.textContent);
    expect(headers).toEqual(["Today", "Previous 7 days", "Older"]);
    const titles = screen
      .getAllByRole("button", { name: /chat$/ })
      .filter((b) => !b.getAttribute("aria-label")?.startsWith("Delete"))
      .map((b) => b.textContent);
    expect(titles).toEqual(["Fresh chat", "Old chat", "Undated chat"]);
  });

  it("collapses a section toward its header and remembers it", async () => {
    seed();
    const { unmount } = render(<Sidebar />);
    const header = screen.getByRole("button", { name: "Previous 7 days" });
    await userEvent.setup().click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    const body = document.getElementById(header.getAttribute("aria-controls")!)!;
    expect(body).toHaveAttribute("data-state", "closed");
    expect(body).toHaveAttribute("inert");
    unmount();
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "Previous 7 days" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("expands the active chat's section", () => {
    seed();
    localStorage.setItem("paperhub-sidebar-collapsed-groups", JSON.stringify(["d7"]));
    useChatStore.setState({ activeSessionId: 1 });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "Previous 7 days" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("names a month section in the UI language", () => {
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [
        { id: 1, title: "Ancient chat", messages: [], backend_session_id: 1, updated_at: "2024-03-10T12:00:00.000Z" },
      ],
    });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "March 2024" })).toBeInTheDocument();
  });
});

describe("Sidebar chat titles", () => {
  afterEach(() => useChatStore.getState().reset());

  function chatRow(title: string): { button: HTMLElement; label: HTMLElement } {
    useChatStore.setState({
      sidebarTab: "chats",
      sessions: [
        { id: 1, title, messages: [], backend_session_id: 1, updated_at: new Date().toISOString() },
      ],
    });
    render(<Sidebar />);
    const label = screen.getByText(title);
    const button = label.closest("button");
    if (!button) throw new Error("chat row not found");
    return { button, label };
  }

  it("shows a cut-off title in full on hover", () => {
    const title = "Where does the XR latency drop of 83-89% come from in the predictive teleoperation system?";
    const { button, label } = chatRow(title);
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 400 });
    Object.defineProperty(label, "clientWidth", { configurable: true, value: 180 });
    fireEvent.pointerEnter(button);
    expect(button).toHaveAttribute("title", title);
  });

  it("adds no title when the title fits", () => {
    const { button } = chatRow("Short");
    fireEvent.pointerEnter(button);
    expect(button).not.toHaveAttribute("title");
  });
});
