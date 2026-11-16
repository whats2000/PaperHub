/** Pure helpers over a note's flat block list: the local copy after a burst
 * of ops, and a block's plain text (the backend's `block_text` rule). */
import type { NoteBlock, NoteOp } from "@/types/notes";

function runsText(content: unknown): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((run: unknown) => {
      if (!run || typeof run !== "object") return "";
      const r = run as { text?: unknown; content?: unknown };
      return (typeof r.text === "string" ? r.text : "") + (r.content !== undefined ? runsText(r.content) : "");
    })
    .join("");
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

/** The local copy after `ops` (what the server will hold once they land).
 * Sibling order is what matters; a placed block goes beside its anchor. */
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
  return out;
}
