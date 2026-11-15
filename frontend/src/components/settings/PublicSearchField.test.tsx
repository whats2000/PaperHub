import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PublicSearchField } from "./PublicSearchField";

const base = { id: "p", label: "Public web search", help: "help" };

describe("PublicSearchField", () => {
  it("click on an off switch opens the warning and does not save", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PublicSearchField {...base} checked={false} onSave={onSave} />);
    const panel = screen.getByTestId("public-search-warning");
    expect(panel).toHaveAttribute("data-state", "closed");
    await userEvent.click(screen.getByRole("switch", { name: "Public web search" }));
    expect(panel).toHaveAttribute("data-state", "open");
    expect(screen.getByText("Your search text will leave this device.")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("Keep it off collapses the panel without saving", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PublicSearchField {...base} checked={false} onSave={onSave} />);
    await userEvent.click(screen.getByRole("switch", { name: "Public web search" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it off" }));
    expect(screen.getByTestId("public-search-warning")).toHaveAttribute("data-state", "closed");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("Turn on public search saves 1", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PublicSearchField {...base} checked={false} onSave={onSave} />);
    await userEvent.click(screen.getByRole("switch", { name: "Public web search" }));
    await userEvent.click(screen.getByRole("button", { name: "Turn on public search" }));
    expect(onSave).toHaveBeenCalledWith("1");
    expect(screen.getByTestId("public-search-warning")).toHaveAttribute("data-state", "closed");
  });

  it("turning off saves 0 at once, and the on note shows while on", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PublicSearchField {...base} checked onSave={onSave} />);
    expect(screen.getByTestId("public-search-on")).toHaveAttribute("data-state", "open");
    expect(screen.getByText(/Queries reach public engines after review/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Public web search" }));
    expect(onSave).toHaveBeenCalledWith("0");
  });

  it("locked: switch disabled with the deployer note", () => {
    render(<PublicSearchField {...base} checked={false} locked onSave={vi.fn()} />);
    expect(screen.getByRole("switch", { name: "Public web search" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Set by the deployer")).toBeInTheDocument();
  });

  it("shows when it was turned on, and omits the clause when unknown", () => {
    const { rerender } = render(
      <PublicSearchField {...base} checked turnedOnAt="2026-11-13 10:42:00" onSave={vi.fn()} />,
    );
    expect(screen.getByText(/Turned on by you, .*2026/)).toBeInTheDocument();
    rerender(<PublicSearchField {...base} checked turnedOnAt={null} onSave={vi.fn()} />);
    expect(screen.queryByText(/Turned on by you/)).not.toBeInTheDocument();
    expect(screen.getByText("On. Queries reach public engines after review.")).toBeInTheDocument();
  });

  it("the confirm panel is a non-modal region, not an alertdialog", () => {
    render(<PublicSearchField {...base} checked={false} onSave={vi.fn()} />);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByTestId("public-search-warning").querySelector('[role="region"]')).not.toBeNull();
  });
});
