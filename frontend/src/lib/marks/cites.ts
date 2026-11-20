/** Inline citations of marks in note blocks (H5b.1, [user, 2026-11-21]): a
 * mark is in its note as a reference block or as a `markCite` chip. */
import type { Mark, NoteBlock } from "@/types/notes";

/** Whether these runs (a block's content, or a link's) hold a citation of `markId`. */
export function runsCite(content: unknown, markId: string): boolean {
  if (!Array.isArray(content)) return false;
  return content.some((r: unknown) => {
    if (!r || typeof r !== "object") return false;
    const run = r as { type?: string; props?: { markId?: unknown }; content?: unknown };
    if (run.type === "markCite") return run.props?.markId === markId;
    return runsCite(run.content, markId);
  });
}

/** The block holding an inline citation of `markId` (null: none). */
export function citeBlockOf(blocks: readonly NoteBlock[], markId: string): string | null {
  return blocks.find((b) => runsCite(b.content, markId))?.id ?? null;
}

/** Where a mark is in its note: its reference block when there, else the
 * block holding its inline citation, else its block id (gone). */
export function noteBlockOfMark(blocks: readonly NoteBlock[] | undefined, mark: Pick<Mark, "id" | "block_id">): string {
  if (!blocks || blocks.some((b) => b.id === mark.block_id)) return mark.block_id;
  return citeBlockOf(blocks, mark.id) ?? mark.block_id;
}
