import { afterEach, describe, expect, it } from "vitest";

import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

import {
  activePartFor,
  focusedMarkIds,
  marksInPart,
  partsOfNote,
  partsOfSection,
  sectionForHeading,
} from "./parts";

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

const s1 = blk("s1", "section", { props: { title: "1 Introduction" }, section_key: "1 Introduction" });
const s2 = blk("s2", "section", { props: { title: "2 Method" }, section_key: "2 Method" });
const q1 = blk("q1", "paperQuote", { props: { markId: "mk_1" } });
const q2 = blk("q2", "paperFigure", { props: { markId: "mk_2" } });
const q3 = blk("q3", "paperQuote", { props: { markId: "mk_3" } });
const p1 = blk("p1", "paragraph");
const h1 = blk("h1", "heading", { props: { level: 3 } });
const p2 = blk("p2", "paragraph");
const d1 = blk("d1", "divider");
const p3 = blk("p3", "paragraph");
const d2 = blk("d2", "divider");
const p4 = blk("p4", "paragraph");

const NOTE = [s1, p1, s2, q1, h1, p2, d1, q2, p3, q3, d2, p4];

const ids = (blocks: { id: string }[]) => blocks.map((b) => b.id);

afterEach(() => {
  useNotesStore.setState({ notes: {}, activePart: {}, focusOn: false });
});

describe("partsOfSection (the backend's parts_of_section rule)", () => {
  it("splits only at dividers; the heading is in the first part, a sub-heading stays inside its part", () => {
    const parts = partsOfSection(NOTE, "s2");
    expect(parts.map((p) => ids(p.blocks))).toEqual([
      ["s2", "q1", "h1", "p2"],
      ["q2", "p3", "q3"],
      ["p4"],
    ]);
    // A part is named by the block it follows: its section, or the divider above it.
    expect(parts.map((p) => p.id)).toEqual(["s2", "d1", "d2"]);
    expect(parts.map((p) => p.dividerId)).toEqual([null, "d1", "d2"]);
  });

  it("stops at the next section and keeps an empty part after a trailing divider", () => {
    expect(partsOfSection(NOTE, "s1").map((p) => ids(p.blocks))).toEqual([["s1", "p1"]]);
    const trailing = partsOfSection([s1, p1, d1, s2], "s1");
    expect(trailing.map((p) => ids(p.blocks))).toEqual([["s1", "p1"], []]);
  });

  it("is empty for a section that is not in the note", () => {
    expect(partsOfSection(NOTE, "nope")).toEqual([]);
  });
});

describe("partsOfNote", () => {
  it("lists every section's parts in order, plus the lines before the first section", () => {
    const parts = partsOfNote([p1, d1, p2, ...NOTE.slice(0, 2)]);
    expect(parts.map((p) => p.id)).toEqual(["start", "d1", "s1"]);
    expect(parts.map((p) => ids(p.blocks))).toEqual([["p1"], ["p2"], ["s1", "p1"]]);
  });

  it("leaves out an empty start", () => {
    expect(partsOfNote(NOTE).map((p) => p.id)).toEqual(["s1", "s2", "d1", "d2"]);
  });
});

describe("activePartFor and marksInPart", () => {
  it("names the part a block is in; a divider leads the part below it", () => {
    expect(activePartFor("q2", NOTE)).toBe("d1");
    expect(activePartFor("h1", NOTE)).toBe("s2");
    expect(activePartFor("d2", NOTE)).toBe("d2");
    expect(activePartFor("zz", NOTE)).toBeNull();
  });

  it("reads the loaded notes when no blocks are given", () => {
    useNotesStore.setState({
      notes: {
        7: {
          note: { id: 7, target_kind: "paper", target_id: "3", title: "T", revision: 1, updated_at: "" },
          blocks: NOTE,
          suggestions: [],
          comments: [],
          revision: 1,
        },
      },
    });
    expect(activePartFor("p3")).toBe("d1");
  });

  it("lists the mark ids quoted in a part, once each", () => {
    const parts = partsOfSection([s2, q1, q1, d1, q2, p3, q3], "s2");
    expect(parts.map(marksInPart)).toEqual([["mk_1"], ["mk_2", "mk_3"]]);
  });
});

describe("focusedMarkIds (the store selector the paper's Focus reads)", () => {
  const seed = (focusOn: boolean, active: string | null) =>
    useNotesStore.setState({
      notes: {
        7: {
          note: { id: 7, target_kind: "paper", target_id: "3", title: "T", revision: 1, updated_at: "" },
          blocks: NOTE,
          suggestions: [],
          comments: [],
          revision: 1,
        },
      },
      activePart: { 7: active },
      focusOn,
    });

  it("is null while Focus is off", () => {
    seed(false, "d1");
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toBeNull();
  });

  it("exposes the focused part's mark ids, the same array while nothing changes", () => {
    seed(true, "d1");
    const a = focusedMarkIds(useNotesStore.getState(), 7);
    expect(a).toEqual(["mk_2", "mk_3"]);
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toBe(a);
    seed(true, "s2");
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toEqual(["mk_1"]);
  });
});

describe("sectionForHeading (Follow)", () => {
  it("matches the reader's heading to a section block loosely, like the reader's own section search", () => {
    expect(sectionForHeading(NOTE, "2  Method")?.id).toBe("s2");
    expect(sectionForHeading(NOTE, "Method")?.id).toBe("s2");
    expect(sectionForHeading(NOTE, "1 Introduction")?.id).toBe("s1");
    expect(sectionForHeading(NOTE, "Related work")).toBeNull();
    expect(sectionForHeading(NOTE, null)).toBeNull();
  });

  it("falls back to the headings a subsection is in, innermost first", () => {
    expect(sectionForHeading(NOTE, "2.1 Details", ["2 Method"])?.id).toBe("s2");
    expect(sectionForHeading(NOTE, "Data", ["2 Method", "2.1 Details"])?.id).toBe("s2");
    expect(sectionForHeading(NOTE, "Data", ["9 Appendix"])).toBeNull();
  });
});

// [user, 2026-11-19] Marks add no headings: a note without them groups each
// run of one section's marks (and the lines under them) as a part.
describe("a note without section headings", () => {
  const mq = (id: string, section: string) =>
    blk(id, "paperQuote", { props: { markId: `mk_${id}`, source: { section } } });
  const intro = blk("t0", "paragraph");
  const a = mq("a", "1 Introduction");
  const mine = blk("m", "paragraph");
  const b = mq("b", "1 Introduction");
  const c = mq("c", "2 Method");
  const dv = blk("dv", "divider");
  const e = mq("e", "2 Method");
  const BARE = [intro, a, mine, b, c, dv, e];

  it("splits parts where the marked section changes, keeping lines under a mark with it", () => {
    expect(partsOfNote(BARE).map((p) => [p.id, p.blocks.map((x) => x.id)])).toEqual([
      ["start", ["t0"]],
      ["a", ["a", "m", "b"]],
      ["c", ["c"]],
      ["dv", ["e"]],
    ]);
    expect(partsOfNote(BARE)[1]!.sectionKey).toBe("1 Introduction");
    expect(activePartFor("m", BARE)).toBe("a");
  });

  it("Follow finds the first block of the section when there is no heading", () => {
    expect(sectionForHeading(BARE, "2 Method")?.id).toBe("c");
    expect(sectionForHeading(BARE, "Introduction")?.id).toBe("a");
    expect(sectionForHeading(BARE, "9 Appendix")).toBeNull();
  });

  it("keeps parts as before once the note has headings", () => {
    expect(partsOfNote([s1, a, c]).map((p) => p.id)).toEqual(["s1"]);
  });
});
