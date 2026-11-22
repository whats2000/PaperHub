import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { editorHasBlockWithType } from "@blocknote/core";
import type { EditorView } from "@tiptap/pm/view";

import { NoteEditor } from "@/components/notes/NoteEditor";
import notesPaneSource from "@/components/notes/NotesPane.tsx?raw";
import indexCss from "@/index.css?raw";
import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteBlock, NoteOpsBody, TableContent } from "@/types/notes";

import { cleanPastedTable, deleteRowOrColumn, insertNoteTable, notePasteHandler, pickAlignments, tableClean, tableKeys } from "./tableKeys";
import { createCaptionHosts, partRequests, tableCaptions } from "./tableCaptions";
import { shrinkAway, tableMotion } from "./tableMotion";
import { tableBorderStats, tableBorderView } from "./tableBorderView";
import { canMerge, deleteSelectedRows, mergeSelected, selectCells, splitSelected } from "./tableMerge";
import { dumpBorders, parseBorders, presetLines } from "./tableBorders";
import { opBlock, toEditorBlocks } from "./sync";
import { cleanupEditors, makeEditor, press, type, type AnyEditor } from "./testEditor";

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
  it("keeps a pasted table's merged cells, strips its colours and keeps a header row", () => {
    const editor = makeEditor([], [{ id: "p", type: "paragraph" }], { pasteHandler: notePasteHandler });
    paste(editor, {
      "text/html":
        "<table><tr><td colspan='2' style='background-color: rgb(255, 0, 0)'><span style='color: red'>wide</span></td></tr>" +
        "<tr><td>x</td><td>y</td></tr></table>",
    });
    const t = tableOf(editor)!;
    expect(t).toBeDefined();
    expect(t.headerRows).toBe(1);
    expect(t.rows.map((r) => r.cells.length)).toEqual([1, 2]);
    expect(t.rows[0]!.cells[0]!.props.colspan).toBe(2);
    for (const row of t.rows) {
      for (const cell of row.cells) {
        expect(cell.props.backgroundColor ?? "default").toBe("default");
        expect(cell.props.textColor ?? "default").toBe("default");
        for (const run of cell.content) expect(run.styles?.textColor).toBeUndefined();
      }
    }
    expect(texts(t)).toEqual([["wide"], ["x", "y"]]);
  });

  it("turns pasted GFM text into a table with the delimiter row's alignment", () => {
    const editor = makeEditor([], [{ id: "p", type: "paragraph" }], { pasteHandler: notePasteHandler });
    paste(editor, { "text/plain": "| a | b |\n|---|--:|\n| 1 | 2 |" });
    const t = tableOf(editor)!;
    expect(texts(t)).toEqual([["a", "b"], ["1", "2"]]);
    expect(t.headerRows).toBe(1);
    expect(t.rows.map((r) => r.cells[1]!.props.textAlignment)).toEqual(["right", "right"]);
  });

  it("cleanPastedTable keeps merged cells, fills a short row and counts the header's depth", () => {
    const content: TableContent = {
      type: "tableContent",
      columnWidths: [null],
      rows: [
        { cells: [{ type: "tableCell", props: { rowspan: 2, backgroundColor: "red", textAlignment: "center" }, content: "tall" }, "b"] },
        { cells: ["c"] },
        { cells: ["d"] },
      ],
    };
    const out = cleanPastedTable(content);
    expect(out.headerRows).toBe(2);
    expect(out.columnWidths).toEqual([null, null]);
    expect(out.rows.map((r) => r.cells.length)).toEqual([2, 1, 2]);
    const first = out.rows[0]!.cells[0] as { props: Record<string, unknown> };
    expect(first.props).toEqual({ rowspan: 2, textAlignment: "center" });
    const third = out.rows[2]!.cells as { content: unknown }[];
    expect(third[1]!.content).toEqual([]);
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

  it("table rules and guides are mixed against the surface the table sits on", () => {
    // Opaque rules (a translucent one darkens the joints), mixed with the
    // surface each editor sits on: the pane (card), the sticky note card,
    // else the page background.
    const decl = (name: string) => new RegExp(`${name}:\\s*color-mix\\(([^;]*)\\);`).exec(indexCss)?.[1] ?? "";
    for (const v of ["--note-table-rule", "--note-table-guide"]) {
      expect(decl(v)).toContain("var(--note-table-surface, var(--background))");
      expect(decl(v)).not.toContain("transparent");
    }
    expect(indexCss).toMatch(/\.notes-pane\s*\{[^}]*--note-table-surface:\s*var\(--card\)/);
    expect(indexCss).toMatch(/\.note-card-sticky\s*\{[^}]*--note-table-surface:\s*var\(--sticky-bg\)/);
    expect(notesPaneSource).toMatch(/className="[^"]*\bnotes-pane\b[^"]*bg-card/);
  });

  it("a table handle menu is as wide as its longest item, not squeezed by its 0 px portal anchor", () => {
    // The menus are positioned inside BlockNote's zero-width portal anchor, so
    // a shrink-to-fit width wrapped "Delete column" and "Align centre ✓".
    expect(indexCss).toMatch(/\.bn-table-handle-menu\s*\{[^}]*width:\s*max-content/);
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

  it("the grips drag and the + buttons stay; the cell handle opens PaperHub's cell menu", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    const grip = await screen.findByRole("button", { name: "Column options" });
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

  it("fades only when the table itself is clipped, not the scroll box's handle padding", () => {
    const editor = makeEditor([tableMotion], grid());
    const wrapper = editor._tiptapEditor.view.dom.querySelector<HTMLElement>(".tableWrapper")!;
    // BlockNote pads the box for its handles (9px left, 22px right).
    wrapper.style.paddingLeft = "9px";
    wrapper.style.paddingRight = "22px";
    let left = 9;
    Object.defineProperty(wrapper, "scrollWidth", { configurable: true, get: () => 500 });
    Object.defineProperty(wrapper, "clientWidth", { configurable: true, get: () => 200 });
    Object.defineProperty(wrapper, "scrollLeft", { configurable: true, get: () => left });
    // Scrolled into view (the table's left edge at the box's): nothing of
    // the first column is hidden, so it must not fade.
    wrapper.dispatchEvent(new Event("scroll"));
    expect(wrapper.hasAttribute("data-clip-left")).toBe(false);
    expect(wrapper.hasAttribute("data-clip-right")).toBe(true);
    // Scrolled to the table's right edge (only the padding is past it).
    left = 280;
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

// ---------------------------------------------------------------- review fixes

describe("only what a GFM table holds, on every path", () => {
  it("a table takes no colour (the ⋮⋮ menu's Colors item has nothing to set)", () => {
    const editor = makeEditor([], grid());
    expect(editorHasBlockWithType(editor, "table", { textColor: "string" })).toBe(false);
    expect(editor.getBlock("t")!.props).not.toHaveProperty("textColor");
  });

  it("a table dropped or inserted as HTML loses its colours, not its merged cells", () => {
    const editor = makeEditor([tableClean], [{ id: "p", type: "paragraph" }]);
    // ProseMirror's own paste / drop, past BlockNote's paste handler.
    editor._tiptapEditor.view.pasteHTML(
      "<table><tr><td colspan='2' style='background-color: rgb(255, 0, 0)'>wide</td></tr><tr><td>x</td><td>y</td></tr></table>",
    );
    const t = tableOf(editor)!;
    expect(t.rows.map((r) => r.cells.length)).toEqual([1, 2]);
    expect(t.rows[0]!.cells[0]!.props.colspan).toBe(2);
    for (const row of t.rows) for (const cell of row.cells) expect(cell.props.backgroundColor ?? "default").toBe("default");
  });

  it("a table block inserted with a merged, coloured cell keeps the merge, loses the colour", () => {
    const editor = makeEditor([tableClean], [{ id: "p", type: "paragraph" }]);
    editor.insertBlocks(
      [
        {
          id: "t2",
          type: "table",
          content: {
            type: "tableContent",
            rows: [
              { cells: [{ type: "tableCell", props: { colspan: 2, textColor: "red" }, content: "wide" }] },
              { cells: ["x", "y"] },
            ],
          },
        },
      ] as never,
      "p",
      "after",
    );
    const t = tableOf(editor)!;
    expect(t.rows.map((r) => r.cells.length)).toEqual([1, 2]);
    expect(t.rows[0]!.cells[0]!.props.colspan).toBe(2);
    expect(t.rows[0]!.cells.every((c) => (c.props.textColor ?? "default") === "default")).toBe(true);
  });

  it("a dragged column width is saved as a whole number", () => {
    const editor = makeEditor([tableClean], grid());
    const view = editor._tiptapEditor.view;
    let cellPos = -1;
    view.state.doc.descendants((node, pos) => {
      if (cellPos < 0 && (node.type.name === "tableHeader" || node.type.name === "tableCell")) cellPos = pos;
      return cellPos < 0;
    });
    // What prosemirror-tables' column resizing commits on mouse-up.
    const cell = view.state.doc.nodeAt(cellPos)!;
    view.dispatch(view.state.tr.setNodeMarkup(cellPos, undefined, { ...cell.attrs, colwidth: [120.6] }));
    expect(tableOf(editor)!.columnWidths[0]).toBe(121);
  });
});

describe("the note editor normalises tables", () => {
  it("a merged, coloured table dropped into a note keeps the merge, not the colour", async () => {
    seed([blk("p0", { content: [] })]);
    const pm = mountEditor();
    act(() => {
      pm.editor.view.pasteHTML("<table><tr><td colspan='2' style='color: red'>wide</td></tr><tr><td>x</td><td>y</td></tr></table>");
    });
    await waitFor(() => expect([...pm.querySelectorAll("tr")].map((r) => r.children.length)).toEqual([1, 2]));
    expect(pm.querySelector("[colspan='2']")).not.toBeNull();
    expect(pm.querySelector("[data-text-color], [data-background-color]")).toBeNull();
  });
});

describe("alignment of pasted tables", () => {
  const two: TableContent = { type: "tableContent", columnWidths: [null, null], rows: [{ cells: ["a", "b"] }] };
  it("applies a source's alignments only when they match the pasted tables one to one", () => {
    expect(pickAlignments([two], [[["left", "right"]]])).toEqual([["left", "right"]]);
    // A "|---|" line that is not a table: two lists for one table.
    expect(pickAlignments([two], [[["left", "right"], [null]]])).toBeNull();
    // Column counts differ.
    expect(pickAlignments([two], [[[null, null, "right"]]])).toBeNull();
    // The first source that fits wins.
    expect(pickAlignments([two], [[[null]], [[null, "center"]]])).toEqual([[null, "center"]]);
  });
});

describe("no op churn from tables", () => {
  it("a stored table with plain-string cells sends nothing when a neighbouring line is edited", async () => {
    const posted: NoteOpsBody[] = [];
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        const res: ApplyResult = {
          note_id: 7,
          revision: body.base_revision + 1,
          change_set_id: "cs",
          applied: body.ops.map((o) => o.block_id ?? ""),
          suggested: [],
          rejected: [],
          reparented: [],
        };
        return HttpResponse.json(res);
      }),
    );
    seed([
      blk("t", {
        type: "table",
        content: {
          type: "tableContent",
          columnWidths: [null, null],
          headerRows: 1,
          rows: [{ cells: ["指標", "值"] }, { cells: ["ORR", "62%"] }],
        },
        text: "指標 | 值\nORR | 62%",
      }),
      blk("p1", { content: [{ type: "text", text: "Mine", styles: {} }], text: "Mine" }),
    ]);
    const pm = mountEditor();
    await screen.findByText("Mine");
    act(() => {
      (pm.editor.commands as unknown as { insertContentAt: (p: number, t: string) => boolean }).insertContentAt(
        pm.editor.view.state.doc.content.size - 3,
        "!",
      );
    });
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted.flatMap((b) => b.ops.map((o) => o.block_id))).toEqual(["p1"]);
  });
});

describe("motion, robustly", () => {
  afterEach(() => {
    delete (Element.prototype as { animate?: unknown }).animate;
  });

  it("a delete finds its row again after the shrink, even if rows moved meanwhile; a second one is ignored", async () => {
    reducedMotion(false);
    let finish!: () => void;
    const done = new Promise<void>((r) => (finish = r));
    Object.defineProperty(Element.prototype, "animate", {
      configurable: true,
      writable: true,
      value: () => ({ finished: done, cancel: () => {} }),
    });
    const editor = makeEditor([tableMotion], grid());
    const view = editor._tiptapEditor.view;
    const removed: number[] = [];
    shrinkAway(view, "t", "row", 1, (i) => removed.push(i));
    shrinkAway(view, "t", "row", 0, (i) => removed.push(i));
    // A row lands above while the shrink runs.
    const t = tableOf(editor)!;
    editor.updateBlock("t", { type: "table", content: { ...t, rows: [{ cells: ["new", "row"] }, ...t.rows] } } as never);
    finish();
    await waitFor(() => expect(removed).toEqual([2]));
  });

  it("a change in a note with no table touches no table", () => {
    const editor = makeEditor([tableMotion], [{ id: "p", type: "paragraph", content: "x" }]);
    const view = editor._tiptapEditor.view;
    const spy = vi.spyOn(view.dom, "querySelectorAll");
    view.dispatch(view.state.tr.insertText("y", 2));
    expect(spy.mock.calls.filter(([sel]) => String(sel).includes("tableWrapper"))).toHaveLength(0);
  });

  it("the row / column grips and the + buttons are outside the faded scroll box", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    const grip = await screen.findByRole("button", { name: "Column options" });
    expect(grip.closest(".tableWrapper")).toBeNull();
    expect(tableOptions().closest(".tableWrapper")).toBeNull();
    expect(document.querySelector(".bn-extend-button")!.closest(".tableWrapper")).toBeNull();
  });
});

// ---------------------------------------------------------------- caption and notes

describe("table caption and notes", () => {
  const second = blk("t2", {
    type: "table",
    content: {
      type: "tableContent",
      columnWidths: [null],
      headerRows: 1,
      rows: [{ cells: ["k"] }, { cells: ["v"] }],
    },
    text: "k\nv",
  });

  /** The caption (or notes) part of the table block `id`. */
  const part = (id: string, which: "caption" | "notes") =>
    document.querySelector<HTMLElement>(`[data-id="${id}"] [data-table-caption='${which}']`);

  async function openTableMenu(pm: PM, r = 1, c = 0) {
    hoverCell(pm, r, c);
    const more = await waitFor(() => tableOptions());
    act(() => {
      fireEvent.click(more);
    });
    return screen.findByRole("menu", HIDDEN);
  }

  const items = (menu: HTMLElement) =>
    [...menu.querySelectorAll("[role^=menuitem]")].map((e) => e.textContent?.trim());

  const propsOf = (pm: PM, id: string) => {
    let props: Record<string, unknown> | null = null;
    pm.editor.view.state.doc.descendants((node) => {
      if (props) return false;
      if (node.type.name === "blockContainer" && node.attrs.id === id) props = node.firstChild!.attrs;
      return !props;
    });
    return props as unknown as Record<string, unknown>;
  };

  it("Add caption shows an editable Table 1. above the table with the caret in it; Enter saves it", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    const menu = await openTableMenu(pm);
    expect(items(menu).slice(2, 6)).toEqual(["Header row", "Header column", "Add caption", "Add notes"]);
    act(() => {
      fireEvent.click(within(menu).getByText("Add caption"));
    });
    const field = await waitFor(() => {
      const el = screen.getByRole("textbox", { name: "Table caption" });
      expect(document.activeElement).toBe(el);
      return el;
    });
    const cap = part("t", "caption")!;
    expect(cap).toHaveAttribute("data-state", "open");
    expect(cap.textContent).toContain("Table 1.");
    // Above the table.
    const table = pm.querySelector("[data-id='t'] table")!;
    expect(cap.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    act(() => {
      fireEvent.change(field, { target: { value: "Response rates $p$" } });
    });
    act(() => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(propsOf(pm, "t").caption).toBe("Response rates $p$");
    expect(screen.queryByRole("textbox", { name: "Table caption" })).toBeNull();
    expect(cap.textContent).toContain("Response rates");
    expect(cap.querySelector(".katex")).not.toBeNull();
    const again = await openTableMenu(pm);
    expect(items(again)).toContain("Remove caption");
  });

  it("captioned tables are numbered in order: a caption added above renumbers both", async () => {
    seed([{ ...second, id: "t0" }, blk("t", { ...seededTable, props: { caption: "Lower" } })]);
    const pm = mountEditor();
    await waitFor(() => expect(part("t", "caption")?.textContent).toContain("Table 1."));
    // The first table's rows come first in the document.
    const menu = await openTableMenu(pm, 1, 0);
    act(() => {
      fireEvent.click(within(menu).getByText("Add caption"));
    });
    const field = await screen.findByRole("textbox", { name: "Table caption" });
    expect(part("t0", "caption")!.textContent).toContain("Table 1.");
    expect(part("t", "caption")!.textContent).toContain("Table 2.");
    act(() => {
      fireEvent.change(field, { target: { value: "Upper" } });
    });
    act(() => {
      fireEvent.blur(field);
    });
    expect(propsOf(pm, "t0").caption).toBe("Upper");
    expect(part("t0", "caption")!.textContent).toContain("Table 1.");
    expect(part("t", "caption")!.textContent).toContain("Table 2.");
  });

  it("emptying the caption removes it (it collapses toward the table) and the menu says Add again", async () => {
    seed([blk("t", { ...seededTable, props: { caption: "Gone soon" } })]);
    const pm = mountEditor();
    const cap = await waitFor(() => {
      const el = part("t", "caption")!;
      expect(el.textContent).toContain("Gone soon");
      return el;
    });
    act(() => {
      fireEvent.click(within(cap).getByRole("button", { name: /Gone soon/ }));
    });
    const field = await screen.findByRole("textbox", { name: "Table caption" });
    act(() => {
      fireEvent.change(field, { target: { value: "  " } });
    });
    act(() => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(propsOf(pm, "t").caption).toBe("");
    // It plays its exit (closed, its words kept while it collapses).
    expect(cap).toHaveAttribute("data-state", "closed");
    expect(cap).toHaveAttribute("data-side", "above");
    const menu = await openTableMenu(pm);
    expect(items(menu)).toContain("Add caption");
    expect(items(menu)).not.toContain("Remove caption");
  });

  it("Remove caption / Remove notes in the table menu clear them", async () => {
    seed([blk("t", { ...seededTable, props: { caption: "C", notes: "N" } })]);
    const pm = mountEditor();
    let menu = await openTableMenu(pm);
    expect(items(menu).slice(2, 6)).toEqual(["Header row", "Header column", "Remove caption", "Remove notes"]);
    act(() => {
      fireEvent.click(within(menu).getByText("Remove notes"));
    });
    expect(propsOf(pm, "t").notes).toBe("");
    expect(propsOf(pm, "t").caption).toBe("C");
    menu = await openTableMenu(pm);
    act(() => {
      fireEvent.click(within(menu).getByText("Remove caption"));
    });
    expect(propsOf(pm, "t").caption).toBe("");
  });

  it("notes sit below the table in muted text, several lines, and take $...$ as math", async () => {
    seed([blk("t", { ...seededTable, props: { notes: "ORR, objective response.\n* $p<0.05$" } })]);
    const pm = mountEditor();
    const notes = await waitFor(() => {
      const el = part("t", "notes")!;
      expect(el.textContent).toContain("objective response");
      return el;
    });
    const table = pm.querySelector("[data-id='t'] table")!;
    expect(notes.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(notes.querySelector(".text-muted-foreground")).not.toBeNull();
    expect(notes.querySelector(".katex")).not.toBeNull();
    expect(notes.querySelector(".whitespace-pre-wrap")).not.toBeNull();
    // Edited in place: Enter is a new line, Ctrl+Enter (or leaving) saves.
    act(() => {
      // (jsdom cannot name a button holding KaTeX's MathML: found by tag.)
      fireEvent.click(notes.querySelector("button")!);
    });
    const field = await screen.findByRole("textbox", { name: "Table notes" });
    act(() => {
      fireEvent.change(field, { target: { value: "a\nb" } });
    });
    act(() => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(screen.getByRole("textbox", { name: "Table notes" })).toBe(field);
    act(() => {
      fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });
    });
    expect(propsOf(pm, "t").notes).toBe("a\nb");
    expect(notes.textContent).not.toContain("Table");
  });

  it("caption and notes are saved as the table's props, an empty one sent as none", async () => {
    const posted: NoteOpsBody[] = [];
    server.use(
      http.post(`${API_BASE_URL}/notes/7/ops`, async ({ request }) => {
        const body = (await request.json()) as NoteOpsBody;
        posted.push(body);
        const res: ApplyResult = {
          note_id: 7,
          revision: body.base_revision + 1,
          change_set_id: "cs",
          applied: body.ops.map((o) => o.block_id ?? ""),
          suggested: [],
          rejected: [],
          reparented: [],
        };
        return HttpResponse.json(res);
      }),
    );
    seed([blk("t", { ...seededTable, props: { notes: "N" } })]);
    const pm = mountEditor();
    const menu = await openTableMenu(pm);
    act(() => {
      fireEvent.click(within(menu).getByText("Add caption"));
    });
    const field = await screen.findByRole("textbox", { name: "Table caption" });
    act(() => {
      fireEvent.change(field, { target: { value: "Rates" } });
    });
    act(() => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    const op = posted.flatMap((b) => b.ops).find((o) => o.block_id === "t");
    expect(op?.block?.props).toEqual({ caption: "Rates", notes: "N" });
    expect(propsOf(pm, "t").caption).toBe("Rates");
    // Removing the notes sends no empty prop: it names the notes as null
    // (the server patches props, so a key left out would be kept).
    posted.length = 0;
    const again = await openTableMenu(pm);
    act(() => {
      fireEvent.click(within(again).getByText("Remove notes"));
    });
    await act(async () => {
      await useNotesStore.getState().flush(7);
    });
    expect(posted.flatMap((b) => b.ops).find((o) => o.block_id === "t")?.block?.props).toEqual({
      caption: "Rates",
      notes: null,
    });
    expect(useNotesStore.getState().notes[7]!.blocks.find((b) => b.id === "t")!.props).toEqual({ caption: "Rates" });
  });

  it("an empty caption or notes part takes no room and draws nothing", async () => {
    seed([seededTable]);
    mountEditor();
    await waitFor(() => expect(part("t", "caption")).not.toBeNull());
    for (const which of ["caption", "notes"] as const) {
      const el = part("t", which)!;
      expect(el).toHaveAttribute("data-state", "closed");
      expect(el.textContent).toBe("");
      expect(el.className).toContain("motion-reduce:transition-none");
    }
  });
});

describe("caption hosts and part requests (fix round 1)", () => {
  it("a deleted table's caption and notes hosts are let go", () => {
    const hosts = createCaptionHosts();
    const editor = makeEditor([tableCaptions(hosts)], [...grid(), { ...grid()[0] as object, id: "t2" }]);
    expect(hosts.size()).toBe(4);
    const first = hosts.host("t", "caption");
    editor.removeBlocks(["t2"]);
    expect(hosts.size()).toBe(2);
    expect(hosts.host("t", "caption")).toBe(first);
  });

  it("a part request reaches only its own editor", () => {
    const a = makeEditor([], grid());
    const b = makeEditor([], grid());
    const seen: string[] = [];
    const offA = partRequests(a).subscribe(() => seen.push("a"));
    const offB = partRequests(b).subscribe(() => seen.push("b"));
    partRequests(a).request("t", "caption");
    expect(seen).toEqual(["a"]);
    expect(partRequests(a).get()?.blockId).toBe("t");
    expect(partRequests(b).get()).toBeNull();
    offA();
    offB();
  });

  it("two editors on one note: Add caption in one opens the part only there", async () => {
    seed([seededTable]);
    act(() => {
      render(
        <>
          <NoteEditor noteId={7} variant="pane" />
          <NoteEditor noteId={7} variant="card" />
        </>,
      );
    });
    const [pane] = [...document.querySelectorAll<HTMLElement>(".ProseMirror")] as PM[];
    hoverCell(pane!, 1, 0);
    const more = await waitFor(() => tableOptions());
    act(() => {
      fireEvent.click(more);
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(within(menu).getByText("Add caption"));
    });
    expect(document.querySelectorAll(".ProseMirror")).toHaveLength(2);
    expect(document.querySelectorAll("[data-table-caption='caption']")).toHaveLength(2);
    await waitFor(() => expect(screen.getAllByRole("textbox", { name: "Table caption" })).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    const fields = screen.getAllByRole("textbox", { name: "Table caption" });
    expect(fields).toHaveLength(1);
    // The one opened is in the editor whose menu was used, and has the caret.
    expect(pane!.contains(fields[0]!)).toBe(true);
    expect(document.activeElement).toBe(fields[0]);
    // The other editor never opened its caption (no empty caption written).
    expect(useNotesStore.getState().notes[7]!.blocks.find((x) => x.id === "t")!.props).toEqual({});
  });
});

// ------------------------------------------------- each table's own lines (H5b.1)

const cellOf = (text: string) => ({ type: "tableCell" as const, props: {}, content: text ? [{ type: "text", text, styles: {} }] : [] });

describe("table borders drawn from the table's lines", () => {
  const plainTable = (rows: number, cols: number, borders = "", id = "t"): unknown[] => [
    {
      id,
      type: "table",
      props: borders ? { borders } : {},
      content: {
        type: "tableContent",
        columnWidths: Array.from({ length: cols }, () => null),
        headerRows: 1,
        rows: Array.from({ length: rows }, (_, r) => ({ cells: Array.from({ length: cols }, (_, c) => `r${r}c${c}`) })),
      },
    },
    { id: `${id}-after`, type: "paragraph", content: "after" },
  ];
  const cells = (editor: AnyEditor) =>
    [...editor._tiptapEditor.view.dom.querySelectorAll("tr")].map((tr) => [...tr.querySelectorAll<HTMLElement>("th, td")]);
  const bordersProp = (editor: AnyEditor) =>
    parseBorders((editor.document.find((b) => b.type === "table")?.props as Record<string, unknown>).borders);

  it("draws a table without lines as Paper, and loading it sends nothing", () => {
    const stored = blk("t", {
      type: "table",
      content: {
        type: "tableContent",
        columnWidths: [null, null],
        headerRows: 1,
        rows: [{ cells: ["a", "b"] }, { cells: ["1", "2"] }, { cells: ["3", "4"] }],
      },
    });
    const editor = makeEditor([tableBorderView], toEditorBlocks([stored]));
    const grid = cells(editor);
    expect(grid[0]![0]!.dataset.bt).toBe("2");
    expect(grid[0]![0]!.dataset.bb).toBe("1");
    expect(grid[2]![1]!.dataset.bb).toBe("2");
    expect(grid[1]![0]!.dataset.bl).toBe("0");
    expect(grid[1]![0]!.dataset.bb).toBe("0");
    // A 0 edge is a dashed guide while editing.
    expect(indexCss).toMatch(/\[data-bt="0"\][^{]*\{[^}]*dashed var\(--note-table-guide\)/);
    // Its lines stay unset: nothing is sent for them (the "no op churn" test
    // below mounts the whole editor).
    expect(opBlock(editor.document[0] as never).props).toEqual({});
  });

  it("Tab in the last cell keeps the end rule under the new row", () => {
    const custom = dumpBorders({ preset: "paper", h: ["22", "11", "22"], v: ["202", "202"] });
    const editor = makeEditor([tableBorderView, tableKeys], plainTable(2, 2, custom));
    caretInCell(editor, 1, 1);
    press(editor, { key: "Tab" });
    const grid = cells(editor);
    expect(grid).toHaveLength(3);
    expect(grid[2]![0]!.dataset.bb).toBe("2");
    expect(grid[1]![0]!.dataset.bb).not.toBe("2");
    expect(bordersProp(editor)?.v).toEqual(["202", "202", "202"]);
  });

  it("Tab past a last row holding a merged cell keeps the end rule at the bottom", () => {
    const content = {
      type: "tableContent",
      columnWidths: [null, null],
      headerRows: 1,
      rows: [
        { cells: ["h1", "h2"] },
        { cells: [{ type: "tableCell", props: { rowspan: 2 }, content: "m" }, "x"] },
        { cells: ["y"] },
      ],
    };
    const lines = { preset: "paper" as const, h: ["22", "11", "00", "22"], v: ["020", "020", "020"] };
    const editor = makeEditor([tableBorderView, tableKeys], [
      { id: "t", type: "table", props: { borders: dumpBorders(lines) }, content },
      { id: "after", type: "paragraph", content: "after" },
    ]);
    caretInCell(editor, 2, 0);
    press(editor, { key: "Tab" });
    const b = bordersProp(editor)!;
    expect(b.v).toHaveLength(4);
    expect(b.h[4]).toBe("22");
    expect(b.h[3]).not.toBe("22");
  });

  it("inserting rows and columns keeps the lines' shape and follows the rules", () => {
    const custom = dumpBorders({ preset: "paper", h: ["22", "11", "02", "22"], v: ["000", "010", "010"] });
    const editor = makeEditor([tableBorderView], plainTable(3, 2, custom));
    const t = () => tableOf(editor)!;
    // Insert above row 1 (as the row menu does: a new row in the content).
    editor.updateBlock("t", {
      type: "table",
      content: { ...t(), rows: [t().rows[0]!, { cells: [cellOf(""), cellOf("")] }, ...t().rows.slice(1)] },
    } as never);
    let b = bordersProp(editor)!;
    expect(b.h).toHaveLength(5);
    expect(b.v[1]).toBe("000");
    // A column at the right edge.
    editor.updateBlock("t", {
      type: "table",
      content: { ...t(), columnWidths: [null, null, null], rows: t().rows.map((r) => ({ cells: [...r.cells, cellOf("")] })) },
    } as never);
    b = bordersProp(editor)!;
    expect(b.h.every((l) => l.length === 3)).toBe(true);
    expect(b.v.every((l) => l.length === 4)).toBe(true);
    expect(b.h[0]).toBe("222");
  });

  it("dragging a row moves its vertical lines with it", () => {
    const custom = dumpBorders({ preset: "paper", h: ["22", "11", "00", "22"], v: ["000", "010", "202"] });
    const editor = makeEditor([tableBorderView], plainTable(3, 2, custom));
    const t = tableOf(editor)!;
    editor.updateBlock("t", { type: "table", content: { ...t, rows: [t.rows[0]!, t.rows[2]!, t.rows[1]!] } } as never);
    const b = bordersProp(editor)!;
    expect(b.v).toEqual(["000", "202", "010"]);
    expect(b.h).toEqual(["22", "11", "00", "22"]);
  });

  it("deleting a row that crosses a merged cell keeps the shape and the end rule", () => {
    const content = {
      type: "tableContent",
      columnWidths: [null, null],
      headerRows: 1,
      rows: [
        { cells: ["h1", "h2"] },
        { cells: [{ type: "tableCell", props: { rowspan: 2 }, content: "m" }, "x"] },
        { cells: ["y"] },
        { cells: ["z1", "z2"] },
      ],
    };
    const lines = { preset: "paper" as const, h: ["22", "11", "00", "01", "22"], v: ["000", "020", "101", "000"] };
    const editor = makeEditor([tableBorderView], [{ id: "t", type: "table", props: { borders: dumpBorders(lines) }, content }]);
    const t = tableOf(editor)!;
    const anchor = t.rows[1]!.cells[0]!;
    const shrunk = {
      ...t,
      rows: [t.rows[0]!, { cells: [{ ...anchor, props: { ...anchor.props, rowspan: 1 } }, t.rows[1]!.cells[1]!] }, t.rows[3]!],
    };
    expect(() => editor.updateBlock("t", { type: "table", content: shrunk } as never)).not.toThrow();
    const b = bordersProp(editor)!;
    // The edited row (its merge shrank) keeps its lines; the deleted row's go.
    expect(b.v).toEqual(["000", "020", "000"]);
    expect(b.h[3]).toBe("22");
  });

  it("deleting the merged cell's own row drops that row's lines, not the next row's", () => {
    const c = (text: string, props: Record<string, unknown> = {}) => ({ type: "tableCell", props, content: text });
    const content = {
      type: "tableContent",
      columnWidths: [null, null],
      headerRows: 1,
      rows: [{ cells: [c("h1"), c("h2")] }, { cells: [c("A", { rowspan: 2 }), c("x")] }, { cells: [c("y")] }, { cells: [c("z1"), c("z2")] }],
    };
    const lines = { preset: "paper" as const, h: ["22", "11", "00", "01", "22"], v: ["000", "020", "101", "000"] };
    const editor = makeEditor([tableBorderView], [{ id: "t", type: "table", props: { borders: dumpBorders(lines) }, content }]);
    // The row handle's Delete on row 1: prosemirror-tables moves A down into row 2.
    deleteRowOrColumn(editor, "t", "row", 1);
    expect(texts(tableOf(editor)!)).toEqual([["h1", "h2"], ["A", "y"], ["z1", "z2"]]);
    const b = bordersProp(editor)!;
    expect(b.v).toEqual(["000", "101", "000"]);
    expect(b.h).toEqual(["22", "11", "01", "22"]);
  });

  it("re-draws only the tables a change touched", () => {
    const editor = makeEditor([tableBorderView, tableKeys], [
      { id: "p", type: "paragraph", content: "start" },
      ...plainTable(2, 2, "", "a"),
      ...plainTable(2, 2, "", "b"),
      ...plainTable(2, 2, "", "c"),
    ]);
    tableBorderStats.decorated = 0;
    editor.setTextCursorPosition("p", "end");
    type(editor, "xyz");
    expect(tableBorderStats.decorated).toBe(0);
    caretInCell(editor, 1, 1);
    type(editor, "q");
    expect(tableBorderStats.decorated).toBe(1);
    // A change in one table leaves the others drawn.
    const all = [...editor._tiptapEditor.view.dom.querySelectorAll('[data-content-type="table"]')];
    expect(all.map((t) => t.getAttribute("data-table-preset"))).toEqual(["paper", "paper", "paper"]);
    expect(all.every((t) => t.querySelector("th")?.getAttribute("data-bt") === "2")).toBe(true);
  });

  it("one undo takes back both the row and its lines", () => {
    const custom = dumpBorders({ preset: "paper", h: ["22", "11", "22"], v: ["202", "202"] });
    const editor = makeEditor([tableBorderView, tableKeys], plainTable(2, 2, custom));
    caretInCell(editor, 1, 1);
    press(editor, { key: "Tab" });
    expect(tableOf(editor)!.rows).toHaveLength(3);
    editor.undo();
    expect(tableOf(editor)!.rows).toHaveLength(2);
    expect(bordersProp(editor)?.v).toEqual(["202", "202"]);
  });

  it("edit guides show only while editing, and can be hidden", () => {
    // Read-only views and the Hide edit guides switch draw no guide.
    expect(indexCss).toMatch(/\[contenteditable="false"\][^{]*\[data-b[trbl]="0"\]/);
    expect(indexCss).toMatch(/\[data-guides="off"\][^{]*\[data-b[trbl]="0"\]/);
    const editor = makeEditor([tableBorderView], plainTable(2, 2));
    editor.isEditable = false;
    expect(cells(editor)[0]![0]!.dataset.bt).toBe("2");
  });

  it("marks each table with its preset; only Notion tints the header", () => {
    const notion = dumpBorders(presetLines(2, 2, 1, "notion"));
    const editor = makeEditor([tableBorderView], [...plainTable(2, 2, notion), ...plainTable(2, 2, "", "u")]);
    const tables = [...editor._tiptapEditor.view.dom.querySelectorAll('[data-content-type="table"]')];
    expect(tables.map((t) => t.getAttribute("data-table-preset"))).toEqual(["notion", "paper"]);
    expect(indexCss).toMatch(/\[data-table-preset="notion"\][^{]*th\s*\{[^}]*background/);
    expect(indexCss).not.toMatch(/data-table-style/);
  });
});

describe("per-table presets (H5b.1)", () => {
  afterEach(() => {
    useNotesStore.setState({ tablePreset: "paper", tableGuides: true });
  });

  const twoTables = () => [
    seededTable,
    blk("u", {
      type: "table",
      content: { type: "tableContent", columnWidths: [null, null], headerRows: 1, rows: [{ cells: ["x", "y"] }, { cells: ["3", "4"] }] },
    }),
  ];
  const propsOf = (pm: PM, id: string) => {
    const el = pm.querySelector(`[data-id="${id}"] [data-content-type="table"]`);
    return el;
  };

  it("the table ⋯ menu shows Paper first; Notion re-draws only that table and becomes the default", async () => {
    seed(twoTables());
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    await screen.findByRole("button", { name: "Column options" });
    act(() => {
      fireEvent.click(tableOptions());
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    const items = [...menu.querySelectorAll("[role^=menuitem]")].map((e) => e.textContent ?? "");
    expect(items[0]).toMatch(/^Paper/);
    expect(items[1]).toMatch(/^Notion/);
    expect(items.at(-1)).toMatch(/Hide edit guides/);
    const paper = within(menu).getByRole("menuitemcheckbox", { name: /^Paper/, ...HIDDEN });
    expect(paper).toHaveAttribute("aria-checked", "true");
    act(() => {
      fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: /^Notion/, ...HIDDEN }));
    });
    await waitFor(() => expect(propsOf(pm, "t")).toHaveAttribute("data-table-preset", "notion"));
    expect(propsOf(pm, "u")).toHaveAttribute("data-table-preset", "paper");
    expect(useNotesStore.getState().tablePreset).toBe("notion");
  });

  it("/table and a pasted table take the last preset chosen", () => {
    useNotesStore.setState({ tablePreset: "notion" });
    const slash = makeEditor([], [{ id: "p0", type: "paragraph", content: "" }]);
    insertNoteTable(slash);
    const inserted = slash.document.find((b) => b.type === "table");
    expect(parseBorders((inserted?.props as Record<string, unknown>).borders)).toEqual(presetLines(3, 3, 1, "notion"));

    const editor = makeEditor([], [{ id: "p", type: "paragraph", content: "" }], { pasteHandler: notePasteHandler });
    paste(editor, { "text/html": "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>" });
    const pasted = editor.document.find((b) => b.type === "table");
    expect(parseBorders((pasted?.props as Record<string, unknown>).borders)?.preset).toBe("notion");
  });

  it("Hide edit guides turns the guides off for every table, and is remembered", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    const root = screen.getByTestId("note-editor");
    expect(root).toHaveAttribute("data-guides", "on");
    hoverCell(pm, 1, 1);
    await screen.findByRole("button", { name: "Column options" });
    act(() => {
      fireEvent.click(tableOptions());
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(within(menu).getByText("Hide edit guides"));
    });
    await waitFor(() => expect(root).toHaveAttribute("data-guides", "off"));
    expect(useNotesStore.getState().tableGuides).toBe(false);
  });
});

// ------------------------------------------------------- merge and split (H5b.1)

describe("merging and splitting cells", () => {
  const table = (headerRows: number, borders = ""): unknown[] => [
    {
      id: "t",
      type: "table",
      props: borders ? { borders } : {},
      content: {
        type: "tableContent",
        columnWidths: [null, null, null],
        headerRows,
        rows: [{ cells: ["h1", "h2", "h3"] }, { cells: ["a", "b", "c"] }, { cells: ["d", "e", "f"] }],
      },
    },
  ];
  const spans = (t: Table) => t.rows.map((r) => r.cells.map((c) => [c.props.rowspan ?? 1, c.props.colspan ?? 1]));

  it("merges a 1×2 selection into one cell holding both texts; the lines stay", () => {
    const lines = dumpBorders({ ...presetLines(3, 3, 1, "paper"), h: ["222", "111", "020", "222"] });
    const editor = makeEditor([tableBorderView], table(1, lines));
    selectCells(editor, [1, 0], [1, 1]);
    expect(mergeSelected(editor)).toBe(true);
    const t = tableOf(editor)!;
    expect(t.rows[1]!.cells).toHaveLength(2);
    expect(t.rows[1]!.cells[0]!.props.colspan).toBe(2);
    expect(texts(t)[1]![0]).toMatch(/a\s*b/);
    expect((editor.document[0]!.props as Record<string, unknown>).borders).toBe(lines);
  });

  it("splits a merged cell back into two, the second empty", () => {
    const editor = makeEditor([tableBorderView], table(1));
    selectCells(editor, [1, 0], [1, 1]);
    mergeSelected(editor);
    selectCells(editor, [1, 0], [1, 0]);
    expect(splitSelected(editor)).toBe(true);
    const t = tableOf(editor)!;
    expect(t.rows[1]!.cells).toHaveLength(3);
    expect(texts(t)[1]![1]).toBe("");
  });

  it("a header cell merged down deepens the header; splitting it makes it one row again", () => {
    const editor = makeEditor([tableBorderView], table(1));
    selectCells(editor, [0, 0], [1, 0]);
    mergeSelected(editor);
    expect(tableOf(editor)!.headerRows).toBe(2);
    selectCells(editor, [0, 0], [0, 0]);
    splitSelected(editor);
    expect(tableOf(editor)!.headerRows).toBe(1);
  });

  it("deleting a row a merged cell crosses shrinks the span and throws nothing", () => {
    const editor = makeEditor([tableBorderView], table(1));
    selectCells(editor, [1, 0], [2, 0]);
    mergeSelected(editor);
    expect(spans(tableOf(editor)!)[1]![0]).toEqual([2, 1]);
    selectCells(editor, [2, 1], [2, 1]);
    expect(() => deleteSelectedRows(editor)).not.toThrow();
    const t = tableOf(editor)!;
    expect(t.rows).toHaveLength(2);
    expect(spans(t)[1]![0]).toEqual([1, 1]);
  });

  it("the merged cell fades in; nothing moves under reduced motion", () => {
    reducedMotion(false);
    const calls = recordAnimations();
    const editor = makeEditor([tableBorderView], table(1));
    selectCells(editor, [1, 0], [1, 1]);
    mergeSelected(editor);
    expect(calls.some((c) => c.el.tagName === "TD" && c.keyframes[0]?.opacity === 0)).toBe(true);
    reducedMotion(true);
    calls.length = 0;
    selectCells(editor, [1, 0], [1, 0]);
    splitSelected(editor);
    expect(calls).toHaveLength(0);
    delete (Element.prototype as { animate?: unknown }).animate;
  });

  it("a row or column handle's Delete on a merged table removes only that row / column", () => {
    const content = (rows: unknown[][]) => [
      {
        id: "t",
        type: "table",
        content: { type: "tableContent", columnWidths: rows[0]!.map(() => null), headerRows: 1, rows: rows.map((cells) => ({ cells })) },
      },
    ];
    const c = (text: string, props: Record<string, unknown> = {}) => ({ type: "tableCell", props, content: text });
    // Row 2 (C) sits under A's rowspan.
    const editor = makeEditor([tableBorderView], content([[c("H1"), c("H2")], [c("A", { rowspan: 2 }), c("B")], [c("C")], [c("D"), c("E")]]));
    deleteRowOrColumn(editor, "t", "row", 2);
    expect(texts(tableOf(editor)!)).toEqual([["H1", "H2"], ["A", "B"], ["D", "E"]]);
    // Column 2 under a header group spanning columns 1-2.
    const ed2 = makeEditor([tableBorderView], content([[c("M"), c("G", { colspan: 2 })], [c("x"), c("y"), c("z")]]));
    deleteRowOrColumn(ed2, "t", "column", 2);
    const t2 = tableOf(ed2)!;
    expect(texts(t2)).toEqual([["M", "G"], ["x", "y"]]);
    expect(t2.rows[0]!.cells[1]!.props.colspan ?? 1).toBe(1);
  });

  it("will not merge cells that would leave a row without a cell of its own", () => {
    const c = (text: string, props: Record<string, unknown> = {}) => ({ type: "tableCell", props, content: text });
    const editor = makeEditor([tableBorderView], [
      {
        id: "t",
        type: "table",
        content: { type: "tableContent", columnWidths: [null, null, null], headerRows: 0, rows: [{ cells: [c("A", { rowspan: 2 }), c("B"), c("C")] }, { cells: [c("E"), c("F")] }] },
      },
    ]);
    selectCells(editor, [0, 1], [1, 2]);
    expect(canMerge(editor._tiptapEditor.view.state)).toBe(false);
    selectCells(editor, [0, 1], [0, 2]);
    expect(canMerge(editor._tiptapEditor.view.state)).toBe(true);
  });

  it("will not merge a whole row's width over several rows (a row needs a cell of its own)", () => {
    const editor = makeEditor([tableBorderView], table(0));
    selectCells(editor, [1, 0], [2, 2]);
    expect(canMerge(editor._tiptapEditor.view.state)).toBe(false);
    selectCells(editor, [1, 0], [2, 1]);
    expect(canMerge(editor._tiptapEditor.view.state)).toBe(true);
  });

  it("Borders… in the cell menu opens the picker on the cell's lines", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Cell options", ...HIDDEN }));
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(within(menu).getByText("Borders…"));
    });
    const dialog = await screen.findByRole("dialog", { name: /Borders · 1×1 cells/, ...HIDDEN });
    expect(within(dialog).getAllByRole("button", HIDDEN).length).toBeGreaterThanOrEqual(12);
  });

  it("the cell menu: Merge off on one plain cell, Split only on a merged one, no colour", async () => {
    seed([seededTable]);
    const pm = mountEditor();
    hoverCell(pm, 1, 1);
    const handle = await screen.findByRole("button", { name: "Cell options" });
    act(() => {
      fireEvent.click(handle);
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    const names = [...menu.querySelectorAll("[role^=menuitem]")].map((e) => e.textContent?.trim());
    expect(names.slice(0, 2)).toEqual(["Merge cells", "Split cell"]);
    expect(names[2]).toMatch(/^Borders…/);
    expect(names).toContain("Delete rows");
    expect(within(menu).queryByText(/colou?r/i)).toBeNull();
    const item = (name: string) =>
      [...menu.querySelectorAll<HTMLElement>("[role^=menuitem]")].find((e) => e.textContent?.trim() === name)!;
    const disabled = (el: HTMLElement) => el.getAttribute("aria-disabled") === "true" || el.hasAttribute("data-disabled");
    expect(disabled(item("Merge cells"))).toBe(true);
    expect(disabled(item("Split cell"))).toBe(true);
    expect(disabled(item("Delete rows"))).toBe(false);
    act(() => {
      fireEvent.keyDown(menu, { key: "Escape" });
    });
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
  });
});
