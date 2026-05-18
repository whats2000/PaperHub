import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { EmptyState } from "@/components/states/EmptyState";
import { useChatStore } from "@/store/chat";

beforeEach(() => {
  localStorage.clear();
  useChatStore.getState().reset();
});

describe("EmptyState", () => {
  it("renders 4 prompt cards with correct labels", () => {
    render(<EmptyState />);
    expect(screen.getByText("Find papers")).toBeInTheDocument();
    expect(screen.getByText("Compare papers")).toBeInTheDocument();
    expect(screen.getByText("Generate slides")).toBeInTheDocument();
    expect(screen.getByText("Library stats")).toBeInTheDocument();
  });

  it("clicking a prompt card sets the composer draft in the store", async () => {
    render(<EmptyState />);
    const card = screen.getByText("Find papers").closest("button");
    expect(card).not.toBeNull();
    await userEvent.click(card!);
    expect(useChatStore.getState().composerDraft).toBe(
      "Find recent papers on mixture-of-experts routing",
    );
  });

  it("clicking different cards sets the correct draft text", async () => {
    render(<EmptyState />);
    const card = screen.getByText("Library stats").closest("button");
    await userEvent.click(card!);
    expect(useChatStore.getState().composerDraft).toBe(
      "How many papers did I add this week?",
    );
  });
});
