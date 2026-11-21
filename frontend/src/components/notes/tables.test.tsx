import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { EditorView } from "@tiptap/pm/view";

import { NoteEditor } from "@/components/notes/NoteEditor";
import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { NoteBlock, TableContent } from "@/types/notes";

import { cleanPastedTable, notePasteHandler, tableKeys } from "./tableKeys";
import { tableMotion } from "./tableMotion";
import { cleanupEditors, makeEditor, press, type AnyEditor } from "./testEditor";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

// jsdom has no ClipboardEvent; ProseMirror's pasteHTML makes one.
if (!("ClipboardEvent" in globalThis)) {
  Object.defineProperty(globalThis, "ClipboardEvent", {
    configurable: true,
    value: class extends Event {
      clipboardData = null;
    },
  });
}

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterAll(() => server.close());
afterEach(() => {
  cleanupEditors();
  clearPendingNoteOps();
  server.resetHandlers();
  vi.restoreAllMocks();
});
beforeEach(() => {
  server.use(http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])));
  // jsdom's rects lack toJSON, which the floating menus' positioning reads.
  const rect = () =>
    ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(rect);
  if (!("getBoundingClientRect" in Range.prototype)) {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", { configurable: true, value: rect });
    Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: () => [] });
  }
  // jsdom has no hit testing; the side menu asks for it on mouse moves.
  if (!document.elementFromPoint) {
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => null });
    Object.defineProperty(document, "elementsFromPoint", { configurable: true, value: () => [] });
  }
});

type Cell = { type: "tableCell"; props: Record<string, unknown>; content: { text?: string; styles?: Record<string, unknown> }[] };
type Table = { type: "tableContent"; headerRows?: number; headerCols?: number; columnWidths: unknown[]; rows: { cells: Cell[] }[] };

const tableOf = (editor: AnyEditor) =>
  editor.document.find((b) => b.type === "table")?.content as unknown as Table | undefined;
const texts = (t: Table) => t.rows.map((r) => r.cells.map((c) => c.content.map((x) => x.text ?? "").join("")));

function grid(): unknown[] {
  return [
    {
      id: "t",
      type: "table",
      content: {
        type: "tableContent",
        columnWidths: [null, null],
        headerRows: 1,
        rows: [{ cells: ["a", "b"] }, { cells: ["1", "2"] }],
      },
    },
    { id: "after", type: "paragraph", content: "after" },
  ];
}

/** The caret into a cell's text (row r, column c) of the first table. */
function caretInCell(editor: AnyEditor, r: number, c: number) {
  const view = editor._tiptapEditor.view;
  let pos = -1;
  let row = -1;
  view.state.doc.descendants((node, p) => {
    if (pos >= 0) return false;
    if (node.type.name === "tableRow") {
      row += 1;
      if (row !== r) return false;
      let at = p + 1;
      for (let i = 0; i < c; i++) at += node.child(i).nodeSize;
      pos = at + 2; // into the cell, then into its paragraph
      return false;
    }
    return true;
  });
  editor._tiptapEditor.commands.setTextSelection(pos);
}

/** Row and column of the caret's cell. */
function caretCell(view: EditorView): [number, number] {
  const $from = view.state.selection.$from;
  for (let d = $from.depth; d > 0; d--) {
    if ($from.node(d).type.name === "tableRow") return [$from.index(d - 1), $from.index(d)];
  }
  return [-1, -1];
}

function paste(editor: AnyEditor, data: Record<string, string>) {
  const ev = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
  ev.clipboardData = { types: Object.keys(data), getData: (t: string) => data[t] ?? "" };
  editor._tiptapEditor.view.dom.dispatchEvent(ev);
}

describe("Tab in a table", () => {
  it("Tab in the last cell adds a row and puts the caret in its first cell; Shift+Tab moves back", () => {
    const editor = makeEditor([tableKeys], grid());
    caretInCell(editor, 1, 1);
    press(editor, { key: "Tab" });
    const t = tableOf(editor)!;
    expect(t.rows).toHaveLength(3);
    expect(texts(t)[2]).toEqual(["", ""]);
    expect(caretCell(editor._tiptapEditor.view)).toEqual([2, 0]);
    press(editor, { key: "Tab", shiftKey: true });
    expect(caretCell(editor._tiptapEditor.view)).toEqual([1, 1]);
    expect(tableOf(editor)!.rows).toHaveLength(3);
  });

  it("Tab in another cell just moves to the next one", () => {
    const editor = makeEditor([tableKeys], grid());
    caretInCell(editor, 0, 1);
    press(editor, { key: "Tab" });
    expect(caretCell(editor._tiptapEditor.view)).toEqual([1, 0]);
    expect(tableOf(editor)!.rows).toHaveLength(2);
  });
});

