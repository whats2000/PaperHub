import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCanvasStore } from "@/store/canvas";
import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

import { useFollowReading } from "./useFollowReading";

function blk(id: string, type: string, props: Record<string, unknown> = {}): NoteBlock {
  return {
    id, note_id: 7, parent_id: null, type: type as NoteBlock["type"], props,
    content: null, text: "", author: "user", section_key: null, changed_rev: 1,
  };
}

afterEach(() => {
  useCanvasStore.setState({ readingSection: null });
  useNotesStore.setState({ notes: {}, followOn: false, activePart: {} });
});

describe("useFollowReading without section headings", () => {
  it("scrolls the section's first block to the top and makes its part active", () => {
    const blocks = [
      blk("a", "paperQuote", { markId: "mk_a", source: { section: "1 Intro" } }),
      blk("c", "paperQuote", { markId: "mk_c", source: { section: "2 Method" } }),
    ];
    useNotesStore.setState({
      notes: { 7: { note: {} as never, blocks, suggestions: [], comments: [], revision: 1 } },
      followOn: true, focusOn: false, activePart: {},
    });
    useCanvasStore.setState({ readingSection: { paperId: 3, heading: "2 Method", within: [] } });
    const root = document.createElement("div");
    const scrolled: string[] = [];
    for (const id of ["a", "c"]) {
      const el = document.createElement("div");
      el.className = "bn-block-outer";
      el.dataset.id = id;
      el.scrollIntoView = vi.fn(() => scrolled.push(id));
      root.appendChild(el);
    }
    renderHook(() => useFollowReading({ noteId: 7, paperId: 3, root }));
    expect(scrolled).toEqual(["c"]);
    expect(useNotesStore.getState().activePart[7]).toBe("c");
  });
});
