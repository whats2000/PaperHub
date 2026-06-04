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
});
