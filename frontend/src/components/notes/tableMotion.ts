/** How a table moves: a wide table fades on the side it is clipped, a new row
 * grows from zero height and a new column from zero width, and a deleted one
 * shrinks away first (`shrinkAway`). Nothing moves under reduced motion.
 *
 * Only `.tableWrapper` attributes and Web Animations are touched: BlockNote's
 * table view ignores mutations outside its inner table, and an animation
 * changes no DOM, so ProseMirror never sees either.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { prefersReducedMotion } from "@/lib/motion";

import { changedRange, tablesIn } from "./tableKeys";

const TIMING: KeyframeAnimationOptions = { duration: 200, easing: "ease-out" };

function setFlag(el: Element, name: string, on: boolean) {
  if (el.hasAttribute(name) !== on) el.toggleAttribute(name, on);
}

/** `data-clip-left` / `data-clip-right` on a table's scroll box while the
 * table runs past that side (the CSS draws the fade). The box's padding
 * (room for BlockNote's handles) scrolled out of view hides nothing: a
 * table scrolled into view, its left edge at the box's, does not fade. */
function markClipped(wrapper: HTMLElement) {
  const { scrollLeft, clientWidth, scrollWidth } = wrapper;
  const cs = getComputedStyle(wrapper);
  const padL = parseFloat(cs.paddingLeft) || 0;
  const padR = parseFloat(cs.paddingRight) || 0;
  setFlag(wrapper, "data-clip-left", scrollLeft > padL + 1);
  setFlag(wrapper, "data-clip-right", scrollLeft + clientWidth < scrollWidth - padR - 1);
}

/** The part of a table its scroll box (BlockNote's .tableWrapper) shows. */
export function visibleRect(table: Element): DOMRect {
  const r = table.getBoundingClientRect();
  const box = table.closest(".tableWrapper")?.getBoundingClientRect();
  if (!box) return r;
  const left = Math.max(r.left, box.left);
  const right = Math.max(left, Math.min(r.right, box.right));
  return new DOMRect(left, r.top, right - left, r.height);
}

/** The tables a change touched in `doc` (only the changed range is
 * walked), by block id. */
function touchedTables(doc: PMNode, from: number, to: number): Map<string, PMNode> {
  const out = new Map<string, PMNode>();
  for (const { pos, node } of tablesIn(doc, from, to)) {
    out.set(String(doc.resolve(pos).parent.attrs.id), node);
  }
  return out;
}

const children = (node: PMNode) => {
  const out: PMNode[] = [];
  node.forEach((c) => out.push(c));
  return out;
};

/** Where `next` first differs from `prev`: the first of the items added. */
function firstChange(prev: PMNode[], next: PMNode[]): number {
  let i = 0;
  while (i < prev.length && i < next.length && prev[i]!.eq(next[i]!)) i++;
  return i;
}

function tableElement(view: EditorView, blockId: string): HTMLTableElement | null {
  return view.dom.querySelector<HTMLTableElement>(`[data-id="${blockId}"] table`);
}

const canAnimate = (el: Element) => typeof (el as { animate?: unknown }).animate === "function";

/** Height keyframes for a row's cells, from zero to their size. */
function rowFrames(cell: HTMLElement): [HTMLElement, Keyframe[]][] {
  const cs = getComputedStyle(cell);
  const out: [HTMLElement, Keyframe[]][] = [
    [
      cell,
      [
        { paddingTop: "0px", paddingBottom: "0px", opacity: 0 },
        { paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, opacity: 1 },
      ],
    ],
  ];
  const text = cell.firstElementChild as HTMLElement | null;
  if (text) {
    out.push([
      text,
      [
        { height: "0px", overflow: "hidden" },
        { height: `${text.offsetHeight}px`, overflow: "hidden" },
      ],
    ]);
  }
  return out;
}

/** Width keyframes for a column: its `<col>` from zero, its cells revealed. */
function columnFrames(table: HTMLTableElement, index: number): [HTMLElement, Keyframe[]][] {
  const out: [HTMLElement, Keyframe[]][] = [];
  const col = table.querySelector("colgroup")?.children[index] as HTMLElement | undefined;
  const first = table.rows[0]?.cells[index];
  if (col) {
    const width = col.style.width || `${first?.offsetWidth ?? 0}px`;
    out.push([col, [{ width: "0px" }, { width }]]);
  }
  for (const row of [...table.rows]) {
    const cell = row.cells[index];
    if (cell) out.push([cell, [{ opacity: 0, clipPath: "inset(0 100% 0 0)" }, { opacity: 1, clipPath: "inset(0 0 0 0)" }]]);
  }
  return out;
}

