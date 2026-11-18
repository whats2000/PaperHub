/** The rows of the shortcuts guide, in the sections of the approved prototype. */

export type SectionId = "marks" | "lineStart" | "anywhere" | "lists" | "moving" | "keys" | "pane" | "paper";
export type Suffix = "plusSpace" | "plusEnter" | "onEmptyItem" | "atStart";
export type Sample = "bold" | "italic" | "underline" | "strike" | "code" | "hidden";

export interface Row {
  /** Catalog id: `notes:guide.rows.<id>` is the right cell, `.left.<id>` a plain left cell. */
  id: string;
  /** Literal key caps (shortcuts and Markdown are never translated). */
  keys?: string[];
  suffix?: Suffix;
  /** The left cell is a phrase, not key caps. */
  plainLeft?: string;
  right: string;
  /** A key cap closing the right cell (`notes:guide.rows.<id>Stops` follows it). */
  rightKey?: string;
  rightAfter?: string;
  /** The right cell shows the mark itself, rendered. */
  sample?: Sample;
}

export const GUIDE_SECTIONS: { id: SectionId; title: string; rows: Row[] }[] = [
  {
    id: "marks",
    title: "Marks as you type",
    rows: [
      { id: "bold", keys: ["**bold**"], right: "bold", sample: "bold" },
      { id: "italic", keys: ["*italic*", "_italic_"], right: "italic", sample: "italic" },
      { id: "underline", keys: ["__underline__"], right: "underline", sample: "underline" },
      { id: "strike", keys: ["~~strike~~"], right: "strike", sample: "strike" },
      { id: "code", keys: ["`code`"], right: "code", sample: "code" },
      { id: "hidden", keys: ["||hidden||"], right: "hidden", sample: "hidden" },
    ],
  },
  {
    id: "lineStart",
    title: "At the start of a line",
    rows: [
      { id: "heading", keys: ["#", "##", "###"], suffix: "plusSpace", right: "Heading 1–3" },
      { id: "bullet", keys: ["-"], suffix: "plusSpace", right: "• Bulleted list" },
      { id: "numbered", keys: ["1."], suffix: "plusSpace", right: "1. Numbered list" },
      { id: "todo", keys: ["[]"], suffix: "plusSpace", right: "☐ To-do" },
      { id: "quote", keys: ['"'], suffix: "plusSpace", right: "Quote" },
      { id: "divider", keys: ["---"], right: "Divider: starts a new note" },
      { id: "equation", keys: ["$$"], suffix: "plusSpace", right: "Equation (LaTeX)" },
      { id: "codeBlock", keys: ["```python"], suffix: "plusEnter", right: "Code block (a space works too)" },
    ],
  },
  {
    id: "anywhere",
    title: "Anywhere",
    rows: [
      { id: "slash", keys: ["/"], right: "Every block, incl. Quote from the paper" },
      { id: "at", keys: ["@"], right: "Link a paper, section or idea" },
      { id: "select", plainLeft: "Select text", right: "Toolbar: bold … hide, link, comment" },
      { id: "hover", plainLeft: "Hover a block", right: "+ add · ⋮⋮ drag or menu" },
    ],
  },
  {
    id: "lists",
    title: "Lists",
    rows: [
      { id: "tab", keys: ["Tab"], right: "Nest under the line above" },
      { id: "shiftTab", keys: ["Shift+Tab"], right: "Step back out a level" },
      { id: "emptyEnter", keys: ["Enter"], suffix: "onEmptyItem", right: "Step out; again: back to text" },
      { id: "listBackspace", keys: ["Backspace"], suffix: "atStart", right: "Turn the item into text" },
    ],
  },
  {
    id: "moving",
    title: "Moving around",
    rows: [
      { id: "enter", keys: ["Enter"], right: "Split the line here" },
      { id: "backspace", keys: ["Backspace"], suffix: "atStart", right: "Join with the line above" },
      { id: "arrows", keys: ["↑", "↓"], right: "Move between blocks, incl. references" },
      { id: "clickRef", plainLeft: "Click a reference, heading or divider, then Enter", right: "New line below it" },
    ],
  },
  {
    id: "keys",
    title: "Keys",
    rows: [
      { id: "bius", keys: ["Ctrl+B", "I", "U"], right: "Bold, italic, underline" },
      { id: "ctrlE", keys: ["Ctrl+E"], right: "Inline code" },
      { id: "ctrlShiftS", keys: ["Ctrl+Shift+S"], right: "Strikethrough" },
      { id: "ctrlShiftM", keys: ["Ctrl+Shift+M"], right: "Comment" },
      { id: "shiftEnter", keys: ["Shift+Enter"], right: "Line break" },
      { id: "ctrlEnter", keys: ["Ctrl+Enter"], right: "Leave a code block" },
    ],
  },
  {
    id: "pane",
    title: "The notes pane",
    rows: [
      { id: "following", plainLeft: "Following §2", right: "Click to stop the note scrolling with the paper" },
      { id: "focus", plainLeft: "Focus", right: "Fade the other notes and their marks", rightKey: "Esc", rightAfter: "stops" },
      { id: "fold", plainLeft: "⌄ on a divider", right: "Fold the note below it" },
      { id: "trash", plainLeft: "Trash on a block", right: "Delete, with Undo" },
    ],
  },
  {
    id: "paper",
    title: "On the paper",
    rows: [
      { id: "selectPaper", plainLeft: "Select text", right: "Pick a style and a colour: it goes to the note" },
      { id: "highlighter", plainLeft: "Highlighter", right: "Drag over words, or click a figure" },
      { id: "box", plainLeft: "Box", right: "Drag over a figure or equation" },
      { id: "clickMark", plainLeft: "Click a mark", right: "Opens its note" },
    ],
  },
];

export const SUFFIX_EN: Record<Suffix, string> = {
  plusSpace: "+ space",
  plusEnter: "+ Enter",
  onEmptyItem: "on an empty item",
  atStart: "at the start",
};
