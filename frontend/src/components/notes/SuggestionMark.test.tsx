import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { API_BASE_URL } from "@/lib/api";
import { diffText } from "@/lib/notes/textDiff";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteBlock, NoteComment, NoteOpsBody, NoteSuggestion } from "@/types/notes";

const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
const posted: NoteOpsBody[] = [];
const decided: unknown[] = [];

type Run = { type: "text"; text: string; styles: Record<string, unknown> };
const run = (text: string, styles: Record<string, unknown> = {}): Run => ({ type: "text", text, styles });

function para(id: string, runs: Run[], rev = 1): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: runs,
    text: runs.map((r) => r.text).join(""),
    author: "user",
    section_key: null,
    changed_rev: rev,
  };
}

function suggestion(blockId: string, proposed: string | null): NoteSuggestion {
  return {
    id: "sg_1",
    block_id: blockId,
    change_set_id: "cs_1",
    kind: proposed === null ? "delete" : "update",
    proposed: proposed === null ? null : { type: "paragraph", props: {}, content: [run(proposed)] },
    status: "open",
    created_at: "2026-11-16T00:00:00Z",
  };
}

function thread(id: string, quote: string): NoteComment {
  return { id, note_id: 7, block_id: "p1", quote, status: "open", created_at: "2026-11-16T00:00:00Z", items: [{ id: 1, text: "why?", created_at: "2026-11-16T00:00:00Z" }] };
}

const note = { id: 7, target_kind: "paper" as const, target_id: "3", title: "Attention", revision: 4, updated_at: "2026-11-16T00:00:00Z" };

function seed(blocks: NoteBlock[], suggestions: NoteSuggestion[], comments: NoteComment[] = []) {
  useNotesStore.setState({
    notes: { 7: { note, blocks, suggestions, comments, revision: 4 } },
    byTarget: { "paper:3": 7 },
    reloads: {},
  });
}

function result(revision: number, applied: string[]): ApplyResult {
  return { note_id: 7, revision, change_set_id: `cs_${revision}`, applied, suggested: [], rejected: [], reparented: [] };
}

/** The server after a decision: the block as given, no open suggestion. */
function serve(after: NoteBlock, comments: NoteComment[] = []) {
  server.use(
    http.post(`${API_BASE_URL}/notes/suggestions/sg_1`, async ({ request }) => {
      decided.push(await request.json());
      return HttpResponse.json(result(5, ["p1"]));
    }),
    http.get(`${API_BASE_URL}/notes/7`, () =>
      HttpResponse.json({ note: { ...note, revision: 5 }, blocks: [after], suggestions: [], comments, shared_by: 1 }),
    ),
    http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
      posted.push((await request.json()) as NoteOpsBody);
      return HttpResponse.json(result(6, ["p1"]));
    }),
  );
}

function mount() {
  act(() => {
    render(<NoteEditor noteId={7} variant="pane" />);
  });
}

const pm = () => document.querySelector(".ProseMirror") as HTMLElement;
const commentRuns = (id: string) =>
  (useNotesStore.getState().notes[7]!.blocks[0]!.content as Run[]).filter((r) => r.styles.comment === id).map((r) => r.text);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  // jsdom has no layout: give ranges the rectangles ProseMirror asks for.
  const zero = () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => zero();
});
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
});
beforeEach(() => {
  posted.length = 0;
  decided.length = 0;
  toastFn.mockReset();
  useNotesStore.setState({ colorNames: {}, retrying: {}, drafts: {} });
  server.use(http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])));
});

describe("diffText", () => {
  it("finds the one changed stretch without cutting a word or a number", () => {
    expect(diffText("latency drops up to 83%", "latency drops 89%, 88% and 83%")).toEqual({
      prefix: "latency drops ",
      del: "up to",
      ins: "89%, 88% and",
      suffix: " 83%",
    });
    expect(diffText("a 83% cut", "a 89% cut")).toMatchObject({ del: "83", ins: "89" });
    expect(diffText("延遲降低", "延遲大幅降低")).toMatchObject({ prefix: "延遲", del: "", ins: "大幅" });
  });
});

