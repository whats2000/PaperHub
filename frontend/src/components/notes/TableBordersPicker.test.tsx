import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TableBordersPicker } from "./TableBordersPicker";
import { parseBorders, presetLines, dumpBorders } from "./tableBorders";
import { cleanupEditors, makeEditor, type AnyEditor } from "./testEditor";

afterEach(() => {
  cleanupEditors();
});

const cell = (text: string, props: Record<string, unknown> = {}) => ({
  type: "tableCell",
  props,
  content: text ? [{ type: "text", text, styles: {} }] : [],
});

function tableEditor(rows: unknown[][], borders = ""): AnyEditor {
  return makeEditor([], [
    {
      id: "t",
      type: "table",
      props: borders ? { borders } : {},
      content: { type: "tableContent", columnWidths: rows[0]!.map(() => null), headerRows: 1, rows: rows.map((cells) => ({ cells })) },
    },
  ]);
}

const lines = (editor: AnyEditor) => parseBorders((editor.document[0]!.props as Record<string, unknown>).borders)!;

function open(editor: AnyEditor, selection = { r0: 1, r1: 2, c0: 0, c1: 2 }, onClose = vi.fn()) {
  const anchor = document.createElement("button");
  anchor.textContent = "⌄";
  document.body.appendChild(anchor);
  const view = render(
    <TableBordersPicker editor={editor} blockId="t" selection={selection} anchor={anchor} open onClose={onClose} />,
  );
  return { anchor, onClose, view };
}

const grid3 = () => [
  ["h1", "h2", "h3"],
  ["a", "b", "c"],
  ["d", "e", "f"],
];

describe("the Borders… picker", () => {
  it("Inner sets the selection's inner lines at the weight, reads pressed, and clears on a second click", async () => {
    const editor = tableEditor(grid3());
    open(editor);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/2×3/)).toBeInTheDocument();
    const inner = within(dialog).getByRole("button", { name: "Inner borders" });
    act(() => {
      fireEvent.click(inner);
    });
    const b = lines(editor);
    expect(b.h[2]).toBe("111");
    expect(b.v[1]).toBe("0110");
    await waitFor(() => expect(inner).toHaveAttribute("aria-pressed", "true"));
    expect(dialog.querySelectorAll("svg[role=img] line.on").length).toBeGreaterThan(0);
    act(() => {
      fireEvent.click(inner);
    });
    expect(lines(editor)).toEqual(presetLines(3, 3, 1, "paper"));
  });

  it("Thick then Outer makes the outer lines 2", async () => {
    const editor = tableEditor(grid3());
    open(editor);
    const dialog = await screen.findByRole("dialog");
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: /Thick/ }));
    });
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Outer borders" }));
    });
    const b = lines(editor);
    expect(b.h[1]).toBe("222");
    expect(b.h[3]).toBe("222");
    expect(b.v[1]![0]).toBe("2");
    expect(b.v[2]![3]).toBe("2");
  });

  it("a set the selection has no lines of is disabled and named so", async () => {
    const editor = tableEditor(grid3());
    open(editor, { r0: 1, r1: 1, c0: 0, c1: 2 });
    const dialog = await screen.findByRole("dialog");
    const innerH = within(dialog).getByRole("button", { name: /Inner horizontal borders \(this selection has none/ });
    expect(innerH).toHaveAttribute("aria-disabled", "true");
    const before = (editor.document[0]!.props as Record<string, unknown>).borders;
    act(() => {
      fireEvent.click(innerH);
    });
    expect((editor.document[0]!.props as Record<string, unknown>).borders).toBe(before);
  });

  it("draws no inner line inside a merged cell", async () => {
    const editor = tableEditor([
      [cell("M", { rowspan: 2 }), cell("S", { colspan: 2 })],
      [cell("r"), cell("t")],
      [cell("x"), cell("1"), cell("2")],
    ]);
    open(editor, { r0: 0, r1: 1, c0: 0, c1: 2 });
    const dialog = await screen.findByRole("dialog");
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: "All borders" }));
    });
    const b = lines(editor);
    expect(b.h[1]![0]).toBe("0"); // inside Method's rowspan
    expect(b.v[0]![2]).toBe("0"); // inside the header group's colspan
    expect(b.h[1]![1]).toBe("1");
  });

  it("arrow keys move across the 5-column grid with wrap; Home / End; Enter applies", async () => {
    const editor = tableEditor(grid3());
    open(editor);
    const dialog = await screen.findByRole("dialog");
    const bar = within(dialog).getByRole("toolbar");
    const buttons = within(bar).getAllByRole("button");
    await waitFor(() => expect(document.activeElement).toBe(buttons[0]));
    fireEvent.keyDown(bar, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(buttons[9]);
    fireEvent.keyDown(bar, { key: "ArrowDown" });
    expect(document.activeElement).toBe(buttons[4]);
    fireEvent.keyDown(bar, { key: "Home" });
    expect(document.activeElement).toBe(buttons[0]);
    fireEvent.keyDown(bar, { key: "End" });
    expect(document.activeElement).toBe(buttons[9]);
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(document.activeElement).toBe(buttons[0]);
    expect(buttons[0]).toHaveAttribute("tabindex", "0");
    expect(buttons[1]).toHaveAttribute("tabindex", "-1");
  });

  it("Escape closes it and hands the focus back to ⌄", async () => {
    const editor = tableEditor(grid3());
    const { onClose } = open(editor);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("each click is one undo step", async () => {
    const editor = tableEditor(grid3(), dumpBorders(presetLines(3, 3, 1, "paper")));
    open(editor);
    const dialog = await screen.findByRole("dialog");
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: "All borders" }));
    });
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Clear borders" }));
    });
    act(() => {
      editor.undo();
    });
    expect(lines(editor).v[1]).toBe("1111");
    act(() => {
      editor.undo();
    });
    expect(lines(editor)).toEqual(presetLines(3, 3, 1, "paper"));
  });
});