describe("pasting a table", () => {
  it("strips cell colours and merged cells from pasted HTML and keeps a header row", () => {
    const editor = makeEditor([], [{ id: "p", type: "paragraph" }], { pasteHandler: notePasteHandler });
    paste(editor, {
      "text/html":
        "<table><tr><td colspan='2' style='background-color: rgb(255, 0, 0)'><span style='color: red'>wide</span></td></tr>" +
        "<tr><td>x</td><td>y</td></tr></table>",
    });
    const t = tableOf(editor)!;
    expect(t).toBeDefined();
    expect(t.headerRows).toBe(1);
    for (const row of t.rows) {
      expect(row.cells).toHaveLength(2);
      for (const cell of row.cells) {
        expect(cell.props.backgroundColor ?? "default").toBe("default");
        expect(cell.props.textColor ?? "default").toBe("default");
        expect(cell.props.colspan ?? 1).toBe(1);
        expect(cell.props.rowspan ?? 1).toBe(1);
        for (const run of cell.content) expect(run.styles?.textColor).toBeUndefined();
      }
    }
    expect(texts(t)).toEqual([["wide", ""], ["x", "y"]]);
  });

  it("turns pasted GFM text into a table with the delimiter row's alignment", () => {
    const editor = makeEditor([], [{ id: "p", type: "paragraph" }], { pasteHandler: notePasteHandler });
    paste(editor, { "text/plain": "| a | b |\n|---|--:|\n| 1 | 2 |" });
    const t = tableOf(editor)!;
    expect(texts(t)).toEqual([["a", "b"], ["1", "2"]]);
    expect(t.headerRows).toBe(1);
    expect(t.rows.map((r) => r.cells[1]!.props.textAlignment)).toEqual(["right", "right"]);
  });

  it("cleanPastedTable spreads a merged cell over empty cells and adds a header row", () => {
    const content: TableContent = {
      type: "tableContent",
      columnWidths: [null],
      rows: [
        { cells: [{ type: "tableCell", props: { colspan: 2, backgroundColor: "red", textAlignment: "center" }, content: "wide" }] },
        { cells: [{ type: "tableCell", props: { rowspan: 2 }, content: "tall" }, "b"] },
        { cells: ["c"] },
      ],
    };
    const out = cleanPastedTable(content);
    expect(out.headerRows).toBe(1);
    expect(out.columnWidths).toEqual([null, null]);
    expect(out.rows.map((r) => r.cells.length)).toEqual([2, 2, 2]);
    const first = out.rows[0]!.cells[0] as { props: Record<string, unknown> };
    expect(first.props).toEqual({ textAlignment: "center" });
    const third = out.rows[2]!.cells as { content: unknown }[];
    expect(third[0]!.content).toEqual([]);
    expect(third[1]!.content).toEqual("c");
  });
});

// ---------------------------------------------------------------- the editor

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

function seed(blocks: NoteBlock[]) {
  useNotesStore.setState({
    notes: {
      7: {
        note: { id: 7, target_kind: "session", target_id: "1", title: "T", revision: 1, updated_at: "2026-11-22T00:00:00Z" },
        blocks,
        suggestions: [],
        comments: [],
        revision: 1,
      },
    },
    byTarget: { "session:1": 7 },
    reloads: {},
    colorNames: {},
    retrying: {},
    drafts: {},
  });
}

const seededTable = blk("t", {
  type: "table",
  content: {
    type: "tableContent",
    columnWidths: [null, null],
    headerRows: 1,
    rows: [{ cells: ["a", "b"] }, { cells: ["1", "2"] }],
  },
  text: "a | b\n1 | 2",
});

type PM = HTMLElement & { editor: { view: EditorView; commands: { focus: () => boolean; setTextSelection: (p: number) => boolean } } };

function mountEditor() {
  act(() => {
    render(<NoteEditor noteId={7} variant="pane" />);
  });
  return document.querySelector(".ProseMirror") as PM;
}

/** jsdom has no layout, so floating-ui hides the popovers (visibility):
 * they are queried as hidden ones. */
const HIDDEN = { hidden: true } as const;

/** The table's ⋯ button. */
function tableOptions(): HTMLElement {
  const el = document.querySelector<HTMLElement>("[aria-label='Table options']");
  if (!el) throw new Error("no Table options button");
  return el;
}

