import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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
