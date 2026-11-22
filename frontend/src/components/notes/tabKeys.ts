/** Tab in a note nests the block and Shift+Tab steps it out (BlockNote, as
 * in Notion). When it cannot (nothing above to nest under, already at the
 * top), BlockNote leaves the key to the browser, which moves focus out of
 * the note to the next control and scrolls the pane to it: the view jumps
 * to the top. Notion does nothing then; so does this.
 *
 * Left alone: Tab while BlockNote's formatting or link toolbar is open (it
 * moves into the toolbar), Tab from a control inside the note (a fold
 * toggle, a table handle), and Tab with Ctrl / Alt / ⌘.
 */
import { createExtension } from "@blocknote/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";

export const tabStays = createExtension({
  key: "paperhubTabStays",
  prosemirrorPlugins: [
    new Plugin({
      key: new PluginKey("paperhubTabStays"),
      // A listener added after ProseMirror's own: it sees the key once
      // BlockNote has nested (and prevented it) or not.
      view: (view) => {
        const onKeyDown = (e: KeyboardEvent) => {
          if (e.key !== "Tab" || e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey) return;
          if (e.target !== view.dom) return;
          if (view.dom.ownerDocument.querySelector(".bn-formatting-toolbar, .bn-link-toolbar")) return;
          e.preventDefault();
        };
        view.dom.addEventListener("keydown", onKeyDown);
        return { destroy: () => view.dom.removeEventListener("keydown", onKeyDown) };
      },
    }),
  ],
});
