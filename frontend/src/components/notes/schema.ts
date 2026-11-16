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
  inlineContentSpecs: { ...defaultInlineContentSpecs, mention: mentionInline },
  styleSpecs: { ...defaultStyleSpecs, hidden: hiddenStyle, comment: commentStyle },
});

/** The Notion line-start shortcut BlockNote lacks: `$$ ` starts an equation.
 * (`" ` quotes and Enter on a selected reference, section or divider adding a
 * line below are BlockNote's own; `---` is `dividerRule`. Tests pin them.) */
export const paperhubKeys = createExtension({
  key: "paperhubKeys",
  inputRules: [{ find: /^\$\$\s$/, replace: () => ({ type: "math", props: {} }) }],
});
