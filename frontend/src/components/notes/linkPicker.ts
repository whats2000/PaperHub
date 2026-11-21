/** The `[[` picker: what the typed query asks for and the targets it lists.
 *
 * As in Obsidian: `[[name` searches notes and library papers, `[[name#word`
 * that note's headings and sections (a paper's sections for a paper),
 * `[[#word` this note's headings, `[[##word` headings across all notes, and
 * `|text` the display text.
 */
import type { BlockNoteEditor } from "@blocknote/core";

import {
  getNoteOutline,
  getPaperSections,
  listLibrary,
  searchNoteHeadings,
  searchNotes,
  type NoteSearchHit,
} from "@/lib/api";
import { useNotesStore } from "@/store/notes";
import type { LibraryItem } from "@/types/domain";

type Ed = BlockNoteEditor<any, any, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface LinkQuery {
  mode: "targets" | "noteHeadings" | "thisNote" | "allHeadings";
  name: string;
  heading: string;
  label: string;
}

export function parseLinkQuery(q: string): LinkQuery {
  const bar = q.indexOf("|");
  const label = bar < 0 ? "" : q.slice(bar + 1).trim();
  const rest = bar < 0 ? q : q.slice(0, bar);
  if (rest.startsWith("##")) return { mode: "allHeadings", name: "", heading: rest.slice(2).trim(), label };
  if (rest.startsWith("#")) return { mode: "thisNote", name: "", heading: rest.slice(1).trim(), label };
  const hash = rest.indexOf("#");
  if (hash >= 0) {
    return { mode: "noteHeadings", name: rest.slice(0, hash).trim(), heading: rest.slice(hash + 1).trim(), label };
  }
  return { mode: "targets", name: rest.trim(), heading: "", label };
}

/** A `noteLink` node's props (see `blocks/NoteLink`). */
export interface NoteLinkProps {
  targetKind: "note" | "paper";
  targetId: number;
  blockId: string;
  sectionKey: string;
  label: string;
  /** The full display text when inserted: title (+ " › " heading / section). */
  name: string;
}

export interface LinkItem {
  group: "notes" | "papers" | "headings" | "sections";
  icon: "paperNote" | "chatNote" | "ideaNote" | "paper" | "heading" | "section";
  title: string;
  subtext?: string;
  props: NoteLinkProps;
}

const LIMIT = 8;
const SEP = " › ";
const fold = (s: string) => s.trim().toLowerCase();
const has = (text: string, word: string) => !word || fold(text).includes(fold(word));

const noteIcon = (kind: NoteSearchHit["kind"]): LinkItem["icon"] =>
  kind === "session" ? "chatNote" : kind === "idea" ? "ideaNote" : "paperNote";

async function quietly<T>(p: () => Promise<T[]>): Promise<T[]> {
  try {
    return await p();
  } catch {
    return [];
  }
}

const pick = <T extends { title: string }>(rows: T[], name: string): T | undefined =>
  rows.find((r) => fold(r.title) === fold(name)) ?? rows[0];

/** A block's inline text (chips by their shown text). */
function inlineText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((c: { type?: string; text?: string; props?: { label?: string; name?: string } }) =>
      c.type === "text" || c.type === "link"
        ? (c.text ?? inlineText((c as { content?: unknown }).content))
        : c.props?.label || c.props?.name || "",
    )
    .join("");
}

/** The headings and sections of the note being edited, as the editor has them. */
function ownOutline(editor: Ed): { id: string; type: "heading" | "section"; text: string }[] {
  const out: { id: string; type: "heading" | "section"; text: string }[] = [];
  const walk = (blocks: readonly { id: string; type: string; props: object; content?: unknown; children: unknown }[]) => {
    for (const b of blocks) {
      if (b.type === "heading") out.push({ id: b.id, type: "heading", text: inlineText(b.content) });
      else if (b.type === "section") {
        const p = b.props as { title?: string; sectionKey?: string };
        out.push({ id: b.id, type: "section", text: p.title || p.sectionKey || "" });
      }
      walk(b.children as typeof blocks);
    }
  };
  walk(editor.document as never);
  return out;
}

