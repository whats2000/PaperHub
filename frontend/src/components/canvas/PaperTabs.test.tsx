/** The paper tabs the reader and the notes pane share: solid for the
 * chat's papers (three, then "…"), dashed for a paper not in the chat,
 * italic while it is temporary. */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { roveTabs } from "@/lib/paperTabs";

import { PaperTabs } from "./PaperTabs";

const papers = [
  { id: 1, title: "RoboTTT" },
  { id: 2, title: "Mamba" },
  { id: 3, title: "LoRA" },
  { id: 4, title: "Attention" },
];

function setup(over: Partial<Parameters<typeof PaperTabs>[0]> = {}) {
  const props = {
    papers: papers.slice(0, 2),
    outside: [],
    selected: 1,
    onSelect: vi.fn(),
    onAdd: vi.fn(),
    onClose: vi.fn(),
    onKeep: vi.fn(),
    ...over,
  };
  render(
    <div role="tablist" aria-label="Papers" onKeyDown={roveTabs}>
      <PaperTabs {...props} />
    </div>,
  );
  return props;
}

const tabNames = () => screen.getAllByRole("tab").map((t) => t.textContent);

describe("PaperTabs", () => {
  it("draws the chat's papers in order, the selected one marked", () => {
    setup();
    expect(tabNames()).toEqual(["RoboTTT", "Mamba"]);
    expect(screen.getByRole("tab", { name: "RoboTTT" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Mamba" })).toHaveAttribute("aria-selected", "false");
    // A chat's paper has no close or add.
    expect(screen.queryByRole("button", { name: /Close/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add/ })).toBeNull();
  });

  it("shows three papers, then a … menu that marks the selected hidden one", async () => {
    const p = setup({ papers, selected: 4 });
    expect(tabNames()).toEqual(["RoboTTT", "Mamba", "LoRA"]);
    const more = screen.getByRole("button", { name: "More papers" });
    expect(more).toHaveAttribute("data-selected", "true");
    await userEvent.click(more);
    await userEvent.click(await screen.findByRole("menuitemradio", { name: "Attention" }));
    expect(p.onSelect).toHaveBeenCalledWith(4);
  });

  it("a paper not in the chat is dashed with Add and close; italic while temporary", async () => {
    const p = setup({ outside: [{ id: 9, title: "GPT", preview: true }], selected: 9 });
    const tab = screen.getByRole("tab", { name: "GPT" });
    const shell = tab.closest("[data-paper-tab]") as HTMLElement;
    expect(shell).toHaveAttribute("data-dashed", "true");
    expect(shell).toHaveAttribute("data-preview", "true");
    expect(within(shell).getByText("GPT")).toHaveClass("italic");
    await userEvent.click(within(shell).getByRole("button", { name: /Add/ }));
    expect(p.onAdd).toHaveBeenCalledWith(9);
    await userEvent.click(within(shell).getByRole("button", { name: "Close GPT" }));
    expect(p.onClose).toHaveBeenCalledWith(9);
  });

  it("double-clicking a temporary tab keeps it", async () => {
    const p = setup({ outside: [{ id: 9, title: "GPT", preview: true }] });
    await userEvent.dblClick(screen.getByRole("tab", { name: "GPT" }));
    expect(p.onKeep).toHaveBeenCalledWith(9);
  });

  it("a kept tab is dashed but not italic", () => {
    setup({ outside: [{ id: 9, title: "GPT", preview: false }] });
    const shell = screen.getByRole("tab", { name: "GPT" }).closest("[data-paper-tab]") as HTMLElement;
    expect(shell).toHaveAttribute("data-preview", "false");
    expect(within(shell).getByText("GPT")).not.toHaveClass("italic");
  });

  it("arrow keys move across the tabs and pick them", async () => {
    const p = setup({ outside: [{ id: 9, title: "GPT", preview: true }] });
    screen.getByRole("tab", { name: "RoboTTT" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(p.onSelect).toHaveBeenLastCalledWith(2);
    expect(screen.getByRole("tab", { name: "Mamba" })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(p.onSelect).toHaveBeenLastCalledWith(9);
  });

  it("nothing selected (the notes pane on This chat): every tab reachable, none marked", () => {
    setup({ selected: null });
    for (const t of screen.getAllByRole("tab")) expect(t).toHaveAttribute("aria-selected", "false");
  });
});
