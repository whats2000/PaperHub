/** Markdown marks as you type, and the code fence, for the notes editor.
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

export type InlineStyle = "bold" | "italic" | "underline" | "strike" | "code" | "hidden";

export interface InlineRule {
  style: InlineStyle;
  /** Matched against the text before the caret plus the character just typed. */
  find: RegExp;
}

/** The text between the marks: not empty, no space against either mark. */
const body = (not: string) => `([^${not}\\s](?:[^${not}]*[^${not}\\s])?)`;

export const INLINE_RULES: InlineRule[] = [
  { style: "bold", find: new RegExp(`(?<![*\\\\])\\*\\*${body("*")}\\*\\*$`) },
  { style: "underline", find: new RegExp(`(?<![_\\w])__${body("_")}__$`) },
  { style: "strike", find: new RegExp(`(?<!~)~~${body("~")}~~$`) },
  { style: "hidden", find: new RegExp(`(?<!\\|)\\|\\|${body("|")}\\|\\|$`) },
  { style: "italic", find: new RegExp(`(?<![*\\w])\\*${body("*")}\\*$`) },
  { style: "italic", find: new RegExp(`(?<![_\\w])_${body("_")}_$`) },
  { style: "code", find: /(?<!`)`([^`\s][^`]*)`$/ },
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
                if (!mark) return null;
                const text = state.schema.text(match[1]!, [mark.create()]);
                state.tr.replaceWith(range.from, range.to, text).removeStoredMark(mark);
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
      replace: ({ match }) => ({
        type: "codeBlock",
        props: { language: resolveCodeLanguage(match[1] ?? "") },
        content: [],
      }),
    },
  ],
  keyboardShortcuts: {
    Enter: ({ editor }) => {
      const { block } = editor.getTextCursorPosition();
      if (block.type !== "paragraph") return false;
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
      if (nextBlock) {
        editor.setTextCursorPosition(nextBlock, "start");
      } else {
        const [fresh] = editor.insertBlocks([{ type: "paragraph" }], block, "after");
        editor.setTextCursorPosition(fresh!, "start");
      }
      return true;
    },
  },
});
