/** What the formatting bar's buttons (and the `@` menu) do to the editor,
 * kept apart from the React bar so they are tested against a real editor. */
import type { BlockNoteEditor } from "@blocknote/core";

/** Blocks whose content is text a quote can wrap. References, sections,
 * equations, code and dividers are never converted. */
const QUOTABLE = new Set([
  "paragraph",
  "heading",
  "bulletListItem",
  "numberedListItem",
  "checkListItem",
  "quote",
]);

// The bar works on any schema; only the block `type` and `id` are read here.
type Ed = BlockNoteEditor<any, any, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export function quoteTargets<B extends { type: string }>(blocks: B[]): B[] {
  return blocks.filter((b) => QUOTABLE.has(b.type));
}

function selectedBlocks(editor: Ed) {
  return editor.getSelection()?.blocks ?? [editor.getTextCursorPosition().block];
}

/** True when every text block in the selection is already a quote. */
export function selectionIsQuote(editor: Ed): boolean {
  const targets = quoteTargets(selectedBlocks(editor));
  return targets.length > 0 && targets.every((b) => b.type === "quote");
}

/** Quote ↔ text for the text blocks of the selection, as one undo step. */
export function toggleQuote(editor: Ed): void {
  const targets = quoteTargets(selectedBlocks(editor));
  if (targets.length === 0) return;
  const back = targets.every((b) => b.type === "quote");
  editor.transact(() => {
    for (const b of targets) editor.updateBlock(b, { type: back ? "paragraph" : "quote" });
  });
}

export interface MentionTarget {
  kind: "paper" | "section";
  paperId: number;
  sectionKey: string;
  label: string;
}

export function mentionHref(m: Pick<MentionTarget, "paperId" | "sectionKey">): string {
  const q = m.sectionKey ? `?section=${encodeURIComponent(m.sectionKey)}` : "";
  return `paperhub://paper/${m.paperId}${q}`;
}

/** The paper (and section) a `paperhub://` link points at; null for any other link. */
export function parseMentionHref(href: string): { paperId: number; sectionKey: string } | null {
  const m = /^paperhub:\/\/paper\/(\d+)(?:\?section=(.*))?$/.exec(href);
  if (!m) return null;
  return { paperId: Number(m[1]), sectionKey: m[2] ? decodeURIComponent(m[2]) : "" };
}

/** A chip at the caret; with words selected, the words stay and become the link. */
export function insertMention(editor: Ed, m: MentionTarget): void {
  if (!editor.prosemirrorState.selection.empty) {
    editor.createLink(mentionHref(m));
    return;
  }
  editor.insertInlineContent([{ type: "mention", props: m }, " "]);
}
