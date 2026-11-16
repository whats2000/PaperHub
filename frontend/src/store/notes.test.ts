import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { toast } from "sonner";

import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, pendingNoteOps, useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteBlock, NoteOpsBody } from "@/types/notes";

const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
const posted: NoteOpsBody[] = [];

function block(id: string, text: string, rev = 1): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: [{ type: "text", text, styles: {} }],
    text,
    author: "user",
    section_key: null,
    changed_rev: rev,
  };
}

const note = {
  id: 7,
  target_kind: "paper" as const,
  target_id: "3",
  title: "Attention",
  revision: 4,
  updated_at: "2026-11-16T00:00:00Z",
};

function result(revision: number, applied: string[], rejected: ApplyResult["rejected"] = []): ApplyResult {
  return {
    note_id: 7,
    revision,
    change_set_id: rejected.length && !applied.length ? null : `cs_${revision}`,
    applied,
    suggested: [],
    rejected,
    reparented: [],
  };
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
});
beforeEach(() => {
  posted.length = 0;
  toastFn.mockReset();
  useNotesStore.setState({ notes: {}, byTarget: {}, reloads: {}, retrying: {}, drafts: {} });
  server.use(
    http.get(`${API_BASE_URL}/notes/target`, () =>
      HttpResponse.json({ note, blocks: [block("a", "A"), block("b", "B")] }),
    ),
    http.get(`${API_BASE_URL}/notes/7`, () =>
      HttpResponse.json({ note, blocks: [block("a", "A"), block("b", "B")], suggestions: [], comments: [] }),
    ),
  );
});

function upd(id: string, text: string) {
  return {
    op: "update" as const,
    block_id: id,
    block: { type: "paragraph" as const, props: {}, content: [{ type: "text", text, styles: {} }] },
  };
}

