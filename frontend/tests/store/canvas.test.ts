import { beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => useCanvasStore.getState().closeCanvas());

describe("canvas store", () => {
  it("starts closed", () => {
    const s = useCanvasStore.getState();
    expect(s.open).toBe(false);
    expect(s.chunkId).toBeNull();
  });

  it("openCitation sets target + open", () => {
    useCanvasStore.getState().openCitation(42);
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.chunkId).toBe(42);
  });

  it("closeCanvas resets open but is idempotent", () => {
    useCanvasStore.getState().openCitation(42);
    useCanvasStore.getState().closeCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("re-opening with a new chunk updates the target", () => {
    useCanvasStore.getState().openCitation(42);
    useCanvasStore.getState().openCitation(7);
    expect(useCanvasStore.getState().chunkId).toBe(7);
  });
});
