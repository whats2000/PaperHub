import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { toast } from "sonner";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { TextSelection } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { NoteEditor } from "@/components/notes/NoteEditor";
import indexCss from "@/index.css?raw";
import { API_BASE_URL } from "@/lib/api";
import { clearPendingNoteOps, useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

import { selectCells } from "./tableMerge";
import { cleanupEditors } from "./testEditor";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterAll(() => server.close());
afterEach(() => {
  cleanupEditors();
  clearPendingNoteOps();
  server.resetHandlers();
  vi.restoreAllMocks();
});

/** jsdom has no layout: cells get a grid of rects (100 × 30 from 100, 100),
 * the rest the 1000 × 1000 page. */
function fakeLayout() {
  const rect = (left: number, top: number, width: number, height: number) =>
    ({ x: left, y: top, left, top, right: left + width, bottom: top + height, width, height, toJSON: () => ({}) }) as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this instanceof HTMLTableCellElement) {
      const row = (this.parentElement as HTMLTableRowElement).rowIndex;
      return rect(100 + this.cellIndex * 100, 100 + row * 30, 100 * this.colSpan, 30 * this.rowSpan);
    }
    if (this.classList.contains("note-cell-layer")) return rect(0, 0, 0, 0);
    return rect(0, 0, 1000, 1000);
  });
  if (!("getBoundingClientRect" in Range.prototype)) {
    Object.defineProperty(Range.prototype, "getBoundingClientRect", { configurable: true, value: () => rect(0, 0, 0, 0) });
    Object.defineProperty(Range.prototype, "getClientRects", { configurable: true, value: () => [] });
  }
  if (!document.elementFromPoint) {
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => null });
    Object.defineProperty(document, "elementsFromPoint", { configurable: true, value: () => [] });
  }
}

beforeEach(() => {
  server.use(http.get(`${API_BASE_URL}/marks/colors`, () => HttpResponse.json([])));
  fakeLayout();
  vi.mocked(toast).mockClear();
});

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