describe("a suggestion in the editor", () => {
  it("shows the agent's update inline as a tracked change, the user's text untouched", async () => {
    seed([para("p1", [run("latency drops up to 83%")])], [suggestion("p1", "latency drops 89%, 88% and 83%")]);
    mount();
    const del = await waitFor(() => {
      const el = pm().querySelector(".note-sug-del");
      expect(el).not.toBeNull();
      return el!;
    });
    expect(del).toHaveTextContent("up to");
    expect(pm().querySelector(".note-sug-ins")).toHaveTextContent("89%, 88% and");
    expect(screen.getByText("Suggested by the assistant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    // The decoration is not note text: nothing is sent.
    await new Promise((r) => setTimeout(r, 700));
    expect(posted).toEqual([]);
  });

  it("shows a suggested delete as the whole line struck through", async () => {
    seed([para("p1", [run("Mine")])], [suggestion("p1", null)]);
    mount();
    expect(await screen.findByText("Suggested removing this line")).toBeInTheDocument();
    expect(pm().querySelector(".note-sug-del")).toHaveTextContent("Mine");
  });

  it("Accept calls the API and the block shows the new text, the change animating out", async () => {
    seed([para("p1", [run("latency drops up to 83%")])], [suggestion("p1", "latency drops 89%, 88% and 83%")]);
    serve(para("p1", [run("latency drops 89%, 88% and 83%")], 5));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(decided).toEqual([{ accept: true }]));
    await waitFor(() => expect(pm()).toHaveTextContent("latency drops 89%, 88% and 83%"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Accept" })).toBeNull());
    expect(pm().querySelector(".note-sug-del")).toBeNull();
    expect(pm().querySelector(".note-sug-ins")).toBeNull();
  });

  it("Reject calls the API and leaves the user's text", async () => {
    seed([para("p1", [run("latency drops up to 83%")])], [suggestion("p1", "latency drops 89%, 88% and 83%")]);
    serve(para("p1", [run("latency drops up to 83%")]));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() => expect(decided).toEqual([{ accept: false }]));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reject" })).toBeNull());
    expect(pm()).toHaveTextContent("latency drops up to 83%");
    expect(pm().querySelector(".note-sug-ins")).toBeNull();
  });
});

describe("accepting a suggestion on commented words", () => {
  it("puts the comment back over its quote in the new text and saves it", async () => {
    const c = thread("c_1", "Mine");
    seed([para("p1", [run("Mine", { comment: "c_1" }), run(" is short")])], [suggestion("p1", "Mine is longer now")], [c]);
    serve(para("p1", [run("Mine is longer now")], 5), [c]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(pm()).toHaveTextContent("Mine is longer now"));
    await waitFor(() => expect(commentRuns("c_1")).toEqual(["Mine"]));
    // Saved as the user's edit of the block.
    await waitFor(() => expect(posted.length).toBeGreaterThan(0), { timeout: 2000 });
    const sent = posted.flatMap((b) => b.ops).find((o) => o.block_id === "p1");
    expect(JSON.stringify(sent?.block?.content)).toContain('"comment":"c_1"');
  });

  it("marks the same occurrence of a repeated quote", async () => {
    const c = thread("c_1", "foo");
    seed([para("p1", [run("foo bar "), run("foo", { comment: "c_1" })])], [suggestion("p1", "foo bar foo baz")], [c]);
    serve(para("p1", [run("foo bar foo baz")], 5), [c]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(pm()).toHaveTextContent("foo bar foo baz"));
    await waitFor(() => {
      const runs = useNotesStore.getState().notes[7]!.blocks[0]!.content as Run[];
      expect(runs.map((r) => [r.text, r.styles.comment ?? null])).toEqual([
        ["foo bar ", null],
        ["foo", "c_1"],
        [" baz", null],
      ]);
    });
  });

  it("keeps the thread open when its quote is gone and says the text changed", async () => {
    const c = thread("c_1", "Mine");
    seed([para("p1", [run("Mine", { comment: "c_1" })])], [suggestion("p1", "Theirs")], [c]);
    serve(para("p1", [run("Theirs")], 5), [c]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(pm()).toHaveTextContent("Theirs"));
    expect(await screen.findByText("The commented text changed.")).toBeInTheDocument();
    expect(screen.getByTestId("comment-thread")).toHaveTextContent("why?");
    expect(useNotesStore.getState().notes[7]!.comments.map((x) => x.status)).toEqual(["open"]);
  });

  it("puts nothing back when the server withdrew the accept (the block was gone)", async () => {
    const c = thread("c_1", "Mine");
    seed([para("p1", [run("Mine", { comment: "c_1" })]), para("p2", [run("Other")])], [suggestion("p1", "Theirs")], [c]);
    server.use(
      http.post(`${API_BASE_URL}/notes/suggestions/sg_1`, () =>
        HttpResponse.json({ note_id: 7, revision: 4, change_set_id: "cs_1", applied: [], suggested: [], reparented: [],
          rejected: [{ block_id: "p1", reason: "missing", current: null }] }),
      ),
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({ note, blocks: [para("p2", [run("Other")])], suggestions: [], comments: [c], shared_by: 1 }),
      ),
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(toastFn).toHaveBeenCalledWith("That suggestion no longer applies."));
    await waitFor(() => expect(pm()).not.toHaveTextContent("Mine"));
    expect(screen.queryByTestId("comment-thread")).toBeNull();
  });
});
