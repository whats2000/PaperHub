import { describe, it, expect, beforeEach } from "vitest";
import { useSlidesStore } from "@/store/slides";

describe("slides store", () => {
  beforeEach(() => useSlidesStore.setState({ deckBySession: {}, currentPageBySession: {}, open: false }));

  it("sets deck and tracks current page per session", () => {
    useSlidesStore.getState().setDeck(7, { deck_id: 1, session_id: 7, page_count: 5,
      title: "T", status: "ok", contributing_papers: [], has_notes: true });
    expect(useSlidesStore.getState().deckBySession[7]?.page_count).toBe(5);
    useSlidesStore.getState().setCurrentPage(7, 3);
    expect(useSlidesStore.getState().currentPageBySession[7]).toBe(3);
  });

  it("toggleOpen flips open", () => {
    useSlidesStore.getState().toggleOpen();
    expect(useSlidesStore.getState().open).toBe(true);
  });
});
