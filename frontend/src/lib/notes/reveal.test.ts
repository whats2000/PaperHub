import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REVEAL_GIVE_UP_MS, revealBlock, unfoldInNote } from "@/lib/notes/reveal";
import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

function outer(id: string, attrs: Record<string, string> = {}): HTMLElement {
  const el = document.createElement("div");
  el.className = "bn-block-outer";
  el.setAttribute("data-id", id);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

let root: HTMLElement;
const scrolled: Element[] = [];

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  scrolled.length = 0;
  Object.assign(Element.prototype, {
    scrollIntoView(this: Element) {
      scrolled.push(this);
    },
  });
});
afterEach(() => {
  root.remove();
  vi.useRealTimers();
});

describe("revealBlock", () => {
  it("scrolls the block into view and flashes it for a moment", async () => {
    vi.useFakeTimers();
    const el = outer("b1");
    root.appendChild(el);
    await expect(revealBlock(root, "b1")).resolves.toBe(true);
    expect(scrolled).toEqual([el]);
    expect(el).toHaveAttribute("data-flash");
    vi.advanceTimersByTime(1600);
    expect(el).not.toHaveAttribute("data-flash");
  });

  it("waits for a block that is not there yet", async () => {
    const p = revealBlock(root, "late");
    const el = outer("late");
    root.appendChild(el);
    await expect(p).resolves.toBe(true);
    expect(scrolled).toEqual([el]);
  });

  it("unfolds a folded part first, then reveals the block", async () => {
    const el = outer("b2", { "data-folded": "true" });
    root.appendChild(el);
    const unfold = vi.fn(() => {
      // The editor redraws the part unfolded.
      queueMicrotask(() => el.removeAttribute("data-folded"));
    });
    await expect(revealBlock(root, "b2", { unfold })).resolves.toBe(true);
    expect(unfold).toHaveBeenCalledWith("b2");
    expect(unfold).toHaveBeenCalledTimes(1);
    expect(scrolled).toEqual([el]);
    expect(el).toHaveAttribute("data-flash");
  });

  it("gives up on a block that never shows", async () => {
    vi.useFakeTimers();
    const p = revealBlock(root, "never");
    vi.advanceTimersByTime(REVEAL_GIVE_UP_MS);
    await expect(p).resolves.toBe(false);
    expect(scrolled).toEqual([]);
  });

  it("stops when its signal aborts", async () => {
    const ctrl = new AbortController();
    const p = revealBlock(root, "x", { signal: ctrl.signal });
    ctrl.abort();
    await expect(p).resolves.toBe(false);
    root.appendChild(outer("x"));
    await Promise.resolve();
    expect(scrolled).toEqual([]);
  });
});

describe("unfoldInNote", () => {
  const b = (id: string, type = "paragraph"): NoteBlock => ({
    id,
    note_id: 5,
    parent_id: null,
    type: type as NoteBlock["type"],
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
  });

  it("unfolds the folded part holding the block, and leaves an open one alone", () => {
    useNotesStore.setState({
      notes: {
        5: {
          note: { id: 5, target_kind: "paper", target_id: "3", title: "", revision: 1, updated_at: "" },
          blocks: [b("a"), b("d1", "divider"), b("h"), b("d2", "divider"), b("z")],
          suggestions: [],
          comments: [],
          revision: 1,
        },
      },
      folded: { 5: ["d1"] },
    });
    unfoldInNote(5)("h");
    expect(useNotesStore.getState().folded[5]).toEqual([]);
    unfoldInNote(5)("z");
    expect(useNotesStore.getState().folded[5]).toEqual([]);
  });
});
