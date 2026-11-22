import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TOC_PIN_SETTLE_MS, TOC_RAIL_CLOSE_MS, TocRail } from "@/components/notes/TocRail";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

vi.mock("@/lib/notes/reveal", () => ({
  revealBlock: vi.fn(() => Promise.resolve(true)),
  unfoldInNote: vi.fn(() => () => {}),
}));

function blk(id: string, type: string, extra: Partial<NoteBlock> = {}): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: type as NoteBlock["type"],
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
    ...extra,
  };
}
const heading = (id: string, text: string, level: number) =>
  blk(id, "heading", { props: { level }, content: [{ type: "text", text, styles: {} }] as never, text });

function seed(blocks: NoteBlock[]) {
  useNotesStore.setState({
    notes: {
      7: {
        note: { id: 7, target_kind: "paper", target_id: "3", title: "T", revision: 1, updated_at: "" },
        blocks,
        suggestions: [],
        comments: [],
        revision: 1,
      },
    },
    tocOn: true,
  });
}

const THREE = [
  blk("s1", "section", { props: { title: "1 Introduction" }, section_key: "1 Introduction" }),
  blk("p1", "paragraph"),
  heading("h2", "Setup", 2),
  heading("h3", "Details", 3),
];

/** A scroll panel holding one `.bn-block-outer` per block id. */
function makeRoot(ids: string[]): HTMLElement {
  const root = document.createElement("div");
  for (const id of ids) {
    const el = document.createElement("div");
    el.className = "bn-block-outer";
    el.setAttribute("data-id", id);
    root.appendChild(el);
  }
  document.body.appendChild(root);
  return root;
}
const rect = (top: number) => ({ top, bottom: top + 20, left: 0, right: 100, width: 100, height: 20, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
function placeAt(root: HTMLElement, id: string, top: number) {
  const el = root.querySelector<HTMLElement>(`[data-id="${id}"]`)!;
  el.getBoundingClientRect = () => rect(top);
}

let root: HTMLElement;
beforeEach(() => {
  seed(THREE);
  root = makeRoot(["s1", "p1", "h2", "h3"]);
  root.getBoundingClientRect = () => ({ ...rect(100), bottom: 600, height: 500 });
});
afterEach(() => {
  root.remove();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("TocRail", () => {
  it("is hidden with fewer than two entries", () => {
    seed([heading("h1", "Only", 1), blk("p1", "paragraph")]);
    render(<TocRail noteId={7} root={root} />);
    expect(screen.queryByTestId("toc-rail")).toBeNull();
  });

  it("draws one short line per entry, shorter by level, positioned over the content", () => {
    render(<TocRail noteId={7} root={root} />);
    const rail = screen.getByTestId("toc-rail");
    expect(rail.className).toMatch(/\babsolute\b/);
    const lines = rail.querySelectorAll<HTMLElement>("[data-toc-line]");
    expect(lines).toHaveLength(3);
    const widths = [...lines].map((l) => parseInt(l.style.width, 10));
    expect(widths[0]).toBeGreaterThan(widths[1]!);
    expect(widths[1]).toBeGreaterThan(widths[2]!);
  });

  it("hover slides the list out over the content; leaving retracts it after a short delay", () => {
    vi.useFakeTimers();
    render(<TocRail noteId={7} root={root} />);
    const list = screen.getByTestId("toc-rail-list");
    expect(list).toHaveAttribute("data-state", "closed");
    expect(list.className).toMatch(/\babsolute\b/);
    expect(list.className).toMatch(/data-\[state=closed\]:translate-x/);
    fireEvent.mouseEnter(screen.getByTestId("toc-rail"));
    expect(list).toHaveAttribute("data-state", "open");
    expect(screen.getByRole("button", { name: "Setup" })).toBeInTheDocument();
    fireEvent.mouseLeave(screen.getByTestId("toc-rail"));
    expect(list).toHaveAttribute("data-state", "open");
    act(() => {
      vi.advanceTimersByTime(TOC_RAIL_CLOSE_MS);
    });
    expect(list).toHaveAttribute("data-state", "closed");
  });

  it("re-entering before the delay keeps it open", () => {
    vi.useFakeTimers();
    render(<TocRail noteId={7} root={root} />);
    const rail = screen.getByTestId("toc-rail");
    fireEvent.mouseEnter(rail);
    fireEvent.mouseLeave(rail);
    act(() => {
      vi.advanceTimersByTime(TOC_RAIL_CLOSE_MS / 2);
    });
    fireEvent.mouseEnter(rail);
    act(() => {
      vi.advanceTimersByTime(TOC_RAIL_CLOSE_MS * 2);
    });
    expect(screen.getByTestId("toc-rail-list")).toHaveAttribute("data-state", "open");
  });

  it("a click reveals and flashes the entry, unfolding a folded part", () => {
    render(<TocRail noteId={7} root={root} />);
    fireEvent.mouseEnter(screen.getByTestId("toc-rail"));
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(vi.mocked(unfoldInNote)).toHaveBeenCalledWith(7);
    const [calledRoot, calledId, opts] = vi.mocked(revealBlock).mock.calls[0]!;
    expect(calledRoot).toBe(root);
    expect(calledId).toBe("h3");
    expect(typeof opts?.unfold).toBe("function");
    // The clicked entry is the current one at once.
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute("aria-current", "location");
  });

  it("marks the last entry at or above the pane's top + 90 px as current while scrolling", async () => {
    render(<TocRail noteId={7} root={root} />);
    placeAt(root, "s1", 40);
    placeAt(root, "h2", 185);
    placeAt(root, "h3", 400);
    fireEvent.scroll(root);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Setup", hidden: true })).toHaveAttribute("aria-current", "location"),
    );
    const lines = screen.getByTestId("toc-rail").querySelectorAll<HTMLElement>("[data-toc-line]");
    expect(lines[1]).toHaveAttribute("data-current", "true");
    // A programmatic scroll (Follow reading) only moves the highlight.
    placeAt(root, "h2", 300);
    fireEvent.scroll(root);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "1 Introduction", hidden: true })).toHaveAttribute(
        "aria-current",
        "location",
      ),
    );
    expect(root.getAttribute("style") ?? "").toBe("");
  });

  it("fades out before it leaves when turned off", async () => {
    render(<TocRail noteId={7} root={root} />);
    act(() => useNotesStore.getState().setTocOn(false));
    expect(screen.getByTestId("toc-rail")).toHaveAttribute("data-state", "closed");
    await vi.waitFor(() => expect(screen.queryByTestId("toc-rail")).toBeNull());
  });

  it("Esc in the list closes it and leaves focus on the rail, closed", async () => {
    const user = userEvent.setup();
    render(<TocRail noteId={7} root={root} />);
    const rail = screen.getByRole("button", { name: "Table of contents" });
    await user.tab();
    expect(rail).toHaveFocus();
    expect(screen.getByTestId("toc-rail-list")).toHaveAttribute("data-state", "open");
    await user.tab();
    expect(screen.getByRole("button", { name: "1 Introduction" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(rail).toHaveFocus();
    expect(screen.getByTestId("toc-rail-list")).toHaveAttribute("data-state", "closed");
    expect(rail).toHaveAttribute("aria-expanded", "false");
  });

  it("leaving after a click on an entry closes the list with focus back on the rail, never inside the hidden list", () => {
    vi.useFakeTimers();
    render(<TocRail noteId={7} root={root} />);
    const wrap = screen.getByTestId("toc-rail");
    fireEvent.mouseEnter(wrap);
    const entry = screen.getByRole("button", { name: "1 Introduction" });
    entry.focus(); // a mouse click focuses the entry
    fireEvent.mouseLeave(wrap);
    act(() => {
      vi.advanceTimersByTime(TOC_RAIL_CLOSE_MS);
    });
    const list = screen.getByTestId("toc-rail-list");
    expect(list).toHaveAttribute("data-state", "closed");
    expect(list.contains(document.activeElement)).toBe(false);
    expect(screen.getByRole("button", { name: "Table of contents" })).toHaveFocus();
    // Focus coming back to the rail does not reopen the list.
    expect(list).toHaveAttribute("data-state", "closed");
  });

  it("a tap or a click while hovering leaves the list open", async () => {
    const user = userEvent.setup();
    render(<TocRail noteId={7} root={root} />);
    const rail = screen.getByRole("button", { name: "Table of contents" });
    await user.click(rail); // hover + focus + click, like a tap
    expect(screen.getByTestId("toc-rail-list")).toHaveAttribute("data-state", "open");
    await user.click(rail);
    expect(screen.getByTestId("toc-rail-list")).toHaveAttribute("data-state", "open");
  });

  it("the rail button controls the list", () => {
    render(<TocRail noteId={7} root={root} />);
    const rail = screen.getByRole("button", { name: "Table of contents" });
    const id = rail.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id!)).toBe(screen.getByTestId("toc-rail-list"));
  });

  it("fades in on first show", async () => {
    render(<TocRail noteId={7} root={root} />);
    expect(screen.getByTestId("toc-rail")).toHaveAttribute("data-state", "closed");
    await vi.waitFor(() => expect(screen.getByTestId("toc-rail")).toHaveAttribute("data-state", "open"));
  });

  it("a clicked entry stays current through its reveal, then the first user scroll releases it", async () => {
    render(<TocRail noteId={7} root={root} />);
    placeAt(root, "s1", 40);
    placeAt(root, "h2", 185);
    placeAt(root, "h3", 350); // centred by the reveal: below the 90 px line
    fireEvent.mouseEnter(screen.getByTestId("toc-rail"));
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    fireEvent.scroll(root); // the reveal's own smooth scroll
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute("aria-current", "location");
    await new Promise((r) => setTimeout(r, TOC_PIN_SETTLE_MS + 50));
    fireEvent.scroll(root); // the user scrolls a little; Details is still on screen
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Setup" })).toHaveAttribute("aria-current", "location"),
    );
  });

  it("re-reads the current entry after a fold has redrawn the note (a frame later)", async () => {
    placeAt(root, "s1", 40);
    placeAt(root, "h2", 185);
    placeAt(root, "h3", 400);
    render(<TocRail noteId={7} root={root} />);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Setup", hidden: true })).toHaveAttribute("aria-current", "location"),
    );
    // As in the app: the store folds first; the note hides the part only
    // once its decorations redraw, so the layout moves after that commit.
    const unsub = useNotesStore.subscribe((s, prev) => {
      if (s.folded !== prev.folded) requestAnimationFrame(() => placeAt(root, "h3", 150));
    });
    act(() => {
      useNotesStore.setState({ folded: { 7: ["d1"] } });
    });
    unsub();
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Details", hidden: true })).toHaveAttribute("aria-current", "location"),
    );
  });

  it("re-reads the current entry when the note content resizes", async () => {
    const observed = new Set<Element>();
    let onResize: (() => void) | null = null;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: () => void) {
          onResize = cb;
        }
        observe(el: Element) {
          observed.add(el);
        }
        unobserve(el: Element) {
          observed.delete(el);
        }
        disconnect() {
          observed.clear();
        }
      },
    );
    // The pane as NotesPane builds it: the zero-height rail layer first,
    // then the note content.
    const pane = document.createElement("div");
    const layer = document.createElement("div");
    layer.setAttribute("data-toc-layer", "");
    const content = document.createElement("div");
    pane.append(layer, content);
    for (const id of ["s1", "p1", "h2", "h3"]) {
      const el = document.createElement("div");
      el.className = "bn-block-outer";
      el.setAttribute("data-id", id);
      content.appendChild(el);
    }
    document.body.appendChild(pane);
    pane.getBoundingClientRect = () => ({ ...rect(100), bottom: 600, height: 500 });
    placeAt(pane, "s1", 40);
    placeAt(pane, "h2", 150);
    placeAt(pane, "h3", 160);
    render(<TocRail noteId={7} root={pane} />);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Details", hidden: true })).toHaveAttribute("aria-current", "location"),
    );
    // A narrower pane wraps the text: Details drops below the line. Only
    // the content changes size (the rail layer has no height).
    placeAt(pane, "h3", 400);
    expect(observed.has(content)).toBe(true);
    act(() => {
      if (observed.has(content)) onResize?.();
    });
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Setup", hidden: true })).toHaveAttribute("aria-current", "location"),
    );
    document.body.removeChild(pane);
    vi.unstubAllGlobals();
  });

  it("a slow reveal keeps the clicked entry current through its first scroll", async () => {
    let finish: (ok: boolean) => void = () => {};
    vi.mocked(revealBlock).mockImplementationOnce(
      () =>
        new Promise<boolean>((r) => {
          finish = r;
        }),
    );
    render(<TocRail noteId={7} root={root} />);
    placeAt(root, "s1", 40);
    placeAt(root, "h2", 185);
    placeAt(root, "h3", 350);
    fireEvent.mouseEnter(screen.getByTestId("toc-rail"));
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    // The part is still unfolding: nothing scrolls for a while.
    await new Promise((r) => setTimeout(r, TOC_PIN_SETTLE_MS + 100));
    await act(async () => {
      finish(true);
      await Promise.resolve();
    });
    fireEvent.scroll(root); // the reveal's own (first) scroll
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.getByRole("button", { name: "Details" })).toHaveAttribute("aria-current", "location");
    await new Promise((r) => setTimeout(r, TOC_PIN_SETTLE_MS + 50));
    fireEvent.scroll(root); // now the user
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "Setup" })).toHaveAttribute("aria-current", "location"),
    );
  });

  it("lists a heading nested under another block in document order", () => {
    seed([
      heading("a", "Alpha", 1),
      heading("c", "Gamma", 1),
      // Child of "a" but listed after "c" in the store.
      { ...heading("b", "Beta", 2), parent_id: "a" },
    ]);
    render(<TocRail noteId={7} root={root} />);
    fireEvent.mouseEnter(screen.getByTestId("toc-rail"));
    const names = screen.getAllByRole("button").filter((b) => b.closest("nav")).map((b) => b.textContent);
    expect(names).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("is off when the toolbar toggle turns it off", () => {
    useNotesStore.setState({ tocOn: false });
    render(<TocRail noteId={7} root={root} />);
    expect(screen.queryByTestId("toc-rail")).toBeNull();
  });
});
