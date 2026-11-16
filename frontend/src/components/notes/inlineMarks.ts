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
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, type Transaction } from "@tiptap/pm/state";
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

interface LineBlock {
  id: string;
  type: string;
  props: Record<string, unknown>;
  children: LineBlock[];
}

type DividerEditor = {
  getBlock: (id: string) => unknown;
  getNextBlock: (id: string) => unknown;
  getParentBlock: (id: string) => unknown;
  updateBlock: (id: string, update: never) => unknown;
  removeBlocks: (ids: string[]) => unknown;
  insertBlocks: (blocks: never[], ref: string, placement: "before" | "after") => unknown;
  setTextCursorPosition: (id: string, placement: "start" | "end") => void;
};

/** Where a `---` line was, to put it back there: its type and props, and,
 * for a nested line (whose divider moved to the top level), its parent and
 * neighbours. */
export interface DashLineOrigin {
  type: string;
  props: Record<string, unknown>;
  parentId: string | null;
  prevId: string | null;
  nextId: string | null;
}

export function dashLineOrigin(editor: DividerEditor, block: LineBlock): DashLineOrigin {
  const parent = editor.getParentBlock(block.id) as LineBlock | undefined;
  const sibs = parent?.children ?? [];
  const i = sibs.findIndex((b) => b.id === block.id);
  return {
    type: block.type,
    props: block.props,
    parentId: parent?.id ?? null,
    prevId: i > 0 ? sibs[i - 1]!.id : null,
    nextId: i >= 0 && i + 1 < sibs.length ? sibs[i + 1]!.id : null,
  };
}

interface LineContent {
  content?: unknown[];
  children?: unknown[];
  type?: string;
}

const isEmptyLine = (b: LineContent | undefined): boolean =>
  !!b && b.type === "paragraph" && (b.content?.length ?? 0) === 0 && (b.children?.length ?? 0) === 0;

/** The caret goes to the line below a divider just typed: the empty line
 * already there, else a new one. Returns the id of a line it added. */
export function caretBelowDivider(editor: DividerEditor, dividerId: string): string | null {
  const next = editor.getNextBlock(dividerId) as (LineContent & { id: string }) | undefined;
  if (isEmptyLine(next)) {
    editor.setTextCursorPosition(next!.id, "start");
    return null;
  }
  const [fresh] = editor.insertBlocks([{ type: "paragraph" } as never], dividerId, "after") as { id: string }[];
  editor.setTextCursorPosition(fresh!.id, "start");
  return fresh!.id;
}

/** "Divider added" with Undo, which turns the divider back into the three
 * dashes where they were typed (the same as Ctrl+Z) and offers "Make a
 * divider again" (and so on). Undo also takes away `addedLine`, the line the
 * caret was put on, while nothing has been written on it. */
export function announceDivider(
  editor: DividerEditor,
  blockId: string,
  origin?: DashLineOrigin,
  addedLine?: string | null,
): void {
  const added = () =>
    toast(i18n.t("notes:divider.added", "Divider added"), {
      duration: UNDO_MS,
      action: { label: i18n.t("notes:divider.undo", "Undo"), onClick: back },
    });
  const back = () => {
    if (!editor.getBlock(blockId)) return;
    if (addedLine && isEmptyLine(editor.getBlock(addedLine) as LineContent | undefined)) {
      editor.removeBlocks([addedLine]);
    }
    const line = {
      id: blockId,
      type: origin?.type ?? "paragraph",
      props: origin?.props ?? {},
      content: "---",
    };
    const at = origin?.parentId ? origin : null;
    if (at && editor.getBlock(at.parentId!)) {
      // A nested line: its divider moved to the top level; put the line back.
      editor.removeBlocks([blockId]);
      if (at.prevId && editor.getBlock(at.prevId)) editor.insertBlocks([line as never], at.prevId, "after");
      else if (at.nextId && editor.getBlock(at.nextId)) editor.insertBlocks([line as never], at.nextId, "before");
      else editor.updateBlock(at.parentId!, { children: [line] } as never);
    } else {
      editor.updateBlock(blockId, { type: line.type, props: line.props, content: "---" } as never);
    }
    editor.setTextCursorPosition(blockId, "end");
    toast(i18n.t("notes:divider.back", "Back to ---"), {
      duration: UNDO_MS,
      action: { label: i18n.t("notes:divider.again", "Make a divider again"), onClick: again },
    });
  };
  const again = () => {
    if (!editor.getBlock(blockId)) return;
    editor.updateBlock(blockId, { type: "divider", props: {}, content: undefined } as never);
    added();
  };
  added();
}

/** Block types a `---` line can be typed in. */
const DASH_LINE_TYPES = new Set(["paragraph", "bulletListItem", "numberedListItem", "checkListItem"]);

/** Moves the first nested divider to the top level, right after the
 * top-level block it was inside (a nest left empty goes with it). Null when
 * every divider is already top level. */
export function liftNestedDivider(doc: PMNode, tr: Transaction): Transaction | null {
  const group = doc.firstChild;
  if (!group) return null;
  let found: { from: number; to: number; node: PMNode; onlyChild: boolean; topEnd: number } | null = null;
  group.forEach((top, offset) => {
    if (found) return;
    const topFrom = 1 + offset;
    top.descendants((node, pos, parent) => {
      if (found) return false;
      if (node.type.name === "blockContainer" && node.firstChild?.type.name === "divider") {
        const from = topFrom + 1 + pos;
        found = { from, to: from + node.nodeSize, node, onlyChild: parent?.childCount === 1, topEnd: topFrom + top.nodeSize };
        return false;
      }
      return true;
    });
  });
  if (!found) return null;
  const f = found as { from: number; to: number; node: PMNode; onlyChild: boolean; topEnd: number };
  // An only child takes its blockGroup with it (an empty group is invalid).
  const [from, to] = f.onlyChild ? [f.from - 1, f.to + 1] : [f.from, f.to];
  tr.delete(from, to);
  tr.insert(tr.mapping.map(f.topEnd), f.node);
  return tr;
}

/** `---` on an empty line turns it into a divider at once (BlockNote's own
 * rule goes with its divider, replaced by PaperHub's), with an Undo toast.
 * Dividers are always top level, as the parts of a section are: one typed,
 * made or moved inside a nested block lands right after its top-level block. */
export const dividerRule = createExtension({
  key: "paperhubDivider",
  prosemirrorPlugins: [
    new Plugin({
      appendTransaction: (trs, _old, state) =>
        trs.some((t) => t.docChanged) ? liftNestedDivider(state.doc, state.tr) : null,
    }),
  ],
  inputRules: [
    {
      find: /^---$/,
      replace: ({ editor }) => {
        const { block } = editor.getTextCursorPosition();
        const sel = editor.prosemirrorState.selection;
        // Only a line that is just the dashes: text after the caret is kept.
        if (!DASH_LINE_TYPES.has(String(block.type)) || !sel.empty || sel.$from.parentOffset !== sel.$from.parent.content.size) {
          return undefined;
        }
        // The caret and the toast follow the change (the rule applies after
        // this returns); the caret goes on below the divider.
        const origin = dashLineOrigin(editor, block);
        queueMicrotask(() => {
          if (!editor.getBlock(block.id)) return;
          const added = caretBelowDivider(editor, block.id);
          announceDivider(editor, block.id, origin, added);
        });
        return { type: "divider", props: {} };
      },
    },
  ],
});