function play(frames: [HTMLElement, Keyframe[]][], reverse = false): Animation[] {
  return frames
    .filter(([el]) => canAnimate(el))
    .map(([el, kf]) => el.animate(reverse ? [...kf].reverse() : kf, { ...TIMING, fill: reverse ? "forwards" : "none" }));
}

/** Rows and columns added to a table since `prev`, grown in; the fades of
 * the tables the change touched brought up to date. */
function growAdded(view: EditorView, prevDoc: PMNode) {
  const range = changedRange(prevDoc, view.state.doc);
  if (!range) return;
  const now = touchedTables(view.state.doc, range.from, range.to);
  if (!now.size) return;
  const before = touchedTables(prevDoc, range.prevFrom, range.prevTo);
  const reduced = prefersReducedMotion();
  for (const [id, next] of now) {
    const table = tableElement(view, id);
    const wrapper = table?.closest<HTMLElement>(".tableWrapper");
    if (wrapper) markClipped(wrapper);
    const prev = before.get(id);
    if (reduced || !table || !prev || prev.eq(next)) continue;
    const prevRows = children(prev);
    const nextRows = children(next);
    const added = nextRows.length - prevRows.length;
    if (added > 0) {
      const at = firstChange(prevRows, nextRows);
      const frames = [...table.rows].slice(at, at + added).flatMap((tr) => [...tr.cells].flatMap(rowFrames));
      void play(frames);
    }
    const prevCols = prevRows[0] ? children(prevRows[0]) : [];
    const nextCols = nextRows[0] ? children(nextRows[0]) : [];
    const addedCols = nextCols.length - prevCols.length;
    if (addedCols > 0) {
      const at = firstChange(prevCols, nextCols);
      const frames: [HTMLElement, Keyframe[]][] = [];
      for (let i = at; i < at + addedCols; i++) frames.push(...columnFrames(table, i));
      void play(frames);
    }
  }
}

/** Tables with a shrink running: a second delete waits for none. */
const shrinking = new WeakSet<HTMLTableElement>();

/** Shrinks row or column `index` of a table to nothing, then runs
 * `remove` with its index then (at once under reduced motion). The row or
 * column is followed by its DOM (ProseMirror keeps an unchanged row's), so a
 * change meanwhile can't make it delete another; gone by then, nothing is
 * deleted. A delete asked for while one shrinks is ignored. The shrink is
 * then let go: a cell ProseMirror keeps for a neighbour shows at its size. */
export function shrinkAway(
  view: EditorView,
  blockId: string,
  orientation: "row" | "column",
  index: number,
  remove: (index: number) => void,
) {
  const table = tableElement(view, blockId);
  if (!table || prefersReducedMotion()) {
    remove(index);
    return;
  }
  if (shrinking.has(table)) return;
  const anchor = orientation === "row" ? table.rows[index] : table.rows[0]?.cells[index];
  if (!anchor) return;
  shrinking.add(table);
  const frames =
    orientation === "row" ? [...(table.rows[index]?.cells ?? [])].flatMap(rowFrames) : columnFrames(table, index);
  const running = play(frames, true);
  void Promise.all(running.map((a) => a.finished))
    .catch(() => undefined)
    .then(() => {
      shrinking.delete(table);
      if (anchor.isConnected) {
        remove(anchor instanceof HTMLTableRowElement ? anchor.rowIndex : (anchor).cellIndex);
      }
      running.forEach((a) => a.cancel());
    });
}

/** The fades and the growing rows / columns. */
export const tableMotion = createExtension({
  key: "tableMotion",
  prosemirrorPlugins: [
    new Plugin({
      view(view) {
        const markAll = () => view.dom.querySelectorAll<HTMLElement>(".tableWrapper").forEach(markClipped);
        const onScroll = (e: Event) => {
          const el = e.target;
          if (el instanceof HTMLElement && el.classList.contains("tableWrapper")) markClipped(el);
        };
        view.dom.addEventListener("scroll", onScroll, true);
        const resize = typeof ResizeObserver === "function" ? new ResizeObserver(markAll) : null;
        resize?.observe(view.dom);
        markAll();
        return {
          update(v, prevState) {
            if (v.state.doc === prevState.doc) return;
            growAdded(v, prevState.doc);
          },
          destroy() {
            view.dom.removeEventListener("scroll", onScroll, true);
            resize?.disconnect();
          },
        };
      },
    }),
  ],
});
