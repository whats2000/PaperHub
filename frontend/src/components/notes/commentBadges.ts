/** Open comment threads whose words are no longer in the note (an accepted
 * suggestion rewrote them): they have no underline to click, so each block
 * holding such threads gets a small badge with their count; a click opens
 * them one after another. The badge is a widget decoration (never note
 * content) whose host React renders into; it fades and zooms in and out.
 */
import { createExtension, type BlockNoteEditor } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { useCallback, useEffect, useRef, useState } from "react";

import { prefersReducedMotion } from "@/lib/motion";
import { useNotesStore } from "@/store/notes";
import type { NoteComment } from "@/types/notes";

// The comment code works on any schema; it reads block ids and marks only.
type Ed = BlockNoteEditor<any, any, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** Block id -> ids of its open threads without words in the note. */
export type LostThreads = Map<string, string[]>;

export interface BadgeView {
  threads: readonly NoteComment[];
  /** Blocks that show a badge now (lost threads, or a badge closing). */
  badges: ReadonlySet<string>;
  host: (blockId: string) => HTMLElement;
  /** Told the lost threads whenever they change. */
  onLost: (lost: LostThreads) => void;
}

const key = new PluginKey<number>("paperhubCommentBadges");
const EXIT_MS = 200;

/** The open threads of `threads` whose block is in `doc` but whose id is on
 * no text there. */
export function lostThreads(doc: PMNode, threads: readonly NoteComment[]): LostThreads {
  const blocks = new Set<string>();
  const anchored = new Set<string>();
  doc.descendants((node) => {
    if (node.type.name === "blockContainer") blocks.add(String(node.attrs.id));
    if (node.isText) {
      for (const m of node.marks) if (m.type.name === "comment") anchored.add(String(m.attrs.stringValue));
    }
    return true;
  });
  const lost: LostThreads = new Map();
  for (const c of threads) {
    if (c.status !== "open" || anchored.has(c.id) || !blocks.has(c.block_id)) continue;
    lost.set(c.block_id, [...(lost.get(c.block_id) ?? []), c.id]);
  }
  return lost;
}

const same = (a: LostThreads, b: LostThreads) =>
  a.size === b.size && [...a].every(([k, v]) => b.get(k)?.join() === v.join());

function badgeDecorations(doc: PMNode, view: BadgeView): DecorationSet {
  if (!view.badges.size) return DecorationSet.empty;
  const decos: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (node.type.name !== "blockContainer") return true;
    const id = String(node.attrs.id);
    const content = node.firstChild;
    if (!view.badges.has(id) || !content) return true;
    // At the end of a line's text; after a block with no text of its own
    // (a table: past the table and its notes, never in its rows), as the
    // suggestion bar is placed.
    const at = content.isTextblock ? pos + 2 + content.content.size : pos + 1 + content.nodeSize;
    decos.push(
      Decoration.widget(at, () => view.host(id), {
        // After the table notes' host (side 1) at the same place.
        side: 2,
        key: `cbadge:${id}`,
        ignoreSelection: true,
        stopEvent: () => true,
        marks: [],
      }),
    );
    return true;
  });
  return DecorationSet.create(doc, decos);
}

export function lostCommentBadges(get: () => BadgeView) {
  let cache: { doc: PMNode; version: number; set: DecorationSet } | null = null;
  let last: LostThreads = new Map();
  const report = (doc: PMNode) => {
    const view = get();
    const now = lostThreads(doc, view.threads);
    if (!same(now, last)) {
      last = now;
      view.onLost(now);
    }
  };
  return createExtension({
    key: "paperhubCommentBadges",
    prosemirrorPlugins: [
      new Plugin<number>({
        key,
        state: {
          init: () => 0,
          apply: (tr, version) => (tr.getMeta(key) ? version + 1 : version),
        },
        view: (editorView) => {
          queueMicrotask(() => report(editorView.state.doc));
          return { update: (v) => report(v.state.doc) };
        },
        props: {
          decorations(state) {
            const version = key.getState(state) ?? 0;
            if (cache?.doc !== state.doc || cache.version !== version) {
              cache = { doc: state.doc, version, set: badgeDecorations(state.doc, get()) };
            }
            return cache.set;
          },
        },
      }),
    ],
  });
}

export interface Badge {
  ids: string[];
  closing: boolean;
}

/** The badges' state and hosts. `badgeView` feeds the plugin; `badges`
 * renders them (portals) and opens the threads through `open`. */
export function useLostCommentBadges(noteId: number) {
  const [badges, setBadges] = useState<Record<string, Badge>>({});
  const hosts = useRef(new Map<string, HTMLElement>());
  const state = useRef(badges);
  useEffect(() => {
    state.current = badges;
  }, [badges]);

  const host = useCallback((blockId: string) => {
    let el = hosts.current.get(blockId);
    if (!el) {
      el = document.createElement("span");
      el.className = "note-cbadge-host";
      el.contentEditable = "false";
      hosts.current.set(blockId, el);
    }
    return el;
  }, []);

  const onLost = useCallback((lost: LostThreads) => {
    setBadges((prev) => {
      const next: Record<string, Badge> = {};
      for (const [blockId, ids] of lost) next[blockId] = { ids, closing: false };
      for (const [blockId, b] of Object.entries(prev)) {
        if (!(blockId in next) && !prefersReducedMotion()) next[blockId] = { ids: b.ids, closing: true };
      }
      return next;
    });
  }, []);

  // A closing badge leaves once its exit has played.
  useEffect(() => {
    if (!Object.values(badges).some((b) => b.closing)) return;
    const t = setTimeout(() => {
      setBadges((prev) => Object.fromEntries(Object.entries(prev).filter(([, b]) => !b.closing)));
    }, EXIT_MS);
    return () => clearTimeout(t);
  }, [badges]);

  const badgeView = useCallback(
    (): BadgeView => ({
      threads: useNotesStore.getState().notes[noteId]?.comments ?? [],
      badges: new Set(Object.keys(state.current)),
      host,
      onLost,
    }),
    [noteId, host, onLost],
  );

  return { badges, badgeView, host };
}

/** Redraws after the threads or the badges changed (no transaction did). */
export function useBadgeRefresh(editor: Ed, deps: unknown[]) {
  useEffect(() => {
    try {
      const view = editor._tiptapEditor.view;
      view.dispatch(view.state.tr.setMeta(key, true).setMeta("addToHistory", false));
    } catch {
      /* not mounted yet */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, ...deps]);
}
