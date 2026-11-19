/** The notes editor schema: BlockNote's text blocks plus PaperHub's.
 *
 * Only block types the notes API stores are in the schema (tables, files,
 * media and toggle lists are left out: an unknown type would fail a whole
 * burst of ops). Code blocks come from `@blocknote/code-block`.
 */
import {
  BlockNoteSchema,
  createExtension,
  defaultBlockSpecs,
  defaultInlineContentSpecs,
  defaultStyleSpecs,
} from "@blocknote/core";

import { calloutBlock } from "./blocks/CalloutBlock";
import { codeBlockWithCopy } from "./blocks/CodeBlock";
import { dividerBlock } from "./blocks/DividerBlock";
import { commentStyle } from "./blocks/CommentStyle";
import { hiddenStyle } from "./blocks/HiddenStyle";
import { inlineMath } from "./blocks/InlineMath";
import { mathBlock } from "./blocks/MathBlock";
import { mentionInline } from "./blocks/MentionChip";
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
} = defaultBlockSpecs;

export const noteSchema = BlockNoteSchema.create({
  blockSpecs: {
    paragraph,
    heading,
    bulletListItem,
    numberedListItem,
    checkListItem,
    quote,
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
  inlineContentSpecs: { ...defaultInlineContentSpecs, mention: mentionInline, inlineMath },
  styleSpecs: { ...defaultStyleSpecs, hidden: hiddenStyle, comment: commentStyle },
});

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