function seed(cells: string[][]) {
  useNotesStore.setState({
    notes: {
      7: {
        note: { id: 7, target_kind: "session", target_id: "1", title: "T", revision: 1, updated_at: "2026-11-22T00:00:00Z" },
        blocks: [
          blk("t", {
            type: "table",
            content: { type: "tableContent", columnWidths: cells[0]!.map(() => null), headerRows: 1, rows: cells.map((c) => ({ cells: c })) },
          }),
          blk("after", { content: [{ type: "text", text: "after", styles: {} }], text: "after" }),
        ],
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

type PM = HTMLElement & { editor: { view: EditorView } };

function mount(cells: string[][] = [["a", "b"], ["1", "2"]]) {
  seed(cells);
  act(() => {
    render(<NoteEditor noteId={7} variant="pane" />);
  });
  return document.querySelector(".ProseMirror") as PM;
}

/** The caret into a cell (row r, column c) of the table, the editor focused. */
function caretIn(pm: PM, r: number, c: number) {
  const view = pm.editor.view;
  act(() => {
    pm.focus();
    selectCells({ _tiptapEditor: { view } }, [r, c], [r, c]);
    const $cell = (view.state.selection as unknown as { $anchorCell: { pos: number } }).$anchorCell;
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, $cell.pos + 2)));
  });
}

/** The caret: a text selection's cell text and offset in it (null for any other selection). */
function caret(pm: PM): { text: string; offset: number } | null {
  const sel = pm.editor.view.state.selection;
  if (!(sel instanceof TextSelection) || !sel.empty || sel.$from.parent.type.name !== "tableParagraph") return null;
  return { text: sel.$from.parent.textContent, offset: sel.$from.parentOffset };
}

const HIDDEN = { hidden: true } as const;
const handle = () => document.querySelector<HTMLButtonElement>(".note-cell-handle")!;
const outline = () => document.querySelector<HTMLElement>(".note-cell-outline")!;
const items = (menu: HTMLElement) => [...menu.querySelectorAll<HTMLElement>("[role=menuitem], [role=menuitemradio]")];
const names = (menu: HTMLElement) => items(menu).map((e) => e.textContent?.trim());
const item = (menu: HTMLElement, name: string) => items(menu).find((e) => e.textContent?.trim().startsWith(name))!;

describe("the selected cells' outline and ⋮ handle (Notion)", () => {
  it("a caret in a cell shows the outline and the handle centred on its right border", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    expect(outline()).toHaveAttribute("data-on");
    // Cell (1, 0): 100..200 × 130..160 — the handle at x 200, y 145.
    expect([handle().style.left, handle().style.top]).toEqual(["200px", "145px"]);
    expect([outline().style.left, outline().style.top, outline().style.width, outline().style.height]).toEqual([
      "100px",
      "130px",
      "100px",
      "30px",
    ]);
    expect(handle()).toHaveAccessibleName(/Cell menu \((Ctrl|⌘)/);
  });

  it("a range: the outline wraps it, the handle on its right border", async () => {
    const pm = mount([["a", "b", "c"], ["1", "2", "3"], ["x", "y", "z"]]);
    act(() => {
      pm.focus();
      selectCells({ _tiptapEditor: { view: pm.editor.view } }, [1, 0], [2, 1]);
    });
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    expect([outline().style.left, outline().style.top, outline().style.width, outline().style.height]).toEqual([
      "100px",
      "130px",
      "200px",
      "60px",
    ]);
    expect([handle().style.left, handle().style.top]).toEqual(["300px", "160px"]);
  });

  it("the outline takes the table box's edge fade (it sits under it, as the cells do)", async () => {
    const pm = mount();
    const wrap = pm.querySelector<HTMLElement>(".tableWrapper")!;
    wrap.style.setProperty("--note-table-fade-r", "28px");
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    // Box 0..1000 (fake layout), outline from 100: fade from 872 to 900 px in it.
    expect(outline().style.maskImage).toBe(
      "linear-gradient(to right, transparent -100px, rgb(0, 0, 0) -100px, rgb(0, 0, 0) 872px, transparent 900px)",
    );
  });

  it("leaving the table hides both, kept in place to animate out", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    const view = pm.editor.view;
    act(() => {
      view.dispatch(view.state.tr.setSelection(TextSelection.atEnd(view.state.doc)));
    });
    await waitFor(() => expect(handle()).not.toHaveAttribute("data-on"));
    expect(outline()).not.toHaveAttribute("data-on");
    expect(handle()).toBeInTheDocument();
    expect(handle().tabIndex).toBe(-1);
  });

  it("BlockNote's ⌄ on the hovered cell is gone", async () => {
    const pm = mount();
    act(() => {
      fireEvent.mouseMove(pm.querySelectorAll("td, th")[3]!);
    });
    await screen.findByRole("button", { name: "Column options" });
    expect(document.querySelector(".bn-table-cell-handle")).toBeNull();
  });

  it("a drag in the editor turns the handle's pointer events off until the button is up", () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    const layer = document.querySelector(".note-cell-layer")!;
    act(() => {
      fireEvent.pointerDown(pm.querySelector("td")!, { button: 0 });
    });
    expect(layer).toHaveAttribute("data-dragging");
    act(() => {
      fireEvent.pointerUp(document);
    });
    expect(layer).not.toHaveAttribute("data-dragging");
    expect(indexCss).toMatch(/\.note-cell-layer\[data-dragging\]\s+\.note-cell-handle\s*\{[^}]*pointer-events:\s*none/);
  });

  it("is a 12 × 22 blue pill with a ring, fading and scaling in and out in 120 ms (still under reduced motion)", () => {
    const rule = /\.note-cell-handle\s*\{([^}]*)\}/.exec(indexCss)?.[1] ?? "";
    expect(rule).toMatch(/width:\s*12px/);
    expect(rule).toMatch(/height:\s*22px/);
    expect(rule).toMatch(/border-radius:\s*6px/);
    expect(rule).toMatch(/background:\s*var\(--note-select\)/);
    expect(rule).toMatch(/0 0 0 1\.5px/);
    expect(rule).toMatch(/opacity 120ms[^;]*transform 120ms/);
    expect(indexCss).toMatch(/\.note-cell-handle::before\s*\{[^}]*inset:\s*-5px -7px/);
    expect(indexCss).toMatch(/\.note-cell-outline\s*\{[^}]*border:\s*2px solid var\(--note-select\)/);
    expect(indexCss).toMatch(/prefers-reduced-motion[\s\S]*\.note-cell-(handle|outline)[^{]*\{[^}]*transition:\s*none/);
  });
});

