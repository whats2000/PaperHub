import { beforeEach, describe, expect, it } from "vitest";
import { useSlidesStore } from "@/store/slides";

describe("slides store — presentation state", () => {
  beforeEach(() => {
    // Reset every map this suite touches (setState is a partial merge, so an
    // unrelated test file in the same worker could otherwise bleed state).
    useSlidesStore.setState({
      open: false,
      presentingBySession: {},
      presentStartedAtBySession: {},
      currentPageBySession: {},
      deckBySession: {},
      deckRevisionBySession: {},
      restoringBySession: {},
    });
  });

  it("startPresenting flips the per-session flag + stamps a start time", () => {
    useSlidesStore.getState().startPresenting(7);
    const s = useSlidesStore.getState();
    expect(s.presentingBySession[7]).toBe(true);
    expect(s.presentStartedAtBySession[7]).toBeGreaterThan(0);
  });

  it("stopPresenting clears only the flag", () => {
    useSlidesStore.getState().startPresenting(7);
    useSlidesStore.getState().stopPresenting(7);
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(false);
  });

  it("closePanel preserves presenting + current page (the Q&A-reopen invariant)", () => {
    const st = useSlidesStore.getState();
    st.startPresenting(7);
    st.setCurrentPage(7, 4);
    st.openPanel();
    st.closePanel();
    const s = useSlidesStore.getState();
    expect(s.open).toBe(false);
    expect(s.presentingBySession[7]).toBe(true);
    expect(s.currentPageBySession[7]).toBe(4);
  });
});
