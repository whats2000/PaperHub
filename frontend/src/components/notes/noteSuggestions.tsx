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
import { SuggestionMark, type TablePartChange } from "./SuggestionMark";

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
  // A table's caption and notes are shown as their own change
  // (`tablePartChanges`), not folded into the cells.
  const props = type === "table" ? {} : ((p.props as Record<string, unknown> | undefined) ?? {});
  return blockText(type, props, p.content);
}

/** The caption / notes a table suggestion changes against the block's
 * current props. A part the proposal leaves out is kept by the accept
 * (the props merge), so it is no change. */
export function tablePartChanges(s: NoteSuggestion, current: Record<string, unknown>): TablePartChange[] {
  const p = s.proposed;
  if (s.kind !== "update" || !p || p.type !== "table") return [];
  const props = (p.props as Record<string, unknown> | undefined) ?? {};
  const out: TablePartChange[] = [];
  for (const part of ["caption", "notes"] as const) {
    const to = props[part];
    if (typeof to !== "string") continue;
    const cur = current[part];
    const from = typeof cur === "string" ? cur : "";
    if (from !== to) out.push({ part, from, to });
  }
  return out;
}

/** Whether an accept came back `stale`: the block changed since the
 * suggestion was made, so nothing was applied and it stays open. */
export function isStale(res: ApplyResult, blockId: string): boolean {
  return res.rejected.some((r) => r.block_id === blockId && r.reason === "stale");
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

/** A suggestion counts as arriving for this long after it is first drawn:
 * its marks fade in then, and are not replayed by later redraws. */
const ENTER_MS = 400;

function insElement(text: string, id: string, entering: boolean): HTMLElement {
  const el = document.createElement("ins");
  el.className = entering ? "note-sug-ins note-sug-enter" : "note-sug-ins";
  el.setAttribute("data-suggestion-id", id);
  el.textContent = text;
  return el;
}

/** The decorations of every open suggestion in `doc`. */
export function suggestionDecorations(
  doc: PMNode,
  view: SuggestionsView,
  entering: (id: string) => boolean = () => false,
): DecorationSet {
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
    const enter = entering(s.id);
    const mark = { class: enter ? "note-sug-del note-sug-enter" : "note-sug-del", "data-suggestion-id": s.id };
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
            Decoration.widget(where, () => insElement(d.ins, s.id, enter), {
              side: 1,
              key: `ins:${s.id}:${d.ins}`,
              ignoreSelection: true,
              marks: [],
            }),
          );
        }
      }
    }
    // The bar: at the end of a line's text; after a block with no text of
    // its own (a table: past the table and its notes, never in its rows;
    // for a leaf block the two places are the same).
    const barAt = content.isTextblock ? end : pos + 1 + content.nodeSize;
    decos.push(
      Decoration.widget(barAt, () => view.host(s.id), {
        // After the table notes' host (side 1) at the same place.
        side: content.isTextblock ? 1 : 2,
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

/** When each suggestion was first drawn (its words fade in for ENTER_MS).
 * `keep` forgets the ones no longer open, so the map never grows past the
 * suggestions on screen. */
export function enterTracker(now: () => number = Date.now) {
  const firstSeen = new Map<string, number>();
  return {
    entering: (id: string) => {
      const t = now();
      if (!firstSeen.has(id)) firstSeen.set(id, t);
      return t - firstSeen.get(id)! < ENTER_MS;
    },
    keep: (open: Iterable<string>) => {
      const ids = new Set(open);
      for (const id of firstSeen.keys()) if (!ids.has(id)) firstSeen.delete(id);
    },
    size: () => firstSeen.size,
  };
}

/** The extension; `get` returns the current suggestions and bar hosts. */
export function noteSuggestionMarks(get: () => SuggestionsView) {
  let cache: { doc: PMNode; version: number; set: DecorationSet } | null = null;
  const seen = enterTracker();
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
              const view = get();
              seen.keep(view.suggestions.map((s) => s.id));
              cache = { doc: state.doc, version, set: suggestionDecorations(state.doc, view, seen.entering) };
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

/** The bar hosts of one editor (kept across renders; one per suggestion).
 * A span shown as a block (index.css `.note-sug-host`): the host sits inside
 * the line's <p>, where only phrasing content is valid. */
export function useSuggestionHosts() {
  const hosts = useRef(new Map<string, HTMLElement>());
  return useCallback((id: string) => {
    let el = hosts.current.get(id);
    if (!el) {
      el = document.createElement("span");
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

  const decide = async (s: NoteSuggestion, accept: boolean, exit: () => Promise<void>): Promise<void | "kept"> => {
    // Where each open thread on the block sits now, read at the click.
    const hints: ReanchorHint[] = accept
      ? (useNotesStore.getState().notes[noteId]?.comments ?? [])
          .filter((c) => c.block_id === s.block_id && c.status === "open")
          .map((c) => ({ id: c.id, blockId: c.block_id, quote: c.quote, near: commentAnchor(editor, c.id) }))
      : [];
    // Handed to the editor only once the server took the decision, right
    // before the saved note is shown.
    const settle = async (res: ApplyResult) => {
      // Stale: it stays on screen and is redrawn against the current text.
      if (isStale(res, s.block_id)) return;
      // A withdrawn accept (the block was gone) rewrote nothing.
      if (hints.length && res.applied.includes(s.block_id)) pendingReanchorRef.current = hints;
      await exit();
    };
    try {
      const res = await resolveSuggestion(noteId, s.id, accept, settle);
      if (accept && isStale(res, s.block_id)) {
        // Not an error: the user's words changed since; the reloaded note
        // shows the suggestion against them, and Accept works again.
        toast(t("notes:suggestion.refreshed", "Your text changed since this was suggested. It now shows against the current text."));
        return "kept";
      }
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
    const block = (() => {
      try {
        return editor.getBlock(s.block_id) as { content?: unknown; props?: Record<string, unknown> } | undefined;
      } catch {
        return undefined;
      }
    })();
    const inText = Array.isArray(block?.content);
    return createPortal(
      <SuggestionMark
        suggestionId={s.id}
        deletes={s.kind === "delete"}
        detached={inText || s.kind === "delete" ? null : proposedText(s)}
        partChanges={tablePartChanges(s, block?.props ?? {})}
        onDecide={(accept, exit) => decide(s, accept, exit)}
      />,
      host(s.id),
      s.id,
    );
  });
}
