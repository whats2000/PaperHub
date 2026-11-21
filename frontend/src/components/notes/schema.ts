/** The notes editor schema: BlockNote's text blocks plus PaperHub's.
 *
 * Only block types the notes API stores are in the schema (files, media and
 * toggle lists are left out: an unknown type would fail a whole burst of
 * ops). Tables are BlockNote's own (`tableKeys.ts` adds what Notion does).
 * Code blocks come from `@blocknote/code-block`.
 */
import {
  BlockNoteSchema,
  createExtension,
  defaultBlockSpecs,
  defaultInlineContentSpecs,
  defaultStyleSpecs,
} from "@blocknote/core";
import type { EditorView } from "@tiptap/pm/view";

import { atomCaretKey, setAtomCaret } from "./atomCaret";
import { calloutBlock } from "./blocks/CalloutBlock";
import { codeBlockWithCopy } from "./blocks/CodeBlock";
import { dividerBlock } from "./blocks/DividerBlock";
import { commentStyle } from "./blocks/CommentStyle";
import { hiddenStyle } from "./blocks/HiddenStyle";
import { inlineMath } from "./blocks/InlineMath";
import { markCite } from "./blocks/MarkCite";
import { mathBlock } from "./blocks/MathBlock";
import { mentionInline } from "./blocks/MentionChip";
import { noteLink } from "./blocks/NoteLink";
import { paperEquationBlock } from "./blocks/PaperEquationBlock";
import { paperFigureBlock } from "./blocks/PaperFigureBlock";
import { paperQuoteBlock } from "./blocks/PaperQuoteBlock";
import { paperRegionBlock } from "./blocks/PaperRegionBlock";
import { sectionBlock } from "./blocks/SectionBlock";

const {
  paragraph,
  heading,
  bulletListItem,
  numberedListItem,
  checkListItem,
  quote,
  table,
} = defaultBlockSpecs;

export const noteSchema = BlockNoteSchema.create({
  blockSpecs: {
    paragraph,
    heading,
    bulletListItem,
    numberedListItem,
    checkListItem,
    quote,
    // No colour on a table: a GFM table holds none (the ⋮⋮ menu's Colors
    // item shows only for blocks with a textColor prop).
    table: { ...table, config: { ...table.config, propSchema: {} } },
    divider: dividerBlock(),
    codeBlock: codeBlockWithCopy(),
    callout: calloutBlock(),
    math: mathBlock(),
    section: sectionBlock(),
    paperQuote: paperQuoteBlock(),
    paperFigure: paperFigureBlock(),
    paperEquation: paperEquationBlock(),
    paperRegion: paperRegionBlock(),
  },
  inlineContentSpecs: { ...defaultInlineContentSpecs, mention: mentionInline, inlineMath, markCite, noteLink },
  styleSpecs: { ...defaultStyleSpecs, hidden: hiddenStyle, comment: commentStyle },
});

interface MovableEditor {
  moveBlocksUp: () => void;
  moveBlocksDown: () => void;
  _tiptapEditor: { view: EditorView };
}

/** Alt+↑ / Alt+↓: the caret's block (or the selected blocks) one place up or
 * down (BlockNote's own move). A reference held by the atom caret moves with
 * the caret still beside it. */
function moveBlocks(editor: MovableEditor, dir: "up" | "down"): boolean {
  const view = editor._tiptapEditor.view;
  const caret = atomCaretKey.getState(view.state);
  const id: unknown = caret ? view.state.doc.resolve(caret.pos).parent.attrs.id : null;
  if (dir === "up") editor.moveBlocksUp();
  else editor.moveBlocksDown();
  if (caret && typeof id === "string") {
    let pos = -1;
    view.state.doc.descendants((node, p) => {
      if (pos >= 0) return false;
      if (node.type.name === "blockContainer" && node.attrs.id === id) pos = p + 1;
      return pos < 0;
    });
    if (pos >= 0) setAtomCaret(view, pos, caret.side);
  }
  return true;
}

const LIST_TYPES = new Set(["bulletListItem", "numberedListItem", "checkListItem"]);

/** The Notion keys BlockNote lacks: `$$ ` starts an equation, and Enter on an
 * empty nested list item steps out a level (BlockNote turns it into text in
 * place; at the top level that is still what Enter does).
 * (`" ` quotes and Enter on a selected reference, section or divider adding a
 * line below are BlockNote's own; `---` is `dividerRule`. Tests pin them.) */
export const paperhubKeys = createExtension({
  key: "paperhubKeys",
  inputRules: [{ find: /^\$\$\s$/, replace: () => ({ type: "math", props: {} }) }],
  keyboardShortcuts: {
    "Alt-ArrowUp": ({ editor }) => moveBlocks(editor as never, "up"),
    "Alt-ArrowDown": ({ editor }) => moveBlocks(editor as never, "down"),
    Enter: ({ editor }) => {
      if (!editor.prosemirrorState.selection.empty) return false;
      const { block } = editor.getTextCursorPosition();
      if (!LIST_TYPES.has(String(block.type)) || (block.content as unknown[]).length > 0) return false;
      if (!editor.getParentBlock(block) || !editor.canUnnestBlock()) return false;
      editor.unnestBlock();
      return true;
    },
  },
});
