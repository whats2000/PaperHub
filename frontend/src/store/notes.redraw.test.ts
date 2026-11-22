import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, followRedraws, onPaperMarksChanged, useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteBlock } from "@/types/notes";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();

const note = (id: number) => ({
  id,
  target_kind: "session" as const,
  target_id: String(id),
  title: `N${id}`,
  revision: 4,
  updated_at: "2026-11-16T00:00:00Z",
});

function block(id: string, noteId: number, color: string): NoteBlock {
  return {
    id,
    note_id: noteId,
    parent_id: null,
    type: "paperQuote",
    props: { markId: "mk_1", color, kind: "text", style: "highlight" },
    content: null,
    text: "q",
    author: "ai",
    section_key: null,
    changed_rev: 1,
  };
}

const seed = (id: number, color: string) => ({ note: note(id), blocks: [block(`q${id}`, id, color)], suggestions: [], comments: [], revision: 4 });

/** An accept that recoloured mk_1 and redrew its copy in note 8. */
const accepted: ApplyResult = {
  note_id: 7,
  revision: 5,
  change_set_id: "cs_5",
  applied: ["q7"],
  suggested: [],
  rejected: [],
  reparented: [],
  marks: [{ mark_id: "mk_1", paper_id: 3, block_id: "q7", color: { from: "green", to: "blue" }, blocks: ["q8"] }],
  redrawn: [{ note_id: 8, revision: 5, change_set_id: "cs_8", blocks: ["q8"] }],
};

const loads: number[] = [];

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
});
beforeEach(() => {
  loads.length = 0;
  useNotesStore.setState({
    notes: { 7: seed(7, "green"), 8: seed(8, "green") },
    byTarget: {},
    reloads: {},
    retrying: {},
    drafts: {},
  });
  for (const id of [7, 8]) {
    server.use(
      http.get(`${API_BASE_URL}/notes/${id}`, () => {
        loads.push(id);
        return HttpResponse.json({ note: { ...note(id), revision: 5 }, blocks: [block(`q${id}`, id, "blue")], suggestions: [], comments: [] });
      }),
    );
  }
});

describe("a mark recolour that redrew other notes", () => {
  it("an accept reloads the other open note and redraws the reader's paper", async () => {
    server.use(http.post(`${API_BASE_URL}/notes/suggestions/sg_1`, () => HttpResponse.json(accepted)));
    const papers: number[][] = [];
    const off = onPaperMarksChanged((ids) => papers.push([...ids]));
    await useNotesStore.getState().resolveSuggestion(7, "sg_1", true);
    off();
    await waitFor(() => expect(loads).toContain(8));
    await waitFor(() => expect(useNotesStore.getState().notes[8]!.blocks[0]!.props.color).toBe("blue"));
    expect(papers).toEqual([[3]]);
  });

  it("a saved edit whose answer lists another note reloads that note and its own", async () => {
    server.use(http.post(`${API_BASE_URL}/notes/7/ops`, () => HttpResponse.json(accepted)));
    useNotesStore.getState().applyLocal(7, [
      { op: "update", block_id: "q7", block: { type: "paperQuote", props: { markId: "mk_1", color: "blue" } } as never },
    ]);
    await useNotesStore.getState().flush(7);
    await waitFor(() => expect(loads).toEqual(expect.arrayContaining([7, 8])));
  });

  it("leaves a note that is not open alone", async () => {
    useNotesStore.setState({ notes: { 7: seed(7, "green") } });
    followRedraws(accepted, 7);
    await new Promise((r) => setTimeout(r, 20));
    expect(loads).toEqual([]);
  });
});
