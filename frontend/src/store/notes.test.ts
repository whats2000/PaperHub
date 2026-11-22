import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { toast } from "sonner";

import { API_BASE_URL } from "@/lib/api";
import {
  CARET_GIVE_UP_MS,
  clearPendingNoteOps,
  isUnsavedNote,
  noteHasContent,
  pendingNoteOps,
  resetLinkTargets,
  savedNoteId,
  setNoteSessionCreator,
  useNotesStore,
} from "@/store/notes";
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

  it("keeps what the user typed into a block the server's version won as an unsaved draft", async () => {
    // The agent rewrote the AI block "a" before the user's typing landed.
    const theirs = { ...block("a", "Agent's rewrite", 5), author: "ai" as const };
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(result(5, [], [{ block_id: "a", reason: "changed_since_base", current: theirs }])),
      ),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 5 },
          blocks: [theirs, block("b", "B")],
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "My words")]);
    await useNotesStore.getState().flush(7);
    const s = useNotesStore.getState();
    // The server's version stands, the user's words right after it.
    expect(s.notes[7]?.blocks.map((b) => b.text)).toEqual(["Agent's rewrite", "My words", "B"]);
    const kept = s.notes[7]!.blocks[1]!;
    expect(kept.id).not.toBe("a");
    expect(Object.keys(s.drafts[7] ?? {})).toEqual([kept.id]);
    expect(toastFn).toHaveBeenCalledWith("Updated from another view");
  });

  it("drops no draft when the refused update matches the server's version", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json(result(5, [], [{ block_id: "a", reason: "changed_since_base", current: block("a", "Same", 5) }])),
      ),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note: { ...note, revision: 5 }, blocks: [block("a", "Same", 5), block("b", "B")], suggestions: [], comments: [] }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [upd("a", "Same")]);
    await useNotesStore.getState().flush(7);
    expect(useNotesStore.getState().drafts[7] ?? {}).toEqual({});
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
    const blocks = useNotesStore.getState().notes[7]?.blocks ?? [];
    expect(blocks.slice(0, 2).map((b) => b.id)).toEqual(["b", "a"]);
    // "Mine" stays on screen, an unsaved draft after the server's "a".
    expect(blocks.map((b) => b.text)).toEqual(["B", "A", "Mine"]);
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

  it("re-keys a pasted block the server saved under a new id, so typing in it saves", async () => {
    // The server's note: an insert whose id is live in another note gets a
    // new id; an op naming the old id finds nothing.
    const saved = new Map<string, string>([["a", "A"], ["b", "B"]]);
    let reply: (() => void) | null = null;
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        const applied: string[] = [];
        const renamed: { from: string; to: string }[] = [];
        const rejected: ApplyResult["rejected"] = [];
        for (const o of body.ops) {
          const text = typeof o.block?.content === "string" ? o.block.content : "";
          if (o.op === "insert" && o.block_id === "elsewhere") {
            renamed.push({ from: "elsewhere", to: "srv1" });
            saved.set("srv1", text);
            applied.push("srv1");
          } else if (saved.has(o.block_id ?? "")) {
            saved.set(o.block_id!, text);
            applied.push(o.block_id!);
          } else {
            rejected.push({ block_id: o.block_id ?? "", reason: "missing", current: null });
          }
        }
        // The first response is held while the user keeps typing.
        if (posted.length === 1) await new Promise<void>((r) => (reply = r));
        return HttpResponse.json({ ...result(body.base_revision + 1, applied, rejected), renamed });
      }),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: { ...note, revision: 6 },
          blocks: [...saved].map(([id, text]) => block(id, text, 6)),
          suggestions: [],
          comments: [],
        }),
      ),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    const para = (text: string) => ({ type: "paragraph" as const, props: {}, content: text });
    useNotesStore
      .getState()
      .applyLocal(7, [{ op: "insert", block_id: "elsewhere", block: para("pasted"), parent_id: null, after: "b" }]);
    const first = useNotesStore.getState().flush(7);
    await waitFor(() => expect(reply).not.toBeNull());
    // Typing after the paste, while its save is on the way.
    useNotesStore.getState().applyLocal(7, [{ op: "update", block_id: "elsewhere", block: para("pasted!") }]);
    reply!();
    await first;
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks.map((b) => b.id)).toEqual(["a", "b", "srv1"]);
    expect(s.renames[7]?.pairs).toEqual([["elsewhere", "srv1"]]);
    expect(pendingNoteOps(7)).toEqual([{ op: "update", block_id: "srv1", block: para("pasted!") }]);
    await useNotesStore.getState().flush(7);
    expect(posted[1]?.ops.map((o) => o.block_id)).toEqual(["srv1"]);
    // Saved, with no conflict reload and no toast.
    expect(saved.get("srv1")).toBe("pasted!");
    expect(useNotesStore.getState().reloads[7]?.seq ?? 0).toBe(seq);
    expect(toastFn).not.toHaveBeenCalled();
    // A reload shows the block once, with the typing.
    await useNotesStore.getState().loadNote(7);
    const after = useNotesStore.getState().notes[7]?.blocks ?? [];
    expect(after.map((b) => [b.id, b.text])).toEqual([["a", "A"], ["b", "B"], ["srv1", "pasted!"]]);
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
    // "Mine", refused, stays as an unsaved draft right after "Theirs".
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.text)).toEqual(["Theirs", "Mine", "new", "B"]);
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
    // The typing into the deleted "b" stays, an unsaved draft in its place.
    const blocks = useNotesStore.getState().notes[7]?.blocks ?? [];
    expect(blocks.map((b) => b.text)).toEqual(["A", "B2", "new"]);
    expect(blocks[2]?.id).toBe("n");
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

  it("deletes a section heading with notes under it, with no toast", async () => {
    // [user, 2026-11-19] Any heading may go; the notes under it stay.
    const sec: NoteBlock = {
      ...block("s", "1 Intro"),
      type: "section",
      props: { title: "1 Intro" },
      content: null,
      section_key: "1 Intro",
    };
    let sent: unknown = null;
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () => HttpResponse.json({ note, blocks: [sec, block("a", "A")] })),
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(result(5, ["s"]));
      }),
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(7, [{ op: "delete", block_id: "s" }]);
    await useNotesStore.getState().flush(7);
    expect(sent).toMatchObject({ ops: [{ op: "delete", block_id: "s" }] });
    expect(toastFn).not.toHaveBeenCalled();
    expect(useNotesStore.getState().notes[7]?.blocks.map((b) => b.id)).toEqual(["a"]);
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

  it("keeps Focus, Follow, the table of contents, the table preferences and folded parts across reloads (persisted)", () => {
    const s = useNotesStore.getState();
    expect(s.tocOn).toBe(true);
    expect(s.tablePreset).toBe("paper");
    s.setFocusOn(true);
    s.setFollowOn(false);
    s.setTocOn(false);
    s.setTablePreset("notion");
    s.toggleFold(7, "d1");
    const raw = localStorage.getItem("paperhub-notes-v1") ?? "{}";
    const saved = JSON.parse(raw) as { state: Record<string, unknown> };
    expect(saved.state).toEqual({
      focusOn: true,
      followOn: false,
      tocOn: false,
      tablePreset: "notion",
      tableGuides: true,
      folded: { "7": ["d1"] },
    });
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

describe("unsaved drafts and leaving the page", () => {
  const leave = () => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  };

  it("asks before the page is left while a note holds an unsaved draft", () => {
    expect(leave()).toBe(false);
    useNotesStore.setState({ drafts: { 7: { d1: { op: "insert", block_id: "d1" } } } });
    expect(leave()).toBe(true);
    useNotesStore.setState({ drafts: { 7: {} } });
    expect(leave()).toBe(false);
  });
});

describe("caretAfter", () => {
  it("is dropped when its block never shows", () => {
    vi.useFakeTimers();
    try {
      useNotesStore.getState().setCaretAfter({ noteId: 7, blockId: "b_gone" });
      vi.advanceTimersByTime(CARET_GIVE_UP_MS - 1);
      expect(useNotesStore.getState().caretAfter).not.toBeNull();
      vi.advanceTimersByTime(1);
      expect(useNotesStore.getState().caretAfter).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("noteHasContent", () => {
  const withTable = (rows: string[][], props: Record<string, unknown> = {}) => ({
    note: { id: 7, target_kind: "session" as const, target_id: "1", title: "", revision: 1, updated_at: "" },
    blocks: [
      {
        ...block("t", ""),
        type: "table",
        props,
        content: {
          type: "tableContent",
          columnWidths: rows[0]!.map(() => null),
          headerRows: 1,
          rows: rows.map((r) => ({ cells: r.map((c) => (c ? [{ type: "text", text: c, styles: {} }] : [])) })),
        },
      },
    ],
    suggestions: [],
    comments: [],
    revision: 1,
  });

  it("an empty table is not content; a cell, caption or notes with text is", () => {
    expect(noteHasContent(withTable([["", "", ""], ["", "", ""]]) as never)).toBe(false);
    expect(noteHasContent(withTable([["", ""], ["", "x"]]) as never)).toBe(true);
    expect(noteHasContent(withTable([["", ""]], { caption: "Rates" }) as never)).toBe(true);
  });
});

describe("resolveLinkTarget", () => {
  it("keeps a known target when a stale re-check fails in transit", async () => {
    resetLinkTargets();
    const known = { status: "ok" as const, checkedAt: Date.now() - 61_000, name: "RoboTTT", blocks: [] };
    useNotesStore.setState({ linkTargets: { "note:12": known } });
    server.use(http.get(`${API_BASE_URL}/notes/12`, () => HttpResponse.error()));
    await useNotesStore.getState().resolveLinkTarget("note", 12, { maxAgeMs: 60_000 });
    expect(useNotesStore.getState().linkTargets["note:12"]).toEqual(known);
  });

  it("still marks a stale target dead when the re-check finds it deleted", async () => {
    resetLinkTargets();
    useNotesStore.setState({
      linkTargets: { "note:12": { status: "ok", checkedAt: Date.now() - 61_000, name: "RoboTTT" } },
    });
    server.use(http.get(`${API_BASE_URL}/notes/12`, () => new HttpResponse(null, { status: 404 })));
    await useNotesStore.getState().resolveLinkTarget("note", 12, { maxAgeMs: 60_000 });
    expect(useNotesStore.getState().linkTargets["note:12"]?.status).toBe("dead");
  });
});

describe("nothing is created by looking (Task 14)", () => {
  const saved = { id: 9, target_kind: "session" as const, target_id: "42", title: "", revision: 0, updated_at: "x" };
  const ins = (id: string, text: string) => ({
    op: "insert" as const,
    block_id: id,
    block: { type: "paragraph" as const, props: {}, content: [{ type: "text", text, styles: {} }] },
  });

  it("a target with no note gets an empty placeholder, and nothing is created", async () => {
    const calls: string[] = [];
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () =>
        HttpResponse.json({ note: null, blocks: [], shared_by: 2, default_title: "Attention", suggestions: [], comments: [] }),
      ),
      http.post(`${API_BASE_URL}/notes/target`, () => {
        calls.push("create");
        return HttpResponse.json({});
      }),
    );
    const id = await useNotesStore.getState().loadTarget("paper", 3);
    expect(isUnsavedNote(id)).toBe(true);
    const st = useNotesStore.getState();
    expect(st.byTarget["paper:3"]).toBe(id);
    expect(st.notes[id]).toMatchObject({ blocks: [], sharedBy: 2, defaultTitle: "Attention" });
    // Looking again keeps the same placeholder; flushing nothing creates nothing.
    expect(await useNotesStore.getState().loadTarget("paper", 3)).toBe(id);
    await useNotesStore.getState().flush(id);
    expect(calls).toEqual([]);
  });

  it("the first write of a local chat's note creates the chat, then the note, then saves it — once", async () => {
    const order: string[] = [];
    const sessionCreator = vi.fn(() => {
      order.push("session");
      return Promise.resolve(42);
    });
    setNoteSessionCreator(sessionCreator);
    server.use(
      http.post(`${API_BASE_URL}/notes/target`, async ({ request }) => {
        const url = new URL(request.url);
        order.push(`note:${url.searchParams.get("kind")}:${url.searchParams.get("id")}`);
        await new Promise((r) => setTimeout(r, 30));
        return HttpResponse.json({ note: saved, blocks: [], shared_by: 1, default_title: "", suggestions: [], comments: [] });
      }),
      http.post(`${API_BASE_URL}/notes/9/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        order.push("ops");
        return HttpResponse.json({ ...result(body.base_revision + 1, body.ops.map((o) => o.block_id!)), note_id: 9 });
      }),
    );
    const ph = useNotesStore.getState().openChatDraft(5);
    expect(isUnsavedNote(ph)).toBe(true);
    expect(useNotesStore.getState().openChatDraft(5)).toBe(ph);
    expect(sessionCreator).not.toHaveBeenCalled();
    // Typing: the first keystrokes, then more while the note is being made.
    useNotesStore.getState().applyLocal(ph, [ins("p1", "H")], "pane");
    const first = useNotesStore.getState().flush(ph);
    useNotesStore.getState().applyLocal(ph, [upd("p1", "He")], "pane");
    const second = useNotesStore.getState().flush(ph);
    await Promise.all([first, second]);
    await useNotesStore.getState().flush(9);
    expect(order[0]).toBe("session");
    expect(order.filter((o) => o.startsWith("note:"))).toEqual(["note:session:42"]);
    expect(sessionCreator).toHaveBeenCalledTimes(1);
    // Nothing typed is lost: the saved note has the latest text.
    const sent = posted.flatMap((b) => b.ops);
    expect(sent[0]).toMatchObject({ op: "insert", block_id: "p1" });
    const last = sent.filter((o) => o.block_id === "p1").at(-1)!;
    expect(JSON.stringify(last.block?.content)).toContain('"He"');
    const st = useNotesStore.getState();
    expect(st.notes[ph]).toBeUndefined();
    expect(st.byTarget["local:5"]).toBe(9);
    expect(st.byTarget["session:42"]).toBe(9);
    expect(st.origins[9]).toBe(ph);
    expect(savedNoteId(ph)).toBe(9);
    expect(st.notes[9]!.blocks.map((b) => b.text)).toEqual(["He"]);
  });

  it("a note saved elsewhere (a mark) replaces an empty placeholder", async () => {
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () =>
        HttpResponse.json({ note: null, blocks: [], shared_by: 0, suggestions: [], comments: [] }),
      ),
    );
    const ph = await useNotesStore.getState().loadTarget("paper", 3);
    await useNotesStore.getState().noteAppeared(7);
    const st = useNotesStore.getState();
    expect(st.byTarget["paper:3"]).toBe(7);
    expect(st.notes[ph]).toBeUndefined();
    expect(st.notes[7]!.blocks.map((b) => b.id)).toEqual(["a", "b"]);
  });
});

describe("a first write that can never be saved (Task 14)", () => {
  const ins = {
    op: "insert" as const,
    block_id: "p1",
    block: { type: "paragraph" as const, props: {}, content: [{ type: "text", text: "x", styles: {} }] },
  };

  it("a target gone (4xx) says so plainly, keeps the words and does not retry", async () => {
    let tries = 0;
    server.use(
      http.get(`${API_BASE_URL}/notes/target`, () =>
        HttpResponse.json({ note: null, blocks: [], shared_by: 0, suggestions: [], comments: [] }),
      ),
      http.post(`${API_BASE_URL}/notes/target`, () => {
        tries += 1;
        return new HttpResponse("gone", { status: 404 });
      }),
    );
    const ph = await useNotesStore.getState().loadTarget("paper", 3);
    useNotesStore.getState().applyLocal(ph, [ins], "pane");
    await useNotesStore.getState().flush(ph);
    expect(tries).toBe(1);
    expect(toastFn).toHaveBeenCalledWith("Couldn't save this note.");
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled();
    expect(useNotesStore.getState().retrying[ph] ?? false).toBe(false);
    expect(useNotesStore.getState().notes[ph]!.blocks.map((b) => b.text)).toEqual(["x"]);
  });

  it("a local chat no longer there is permanent too (no retry loop)", async () => {
    setNoteSessionCreator(() => Promise.reject(new Error("session 5 not found")));
    const ph = useNotesStore.getState().openChatDraft(5);
    useNotesStore.getState().applyLocal(ph, [ins], "pane");
    await useNotesStore.getState().flush(ph);
    expect(toastFn).toHaveBeenCalledWith("Couldn't save this note.");
    expect(useNotesStore.getState().retrying[ph] ?? false).toBe(false);
  });
});

describe("table preferences", () => {
  it("keeps the last table preset and the guides switch, and drops a legacy table style", async () => {
    localStorage.removeItem("paperhub-notes-v1");
    const init = useNotesStore.getInitialState();
    expect(init.tablePreset).toBe("paper");
    expect(init.tableGuides).toBe(true);
    useNotesStore.getState().setTablePreset("notion");
    useNotesStore.getState().setTableGuides(false);
    const saved = JSON.parse(localStorage.getItem("paperhub-notes-v1") ?? "{}") as {
      state: { tablePreset?: string; tableGuides?: boolean };
    };
    expect(saved.state).toMatchObject({ tablePreset: "notion", tableGuides: false });
    useNotesStore.setState({ tablePreset: "paper", tableGuides: true });
    localStorage.setItem("paperhub-notes-v1", JSON.stringify(saved));
    await useNotesStore.persist.rehydrate();
    expect(useNotesStore.getState().tablePreset).toBe("notion");
    expect(useNotesStore.getState().tableGuides).toBe(false);
    // An old build's global style, or an unknown preset: the default.
    localStorage.setItem("paperhub-notes-v1", JSON.stringify({ state: { tableStyle: "grid" }, version: 0 }));
    useNotesStore.setState({ tablePreset: "paper", tableGuides: true });
    await useNotesStore.persist.rehydrate();
    expect(useNotesStore.getState().tablePreset).toBe("paper");
    expect("tableStyle" in useNotesStore.getState()).toBe(false);
    localStorage.setItem("paperhub-notes-v1", JSON.stringify({ state: { tablePreset: "dotted" }, version: 0 }));
    await useNotesStore.persist.rehydrate();
    expect(useNotesStore.getState().tablePreset).toBe("paper");
  });
});
