/** A note's parts: dividers split a section into notes.
 *
 * The rule is the backend's (`paperhub.notes.papers.parts_of_section`): a
 * section's blocks run from its heading to the next section heading, in
 * document order; only `divider` blocks split them, the heading belongs to the
 * first part and a sub-heading stays inside its part. A part is named by the
 * block it follows: its section heading, or the divider above it.
 */
import { useNotesStore } from "@/store/notes";
import type { NoteBlock } from "@/types/notes";

/** What the rule reads of a block (a stored block or an editor block). */
export interface PartBlock {
  id: string;
  type: string;
  props?: Record<string, unknown>;
}

export interface Part<B extends PartBlock = PartBlock> {
  /** The block the part follows: its section heading, the divider above it,
   * or `START_PART` for the lines before a note's first section. */
  id: string;
  dividerId: string | null;
  /** The part's blocks (the section heading is in its first part; dividers
   * are in none). */
  blocks: B[];
}

/** The id of the lines before a note's first section heading. */
export const START_PART = "start";

/** The parts of the run of blocks that starts at `blocks[from]` and ends
 * before the next section heading. */
function splitRun<B extends PartBlock>(blocks: B[], from: number, lead: Part<B>): { parts: Part<B>[]; end: number } {
  const parts: Part<B>[] = [lead];
  let i = from;
  for (; i < blocks.length; i++) {
    const b = blocks[i]!;
    if (b.type === "section") break;
    if (b.type === "divider") parts.push({ id: b.id, dividerId: b.id, blocks: [] });
    else parts[parts.length - 1]!.blocks.push(b);
  }
  return { parts, end: i };
}

/** A section's parts (`blocks` in document order); empty if it is not there. */
export function partsOfSection<B extends PartBlock>(blocks: B[], sectionId: string): Part<B>[] {
  const idx = blocks.findIndex((b) => b.id === sectionId);
  if (idx < 0) return [];
  return splitRun(blocks, idx + 1, { id: sectionId, dividerId: null, blocks: [blocks[idx]!] }).parts;
}

/** Every part of a note, in order: the lines before its first section (when
 * there are any; dividers split them too), then each section's parts. */
export function partsOfNote<B extends PartBlock>(blocks: B[]): Part<B>[] {
  const head = splitRun(blocks, 0, { id: START_PART, dividerId: null, blocks: [] });
  const out = head.parts.filter((p) => p.id !== START_PART || p.blocks.length > 0);
  for (let i = head.end; i < blocks.length; ) {
    const run = splitRun(blocks, i + 1, { id: blocks[i]!.id, dividerId: null, blocks: [blocks[i]!] });
    out.push(...run.parts);
    i = run.end;
  }
  return out;
}

/** The part holding `blockId` (a divider leads the part below it). */
export function partOf<B extends PartBlock>(blocks: B[], blockId: string): Part<B> | null {
  return partsOfNote(blocks).find((p) => p.id === blockId || p.blocks.some((b) => b.id === blockId)) ?? null;
}

/** The id of the part holding `blockId`, from `blocks` or, when omitted,
 * from whichever loaded note holds the block. */
export function activePartFor(blockId: string, blocks?: PartBlock[]): string | null {
  if (blocks) return partOf(blocks, blockId)?.id ?? null;
  for (const state of Object.values(useNotesStore.getState().notes)) {
    if (state.blocks.some((b) => b.id === blockId)) return partOf(state.blocks, blockId)?.id ?? null;
  }
  return null;
}

function markIdOf(b: PartBlock): string | null {
  const id = b.props?.markId;
  return typeof id === "string" && id ? id : null;
}

/** The ids of the marks quoted in a part, once each, in order. */
export function marksInPart(part: Part): string[] {
  const out: string[] = [];
  for (const b of part.blocks) {
    const id = markIdOf(b);
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/** What a folded part's line says: its blocks (without the section heading)
 * and the marks it quotes. */
export function foldSummary(part: Part): { blocks: number; marks: number } {
  return {
    blocks: part.blocks.filter((b) => b.type !== "section").length,
    marks: marksInPart(part).length,
  };
}

/** A folded part's line, one translated sentence: "4 blocks · 2 marks", or
 * "4 blocks" when it quotes no mark. */
export function foldLine(
  t: (key: string, fallback: string, options: Record<string, unknown>) => string,
  summary: { blocks: number; marks: number },
): string {
  return t("notes:fold.summary", "{{count}} blocks", {
    count: summary.blocks,
    marks: summary.marks,
    context: summary.marks ? "marks" : undefined,
  });
}

// ---- Focus ----------------------------------------------------------------

type NotesState = ReturnType<typeof useNotesStore.getState>;

/** Per blocks array: part id -> its mark ids (stable arrays for selectors). */
const markCache = new WeakMap<NoteBlock[], Map<string, string[]>>();

/** The mark ids of the part in Focus, or null while Focus is off (or no part
 * is chosen). The same array is returned while the note is unchanged, so it
 * is safe as a zustand selector; the paper keeps colour on these marks only. */
export function focusedMarkIds(state: NotesState, noteId: number): string[] | null {
  if (!state.focusOn) return null;
  const active = state.activePart[noteId];
  const blocks = state.notes[noteId]?.blocks;
  if (!active || !blocks) return null;
  let byPart = markCache.get(blocks);
  if (!byPart) {
    byPart = new Map(partsOfNote(blocks).map((p) => [p.id, marksInPart(p)]));
    markCache.set(blocks, byPart);
  }
  return byPart.get(active) ?? null;
}

/** `focusedMarkIds` as a hook. */
export function useFocusedMarkIds(noteId: number): string[] | null {
  return useNotesStore((s) => focusedMarkIds(s, noteId));
}

// ---- Follow ---------------------------------------------------------------

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

/** The section block for a heading the reader is at: an exact title match,
 * else a loose one (pandoc headings may carry a number, keys may not). */
export function sectionForHeading<B extends PartBlock & { section_key?: string | null }>(
  blocks: B[],
  heading: string | null,
  within: string[] = [],
): B | null {
  // A subsection the note has no section for: the section it is in.
  if (heading) {
    for (const h of [heading, ...within.slice().reverse()]) {
      const found = sectionForOne(blocks, h);
      if (found) return found;
    }
  }
  return null;
}

function sectionForOne<B extends PartBlock & { section_key?: string | null }>(blocks: B[], heading: string): B | null {
  const target = norm(heading);
  if (!target) return null;
  const sections = blocks
    .filter((b) => b.type === "section")
    .map((b) => ({ b, key: norm(b.section_key ?? (typeof b.props?.title === "string" ? b.props.title : "")) }))
    .filter((s) => s.key);
  return (
    sections.find((s) => s.key === target)?.b ??
    sections.find((s) => s.key.includes(target) || target.includes(s.key))?.b ??
    null
  );
}
