import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { toast } from "sonner";

import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
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
  useNotesStore.setState({ notes: {}, byTarget: {}, reloads: {} });
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
    );
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    useNotesStore.getState().applyLocal(7, [upd("a", "Mine")]);
    await useNotesStore.getState().flush(7);
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks[0]?.text).toBe("Theirs");
    expect(s.notes[7]?.revision).toBe(5);
    expect(s.reloads[7]).toEqual({ seq: seq + 1, blockIds: ["a"] });
    expect(toastFn).toHaveBeenCalledWith("Updated from another view");
  });

  it("shows the saved version when a burst fails", async () => {
    server.use(http.post(`${API_BASE_URL}/notes/7/ops`, () => new HttpResponse(null, { status: 500 })));
    await useNotesStore.getState().loadTarget("paper", 3);
    const seq = useNotesStore.getState().reloads[7]?.seq ?? 0;
    useNotesStore.getState().applyLocal(7, [upd("a", "Lost")]);
    await useNotesStore.getState().flush(7);
    expect(toast.error).toHaveBeenCalledWith("Couldn't save your note. Showing the saved version.");
    const s = useNotesStore.getState();
    expect(s.notes[7]?.blocks[0]?.text).toBe("A");
    expect(s.reloads[7]).toEqual({ seq: seq + 1, blockIds: null });
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
});
