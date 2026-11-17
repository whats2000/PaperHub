import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

import { API_BASE_URL } from "@/lib/api";
import { markHint, queueMarkOp, resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, NoteBlock } from "@/types/notes";

const { toastFn, toastError } = vi.hoisted(() => ({ toastFn: vi.fn(), toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: toastError, success: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const MARK: Mark = {
  id: "mk_1",
  paper_content_id: 3,
  block_id: "b_q",
  kind: "highlight",
  color: "green",
  view: "html",
  pieces: [{ quote: "agent pose", chunk_start: 11, chunk_end: 11, prefix: "", suffix: "" }],
  lang: null,
  source_ref: null,
  section_key: "2 Method",
  status: "ok",
  created_at: "2026-11-17T00:00:00Z",
  deleted_at: null,
};

function block(id: string, type: string, props: Record<string, unknown> = {}): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: type as NoteBlock["type"],
    props,
    content: [],
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
  };
}

const note = {
  id: 7,
  target_kind: "paper" as const,
  target_id: "3",
  title: "Duo-Virtual Spaces",
  revision: 4,
  updated_at: "2026-11-17T00:00:00Z",
};

let noteLoads = 0;

beforeEach(() => {
  toastFn.mockReset();
  toastError.mockReset();
  noteLoads = 0;
  resetMarkOps();
  useMarksStore.setState({ marksByPaper: {}, chunksByPaper: {} });
  useNotesStore.setState({
    notes: {
      7: {
        note,
        blocks: [block("s2", "section"), block("p1", "paragraph"), block("d1", "divider"), block("p2", "paragraph")],
        suggestions: [],
        comments: [],
        revision: 4,
      },
    },
    byTarget: { "paper:3": 7 },
    quoteTarget: null,
    activePart: {},
  });
  server.use(
    http.get(`${API_BASE_URL}/notes/7`, () => {
      noteLoads += 1;
      return HttpResponse.json({ note, blocks: [], suggestions: [], comments: [] });
    }),
  );
});

describe("useMarksStore.create", () => {
  it("adds the mark, refreshes the open note and says where it went", async () => {
    const created: MarkCreated = {
      mark: MARK,
      block: null,
      saved_to: { note_id: 7, paper_title: "Duo-Virtual Spaces", section: "2 Method", part_index: 1, part_count: 2 },
    };
    let body: unknown = null;
    server.use(
      http.post(`${API_BASE_URL}/marks`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(created);
      }),
    );
    useNotesStore.setState({ quoteTarget: { noteId: 7, blockId: "p1" } });
    await useMarksStore.getState().create({
      paper_content_id: 3,
      kind: "highlight",
      color: "green",
      view: "html",
      pieces: MARK.pieces,
      section_key: "2 Method",
      hint_block_id: "p1",
    });
    expect(body).toMatchObject({ hint_block_id: "p1", section_key: "2 Method" });
    expect(useMarksStore.getState().marksByPaper[3]).toEqual([{ ...MARK, note_id: 7 }]);
    expect(toastFn).toHaveBeenCalledWith("Saved to Duo-Virtual Spaces › §2 · note 2 of 2", expect.anything());
    // "Quote from the paper" points at one quote: the next goes to the section.
    expect(useNotesStore.getState().quoteTarget).toBeNull();
    await waitFor(() => expect(noteLoads).toBe(1));
  });
});

describe("useMarksStore.remove", () => {
  it("deletes with Undo, and Undo offers Delete again", async () => {
    const calls: string[] = [];
    server.use(
      http.delete(`${API_BASE_URL}/marks/mk_1`, () => {
        calls.push("delete");
        return HttpResponse.json({ revision: 5, applied: ["b_q"], rejected: [], change_set_id: null });
      }),
      http.post(`${API_BASE_URL}/marks/mk_1/restore`, () => {
        calls.push("restore");
        return HttpResponse.json({ revision: 6, applied: ["b_q"], rejected: [], change_set_id: null });
      }),
    );
    useMarksStore.setState({ marksByPaper: { 3: [{ ...MARK, note_id: 7 }] } });
    await useMarksStore.getState().remove("mk_1");
    expect(useMarksStore.getState().marksByPaper[3]).toEqual([]);
    expect(toastFn.mock.lastCall?.[0]).toBe("Mark deleted");

    const undo = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(undo.label).toBe("Undo");
    undo.onClick();
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]).toHaveLength(1));
    expect(toastFn).toHaveBeenLastCalledWith("Mark restored", expect.anything());

    const again = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(again.label).toBe("Delete again");
    again.onClick();
    await waitFor(() => expect(useMarksStore.getState().marksByPaper[3]).toEqual([]));
    expect(calls).toEqual(["delete", "restore", "delete"]);
  });
});