/** Hover a cell so the table's handles show. */
function hoverCell(pm: PM, r: number, c: number) {
  const cell = pm.querySelectorAll("tr")[r]!.children[c]!;
  act(() => {
    fireEvent.mouseMove(cell);
  });
}

describe("tables in the note editor", () => {
  it("/table inserts a 3 × 3 table whose first row is a header", async () => {
    seed([blk("p0", { content: [] })]);
    const pm = mountEditor();
    const view = pm.editor.view;
    act(() => {
      pm.editor.commands.focus();
      pm.editor.commands.setTextSelection(3);
      for (const ch of "/table") {
        const { from, to } = view.state.selection;
        const handled = view.someProp("handleTextInput", (f) => f(view, from, to, ch, () => view.state.tr));
        if (!handled) view.dispatch(view.state.tr.insertText(ch, from, to));
      }
    });
    const item = await screen.findByText("Table");
    act(() => {
      fireEvent.click(item);
    });
    await waitFor(() => expect(pm.querySelectorAll("tr")).toHaveLength(3));
    const rows = [...pm.querySelectorAll("tr")];
    expect(rows.map((r) => r.children.length)).toEqual([3, 3, 3]);
    expect(rows[0]!.querySelectorAll("th")).toHaveLength(3);
    expect(rows[1]!.querySelectorAll("th")).toHaveLength(0);
  });

  it("the column handle menu offers insert, alignment and delete, and no colour", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    const handle = await screen.findByRole("button", { name: "Column options" });
    act(() => {
      fireEvent.click(handle);
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    const names = [...menu.querySelectorAll("[role^=menuitem]")].map((e) => e.textContent?.trim());
    expect(names).toEqual(["Insert left", "Insert right", "Align left", "Align centre", "Align right", "Delete column"]);
    expect(within(menu).queryByText(/colou?r/i)).toBeNull();
    act(() => {
      fireEvent.click(within(menu).getByText("Align right"));
    });
    await waitFor(() =>
      expect([...pm.querySelectorAll("tr")].map((r) => r.children[1]!.getAttribute("data-text-alignment"))).toEqual([
        "right",
        "right",
      ]),
    );
    expect(pm.querySelectorAll("tr")[0]!.children[0]!.getAttribute("data-text-alignment")).toBeNull();
  });

  it("the row handle menu offers insert above / below and delete", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 0);
    const handle = await screen.findByRole("button", { name: "Row options" });
    act(() => {
      fireEvent.click(handle);
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    expect([...menu.querySelectorAll("[role^=menuitem]")].map((e) => e.textContent?.trim())).toEqual([
      "Insert above",
      "Insert below",
      "Delete row",
    ]);
    act(() => {
      fireEvent.click(within(menu).getByText("Insert below"));
    });
    await waitFor(() => expect(pm.querySelectorAll("tr")).toHaveLength(3));
  });

  it("the table ⋯ menu toggles the header row and the header column", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    await screen.findByRole("button", { name: "Column options" });
    const more = await waitFor(() => tableOptions());
    act(() => {
      fireEvent.click(more);
    });
    let menu = await screen.findByRole("menu", HIDDEN);
    const headerRow = within(menu).getByRole("menuitemcheckbox", { name: "Header row", ...HIDDEN });
    expect(headerRow).toHaveAttribute("aria-checked", "true");
    expect(within(menu).getByRole("menuitemcheckbox", { name: "Header column", ...HIDDEN })).toHaveAttribute("aria-checked", "false");
    act(() => {
      fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Header column", ...HIDDEN }));
    });
    await waitFor(() => expect(pm.querySelectorAll("tr")[1]!.children[0]!.tagName).toBe("TH"));
    // A switch keeps the menu open (as Notion's).
    menu = await screen.findByRole("menu", HIDDEN);
    expect(within(menu).getByRole("menuitemcheckbox", { name: "Header column", ...HIDDEN })).toHaveAttribute("aria-checked", "true");
    act(() => {
      fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Header row", ...HIDDEN }));
    });
    await waitFor(() => expect(pm.querySelectorAll("tr")[0]!.children[1]!.tagName).toBe("TD"));
  });

  it("has no cell menu (no colours, no split / merge); the grips drag and the + buttons stay", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    const grip = await screen.findByRole("button", { name: "Column options" });
    expect(document.querySelector(".bn-table-cell-handle")).toBeNull();
    expect(grip).toHaveAttribute("draggable", "true");
    const rowGrip = [...document.querySelectorAll("button")].find((b) => b.textContent === "Row options");
    expect(rowGrip).toHaveAttribute("draggable", "true");
    // The last cell: "+" on the right edge and the bottom edge.
    expect(document.querySelector(".bn-extend-button-add-remove-rows")).not.toBeNull();
    expect(document.querySelector(".bn-extend-button-add-remove-columns")).not.toBeNull();
  });
});

