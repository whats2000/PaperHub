/** Find inside a note: every match of a query as editor decorations, the
 * current one drawn stronger.
 *
 * Matching is case-insensitive over each text block's words. An inline node
 * counts as one unit (an inline equation by its LaTeX, a mention by its
 * label, an inline citation by its words, a `[[` link by its display text
 * or, without one, its target's saved name): a match touching it covers the
 * whole node. A paper reference block matches by its quote and is one unit
 * too. Matches never cross blocks.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey, type EditorState } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";

import { blockIdInDoc } from "./blockId";
import { citeDefaultText } from "./blocks/markCiteText";

export interface FindMatch {
  from: number;
  to: number;
  /** "text": words (maybe with inline nodes); "node": one inline node;
   * "block": a reference block (`from` is its content node). */
  kind: "text" | "node" | "block";
  /** The block holding the match. */
  blockId: string;
}

/** Reference blocks that match by their quote. */
const REF_TYPES = new Set(["paperQuote", "paperEquation", "paperFigure", "paperRegion"]);

/** Lowercase one character at a time, so indexes keep lining up. */
function fold(s: string): string {
  let out = "";
  for (const c of s) {
    const l = c.toLowerCase();
    out += l.length === c.length ? l : c;
  }
  return out;
}

function indexesOf(hay: string, needle: string): number[] {
  const out: number[] = [];
  if (!needle) return out;
  for (let i = hay.indexOf(needle); i >= 0; i = hay.indexOf(needle, i + needle.length)) out.push(i);
  return out;
}

/** What an inline node reads as. */
const str = (v: unknown): string => (typeof v === "string" ? v : "");

function inlineText(node: PMNode, quotes: Map<string, string>): string {
  const a = node.attrs as Record<string, unknown>;
  switch (node.type.name) {
    case "inlineMath":
      return str(a.latex);
    case "mention":
      return str(a.label);
    case "markCite":
      return str(a.text) || citeDefaultText(quotes.get(str(a.markId)) ?? "");
    case "noteLink":
      return str(a.label) || str(a.name);
    default:
      return "";
  }
}

/** Every match of `query` in `doc`, in document order. */
export function findMatches(doc: PMNode, query: string): FindMatch[] {
  const q = fold(query);
  if (!q.trim()) return [];
  const quotes = new Map<string, string>();
  doc.descendants((node) => {
    if (REF_TYPES.has(node.type.name) && node.attrs.markId) quotes.set(String(node.attrs.markId), String(node.attrs.quote ?? ""));
    return !node.isTextblock;
  });
  const out: FindMatch[] = [];
  doc.descendants((node, pos) => {
    const blockId = () => blockIdInDoc(doc, pos) ?? "";
    if (REF_TYPES.has(node.type.name)) {
      for (let n = indexesOf(fold(String(node.attrs.quote ?? "")), q).length; n > 0; n--) {
        out.push({ from: pos, to: pos + node.nodeSize, kind: "block", blockId: blockId() });
      }
      return false;
    }
    if (!node.isTextblock) return true;
    // Each character, and the document range it stands for.
    let text = "";
    const at: { from: number; to: number; atom: boolean }[] = [];
    node.forEach((child, offset) => {
      const p = pos + 1 + offset;
      const s = child.isText ? (child.text ?? "") : inlineText(child, quotes);
      const f = fold(s);
      text += f;
      for (let i = 0; i < f.length; i++) {
        at.push(child.isText ? { from: p + i, to: p + i + 1, atom: false } : { from: p, to: p + child.nodeSize, atom: true });
      }
    });
    for (const i of indexesOf(text, q)) {
      const a = at[i]!;
      const b = at[i + q.length - 1]!;
      const kind = a.atom && b.atom && a.from === b.from ? "node" : "text";
      out.push({ from: a.from, to: b.to, kind, blockId: blockId() });
    }
    return false;
  });
  return out;
}

export interface FindState {
  matches: FindMatch[];
  current: number;
}

export const noteFindKey = new PluginKey<FindState>("paperhubNoteFind");

const ALL = "ph-find";
const CURRENT = "ph-find ph-find-current";

function decorations(doc: PMNode, s: FindState): DecorationSet {
  if (!s.matches.length) return DecorationSet.empty;
  const decos = s.matches.map((m, i) => {
    const attrs = { class: i === s.current ? CURRENT : ALL, "data-find": m.kind };
    return m.kind === "text" ? Decoration.inline(m.from, m.to, attrs) : Decoration.node(m.from, m.to, attrs);
  });
  return DecorationSet.create(doc, decos);
}

/** The editor extension: draws the matches set with `setFindMatches`. */
export function noteFind() {
  return createExtension({
    key: "paperhubNoteFind",
    prosemirrorPlugins: [
      new Plugin<FindState>({
        key: noteFindKey,
        state: {
          init: () => ({ matches: [], current: -1 }),
          apply: (tr, s) => {
            const meta = tr.getMeta(noteFindKey) as FindState | undefined;
            if (meta) return meta;
            if (!tr.docChanged || !s.matches.length) return s;
            // Edited while finding: the matches move with the text.
            const matches = s.matches
              .map((m) => ({ ...m, from: tr.mapping.map(m.from, 1), to: tr.mapping.map(m.to, -1) }))
              .filter((m) => m.to > m.from);
            return { matches, current: Math.min(s.current, matches.length - 1) };
          },
        },
        props: {
          decorations: (state: EditorState) => {
            const s = noteFindKey.getState(state);
            return s ? decorations(state.doc, s) : DecorationSet.empty;
          },
        },
      }),
    ],
  });
}

/** Draws these matches (none: clears them). */
export function setFindMatches(view: EditorView, matches: FindMatch[], current: number) {
  view.dispatch(view.state.tr.setMeta(noteFindKey, { matches, current }));
}