const props = (over: Partial<NoteLinkProps> & Pick<NoteLinkProps, "targetKind" | "targetId" | "name">) => ({
  blockId: "",
  sectionKey: "",
  label: "",
  ...over,
});

/** The picker's rows for the query typed after `[[`. Never rejects: a
 * search that fails leaves the others. No "create a note" row (H5b.2). */
export async function linkItems(editor: Ed, noteId: number, q: string): Promise<LinkItem[]> {
  const { mode, name, heading, label } = parseLinkQuery(q);

  if (mode === "targets") {
    const [notes, papers] = await Promise.all([
      quietly(() => searchNotes(name, LIMIT)),
      quietly<LibraryItem>(() => listLibrary(0, name || undefined, LIMIT)),
    ]);
    return [
      ...notes.map<LinkItem>((n) => ({
        group: "notes",
        icon: noteIcon(n.kind),
        title: n.title,
        props: props({ targetKind: "note", targetId: n.note_id, name: n.title, label }),
      })),
      ...papers.map<LinkItem>((p) => ({
        group: "papers",
        icon: "paper",
        title: p.title,
        subtext: p.year ? String(p.year) : undefined,
        props: props({ targetKind: "paper", targetId: p.paper_content_id, name: p.title, label }),
      })),
    ];
  }

  if (mode === "allHeadings") {
    const hits = await quietly(() => searchNoteHeadings(heading, LIMIT * 2));
    return hits.map((h) => ({
      group: "headings",
      icon: "heading",
      title: h.text,
      subtext: h.note_title,
      props: props({
        targetKind: "note",
        targetId: h.note_id,
        blockId: h.block_id,
        name: h.note_title + SEP + h.text,
        label,
      }),
    }));
  }

  if (mode === "thisNote") {
    const st = useNotesStore.getState().notes[noteId];
    const title = st?.note.title || st?.defaultTitle || "";
    return ownOutline(editor)
      .filter((o) => o.text && has(o.text, heading))
      .map((o) => ({
        group: "headings",
        icon: o.type,
        title: o.text,
        props: props({ targetKind: "note", targetId: noteId, blockId: o.id, name: title ? title + SEP + o.text : o.text, label }),
      }));
  }

  // name#word: the named note's outline and the named paper's sections.
  const [notes, papers] = await Promise.all([
    quietly(() => searchNotes(name, LIMIT)),
    quietly<LibraryItem>(() => listLibrary(0, name || undefined, LIMIT)),
  ]);
  const note = pick(notes, name);
  const paper = pick(papers, name);
  const [outline, sections] = await Promise.all([
    note ? quietly(() => getNoteOutline(note.note_id)) : Promise.resolve([]),
    paper ? quietly(() => getPaperSections(paper.paper_content_id)) : Promise.resolve([]),
  ]);
  return [
    ...outline
      .filter((o) => has(o.text, heading))
      .map<LinkItem>((o) => ({
        group: "headings",
        icon: o.type,
        title: o.text,
        subtext: note!.title,
        props: props({
          targetKind: "note",
          targetId: note!.note_id,
          blockId: o.block_id,
          name: note!.title + SEP + o.text,
          label,
        }),
      })),
    ...sections
      .filter((s) => has(s, heading))
      .map<LinkItem>((s) => ({
        group: "sections",
        icon: "section",
        title: s,
        subtext: paper!.title,
        props: props({
          targetKind: "paper",
          targetId: paper!.paper_content_id,
          sectionKey: s,
          name: paper!.title + SEP + s,
          label,
        }),
      })),
  ];
}

/** Puts a link chip at the caret, then a space (as the `@` menu does). */
export function insertNoteLink(editor: Ed, p: NoteLinkProps): void {
  editor.insertInlineContent([{ type: "noteLink", props: p } as never, " "]);
}
