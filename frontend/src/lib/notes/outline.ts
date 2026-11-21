/** A note's outline as the editor holds it: headings 1-3 and `section`
 * blocks (level 1), in document order, children of a toggle heading
 * included. The backend's `GET /notes/{id}/outline` lists the same entries
 * for a stored note (it also keeps empty headings; the table of contents
 * leaves them out, there is nothing to read or click). */

/** The part of an editor block the outline reads. */
export interface EditorBlockLike {
  id: string;
  type: string;
  props?: Record<string, unknown>;
  content?: unknown;
  children?: EditorBlockLike[];
}

export interface OutlineEntry {
  blockId: string;
  text: string;
  level: 1 | 2 | 3;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");

function runsText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  let out = "";
  for (const run of content as Record<string, unknown>[]) {
    if (typeof run.text === "string") out += run.text;
    else if (run.type === "inlineMath") out += str((run.props as { latex?: unknown } | undefined)?.latex);
    else if (typeof (run.props as { label?: unknown } | undefined)?.label === "string") {
      out += (run.props as { label: string }).label;
    }
    if (Array.isArray(run.content)) out += runsText(run.content);
  }
  return out;
}

export function noteOutline(blocks: EditorBlockLike[]): OutlineEntry[] {
  const out: OutlineEntry[] = [];
  const walk = (list: EditorBlockLike[]) => {
    for (const b of list) {
      if (b.type === "section") {
        const text = (str(b.props?.title).trim() || str(b.props?.sectionKey).trim());
        if (text) out.push({ blockId: b.id, text, level: 1 });
      } else if (b.type === "heading") {
        const raw = b.props?.level;
        const level = typeof raw === "number" ? raw : 1;
        const text = runsText(b.content).trim();
        if (text && level >= 1 && level <= 3) out.push({ blockId: b.id, text, level: level as 1 | 2 | 3 });
      }
      if (b.children?.length) walk(b.children);
    }
  };
  walk(blocks);
  return out;
}

/** The part of a stored block (`NoteBlock`) the outline reads. */
export interface StoredBlockLike extends Omit<EditorBlockLike, "children"> {
  parent_id: string | null;
}

/** The outline of a note's stored blocks: rebuilt as a tree from
 * `parent_id`, so a nested heading lists under its parent whatever the
 * array's order; a block whose parent is missing comes after the tree (as
 * the server lists orphans). */
export function storedNoteOutline(blocks: StoredBlockLike[]): OutlineEntry[] {
  const ids = new Set(blocks.map((b) => b.id));
  const kids = new Map<string | null, StoredBlockLike[]>();
  const orphans: StoredBlockLike[] = [];
  for (const b of blocks) {
    if (b.parent_id !== null && !ids.has(b.parent_id)) {
      orphans.push(b);
      continue;
    }
    const list = kids.get(b.parent_id);
    if (list) list.push(b);
    else kids.set(b.parent_id, [b]);
  }
  const tree = (list: StoredBlockLike[]): EditorBlockLike[] =>
    list.map((b) => ({ ...b, children: tree(kids.get(b.id) ?? []) }));
  return noteOutline([...tree(kids.get(null) ?? []), ...tree(orphans)]);
}
