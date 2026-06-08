import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SlideContextChip } from "./SlideContextChip";

describe("SlideContextChip", () => {
  it("shows the active slide page and is attached by default (eye open)", () => {
    render(<SlideContextChip page={5} attached onToggle={() => {}} />);
    expect(screen.getByText(/Slide 5/)).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });

  it("calls onToggle when the eye is clicked", () => {
    const onToggle = vi.fn();
    render(<SlideContextChip page={3} attached={false} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledOnce();
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/Slide 3/)).toBeInTheDocument();
  });
});
