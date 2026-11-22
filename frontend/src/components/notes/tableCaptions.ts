/** Where a table's caption and notes are drawn: a widget decoration above
 * each table (inside its block, before the table) and one below it, each
 * a host element that `TableCaptions` (TableCaption.tsx) renders into. The
 * caption and notes themselves are the table block's `caption` / `notes`
 * props, never document text, so the hosts are never note content.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import type { TablePart } from "./noteFind";

export interface CaptionHosts {
  /** The element a table's caption (or notes) is rendered into; the same
   * one every time, so a redrawn widget keeps what React put in it. */
  host: (blockId: string, part: TablePart) => HTMLElement;
  /** Lets go of the hosts of tables no longer in the note. */
  keep: (blockIds: ReadonlySet<string>) => void;
  /** How many hosts are held. */
  size: () => number;
}

export function createCaptionHosts(): CaptionHosts {
  const hosts = new Map<string, HTMLElement>();
  return {
    keep(blockIds) {
      for (const key of [...hosts.keys()]) {
        if (!blockIds.has(key.slice(0, key.lastIndexOf(":")))) hosts.delete(key);
      }
    },
    size: () => hosts.size,
    host(blockId, part) {
      const key = `${blockId}:${part}`;
      let el = hosts.get(key);
      if (!el) {
        el = document.createElement("div");
        el.className = "note-table-part-host";
        el.contentEditable = "false";
        hosts.set(key, el);
      }
      return el;
    },
  };
}

/** A table block: its id, caption and notes. */
export interface TableInfo {
  id: string;
  caption: string;
  notes: string;
}

/** The tables of `doc`, in document order. */
export function tablesOf(doc: PMNode): TableInfo[] {
  const out: TableInfo[] = [];
  doc.descendants((node) => {
    if (node.type.name !== "blockContainer") return true;
    const content = node.firstChild;
    if (content?.type.name === "table") {
      out.push({
        id: String(node.attrs.id),
        caption: String(content.attrs.caption ?? ""),
        notes: String(content.attrs.notes ?? ""),
      });
    }
    return true;
  });
  return out;
}

function widgets(doc: PMNode, hosts: CaptionHosts): DecorationSet {
  const decos: Decoration[] = [];
  const spec = { ignoreSelection: true, stopEvent: () => true, marks: [] };
  const ids = new Set<string>();
  doc.descendants((node, pos) => {
    if (node.type.name !== "blockContainer") return true;
    const content = node.firstChild;
    if (content?.type.name === "table") {
      const id = String(node.attrs.id);
      ids.add(id);
      // Inside the block: before its table, and after it (before any
      // nested blocks).
      decos.push(
        Decoration.widget(pos + 1, () => hosts.host(id, "caption"), { ...spec, side: -1, key: `tcap:${id}` }),
        Decoration.widget(pos + 1 + content.nodeSize, () => hosts.host(id, "notes"), {
          ...spec,
          side: 1,
          key: `tnotes:${id}`,
        }),
      );
    }
    return true;
  });
  // A deleted table's hosts go (an undo makes new ones; React re-renders
  // into them).
  hosts.keep(ids);
  return DecorationSet.create(doc, decos);
}

const key = new PluginKey("paperhubTableCaptions");

/** The editor extension: a caption host above and a notes host below
 * every table. */
export function tableCaptions(hosts: CaptionHosts) {
  let cache: { doc: PMNode; set: DecorationSet } | null = null;
  return createExtension({
    key: "paperhubTableCaptions",
    prosemirrorPlugins: [
      new Plugin({
        key,
        props: {
          decorations(state) {
            if (cache?.doc !== state.doc) cache = { doc: state.doc, set: widgets(state.doc, hosts) };
            return cache.set;
          },
        },
      }),
    ],
  });
}

// ------------------------------------------------- opening a part for editing

export interface PartRequest {
  blockId: string;
  part: TablePart;
  seq: number;
  /** Characters to select once it is open (a find match). */
  select?: [number, number];
}

export interface PartRequests {
  /** Opens a table's caption (or notes) for editing, the caret in it (or
   * `select` selected): "Add caption" in the table menu, or find closing
   * on a match there. */
  request: (blockId: string, part: TablePart, select?: [number, number]) => void;
  subscribe: (f: () => void) => () => void;
  get: () => PartRequest | null;
}

// One per editor: two editors on one note (the pane and the card) never
// both open a part.
const requestsByEditor = new WeakMap<object, PartRequests>();

export function partRequests(editor: object): PartRequests {
  let out = requestsByEditor.get(editor);
  if (!out) {
    let current: PartRequest | null = null;
    const listeners = new Set<() => void>();
    out = {
      request(blockId, part, select) {
        current = { blockId, part, seq: (current?.seq ?? 0) + 1, ...(select ? { select } : {}) };
        listeners.forEach((f) => f());
      },
      subscribe(f) {
        listeners.add(f);
        return () => {
          listeners.delete(f);
        };
      },
      get: () => current,
    };
    requestsByEditor.set(editor, out);
  }
  return out;
}
