import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { useMarkNavStore } from "@/store/markNav";
import { resetMarkHistory, resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { clearPendingNoteOps, onKeepLine, useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, MarkIn, NoteBlock } from "@/types/notes";

import { useMarkSave } from "./useMarkSave";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const MARK: Mark = {
  id: "mk_1",
  paper_content_id: 3,
  block_id: "b_q",
  kind: "text",
  style: "highlight",
  color: "green",
  view: "html",
  pieces: [],
  lang: null,
  source_ref: null,
  section_key: null,
  status: "ok",
  created_at: "2026-11-21T00:00:00Z",
  deleted_at: null,
};

let sent: MarkIn[] = [];
let cite: MarkCreated["cite"] = null;

beforeEach(() => {
  sent = [];
  cite = null;
  resetMarkOps();
  resetMarkHistory();
  useMarksStore.setState({ marksByPaper: {} });
  useNotesPaneStore.setState({ open: false });
  useMarkNavStore.setState({ card: null });
  useNotesStore.setState({
    notes: {},
    byTarget: { "paper:3": 7 },
    quoteTarget: null,
    lastCaret: { 7: "p1" },
    lastCaretOffset: { 7: 5 },
    caretAfter: null,
  });
  server.use(
    http.post(`${API_BASE_URL}/marks`, async ({ request }) => {
      sent.push((await request.json()) as MarkIn);
      return HttpResponse.json({
        mark: MARK,
        block: null,
        saved_to: { note_id: 7, paper_title: "", section: null, part_index: 0, part_count: 1 },
        cite,
      } satisfies MarkCreated);
    }),
  );
});

async function save(quote: string, kind: "text" | "figure" = "text") {
  const { result } = renderHook(() => useMarkSave(3));
  await act(async () => {
    await result.current.save({
      kind,
      style: kind === "text" ? "highlight" : null,
      color: "green",
      view: "html",
      groups: () => [{ section: null, pieces: [{ quote }] }],
    });
  });
}

describe("a new mark goes to the note in the form its length asks for", () => {
  it("a short quote asks for an inline citation at the remembered caret", async () => {
    await save("62%");
    expect(sent[0]!.cite_at).toEqual({ block_id: "p1", offset: 5 });
  });

  it("counts user-perceived characters: 24 CJK characters are short, 25 are not", async () => {
    await save("中".repeat(24));
    await save("中".repeat(25));
    expect(sent[0]!.cite_at).toBeTruthy();
    expect(sent[1]!.cite_at).toBeUndefined();
  });

  it("a long quote and a figure are reference blocks", async () => {
    await save("a passage longer than twenty-four characters");
    await save("Fig. 1", "figure");
    expect(sent.map((b) => b.cite_at)).toEqual([undefined, undefined]);
  });

  it("with no caret this visit: no block, the server takes the last edited one", async () => {
    useNotesStore.setState({ lastCaret: {}, lastCaretOffset: {} });
    await save("62%");
    expect(sent[0]!.cite_at).toEqual({ block_id: null, offset: null });
  });

  it("puts the caret right after the chip, its alias field closed", async () => {
    cite = { block_id: "p1", offset: 6, new_block: false };
    await save("62%");
    expect(useNotesStore.getState().caretAfter).toMatchObject({ noteId: 7, blockId: "p1", offset: 6, in: "card" });
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_1" });
  });
});

describe("the new block in the notes pane [user, 2026-11-22]", () => {
  it("scrolls into view and flashes there when the pane shows (no card)", async () => {
    useNotesPaneStore.setState({ open: true, activeTab: "notes", request: null });
    await save("a passage longer than twenty-four characters");
    // The pane's request is "Open in notes": revealBlock scrolls + flashes.
    expect(useNotesPaneStore.getState().request).toMatchObject({ kind: "paper", id: 3, noteId: 7, blockId: "b_q" });
    expect(useMarkNavStore.getState().card).toBeNull();
  });

  it("an inline citation reveals the paragraph holding the chip", async () => {
    useNotesPaneStore.setState({ open: true, activeTab: "notes", request: null });
    cite = { block_id: "p2", offset: 3, new_block: false };
    await save("62%");
    expect(useNotesPaneStore.getState().request).toMatchObject({ noteId: 7, blockId: "p2" });
  });

  it("with the pane closed: no reveal, the note card opens", async () => {
    useNotesPaneStore.setState({ open: false, request: null });
    await save("a passage longer than twenty-four characters");
    expect(useNotesPaneStore.getState().request).toBeNull();
    expect(useMarkNavStore.getState().card).toMatchObject({ paperId: 3, markId: "mk_1" });
  });
});

describe("the caret on an empty line not saved yet [user, 2026-11-23]", () => {
  // The editor's part: asked to keep the caret's empty line, it sends it.
  let stop: () => void = () => undefined;
  const order: string[] = [];
  beforeEach(() => {
    order.length = 0;
    const p1: NoteBlock = {
      id: "p1", note_id: 7, parent_id: null, type: "paragraph", props: {}, content: [{ type: "text", text: "Mine", styles: {} }],
      text: "Mine", author: "user", section_key: null, changed_rev: 1,
    };
    useNotesStore.setState({
      notes: {
        7: {
          note: { id: 7, target_kind: "paper", target_id: "3", title: "", revision: 4, updated_at: "2026-11-23T00:00:00Z" },
          blocks: [p1],
          suggestions: [],
          comments: [],
          revision: 4,
        },
      },
      lastCaret: { 7: "e1" },
      lastCaretOffset: { 7: 0 },
    });
    stop = onKeepLine((noteId, blockId) => {
      if (noteId === 7 && blockId === "e1") {
        useNotesStore.getState().applyLocal(7, [
          { op: "insert", block_id: "e1", after: "p1", block: { type: "paragraph", props: {}, content: [] } },
        ]);
      }
    });
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as { ops: { block_id: string }[] };
        order.push(`ops ${body.ops.map((o) => o.block_id).join(",")}`);
        return HttpResponse.json({
          note_id: 7, revision: 5, change_set_id: null,
          applied: body.ops.map((o) => o.block_id), suggested: [], rejected: [], reparented: [],
        });
      }),
      http.post(`${API_BASE_URL}/marks`, async ({ request }) => {
        const body = (await request.json()) as MarkIn;
        sent.push(body);
        order.push("mark");
        return HttpResponse.json({
          mark: MARK, block: null,
          saved_to: { note_id: 7, paper_title: "", section: null, part_index: 0, part_count: 1 },
          cite,
        } satisfies MarkCreated);
      }),
      http.get(`${API_BASE_URL}/notes/7`, () => HttpResponse.json({ error: "not needed" }, { status: 500 })),
    );
  });
  afterEach(() => {
    stop();
    clearPendingNoteOps();
  });

  it("a long quote: the line is saved first, then named as the place", async () => {
    await save("a passage longer than twenty-four characters");
    expect(order).toEqual(["ops e1", "mark"]);
    expect(sent[0]!.hint_block_id).toBe("e1");
  });

  it("a short quote: the chip goes into the line", async () => {
    await save("62%");
    expect(order).toEqual(["ops e1", "mark"]);
    expect(sent[0]!.cite_at).toEqual({ block_id: "e1", offset: 0 });
  });
});
