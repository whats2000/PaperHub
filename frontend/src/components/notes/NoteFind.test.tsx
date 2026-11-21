import { act, fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { findMatches } from "@/components/notes/noteFind";
import { cleanupEditors, makeEditor } from "@/components/notes/testEditor";
import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();

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

const para = (id: string, text: string) => blk(id, { content: [{ type: "text", text, styles: {} }], text });

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

type PMNode = { attrs: Record<string, unknown>; type: { name: string }; nodeSize: number };
type PM = HTMLElement & {
  editor: {
    state: {
      doc: { descendants: (f: (n: PMNode, pos: number) => boolean | void) => void; textBetween: (a: number, b: number, s?: string, l?: string) => string; nodeAt: (p: number) => PMNode | null };
      selection: { from: number; to: number; node?: PMNode; $from: { depth: number; node: (d: number) => PMNode } };
    };
    commands: { setTextSelection: (pos: number) => boolean };
  };
};

function mount() {
  act(() => {
    render(
      <div>
        <button>outside</button>
        <NoteEditor noteId={7} variant="pane" />
      </div>,
    );
  });
  return document.querySelector(".ProseMirror") as PM;
}

const ctrlF = (el: Element) => fireEvent.keyDown(el, { key: "f", code: "KeyF", ctrlKey: true });
const findBox = () => screen.getByRole("textbox", { name: "Find in the note" });
const count = () => screen.getByTestId("note-find-count").textContent;
function typeQuery(q: string) {
  act(() => {
    fireEvent.change(findBox(), { target: { value: q } });
  });
}
const selected = (pm: PM) => {
  const { from, to } = pm.editor.state.selection;
  return pm.editor.state.doc.textBetween(from, to, " ", "\u0000");
};
const blockAtCaret = (pm: PM) => {
  const $f = pm.editor.state.selection.$from;
  for (let d = $f.depth; d > 0; d--) {
    const n = $f.node(d);
    if (n.type.name === "blockContainer") return n.attrs.id;
  }
  return null;
};

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  clearPendingNoteOps();
  server.resetHandlers();
  cleanupEditors();
});
beforeEach(() => {
  useNotesStore.setState({ colorNames: {}, retrying: {}, drafts: {}, folded: {}, focusOn: false, activePart: {} });
  server.use(http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])));
});

