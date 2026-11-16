import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

import i18n from "../src/lib/i18n";

// Existing getByText assertions match the English source catalog.
beforeEach(() => {
  void i18n.changeLanguage("en");
});

// jsdom does not implement window.matchMedia — provide a minimal stub
// so next-themes and other media-query-dependent code can run in tests.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom has no ResizeObserver — react-zoom-pan-pinch (the Citation Canvas image
// lightbox) constructs one on mount. A no-op stub lets it render in tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
  ResizeObserverStub;

// jsdom has no BroadcastChannel. Provide a deterministic in-memory one that
// delivers synchronously to other open instances of the same name (excluding
// the sender), so presentation-sync tests don't depend on event-loop timing.
class MemoryBroadcastChannel {
  static channels = new Map<string, Set<MemoryBroadcastChannel>>();
  name: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor(name: string) {
    this.name = name;
    const set = MemoryBroadcastChannel.channels.get(name) ?? new Set();
    set.add(this);
    MemoryBroadcastChannel.channels.set(name, set);
  }
  postMessage(data: unknown) {
    // Only `data` is populated — consumers read `e.data` exclusively. Other
    // MessageEvent fields (origin, source, ports) are intentionally omitted.
    for (const ch of MemoryBroadcastChannel.channels.get(this.name) ?? []) {
      if (ch !== this && ch.onmessage) ch.onmessage({ data } as MessageEvent);
    }
  }
  close() {
    MemoryBroadcastChannel.channels.get(this.name)?.delete(this);
  }
}
(globalThis as unknown as { BroadcastChannel: unknown }).BroadcastChannel =
  MemoryBroadcastChannel;

afterEach(() => {
  cleanup();
  // Drop any channels a test forgot to close() so a leaked instance can't
  // deliver to a later test reusing the same session id.
  MemoryBroadcastChannel.channels.clear();
});

// jsdom's Range has no geometry. ProseMirror reads it to place BlockNote's
// floating bars (formatting bar, menus) when a test selects text; without it
// the placement throws "target.getClientRects is not a function" outside the
// test, an unhandled error that fails the run at random.
const rangeProto = Range.prototype as unknown as Record<string, unknown>;
if (!("getClientRects" in Range.prototype)) rangeProto.getClientRects = () => [];
if (!("getBoundingClientRect" in Range.prototype)) rangeProto.getBoundingClientRect = () => new DOMRect();
