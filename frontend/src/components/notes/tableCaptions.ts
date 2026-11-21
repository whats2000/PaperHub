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
}

export function createCaptionHosts(): CaptionHosts {
  const hosts = new Map<string, HTMLElement>();
  return {
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
  doc.descendants((node, pos) => {
    if (node.type.name !== "blockContainer") return true;
    const content = node.firstChild;
    if (content?.type.name === "table") {
      const id = String(node.attrs.id);
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

// ------------------------------------------------- "Add caption" / "Add notes"

export interface PartRequest {
  blockId: string;
  part: TablePart;
  seq: number;
}

let request: PartRequest | null = null;
const listeners = new Set<() => void>();

/** Opens a table's caption (or notes) for editing, from the table menu. */
export function requestTablePart(blockId: string, part: TablePart): void {
  request = { blockId, part, seq: (request?.seq ?? 0) + 1 };
  listeners.forEach((f) => f());
}

export function subscribeTablePart(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export const tablePartRequest = (): PartRequest | null => request;