describe("find in a note", () => {
  it("Ctrl+F in the editor opens the find bar; Esc slides it out and leaves the caret on the current match", async () => {
    seed([para("p1", "alpha beta"), para("p2", "gamma alpha")]);
    const pm = mount();
    await screen.findByText("gamma alpha");
    act(() => {
      pm.focus();
      pm.editor.commands.setTextSelection(2);
    });
    let notPrevented = true;
    act(() => {
      notPrevented = ctrlF(pm);
    });
    expect(notPrevented).toBe(false);
    expect(document.activeElement).toBe(findBox());
    const bar = screen.getByTestId("note-find");
    expect(bar.dataset.state).toBe("open");
    expect(bar.className).toContain("slide-in-from-top");
    typeQuery("alpha");
    expect(count()).toBe("1 / 2");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Enter" });
    });
    expect(count()).toBe("2 / 2");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Escape" });
    });
    // It slides back up (the exit class), then is gone.
    expect(bar.dataset.state).toBe("closed");
    expect(bar.className).toContain("slide-out-to-top");
    act(() => {
      fireEvent.animationEnd(bar);
    });
    expect(screen.queryByTestId("note-find")).toBeNull();
    expect(blockAtCaret(pm)).toBe("p2");
    expect(selected(pm)).toBe("alpha");
  });

  it("counts case-insensitively, wraps with Enter / Shift+Enter / F3, and highlights every match", async () => {
    seed([para("p1", "Alpha alpha"), para("p2", "ALPHA")]);
    const pm = mount();
    await screen.findByText("ALPHA");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("alpha");
    expect(count()).toBe("1 / 3");
    expect(document.querySelectorAll(".ph-find").length).toBe(3);
    expect(document.querySelectorAll(".ph-find-current").length).toBe(1);
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Enter", shiftKey: true });
    });
    expect(count()).toBe("3 / 3");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "F3" });
    });
    expect(count()).toBe("1 / 3");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "F3", shiftKey: true });
    });
    expect(count()).toBe("3 / 3");
    typeQuery("zzz");
    expect(count()).toBe("0 / 0");
    expect(document.querySelectorAll(".ph-find").length).toBe(0);
  });

  it("finds CJK text", async () => {
    seed([para("p1", "注意力機制很重要"), para("p2", "自注意力")]);
    const pm = mount();
    await screen.findByText("自注意力");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("注意力");
    expect(count()).toBe("1 / 2");
  });

  it("unfolds a folded part when its match becomes the current one", async () => {
    const d1 = blk("d1", { type: "divider" });
    seed([para("p1", "first needle"), d1, para("p2", "hidden needle")]);
    useNotesStore.setState({ folded: { 7: ["d1"] } });
    const pm = mount();
    await screen.findByText("hidden needle");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("needle");
    expect(count()).toBe("1 / 2");
    expect(useNotesStore.getState().folded[7]).toEqual(["d1"]);
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Enter" });
    });
    expect(useNotesStore.getState().folded[7] ?? []).toEqual([]);
    expect(blockAtCaret(pm)).toBe("p2");
  });

  it("matches an inline equation's LaTeX and a mention's label as one unit", async () => {
    seed([
      blk("p1", {
        content: [
          { type: "text", text: "see ", styles: {} },
          { type: "inlineMath", props: { latex: "x^2" } },
          { type: "text", text: " and ", styles: {} },
          { type: "mention", props: { kind: "paper", paperId: 3, sectionKey: "", label: "Transformer" } },
        ] as never,
        text: "see x^2 and Transformer",
      }),
    ]);
    const pm = mount();
    await screen.findByText("Transformer");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("x^");
    expect(count()).toBe("1 / 1");
    const sel = pm.editor.state.selection;
    expect(sel.node?.type.name).toBe("inlineMath");
    typeQuery("former");
    expect(count()).toBe("1 / 1");
    expect(pm.editor.state.selection.node?.type.name).toBe("mention");
  });

  it("matches a reference block's quote and puts the atom caret on it", async () => {
    seed([para("p1", "intro"), quote]);
    const pm = mount();
    await screen.findByText("intro");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("all you");
    expect(count()).toBe("1 / 1");
    expect(pm.editor.state.selection.node?.type.name).toBe("paperQuote");
    expect(document.querySelector("[data-atom-caret]")).not.toBeNull();
  });

  it("leaves Ctrl+F outside the editor to the browser", async () => {
    seed([para("p1", "alpha")]);
    mount();
    await screen.findByText("alpha");
    const outside = screen.getByRole("button", { name: "outside" });
    let notPrevented = false;
    act(() => {
      notPrevented = ctrlF(outside);
    });
    expect(notPrevented).toBe(true);
    expect(screen.queryByTestId("note-find")).toBeNull();
  });
  it("finds text inside a table cell and Enter selects it, with the table's block id", async () => {
    seed([
      para("p1", "needle first"),
      blk("t", {
        type: "table",
        content: {
          type: "tableContent",
          columnWidths: [null, null],
          headerRows: 1,
          rows: [{ cells: ["head", "x"] }, { cells: ["a needle", "b"] }],
        } as never,
        text: "head | x\na needle | b",
      }),
    ]);
    const pm = mount();
    await screen.findByText("needle first");
    act(() => {
      ctrlF(pm);
    });
    typeQuery("needle");
    expect(count()).toBe("1 / 2");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Enter" });
    });
    expect(count()).toBe("2 / 2");
    act(() => {
      fireEvent.keyDown(findBox(), { key: "Escape" });
    });
    act(() => {
      fireEvent.animationEnd(screen.getByTestId("note-find"));
    });
    expect(selected(pm)).toBe("needle");
    expect(blockAtCaret(pm)).toBe("t");
  });
});

describe("findMatches in a table", () => {
  it("gives a match inside a cell the table block's id", () => {
    const editor = makeEditor([], [
      { id: "p", type: "paragraph", content: "needle" },
      {
        id: "t",
        type: "table",
        content: { type: "tableContent", columnWidths: [null], headerRows: 1, rows: [{ cells: ["h"] }, { cells: ["a needle"] }] },
      },
    ]);
    const hits = findMatches(editor.prosemirrorState.doc, "needle");
    expect(hits.map((h) => h.blockId)).toEqual(["p", "t"]);
  });
});
