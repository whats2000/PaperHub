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

  it("a saved edit that took a mark's last inline citation out redraws that paper's marks", async () => {
    // The server deleted the mark with its last chip (2026-11-22): the
    // reader must stop drawing it.
    const uncited: ApplyResult = {
      note_id: 7,
      revision: 5,
      change_set_id: "cs_5",
      applied: ["q7"],
      suggested: [],
      rejected: [],
      reparented: [],
      cited: [{ mark_id: "mk_2", paper_id: 4, deleted: true }],
    };
    server.use(http.post(`${API_BASE_URL}/notes/7/ops`, () => HttpResponse.json(uncited)));
    const papers: number[][] = [];
    const off = onPaperMarksChanged((ids) => papers.push([...ids]));
    useNotesStore.getState().applyLocal(7, [
      { op: "update", block_id: "q7", block: { type: "paragraph", props: {}, content: [] } as never },
    ]);
    await useNotesStore.getState().flush(7);
    off();
    expect(papers).toEqual([[4]]);
  });

  it("leaves a note that is not open alone", async () => {
    useNotesStore.setState({ notes: { 7: seed(7, "green") } });
    followRedraws(accepted, 7);
    await new Promise((r) => setTimeout(r, 20));
    expect(loads).toEqual([]);
  });
});

describe("the reload after a saved recolour (Task 16, round 2)", () => {
  const para = (text: string, rev: number): NoteBlock => ({
    id: "p1",
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: [{ type: "text", text, styles: {} }],
    text,
    author: "user",
    section_key: null,
    changed_rev: rev,
  });
  const upd = (text: string) => ({
    op: "update" as const,
    block_id: "p1",
    block: { type: "paragraph" as const, props: {}, content: [{ type: "text", text, styles: {} }] },
  });
  const gate = () => {
    let open!: () => void;
    const p = new Promise<void>((r) => (open = r));
    return { p, open };
  };

  it("never drops an edit saved while it was loading", async () => {
    let serverText = "A";
    let revision = 4;
    let posts = 0;
    const getGate = gate();
    const secondPost = gate();
    useNotesStore.setState({
      notes: { 7: { note: note(7), blocks: [para("A", 1)], suggestions: [], comments: [], revision: 4 } },
    });
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        posts += 1;
        const body = (await request.json()) as { ops: { block: { content: { text: string }[] } }[] };
        if (posts === 2) await secondPost.p;
        serverText = body.ops.at(-1)!.block.content[0]!.text;
        revision += 1;
        // The first save recoloured a mark: the client reloads the note.
        return HttpResponse.json({ ...accepted, revision, applied: ["p1"], redrawn: [], ...(posts === 1 ? {} : { marks: [] }) });
      }),
      http.get(`${API_BASE_URL}/notes/7`, async () => {
        const snapshot = { text: serverText, revision };
        await getGate.p;
        return HttpResponse.json({ note: { ...note(7), revision: snapshot.revision }, blocks: [para(snapshot.text, snapshot.revision)], suggestions: [], comments: [] });
      }),
    );
    const store = useNotesStore.getState();
    store.applyLocal(7, [upd("A1")]);
    await store.flush(7); // saved; its reload is loading (held)
    store.applyLocal(7, [upd("A1 B")]);
    const second = store.flush(7); // in flight while the reload lands
    await new Promise((r) => setTimeout(r, 20));
    getGate.open();
    await new Promise((r) => setTimeout(r, 20));
    const text = () => useNotesStore.getState().notes[7]!.blocks[0]!.text;
    expect(text()).toBe("A1 B");
    secondPost.open();
    await second;
    await waitFor(() => expect(text()).toBe("A1 B"));
  });
});

describe("a reload a burst overtook (Task 16, round 3)", () => {
  const para = (text: string, rev: number): NoteBlock => ({
    id: "p1",
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: [{ type: "text", text, styles: {} }],
    text,
    author: "user",
    section_key: null,
    changed_rev: rev,
  });
  const upd = (text: string) => ({
    op: "update" as const,
    block_id: "p1",
    block: { type: "paragraph" as const, props: {}, content: [{ type: "text", text, styles: {} }] },
  });

  it("runs again when the burst's answer came back before the reload's", async () => {
    let serverText = "A";
    let revision = 4;
    let posts = 0;
    let gets = 0;
    let openGet!: () => void;
    const firstGet = new Promise<void>((r) => (openGet = r));
    useNotesStore.setState({
      notes: { 7: { note: note(7), blocks: [para("A", 1)], suggestions: [], comments: [], revision: 4 } },
    });
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        posts += 1;
        const body = (await request.json()) as { ops: { block: { content: { text: string }[] } }[] };
        serverText = body.ops.at(-1)!.block.content[0]!.text;
        revision += 1;
        return HttpResponse.json({ ...accepted, revision, applied: ["p1"], redrawn: [], ...(posts === 1 ? {} : { marks: [] }) });
      }),
      http.get(`${API_BASE_URL}/notes/7`, async () => {
        gets += 1;
        const snapshot = { text: serverText, revision };
        if (gets === 1) await firstGet;
        return HttpResponse.json({ note: { ...note(7), revision: snapshot.revision }, blocks: [para(snapshot.text, snapshot.revision)], suggestions: [], comments: [] });
      }),
    );
    const store = useNotesStore.getState();
    store.applyLocal(7, [upd("A1")]);
    await store.flush(7); // saved; its reload is loading (held)
    store.applyLocal(7, [upd("A1 B")]);
    await store.flush(7); // the next burst is answered first
    openGet(); // then the stale reload lands: dropped, and run again
    await waitFor(() => expect(gets).toBe(2));
    await waitFor(() => expect(useNotesStore.getState().notes[7]!.revision).toBe(revision));
    expect(useNotesStore.getState().notes[7]!.blocks[0]!.text).toBe("A1 B");
  });
});
