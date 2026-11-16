/** Test helpers: a mounted notes editor driven the way a person drives it
 * (one character at a time through ProseMirror's text input and key handling). */
import { BlockNoteEditor } from "@blocknote/core";

import { codeFence, inlineMarks } from "./inlineMarks";
import { noteSchema } from "./schema";

export type AnyEditor = BlockNoteEditor<
  typeof noteSchema.blockSchema,
  typeof noteSchema.inlineContentSchema,
  typeof noteSchema.styleSchema
>;

const mounted: HTMLElement[] = [];

export function cleanupEditors() {
  mounted.splice(0).forEach((el) => el.remove());
}

/** An editor with the notes schema and the inline marks, caret in the first line. */
export function makeEditor(extra: unknown[] = [], initialContent?: unknown[]): AnyEditor {
  const editor = BlockNoteEditor.create({
    schema: noteSchema,
    extensions: [inlineMarks, codeFence, ...extra] as never,
    initialContent: initialContent as never,
  });
  const el = document.createElement("div");
  document.body.appendChild(el);
  mounted.push(el);
  editor.mount(el);
  editor.setTextCursorPosition(editor.document[0]!, "start");
  return editor;
}

export function type(editor: AnyEditor, text: string) {
  const view = editor._tiptapEditor.view;
  for (const ch of text) {
    const { from, to } = view.state.selection;
    const handled = view.someProp("handleTextInput", (f) =>
      f(view, from, to, ch, () => view.state.tr.insertText(ch, from, to)),
    );
    if (!handled) view.dispatch(view.state.tr.insertText(ch, from, to));
  }
}

export function press(editor: AnyEditor, init: KeyboardEventInit) {
  const view = editor._tiptapEditor.view;
  view.someProp("handleKeyDown", (f) => f(view, new KeyboardEvent("keydown", init)));
}

export interface Run {
  text: string;
  styles: Record<string, unknown>;
}

export function runs(editor: AnyEditor): Run[] {
  const first = editor.document[0] as unknown as { content: Run[] };
  return first.content.map((c) => ({ text: c.text, styles: c.styles }));
}
