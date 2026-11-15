import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SqlCard } from "./SqlCard";

describe("SqlCard", () => {
  it("starts open and height-animates closed and open from its header", () => {
    render(<SqlCard sql="SELECT 1" />);
    const header = screen.getByRole("button", { name: /SQL/ });
    const body = screen.getByText("SELECT 1").closest("[data-state]") as HTMLElement;
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(body).toHaveAttribute("data-state", "open");
    expect(body.className).toContain("transition-[grid-template-rows]");

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(body).toHaveAttribute("data-state", "closed");
    expect(screen.getByText("SELECT 1")).toBeInTheDocument();
  });
});
