/** The agent's suggestions on the user's text, drawn in the editor as tracked
 * changes: the words it would replace struck through, its proposal inserted
 * after them, and a bar (SuggestionMark) with Accept / Reject under the line.
 *
 * Everything is a ProseMirror decoration, never note content, so nothing of
 * it is synced; the user's words stay as they are until they accept. The
 * bar is a widget whose DOM host React renders into through a portal.
 */
import { createExtension, type BlockNoteEditor } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";
import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { blockText } from "@/lib/notes/ops";
import { diffText } from "@/lib/notes/textDiff";
import { useNotesStore } from "@/store/notes";
import type { ApplyResult, NoteSuggestion } from "@/types/notes";

import { commentAnchor, type ReanchorHint } from "./noteComments";
import { SuggestionMark } from "./SuggestionMark";

// The suggestion code works on any schema; it reads block ids and text only.
type Ed = BlockNoteEditor<any, any, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface SuggestionsView {
  suggestions: readonly NoteSuggestion[];
  /** The DOM host of a suggestion's bar. */
  host: (suggestionId: string) => HTMLElement;
}

const key = new PluginKey<number>("paperhubSuggestions");

/** The text a suggestion proposes (empty for a delete). */
export function proposedText(s: NoteSuggestion): string {
  const p = s.proposed;
  if (s.kind === "delete" || !p) return "";
  const type = typeof p.type === "string" ? p.type : "paragraph";
  return blockText(type, (p.props as Record<string, unknown> | undefined) ?? {}, p.content);
}

/** A textblock's text with the document position of each character
 * (anything that is not text, e.g. a mention chip, is one placeholder). */
function textWithPositions(content: PMNode, start: number): { text: string; at: number[] } {
  let text = "";
  const at: number[] = [];
  content.descendants((child, off) => {
    if (child.isText) {
      for (let k = 0; k < child.text!.length; k++) {
        at.push(start + off + k);
        text += child.text![k];
      }
    } else if (child.isLeaf) {
      at.push(start + off);
      text += "￼";
    }
    return true;
  });
  return { text, at };
}

function insElement(text: string, id: string): HTMLElement {
  const el = document.createElement("ins");
  el.className = "note-sug-ins";
  el.setAttribute("data-suggestion-id", id);
  el.textContent = text;
  return el;
}

/** The decorations of every open suggestion in `doc`. */
export function suggestionDecorations(doc: PMNode, view: SuggestionsView): DecorationSet {
  const byBlock = new Map(view.suggestions.map((s) => [s.block_id, s]));
  if (!byBlock.size) return DecorationSet.empty;
  const decos: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (node.type.name !== "blockContainer") return true;
    const s = byBlock.get(String(node.attrs.id));
    const content = node.firstChild;
    if (!s || !content) return true;
    const start = pos + 2; // inside the container, inside the content
    const end = start + content.content.size;
    const mark = { class: "note-sug-del", "data-suggestion-id": s.id };
    if (content.isTextblock) {
      const { text, at } = textWithPositions(content, start);
      if (s.kind === "delete") {
        if (text) decos.push(Decoration.inline(at[0]!, at[text.length - 1]! + 1, mark));
      } else {
        const d = diffText(text, proposedText(s));
        const p = d.prefix.length;
        const gone = d.del.length;
        if (gone) decos.push(Decoration.inline(at[p]!, at[p + gone - 1]! + 1, mark));
        if (d.ins) {
          const where = gone ? at[p + gone - 1]! + 1 : p < at.length ? at[p]! : end;
          decos.push(
            Decoration.widget(where, () => insElement(d.ins, s.id), {
              side: 1,
              key: `ins:${s.id}:${d.ins}`,
              ignoreSelection: true,
              marks: [],
            }),
          );
        }
      }
    }
    decos.push(
      Decoration.widget(end, () => view.host(s.id), {
        side: 1,
        key: `bar:${s.id}`,
        ignoreSelection: true,
        stopEvent: () => true,
        marks: [],
      }),
    );
    return true;
  });
  return DecorationSet.create(doc, decos);
}

