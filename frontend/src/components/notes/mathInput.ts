/** Typing math in a note, as in Typora / Obsidian / Notion.
 *
 * - `$$` on an empty line, then Enter (or a space: `paperhubKeys`), turns the
 *   line into a block equation with the caret in its LaTeX.
 * - `$…$` turns into inline math on the closing `$`: not empty, no space
 *   just inside either `$` (so "$5 and $6" stays text), neither `$` escaped
 *   (`\$`), and never a `$$`.
 */
import { createExtension } from "@blocknote/core";
import { Extension, InputRule } from "@tiptap/core";

/** Matched against the text before the caret plus the `$` just typed. */
const CH = String.raw`(?:[^$\\]|\\.)`;
const EDGE = String.raw`(?:[^\s$\\]|\\.)`;
const INLINE_MATH = new RegExp(String.raw`(?<![\\$])\$(${EDGE}(?:${CH}*${EDGE})?)\$$`);

export function matchInlineMath(textBefore: string): { latex: string; index: number } | null {
  const m = INLINE_MATH.exec(textBefore);
  return m ? { latex: m[1]!, index: m.index } : null;
}

export const mathInput = createExtension({
  key: "paperhubMathInput",
  tiptapExtensions: [
    Extension.create({
      name: "paperhubInlineMath",
      priority: 1000,
      addInputRules() {
        return [
          new InputRule({
            find: INLINE_MATH,
            handler: ({ state, range, match }) => {
              const node = state.schema.nodes.inlineMath;
              // tiptap writes '%leaf%' for an atom in the matched text.
              if (!node || match[0].includes("%leaf%")) return null;
              state.tr.replaceWith(range.from, range.to, node.create({ latex: match[1]! }));
            },
          }),
        ];
      },
    }),
  ],
  keyboardShortcuts: {
    Enter: ({ editor }) => {
      const { block } = editor.getTextCursorPosition();
      if (block.type !== "paragraph") return false;
      const sel = editor.prosemirrorState.selection;
      if (!sel.empty || sel.$from.parentOffset !== sel.$from.parent.content.size) return false;
      const content = block.content as { type: string; text?: string }[];
      if (content.length !== 1 || content[0]!.type !== "text" || content[0]!.text !== "$$") return false;
      editor.updateBlock(block, { type: "math", props: { latex: "" } } as never);
      editor.setTextCursorPosition(block, "start");
      return true;
    },
  },
});

/** What finds the equation items in the slash menu, in every UI language
 * (their localized titles match too). */
export const MATH_ALIASES = ["eq", "equation", "math", "latex", "formula", "公式", "数式", "數學", "数学", "方程式"];

/** The plain text of a block's inline content (an atom counts as nothing). */
function inlineText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((c: { type?: string; text?: string; content?: unknown }) =>
      c.type === "text" ? (c.text ?? "") : c.type === "link" ? inlineText(c.content) : "",
    )
    .join("");
}

/** The caret line's text without the "/query" being typed in it. */
export function slashLineText(editor: { getTextCursorPosition: () => { block: { content: unknown } } }, query: string): string {
  let text: string;
  try {
    text = inlineText(editor.getTextCursorPosition().block.content);
  } catch {
    return "";
  }
  const at = text.lastIndexOf(`/${query}`);
  return at < 0 ? text : text.slice(0, at) + text.slice(at + query.length + 1);
}

/** Notion's "Block equation · Turn into": the caret line becomes a block
 * equation whose LaTeX is the line's text. */
export function turnIntoEquation(editor: {
  getTextCursorPosition: () => { block: { content: unknown } };
  updateBlock: (block: never, update: never) => unknown;
}): void {
  const { block } = editor.getTextCursorPosition();
  const latex = inlineText(block.content).trim();
  editor.updateBlock(block as never, { type: "math", props: { latex }, content: undefined } as never);
}
