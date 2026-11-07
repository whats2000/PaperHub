import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GraphLegend } from "./GraphLegend";

const domains = [
  { id: 1, name: "Vision", color_index: 0 },
  { id: 2, name: "Cell Biology", color_index: null },
  { id: 3, name: "Genomics", color_index: null },
];

describe("GraphLegend", () => {
  it("lists the domains folded into Other when it is opened", async () => {
    const user = userEvent.setup();
    render(<GraphLegend domains={domains} />);
    const other = screen.getByTestId("legend-other");
    const toggle = within(other).getByRole("button", { name: /Other\s*2/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(screen.getByTestId("legend-other-list")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Cell Biology",
      "Genomics",
    ]);
  });

  it("stays folded once the reader folds it, until the reader opens it again", async () => {
    // Folding is the reader's call alone; nothing else on the page reopens it.
    const { rerender } = render(<GraphLegend domains={domains} />);
    const toggle = () => screen.getByRole("button", { name: "Legend" });
    await userEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    rerender(<GraphLegend domains={[...domains]} />);
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
  });
});

describe("GraphLegend hover highlight", () => {
  it("reports the hovered category and clears it on leave", async () => {
    const onHighlight = vi.fn();
    render(<GraphLegend domains={domains} onHighlight={onHighlight} />);
    await userEvent.hover(screen.getByText("Vision"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "domains", ids: [1] });
    await userEvent.unhover(screen.getByText("Vision"));
    expect(onHighlight).toHaveBeenLastCalledWith(null);
    await userEvent.hover(screen.getByTestId("legend-other"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "domains", ids: [2, 3] });
    await userEvent.hover(screen.getByText("Citation"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "citations" });
    await userEvent.hover(screen.getByText("Idea: source → target"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "ideas" });
    await userEvent.hover(screen.getByText("Saved / trying / worked"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "status", statuses: ["saved", "trying", "worked"] });
    await userEvent.hover(screen.getByText("Trying"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "status", statuses: ["trying"] });
    await userEvent.hover(screen.getByText("Private document"));
    expect(onHighlight).toHaveBeenLastCalledWith({ kind: "private" });
  });

  it("does not highlight the size row, which is no category", async () => {
    const onHighlight = vi.fn();
    render(<GraphLegend domains={domains} onHighlight={onHighlight} />);
    await userEvent.hover(screen.getByText("Size = related ideas"));
    expect(onHighlight).not.toHaveBeenCalled();
  });
});
