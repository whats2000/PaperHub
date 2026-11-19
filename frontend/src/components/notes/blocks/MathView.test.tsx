import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InlineMathView } from "./InlineMathView";
import { MathView } from "./MathView";

const field = () => screen.getByRole("textbox", { name: "LaTeX" });
const isOpen = () => field().closest("[data-state]")!.getAttribute("data-state") === "open";

describe("MathView", () => {
  it("draws an equation copied from a paper without its \\label, which KaTeX would print as text", () => {
    const latex = String.raw`k = \frac{n \times c}{e} \label{eq:es}`;
    const { container } = render(<MathView latex={latex} onChange={() => undefined} />);
    const math = container.querySelector(".katex")!;
    expect(math.textContent).not.toContain("label");
    expect(math.textContent).not.toContain("eq:es");
  });

  it("shows the LaTeX above a live preview while editing", () => {
    const { container } = render(<MathView latex="" onChange={() => undefined} />);
    expect(isOpen()).toBe(true);
    const preview = container.querySelector("[data-math-render]")!;
    // The field comes first, the rendered formula under it.
    expect(field().compareDocumentPosition(preview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("Enter finishes and shows the formula; Shift+Enter is a new line", () => {
    const onLeave = vi.fn();
    render(<MathView latex="x" onChange={() => undefined} onLeave={onLeave} />);
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(field(), { key: "Enter", shiftKey: true });
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(isOpen()).toBe(false);
    expect(onLeave).toHaveBeenCalledWith("down");
  });

  it("Escape and clicking outside leave editing", () => {
    render(<MathView latex="x" onChange={() => undefined} />);
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    fireEvent.keyDown(field(), { key: "Escape" });
    expect(isOpen()).toBe(false);
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    fireEvent.blur(field());
    expect(isOpen()).toBe(false);
  });

  it("arrows past the first or last line leave editing that way", () => {
    const onLeave = vi.fn();
    render(<MathView latex={"a\nb"} onChange={() => undefined} onLeave={onLeave} />);
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    const f = field() as HTMLTextAreaElement;
    f.setSelectionRange(3, 3);
    fireEvent.keyDown(f, { key: "ArrowUp" }); // on the last line: stays
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(f, { key: "ArrowDown" });
    expect(onLeave).toHaveBeenLastCalledWith("down");
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    f.setSelectionRange(0, 0);
    fireEvent.keyDown(f, { key: "ArrowUp" });
    expect(onLeave).toHaveBeenLastCalledWith("up");
  });
});

describe("InlineMathView", () => {
  it("click edits the LaTeX; Enter saves it", () => {
    const onCommit = vi.fn();
    const { container } = render(<InlineMathView latex="x^2" onCommit={onCommit} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    fireEvent.click(screen.getByTitle("Click to edit the LaTeX"));
    expect(isOpen()).toBe(true);
    fireEvent.change(field(), { target: { value: "y^3" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(isOpen()).toBe(false);
    expect(onCommit).toHaveBeenCalledWith("y^3");
  });

  it("an equation left empty is removed", () => {
    const onCommit = vi.fn();
    render(<InlineMathView latex="" autoEdit onCommit={onCommit} />);
    expect(isOpen()).toBe(true);
    fireEvent.blur(field());
    expect(onCommit).toHaveBeenCalledWith("");
  });
});
