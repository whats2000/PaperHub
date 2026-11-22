import { afterEach, describe, expect, it } from "vitest";

import { handleSpot, isCellMenuKey, menuSide, outlineClip, outlineMask, unionBox } from "./cellHandleGeometry";
import { TextSelection } from "@tiptap/pm/state";

import { clearCells, filledCells, restoreCells, selectCells, selectedAlignment, tableSelection } from "./tableMerge";
import { cleanupEditors, makeEditor, type AnyEditor } from "./testEditor";

afterEach(() => cleanupEditors());

const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom });

describe("the cell handle's geometry", () => {
  it("the outline is the selected cells' union", () => {
    expect(unionBox([box(10, 20, 60, 40), box(60, 20, 120, 40), box(10, 40, 60, 70)])).toEqual(box(10, 20, 120, 70));
    expect(unionBox([])).toBeNull();
  });

  it("the handle sits on the right border, halfway down the part in view", () => {
    expect(handleSpot(box(10, 100, 200, 160), box(0, 0, 500, 900))).toEqual({ x: 200, y: 130, visible: true });
    // The selection runs above the pane's top: centred on what shows.
    expect(handleSpot(box(10, -100, 200, 100), box(0, 0, 500, 900))).toEqual({ x: 200, y: 50, visible: true });
  });

  it("hides when the selection's right edge is scrolled out of the table's box", () => {
    expect(handleSpot(box(10, 100, 600, 160), box(0, 0, 500, 900)).visible).toBe(false);
    expect(handleSpot(box(-300, 100, -10, 160), box(0, 0, 500, 900)).visible).toBe(false);
    // On the box's right edge (a table filling its box): still shown.
    expect(handleSpot(box(10, 100, 501, 160), box(0, 0, 500, 900)).visible).toBe(true);
  });

  it("the outline is clipped where the box hides the selection", () => {
    expect(outlineClip(box(-40, 0, 700, 10), box(0, 0, 500, 900))).toEqual({ left: 40, right: 200 });
    expect(outlineClip(box(10, 0, 200, 10), box(0, 0, 500, 900))).toEqual({ left: 0, right: 0 });
  });

  it("the outline fades under the table box's edge fades, as the cells do", () => {
    // Box 0..500, fades 28 / 28; the outline from 300 to 520 (box right at 200 in its own px).
    expect(outlineMask(box(300, 0, 520, 10), box(0, 0, 500, 900), 28, 28)).toBe(
      "linear-gradient(to right, transparent -300px, #000 -272px, #000 172px, transparent 200px)",
    );
    // No fade on either side: no mask.
    expect(outlineMask(box(300, 0, 520, 10), box(0, 0, 500, 900), 0, 0)).toBe("");
  });

  it("the menu opens right of the handle with room, else under the selection, else over it", () => {
    const size = { width: 224, height: 300 };
    const pane = box(0, 0, 800, 900);
    expect(menuSide(box(400, 100, 412, 122), box(100, 90, 406, 132), pane, size)).toBe("right");
    expect(menuSide(box(600, 100, 612, 122), box(100, 90, 606, 132), pane, size)).toBe("bottom");
    expect(menuSide(box(600, 700, 612, 722), box(100, 690, 606, 732), pane, size)).toBe("top");
  });

  it("Ctrl or ⌘ + /, the context menu key and Shift+F10 open the cell menu", () => {
    const k = (key: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "shiftKey" | "altKey", boolean>> = {}) =>
      isCellMenuKey({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
    expect(k("/", { ctrlKey: true })).toBe(true);
    expect(k("/", { metaKey: true })).toBe(true);
    expect(k("ContextMenu")).toBe(true);
    expect(k("F10", { shiftKey: true })).toBe(true);
    expect(k("/")).toBe(false);
    expect(k("F10")).toBe(false);
  });
});

function table(): unknown[] {
  return [
    {
      id: "t",
      type: "table",
      content: {
        type: "tableContent",
        columnWidths: [null, null, null],
        headerRows: 1,
        rows: [{ cells: ["h1", "h2", "h3"] }, { cells: ["a", "", "c"] }, { cells: ["d", "e", "f"] }],
      },
    },
  ];
}
const texts = (editor: AnyEditor) =>
  (editor.document[0]!.content as unknown as { rows: { cells: { content: { text?: string }[] }[] }[] }).rows.map((r) =>
    r.cells.map((c) => c.content.map((x) => x.text ?? "").join("")),
  );

describe("clearing the selected cells", () => {
  it("lists only the selected cells that hold something", () => {
    const editor = makeEditor([], table());
    selectCells(editor, [1, 0], [2, 1]);
    expect(filledCells(editor._tiptapEditor.view.state).map((c) => [c.row, c.col])).toEqual([[1, 0], [2, 0], [2, 1]]);
    selectCells(editor, [1, 1], [1, 1]);
    expect(filledCells(editor._tiptapEditor.view.state)).toEqual([]);
  });

  it("empties them in one step, and Undo puts the text back", () => {
    const editor = makeEditor([], table());
    selectCells(editor, [1, 0], [2, 1]);
    const cleared = clearCells(editor);
    expect(cleared?.cells).toHaveLength(3);
    expect(texts(editor)).toEqual([["h1", "h2", "h3"], ["", "", "c"], ["", "", "f"]]);
    restoreCells(editor, cleared!);
    expect(texts(editor)).toEqual([["h1", "h2", "h3"], ["a", "", "c"], ["d", "e", "f"]]);
  });

  it("the selection stays on the cleared cells (a caret in its cell)", () => {
    const editor = makeEditor([], table());
    const view = editor._tiptapEditor.view;
    selectCells(editor, [1, 0], [1, 0]);
    const $cell = (view.state.selection as unknown as { $anchorCell: { pos: number } }).$anchorCell;
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, $cell.pos + 3)));
    clearCells(editor);
    expect(tableSelection(view.state)?.rect).toEqual({ r0: 1, r1: 1, c0: 0, c1: 0 });
    selectCells(editor, [1, 0], [2, 1]);
    clearCells(editor);
    expect(tableSelection(view.state)?.rect).toEqual({ r0: 1, r1: 2, c0: 0, c1: 1 });
  });

  it("Undo leaves a cell typed into since then alone", () => {
    const editor = makeEditor([], table());
    selectCells(editor, [1, 0], [1, 0]);
    const cleared = clearCells(editor)!;
    selectCells(editor, [1, 0], [1, 0]);
    const view = editor._tiptapEditor.view;
    const { $anchorCell } = view.state.selection as unknown as { $anchorCell: { pos: number } };
    view.dispatch(view.state.tr.insertText("new", $anchorCell.pos + 2));
    restoreCells(editor, cleared);
    expect(texts(editor)[1]![0]).toBe("new");
  });

  it("reads the selected columns' alignment, none when they differ", () => {
    const editor = makeEditor([], table());
    selectCells(editor, [1, 0], [1, 0]);
    expect(selectedAlignment(editor._tiptapEditor.view.state)).toBe("left");
  });
});