describe("markHint", () => {
  it("prefers the quote target, then the end of the divider part the caret is in", () => {
    expect(markHint(3)).toBeNull();
    // A section heading is where Follow points too: no hint.
    useNotesStore.setState({ activePart: { 7: "s2" } });
    expect(markHint(3)).toBeNull();
    useNotesStore.setState({ activePart: { 7: "d1" } });
    expect(markHint(3)).toBe("p2");
    useNotesStore.setState({ quoteTarget: { noteId: 7, blockId: "p1" } });
    expect(markHint(3)).toBe("p1");
  });
});

describe("the highlighter (pen)", () => {
  it("turns the pen and Box off each other, keeps its colour, and ends the session when off", () => {
    const s = () => useMarksStore.getState();
    s().setBoxOn(true);
    s().setPen(true, "blue");
    expect(s()).toMatchObject({ penOn: true, penColor: "blue", boxOn: false });
    s().notePenMark({ id: "mk_1", color: "blue", places: ["h:1"] });
    expect(s().penSession).toHaveLength(1);
    s().setBoxOn(true);
    expect(s()).toMatchObject({ penOn: false, boxOn: true, penColor: "blue", penSession: [] });
    // Back on: the colour chosen last.
    s().setPen(true);
    expect(s()).toMatchObject({ penOn: true, penColor: "blue", boxOn: false });
    s().setPen(false);
    expect(s().penSession).toEqual([]);
  });
});

