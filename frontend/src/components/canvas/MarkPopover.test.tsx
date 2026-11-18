import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MarkPopover } from "@/components/canvas/MarkPopover";
import { placeSelectionPopover } from "@/lib/cardPlacement";
import type { MarkColorInfo } from "@/types/notes";

const COLORS: MarkColorInfo[] = [
  { color: "green", name: "Key result", visible: true },
  { color: "blue", name: "Methods we reuse", visible: true },
  { color: "pink", name: "Question", visible: true },
  { color: "purple", name: "Disagree", visible: false },
];

afterEach(() => {
  vi.useRealTimers();
});

describe("placeSelectionPopover", () => {
  const size = { width: 300, height: 36 };
  const bounds = { width: 800, height: 600 };

  it("sits below the selection, centred on it", () => {
    const p = placeSelectionPopover({ selection: { top: 100, bottom: 120, left: 300, right: 400 }, size, bounds });
    expect(p).toEqual({ side: "below", top: 128, left: 200 });
  });

  it("flips above only when there is no room below", () => {
    const p = placeSelectionPopover({ selection: { top: 540, bottom: 580, left: 300, right: 400 }, size, bounds });
    expect(p.side).toBe("above");
    expect(p.top).toBe(540 - 8 - 36);
  });

  it("stays inside the reader at its edges", () => {
    const p = placeSelectionPopover({ selection: { top: 100, bottom: 120, left: 0, right: 20 }, size, bounds });
    expect(p.left).toBe(6);
    const q = placeSelectionPopover({ selection: { top: 100, bottom: 120, left: 780, right: 800 }, size, bounds });
    expect(q.left).toBe(800 - 300 - 6);
  });
});

function renderPopover(over: Partial<Parameters<typeof MarkPopover>[0]> = {}) {
  const props = {
    open: true,
    anchor: { top: 100, bottom: 120, left: 300, right: 400 },
    bounds: { width: 800, height: 600 },
    colors: COLORS,
    onHighlight: vi.fn(),
    onQuote: vi.fn(),
    onAddNote: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  const utils = render(<MarkPopover {...props} />);
  return { ...utils, props };
}

describe("MarkPopover", () => {
  it("sits below the selection with the named colours, Quote to note and Add note", () => {
    renderPopover();
    const bar = screen.getByRole("toolbar", { name: "Mark the selection" });
    expect(bar.dataset.side).toBe("below");
    expect(Number.parseFloat(bar.style.top)).toBeGreaterThan(120);
    // The user's names; a hidden colour is still offered (it hides marks, not the pen).
    expect(screen.getByRole("button", { name: "Highlight: Key result" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Highlight: Methods we reuse" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Highlight: Disagree" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quote to note" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add note" })).toBeInTheDocument();
  });

  it("calls back with the chosen action", () => {
    const { props } = renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "Highlight: Question" }));
    expect(props.onHighlight).toHaveBeenCalledWith("pink");
    fireEvent.click(screen.getByRole("button", { name: "Quote to note" }));
    expect(props.onQuote).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(props.onAddNote).toHaveBeenCalled();
  });

  it("closes on Escape and on a click outside, and animates out", () => {
    vi.useFakeTimers();
    const { props, rerender } = renderPopover();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(props.onClose).toHaveBeenCalledTimes(1);
    fireEvent.pointerDown(document.body);
    expect(props.onClose).toHaveBeenCalledTimes(2);
    rerender(<MarkPopover {...props} open={false} />);
    // Still there, closing, until its exit animation ends.
    expect(screen.getByRole("toolbar").dataset.state).toBe("closed");
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByRole("toolbar")).toBeNull();
  });
});