describe("the cell menu, from the handle", () => {
  it("lists merge, split, borders, clear; rows; alignment; delete — no colour; a second click closes it", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    expect(names(menu)).toEqual([
      "Merge cells",
      "Split cell",
      expect.stringMatching(/^Borders…/),
      "Clear contents",
      "Insert above",
      "Insert below",
      "Align left",
      "Align centre",
      "Align right",
      "Delete rows",
    ]);
    expect(within(menu).queryByText(/colou?r/i)).toBeNull();
    const off = (name: string) => item(menu, name).getAttribute("aria-disabled") === "true";
    expect(off("Merge cells")).toBe(true);
    expect(off("Split cell")).toBe(true);
    expect(off("Clear contents")).toBe(false);
    expect(item(menu, "Align left")).toHaveAttribute("aria-checked", "true");
    expect(handle()).toHaveAttribute("aria-expanded", "true");
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    expect(handle()).toHaveAttribute("aria-expanded", "false");
  });

  it("mouse-opened: Esc closes it and the caret stays in the cell", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.keyDown(pm, { key: "Escape" });
    });
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    expect(document.activeElement).toBe(pm);
  });

  it("mouse-opened on a cell selection: Esc puts the caret at the end of the anchor cell's text", async () => {
    const pm = mount([["a", "b"], ["12", "3"]]);
    act(() => {
      pm.focus();
      selectCells({ _tiptapEditor: { view: pm.editor.view } }, [1, 0], [1, 1]);
    });
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.keyDown(pm, { key: "Escape" });
    });
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    await waitFor(() => expect(caret(pm)).toEqual({ text: "12", offset: 2 }));
    expect(document.activeElement).toBe(pm);
  });

  it("Ctrl+/ in a cell opens it on its first item; arrows, Home and End move; Esc puts the caret back in the cell", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.keyDown(pm, { key: "/", ctrlKey: true });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    // Merge and Split are off on one plain cell: Borders… is the first.
    await waitFor(() => expect(document.activeElement).toBe(item(menu, "Borders…")));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    });
    expect(document.activeElement).toBe(item(menu, "Clear contents"));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "End" });
    });
    // Delete rows is greyed in a 2-row table: End stops on Align right.
    expect(document.activeElement).toBe(item(menu, "Align right"));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    });
    expect(document.activeElement).toBe(item(menu, "Borders…"));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "Home" });
    });
    expect(document.activeElement).toBe(item(menu, "Borders…"));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    });
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    // Where it was (caretIn: before the cell's text), focused: typing goes on at once.
    await waitFor(() => expect(document.activeElement).toBe(pm));
    expect(caret(pm)).toEqual({ text: "1", offset: 0 });
  });

  it("Enter on the focused handle opens it from the keyboard", async () => {
    const pm = mount();
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      handle().focus();
      fireEvent.click(handle(), { detail: 0 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    await waitFor(() => expect(menu.contains(document.activeElement)).toBe(true));
  });

  it("Clear contents empties the selected cells and offers Undo; greyed once they are empty", async () => {
    const pm = mount([["a", "b"], ["1", "2"]]);
    act(() => {
      pm.focus();
      selectCells({ _tiptapEditor: { view: pm.editor.view } }, [1, 0], [1, 1]);
    });
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(item(menu, "Clear contents"));
    });
    const row = () => [...pm.querySelectorAll("tr")[1]!.querySelectorAll("td")].map((td) => td.textContent);
    expect(row()).toEqual(["", ""]);
    const [message, options] = vi.mocked(toast).mock.calls[0]!;
    expect(message).toBe("Cleared 2 cells");
    expect((options as unknown as { action: { label: string } }).action.label).toBe("Undo");
    // Greyed now.
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const again = await screen.findByRole("menu", HIDDEN);
    expect(item(again, "Clear contents")).toHaveAttribute("aria-disabled", "true");
    // Undo puts the text back.
    const undo = vi.mocked(toast).mock.calls[0]![1] as unknown as { action: { onClick: () => void } };
    act(() => undo.action.onClick());
    expect(row()).toEqual(["1", "2"]);
  });

  it("Borders… opens the picker on the selection's lines", async () => {
    const pm = mount();
    caretIn(pm, 1, 1);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(item(menu, "Borders…"));
    });
    const dialog = await screen.findByRole("dialog", { name: /Borders · 1×1 cells/, ...HIDDEN });
    expect(within(dialog).getAllByRole("button", HIDDEN).length).toBeGreaterThanOrEqual(12);
    // The handle stays while the picker is open; Esc puts the caret back in the cell.
    expect(handle()).toHaveAttribute("data-on");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    });
    await waitFor(() => expect(document.activeElement).toBe(pm));
    expect(caret(pm)).toEqual({ text: "2", offset: 0 });
    expect(handle()).toHaveAttribute("data-on");
  });

  it("Split is on for one merged cell, Merge off", async () => {
    const pm = mount([["a", "b"], ["1", "2"], ["x", "y"]]);
    act(() => {
      pm.focus();
      selectCells({ _tiptapEditor: { view: pm.editor.view } }, [1, 0], [1, 1]);
    });
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    act(() => {
      fireEvent.click(item(screen.getByRole("menu", HIDDEN), "Merge cells"));
    });
    caretIn(pm, 1, 0);
    await waitFor(() => expect(screen.queryByRole("menu", HIDDEN)).toBeNull());
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    expect(item(menu, "Split cell")).not.toHaveAttribute("aria-disabled");
    expect(item(menu, "Merge cells")).toHaveAttribute("aria-disabled", "true");
  });

  it("Delete rows is greyed when the table has 2 rows or fewer (the header and one row stay)", async () => {
    const pm = mount([["a", "b"], ["1", "2"]]);
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    expect(item(menu, "Delete rows")).toHaveAttribute("aria-disabled", "true");
  });

  it("Delete rows is on with 3 rows", async () => {
    const pm = mount([["a", "b"], ["1", "2"], ["x", "y"]]);
    caretIn(pm, 1, 0);
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    expect(item(menu, "Delete rows")).not.toHaveAttribute("aria-disabled");
    act(() => {
      fireEvent.click(item(menu, "Delete rows"));
    });
    expect(pm.querySelectorAll("tr")).toHaveLength(2);
  });

  it("merges a selected range from the menu", async () => {
    const pm = mount([["a", "b"], ["1", "2"], ["x", "y"]]);
    act(() => {
      pm.focus();
      selectCells({ _tiptapEditor: { view: pm.editor.view } }, [1, 0], [1, 1]);
    });
    await waitFor(() => expect(handle()).toHaveAttribute("data-on"));
    act(() => {
      fireEvent.click(handle(), { detail: 1 });
    });
    const menu = await screen.findByRole("menu", HIDDEN);
    act(() => {
      fireEvent.click(item(menu, "Merge cells"));
    });
    expect(pm.querySelectorAll("tr")[1]!.querySelector("td")).toHaveAttribute("colspan", "2");
  });
});
