import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CitationMarker } from "@/components/canvas/CitationMarker";
import { FigureCitationMarker } from "@/components/canvas/FigureCitationMarker";
import { UnsourcedMarker } from "@/components/canvas/UnsourcedMarker";
import { ViewCitationMarker } from "@/components/canvas/ViewCitationMarker";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() => {
  useCanvasStore.setState({ openCitation: vi.fn() });
});

/** FR-03 (2026-11-15): citations read as bracketed markers, not a bare
 * superscript digit that was easy to miss. */
describe("bracketed citation markers", () => {
  it("a chunk citation reads [n] and still opens the canvas", async () => {
    render(<CitationMarker chunkId={12} ordinal={3} />);
    const button = screen.getByRole("button", { name: "citation 3" });
    expect(button).toHaveTextContent(/^\[3\]$/);
    await userEvent.setup().click(button);
    expect(useCanvasStore.getState().openCitation).toHaveBeenCalledWith(12);
  });

  it("a figure citation keeps its icon inside the brackets", () => {
    render(<FigureCitationMarker paper={20} figureId="fig-003" ordinal={2} />);
    const button = screen.getByRole("button", { name: "figure citation 2" });
    expect(button).toHaveTextContent(/^\[2\]$/);
    const [open, , close] = [...button.childNodes];
    expect(open?.textContent).toBe("[");
    expect(button.querySelector("svg")).not.toBeNull();
    expect(close?.textContent).toBe("2");
  });

  it("a page-region citation reads [⌕ n]", () => {
    render(<ViewCitationMarker paper={20} page={5} box={[1, 2, 3, 4]} ordinal={4} />);
    const button = screen.getByRole("button", { name: "region on page 5, citation 4" });
    expect(button).toHaveTextContent(/^\[4\]$/);
    expect(button.querySelector("svg")).not.toBeNull();
  });

  it("an unsourced mark is a dashed [?]", () => {
    render(<UnsourcedMarker reason="not_read" />);
    const button = screen.getByRole("button", { name: /could not be checked|source/i });
    expect(button).toHaveTextContent(/^\[\?\]$/);
    expect(button.className).toContain("border-dashed");
  });
});
