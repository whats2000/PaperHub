import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";

// Stub the heavy chat page: only the "composer stays mounted" contract matters.
vi.mock("@/pages/ChatPage", () => ({
  ChatPage: () => <textarea aria-label="composer" />,
}));
vi.mock("@/components/references/ReferenceSourcesPanel", () => ({
  ReferenceSourcesPanel: () => null,
}));
vi.mock("@/components/settings/SettingsModal", () => ({ SettingsModal: () => null }));
vi.mock("@/components/settings/WelcomeModal", () => ({ WelcomeModal: () => null }));
vi.mock("@/components/about/ChangelogModal", () => ({ ChangelogModal: () => null }));

import App from "./App";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
});
afterEach(() => {
  vi.unstubAllGlobals();
  useNavStore.setState({ page: "chat", feedbackBadge: 0 });
  useChatStore.getState().reset();
});

describe("App page navigation", () => {
  it("Feedback entry shows the feedback page and keeps chat mounted", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /^Feedback/ }));
    expect(screen.getByRole("heading", { name: "Feedback" })).toBeInTheDocument();
    const composer = screen.getByLabelText("composer", { selector: "textarea" });
    expect(composer).toBeInTheDocument();
    expect(composer.closest("[hidden]")).not.toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /^Ideas/ }));
    expect(screen.getByRole("heading", { name: "Ideas" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Feedback" })).toBeNull();
  });

  it("shows the feedback badge count", () => {
    render(<App />);
    act(() => useNavStore.getState().setFeedbackBadge(3));
    expect(screen.getByText("3 need your confirmation")).toHaveClass("sr-only");
    expect(screen.getByText("3")).toHaveAttribute("aria-hidden", "true");
  });

  it("selecting a session from Feedback reveals the chat again", async () => {
    const id = useChatStore.getState().newSession();
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /^Feedback/ }));
    const composer = screen.getByLabelText("composer", { selector: "textarea" });
    expect(composer.closest("[hidden]")).not.toBeNull();
    act(() => useChatStore.getState().selectSession(id));
    expect(composer.closest("[hidden]")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Feedback" })).toBeNull();
  });
  it("fades the chat back in on return and honours reduced motion", async () => {
    render(<App />);
    const chatWrapper = screen
      .getByLabelText("composer", { selector: "textarea" })
      .closest("div");
    expect(chatWrapper).toHaveClass(
      "animate-in",
      "fade-in-0",
      "duration-150",
      "motion-reduce:animate-none",
      "[&[hidden]]:hidden",
    );
    await userEvent.click(screen.getByRole("button", { name: /^Feedback/ }));
    const feedbackHeading = screen.getByRole("heading", { name: "Feedback" });
    expect(feedbackHeading.parentElement?.parentElement).toHaveClass(
      "animate-in",
      "motion-reduce:animate-none",
    );
    await userEvent.click(screen.getByRole("button", { name: /^Ideas/ }));
    const ideasHeading = screen.getByRole("heading", { name: "Ideas" });
    expect(ideasHeading.parentElement?.parentElement).toHaveClass(
      "animate-in",
      "motion-reduce:animate-none",
    );
  });
});
