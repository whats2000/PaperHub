/**
 * Unit-level tests for the three right-panel regression fixes introduced in
 * commit 5026bd6 (shared Citation Canvas / Memory Manager slot).
 *
 * These tests exercise the two Zustand subscription hooks added to ChatPage
 * and the always-mounted Canvas layout, without mounting the full ChatPage
 * (which requires many heavy hooks and network stubs). We do so via a minimal
 * wrapper component that mirrors exactly the logic under test.
 */
import { act, render, screen } from "@testing-library/react";
import { useEffect, useRef, useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "@/store/canvas";
import { useChatStore } from "@/store/chat";

// ---------------------------------------------------------------------------
// Reset stores before every test.
// ---------------------------------------------------------------------------
beforeEach(() => {
  useCanvasStore.setState({
    open: false,
    requestedChunkId: null,
    requestNonce: 0,
  });
  useChatStore.getState().reset();
});

// ---------------------------------------------------------------------------
// Minimal component that mirrors exactly the two subscription effects from
// ChatPage (using zustand subscribe so setState is called from the callback,
// not synchronously in the effect body — matches the react-hooks/set-state-in-effect
// rule compliance approach used in ChatPage):
//   1. canvas store open=true → setMemoryOpen(false)
//   2. chat store activeSessionId changes → setMemoryOpen(false)
// ---------------------------------------------------------------------------
function RightPanelEffectsHarness({
  onMemoryOpen,
}: {
  onMemoryOpen?: (setter: (v: boolean) => void) => void;
}) {
  const canvasOpen = useCanvasStore((s) => s.open);
  const [memoryOpen, setMemoryOpen] = useState(false);

  // Expose the setter so the test can simulate "user opened Memory" AFTER mount.
  useEffect(() => {
    onMemoryOpen?.(setMemoryOpen);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fix 1: mirrors ChatPage — canvas-open subscription closes Memory.
  useEffect(() => {
    return useCanvasStore.subscribe((state) => {
      if (state.open) setMemoryOpen(false);
    });
  }, []);

  // Fix 3: mirrors ChatPage — session-switch subscription closes Memory.
  const prevSessionRef = useRef(useChatStore.getState().activeSessionId);
  useEffect(() => {
    return useChatStore.subscribe((state) => {
      if (prevSessionRef.current !== state.activeSessionId) {
        prevSessionRef.current = state.activeSessionId;
        setMemoryOpen(false);
      }
    });
  }, []);

  return (
    <div>
      <span data-testid="memory-state">{memoryOpen ? "open" : "closed"}</span>
      <span data-testid="canvas-state">{canvasOpen ? "open" : "closed"}</span>
    </div>
  );
}

// Helper: render the harness, expose the memoryOpen setter, then call
// setMemoryOpen(true) inside act() to simulate "user clicked the Memory button".
function renderWithMemoryOpen() {
  let setter!: (v: boolean) => void;
  render(
    <RightPanelEffectsHarness
      onMemoryOpen={(s) => {
        setter = s;
      }}
    />,
  );
  // Simulate user opening Memory after the initial mount effects have settled.
  act(() => {
    setter(true);
  });
  return { setter };
}

// ---------------------------------------------------------------------------
// Fix 1: citations broken while Memory open
// ---------------------------------------------------------------------------
describe("Fix 1 — canvas-open closes Memory (mutual exclusivity via canvasOpen effect)", () => {
  it("openCitation() while Memory is open → memoryOpen becomes false", () => {
    renderWithMemoryOpen();

    // Sanity: memory is now open (after the user-open simulation above).
    expect(screen.getByTestId("memory-state").textContent).toBe("open");

    // Simulate an inline citation click — openCitation sets open=true on the
    // canvas store, bypassing handleToggleCanvas, exactly as CitationMarker does.
    act(() => {
      useCanvasStore.getState().openCitation(42);
    });

    // The effect must have fired: memory now closed, canvas now open.
    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
    expect(screen.getByTestId("canvas-state").textContent).toBe("open");
  });

  it("toggleCanvas() while Memory is open → memoryOpen becomes false", () => {
    renderWithMemoryOpen();
    expect(screen.getByTestId("memory-state").textContent).toBe("open");

    act(() => {
      useCanvasStore.getState().toggleCanvas(); // opens the canvas
    });

    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
  });

  it("closing the canvas does NOT re-open Memory (canvasOpen=false → no-op)", () => {
    // Memory is closed; open then close the canvas.
    render(<RightPanelEffectsHarness />);

    act(() => {
      useCanvasStore.getState().toggleCanvas(); // open
    });
    act(() => {
      useCanvasStore.getState().toggleCanvas(); // close
    });

    // Memory must remain closed.
    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
  });
});

// ---------------------------------------------------------------------------
// Fix 3: Memory doesn't close on session switch
// ---------------------------------------------------------------------------
describe("Fix 3 — session switch closes Memory", () => {
  it("switching activeSessionId while Memory open → memoryOpen becomes false", () => {
    // Create two sessions and select the first so activeSessionId is stable
    // before render (avoids a spurious switch during mount).
    const sid1 = useChatStore.getState().newSession();
    const sid2 = useChatStore.getState().newSession();
    // Select sid1 so the initial activeSessionId is known.
    act(() => { useChatStore.getState().selectSession(sid1); });

    let setter!: (v: boolean) => void;
    render(
      <RightPanelEffectsHarness onMemoryOpen={(s) => { setter = s; }} />,
    );
    // Open Memory after mount.
    act(() => { setter(true); });
    expect(screen.getByTestId("memory-state").textContent).toBe("open");

    // Switch to the second session.
    act(() => {
      useChatStore.getState().selectSession(sid2);
    });

    expect(screen.getByTestId("memory-state").textContent).toBe("closed");

    // Open Memory again, then switch back — must close again.
    act(() => { setter(true); });
    act(() => {
      useChatStore.getState().selectSession(sid1);
    });

    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
  });

  it("null → session-id transition (first session creation) also closes Memory", () => {
    // Start with no active session (activeSessionId = null) — this is the
    // default after reset().
    expect(useChatStore.getState().activeSessionId).toBeNull();

    let setter!: (v: boolean) => void;
    render(
      <RightPanelEffectsHarness onMemoryOpen={(s) => { setter = s; }} />,
    );
    // Open Memory (the initial mount effect fires with null and closes it,
    // so we open it again here to simulate a user action).
    act(() => { setter(true); });
    expect(screen.getByTestId("memory-state").textContent).toBe("open");

    // Create a new session — this sets activeSessionId from null to a number.
    act(() => {
      useChatStore.getState().newSession();
    });

    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
  });
});

// ---------------------------------------------------------------------------
// Fix 2: Canvas keep-alive — always-mounted layout
// ---------------------------------------------------------------------------
describe("Fix 2 — CitationCanvas always-mounted layout", () => {
  /**
   * The fix renders the CitationCanvas wrapper div with `hidden` and `inert`
   * when memoryOpen is true, rather than unmounting it. This test verifies the
   * DOM structure using a lightweight stand-in for the full ChatPage layout:
   * we render the two-panel div pattern and assert the canvas wrapper is present
   * but hidden when memory is open, and visible when memory is closed.
   */
  function TwoPanelLayout({ memoryOpen }: { memoryOpen: boolean }) {
    return (
      <div>
        {/* Canvas wrapper — always present, hidden when Memory is open */}
        <div
          data-testid="canvas-wrapper"
          className="h-full w-full"
          hidden={memoryOpen}
          aria-hidden={memoryOpen || undefined}
          {...(memoryOpen ? { inert: true } : {})}
        >
          <div data-testid="citation-canvas-sentinel" />
        </div>

        {/* Memory overlay — conditionally rendered */}
        {memoryOpen && (
          <div data-testid="memory-overlay" className="absolute inset-0" />
        )}
      </div>
    );
  }

  it("CitationCanvas wrapper is present in DOM when Memory is open (not unmounted)", () => {
    const { rerender } = render(<TwoPanelLayout memoryOpen={false} />);

    // Canvas visible initially.
    const wrapper = screen.getByTestId("canvas-wrapper");
    expect(wrapper).toBeInTheDocument();
    expect(wrapper).not.toHaveAttribute("hidden");

    // Open Memory — Canvas wrapper stays in DOM (keep-alive), just hidden.
    rerender(<TwoPanelLayout memoryOpen={true} />);

    expect(screen.getByTestId("canvas-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("citation-canvas-sentinel")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-wrapper")).toHaveAttribute("hidden");
  });

  it("Memory overlay renders on top when memoryOpen=true, is absent when false", () => {
    const { rerender } = render(<TwoPanelLayout memoryOpen={false} />);
    expect(screen.queryByTestId("memory-overlay")).toBeNull();

    rerender(<TwoPanelLayout memoryOpen={true} />);
    expect(screen.getByTestId("memory-overlay")).toBeInTheDocument();

    rerender(<TwoPanelLayout memoryOpen={false} />);
    expect(screen.queryByTestId("memory-overlay")).toBeNull();
  });

  it("canvas wrapper has inert attribute when Memory is open (not focusable)", () => {
    const { rerender } = render(<TwoPanelLayout memoryOpen={false} />);
    expect(
      screen.getByTestId("canvas-wrapper").hasAttribute("inert"),
    ).toBe(false);

    rerender(<TwoPanelLayout memoryOpen={true} />);
    expect(
      screen.getByTestId("canvas-wrapper").hasAttribute("inert"),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Fix 1 + 2 integration: openCitation while Memory open → Memory closes,
// Canvas visible (not hidden), canvas wrapper not inert.
// ---------------------------------------------------------------------------
describe("Fix 1+2 integration — citation click while Memory open clears hidden/inert", () => {
  function IntegratedHarness({
    onMemoryOpen,
  }: {
    onMemoryOpen?: (setter: (v: boolean) => void) => void;
  }) {
    const [memoryOpen, setMemoryOpen] = useState(false);

    // Expose setter for tests.
    useEffect(() => {
      onMemoryOpen?.(setMemoryOpen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Fix 1: mirrors ChatPage subscription approach.
    useEffect(() => {
      return useCanvasStore.subscribe((state) => {
        if (state.open) setMemoryOpen(false);
      });
    }, []);

    return (
      <div>
        <div
          data-testid="canvas-wrapper"
          hidden={memoryOpen}
          aria-hidden={memoryOpen || undefined}
          {...(memoryOpen ? { inert: true } : {})}
        />
        {memoryOpen && <div data-testid="memory-overlay" />}
        <span data-testid="memory-state">{memoryOpen ? "open" : "closed"}</span>
      </div>
    );
  }

  it("openCitation while Memory open → Memory closed, Canvas wrapper not hidden/inert", () => {
    let setter!: (v: boolean) => void;
    render(<IntegratedHarness onMemoryOpen={(s) => { setter = s; }} />);

    // Open Memory to simulate the user having opened it.
    act(() => { setter(true); });

    // Confirm: memory open, canvas wrapper hidden/inert.
    expect(screen.getByTestId("canvas-wrapper")).toHaveAttribute("hidden");
    expect(screen.getByTestId("memory-overlay")).toBeInTheDocument();

    act(() => {
      useCanvasStore.getState().openCitation(7);
    });

    // After: memory closed, canvas wrapper visible + not inert.
    expect(screen.getByTestId("memory-state").textContent).toBe("closed");
    expect(screen.getByTestId("canvas-wrapper")).not.toHaveAttribute("hidden");
    expect(screen.getByTestId("canvas-wrapper")).not.toHaveAttribute("inert");
    expect(screen.queryByTestId("memory-overlay")).toBeNull();
  });
});
