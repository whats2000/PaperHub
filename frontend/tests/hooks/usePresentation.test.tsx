import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePresentation } from "@/hooks/usePresentation";
import { useSlidesStore } from "@/store/slides";
import { createPresentChannel } from "@/lib/presentChannel";

describe("usePresentation", () => {
  beforeEach(() => {
    useSlidesStore.setState({
      presentingBySession: {},
      presentStartedAtBySession: {},
    });
  });

  it("present() opens the audience window + broadcasts the current page", () => {
    const openWindow = vi.fn(() => null);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));

    const { result } = renderHook(() =>
      usePresentation(7, 5, { openWindow }),
    );
    act(() => result.current.present());

    expect(openWindow).toHaveBeenCalledWith(
      "/present.html?session=7",
      "paperhub-present-7",
      expect.stringContaining("popup"),
    );
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(true);
    expect(pages).toContain(5);
    audience.close();
    act(() => result.current.stop());
  });

  it("broadcasts subsequent page changes while presenting", () => {
    const openWindow = vi.fn(() => null);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));

    let currentPage = 1;
    const { result, rerender } = renderHook(() =>
      usePresentation(7, currentPage, { openWindow }),
    );
    act(() => result.current.present());
    currentPage = 8;
    rerender();
    expect(pages).toContain(8);
    audience.close();
    act(() => result.current.stop());
  });

  it("stop() clears presenting", () => {
    const openWindow = vi.fn(() => null);
    const { result } = renderHook(() => usePresentation(7, 1, { openWindow }));
    act(() => result.current.present());
    act(() => result.current.stop());
    expect(useSlidesStore.getState().presentingBySession[7]).toBe(false);
  });

  it("reconnects on remount (Q&A path) without reopening the window", () => {
    // Simulate "already presenting" (the store survives a panel unmount/remount).
    const openWindow = vi.fn(() => null);
    const audience = createPresentChannel(7);
    const pages: number[] = [];
    audience.onPage((p) => pages.push(p));
    useSlidesStore.setState({
      presentingBySession: { 7: true },
      presentStartedAtBySession: { 7: 1 },
    });

    // First mount: channel created from store state, current page posted.
    const first = renderHook(() => usePresentation(7, 3, { openWindow }));
    expect(pages).toContain(3);
    pages.length = 0;

    // Panel closes for a Q&A turn → hook unmounts (channel closes).
    first.unmount();

    // Panel reopens → hook remounts; presenting is still true, so it recreates
    // the channel and re-posts the page — WITHOUT calling openWindow again.
    renderHook(() => usePresentation(7, 9, { openWindow }));
    expect(pages).toContain(9);
    expect(openWindow).not.toHaveBeenCalled();
    audience.close();
  });
});
