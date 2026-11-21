import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TOC_RAIL_CLOSE_MS, TocRail } from "@/components/notes/TocRail";
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

  it("is off when the toolbar toggle turns it off", () => {
    useNotesStore.setState({ tocOn: false });
    render(<TocRail noteId={7} root={root} />);
    expect(screen.queryByTestId("toc-rail")).toBeNull();
  });
});
