/** Markdown marks as you type, the code fence and the `---` divider, for the
 * notes editor.
 *
 * A mark converts on its CLOSING mark (`**bold**` the moment the last `*` is
 * typed), never inside a word (`my_var_name`, `2*3*4` stay as typed) and never
 * on an unbalanced one (`**b*`). Matching is a pure function of the text
 * before the caret so it is tested without an editor; the tiptap rules only
 * apply the match.
 */
import { codeBlockOptions } from "@blocknote/code-block";
import { createExtension } from "@blocknote/core";
import { Extension, InputRule } from "@tiptap/core";
import { toast } from "sonner";

import i18n from "@/lib/i18n";

export type InlineStyle = "bold" | "italic" | "underline" | "strike" | "code" | "hidden";

export interface InlineRule {
  style: InlineStyle;
  /** Length of the opening (and closing) mark. */
  delimiter: number;
  /** Matched against the text before the caret plus the character just typed. */
  find: RegExp;
}

/** The text between the marks: not empty, no space against either mark. */
const body = (not: string) => `([^${not}\\s](?:[^${not}]*[^${not}\\s])?)`;

export const INLINE_RULES: InlineRule[] = [
  { style: "bold", delimiter: 2, find: new RegExp(`(?<![*\\\\])\\*\\*${body("*")}\\*\\*$`) },
  { style: "underline", delimiter: 2, find: new RegExp(`(?<![_\\w])__${body("_")}__$`) },
  { style: "strike", delimiter: 2, find: new RegExp(`(?<!~)~~${body("~")}~~$`) },
  { style: "hidden", delimiter: 2, find: new RegExp(`(?<!\\|)\\|\\|${body("|")}\\|\\|$`) },
  { style: "italic", delimiter: 1, find: new RegExp(`(?<![*\\w])\\*${body("*")}\\*$`) },
  { style: "italic", delimiter: 1, find: new RegExp(`(?<![_\\w])_${body("_")}_$`) },
  { style: "code", delimiter: 1, find: /(?<!`)`([^`\s][^`]*)`$/ },
];

export interface InlineMatch {
  rule: InlineRule;
  /** The text to keep, without the marks. */
  content: string;
  /** Where the opening mark starts in the text. */
  index: number;
}

export function matchInlineRule(textBefore: string): InlineMatch | null {
  for (const rule of INLINE_RULES) {
    const m = rule.find.exec(textBefore);
    if (m) return { rule, content: m[1]!, index: m.index };
  }
  return null;
}

/** Apply each rule as a tiptap input rule. Priority 1000 beats the built-in
 * bold, which would also take `__x__`. */
export const inlineMarks = createExtension({
  key: "inlineMarks",
  tiptapExtensions: [
    Extension.create({
      name: "paperhubInlineMarks",
      priority: 1000,
      addInputRules() {
        return INLINE_RULES.map(
          (rule) =>
            new InputRule({
              find: rule.find,
              handler: ({ state, range, match }) => {
                const mark = state.schema.marks[rule.style];
                // tiptap writes '%leaf%' for an atom (a mention chip) in the text it
                // matches, which no longer lines up with document positions: leave it.
                if (!mark || match[0].includes("%leaf%")) return null;
                // Like tiptap's markInputRule: drop the marks and mark the words in
                // place, so chips and marks already on them are kept.
                const { tr } = state;
                const start = range.from + rule.delimiter;
                const end = start + match[1]!.length;
                // Whatever closing characters are already in the document (the
                // typed one may or may not be, depending on the input path).
                if (end < range.to) tr.delete(end, range.to);
                tr.delete(range.from, start);
                tr.addMark(range.from, range.from + match[1]!.length, mark.create());
                tr.removeStoredMark(mark);
              },
            }),
        );
      },
    }),
  ],
});

// ---- code fence -----------------------------------------------------------

const FENCE_LINE = /^```(\S*)$/;

/** The id of a supported language for what the user typed after the fence:
 * an id or alias, "text" (plain, uncoloured) for anything unknown, the default
 * when nothing was typed. */
export function resolveCodeLanguage(raw: string): string {
  const typed = raw.trim().toLowerCase();
  if (!typed) return codeBlockOptions.defaultLanguage ?? "text";
  const found = Object.entries(codeBlockOptions.supportedLanguages ?? {}).find(
    ([id, { aliases }]) => id.toLowerCase() === typed || aliases?.some((a) => a.toLowerCase() === typed),
  );
  return found?.[0] ?? "text";
}

/** Code fences: ```` ```lang ```` + space (BlockNote's own rule, redone so an
 * unknown language is plain text instead of a render error) or + Enter;
 * Ctrl+Enter leaves a code block for a new line below it. */
export const codeFence = createExtension({
  key: "paperhubCodeFence",
  inputRules: [
    {
      find: /^```(\S*)\s$/,
      // tiptap runs input rules on Enter too (as a "\n"), so this also covers
      // ```lang + Enter; mid-line, Enter splits the line as usual.
      replace: ({ match, editor }) => {
        const sel = editor.prosemirrorState.selection;
        if (!sel.empty || sel.$from.parentOffset !== sel.$from.parent.content.size) return undefined;
        return {
          type: "codeBlock",
          props: { language: resolveCodeLanguage(match[1] ?? "") },
          content: [],
        };
      },
    },
  ],
  keyboardShortcuts: {
    Enter: ({ editor }) => {
      const { block } = editor.getTextCursorPosition();
      if (block.type !== "paragraph") return false;
      const sel = editor.prosemirrorState.selection;
      // Only a caret at the end of the line: Enter elsewhere splits as usual.
      if (!sel.empty || sel.$from.parentOffset !== sel.$from.parent.content.size) return false;
      const line = (block.content as { type: string; text?: string }[])
        .map((c) => (c.type === "text" ? (c.text ?? "") : "\u0000"))
        .join("");
      const m = FENCE_LINE.exec(line);
      if (!m) return false;
      editor.updateBlock(block, {
        type: "codeBlock",
        props: { language: resolveCodeLanguage(m[1] ?? "") },
        content: [],
      } as never);
      editor.setTextCursorPosition(block, "start");
      return true;
    },
    "Mod-Enter": ({ editor }) => {
      const { block, nextBlock } = editor.getTextCursorPosition();
      if (block.type !== "codeBlock") return false;
      const hasText = nextBlock && (editor.schema.blockSchema as Record<string, { content: string } | undefined>)[nextBlock.type as string]?.content === "inline";
      if (nextBlock && hasText) {
        editor.setTextCursorPosition(nextBlock, "start");
      } else {
        const [fresh] = editor.insertBlocks([{ type: "paragraph" }], block, "after");
        editor.setTextCursorPosition(fresh!, "start");
      }
      return true;
    },
  },
});

// ---- divider --------------------------------------------------------------

const UNDO_MS = 5000;

type DividerEditor = {
  getBlock: (id: string) => unknown;
  updateBlock: (id: string, update: never) => unknown;
  setTextCursorPosition: (id: string, placement: "start" | "end") => void;
};

/** "Divider added" with Undo, which turns the divider back into the three
 * dashes and offers "Make a divider again" (and so on). */
export function announceDivider(editor: DividerEditor, blockId: string): void {
  const added = () =>
    toast(i18n.t("notes:divider.added", "Divider added"), {
      duration: UNDO_MS,
      action: { label: i18n.t("notes:divider.undo", "Undo"), onClick: back },
    });
  const back = () => {
    if (!editor.getBlock(blockId)) return;
    editor.updateBlock(blockId, { type: "paragraph", content: "---" } as never);
    editor.setTextCursorPosition(blockId, "end");
    toast(i18n.t("notes:divider.back", "Back to ---"), {
      duration: UNDO_MS,
      action: { label: i18n.t("notes:divider.again", "Make a divider again"), onClick: again },
    });
  };
  const again = () => {
    if (!editor.getBlock(blockId)) return;
    editor.updateBlock(blockId, { type: "divider", props: {} } as never);
    added();
  };
  added();
}

/** `---` on an empty line turns it into a divider at once (BlockNote's own
 * rule goes with its divider, replaced by PaperHub's), with an Undo toast. */
export const dividerRule = createExtension({
  key: "paperhubDivider",
  inputRules: [
    {
      find: /^---$/,
      replace: ({ editor }) => {
        const { block } = editor.getTextCursorPosition();
        const sel = editor.prosemirrorState.selection;
        // Only a line that is just the dashes: text after the caret is kept.
        if (block.type !== "paragraph" || !sel.empty || sel.$from.parentOffset !== sel.$from.parent.content.size) {
          return undefined;
        }
        // The toast follows the change (the rule applies after this returns).
        queueMicrotask(() => announceDivider(editor, block.id));
        return { type: "divider", props: {} };
      },
    },
  ],
});