// ---------------------------------------------------------------- motion

type Animated = { el: Element; keyframes: Keyframe[] };

/** Records `el.animate` calls (jsdom has none) and finishes them at once. */
function recordAnimations(): Animated[] {
  const calls: Animated[] = [];
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    writable: true,
    value: function (this: Element, keyframes: Keyframe[]) {
      calls.push({ el: this, keyframes });
      return { finished: Promise.resolve(), cancel: () => {} };
    },
  });
  return calls;
}

function reducedMotion(on: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (q: string) =>
      ({ matches: on && q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }) as never,
  );
}

describe("table motion", () => {
  afterEach(() => {
    delete (Element.prototype as { animate?: unknown }).animate;
  });

  it("fades the side of a wide table that is clipped, only that side", () => {
    const editor = makeEditor([tableMotion], grid());
    const wrapper = editor._tiptapEditor.view.dom.querySelector<HTMLElement>(".tableWrapper")!;
    let left = 0;
    Object.defineProperty(wrapper, "scrollWidth", { configurable: true, get: () => 500 });
    Object.defineProperty(wrapper, "clientWidth", { configurable: true, get: () => 200 });
    Object.defineProperty(wrapper, "scrollLeft", { configurable: true, get: () => left });
    wrapper.dispatchEvent(new Event("scroll"));
    expect(wrapper.hasAttribute("data-clip-left")).toBe(false);
    expect(wrapper.hasAttribute("data-clip-right")).toBe(true);
    left = 300;
    wrapper.dispatchEvent(new Event("scroll"));
    expect(wrapper.hasAttribute("data-clip-left")).toBe(true);
    expect(wrapper.hasAttribute("data-clip-right")).toBe(false);
  });

  it("a new row grows from zero height; a new column from zero width", () => {
    reducedMotion(false);
    const calls = recordAnimations();
    const editor = makeEditor([tableMotion, tableKeys], grid());
    caretInCell(editor, 1, 1);
    press(editor, { key: "Tab" });
    const rows = editor._tiptapEditor.view.dom.querySelectorAll("tr");
    const grown = new Set(calls.map((c) => c.el.closest("tr")));
    expect(grown).toEqual(new Set([rows[2]]));
    expect(calls.some((c) => c.keyframes[0]?.height === "0px")).toBe(true);

    calls.length = 0;
    const t = tableOf(editor)!;
    editor.updateBlock("t", {
      type: "table",
      content: { ...t, columnWidths: [null, null, null], rows: t.rows.map((r) => ({ cells: [...r.cells, ""] })) },
    } as never);
    const cols = new Set(calls.map((c) => (c.el.tagName === "COL" ? "col" : (c.el.closest<HTMLTableCellElement>("td, th"))?.cellIndex)));
    expect(cols).toEqual(new Set(["col", 2]));
    expect(calls.some((c) => c.el.tagName === "COL" && c.keyframes[0]?.width === "0px")).toBe(true);
  });

  it("nothing moves under reduced motion", () => {
    reducedMotion(true);
    const calls = recordAnimations();
    const editor = makeEditor([tableMotion, tableKeys], grid());
    caretInCell(editor, 1, 1);
    press(editor, { key: "Tab" });
    expect(tableOf(editor)!.rows).toHaveLength(3);
    expect(calls).toHaveLength(0);
  });

  it("Delete column shrinks the column away, then removes it", async () => {
    reducedMotion(false);
    const calls = recordAnimations();
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Column options" }));
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(within(menu).getByText("Delete column"));
    });
    const shrunk = calls.filter((c) => c.el.closest("table"));
    expect(shrunk.length).toBeGreaterThan(0);
    expect(new Set(shrunk.map((c) => (c.el.tagName === "COL" ? "col" : (c.el.closest("td, th") as HTMLTableCellElement).cellIndex)))).toEqual(
      new Set(["col", 1]),
    );
    await waitFor(() => expect([...pm.querySelectorAll("tr")].map((r) => r.children.length)).toEqual([1, 1]));
  });
});
