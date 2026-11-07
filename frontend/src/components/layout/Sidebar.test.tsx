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
