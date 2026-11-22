import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/store/canvas";

import { useCloseCanvasOnSessionChange } from "./useCloseCanvasOnSessionChange";

beforeEach(() => {
  useCanvasStore.setState({ open: true, activePaperId: 7, readerView: { paperId: 7, kind: "html" } });
});

describe("useCloseCanvasOnSessionChange", () => {
  it("closes the reader and forgets its paper when the chat changes", () => {
    const { rerender } = renderHook(({ id }) => useCloseCanvasOnSessionChange(id), {
      initialProps: { id: 1 },
    });
    // Mounting is not a change.
    expect(useCanvasStore.getState().open).toBe(true);
    expect(useCanvasStore.getState().activePaperId).toBe(7);
    rerender({ id: 2 });
    const s = useCanvasStore.getState();
    expect(s.open).toBe(false);
    expect(s.activePaperId).toBeNull();
    expect(s.readerView).toBeNull();
  });
});
