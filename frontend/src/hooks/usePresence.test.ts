import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePresence } from "./usePresence";

describe("usePresence", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts unmounted when initially closed", () => {
    const { result } = renderHook(() => usePresence(false));
    expect(result.current).toEqual({ mounted: false, state: "closed" });
  });

  it("stays mounted during the exit window, then unmounts", () => {
    const { result, rerender } = renderHook(({ o }) => usePresence(o), {
      initialProps: { o: true },
    });
    expect(result.current).toEqual({ mounted: true, state: "open" });
    rerender({ o: false });
    expect(result.current).toEqual({ mounted: true, state: "closed" });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current).toEqual({ mounted: false, state: "closed" });
  });

  it("reopening mid-exit cancels the unmount", () => {
    const { result, rerender } = renderHook(({ o }) => usePresence(o), {
      initialProps: { o: true },
    });
    rerender({ o: false });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    rerender({ o: true });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toEqual({ mounted: true, state: "open" });
  });

  it("clears the pending timer on unmount", () => {
    const { rerender, unmount } = renderHook(({ o }) => usePresence(o), {
      initialProps: { o: true },
    });
    rerender({ o: false });
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("unmounts immediately under reduced motion", () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({
      matches: true,
      media: q,
    })) as unknown as typeof window.matchMedia;
    try {
      const { result, rerender } = renderHook(({ o }) => usePresence(o), {
        initialProps: { o: true },
      });
      rerender({ o: false });
      act(() => {
        vi.advanceTimersByTime(0);
      });
      expect(result.current).toEqual({ mounted: false, state: "closed" });
    } finally {
      window.matchMedia = original;
    }
  });
});