describe("useNotesStore", () => {
  it("keeps how many chats share the note, from the target and from a reload", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () => HttpResponse.json({ note, blocks: [], shared_by: 2 })),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note, blocks: [], suggestions: [], comments: [], shared_by: 3 }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    expect(useNotesStore.getState().notes[7]?.sharedBy).toBe(2);
    await useNotesStore.getState().loadNote(7);
    expect(useNotesStore.getState().notes[7]?.sharedBy).toBe(3);
  });

  it("opens a note with its open suggestions and comment threads", async () => {
    const sug = { id: "sg_1", block_id: "a", change_set_id: "cs_1", kind: "update", proposed: null, status: "open", created_at: "x" };
    const thread = { id: "c_1", note_id: 7, block_id: "a", quote: "A", status: "open", created_at: "x", items: [] };
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () =>
        HttpResponse.json({ note, blocks: [block("a", "A")], shared_by: 1, suggestions: [sug], comments: [thread] }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    expect(useNotesStore.getState().notes[7]?.suggestions).toEqual([sug]);
    expect(useNotesStore.getState().notes[7]?.comments).toEqual([thread]);
  });

  it("decides a suggestion after sending queued edits, then shows the saved note", async () => {
    const order: string[] = [];
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        posted.push((await request.json()) as NoteOpsBody);
        order.push("ops");
        return HttpResponse.json(result(5, ["b"]));
      }),
      http.post(`${API_BASE_URL}/notes/suggestions/sg_1`, async ({ request }) => {
        order.push(`decide:${JSON.stringify(await request.json())}`);
        return HttpResponse.json(result(6, ["a"]));
      }),
      http.get(`${API_BASE_URL}/notes/7`, () => {
        order.push("reload");
        return HttpResponse.json({ note: { ...note, revision: 6 }, blocks: [block("a", "A2", 6), block("b", "B")], suggestions: [], comments: [] });
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("b", "B!")]);
    const res = await useNotesStore.getState().resolveSuggestion(7, "sg_1", true);
    expect(res.applied).toEqual(["a"]);
    expect(order).toEqual(["ops", 'decide:{"accept":true}', "reload"]);
    const st = useNotesStore.getState().notes[7]!;
    expect(st.blocks.find((b) => b.id === "a")?.text).toBe("A2");
    expect(st.suggestions).toEqual([]);
    expect(st.revision).toBe(6);
  });

  it("loads a target's note and remembers it by target", async () => {
    const id = await useNotesStore.getState().loadTarget("paper", 3);
    expect(id).toBe(7);
    const s = useNotesStore.getState();
    expect(s.byTarget["paper:3"]).toBe(7);
    expect(s.notes[7]?.revision).toBe(4);
    expect(s.notes[7]?.blocks.map((b) => b.id)).toEqual(["a", "b"]);
  });

  it("batches a burst into one call and takes the new revision as its base", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        const rev = body.base_revision + 1;
        return HttpResponse.json(result(rev, body.ops.map((o) => o.block_id ?? "")));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const { applyLocal } = useNotesStore.getState();
    applyLocal(7, [upd("a", "A1")]);
    applyLocal(7, [upd("b", "B1")]);
    applyLocal(7, [upd("a", "A2")]);
    // Applied locally at once.
    expect(useNotesStore.getState().notes[7]?.blocks[0]?.text).toBe("A2");
    await waitFor(() => expect(posted).toHaveLength(1), { timeout: 2000 });
    expect(posted[0]?.base_revision).toBe(4);
    // Two updates of one block in a burst collapse into the last one.
    expect(posted[0]?.ops.map((o) => o.block_id)).toEqual(["a", "b"]);
    expect(posted[0]?.ops[0]?.block?.content).toEqual([{ type: "text", text: "A2", styles: {} }]);
    await waitFor(() => expect(useNotesStore.getState().notes[7]?.revision).toBe(5));

    applyLocal(7, [upd("b", "B2")]);
    await waitFor(() => expect(posted).toHaveLength(2), { timeout: 2000 });
    expect(posted[1]?.base_revision).toBe(5);
  });

  it("replaces a conflicted block with the current one and says so", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(
          result(5, [], [{ block_id: "a", reason: "changed_since_base", current: block("a", "Theirs", 5) }]),
        ),
      ),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 5 },
          blocks: [block("a", "Theirs", 5), block("b", "B")],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    useNotesStore.getState().applyLocal(7, [upd("a", "Mine")]);
    await useNotesStore.getState().flush(7);
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks[0]?.text).toBe("Theirs");
    expect(s.notes[7]?.revision).toBe(5);
    // Any changed-since-base conflict reloads the whole note.
    expect(s.reloads[7]).toEqual({ seq: seq + 1, blockIds: null });
    expect(toastFn).toHaveBeenCalledWith("Updated from another view");
  });

  it("picks up a reorder by another view when a conflict comes back", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(
          result(5, [], [{ block_id: "a", reason: "changed_since_base", current: block("a", "A", 5) }]),
        ),
      ),
      // The other view moved "a" below "b" under the same parent.
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 5 },
          blocks: [block("b", "B"), block("a", "A", 5)],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "Mine")]);
    await useNotesStore.getState().flush(7);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.id)).toEqual(["b", "a"]);
  });

  it("retries a burst whose response was lost under the same key, then sends later typing", async () => {
    // A server that saves the first try but whose response never arrives.
    const seen = new Map<string, ApplyResult>();
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        const key = body.idempotency_key ?? "";
        const stored = seen.get(key);
        if (stored) return HttpResponse.json(stored);
        const res = result(body.base_revision + 1, body.ops.map((o) => o.block_id ?? ""));
        seen.set(key, res);
        if (posted.length === 1) {
          useNotesStore.getState().applyLocal(7, [upd("a", "A typed later")]);
          return new HttpResponse(null, { status: 502 });
        }
        return HttpResponse.json(res);
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "new" }, parent_id: null, after: "a" },
    ]);
    await useNotesStore.getState().flush(7);
    await waitFor(() => expect(posted).toHaveLength(3), { timeout: 3000 });
    // The retry is the same burst under the same key and base.
    expect(posted[1]).toEqual(posted[0]);
    expect(posted[0]?.idempotency_key).toEqual(expect.any(String));
    // The later typing goes in its own burst, on the first burst's revision.
    expect(posted[2]?.ops).toEqual([upd("a", "A typed later")]);
    expect(posted[2]?.base_revision).toBe(5);
    expect(posted[2]?.idempotency_key).not.toBe(posted[0]?.idempotency_key);
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks.map((b) => b.text)).toEqual(["A typed later", "new", "B"]);
    expect(s.notes[7]?.blocks.filter((b) => b.id === "n")).toHaveLength(1);
  });

  it("re-sends a new block whose anchor vanished as a fresh insert with all its typing", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        if (posted.length === 1) {
          useNotesStore.getState().applyLocal(7, [upd("n", "hello world")]);
          // Another view deleted "b": neither op of the burst can land.
          return HttpResponse.json(
            result(4, [], [
              { block_id: "n", reason: "missing", current: null },
              { block_id: "n", reason: "missing", current: null },
            ]),
          );
        }
        return HttpResponse.json(result(5, body.ops.map((o) => o.block_id ?? "")));
      }),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note, blocks: [block("a", "A")], suggestions: [], comments: [] }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    // Typed in the same burst as the insert.
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "h" }, parent_id: null, after: "b" },
    ]);
    useNotesStore.getState().applyLocal(7, [upd("n", "hello")]);
    await useNotesStore.getState().flush(7);
    expect(posted[0]?.ops.map((o) => o.op)).toEqual(["insert", "update"]);
    // One fresh insert carrying everything typed so far, placed after the
    // nearest previous block the server still has; the stale ops are gone.
    expect(pendingNoteOps(7)).toEqual([
      {
        op: "insert",
        block_id: "n",
        block: { type: "paragraph", props: {}, content: [{ type: "text", text: "hello world", styles: {} }] },
        parent_id: null,
        after: "a",
      },
    ]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "hello world"]);
    const fresh = pendingNoteOps(7);
    await useNotesStore.getState().flush(7);
    expect(posted).toHaveLength(2);
    expect(posted[1]?.ops).toEqual(fresh);
  });

  it("places a new block after an anchor another view moved, in one retry", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        if (posted.length === 1) {
          // "b" now sits under "a": an insert after "b" at the top level is invalid.
          return HttpResponse.json(result(4, [], [{ block_id: "n", reason: "invalid", current: null }]));
        }
        return HttpResponse.json(result(5, body.ops.map((o) => o.block_id ?? "")));
      }),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note,
          blocks: [block("a", "A"), { ...block("b", "B"), parent_id: "a" }],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "mine" }, parent_id: null, after: "b" },
    ]);
    await useNotesStore.getState().flush(7);
    await useNotesStore.getState().flush(7);
    expect(posted).toHaveLength(2);
    expect(posted[1]?.ops).toEqual([
      expect.objectContaining({ op: "insert", block_id: "n", parent_id: "a", after: "b" }),
    ]);
    expect(pendingNoteOps(7)).toEqual([]);
  });

  it("keeps a new block refused a third time as an unsaved draft", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        return HttpResponse.json(result(4, [], [{ block_id: "n", reason: "invalid", current: null }]));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "mine" }, parent_id: null, after: "b" },
    ]);
    await useNotesStore.getState().flush(7);
    await useNotesStore.getState().flush(7);
    await useNotesStore.getState().flush(7);
    expect(posted).toHaveLength(3);
    expect(toastFn).toHaveBeenCalledWith(
      "A new line could not be saved. It stays here, marked Not saved; edit it to try again.",
    );
    const s = useNotesStore.getState();
    // Still shown after the reload, held as a draft, nothing queued for it.
    expect(s.notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "B", "mine"]);
    expect(Object.keys(s.drafts[7] ?? {})).toEqual(["n"]);
    expect(pendingNoteOps(7)).toEqual([]);
    // A later reload keeps it.
    await useNotesStore.getState().loadNote(7);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "B", "mine"]);
    expect(posted).toHaveLength(3);
  });

  it("keeps a new block's text as a draft when the server refuses its burst (4xx)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () => new HttpResponse("bad op", { status: 422 })),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "h" }, parent_id: null, after: "a" },
      upd("n", "hello"),
      upd("a", "A edited"),
    ]);
    await useNotesStore.getState().flush(7);
    const s = useNotesStore.getState();
    // The saved version of "a" comes back; the new line stays as a draft.
    expect(s.notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "hello", "B"]);
    expect(Object.keys(s.drafts[7] ?? {})).toEqual(["n"]);
    expect(pendingNoteOps(7)).toEqual([]);
    expect(toastFn).toHaveBeenCalledWith("New lines that could not be saved stay here, marked Not saved.");
  });

  it("tries to save a draft once more when it is edited, and drops it when deleted", () => {
    useNotesStore.setState({
      notes: {
        7: { note, blocks: [block("a", "A"), block("n", "draft")], suggestions: [], comments: [], revision: 4 },
      },
      drafts: {
        7: {
          n: {
            op: "insert",
            block_id: "n",
            block: { type: "paragraph", props: {}, content: [{ type: "text", text: "draft", styles: {} }] },
            parent_id: null,
            after: "a",
          },
        },
      },
    });
    useNotesStore.getState().applyLocal(7, [upd("n", "draft, edited")]);
    expect(useNotesStore.getState().drafts[7]).toEqual({});
    expect(pendingNoteOps(7)).toEqual([
      {
        op: "insert",
        block_id: "n",
        block: { type: "paragraph", props: {}, content: [{ type: "text", text: "draft, edited", styles: {} }] },
        parent_id: null,
        after: "a",
      },
    ]);

    clearPendingNoteOps();
    useNotesStore.setState({
      drafts: { 7: { n: { op: "insert", block_id: "n", block: { type: "paragraph", props: {} }, parent_id: null } } },
    });
    useNotesStore.getState().applyLocal(7, [{ op: "delete", block_id: "n" }]);
    expect(useNotesStore.getState().drafts[7]).toEqual({});
    expect(pendingNoteOps(7)).toEqual([]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.id)).toEqual(["a"]);
  });

  it("makes an edited draft a draft again when its one more try is refused", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        posted.push((await request.json()) as NoteOpsBody);
        return HttpResponse.json(result(4, [], [{ block_id: "n", reason: "invalid", current: null }]));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.setState((s) => ({
      notes: { 7: { ...s.notes[7]!, blocks: [block("a", "A"), block("n", "draft"), block("b", "B")] } },
      drafts: { 7: { n: { op: "insert", block_id: "n", block: { type: "paragraph", props: {} }, parent_id: null, after: "a" } } },
    }));
    useNotesStore.getState().applyLocal(7, [upd("n", "draft, edited")]);
    await useNotesStore.getState().flush(7);
    expect(posted).toHaveLength(1);
    expect(Object.keys(useNotesStore.getState().drafts[7] ?? {})).toEqual(["n"]);
    expect(pendingNoteOps(7)).toEqual([]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "draft, edited", "B"]);
  });

  it("starts a saved draft's refusal count afresh", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        // The edited draft saves; a later insert of the same block (undo of
        // a delete) is refused once.
        if (posted.length === 2) {
          return HttpResponse.json(result(6, [], [{ block_id: "n", reason: "missing", current: null }]));
        }
        return HttpResponse.json(result(body.base_revision + 1, body.ops.map((o) => o.block_id ?? "")));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.setState((s) => ({
      notes: { 7: { ...s.notes[7]!, blocks: [block("a", "A"), block("n", "draft"), block("b", "B")] } },
      drafts: { 7: { n: { op: "insert", block_id: "n", block: { type: "paragraph", props: {} }, parent_id: null, after: "a" } } },
    }));
    useNotesStore.getState().applyLocal(7, [upd("n", "saved now")]);
    await useNotesStore.getState().flush(7);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "again" }, parent_id: null, after: "a" },
    ]);
    await useNotesStore.getState().flush(7);
    // Its first refusal since it was saved: placed and resent, not drafted.
    expect(useNotesStore.getState().drafts[7]).toEqual({});
    expect(pendingNoteOps(7)).toEqual([expect.objectContaining({ op: "insert", block_id: "n", after: "a" })]);
  });

  it("saves a new block at the end of the note when its insert is refused twice", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        if (posted.length <= 2) {
          return HttpResponse.json(result(4, [], [{ block_id: "n", reason: "invalid", current: null }]));
        }
        return HttpResponse.json(result(5, body.ops.map((o) => o.block_id ?? "")));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [
      { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "mine" }, parent_id: null, after: "b" },
    ]);
    await useNotesStore.getState().flush(7);
    await useNotesStore.getState().flush(7);
    expect(toastFn).toHaveBeenCalledWith(
      "A new line could not go where you put it, so it was saved at the end of the note.",
    );
    await useNotesStore.getState().flush(7);
    expect(posted).toHaveLength(3);
    expect(posted[2]?.ops).toEqual([
      {
        op: "insert",
        block_id: "n",
        block: { type: "paragraph", props: {}, content: "mine" },
        parent_id: null,
      },
    ]);
    expect(pendingNoteOps(7)).toEqual([]);
  });

  it("shows the saved version when the server refuses a burst (4xx)", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () => new HttpResponse("bad op", { status: 422 })),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    useNotesStore.getState().applyLocal(7, [upd("a", "Lost")]);
    await useNotesStore.getState().flush(7);
    expect(toast.error).toHaveBeenCalledWith("Couldn't save that change. Showing the saved version.");
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks[0]?.text).toBe("A");
    expect(s.reloads[7]).toEqual({ seq: seq + 1, blockIds: null });
  });

  it("keeps and retries everything when a burst fails (5xx), then sends it all", async () => {
    let calls = 0;
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        calls += 1;
        if (calls === 1) {
          // Typed while the failing request was out.
          useNotesStore.getState().applyLocal(7, [upd("b", "B typed meanwhile")]);
          return new HttpResponse(null, { status: 500 });
        }
        return HttpResponse.json(result(body.base_revision + 1, body.ops.map((o) => o.block_id ?? "")));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "A typed")]);
    await useNotesStore.getState().flush(7);
    expect(useNotesStore.getState().retrying[7]).toBe(true);
    expect(pendingNoteOps(7).map((o) => o.block_id)).toEqual(["a", "b"]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["A typed", "B typed meanwhile"]);
    await waitFor(() => expect(posted).toHaveLength(3), { timeout: 3000 });
    // The failed burst is retried as it was; what was typed meanwhile
    // follows in its own burst.
    expect(posted[1]).toEqual(posted[0]);
    expect(posted[2]?.base_revision).toBe(5);
    expect(posted[2]?.ops.map((o) => o.block_id)).toEqual(["b"]);
    await waitFor(() => expect(useNotesStore.getState().retrying[7]).toBe(false));
    expect(pendingNoteOps(7)).toEqual([]);
  });

  it("keeps the queue when reloading the saved version fails", async () => {
    let calls = 0;
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        calls += 1;
        if (calls === 1) {
          useNotesStore.getState().applyLocal(7, [upd("b", "B kept")]);
          return new HttpResponse("bad op", { status: 422 });
        }
        return HttpResponse.json(result(body.base_revision + 1, body.ops.map((o) => o.block_id ?? "")));
      }),
      http.get(`${API_BASE_URL}/notes/7`, () => new HttpResponse(null, { status: 500 })),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "Refused")]);
    await useNotesStore.getState().flush(7);
    expect(pendingNoteOps(7).map((o) => o.block_id)).toEqual(["b"]);
    await waitFor(() => expect(posted).toHaveLength(2), { timeout: 2000 });
    expect(posted[1]?.ops).toEqual([upd("b", "B kept")]);
  });

  it("collapses typing in one block only when nothing later touches it", () => {
    useNotesStore.setState({
      notes: { 7: { note, blocks: [block("a", "A")], suggestions: [], comments: [], revision: 4 } },
    });
    const { applyLocal } = useNotesStore.getState();
    const ins = {
      op: "insert" as const,
      block_id: "x",
      block: { type: "paragraph" as const, props: {}, content: [] },
      parent_id: null,
      after: "a",
    };
    applyLocal(7, [ins]);
    applyLocal(7, [upd("x", "t")]);
    applyLocal(7, [upd("x", "ty")]); // collapses into the previous update
    applyLocal(7, [{ op: "delete", block_id: "x" }]);
    applyLocal(7, [ins]); // undo brings it back
    applyLocal(7, [upd("x", "typed again")]);
    expect(pendingNoteOps(7).map((o) => o.op)).toEqual(["insert", "update", "delete", "insert", "update"]);
    expect(pendingNoteOps(7)[1]?.block?.content).toEqual([{ type: "text", text: "ty", styles: {} }]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["A", "typed again"]);
  });

  it("keeps an insert anchored after a conflicted block", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        if (posted.length === 1) {
          // A new line typed right after "a" while "a" is being refused.
          useNotesStore.getState().applyLocal(7, [
            { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "new" }, parent_id: null, after: "a" },
          ]);
          return HttpResponse.json(
            result(5, [], [{ block_id: "a", reason: "changed_since_base", current: block("a", "Theirs", 5) }]),
          );
        }
        return HttpResponse.json(result(6, body.ops.map((o) => o.block_id ?? "")));
      }),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 5 },
          blocks: [block("a", "Theirs", 5), block("b", "B")],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "Mine")]);
    await useNotesStore.getState().flush(7);
    expect(pendingNoteOps(7).map((o) => o.block_id)).toEqual(["n"]);
    await useNotesStore.getState().flush(7);
    expect(posted[1]?.ops[0]).toMatchObject({ op: "insert", block_id: "n", after: "a" });
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["Theirs", "new", "B"]);
  });

  it("re-anchors a queued insert whose anchor the server no longer has", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        if (posted.length === 1) {
          useNotesStore.getState().applyLocal(7, [
            { op: "insert", block_id: "n", block: { type: "paragraph", props: {}, content: "new" }, parent_id: null, after: "b" },
          ]);
          return HttpResponse.json(result(5, [], [{ block_id: "b", reason: "missing", current: null }]));
        }
        return HttpResponse.json(result(6, body.ops.map((o) => o.block_id ?? "")));
      }),
      // The server no longer has "b".
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note: { ...note, revision: 5 }, blocks: [block("a", "A")], suggestions: [], comments: [] }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("b", "B2")]);
    await useNotesStore.getState().flush(7);
    expect(pendingNoteOps(7)).toEqual([
      expect.objectContaining({ op: "insert", block_id: "n", parent_id: null, after: "a" }),
    ]);
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.id)).toEqual(["a", "n"]);
  });

  it("reloads the whole note when a conflicted block moved", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(
          result(5, [], [{ block_id: "b", reason: "changed_since_base", current: { ...block("b", "B", 5), parent_id: "a" } }]),
        ),
      ),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 5 },
          blocks: [block("a", "A"), { ...block("b", "B", 5), parent_id: "a" }],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    useNotesStore.getState().applyLocal(7, [upd("b", "B2")]);
    await useNotesStore.getState().flush(7);
    await waitFor(() => expect(useNotesStore.getState().reloads[7]).toEqual({ seq: seq + 1, blockIds: null }));
    expect(useNotesStore.getState().notes[7]?.blocks[1]?.parent_id).toBe("a");
  });

  it("says why a section with notes under it came back", async () => {
    const sec: NoteBlock = {
      ...block("s", "1 Intro"),
      type: "section",
      props: { title: "1 Intro" },
      content: null,
      section_key: "1 Intro",
    };
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () => HttpResponse.json({ note, blocks: [sec, block("a", "A")] })),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note: { ...note, revision: 5 }, blocks: [sec, block("a", "A")], suggestions: [], comments: [] }),
      ),
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(result(5, [], [{ block_id: "s", reason: "invalid", current: sec }])),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [{ op: "delete", block_id: "s" }]);
    await useNotesStore.getState().flush(7);
    expect(toastFn).toHaveBeenCalledWith(
      "This section still has notes under it. Delete them first, then the heading.",
    );
    await waitFor(() => expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.id)).toEqual(["s", "a"]));
  });

  it("re-applies queued edits on top of a reload", async () => {
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "Not sent yet")]);
    // The note reloads (e.g. after an agent change) while the edit waits:
    // the edit's burst is held back by a failing server meanwhile.
    server.use(http.post(`${API_BASE_URL}/notes/7/ops`, () => new HttpResponse(null, { status: 503 })));
    await useNotesStore.getState().loadTarget("paper", 3);
    expect(useNotesStore.getState().notes[7]?.blocks[0]?.text).toBe("Not sent yet");
  });

  it("keeps Focus, Follow and folded parts across reloads (persisted)", () => {
    const s = useNotesStore.getState();
    s.setFocusOn(true);
    s.setFollowOn(false);
    s.toggleFold(7, "d1");
    const raw = localStorage.getItem("paperhub-notes-v1") ?? "{}";
    const saved = JSON.parse(raw) as { state: Record<string, unknown> };
    expect(saved.state).toEqual({ focusOn: true, followOn: false, folded: { "7": ["d1"] } });
    useNotesStore.getState().toggleFold(7, "d1");
    expect(useNotesStore.getState().folded[7]).toEqual([]);
  });

  it("lists a deleted thread again when it is reopened (the Undo of Delete)", async () => {
    const thread = {
      id: "c_1", note_id: 7, block_id: "a", quote: "A", status: "open" as const,
      created_at: "2026-11-16T10:00:00Z", items: [{ id: 1, text: "hm", created_at: "2026-11-16T10:00:00Z" }],
    };
    server.use(
      http.patch(`${API_BASE_URL}/notes/comments/c_1`, async ({ request }) => {
        const { status } = (await request.json()) as { status: string };
        return HttpResponse.json({ ...thread, status });
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.setState((s) => ({
      notes: { ...s.notes, 7: { ...s.notes[7]!, comments: [thread] } },
    }));
    await useNotesStore.getState().setCommentStatus(7, "c_1", "deleted");
    expect(useNotesStore.getState().notes[7]?.comments).toEqual([]);
    await useNotesStore.getState().setCommentStatus(7, "c_1", "open");
    expect(useNotesStore.getState().notes[7]?.comments.map((c) => [c.id, c.status])).toEqual([["c_1", "open"]]);
    // again: no duplicate
    await useNotesStore.getState().setCommentStatus(7, "c_1", "open");
    expect(useNotesStore.getState().notes[7]?.comments).toHaveLength(1);
  });
});
