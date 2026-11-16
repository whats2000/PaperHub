import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { BlockNoteEditor } from "@blocknote/core";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { noteSchema } from "@/components/notes/schema";
import { diffDocuments, opBlock, toEditorBlocks, treeIds, type EditorBlock } from "@/components/notes/sync";
import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteBlock, NoteOpsBody } from "@/types/notes";

const { toastFn } = vi.hoisted(() => ({ toastFn: vi.fn() }));
vi.mock("sonner", () => ({ toast: Object.assign(toastFn, { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
const posted: NoteOpsBody[] = [];

function blk(id: string, extra: Partial<NoteBlock>): NoteBlock {
  return {
    id,
    note_id: 7,
    parent_id: null,
    type: "paragraph",
    props: {},
    content: null,
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 1,
    ...extra,
  };
}

const section = blk("s1", {
  type: "section",
  props: { title: "2 Method" },
  text: "2 Method",
  section_key: "2 Method",
});
const quote = blk("q1", {
  type: "paperQuote",
  props: {
    markId: "mk_1",
    color: "green",
    kind: "highlight",
    quote: "attention is all you need",
    pieces: [{ quote: "attention is all you need", page: 2 }],
    source: { page: 2, section: "2 Method" },
  },
  text: "attention is all you need",
  section_key: "2 Method",
});
const mine = blk("p1", {
  content: [{ type: "text", text: "Mine", styles: {} }],
  text: "Mine",
});

function seed(blocks: NoteBlock[]) {
  useNotesStore.setState({
    notes: {
      7: {
        note: {
          id: 7,
          target_kind: "paper",
          target_id: "3",
          title: "Attention",
          revision: 4,
          updated_at: "2026-11-16T00:00:00Z",
        },
        blocks,
        suggestions: [],
        comments: [],
        revision: 4,
      },
    },
    byTarget: { "paper:3": 7 },
    reloads: {},
  });
}

function mount() {
  act(() => {
    render(<NoteEditor noteId={7} variant="pane" />);
  });
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
  useNotesStore.setState({ colorNames: {} });
  server.use(
    http.get(`${API_BASE_URL}/marks/colors`, () =>
      HttpResponse.json([
        { color: "green", name: "Key result", visible: true },
        { color: "blue", name: "Method", visible: true },
      ]),
    ),
  );
});

describe("the notes schema", () => {
  it("keeps block ids and PaperHub props through load, export and replaceBlocks", () => {
    const nested = blk("c1", { parent_id: "p1", content: [{ type: "text", text: "child", styles: {} }] });
    const stored = [section, quote, mine, nested];
    const editor = BlockNoteEditor.create({ schema: noteSchema, initialContent: toEditorBlocks(stored) as never });
    const doc = () => editor.document as unknown as EditorBlock[];
    expect([...treeIds(doc())]).toEqual(["s1", "q1", "p1", "c1"]);
    // What the editor would send is what the server stored.
    const sent = doc().map((b) => opBlock(b));
    expect(sent[0]).toEqual({ type: "section", props: { title: "2 Method" }, section_key: "2 Method" });
    expect(sent[1]).toEqual({ type: "paperQuote", props: quote.props });
    // A JSON round trip through replaceBlocks keeps every id (incl. nested + custom).
    const json = JSON.parse(JSON.stringify(editor.document)) as never;
    editor.replaceBlocks(editor.document, json);
    expect([...treeIds(doc())]).toEqual(["s1", "q1", "p1", "c1"]);
    // Against the stored tree only BlockNote's default text props differ:
    // no move / insert / delete, and the PaperHub blocks are unchanged.
    const ops = diffDocuments(toEditorBlocks(stored), doc());
    expect(ops.filter((o) => o.op !== "update" || o.block_id === "s1" || o.block_id === "q1")).toEqual([]);
  });
});

describe("NoteEditor", () => {
  it("renders a section heading and a paperQuote with its tag and colour bar", async () => {
    seed([section, quote]);
    mount();
    expect(await screen.findByRole("heading", { name: "2 Method" })).toBeInTheDocument();
    const ref = screen.getByTestId("paper-ref-q1");
    expect(ref).toHaveAttribute("data-color", "green");
    expect(ref.style.getPropertyValue("--mc")).toBe("var(--hl-green)");
    expect(ref).toHaveTextContent("attention is all you need");
    await waitFor(() =>
      expect(ref).toHaveTextContent("From the paper · Key result · §2 · p. 3"),
    );
  });

  it("tags a mark made on a translation and shows the original on demand", async () => {
    seed([
      blk("q2", {
        type: "paperQuote",
        props: {
          markId: "mk_2",
          color: "blue",
          kind: "highlight",
          quote: "使用者動作的延遲",
          pieces: [{ quote: "使用者動作的延遲" }],
          source: { section: "Introduction" },
          lang: "zh-TW",
          orig: "the latency of user actions",
        },
      }),
    ]);
    mount();
    const ref = await screen.findByTestId("paper-ref-q2");
    expect(ref).toHaveTextContent("文 Translated · 繁中");
    const btn = screen.getByRole("button", { name: "Show original" });
    const collapse = screen.getByTestId("orig-q2");
    expect(collapse).toHaveAttribute("data-state", "closed");
    fireEvent.click(btn);
    expect(collapse).toHaveAttribute("data-state", "open");
    expect(collapse).toHaveTextContent("the latency of user actions");
    expect(screen.getByRole("button", { name: "Hide original" })).toBeInTheDocument();
  });

  it("shows the anchor-lost and pieces-lost states", async () => {
    seed([
      blk("q3", {
        type: "paperQuote",
        props: { markId: "mk_3", color: "pink", kind: "highlight", quote: "gone", pieces: [], source: {}, anchorLost: true },
      }),
      blk("q4", {
        type: "paperQuote",
        props: {
          markId: "mk_4",
          color: "purple",
          kind: "highlight",
          quote: "a b",
          pieces: [{ quote: "a" }, { quote: "b", lost: true }],
          source: {},
          piecesLost: 1,
        },
      }),
    ]);
    mount();
    expect(await screen.findByText(/Anchor lost/)).toBeInTheDocument();
    expect(screen.getByText("1 of 2 pieces lost")).toBeInTheDocument();
  });

  it("shows a figure mark's page crop", async () => {
    seed([
      blk("f1", {
        type: "paperFigure",
        props: {
          markId: "mk_5",
          color: "blue",
          kind: "figure",
          quote: "Figure 3: Latency profiling",
          pieces: [{ quote: "Figure 3: Latency profiling", page: 2, box: [0.1, 0.2, 0.5, 0.6] }],
          source: { page: 2 },
        },
      }),
    ]);
    mount();
    const img = await screen.findByRole("img", { name: "Figure 3: Latency profiling" });
    expect(img.getAttribute("src")).toBe(
      `${API_BASE_URL}/papers/content/3/page-crop?page=2&x0=0.1&y0=0.2&x1=0.5&y1=0.6`,
    );
  });

  it("Enter on a selected section heading adds a line below it", async () => {
    seed([section, mine]);
    mount();
    await screen.findByRole("heading", { name: "2 Method" });
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { setNodeSelection: (pos: number) => boolean } };
    };
    const ids = () =>
      [...document.querySelectorAll(".bn-block-outer")].map((e) => e.getAttribute("data-id"));
    expect(ids()).toEqual(["s1", "p1"]);
    act(() => {
      pm.editor.commands.setNodeSelection(2);
    });
    act(() => {
      fireEvent.keyDown(pm, { key: "Enter" });
    });
    const after = ids();
    expect(after).toHaveLength(3);
    expect(after[0]).toBe("s1");
    expect(after[2]).toBe("p1");
    // The empty line is not sent until it holds something.
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted).toHaveLength(0);
  });

  it.each([
    ["$$ ", "math"],
    ['" ', "quote"],
    ["---", "divider"],
  ])("typing %j at a line start makes a %s block", (typed, type) => {
    seed([blk("p0", { content: [] })]);
    mount();
    type View = {
      state: { selection: { from: number; to: number }; tr: { insertText: (t: string, f: number, to: number) => unknown } };
      dispatch: (tr: unknown) => void;
      someProp: (name: string, f: (fn: (...a: unknown[]) => boolean) => boolean) => boolean | undefined;
    };
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { view: View; commands: { setTextSelection: (pos: number) => boolean } };
    };
    const view = pm.editor.view;
    act(() => {
      pm.editor.commands.setTextSelection(3);
      for (const ch of typed) {
        const { from, to } = view.state.selection;
        const handled = view.someProp("handleTextInput", (f) => f(view, from, to, ch, () => view.state.tr));
        if (!handled) view.dispatch(view.state.tr.insertText(ch, from, to));
      }
    });
    expect(document.querySelector(`[data-content-type="${type}"]`)).not.toBeNull();
  });

  it("editor reloads a conflicted block", async () => {
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        posted.push((await request.json()) as NoteOpsBody);
        const res: ApplyResult = {
          note_id: 7,
          revision: 5,
          change_set_id: null,
          applied: [],
          suggested: [],
          rejected: [
            {
              block_id: "p1",
              reason: "changed_since_base",
              current: { ...mine, content: [{ type: "text", text: "Theirs", styles: {} }], text: "Theirs", changed_rev: 5 },
            },
          ],
          reparented: [],
        };
        return HttpResponse.json(res);
      }),
    );
    seed([mine]);
    mount();
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { insertContentAt: (pos: number, text: string) => boolean } };
    };
    await screen.findByText("Mine");
    // Type at the end of "Mine" through the editor, as a keystroke would.
    act(() => {
      pm.editor.commands.insertContentAt(7, "!");
    });
    expect(useNotesStore.getState().notes[7]?.blocks[0]?.text).toBe("Mine!");
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted).toHaveLength(1);
    expect(posted[0]?.ops).toEqual([
      expect.objectContaining({ op: "update", block_id: "p1" }),
    ]);
    await waitFor(() => expect(screen.getByText("Theirs")).toBeInTheDocument());
    expect(screen.queryByText("Mine!")).not.toBeInTheDocument();
    expect(toastFn).toHaveBeenCalledWith("Updated from another view");
    // The reload itself is not sent back as an edit.
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted).toHaveLength(1);
  });
});
