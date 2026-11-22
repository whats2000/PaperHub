/** Editor document <-> note block ops.
 *
 * The editor holds a BlockNote tree; the backend stores flat blocks with a
 * `parent_id` and sibling order. Every block keeps a stable id on both sides,
 * so a change is the difference of two trees by id: insert / update / delete /
 * move, each placed by `after` (or `before` for a new first child) and
 * `parent_id`.
 */
import type { NoteBlock, NoteBlockIn, NoteBlockType, NoteContent, NoteOp } from "@/types/notes";
import { MARK_TYPES } from "@/lib/marks/blockTypes";

/** The parts of a BlockNote block the sync reads (any schema). */
export interface EditorBlock {
  id: string;
  type: string;
  props: Record<string, unknown>;
  content?: unknown;
  children: EditorBlock[];
}

/** The brief's name for an editor block. */
export type Block = EditorBlock;

/** Props BlockNote can only hold as strings (its props are primitives). */
const JSON_PROPS = new Set(["pieces", "source"]);
/** Optional mark props whose editor default means "absent" (a quote with no
 * colour, a mark not made on a translation or its words still there, an anchor not lost). */
const MARK_OPTIONAL_DEFAULTS: Record<string, unknown> = {
  color: "",
  style: "",
  lang: "",
  orig: "",
  sourceRef: "",
  translationChanged: false,
  anchorLost: false,
  piecesLost: 0,
};

function encodeProps(b: NoteBlock): Record<string, unknown> {
  // A mark block always carries every optional prop, so reloading one whose
  // flag was cleared (an anchor found again) clears it in the editor too.
  const out: Record<string, unknown> = MARK_TYPES.has(b.type) ? { ...MARK_OPTIONAL_DEFAULTS } : {};
  for (const [k, v] of Object.entries(b.props)) {
    if (v === null && MARK_TYPES.has(b.type) && k in MARK_OPTIONAL_DEFAULTS) continue;
    out[k] = JSON_PROPS.has(k) && typeof v !== "string" ? JSON.stringify(v ?? null) : v;
  }
  if (b.type === "section") out.sectionKey = b.section_key ?? "";
  return out;
}

function decodeProps(type: string, props: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) {
    if (type === "section" && k === "sectionKey") continue;
    if (MARK_TYPES.has(type) && k in MARK_OPTIONAL_DEFAULTS && MARK_OPTIONAL_DEFAULTS[k] === v) continue;
    // A table with no caption (or no notes) stores none.
    if (type === "table" && (k === "caption" || k === "notes") && v === "") continue;
    if (JSON_PROPS.has(k) && typeof v === "string") {
      try {
        out[k] = JSON.parse(v) as unknown;
      } catch {
        out[k] = v;
      }
      continue;
    }
    out[k] = v;
  }
  return out;
}

/** The op payload of an editor block. */
export function opBlock(b: EditorBlock): NoteBlockIn {
  const out: NoteBlockIn = {
    type: b.type as NoteBlockType,
    props: decodeProps(b.type, b.props),
  };
  if (b.content !== undefined) out.content = b.content as NoteContent;
  if (b.type === "section" && typeof b.props.sectionKey === "string" && b.props.sectionKey) {
    out.section_key = b.props.sectionKey;
  }
  return out;
}

/** An update's block: its full props, and a prop the block had and no
 * longer has (a caption cleared, a colour taken off) named as null, since
 * the server patches the stored props (a key left out is kept). A type
 * change takes the new type's props whole. */
function updateBlock(old: EditorBlock, b: EditorBlock): NoteBlockIn {
  const out = opBlock(b);
  if (old.type !== b.type) return out;
  const props: Record<string, unknown> = { ...out.props };
  for (const k of Object.keys(decodeProps(old.type, old.props))) if (!(k in props)) props[k] = null;
  return { ...out, props };
}

