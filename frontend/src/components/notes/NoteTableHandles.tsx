/** A table's handles, the Notion way, on BlockNote's own table handles.
 *
 * A ⠿ handle on each row (left) and column (top) drags to reorder (BlockNote)
 * and opens a menu: insert above / below / left / right, delete, and a
 * column's alignment (GFM `:--` `:-:` `--:`). The table's ⋯ (top right) holds
 * the header row / header column switches and Add / Remove caption and
 * notes (TableCaption.tsx). The "+" extend buttons are
 * BlockNote's. The ⌄ on a cell opens the cell menu: merge / split, the
 * Borders… picker, rows, alignment, delete. No cell colours.
 */
import { getNodeById } from "@blocknote/core";
import { TableHandlesExtension } from "@blocknote/core/extensions";
import {
  GenericPopover,
  TableHandle,
  TableHandlesController,
  useBlockNoteEditor,
  useComponentsContext,
  useExtension,
  useEditorState,
  useExtensionState,
  usePortalElement,
  type GenericPopoverReference,
  type TableCellButtonProps,
  type TableHandleProps,
} from "@blocknote/react";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ChevronDown,
  Combine,
  Ellipsis,
  Grid2x2,
  SquareSplitHorizontal,
  GripVertical,
  Trash2,
} from "lucide-react";
import { useMemo, useRef, useState, type FC } from "react";
import { useTranslation } from "react-i18next";

import { useNotesStore } from "@/store/notes";
import type { TableContent } from "@/types/notes";

import type { TablePart } from "./noteFind";
import { partRequests } from "./tableCaptions";
import { columnAlignment, deleteRowOrColumn, setColumnAlignment, type Alignment } from "./tableKeys";
import { shrinkAway } from "./tableMotion";
import { bordersOf, dumpBorders, presetLines, tableShape, type Preset, type Selection } from "./tableBorders";
import { headerDepth, tableGrid } from "./tableGrid";
import {
  alignSelectedColumns,
  canMerge,
  canSplit,
  deleteSelectedRows,
  insertRow,
  mergeSelected,
  splitSelected,
  tableSelection,
} from "./tableMerge";

const ICON = "size-4";

