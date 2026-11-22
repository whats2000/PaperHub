/** The fold keys: Ctrl+Shift+[ folds and ] unfolds the caret's heading or
 * list item (VS Code's keys); on a Mac ⌘⌥[ / ], as in VS Code, since ⌘⇧[ / ]
 * switches the browser's tabs. NoteEditor does the folding; this file names
 * the keys and keeps them from typing.
 *
 * The keys are matched by their place on the keyboard (`code`), never by the
 * character: Shift+[ is `{` on a US layout, and an input method (Bopomofo,
 * Pinyin, Japanese) reports "Process" and then commits the character on its
 * own, as text input the keydown's preventDefault cannot stop. The guard
 * drops any bracket the page receives while the fold keys are down.
 */
import { createExtension } from "@blocknote/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

const isMac = () => /Mac|iPhone|iPad/.test(navigator.platform);

type Chord = Pick<KeyboardEvent, "code" | "key" | "ctrlKey" | "shiftKey" | "altKey" | "metaKey">;

/** "fold" / "unfold" when `e` is the fold keys, else null. */
export function foldKeyOf(e: Chord): "fold" | "unfold" | null {
  if (e.code !== "BracketLeft" && e.code !== "BracketRight") return null;
  const chord = isMac()
    ? e.metaKey && e.altKey && !e.ctrlKey && !e.shiftKey
    : e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey;
  if (!chord) return null;
  return e.code === "BracketLeft" ? "fold" : "unfold";
}

/** The fold keys' label: Ctrl+Shift+[ (⌘⌥[ on a Mac). */
export function foldShortcut(unfold: boolean): string {
  const bracket = unfold ? "]" : "[";
  return isMac() ? `⌘⌥${bracket}` : `Ctrl+Shift+${bracket}`;
}

/** What the bracket keys type, on any layout or input method (with Shift,
 * full width, Option on a Mac). */
const BRACKETS = /^[[\]{}「」『』【】〔〕［］｛｝“”‘’]+$/u;

const guardKey = new PluginKey("paperhubFoldKeyGuard");
/** Each editor's "are the fold keys down?". */
const guards = new WeakMap<EditorView, () => boolean>();

/** Drops the text an input method commits for the fold keys. */
export const foldKeyGuard = createExtension({
  key: "paperhubFoldKeyGuard",
  prosemirrorPlugins: [
    new Plugin({
      key: guardKey,
      view: (view: EditorView) => {
        const doc = view.dom.ownerDocument;
        // The key held down (its `code`) while the fold keys are pressed.
        let held: string | null = null;
        const inEditor = (e: Event) => e.target instanceof Node && view.dom.contains(e.target);
        // On the document, capturing: NoteEditor's fold handler stops the
        // keydown before it reaches the editor.
        const onKeyDown = (e: KeyboardEvent) => {
          if (!inEditor(e)) return;
          held = foldKeyOf(e) ? e.code : null;
        };
        const onKeyUp = (e: KeyboardEvent) => {
          if (held !== null && e.code === held) held = null;
        };
        const onBeforeInput = (e: InputEvent) => {
          if (held !== null && e.data && BRACKETS.test(e.data) && e.cancelable) e.preventDefault();
        };
        doc.addEventListener("keydown", onKeyDown, true);
        doc.addEventListener("keyup", onKeyUp, true);
        view.dom.addEventListener("beforeinput", onBeforeInput, true);
        guards.set(view, () => held !== null);
        return {
          destroy: () => {
            doc.removeEventListener("keydown", onKeyDown, true);
            doc.removeEventListener("keyup", onKeyUp, true);
            view.dom.removeEventListener("beforeinput", onBeforeInput, true);
            guards.delete(view);
          },
        };
      },
      props: {
        // Text that reached the page anyway (a composition, which cannot be
        // cancelled): typed and taken back out, so the page shows the note
        // as it is and Undo has nothing to undo.
        handleTextInput: (view, from, to, text, deflt) => {
          if (!guards.get(view)?.() || !BRACKETS.test(text)) return false;
          const was = view.state.doc.slice(from, to);
          const typed = deflt();
          if (typed.doc.eq(view.state.doc)) return true;
          view.dispatch(typed.setMeta("addToHistory", false));
          view.dispatch(view.state.tr.replace(from, from + text.length, was).setMeta("addToHistory", false));
          return true;
        },
      },
    }),
  ],
});

