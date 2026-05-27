import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeckChip } from "@/components/slides/DeckChip";
import { useSlidesStore } from "@/store/slides";
import type { DeckEventData } from "@/types/domain";

vi.mock("@/lib/api", () => ({
  deckPdfUrl: (sessionId: number) => `http://x/sessions/${sessionId}/deck/pdf`,
}));

const deck: DeckEventData = {
  deck_id: 3,
  session_id: 7,
  page_count: 5,
  title: "Attention Is All You Need",
  status: "ok",
  contributing_papers: [{ id: 1, title: "Paper A" }],
  has_notes: true,
};

describe("DeckChip", () => {
  beforeEach(() => {
    useSlidesStore.setState({
      open: false,
      deckBySession: {},
      currentPageBySession: {},
    });
  });

  it("renders the deck title and page count", () => {
    render(<DeckChip deck={deck} />);
    expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
    expect(screen.getByText(/5 slides/i)).toBeInTheDocument();
  });

  it("shows 'ready' status indicator when status is ok", () => {
    render(<DeckChip deck={deck} />);
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("shows 'error' status indicator when status is error", () => {
    render(<DeckChip deck={{ ...deck, status: "error" }} />);
    expect(screen.getByText("error")).toBeInTheDocument();
  });

  it("Open button calls openPanel and setCurrentPage(session_id, 1)", async () => {
    render(<DeckChip deck={deck} />);
    await userEvent.click(screen.getByRole("button", { name: /open slides/i }));

    const state = useSlidesStore.getState();
    expect(state.open).toBe(true);
    expect(state.currentPageBySession[7]).toBe(1);
  });

  it("Download link points to the deck PDF URL", () => {
    render(<DeckChip deck={deck} />);
    const link = screen.getByRole("link", { name: /download pdf/i });
    expect(link).toHaveAttribute(
      "href",
      "http://x/sessions/7/deck/pdf",
    );
  });

  it("renders 'with notes' when has_notes is true", () => {
    render(<DeckChip deck={deck} />);
    expect(screen.getByText("with notes")).toBeInTheDocument();
  });

  it("does not render 'with notes' when has_notes is false", () => {
    render(<DeckChip deck={{ ...deck, has_notes: false }} />);
    expect(screen.queryByText("with notes")).toBeNull();
  });

  it("shows singular 'slide' for a single-page deck", () => {
    render(<DeckChip deck={{ ...deck, page_count: 1 }} />);
    expect(screen.getByText("1 slide")).toBeInTheDocument();
  });

  it("shows 'Generate notes' when has_notes is false and prefills the composer", () => {
    const onPrefill = vi.fn();
    render(<DeckChip deck={{ ...deck, has_notes: false }} onPrefill={onPrefill} />);
    fireEvent.click(screen.getByRole("button", { name: /generate.*notes/i }));
    expect(onPrefill).toHaveBeenCalledWith(
      expect.stringMatching(/speaker notes/i),
    );
  });

  it("shows 'Edit notes' when has_notes is true", () => {
    const onPrefill = vi.fn();
    render(<DeckChip deck={{ ...deck, has_notes: true }} onPrefill={onPrefill} />);
    expect(
      screen.getByRole("button", { name: /edit notes/i }),
    ).toBeInTheDocument();
  });

  it("Edit button prefills an editable edit-slide prompt (does not send)", () => {
    const onPrefill = vi.fn();
    render(<DeckChip deck={deck} onPrefill={onPrefill} />);
    fireEvent.click(screen.getByRole("button", { name: /edit slide/i }));
    expect(onPrefill).toHaveBeenCalledWith(expect.stringMatching(/edit this slide/i));
  });

  it("does not render prefill affordances without onPrefill", () => {
    render(<DeckChip deck={deck} />);
    expect(screen.queryByRole("button", { name: /edit slide/i })).toBeNull();
  });
});