/** The menu of a row's or a column's ⠿ handle. */
function NoteTableHandleMenu({ orientation }: { orientation: "row" | "column" }) {
  const { t } = useTranslation();
  const C = useComponentsContext()!;
  const editor = useBlockNoteEditor();
  const tableHandles = useExtension(TableHandlesExtension);
  const { block, index } = useExtensionState(TableHandlesExtension, {
    selector: (s) => ({ block: s?.block, index: orientation === "column" ? s?.colIndex : s?.rowIndex }),
  });
  if (!block || index === undefined) return null;
  // The row / column shrinks away, then goes (BlockNote's delete is instant).
  const remove = () =>
    shrinkAway(editor.prosemirrorView, block.id, orientation, index, (at) =>
      deleteRowOrColumn(editor, block.id, orientation, at),
    );

  if (orientation === "row") {
    return (
      <C.Generic.Menu.Dropdown className="bn-table-handle-menu">
        <C.Generic.Menu.Item
          icon={<ArrowUp className={ICON} />}
          onClick={() => tableHandles.addRowOrColumn(index, { orientation: "row", side: "above" })}
        >
          {t("notes:table.insertAbove", "Insert above")}
        </C.Generic.Menu.Item>
        <C.Generic.Menu.Item
          icon={<ArrowDown className={ICON} />}
          onClick={() => tableHandles.addRowOrColumn(index, { orientation: "row", side: "below" })}
        >
          {t("notes:table.insertBelow", "Insert below")}
        </C.Generic.Menu.Item>
        <C.Generic.Menu.Divider />
        <C.Generic.Menu.Item
          icon={<Trash2 className={ICON} />}
          onClick={remove}
        >
          {t("notes:table.deleteRow", "Delete row")}
        </C.Generic.Menu.Item>
      </C.Generic.Menu.Dropdown>
    );
  }

  const current = columnAlignment(block.content as unknown as TableContent, index);
  const align = (a: Alignment) => () => setColumnAlignment(editor, block.id, index, a);
  return (
    <C.Generic.Menu.Dropdown className="bn-table-handle-menu">
      <C.Generic.Menu.Item
        icon={<ArrowLeft className={ICON} />}
        onClick={() => tableHandles.addRowOrColumn(index, { orientation: "column", side: "left" })}
      >
        {t("notes:table.insertLeft", "Insert left")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item
        icon={<ArrowRight className={ICON} />}
        onClick={() => tableHandles.addRowOrColumn(index, { orientation: "column", side: "right" })}
      >
        {t("notes:table.insertRight", "Insert right")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Divider />
      <C.Generic.Menu.Item icon={<AlignLeft className={ICON} />} checked={current === "left"} onClick={align("left")}>
        {t("notes:table.alignLeft", "Align left")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item icon={<AlignCenter className={ICON} />} checked={current === "center"} onClick={align("center")}>
        {t("notes:table.alignCenter", "Align centre")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item icon={<AlignRight className={ICON} />} checked={current === "right"} onClick={align("right")}>
        {t("notes:table.alignRight", "Align right")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Divider />
      <C.Generic.Menu.Item
        icon={<Trash2 className={ICON} />}
        onClick={remove}
      >
        {t("notes:table.deleteColumn", "Delete column")}
      </C.Generic.Menu.Item>
    </C.Generic.Menu.Dropdown>
  );
}

/** A row's or a column's ⠿: BlockNote's handle (drag to reorder) with
 * PaperHub's menu. */
function NoteTableHandle(props: TableHandleProps) {
  const { t } = useTranslation();
  const label =
    props.orientation === "row"
      ? t("notes:table.rowOptions", "Row options")
      : t("notes:table.columnOptions", "Column options");
  return (
    <TableHandle {...props} tableHandleMenu={NoteTableHandleMenu as FC}>
      <GripVertical className="size-4" aria-hidden />
      <span className="sr-only">{label}</span>
    </TableHandle>
  );
}

/** Where the Borders… picker opens: the ⌄ it came from, the table and the
 * selected grid cells (Task 10's `TableBordersPicker`). */
export type BordersRequest = { anchor: HTMLElement; blockId: string; selection: Selection };

/** The cell menu (the ⌄ on the hovered cell): merge / split, borders,
 * rows above and below, the selected columns' alignment, delete rows.
 * Merge works on a cell selection; Split on one merged cell (the selected
 * one, else the hovered one). No colours. */
function NoteTableCellMenu({ onBorders, trigger }: { onBorders: (req: BordersRequest) => void; trigger: () => HTMLElement | null }) {
  const { t } = useTranslation();
  const C = useComponentsContext()!;
  const editor = useBlockNoteEditor();
  const tableHandles = useExtension(TableHandlesExtension);
  const { block, row, col } = useExtensionState(TableHandlesExtension, {
    selector: (s) => ({ block: s?.block, row: s?.rowIndex, col: s?.colIndex }),
  });
  const can = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const state = e.prosemirrorState;
      return { merge: canMerge(state), split: canSplit(state), inTable: tableSelection(state) !== null };
    },
  });
  if (!block || row === undefined || col === undefined) return null;
  const hovered = (block.content as unknown as TableContent).rows[row]?.cells[col];
  const hoveredProps =
    hovered && typeof hovered === "object" && !Array.isArray(hovered) && "props" in hovered
      ? ((hovered.props ?? {}))
      : {};
  const hoveredMerged = Number(hoveredProps.colspan ?? 1) > 1 || Number(hoveredProps.rowspan ?? 1) > 1;
  const canSplitHere = can.split || (!can.inTable && hoveredMerged);
  // The commands act on the selection; with none in this table, on the
  // hovered cell.
  const focusHovered = () => {
    if (!can.inTable) {
      const view = editor.prosemirrorView;
      view.dispatch(view.state.tr.setSelection(tableHandles.setCellSelection(view.state, { row, col }).selection));
    }
  };
  const selection = (): Selection => {
    const sel = tableSelection(editor.prosemirrorState);
    if (sel?.blockId === block.id) return sel.rect;
    const { grid } = tableGrid(block.content as unknown as TableContent);
    const a = grid[row]?.find((g) => !Array.isArray(g) && g.index === col && g.row === row);
    return a && !Array.isArray(a)
      ? { r0: a.row, r1: a.row + a.rowspan - 1, c0: a.col, c1: a.col + a.colspan - 1 }
      : { r0: row, r1: row, c0: col, c1: col };
  };
  const align = (a: Alignment) => () => {
    focusHovered();
    alignSelectedColumns(editor, a);
  };
  return (
    <C.Generic.Menu.Dropdown className="bn-table-handle-menu">
      <C.Generic.Menu.Item icon={<Combine className={ICON} />} disabled={!can.merge} onClick={() => mergeSelected(editor)}>
        {t("notes:table.merge", "Merge cells")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item
        icon={<SquareSplitHorizontal className={ICON} />}
        disabled={!canSplitHere}
        onClick={() => {
          focusHovered();
          splitSelected(editor);
        }}
      >
        {t("notes:table.split", "Split cell")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item
        icon={<Grid2x2 className={ICON} />}
        onClick={() => {
          const anchor = trigger();
          if (anchor) onBorders({ anchor, blockId: block.id, selection: selection() });
        }}
      >
        <span className="flex min-w-0 flex-col">
          <span>{t("notes:table.borders", "Borders…")}</span>
          <small className="text-[11px] text-muted-foreground">
            {t("notes:table.bordersHint", "Pick a weight, then click an icon")}
          </small>
        </span>
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Divider />
      <C.Generic.Menu.Item
        icon={<ArrowUp className={ICON} />}
        onClick={() => {
          focusHovered();
          insertRow(editor, "above");
        }}
      >
        {t("notes:table.insertAbove", "Insert above")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item
        icon={<ArrowDown className={ICON} />}
        onClick={() => {
          focusHovered();
          insertRow(editor, "below");
        }}
      >
        {t("notes:table.insertBelow", "Insert below")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Divider />
      <C.Generic.Menu.Item icon={<AlignLeft className={ICON} />} onClick={align("left")}>
        {t("notes:table.alignLeft", "Align left")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item icon={<AlignCenter className={ICON} />} onClick={align("center")}>
        {t("notes:table.alignCenter", "Align centre")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Item icon={<AlignRight className={ICON} />} onClick={align("right")}>
        {t("notes:table.alignRight", "Align right")}
      </C.Generic.Menu.Item>
      <C.Generic.Menu.Divider />
      <C.Generic.Menu.Item
        icon={<Trash2 className={ICON} />}
        onClick={() => {
          focusHovered();
          deleteSelectedRows(editor);
        }}
      >
        {t("notes:table.deleteRows", "Delete rows")}
      </C.Generic.Menu.Item>
    </C.Generic.Menu.Dropdown>
  );
}

/** The ⌄ on the hovered cell, opening the cell menu (BlockNote's cell
 * handle, with PaperHub's menu). */
function makeCellHandle(onBorders: (req: BordersRequest) => void) {
  return function NoteTableCellHandle(props: TableCellButtonProps) {
    const { t } = useTranslation();
    const C = useComponentsContext()!;
    const editor = useBlockNoteEditor();
    const portalElement = usePortalElement();
    const tableHandles = useExtension(TableHandlesExtension);
    const ref = useRef<HTMLSpanElement>(null);
    return (
      <C.Generic.Menu.Root
        position="right"
        portalElement={portalElement}
        onOpenChange={(open: boolean) => {
          if (open) {
            tableHandles.freezeHandles();
            props.hideOtherElements(true);
          } else {
            tableHandles.unfreezeHandles();
            props.hideOtherElements(false);
            editor.focus();
          }
        }}
      >
        <C.Generic.Menu.Trigger>
          <C.Generic.Menu.Button className="bn-table-cell-handle note-table-cell-handle">
            <span ref={ref} className="flex items-center" aria-hidden>
              <ChevronDown className="size-3" />
            </span>
            <span className="sr-only">{t("notes:table.cellOptions", "Cell options")}</span>
          </C.Generic.Menu.Button>
        </C.Generic.Menu.Trigger>
        <NoteTableCellMenu
          onBorders={onBorders}
          trigger={() => ref.current?.closest<HTMLElement>(".bn-table-cell-handle") ?? null}
        />
      </C.Generic.Menu.Root>
    );
  };
}

/** Floating 6 px above the table's top right corner. */
const ABOVE = { name: "paperhubAbove", fn: ({ x, y }: { x: number; y: number }) => ({ x, y: y - 6 }) };

/** One preset in the table's ⋯: its name over its hint. */
function PresetLabel({ name, hint }: { name: string; hint: string }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span>{name}</span>
      <small className="max-w-[16rem] text-[11px] whitespace-normal text-muted-foreground">{hint}</small>
    </span>
  );
}

/** The table's ⋯ (top right): its preset (Paper / Notion, also the next
 * new table's default), header row / header column, caption and notes,
 * and the edit guides switch. */
function TableMoreMenu() {
  const { t } = useTranslation();
  const C = useComponentsContext()!;
  const editor = useBlockNoteEditor();
  const portalElement = usePortalElement();
  const tableHandles = useExtension(TableHandlesExtension);
  const state = useExtensionState(TableHandlesExtension);

  const reference = useMemo<GenericPopoverReference | undefined>(() => {
    if (!state?.block) return undefined;
    const info = getNodeById(state.block.id, editor.prosemirrorState.doc);
    if (!info) return undefined;
    const table = editor.prosemirrorView.domAtPos(info.posBeforeNode + 2).node;
    return table instanceof Element ? { element: table } : undefined;
  }, [editor, state]);

  const block = state?.block;
  const show = Boolean(state?.show && block && !state.draggingState);
  const content = block?.content as unknown as TableContent | undefined;
  const headerRow = Boolean(content?.headerRows);
  const headerCol = Boolean(content?.headerCols);
  const setHeader = (key: "headerRows" | "headerCols", on: boolean) => {
    if (!block) return;
    const now = (editor.getBlock(block.id)?.content ?? block.content) as unknown as TableContent;
    // The header row covers every row a row-0 cell spans into.
    const value = on ? (key === "headerRows" ? headerDepth({ ...now, headerRows: 1 }) : 1) : undefined;
    editor.updateBlock(block.id, { type: "table", content: { ...now, [key]: value } } as never);
  };
  const tableGuides = useNotesStore((s) => s.tableGuides);
  const setTableGuides = useNotesStore((s) => s.setTableGuides);
  const setTablePreset = useNotesStore((s) => s.setTablePreset);
  // The table's caption / notes as they are now (the handles' block is a
  // snapshot from the last hover).
  const blockId = block?.id;
  const parts = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const props = blockId ? (e.getBlock(blockId)?.props as Record<string, unknown> | undefined) : undefined;
      const got = blockId ? e.getBlock(blockId) : undefined;
      const preset =
        got?.type === "table"
          ? bordersOf(props ?? {}, got.content as unknown as TableContent).preset
          : ("paper" as Preset);
      return { caption: Boolean(props?.caption), notes: Boolean(props?.notes), preset };
    },
  });
  // A preset re-draws this table and becomes the next new table's default.
  const choosePreset = (p: Preset) => {
    if (!block) return;
    const now = editor.getBlock(block.id)?.content as unknown as TableContent | undefined;
    if (!now) return;
    const s = tableShape(now);
    editor.updateBlock(block.id, { props: { borders: dumpBorders(presetLines(s.rows, s.cols, s.headerRows, p)) } });
    setTablePreset(p);
  };
  // "Add …" opens the part with the caret in it: the closing menu must not
  // hand the focus back to the editor.
  const keepFocus = useRef(false);
  const partItem = (part: TablePart) => {
    const has = parts[part];
    const onClick = () => {
      if (!block) return;
      if (has) editor.updateBlock(block.id, { props: { [part]: "" } });
      else {
        keepFocus.current = true;
        partRequests(editor).request(block.id, part);
      }
    };
    const text =
      part === "caption"
        ? has
          ? t("notes:table.removeCaption", "Remove caption")
          : t("notes:table.addCaption", "Add caption")
        : has
          ? t("notes:table.removeNotes", "Remove notes")
          : t("notes:table.addNotes", "Add notes");
    return (
      <C.Generic.Menu.Item key={part} onClick={onClick}>
        {text}
      </C.Generic.Menu.Item>
    );
  };

  return (
    <GenericPopover
      reference={reference}
      useFloatingOptions={{ open: show, placement: "top-end", middleware: [ABOVE] }}
      focusManagerProps={{ disabled: true }}
      elementProps={{ style: { zIndex: 10 } }}
    >
      {show && (
        <C.Generic.Menu.Root
          position="bottom-end"
          portalElement={portalElement}
          onOpenChange={(open: boolean) => {
            if (open) tableHandles.freezeHandles();
            else {
              tableHandles.unfreezeHandles();
              if (!keepFocus.current) editor.focus();
              keepFocus.current = false;
            }
          }}
        >
          <C.Generic.Menu.Trigger>
            <C.TableHandle.Root
              className="bn-table-handle note-table-more"
              draggable={false}
              onDragStart={() => {}}
              onDragEnd={() => {}}
              label={t("notes:table.options", "Table options")}
            >
              <Ellipsis className="size-4" aria-hidden />
            </C.TableHandle.Root>
          </C.Generic.Menu.Trigger>
          <C.Generic.Menu.Dropdown className="bn-table-handle-menu">
            <C.Generic.Menu.Label>{t("notes:table.style", "Table style")}</C.Generic.Menu.Label>
            <C.Generic.Menu.Item checked={parts.preset === "paper"} onClick={() => choosePreset("paper")}>
              <PresetLabel
                name={t("notes:table.presetPaper", "Paper")}
                hint={t(
                  "notes:table.presetPaperHint",
                  "Thick rules at the top and end, a thin rule under the header · also the next new table's default",
                )}
              />
            </C.Generic.Menu.Item>
            <C.Generic.Menu.Item checked={parts.preset === "notion"} onClick={() => choosePreset("notion")}>
              <PresetLabel
                name={t("notes:table.presetNotion", "Notion")}
                hint={t(
                  "notes:table.presetNotionHint",
                  "Every line thin, the header tinted · also the next new table's default",
                )}
              />
            </C.Generic.Menu.Item>
            <C.Generic.Menu.Divider />
            <C.Generic.Menu.Item checked={headerRow} onClick={() => setHeader("headerRows", !headerRow)}>
              {t("notes:table.headerRow", "Header row")}
            </C.Generic.Menu.Item>
            <C.Generic.Menu.Item checked={headerCol} onClick={() => setHeader("headerCols", !headerCol)}>
              {t("notes:table.headerColumn", "Header column")}
            </C.Generic.Menu.Item>
            <C.Generic.Menu.Divider />
            {partItem("caption")}
            {partItem("notes")}
            <C.Generic.Menu.Divider />
            <C.Generic.Menu.Item onClick={() => setTableGuides(!tableGuides)}>
              {tableGuides
                ? t("notes:table.guidesHide", "Hide edit guides")
                : t("notes:table.guidesShow", "Show edit guides")}
            </C.Generic.Menu.Item>
          </C.Generic.Menu.Dropdown>
        </C.Generic.Menu.Root>
      )}
    </GenericPopover>
  );
}

/** Rendered inside `BlockNoteView` (with `tableHandles={false}`). */
export function NoteTableHandles() {
  const [borders, setBorders] = useState<BordersRequest | null>(null);
  const CellHandle = useMemo(() => makeCellHandle(setBorders), []);
  void borders; // the Borders… picker (Task 10)
  return (
    <>
      <TableHandlesController tableHandle={NoteTableHandle} tableCellHandle={CellHandle} />
      <TableMoreMenu />
    </>
  );
}