/** Flat backend blocks -> the editor tree (sibling order kept). */
export function toEditorBlocks(blocks: NoteBlock[]): EditorBlock[] {
  const ids = new Set(blocks.map((b) => b.id));
  const kids = new Map<string | null, NoteBlock[]>();
  for (const b of blocks) {
    const parent = b.parent_id !== null && ids.has(b.parent_id) ? b.parent_id : null;
    const list = kids.get(parent) ?? [];
    list.push(b);
    kids.set(parent, list);
  }
  const build = (parent: string | null): EditorBlock[] =>
    (kids.get(parent) ?? []).map((b) => {
      const e: EditorBlock = { id: b.id, type: b.type, props: encodeProps(b), children: build(b.id) };
      if (b.content !== null && b.content !== undefined) e.content = b.content;
      return e;
    });
  return build(null);
}

/** Every id in a tree. */
export function treeIds(tree: EditorBlock[], into = new Set<string>()): Set<string> {
  for (const b of tree) {
    into.add(b.id);
    treeIds(b.children, into);
  }
  return into;
}

function isEmptyContent(c: unknown): boolean {
  return c === undefined || c === null || c === "" || (Array.isArray(c) && c.length === 0);
}

/** Drop empty leaf paragraphs the server has never seen (the editor's own
 * trailing line, a fresh Enter): they are sent once they hold something. */
export function pruneUnsavedEmpty(tree: EditorBlock[], known: Set<string>): EditorBlock[] {
  const out: EditorBlock[] = [];
  for (const b of tree) {
    const children = pruneUnsavedEmpty(b.children, known);
    if (
      b.type === "paragraph" &&
      !known.has(b.id) &&
      children.length === 0 &&
      isEmptyContent(b.content)
    ) {
      continue;
    }
    out.push(children === b.children ? b : { ...b, children });
  }
  return out;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    const bb = b as unknown[];
    return a.length === bb.length && a.every((x, i) => deepEqual(x, bb[i]));
  }
  const ao = a as Record<string, unknown>;
  const bo = b as Record<string, unknown>;
  const ak = Object.keys(ao).filter((k) => ao[k] !== undefined);
  const bk = Object.keys(bo).filter((k) => bo[k] !== undefined);
  return ak.length === bk.length && ak.every((k) => deepEqual(ao[k], bo[k]));
}

interface Placed {
  block: EditorBlock;
  parent: string | null;
}

function index(tree: EditorBlock[]): {
  byId: Map<string, Placed>;
  kids: Map<string | null, string[]>;
} {
  const byId = new Map<string, Placed>();
  const kids = new Map<string | null, string[]>();
  const walk = (list: EditorBlock[], parent: string | null) => {
    kids.set(parent, list.map((b) => b.id));
    for (const b of list) {
      byId.set(b.id, { block: b, parent });
      walk(b.children, b.id);
    }
  };
  walk(tree, null);
  return { byId, kids };
}

/** Indexes (into `seq`) of one longest increasing subsequence. */
function lisIndexes(seq: number[]): Set<number> {
  const len = seq.map(() => 1);
  const prev = seq.map(() => -1);
  let best = -1;
  for (let i = 0; i < seq.length; i++) {
    for (let j = 0; j < i; j++) {
      if (seq[j]! < seq[i]! && len[j]! + 1 > len[i]!) {
        len[i] = len[j]! + 1;
        prev[i] = j;
      }
    }
    if (best < 0 || len[i]! > len[best]!) best = i;
  }
  const out = new Set<number>();
  for (let i = best; i >= 0; i = prev[i]!) out.add(i);
  return out;
}

/** The ops that turn `prev` into `next`, matched by block id.
 *
 * Inserts, moves and updates come in `next`'s document order, so every
 * anchor (`after`, `before`, `parent_id`) exists when its op runs; deletes
 * come last, in reverse document order (a subtree delete takes the children
 * that were not moved out).
 */