/** The extension; `get` returns the current suggestions and bar hosts. */
export function noteSuggestionMarks(get: () => SuggestionsView) {
  let cache: { doc: PMNode; version: number; set: DecorationSet } | null = null;
  return createExtension({
    key: "paperhubSuggestions",
    prosemirrorPlugins: [
      new Plugin<number>({
        key,
        state: {
          init: () => 0,
          apply: (tr, version) => (tr.getMeta(key) ? version + 1 : version),
        },
        props: {
          decorations(state) {
            const version = key.getState(state) ?? 0;
            if (cache?.doc !== state.doc || cache.version !== version) {
              cache = { doc: state.doc, version, set: suggestionDecorations(state.doc, get()) };
            }
            return cache.set;
          },
        },
      }),
    ],
  });
}

/** Redraw the suggestions after the store's list changed. */
export function refreshSuggestionMarks(view: EditorView): void {
  view.dispatch(view.state.tr.setMeta(key, true).setMeta("addToHistory", false));
}

/** The bar hosts of one editor (kept across renders; one per suggestion). */
export function useSuggestionHosts() {
  const hosts = useRef(new Map<string, HTMLElement>());
  return useCallback((id: string) => {
    let el = hosts.current.get(id);
    if (!el) {
      el = document.createElement("div");
      el.className = "note-sug-host";
      el.contentEditable = "false";
      hosts.current.set(id, el);
    }
    return el;
  }, []);
}

/**
 * Draws the note's open suggestions and handles Accept / Reject. Accepting
 * rewrites the block, which drops the comment style on its words: the open
 * threads on that block are put back over their quotes in the new text by
 * `reanchor`, which the editor calls once it shows the saved note.
 */
export function useNoteSuggestions(
  editor: Ed,
  noteId: number,
  host: (id: string) => HTMLElement,
  pendingReanchorRef: React.MutableRefObject<ReanchorHint[] | null>,
): ReactNode {
  const { t } = useTranslation();
  const suggestions = useNotesStore((s) => s.notes[noteId]?.suggestions);
  const resolveSuggestion = useNotesStore((s) => s.resolveSuggestion);

  useEffect(() => {
    try {
      refreshSuggestionMarks(editor._tiptapEditor.view);
    } catch {
      /* not mounted yet: the decorations draw on mount */
    }
  }, [editor, suggestions]);

  const decide = async (s: NoteSuggestion, accept: boolean, exit: () => Promise<void>) => {
    // Where each open thread on the block sits now, read at the click.
    const hints: ReanchorHint[] = accept
      ? (useNotesStore.getState().notes[noteId]?.comments ?? [])
          .filter((c) => c.block_id === s.block_id && c.status === "open")
          .map((c) => ({ id: c.id, blockId: c.block_id, quote: c.quote, near: commentAnchor(editor, c.id) }))
      : [];
    // Handed to the editor only once the server took the decision, right
    // before the saved note is shown.
    const settle = async (res: ApplyResult) => {
      // A withdrawn accept (the block was gone) rewrote nothing.
      if (hints.length && res.applied.includes(s.block_id)) pendingReanchorRef.current = hints;
      await exit();
    };
    try {
      const res = await resolveSuggestion(noteId, s.id, accept, settle);
      if (accept && !res.applied.includes(s.block_id)) {
        toast(t("notes:suggestion.gone", "That suggestion no longer applies."));
      }
    } catch (err) {
      pendingReanchorRef.current = null;
      toast.error(t("notes:suggestion.failed", "Couldn't apply that. Try again."));
      // Decided elsewhere meanwhile (409): show the note as it is now.
      void useNotesStore
        .getState()
        .loadNote(noteId)
        .catch(() => undefined);
      throw err;
    }
  };

  return (suggestions ?? []).map((s) => {
    const inText = (() => {
      try {
        const block = editor.getBlock(s.block_id) as { content?: unknown } | undefined;
        return Array.isArray(block?.content);
      } catch {
        return false;
      }
    })();
    return createPortal(
      <SuggestionMark
        suggestionId={s.id}
        deletes={s.kind === "delete"}
        detached={inText || s.kind === "delete" ? null : proposedText(s)}
        onDecide={(accept, exit) => decide(s, accept, exit)}
      />,
      host(s.id),
      s.id,
    );
  });
}
