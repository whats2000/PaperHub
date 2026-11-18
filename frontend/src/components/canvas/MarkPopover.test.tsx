import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarkPopover } from "@/components/canvas/MarkPopover";
import { placeSelectionPopover } from "@/lib/cardPlacement";
import { useMarksStore } from "@/store/marks";
import type { MarkColorInfo } from "@/types/notes";

const COLORS: MarkColorInfo[] = [
  { color: "green", name: "Key result", visible: true },
  { color: "blue", name: "Methods we reuse", visible: true },
  { color: "pink", name: "Question", visible: true },
  { color: "purple", name: "Disagree", visible: false },
];

beforeEach(() => {
  useMarksStore.setState({ markStyle: "highlight", colors: COLORS });
});
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
    onMark: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  const utils = render(<MarkPopover {...props} />);
  return { ...utils, props };
}

describe("MarkPopover", () => {
  it("offers the four styles, the named colours, no colour and +; no Quote to note or Add note", () => {
    renderPopover();
    const bar = screen.getByRole("toolbar", { name: "Mark the selection" });
    expect(bar.dataset.side).toBe("below");
    expect(Number.parseFloat(bar.style.top)).toBeGreaterThan(120);
    for (const name of ["Highlight", "Underline", "Wavy", "Bar"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    // The user's labels; a hidden colour is still offered (it hides marks, not the pen).
    expect(screen.getByRole("button", { name: "Highlight: Key result" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Highlight: Methods we reuse" })).toHaveAttribute("title", "Methods we reuse");
    expect(screen.getByRole("button", { name: "Highlight: Disagree" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a colour" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quote to note" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add note" })).toBeNull();
    // A highlight needs a colour.
    expect(screen.getByRole("button", { name: "A highlight needs a colour" })).toBeDisabled();
  });

  it("marks in the chosen style and colour, and remembers the style", () => {
    const { props, unmount } = renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "Wavy" }));
    expect(screen.getByRole("button", { name: "Wavy" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Wavy: Question" }));
    expect(props.onMark).toHaveBeenCalledWith("wavy", "pink");
    fireEvent.click(screen.getByRole("button", { name: "Wavy: no colour" }));
    expect(props.onMark).toHaveBeenLastCalledWith("wavy", null);
    unmount();
    // The next selection starts in the style chosen last.
    renderPopover();
    expect(screen.getByRole("button", { name: "Wavy" })).toHaveAttribute("aria-pressed", "true");
    expect(useMarksStore.getState().markStyle).toBe("wavy");
  });

  it("a figure takes no style: colours and no colour only", () => {
    const { props } = renderPopover({ target: "figure" });
    expect(screen.queryByRole("button", { name: "Wavy" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Mark: Key result" }));
    expect(props.onMark).toHaveBeenCalledWith(null, "green");
    fireEvent.click(screen.getByRole("button", { name: "Mark: no colour" }));
    expect(props.onMark).toHaveBeenLastCalledWith(null, null);
  });

  it("+ adds a labelled colour (no label, no save; yellow refused) and marks with it", async () => {
    const addColor = vi.fn(() => Promise.resolve("c_new"));
    useMarksStore.setState({ addColor });
    const { props } = renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "Add a colour" }));
    const save = await screen.findByRole("button", { name: "Add and mark" });
    expect(save).toBeDisabled(); // a colour needs a label
    const label = screen.getByPlaceholderText(/What does this colour mean/);
    fireEvent.change(label, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.change(label, { target: { value: "Key result" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Another colour already has this label.");
    fireEvent.change(label, { target: { value: "To verify" } });
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "#fde047" } });
    expect(screen.getByRole("alert")).toHaveTextContent("yellow is reserved for citations");
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Colour"), { target: { value: "#1f8a8a" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(addColor).toHaveBeenCalledWith("#1f8a8a", "To verify");
    await waitFor(() => expect(props.onMark).toHaveBeenCalledWith("highlight", "c_new"));
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
