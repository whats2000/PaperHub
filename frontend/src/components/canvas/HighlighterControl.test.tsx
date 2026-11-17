import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { BoxToolButton } from "@/components/canvas/BoxTool";
import { HighlighterControl } from "@/components/canvas/HighlighterControl";
import { useMarksStore } from "@/store/marks";

beforeEach(() => {
  useMarksStore.setState({ colors: [], boxOn: false, penOn: false, penColor: "green", penSession: [] });
});

describe("HighlighterControl", () => {
  it("turns the pen on with its colour named; the colours open beside it; Escape turns it off", () => {
    render(<HighlighterControl shown />);
    const button = screen.getByRole("button", { name: "Highlighter" });
    const group = document.querySelector<HTMLElement>('[role="group"][aria-label="Highlighter colour"]')!;
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(group.dataset.state).toBe("closed");
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.textContent).toBe("Highlighter: Key result");
    expect(group.dataset.state).toBe("open");
    // A colour picked is the pen's colour, and is remembered.
    fireEvent.click(screen.getByRole("button", { name: "Method" }));
    expect(button.textContent).toBe("Highlighter: Method");
    expect(useMarksStore.getState().penColor).toBe("blue");
    // An Escape something else took leaves the pen on.
    const handled = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    handled.preventDefault();
    act(() => {
      document.body.dispatchEvent(handled);
    });
    expect(useMarksStore.getState().penOn).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(useMarksStore.getState().penOn).toBe(false);
    expect(group.dataset.state).toBe("closed");
    // On again: the colour chosen last.
    fireEvent.click(button);
    expect(button.textContent).toBe("Highlighter: Method");
  });

  it("pen and Box turn each other off", () => {
    render(
      <>
        <BoxToolButton shown />
        <HighlighterControl shown />
      </>,
    );
    const pen = screen.getByRole("button", { name: "Highlighter" });
    const box = screen.getByRole("button", { name: /Box/ });
    fireEvent.click(box);
    fireEvent.click(pen);
    expect(box.getAttribute("aria-pressed")).toBe("false");
    expect(pen.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(box);
    expect(box.getAttribute("aria-pressed")).toBe("true");
    expect(pen.getAttribute("aria-pressed")).toBe("false");
  });

  it("the label glides to its new width; the colours fold back in on exit (reduced motion respected)", () => {
    render(<HighlighterControl shown />);
    const button = screen.getByRole("button", { name: "Highlighter" });
    const label = button.querySelector("span")!;
    expect(label.className).toContain("transition-[width]");
    expect(label.className).toContain("motion-reduce:transition-none");
    fireEvent.click(button);
    fireEvent.click(button);
    const group = document.querySelector<HTMLElement>('[role="group"]')!;
    expect(group.dataset.state).toBe("closed");
    expect(group.className).toContain("transition-[max-width,opacity]");
    expect(group.className).toContain("motion-reduce:transition-none");
  });
});