describe("useMarksStore.erase", () => {
  const TWO: Mark = {
    ...MARK,
    note_id: 7,
    pieces: [
      { quote: "agent pose", chunk_start: 11, chunk_end: 11, prefix: "", suffix: "" },
      { quote: "of time", chunk_start: 11, chunk_end: 11, prefix: "", suffix: "" },
    ],
  };

  it("erases one piece and keeps the rest, with Undo, then Erase again", async () => {
    const calls: string[] = [];
    const erased = { ...TWO, pieces: [TWO.pieces[0], { ...TWO.pieces[1], erased: true }] };
    server.use(
      http.delete(`${API_BASE_URL}/marks/mk_1/pieces/1`, () => {
        calls.push("erase");
        return HttpResponse.json({ ...erased, note_id: undefined });
      }),
      http.post(`${API_BASE_URL}/marks/mk_1/pieces/1/restore`, () => {
        calls.push("restore");
        return HttpResponse.json({ ...TWO, note_id: undefined });
      }),
    );
    useMarksStore.setState({ marksByPaper: { 3: [TWO] } });
    await useMarksStore.getState().erase("mk_1", 1);
    const after = useMarksStore.getState().marksByPaper[3]![0]!;
    expect(after.pieces[0]!.erased).toBeUndefined();
    expect(after.pieces[1]!.erased).toBe(true);
    expect(after.note_id).toBe(7);
    expect(toastFn.mock.lastCall?.[0]).toBe("Piece erased");

    const undo = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(undo.label).toBe("Undo");
    undo.onClick();
    await waitFor(() => expect(toastFn).toHaveBeenLastCalledWith("Piece restored", expect.anything()));
    expect(useMarksStore.getState().marksByPaper[3]![0]!.pieces[1]!.erased).toBeUndefined();

    const again = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(again.label).toBe("Erase again");
    again.onClick();
    await waitFor(() => expect(calls).toEqual(["erase", "restore", "erase"]));
  });

  it("the server decides a mark left empty is deleted: Undo, then Delete again", async () => {
    const calls: string[] = [];
    server.use(
      http.delete(`${API_BASE_URL}/marks/mk_1/pieces/0`, () => {
        calls.push("erase");
        return HttpResponse.json({ ...MARK, pieces: [{ ...MARK.pieces[0], erased: true }], deleted_at: "2026-11-17T01:00:00Z" });
      }),
      http.post(`${API_BASE_URL}/marks/mk_1/pieces/0/restore`, () => {
        calls.push("restore");
        return HttpResponse.json(MARK);
      }),
    );
    useMarksStore.setState({ marksByPaper: { 3: [{ ...MARK, note_id: 7 }] } });
    await useMarksStore.getState().erase("mk_1", 0);
    expect(calls).toEqual(["erase"]);
    expect(useMarksStore.getState().marksByPaper[3]).toEqual([]);
    expect(toastFn.mock.lastCall?.[0]).toBe("Mark deleted");
    const undo = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    undo.onClick();
    await waitFor(() => expect(toastFn).toHaveBeenLastCalledWith("Mark restored", expect.anything()));
    expect(useMarksStore.getState().marksByPaper[3]).toMatchObject([{ id: "mk_1", note_id: 7 }]);
    const again = (toastFn.mock.lastCall![1] as { action: { label: string; onClick: () => void } }).action;
    expect(again.label).toBe("Delete again");
    again.onClick();
    await waitFor(() => expect(calls).toEqual(["erase", "restore", "erase"]));
  });

  it("waits for a pending stroke: the erase sees the joined mark, not the copy from before", async () => {
    let finishJoin!: () => void;
    const joined: Mark = {
      ...MARK,
      note_id: 7,
      pieces: [MARK.pieces[0]!, { quote: "of time", chunk_start: 11, chunk_end: 11, prefix: "", suffix: "" }],
    };
    const calls: string[] = [];
    server.use(
      http.delete(`${API_BASE_URL}/marks/mk_1/pieces/0`, () => {
        calls.push("erase");
        return HttpResponse.json({ ...joined, pieces: [{ ...joined.pieces[0], erased: true }, joined.pieces[1]] });
      }),
    );
    useMarksStore.setState({ marksByPaper: { 3: [{ ...MARK, note_id: 7 }] } });
    // A stroke joining a second piece is still saving.
    const join = queueMarkOp(
      () =>
        new Promise<void>((resolve) => {
          finishJoin = () => {
            useMarksStore.setState({ marksByPaper: { 3: [joined] } });
            calls.push("join");
            resolve();
          };
        }),
    );
    const erase = useMarksStore.getState().erase("mk_1", 0);
    await new Promise((r) => setTimeout(r, 10));
    expect(calls).toEqual([]);
    finishJoin();
    await Promise.all([join, erase]);
    expect(calls).toEqual(["join", "erase"]);
    // The joined piece stays: only the first was erased.
    const after = useMarksStore.getState().marksByPaper[3]!;
    expect(after).toHaveLength(1);
    expect(after[0]!.pieces[1]!.erased).toBeUndefined();
    expect(toastFn.mock.lastCall?.[0]).toBe("Piece erased");
  });
});

describe("useMarksStore.split", () => {
  it("makes the newest piece its own mark", async () => {
    const two: Mark = {
      ...MARK,
      note_id: 7,
      pieces: [MARK.pieces[0]!, { quote: "of time", chunk_start: 11, chunk_end: 11, prefix: "", suffix: "" }],
    };
    const split: Mark = { ...MARK, id: "mk_2", block_id: "b_2", pieces: [two.pieces[1]!] };
    // The server's original, as it now is: the piece it moved is flagged.
    const original: Mark = { ...two, note_id: undefined, pieces: [two.pieces[0]!, { ...two.pieces[1]!, removed: true }] };
    server.use(http.post(`${API_BASE_URL}/marks/mk_1/split`, () => HttpResponse.json({ ...split, original })));
    // The client's copy is stale (a third piece it never heard of is not
    // guessed at): the store takes the server's.
    useMarksStore.setState({ marksByPaper: { 3: [{ ...two, pieces: [two.pieces[0]!] }] } });
    await useMarksStore.getState().split("mk_1");
    const list = useMarksStore.getState().marksByPaper[3]!;
    expect(list.map((m) => m.id)).toEqual(["mk_1", "mk_2"]);
    expect(list[0]!.pieces).toHaveLength(2);
    expect(list[0]!.pieces[1]!.removed).toBe(true);
    expect(list[0]!.note_id).toBe(7);
    expect(list[1]).not.toHaveProperty("original");
    expect(list[1]).toMatchObject({ id: "mk_2", note_id: 7 });
  });
});
