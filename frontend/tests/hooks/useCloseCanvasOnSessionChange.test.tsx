import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useCloseCanvasOnSessionChange } from "@/hooks/useCloseCanvasOnSessionChange";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() =>
  useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 }),
);

describe("useCloseCanvasOnSessionChange", () => {
  it("does NOT close on initial mount (no session change yet)", () => {
    useCanvasStore.setState({ open: true });
    renderHook(({ id }) => useCloseCanvasOnSessionChange(id), {
      initialProps: { id: 1 },
    });
    expect(useCanvasStore.getState().open).toBe(true);
  });

  it("closes the canvas when the active session id changes", () => {
    useCanvasStore.setState({ open: true });
    const { rerender } = renderHook(
      ({ id }) => useCloseCanvasOnSessionChange(id),
      { initialProps: { id: 1 } },
    );
    rerender({ id: 2 });
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("does not close when the id is unchanged across re-renders", () => {
    const { rerender } = renderHook(
      ({ id }) => useCloseCanvasOnSessionChange(id),
      { initialProps: { id: 5 } },
    );
    useCanvasStore.setState({ open: true });
    rerender({ id: 5 });
    expect(useCanvasStore.getState().open).toBe(true);
  });
});
