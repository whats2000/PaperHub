import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EditorTip, TIP_STORAGE_KEY } from "@/components/notes/EditorTip";
import { GUIDE_SECTIONS } from "@/components/notes/guideData";
import { ShortcutsGuide } from "@/components/notes/ShortcutsGuide";

describe("ShortcutsGuide", () => {
  it("lists every section", () => {
    expect(GUIDE_SECTIONS.map((s) => s.id)).toEqual([
      "marks",
      "lineStart",
      "anywhere",
      "lists",
      "moving",
      "keys",
      "pane",
      "paper",
    ]);
    render(<ShortcutsGuide open onOpenChange={() => {}} trigger={<button>?</button>} />);
    for (const h of [
      "Marks as you type",
      "At the start of a line",
      "Anywhere",
      "Lists",
      "Moving around",
      "Keys",
      "The notes pane",
      "On the paper",
    ]) {
      expect(screen.getByRole("heading", { name: h })).toBeInTheDocument();
    }
    expect(screen.getByText("**bold**")).toBeInTheDocument();
    expect(screen.getByText("Ctrl+Shift+M")).toBeInTheDocument();
    expect(screen.getByText("Ctrl+F")).toBeInTheDocument();
    expect(screen.getByText("Find in the note (Enter: next, Shift+Enter: previous)")).toBeInTheDocument();
    expect(screen.getByText("[[##")).toBeInTheDocument();
    expect(screen.getByText("Link a heading in any note")).toBeInTheDocument();
    expect(screen.getByText("Alt+↑")).toBeInTheDocument();
    expect(screen.getByText("Move the block (or the selected blocks) up or down")).toBeInTheDocument();
  });

  it("closes with Escape and says so through onOpenChange", async () => {
    let open = true;
    render(
      <ShortcutsGuide open={open} onOpenChange={(o) => (open = o)} trigger={<button>?</button>} />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(open).toBe(false));
  });
});

describe("EditorTip", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("shows once, and a dismissal is remembered", () => {
    const { unmount } = render(<EditorTip onOpenGuide={() => {}} />);
    expect(screen.getByText(/New here/)).toBeInTheDocument();
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss the tip" }));
    });
    expect(TIP_STORAGE_KEY).toBe("paperhub-notes-tip-v1");
    expect(localStorage.getItem(TIP_STORAGE_KEY)).toBe("1");
    unmount();
    render(<EditorTip onOpenGuide={() => {}} />);
    expect(screen.queryByText(/New here/)).not.toBeInTheDocument();
  });

  it("still shows when storage is blocked", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      render(<EditorTip onOpenGuide={() => {}} />);
      expect(screen.getByText(/New here/)).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it("opens the guide from its link", () => {
    let opened = 0;
    render(<EditorTip onOpenGuide={() => opened++} />);
    fireEvent.click(screen.getByRole("button", { name: "All shortcuts" }));
    expect(opened).toBe(1);
  });
});
