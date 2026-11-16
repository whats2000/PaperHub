import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { BlockNoteEditor } from "@blocknote/core";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { noteSchema } from "@/components/notes/schema";
import { diffDocuments, opBlock, toEditorBlocks, treeIds, type EditorBlock } from "@/components/notes/sync";
import { API_BASE_URL } from "@/lib/api";
import i18n from "@/lib/i18n";
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
  useNotesStore.setState({ colorNames: {}, retrying: {}, drafts: {} });
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

  it("shows ||hidden|| text as a bar until clicked, and covers it again on a second click", async () => {
    seed([
      blk("h1", {
        content: [{ type: "text", text: "secret", styles: { hidden: true } }],
        text: "secret",
      }),
    ]);
    mount();
    const el = (await screen.findByText("secret")).closest<HTMLElement>(".note-hidden")!;
    expect(el).toHaveClass("note-hidden");
    expect(el).not.toHaveAttribute("data-shown");
    fireEvent.click(el);
    expect(el).toHaveAttribute("data-shown");
    fireEvent.click(el);
    expect(el).not.toHaveAttribute("data-shown");
  });

  it("draws ||hidden|| and comment text in the editor's own DOM (a React mark view rendered late and moved the caret)", async () => {
    seed([
      blk("h1", {
        content: [
          { type: "text", text: "secret", styles: { hidden: true } },
          { type: "text", text: " and ", styles: {} },
          { type: "text", text: "noted", styles: { comment: "c_1" } },
        ],
        text: "secret and noted",
      }),
    ]);
    mount();
    const hidden = (await screen.findByText("secret")).closest<HTMLElement>(".note-hidden")!;
    const comment = screen.getByText("noted").closest<HTMLElement>(".note-comment")!;
    expect(comment).toHaveAttribute("data-comment-id", "c_1");
    for (const el of [hidden, comment]) expect(el.closest(".react-renderer")).toBeNull();
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

  it.each([
    ["section heading", section],
    ["paper quote", quote],
    ["divider", blk("d1", { type: "divider" })],
  ])("Enter on a selected %s adds a line below it", async (_name, first) => {
    seed([first, mine]);
    mount();
    await waitFor(() => expect(document.querySelectorAll(".bn-block-outer")).toHaveLength(2));
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { setNodeSelection: (pos: number) => boolean } };
    };
    const ids = () =>
      [...document.querySelectorAll(".bn-block-outer")].map((e) => e.getAttribute("data-id"));
    expect(ids()).toEqual([first.id, "p1"]);
    act(() => {
      pm.editor.commands.setNodeSelection(2);
    });
    act(() => {
      fireEvent.keyDown(pm, { key: "Enter" });
    });
    const after = ids();
    expect(after).toHaveLength(3);
    expect(after[0]).toBe(first.id);
    expect(after[2]).toBe("p1");
    // The empty line is not sent until it holds something.
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted).toHaveLength(0);
  });

  it("labels the source chip and shows its full text when cut", async () => {
    seed([section, quote]);
    mount();
    const chip = await screen.findByRole("button", { name: "Open in the paper: Attention · §2 · p. 3" });
    const label = chip.querySelector("[data-truncate]") as HTMLElement;
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 300 });
    Object.defineProperty(label, "clientWidth", { configurable: true, value: 120 });
    fireEvent.pointerEnter(chip);
    expect(chip).toHaveAttribute("title", "Attention · §2 · p. 3");
    Object.defineProperty(label, "scrollWidth", { configurable: true, value: 120 });
    fireEvent.pointerEnter(chip);
    expect(chip).toHaveAttribute("title", "Open in the paper");
  });

  it("names the page in the UI language", async () => {
    await i18n.changeLanguage("zh-TW");
    seed([section, quote]);
    mount();
    const ref = await screen.findByTestId("paper-ref-q1");
    expect(ref).toHaveTextContent("§2 · 第 3 頁");
  });

  it("switches the editor's own strings with the UI language", async () => {
    seed([mine]);
    mount();
    await screen.findByText("Mine");
    const before = document.querySelector(".ProseMirror");
    await act(async () => {
      await i18n.changeLanguage("ja");
    });
    // A new editor instance with the Japanese BlockNote dictionary.
    await waitFor(() => expect(document.querySelector(".ProseMirror")).not.toBe(before));
    expect(screen.getByTestId("note-editor")).toHaveAttribute("data-lang", "ja");
    expect(screen.getByText("Mine")).toBeInTheDocument();
  });

  it("says when the note is not saved and being retried", async () => {
    seed([mine]);
    mount();
    await screen.findByText("Mine");
    expect(screen.queryByText("Not saved — retrying")).not.toBeInTheDocument();
    act(() => {
      useNotesStore.setState({ retrying: { 7: true } });
    });
    expect(screen.getByText("Not saved — retrying").closest("[data-state]")).toHaveAttribute("data-state", "open");
    act(() => {
      useNotesStore.setState({ retrying: { 7: false } });
    });
    expect(screen.queryByText("Not saved — retrying")).not.toBeInTheDocument();
  });

  it("marks an unsaved draft and copies its text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    seed([mine, blk("n1", { content: [{ type: "text", text: "my draft", styles: {} }], text: "my draft" })]);
    useNotesStore.setState({
      drafts: { 7: { n1: { op: "insert", block_id: "n1", block: { type: "paragraph", props: {} }, parent_id: null, after: "p1" } } },
    });
    mount();
    // In the editor and in the notice above it.
    expect(await screen.findAllByText("my draft")).toHaveLength(2);
    const notice = screen.getByTestId("unsaved-drafts");
    expect(notice).toHaveAttribute("data-state", "open");
    expect(notice).toHaveTextContent("Not saved");
    expect(notice).toHaveTextContent("my draft");
    expect(document.querySelector("style[data-unsaved]")?.textContent).toContain('[data-id="n1"]');
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("my draft");
  });

  it("says so when the text cannot be copied", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    seed([mine, blk("n1", { content: [{ type: "text", text: "my draft", styles: {} }], text: "my draft" })]);
    useNotesStore.setState({
      drafts: { 7: { n1: { op: "insert", block_id: "n1", block: { type: "paragraph", props: {} }, parent_id: null } } },
    });
    mount();
    await screen.findAllByText("my draft");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await Promise.resolve();
    });
    expect(toast.error).toHaveBeenCalledWith("Couldn't copy. Select the text and copy it yourself.");

    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    vi.mocked(toast.error).mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await Promise.resolve();
    });
    expect(toast.error).toHaveBeenCalledWith("Couldn't copy. Select the text and copy it yourself.");
  });

  it("keeps the last draft's text while the notice closes", async () => {
    seed([mine, blk("n1", { content: [{ type: "text", text: "my draft", styles: {} }], text: "my draft" })]);
    useNotesStore.setState({
      drafts: { 7: { n1: { op: "insert", block_id: "n1", block: { type: "paragraph", props: {} }, parent_id: null } } },
    });
    mount();
    const notice = await screen.findByTestId("unsaved-drafts");
    expect(notice).toHaveTextContent("my draft");
    act(() => {
      useNotesStore.setState({ drafts: { 7: {} } });
    });
    // Closing, with its content still there.
    expect(notice).toHaveAttribute("data-state", "closed");
    expect(notice).toHaveTextContent("my draft");
    fireEvent.transitionEnd(notice, { propertyName: "grid-template-rows" });
    expect(notice).not.toHaveTextContent("my draft");
  });

  it("shows the loading line again when it switches to a note not loaded yet", async () => {
    seed([mine]);
    const { rerender } = render(<NoteEditor noteId={7} variant="pane" />);
    await screen.findByText("Mine");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    rerender(<NoteEditor noteId={8} variant="pane" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the note…");
  });

  it("fades the editor in over the loading line", async () => {
    useNotesStore.setState({ notes: {}, byTarget: {}, reloads: {} });
    render(<NoteEditor noteId={7} variant="pane" />);
    const loading = screen.getByRole("status");
    expect(loading).toHaveTextContent("Loading the note…");
    act(() => seed([mine]));
    const editor = await screen.findByTestId("note-editor");
    expect(editor.className).toContain("fade-in");
    // The loading line leaves with a fade instead of vanishing.
    expect(loading).toHaveAttribute("data-closed");
    fireEvent.transitionEnd(loading, { propertyName: "opacity" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
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
      http.get(`${API_BASE_URL}/notes/7`, () =>
        HttpResponse.json({
          note: {
            id: 7,
            target_kind: "paper",
            target_id: "3",
            title: "Attention",
            revision: 5,
            updated_at: "2026-11-16T00:00:00Z",
          },
          blocks: [{ ...mine, content: [{ type: "text", text: "Theirs", styles: {} }], text: "Theirs", changed_rev: 5 }],
          suggestions: [],
          comments: [],
        }),
      ),
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

describe("comments in the editor", () => {
  type PM = HTMLElement & {
    editor: {
      commands: { setTextSelection: (r: { from: number; to: number }) => boolean };
      view: { dom: HTMLElement };
    };
  };

  // jsdom has no layout: give ranges the rectangles ProseMirror asks for.
  beforeAll(() => {
    const zero = () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
    Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
    Range.prototype.getBoundingClientRect = () => zero();
  });

  it("Ctrl+Shift+M opens a comment box on the selected words; posting marks them and a click reopens the thread", async () => {
    const sent: unknown[] = [];
    server.use(
      http.post(`${API_BASE_URL}/notes/7/comments`, async ({ request }) => {
        const body = (await request.json()) as { block_id: string; quote: string; text: string };
        sent.push(body);
        return HttpResponse.json({
          id: "c_9", note_id: 7, block_id: body.block_id, quote: body.quote, status: "open",
          created_at: "2026-11-16T10:00:00Z",
          items: [{ id: 1, text: body.text, created_at: "2026-11-16T10:00:00Z" }],
        });
      }),
    );
    seed([blk("p1", { content: [{ type: "text", text: "Mine words", styles: {} }], text: "Mine words" })]);
    mount();
    const pm = document.querySelector(".ProseMirror") as PM;
    await screen.findByText("Mine words");
    act(() => {
      pm.editor.commands.setTextSelection({ from: 3, to: 7 }); // "Mine"
    });
    expect(screen.queryByTestId("comment-thread")).toBeNull();
    act(() => {
      fireEvent.keyDown(pm, { key: "M", keyCode: 77, ctrlKey: true, shiftKey: true });
    });
    const box = await screen.findByRole("textbox", { name: "Comment" });
    expect(screen.getByText("New comment")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "check this" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sent).toEqual([{ block_id: "p1", quote: "Mine", text: "check this" }]));
    const words = await waitFor(() => {
      const el = pm.querySelector<HTMLElement>('.note-comment[data-comment-id="c_9"]');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(words).toHaveTextContent("Mine");
    expect(useNotesStore.getState().notes[7]?.comments.map((c) => c.id)).toEqual(["c_9"]);
    // Esc closes the open thread; a click on the words opens it again.
    fireEvent.keyDown(await screen.findByRole("textbox", { name: "Comment" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("comment-thread")).toBeNull());
    fireEvent.click(words);
    expect(await screen.findByText("check this")).toBeInTheDocument();
  });

  it("a selection over two blocks comments the selected part of the first block only", async () => {
    const sent: { block_id: string; quote: string }[] = [];
    server.use(
      http.post(`${API_BASE_URL}/notes/7/comments`, async ({ request }) => {
        const body = (await request.json()) as { block_id: string; quote: string; text: string };
        sent.push(body);
        return HttpResponse.json({
          id: "c_8", note_id: 7, block_id: body.block_id, quote: body.quote, status: "open",
          created_at: "2026-11-16T10:00:00Z", items: [{ id: 1, text: body.text, created_at: "2026-11-16T10:00:00Z" }],
        });
      }),
    );
    seed([
      blk("p1", { content: [{ type: "text", text: "Mine words", styles: {} }], text: "Mine words" }),
      blk("p2", { content: [{ type: "text", text: "Second one", styles: {} }], text: "Second one" }),
    ]);
    mount();
    const pm = document.querySelector(".ProseMirror") as PM;
    await screen.findByText("Second one");
    act(() => {
      pm.editor.commands.setTextSelection({ from: 8, to: 22 }); // "words" .. into the second block
      fireEvent.keyDown(pm, { key: "M", keyCode: 77, ctrlKey: true, shiftKey: true });
    });
    const box = await screen.findByRole("textbox", { name: "Comment" });
    fireEvent.change(box, { target: { value: "hm" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sent).toEqual([expect.objectContaining({ block_id: "p1", quote: "words" })]));
    await waitFor(() => {
      const el = pm.querySelector<HTMLElement>('.note-comment[data-comment-id="c_8"]');
      expect(el).toHaveTextContent("words");
      expect(el).not.toHaveTextContent("Mine");
    });
  });

  it("a thread brought back after Delete opens again on a click on its words", async () => {
    const thread = {
      id: "c_5", note_id: 7, block_id: "p1", quote: "Mine", status: "open" as const,
      created_at: "2026-11-16T10:00:00Z", items: [{ id: 1, text: "back again", created_at: "2026-11-16T10:00:00Z" }],
    };
    server.use(
      http.patch(`${API_BASE_URL}/notes/comments/c_5`, async ({ request }) => {
        const { status } = (await request.json()) as { status: string };
        return HttpResponse.json({ ...thread, status });
      }),
    );
    seed([blk("p1", { content: [{ type: "text", text: "Mine", styles: { comment: "c_5" } }], text: "Mine" })]);
    useNotesStore.setState((st) => ({ notes: { ...st.notes, 7: { ...st.notes[7]!, comments: [thread] } } }));
    mount();
    const words = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('.note-comment[data-comment-id="c_5"]');
      expect(el).not.toBeNull();
      return el!;
    });
    await act(() => useNotesStore.getState().setCommentStatus(7, "c_5", "deleted"));
    fireEvent.click(words);
    expect(screen.queryByTestId("comment-thread")).toBeNull();
    await act(() => useNotesStore.getState().setCommentStatus(7, "c_5", "open"));
    fireEvent.click(words);
    expect(await screen.findByText("back again")).toBeInTheDocument();
  });

  it("Undo after Resolve puts the underline back on the same occurrence of a repeated quote", async () => {
    const thread = {
      id: "c_2", note_id: 7, block_id: "p1", quote: "foo", status: "open" as const,
      created_at: "2026-11-16T10:00:00Z", items: [{ id: 1, text: "this one", created_at: "2026-11-16T10:00:00Z" }],
    };
    server.use(
      http.patch(`${API_BASE_URL}/notes/comments/c_2`, async ({ request }) => {
        const { status } = (await request.json()) as { status: string };
        return HttpResponse.json({ ...thread, status });
      }),
      http.post(`${API_BASE_URL}/notes/7/ops`, () =>
        HttpResponse.json({ note_id: 7, revision: 5, change_set_id: "cs_5", applied: ["p1"], suggested: [], rejected: [], reparented: [] }),
      ),
    );
    const content = [
      { type: "text", text: "foo and ", styles: {} },
      { type: "text", text: "foo", styles: { comment: "c_2" } },
    ];
    seed([blk("p1", { content, text: "foo and foo" })]);
    useNotesStore.setState((st) => ({ notes: { ...st.notes, 7: { ...st.notes[7]!, comments: [thread] } } }));
    mount();
    const words = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('.note-comment[data-comment-id="c_2"]');
      expect(el).not.toBeNull();
      return el!;
    });
    fireEvent.click(words);
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(document.querySelector('.note-comment[data-comment-id="c_2"]')).toBeNull());
    const [, opts] = toastFn.mock.calls.at(-1) as [string, { action: { onClick: () => void } }];
    act(() => opts.action.onClick());
    await waitFor(() => {
      const runs = useNotesStore.getState().notes[7]!.blocks[0]!.content as { text: string; styles: Record<string, unknown> }[];
      expect(runs.map((r) => [r.text, r.styles.comment ?? null])).toEqual([
        ["foo and ", null],
        ["foo", "c_2"],
      ]);
    });
  });

  it("Ctrl+Shift+M with nothing selected does nothing", async () => {
    seed([blk("p1", { content: [{ type: "text", text: "Mine words", styles: {} }], text: "Mine words" })]);
    mount();
    const pm = document.querySelector(".ProseMirror") as PM;
    await screen.findByText("Mine words");
    act(() => {
      pm.editor.commands.setTextSelection({ from: 4, to: 4 });
      fireEvent.keyDown(pm, { key: "M", keyCode: 77, ctrlKey: true, shiftKey: true });
    });
    expect(screen.queryByTestId("comment-thread")).toBeNull();
  });
});

type PMView = {
  state: { doc: { content: { size: number } }; selection: { from: number; to: number }; tr: { insertText: (t: string, f: number, to: number) => unknown } };
  dispatch: (tr: unknown) => void;
  someProp: (name: string, f: (fn: (...a: unknown[]) => boolean) => boolean) => boolean | undefined;
};

describe("dividers, Focus and folding", () => {
  const d1 = blk("d1", { type: "divider" });
  const d2 = blk("d2", { type: "divider" });
  const q2 = blk("q2", { ...quote, id: "q2", props: { ...quote.props, markId: "mk_2" } });
  const p3 = blk("p3", { content: [{ type: "text", text: "Third", styles: {} }], text: "Third" });
  it("typing $$ at a line start puts the caret in the new equation's LaTeX", () => {
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
      pm.focus();
      pm.editor.commands.setTextSelection(3);
      for (const ch of "$$ ") {
        const { from, to } = view.state.selection;
        const handled = view.someProp("handleTextInput", (f) => f(view, from, to, ch, () => view.state.tr));
        if (!handled) view.dispatch(view.state.tr.insertText(ch, from, to));
      }
    });
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "LaTeX" }));
  });

  it("an empty equation already in a note does not take the caret", async () => {
    seed([blk("m0", { type: "math", props: { latex: "" } }), blk("p0", { content: [] })]);
    mount();
    await screen.findByRole("textbox", { name: "LaTeX" });
    expect(document.activeElement).not.toBe(screen.getByRole("textbox", { name: "LaTeX" }));
  });

  it("shows the formatting bar for selected words, not for a selected divider or equation", async () => {
    seed([
      blk("p0", { content: [{ type: "text", text: "Some words", styles: {} }], text: "Some words" }),
      blk("d0", { type: "divider" }),
      blk("m0", { type: "math", props: { latex: "x" } }),
    ]);
    // jsdom has no Range geometry; the bar is placed from it.
    const range = Range.prototype as unknown as Record<"getClientRects" | "getBoundingClientRect", unknown>;
    const had = { rects: range.getClientRects, box: range.getBoundingClientRect };
    range.getClientRects = () => [];
    range.getBoundingClientRect = () => new DOMRect();
    onTestFinished(() => {
      range.getClientRects = had.rects;
      range.getBoundingClientRect = had.box;
    });
    mount();
    const { NodeSelection, TextSelection } = await import("@tiptap/pm/state");
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { view: import("@tiptap/pm/view").EditorView };
    };
    const view = pm.editor.view;
    const posOf = (name: string) => {
      let at = -1;
      view.state.doc.descendants((n, pos) => {
        if (at < 0 && n.type.name === name) at = pos;
      });
      return at;
    };
    act(() => {
      pm.focus();
      view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, 3, 7)));
    });
    await waitFor(() => expect(document.querySelector('[data-test="bold"]')).not.toBeNull());
    for (const name of ["divider", "math"]) {
      act(() => view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, posOf(name)))));
      await waitFor(() => expect(document.querySelector('[data-test="bold"]')).toBeNull());
    }
  });

  const p4 = blk("p4", { content: [{ type: "text", text: "Fourth", styles: {} }], text: "Fourth" });
  const outer = (id: string) => document.querySelector(`.bn-block-outer[data-id="${id}"]`)!;

  beforeEach(() => {
    useNotesStore.setState({ activePart: {}, focusOn: false, folded: {} });
  });

  it("typing --- makes a divider at once; Undo turns it back into ---, then offers Make a divider again", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor([dividerRule]);
    type(editor, "---");
    expect(editor.document[0]!.type).toBe("divider");
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    const [msg, opts] = toastFn.mock.calls[0] as [string, { action: { label: string; onClick: () => void } }];
    expect(msg).toBe("Divider added");
    expect(opts.action.label).toBe("Undo");
    act(() => opts.action.onClick());
    const back = editor.document[0] as unknown as { type: string; content: { text: string }[] };
    expect(back.type).toBe("paragraph");
    expect(back.content.map((c) => c.text).join("")).toBe("---");
    const [msg2, opts2] = toastFn.mock.calls[1] as [string, { action: { label: string; onClick: () => void } }];
    expect(msg2).toBe("Back to ---");
    expect(opts2.action.label).toBe("Make a divider again");
    act(() => opts2.action.onClick());
    expect(editor.document[0]!.type).toBe("divider");
    expect((toastFn.mock.calls[2] as [string])[0]).toBe("Divider added");
    cleanupEditors();
  });

  it("--- puts the caret on a new line below the divider; Undo takes that line away again", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor(
      [dividerRule],
      [
        { id: "x", type: "paragraph", content: "" },
        { id: "y", type: "paragraph", content: "after" },
      ],
    );
    editor.setTextCursorPosition("x", "start");
    type(editor, "---");
    await waitFor(() => expect(toastFn).toHaveBeenCalledTimes(1));
    const lines = () =>
      (editor.document as unknown as { type: string; content?: { text: string }[] }[]).map((b) => [
        b.type,
        (b.content ?? []).map((c) => c.text).join(""),
      ]);
    type(editor, "z");
    expect(lines().slice(0, 3)).toEqual([
      ["divider", ""],
      ["paragraph", "z"],
      ["paragraph", "after"],
    ]);
    // A new line left empty goes with Undo.
    editor.updateBlock(editor.document[1]!, { content: [] });
    const undo = (toastFn.mock.calls[0] as [string, { action: { onClick: () => void } }])[1];
    act(() => undo.action.onClick());
    expect(lines().slice(0, 2)).toEqual([
      ["paragraph", "---"],
      ["paragraph", "after"],
    ]);
    cleanupEditors();
  });

  it("--- right above an empty line moves the caret onto it", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor(
      [dividerRule],
      [
        { id: "x", type: "paragraph", content: "" },
        { id: "e", type: "paragraph", content: "" },
        { id: "y", type: "paragraph", content: "after" },
      ],
    );
    editor.setTextCursorPosition("x", "start");
    type(editor, "---");
    await waitFor(() => expect(toastFn).toHaveBeenCalled());
    expect(editor.document.map((b) => b.id).slice(0, 3)).toEqual(["x", "e", "y"]);
    expect(editor.getTextCursorPosition().block.id).toBe("e");
    cleanupEditors();
  });

  it("dividers are always top level: --- in a nested list item lands after the list", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor(
      [dividerRule],
      [
        {
          id: "li1",
          type: "bulletListItem",
          content: "a",
          children: [{ id: "li2", type: "bulletListItem", content: "b" }, { id: "li3", type: "bulletListItem", content: "" }],
        },
        { id: "after", type: "paragraph", content: "after" },
      ],
    );
    editor.setTextCursorPosition("li3", "start");
    type(editor, "---");
    const shape = (list: { id: string; type: string; children: unknown[] }[]): unknown[] =>
      list.map((b) => [b.type, shape(b.children as never)]);
    expect(shape(editor.document as never)).toEqual([
      ["bulletListItem", [["bulletListItem", []]]],
      ["divider", []],
      ["paragraph", []],
    ]);
    // The only child moved out too: no empty nest is left behind.
    editor.setTextCursorPosition("li2", "start");
    editor.updateBlock("li2", { type: "divider" } as never);
    expect(shape(editor.document as never)).toEqual([
      ["bulletListItem", []],
      ["divider", []],
      ["divider", []],
      ["paragraph", []],
    ]);
    cleanupEditors();
  });

  it("Undo after a lifted --- puts the list item back where it was; Make a divider again lifts it again", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor(
      [dividerRule],
      [
        {
          id: "li1",
          type: "bulletListItem",
          content: "a",
          children: [
            { id: "li2", type: "bulletListItem", content: "b" },
            { id: "li3", type: "checkListItem", props: { checked: true }, content: "" },
            { id: "li4", type: "bulletListItem", content: "d" },
          ],
        },
      ],
    );
    const tree = () => JSON.parse(JSON.stringify(editor.document)) as unknown;
    editor.setTextCursorPosition("li3", "start");
    type(editor, "---");
    expect(editor.document.map((b) => b.type)).toEqual(["bulletListItem", "divider"]);
    await waitFor(() => expect(toastFn).toHaveBeenCalled());
    const undo = (toastFn.mock.calls.at(-1) as [string, { action: { onClick: () => void } }])[1];
    act(() => undo.action.onClick());
    // The same document as before the last dash: the to-do back in its place, with ---.
    const restored = tree() as { children: { id: string; type: string; props: { checked: boolean }; content: { text: string }[] }[] }[];
    expect(restored.map((b) => (b as never as { type: string }).type)).toEqual(["bulletListItem"]);
    const li3 = restored[0]!.children[1]!;
    expect([li3.id, li3.type, li3.props.checked, li3.content.map((c) => c.text).join("")]).toEqual([
      "li3", "checkListItem", true, "---",
    ]);
    expect(restored[0]!.children.map((c) => c.id)).toEqual(["li2", "li3", "li4"]);
    const again = (toastFn.mock.calls.at(-1) as [string, { action: { label: string; onClick: () => void } }])[1];
    expect(again.action.label).toBe("Make a divider again");
    act(() => again.action.onClick());
    expect(editor.document.map((b) => b.type)).toEqual(["bulletListItem", "divider"]);
    cleanupEditors();
  });

  it("--- before other words on the line stays text", async () => {
    const { cleanupEditors, makeEditor, type } = await import("./testEditor");
    const { dividerRule } = await import("./inlineMarks");
    const editor = makeEditor([dividerRule], [{ type: "paragraph", content: "keep" }]);
    type(editor, "---");
    expect(editor.document[0]!.type).toBe("paragraph");
    cleanupEditors();
  });

  it("Focus fades the other parts (headings and dividers stay) and the focused part's marks are exposed", async () => {
    seed([section, quote, d1, q2, p3]);
    useNotesStore.setState({ activePart: { 7: "d1" }, focusOn: true });
    mount();
    await waitFor(() => expect(outer("q1")).toHaveAttribute("data-fade", "true"));
    expect(outer("s1")).not.toHaveAttribute("data-fade");
    expect(outer("d1")).not.toHaveAttribute("data-fade");
    expect(outer("q2")).not.toHaveAttribute("data-fade");
    expect(outer("p3")).not.toHaveAttribute("data-fade");
    const { focusedMarkIds } = await import("./parts");
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toEqual(["mk_2"]);
    // Moving the focus to the first part swaps the fade.
    act(() => useNotesStore.getState().setActivePart(7, "s1"));
    await waitFor(() => expect(outer("q2")).toHaveAttribute("data-fade", "true"));
    expect(outer("q1")).not.toHaveAttribute("data-fade");
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toEqual(["mk_1"]);
    act(() => useNotesStore.getState().setFocusOn(false));
    await waitFor(() => expect(outer("q2")).not.toHaveAttribute("data-fade"));
    expect(focusedMarkIds(useNotesStore.getState(), 7)).toBeNull();
  });

  it("the caret makes its part the active one", async () => {
    seed([section, quote, d1, q2, p3]);
    mount();
    await screen.findByText("Third");
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { commands: { setTextSelection: (pos: number) => boolean }; state: { doc: { content: { size: number } } } };
    };
    act(() => {
      pm.editor.commands.setTextSelection(pm.editor.state.doc.content.size - 4);
    });
    await waitFor(() => expect(useNotesStore.getState().activePart[7]).toBe("d1"));
  });

  it("darkens the dividers around the active part", async () => {
    seed([section, quote, d1, q2, d2, p4]);
    useNotesStore.setState({ activePart: { 7: "d1" } });
    mount();
    const rule = (id: string) => screen.getByTestId(`divider-${id}`).querySelector("hr")!;
    await waitFor(() => expect(rule("d1")).toHaveAttribute("data-edge", "true"));
    expect(rule("d2")).toHaveAttribute("data-edge", "true");
    act(() => useNotesStore.getState().setActivePart(7, "s1"));
    expect(rule("d1")).toHaveAttribute("data-edge", "true");
    expect(rule("d2")).not.toHaveAttribute("data-edge");
  });

  it("a divider just typed frames the part and counts its blocks from the editor, before the store has it", async () => {
    const pe = blk("pe", { content: [] });
    seed([section, quote, d1, q2, p3, pe]);
    // The sync has not landed: the store keeps the line as it was.
    const applyLocal = useNotesStore.getState().applyLocal;
    useNotesStore.setState({ activePart: { 7: "d1" }, applyLocal: () => {} });
    onTestFinished(() => {
      useNotesStore.setState({ applyLocal });
    });
    mount();
    await screen.findByText("Third");
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { view: PMView; commands: { setTextSelection: (pos: number) => boolean } };
    };
    const view = pm.editor.view;
    act(() => {
      pm.editor.commands.setTextSelection(view.state.doc.content.size - 3);
      for (const ch of "---") {
        const { from, to } = view.state.selection;
        const handled = view.someProp("handleTextInput", (f) => f(view, from, to, ch, () => view.state.tr));
        if (!handled) view.dispatch(view.state.tr.insertText(ch, from, to));
      }
    });
    const rule = (await screen.findByTestId("divider-pe")).querySelector("hr")!;
    expect(rule).toHaveAttribute("data-edge", "true");
    // d1's part (q2, p3) as the editor has it.
    act(() => useNotesStore.getState().toggleFold(7, "d1"));
    expect(within(screen.getByTestId("divider-d1")).getByRole("button", { name: "2 blocks · 1 mark" })).toBeInTheDocument();
  });

  it.each([
    ["en", "2 blocks · 1 mark", "1 block", "1 block · 3 marks"],
    ["zh-TW", "2 個區塊 · 1 個標記", "1 個區塊", "1 個區塊 · 3 個標記"],
    ["zh-CN", "2 个区块 · 1 个标记", "1 个区块", "1 个区块 · 3 个标记"],
    ["ja", "2 ブロック · 1 マーク", "1 ブロック", "1 ブロック · 3 マーク"],
  ])("the fold line is one translated sentence (%s)", async (lng, both, blocksOnly, plural) => {
    const { foldLine } = await import("./parts");
    const t = i18n.getFixedT(lng);
    expect(foldLine(t, { blocks: 2, marks: 1 })).toBe(both);
    expect(foldLine(t, { blocks: 1, marks: 0 })).toBe(blocksOnly);
    expect(foldLine(t, { blocks: 1, marks: 3 })).toBe(plural);
  });

  it("the caret never lands in a folded part: moving down skips past it, moving up stops at its divider", async () => {
    seed([section, quote, d1, q2, p3, d2, p4]);
    useNotesStore.setState({ folded: { 7: ["d1"] } });
    mount();
    await screen.findByText("Fourth");
    type Node = { attrs: { id?: string }; type: { name: string }; nodeSize: number };
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: {
        state: {
          doc: { descendants: (f: (n: Node, pos: number) => boolean | void) => void };
          selection: { from: number; $from: { depth: number; node: (d: number) => Node } };
        };
        commands: { setTextSelection: (pos: number) => boolean };
      };
    };
    const posIn = (id: string) => {
      let at = -1;
      pm.editor.state.doc.descendants((n, pos) => {
        if (n.type.name === "blockContainer" && n.attrs.id === id) at = pos + 2;
        return at < 0;
      });
      return at;
    };
    const blockAtCaret = () => {
      const $f = pm.editor.state.selection.$from;
      for (let d = $f.depth; d > 0; d--) {
        const n = $f.node(d);
        if (n.type.name === "blockContainer") return n.attrs.id;
      }
      return null;
    };
    // Down from the first part: past the folded q2 / p3.
    act(() => {
      pm.editor.commands.setTextSelection(posIn("s1"));
    });
    act(() => {
      pm.editor.commands.setTextSelection(posIn("p3"));
    });
    expect(["d2", "p4"]).toContain(blockAtCaret());
    // Up from the last part: onto the folded part's divider.
    act(() => {
      pm.editor.commands.setTextSelection(posIn("p4"));
    });
    act(() => {
      pm.editor.commands.setTextSelection(posIn("p3"));
    });
    expect(["d1", "q1", "s1"]).toContain(blockAtCaret());
    // Unfolded, the caret goes in.
    act(() => useNotesStore.getState().toggleFold(7, "d1"));
    act(() => {
      pm.editor.commands.setTextSelection(posIn("p3"));
    });
    expect(blockAtCaret()).toBe("p3");
  });

  it("forgets a fold when its divider is deleted or turned back into ---", async () => {
    const dx = blk("dx", { type: "divider" });
    seed([section, quote, d1, q2, dx, p3]);
    useNotesStore.setState({ folded: { 7: ["d1", "dx"] } });
    mount();
    await screen.findByTestId("divider-dx");
    type Node = { attrs: { id?: string }; type: { name: string }; nodeSize: number };
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: {
        view: {
          state: {
            doc: { descendants: (f: (n: Node, pos: number) => boolean | void) => void };
            tr: { delete: (a: number, b: number) => unknown };
          };
          dispatch: (tr: unknown) => void;
        };
      };
    };
    const view = pm.editor.view;
    let range: [number, number] | null = null;
    view.state.doc.descendants((n, pos) => {
      if (n.type.name === "blockContainer" && n.attrs.id === "dx") range = [pos, pos + n.nodeSize];
      return range === null;
    });
    act(() => view.dispatch(view.state.tr.delete(range![0], range![1])));
    await waitFor(() => expect(useNotesStore.getState().folded[7]).toEqual(["d1"]));

    // d1 turned back into the --- line (what the toast's Undo does): fold gone.
    type View = {
      state: {
        doc: { descendants: (f: (n: Node, pos: number) => boolean | void) => void };
        schema: { nodes: { paragraph: { create: (a: null, c: unknown) => unknown } }; text: (t: string) => unknown };
        tr: { replaceWith: (a: number, b: number, n: unknown) => unknown };
      };
      dispatch: (tr: unknown) => void;
    };
    const v = view as unknown as View;
    let content: [number, number] | null = null;
    v.state.doc.descendants((n, pos) => {
      if (n.type.name === "divider") {
        content = [pos, pos + n.nodeSize];
        return false;
      }
      return content === null;
    });
    const para = v.state.schema.nodes.paragraph.create(null, v.state.schema.text("---"));
    act(() => v.dispatch(v.state.tr.replaceWith(content![0], content![1], para)));
    await waitFor(() => expect(useNotesStore.getState().folded[7]).toEqual([]));
  });

  it("Esc that closes the slash menu leaves Focus on; a bare Esc in the note ends it", async () => {
    const { NotesToolbar } = await import("./NotesToolbar");
    seed([section, blk("p0", { content: [] })]);
    useNotesStore.setState({ focusOn: true, activePart: { 7: "s1" } });
    // jsdom's rects lack toJSON, which the menu's positioning reads.
    const rect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(rect);
    if (!("getBoundingClientRect" in Range.prototype)) {
      Object.defineProperty(Range.prototype, "getBoundingClientRect", { configurable: true, value: rect });
      Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: () => [] });
    }
    act(() => {
      render(
        <>
          <NotesToolbar noteId={7} followLabel={null} guideOpen={false} onGuideOpenChange={() => {}} />
          <NoteEditor noteId={7} variant="pane" />
        </>,
      );
    });
    await screen.findByTestId("note-editor");
    const pm = document.querySelector(".ProseMirror") as HTMLElement & {
      editor: { view: PMView; commands: { setTextSelection: (pos: number) => boolean; focus: () => boolean } };
    };
    const view = pm.editor.view;
    act(() => {
      pm.editor.commands.focus();
      pm.editor.commands.setTextSelection(view.state.doc.content.size - 3);
      const { from, to } = view.state.selection;
      const handled = view.someProp("handleTextInput", (f) => f(view, from, to, "/", () => view.state.tr));
      if (!handled) view.dispatch(view.state.tr.insertText("/", from, to));
    });
    await screen.findByText("Callout");
    fireEvent.keyDown(pm, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("Callout")).toBeNull());
    expect(useNotesStore.getState().focusOn).toBe(true);
    fireEvent.keyDown(pm, { key: "Escape" });
    expect(useNotesStore.getState().focusOn).toBe(false);
  });

  it("the chevron sits above BlockNote's side menu (z-20), whose drag handle it overlaps", async () => {
    seed([section, quote, d1, q2]);
    mount();
    const fold = within(await screen.findByTestId("divider-d1")).getByRole("button", {
      name: "Fold the part below this divider",
    });
    expect(fold.className.split(" ")).toEqual(expect.arrayContaining(["relative", "z-[21]"]));
  });

  it("the chevron folds the part below to a line that unfolds it, and the fold is kept", async () => {
    seed([section, quote, d1, q2, p3, d2, p4]);
    mount();
    const fold = within(await screen.findByTestId("divider-d1")).getByRole("button", {
      name: "Fold the part below this divider",
    });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(fold);
    expect(useNotesStore.getState().folded[7]).toEqual(["d1"]);
    expect(fold).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(outer("q2")).toHaveAttribute("data-folded", "true"));
    expect(outer("p3")).toHaveAttribute("data-folded", "true");
    expect(outer("p4")).not.toHaveAttribute("data-folded");
    const line = within(screen.getByTestId("divider-d1")).getByRole("button", { name: "2 blocks · 1 mark" });
    const saved = JSON.parse(localStorage.getItem("paperhub-notes-v1") ?? "{}") as { state: { folded: unknown } };
    expect(saved.state.folded).toEqual({ 7: ["d1"] });
    fireEvent.click(line);
    await waitFor(() => expect(outer("q2")).not.toHaveAttribute("data-folded"));
    expect(useNotesStore.getState().folded[7]).toEqual([]);
  });
});