export function diffDocuments(prev: EditorBlock[], next: EditorBlock[]): NoteOp[] {
  const p = index(prev);
  const n = index(next);

  // Blocks that keep their parent and their relative order stay put; of the
  // rest under each parent, only those off the longest kept run are moved.
  const moved = new Set<string>();
  for (const [parent, ids] of n.kids) {
    const stayed = ids.filter((id) => p.byId.has(id) && p.byId.get(id)!.parent === parent);
    for (const id of ids) {
      if (p.byId.has(id) && p.byId.get(id)!.parent !== parent) moved.add(id);
    }
    const prevOrder = p.kids.get(parent) ?? [];
    const keep = lisIndexes(stayed.map((id) => prevOrder.indexOf(id)));
    stayed.forEach((id, i) => {
      if (!keep.has(i)) moved.add(id);
    });
  }

  const ops: NoteOp[] = [];
  const isNew = (id: string) => !p.byId.has(id);
  const walk = (list: EditorBlock[], parent: string | null) => {
    list.forEach((b, i) => {
      const placement = (): Pick<NoteOp, "parent_id" | "after" | "before"> => {
        const after = i > 0 ? list[i - 1]!.id : null;
        if (after !== null) return { parent_id: parent, after };
        const anchor = list.slice(i + 1).find((s) => !isNew(s.id) && !moved.has(s.id));
        return anchor ? { parent_id: parent, before: anchor.id } : { parent_id: parent };
      };
      if (isNew(b.id)) {
        ops.push({ op: "insert", block_id: b.id, block: opBlock(b), ...placement() });
      } else {
        if (moved.has(b.id)) ops.push({ op: "move", block_id: b.id, ...placement() });
        const old = p.byId.get(b.id)!.block;
        if (
          old.type !== b.type ||
          !deepEqual(old.props, b.props) ||
          !deepEqual(old.content, b.content)
        ) {
          ops.push({ op: "update", block_id: b.id, block: updateBlock(old, b) });
        }
      }
      walk(b.children, b.id);
    });
  };
  walk(next, null);

  // Last block first: a section heading goes only after the notes under it
  // (the server keeps a heading that still has notes).
  const deletes: NoteOp[] = [];
  for (const [id, placed] of p.byId) {
    if (n.byId.has(id)) continue;
    if (placed.parent !== null && !n.byId.has(placed.parent)) continue; // its parent's delete takes it
    deletes.push({ op: "delete", block_id: id });
  }
  return [...ops, ...deletes.reverse()];
}

/** `tree` with each block id in `ids` replaced by its new id. */
export function rekeyTree(tree: EditorBlock[], ids: ReadonlyMap<string, string>): EditorBlock[] {
  return tree.map((b) => ({ ...b, id: ids.get(b.id) ?? b.id, children: rekeyTree(b.children, ids) }));
}

interface RekeyNode {
  type: { name: string };
  attrs: Record<string, unknown>;
}
interface RekeyView {
  state: {
    doc: { descendants: (f: (node: RekeyNode, pos: number) => boolean | void) => void };
    tr: {
      setNodeMarkup: (pos: number, type: undefined, attrs: Record<string, unknown>) => RekeyView["state"]["tr"];
      setMeta: (key: string, value: unknown) => RekeyView["state"]["tr"];
      docChanged: boolean;
    };
  };
  dispatch: (tr: RekeyView["state"]["tr"]) => void;
}

/** Gives the editor's blocks in `ids` their new ids in place: the content,
 * the caret and the undo history stay (the change is not an undo step). */
export function rekeyEditorBlocks(view: RekeyView, ids: ReadonlyMap<string, string>): void {
  let tr = view.state.tr;
  view.state.doc.descendants((node, pos) => {
    const id = node.attrs.id;
    if (node.type.name === "blockContainer" && typeof id === "string" && ids.has(id)) {
      tr = tr.setNodeMarkup(pos, undefined, { ...node.attrs, id: ids.get(id) });
    }
  });
  if (tr.docChanged) view.dispatch(tr.setMeta("addToHistory", false));
}
