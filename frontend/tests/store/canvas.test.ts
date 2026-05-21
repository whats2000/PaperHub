import { beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 }));

describe("canvas store", () => {
  it("starts closed with no request", () => {
    const s = useCanvasStore.getState();
    expect(s.open).toBe(false);
    expect(s.requestedChunkId).toBeNull();
  });

  it("openCitation opens, records the chunk, and bumps the nonce", () => {
    const before = useCanvasStore.getState().requestNonce;
    useCanvasStore.getState().openCitation(42);
    const s = useCanvasStore.getState();
    expect(s.open).toBe(true);
    expect(s.requestedChunkId).toBe(42);
    expect(s.requestNonce).toBe(before + 1);
  });

  it("clicking the same chunk again re-bumps the nonce (re-triggers resolve)", () => {
    useCanvasStore.getState().openCitation(42);
    const n1 = useCanvasStore.getState().requestNonce;
    useCanvasStore.getState().openCitation(42);
    expect(useCanvasStore.getState().requestNonce).toBe(n1 + 1);
  });

  it("toggleCanvas opens when closed and closes when open", () => {
    expect(useCanvasStore.getState().open).toBe(false);
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().open).toBe(true);
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("toggleCanvas open does NOT set a chunk request (browse mode)", () => {
    useCanvasStore.getState().toggleCanvas();
    expect(useCanvasStore.getState().requestedChunkId).toBeNull();
  });

  it("consumeCitation clears the requested chunk without closing", () => {
    useCanvasStore.getState().openCitation(42);
    useCanvasStore.getState().consumeCitation();
    const s = useCanvasStore.getState();
    expect(s.requestedChunkId).toBeNull();
    expect(s.open).toBe(true);
  });

  it("closeCanvas closes but preserves open=false (request already consumed by canvas)", () => {
    useCanvasStore.getState().openCitation(7);
    useCanvasStore.getState().closeCanvas();
    expect(useCanvasStore.getState().open).toBe(false);
  });
});
