import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

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
