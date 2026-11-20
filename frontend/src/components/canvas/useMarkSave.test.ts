import { act, renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { useMarkNavStore } from "@/store/markNav";
import { resetMarkHistory, resetMarkOps, useMarksStore } from "@/store/marks";
import { useNotesPaneStore } from "@/store/notesPane";
import { useNotesStore } from "@/store/notes";
import type { Mark, MarkCreated, MarkIn } from "@/types/notes";

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

  it("with no caret in the note yet: a new paragraph at its end", async () => {
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
