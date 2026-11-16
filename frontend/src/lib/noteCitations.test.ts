import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "@/lib/api";
import { openNoteBlock } from "@/lib/noteCitations";
import { useNotesPaneStore } from "@/store/notesPane";

const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  toastFn.mockReset();
  useNotesPaneStore.setState({ open: false, activeTab: "reader", request: null });
});

describe("openNoteBlock (a [note:B] chip in the chat)", () => {
  it("opens the notes pane at the block, in its note", async () => {
    const id = "5a070443-97b8-40ae-bbcc-ff555ee02d52";
    server.use(
      http.get(`${API_BASE_URL}/notes/blocks/${id}`, () =>
        HttpResponse.json({ note_id: 2, target_kind: "paper", target_id: "47" }),
      ),
    );
    await openNoteBlock(id);
    const s = useNotesPaneStore.getState();
    expect(s.open).toBe(true);
    expect(s.request).toMatchObject({ kind: "paper", id: 47, noteId: 2, blockId: id });
  });

  it("says so when the line is gone", async () => {
    server.use(http.get(`${API_BASE_URL}/notes/blocks/b_gone`, () => new HttpResponse(null, { status: 404 })));
    await openNoteBlock("b_gone");
    expect(useNotesPaneStore.getState().request).toBeNull();
    expect(toastFn).toHaveBeenCalledWith("That line is no longer in your notes.");
  });
});
