/** Pure helpers over a note's flat block list: the local copy after a burst
 * of ops, and a block's plain text (the backend's `block_text` rule). */
import type { NoteBlock, NoteOp } from "@/types/notes";

/** A run's own text (the backend's `_runs_text`): text, an inline
 * equation's LaTeX, an inline citation's alias, a `[[` link's label or
 * else its target's saved name. */
function nodeText(r: { type?: unknown; text?: unknown; props?: Record<string, unknown> }): string {
  if (typeof r.text === "string") return r.text;
  const p = r.props ?? {};
  if (r.type === "inlineMath") return propText(p.latex);
  if (r.type === "markCite") return propText(p.text);
  if (r.type === "noteLink") return propText(p.label) || propText(p.name);
  return "";
}

function runsText(content: unknown): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((run: unknown) => {
      if (!run || typeof run !== "object") return "";
      const r = run as { type?: unknown; text?: unknown; props?: Record<string, unknown>; content?: unknown };
      return nodeText(r) + (r.content !== undefined && r.content !== null ? runsText(r.content) : "");
    })
    .join("");
}

/** A table cell's text (its runs). */
export function cellText(cell: unknown): string {
  if (cell && typeof cell === "object" && !Array.isArray(cell)) {
    return runsText((cell as { content?: unknown }).content);
  }
  return runsText(cell);
}

function tableText(content: unknown): string {
  const rows = (content as { rows?: unknown } | null)?.rows;
  if (!Array.isArray(rows)) return "";
  return rows
    .map((row: { cells?: unknown[] }) => (row.cells ?? []).map(cellText).join(" | "))
    .join("\n");
}

/** A prop as text ("" for anything that is not a string or number). */
export function propText(v: unknown): string {
  return typeof v === "string" ? v : typeof v === "number" ? String(v) : "";
}

/** Plain text of a block, the same rule as the backend's `block_text`. */
export function blockText(type: string, props: Record<string, unknown>, content: unknown): string {
  if (type === "section") return propText(props.title);
  if (type === "paperQuote") return propText(props.quote);
  if (type === "math") return propText(props.latex);
  if (type === "table") {
    // The caption first, the notes last (the backend's `table_block_text`).
    return [propText(props.caption), tableText(content), propText(props.notes)].filter(Boolean).join("\n");
  }
  if (type === "codeBlock") return runsText(content) || propText(props.code);
  return runsText(content);
}

function place(
  blocks: NoteBlock[],
  b: NoteBlock,
  op: NoteOp,
): NoteBlock[] {
  const anchorId = op.after ?? op.before ?? null;
  const at = anchorId !== null ? blocks.findIndex((x) => x.id === anchorId) : -1;
  if (at < 0) {
    return [...blocks, { ...b, parent_id: op.parent_id ?? null }];
  }
  const parent = blocks[at]!.parent_id;
  const i = op.after ? at + 1 : at;
  return [...blocks.slice(0, i), { ...b, parent_id: parent }, ...blocks.slice(i)];
}

export function subtree(blocks: NoteBlock[], id: string): Set<string> {
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const b of blocks) {
      if (b.parent_id !== null && out.has(b.parent_id) && !out.has(b.id)) {
        out.add(b.id);
        grew = true;
      }
    }
  }
  return out;
}

/** `blocks` in document order (depth-first, siblings in their list order),
 * as the server lists them; a block whose parent is missing comes after the
 * tree, like the server's orphans. */
function depthFirst(blocks: NoteBlock[]): NoteBlock[] {
  const ids = new Set(blocks.map((b) => b.id));
  const kids = new Map<string, NoteBlock[]>();
  const roots: NoteBlock[] = [];
  const orphans: NoteBlock[] = [];
  for (const b of blocks) {
    if (b.parent_id === null) roots.push(b);
    else if (!ids.has(b.parent_id)) orphans.push(b);
    else {
      const list = kids.get(b.parent_id);
      if (list) list.push(b);
      else kids.set(b.parent_id, [b]);
    }
  }
  const out: NoteBlock[] = [];
  const walk = (list: NoteBlock[]) => {
    for (const b of list) {
      out.push(b);
      walk(kids.get(b.id) ?? []);
    }
  };
  walk(roots);
  walk(orphans);
  return out;
}

/** The local copy after `ops` (what the server will hold once they land), in
 * document order: a placed block goes beside its anchor among its siblings,
 * and a moved block takes its children with it. */
export function applyOpsLocally(noteId: number, blocks: NoteBlock[], ops: NoteOp[]): NoteBlock[] {
  let out = blocks;
  for (const op of ops) {
    const id = op.block_id ?? "";
    if (op.op === "insert" && op.block) {
      const { type, props, content = null, section_key = null } = op.block;
      // An insert naming a block we already hold (a burst the server saved
      // but never confirmed, replayed on a reload) replaces it.
      out = out.filter((b) => b.id !== id);
      out = place(out, {
        id,
        note_id: noteId,
        parent_id: null,
        type,
        props,
        content,
        text: blockText(type, props, content),
        author: "user",
        section_key,
        changed_rev: 0,
      }, op);
    } else if (op.op === "update" && op.block) {
      const { type, props, content = null, section_key } = op.block;
      out = out.map((b) =>
        b.id === id
          ? {
              ...b,
              type,
              props,
              content,
              text: blockText(type, props, content),
              author: "user" as const,
              section_key: section_key ?? b.section_key,
            }
          : b,
      );
    } else if (op.op === "move") {
      const cur = out.find((b) => b.id === id);
      if (cur) out = place(out.filter((b) => b.id !== id), cur, op);
    } else if (op.op === "delete") {
      const gone = subtree(out, id);
      out = out.filter((b) => !gone.has(b.id));
    }
  }
  return depthFirst(out);
}
