/** A mark's two forms in a note (H5b.1, [user, 2026-11-21]): an inline
 * citation (`markCite` chip) in the user's text, or a reference block.
 * Hover offers the other form. A conversion keeps the mark id, creates or
 * deletes no mark, and is one editor transaction (one Ctrl+Z); the element
 * that goes collapses (~200 ms) and the new one flashes, both instant under
 * reduced motion. */
import type { Node as PMNode } from "@tiptap/pm/model";
import type { Transaction } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

import { prefersReducedMotion } from "@/lib/motion";
import { useMarksStore } from "@/store/marks";
import type { Mark, NoteBlock } from "@/types/notes";

import { toEditorBlocks } from "../sync";

/** Blocks whose text can hold an inline citation. */
export const TEXT_BLOCKS: ReadonlySet<string> = new Set([
  "paragraph",
  "heading",
  "bulletListItem",
  "numberedListItem",
  "checkListItem",
  "quote",
  "callout",
]);

interface FormBlock {
  id: string;
  type: string;
  content?: unknown;
}

/** The editor calls a conversion makes (any BlockNote schema). */
export interface FormEditor {
  _tiptapEditor: { view: EditorView };
  getBlock: (id: string) => FormBlock | undefined;
  getPrevBlock: (id: string) => FormBlock | undefined;
  insertBlocks: (blocks: never, ref: string, placement: "before" | "after") => unknown;
  updateBlock: (id: string, update: never) => unknown;
  removeBlocks: (ids: string[]) => unknown;
  transact: <T>(fn: (tr: Transaction) => T) => T;
}

const COLLAPSE_MS = 200;
const FLASH_MS = 1600;

function findMark(markId: string, paperId: number | null): Mark | undefined {
  const marks = useMarksStore.getState().marksByPaper;
  const lists = paperId !== null && marks[paperId] ? [marks[paperId]] : Object.values(marks);
  for (const list of lists) {
    const m = list.find((x) => x.id === markId && x.deleted_at === null);
    if (m) return m;
  }
  return undefined;
}

/** Whether `Turn into reference block` can run: the mark is live and its
 * block's props are known. */
export function canTurnIntoBlock(markId: string, paperId: number | null): boolean {
  return !!findMark(markId, paperId)?.block_props;
}

/** Collapses `el` (its width for a chip, its height for a block) before it
 * goes; at once under reduced motion or where animations don't run. */
async function collapse(el: HTMLElement | null, axis: "width" | "height"): Promise<void> {
  if (!el || prefersReducedMotion() || typeof el.animate !== "function") return;
  const size = axis === "width" ? el.offsetWidth : el.offsetHeight;
  el.style.overflow = "hidden";
  const frames =
    axis === "width"
      ? [{ maxWidth: `${size}px`, opacity: 1 }, { maxWidth: "0px", opacity: 0 }]
      : [{ height: `${size}px`, opacity: 1 }, { height: "0px", opacity: 0 }];
  try {
    await el.animate(frames, { duration: COLLAPSE_MS, easing: "ease-out", fill: "forwards" }).finished;
  } catch {
    /* cancelled: go on */
  }
}

/** A brief highlight on the element the conversion made. */
function flash(root: Element, selector: string): void {
  const el = root.querySelector<HTMLElement>(selector);
  if (!el) return;
  el.scrollIntoView?.({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  if (prefersReducedMotion()) return;
  el.setAttribute("data-flash", "");
  setTimeout(() => el.removeAttribute("data-flash"), FLASH_MS);
}

const blockSelector = (id: string) => `.bn-block-outer[data-id="${CSS.escape(id)}"]`;
const citeSelector = (markId: string) => `[data-mark-cite="${CSS.escape(markId)}"]`;

/** The id of the block whose text holds position `pos` of `doc` (a table
 * cell's text belongs to the table block). */
export function blockIdInDoc(doc: PMNode, pos: number): string | null {
  const $pos = doc.resolve(pos);
  for (let d = $pos.depth; d >= 0; d--) {
    const node = $pos.node(d);
    if (node.type.name === "blockContainer") return String(node.attrs.id);
  }
  return null;
}

function blockIdAt(view: EditorView, pos: number): string | null {
  return blockIdInDoc(view.state.doc, pos);
}

/** The editor props of a mark's reference block (its server props). */
function blockProps(mark: Mark): Record<string, unknown> {
  const fake: NoteBlock = {
    id: mark.block_id,
    note_id: mark.note_id ?? 0,
    parent_id: null,
    type: "paperQuote",
    props: mark.block_props ?? {},
    content: [],
    text: "",
    author: "user",
    section_key: null,
    changed_rev: 0,
  };
  return toEditorBlocks([fake])[0]!.props;
}

/** Turn into reference block: the chip at `pos` goes and the mark's
 * reference block comes right after the paragraph that held it. */
export async function turnCiteIntoBlock(
  editor: FormEditor,
  markId: string,
  paperId: number | null,
  getPos: () => number | undefined,
): Promise<boolean> {
  const mark = findMark(markId, paperId);
  if (!mark?.block_props) return false;
  const view = editor._tiptapEditor.view;
  const chipEl = view.dom.querySelector<HTMLElement>(citeSelector(markId));
  await collapse(chipEl, "width");
  const pos = getPos();
  if (typeof pos !== "number" || view.state.doc.nodeAt(pos)?.type.name !== "markCite") return false;
  const holder = blockIdAt(view, pos);
  if (!holder) return false;
  // Its own block id: the server's mark follows that block again.
  const id = editor.getBlock(mark.block_id) ? undefined : mark.block_id;
  editor.transact((tr) => {
    tr.delete(pos, pos + 1);
    editor.insertBlocks(
      [{ ...(id ? { id } : {}), type: "paperQuote", props: blockProps(mark) }] as never,
      holder,
      "after",
    );
  });
  flash(view.dom, blockSelector(mark.block_id));
  return true;
}

function endsInSpace(content: unknown): boolean {
  if (!Array.isArray(content) || !content.length) return true;
  const last = content[content.length - 1] as { type?: string; text?: unknown };
  if (last.type !== "text") return false;
  return typeof last.text !== "string" || last.text === "" || /\s$/.test(last.text);
}

/** Turn into inline citation: the reference block goes and its chip is
 * appended to the end of the previous text block (a space first when that
 * text doesn't end in one); with no text block before it, a new paragraph
 * holding just the chip. */
export async function turnBlockIntoCite(
  editor: FormEditor,
  blockId: string,
  markId: string,
  paperId: number,
): Promise<boolean> {
  const block = editor.getBlock(blockId);
  if (!block || !markId) return false;
  const view = editor._tiptapEditor.view;
  await collapse(view.dom.querySelector<HTMLElement>(blockSelector(blockId)), "height");
  if (!editor.getBlock(blockId)) return false;
  const chip = { type: "markCite", props: { markId, text: "", paperId } };
  const prev = editor.getPrevBlock(blockId);
  editor.transact(() => {
    if (prev && TEXT_BLOCKS.has(prev.type) && Array.isArray(prev.content)) {
      const space = endsInSpace(prev.content) ? [] : [{ type: "text", text: " ", styles: {} }];
      editor.updateBlock(prev.id, { content: [...(prev.content as unknown[]), ...space, chip] } as never);
    } else {
      editor.insertBlocks([{ type: "paragraph", content: [chip] }] as never, blockId, "before");
    }
    editor.removeBlocks([blockId]);
  });
  flash(view.dom, citeSelector(markId));
  return true;
}
