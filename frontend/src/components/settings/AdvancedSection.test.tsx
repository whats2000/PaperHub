import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdvancedSection } from "./AdvancedSection";

describe("AdvancedSection", () => {
  it("starts closed and height-animates open and shut from its header", () => {
    render(
      <AdvancedSection title="Per-slot model overrides">
        <p>field</p>
      </AdvancedSection>,
    );
    const header = screen.getByRole("button", { name: "Per-slot model overrides" });
    const region = screen.getByTestId("advanced-section-body").firstElementChild as HTMLElement;
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(region).toHaveAttribute("data-state", "closed");
    expect(region.className).toContain("transition-[grid-template-rows]");

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(region).toHaveAttribute("data-state", "open");

    fireEvent.click(header);
    // Closing keeps it mounted so the height collapses instead of vanishing.
    expect(region).toHaveAttribute("data-state", "closed");
    expect(screen.getByText("field")).toBeInTheDocument();
  });
});
