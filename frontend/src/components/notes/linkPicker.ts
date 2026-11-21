/** The `[[` picker: what the typed query asks for and the targets it lists.
 *
 * As in Obsidian: `[[name` searches notes and library papers, `[[name#word`
 * that note's headings and sections (a paper's sections for a paper),
 * `[[#word` this note's headings, `[[##word` headings across all notes, and
 * `|text` the display text.
 */
import type { BlockNoteEditor } from "@blocknote/core";
import { SuggestionMenu } from "@blocknote/core/extensions";

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
  // A third [ typed after [[ stays in the query (it never re-opens the menu).
  const typed = q.replace(/^\[+/, "");
  const bar = typed.indexOf("|");
  const label = bar < 0 ? "" : typed.slice(bar + 1).trim();
  const rest = bar < 0 ? typed : typed.slice(0, bar);
  if (rest.startsWith("##")) return { mode: "allHeadings", name: "", heading: rest.slice(2).trim(), label };
  if (rest.startsWith("#")) return { mode: "thisNote", name: "", heading: rest.slice(1).trim(), label };
  // Up to the last #: a title may hold one (`C# notes#動`).
  const hash = rest.lastIndexOf("#");
  if (hash >= 0) {
    return { mode: "noteHeadings", name: rest.slice(0, hash).trim(), heading: rest.slice(hash + 1).trim(), label };
  }
  return { mode: "targets", name: rest.trim(), heading: "", label };
}

/** BlockNote's `shouldOpen` for the `[[` menu: not when the [[ would be the
 * tail of a longer bracket run (`[[[`), whose third [ belongs to the open
 * query, so a pick removes the whole run and leaves no stray bracket. */
export function opensLinkPicker(tr: { selection: { $from: { parent: { textBetween: (a: number, b: number, c?: string, d?: string) => string }; parentOffset: number } } }): boolean {
  const $from = tr.selection.$from;
  const at = $from.parentOffset;
  return $from.parent.textBetween(Math.max(0, at - 2), at, undefined, String.fromCharCode(0xfffc)) !== "[[";
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
  /** Picking the row completes the query to this (`<title>#…`) instead of
   * linking: the menu stays open on that target's headings. */
  complete?: string;
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

/** The row whose title is exactly `name` (case-folded), if any. */
const exact = <T extends { title: string }>(rows: T[], name: string): T | undefined =>
  rows.find((r) => fold(r.title) === fold(name));

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

/** Notes and library papers whose title contains `name`. */
async function targets(name: string, label: string, untitled: string): Promise<LinkItem[]> {
  const [notes, papers] = await Promise.all([
    quietly(() => searchNotes(name, LIMIT)),
    quietly<LibraryItem>(() => listLibrary(0, name || undefined, LIMIT)),
  ]);
  return [
    ...notes.map<LinkItem>((n) => ({
      group: "notes",
      icon: noteIcon(n.kind),
      title: n.title || untitled,
      props: props({ targetKind: "note", targetId: n.note_id, name: n.title || untitled, label }),
    })),
    ...papers.map<LinkItem>((p) => ({
      group: "papers",
      icon: "paper",
      title: p.title || untitled,
      subtext: p.year ? String(p.year) : undefined,
      props: props({ targetKind: "paper", targetId: p.paper_content_id, name: p.title || untitled, label }),
    })),
  ];
}

/** The picker's rows for the query typed after `[[`. Never rejects: a
 * search that fails leaves the others. `untitled` names a note without a
 * title (rows and the link's saved name alike). No "create a note" row
 * (H5b.2). */
export async function linkItems(editor: Ed, noteId: number, q: string, untitled = "Untitled"): Promise<LinkItem[]> {
  const { mode, name, heading, label } = parseLinkQuery(q);

  if (mode === "targets") return targets(name, label, untitled);

  if (mode === "allHeadings") {
    const hits = await quietly(() => searchNoteHeadings(heading, LIMIT * 2));
    return hits.map((h) => {
      const title = h.note_title || untitled;
      return {
        group: "headings",
        icon: "heading",
        title: h.text,
        subtext: title,
        props: props({ targetKind: "note", targetId: h.note_id, blockId: h.block_id, name: title + SEP + h.text, label }),
      };
    });
  }

  if (mode === "thisNote") {
    const st = useNotesStore.getState().notes[noteId];
    const title = st?.note.title || st?.defaultTitle || untitled;
    return ownOutline(editor)
      .filter((o) => o.text && has(o.text, heading))
      .map((o) => ({
        group: "headings",
        icon: o.type,
        title: o.text,
        props: props({ targetKind: "note", targetId: noteId, blockId: o.id, name: title + SEP + o.text, label }),
      }));
  }

  // name#word: the note titled exactly `name` (its outline) and the paper
  // titled so (its sections). A heading may hold # too (`Lang#C# basics`):
  // the splits are tried from the last # back, the first exact title wins.
  const text = q.replace(/^\[+/, "").split("|")[0]!.trim();
  for (const at of hashes(text)) {
    const found = await exactTarget(text.slice(0, at).trim(), untitled);
    if (found) return outlineRows(found, text.slice(at + 1).trim(), label);
  }
  // No exact title. As in Obsidian, the targets holding the text before #
  // come first: picking one completes the query to `<title>#` and shows
  // its headings. Targets holding the whole text (a title with a #) link
  // as they are.
  const [partial, asIs] = await Promise.all([targets(name, label, untitled), targets(text, label, untitled)]);
  const key = (i: LinkItem) => `${i.props.targetKind}:${i.props.targetId}`;
  const offered = new Set(partial.map(key));
  const tail = heading + (label ? `|${label}` : "");
  return [
    ...partial.map((i) => ({ ...i, complete: `${i.props.name}#${tail}` })),
    ...asIs.filter((i) => !offered.has(key(i))),
  ];
}

/** Positions of the #s that may end a title in `text`, last first (at most 3). */
function hashes(text: string): number[] {
  const out: number[] = [];
  for (let i = text.length - 1; i > 0 && out.length < 3; i--) if (text[i] === "#") out.push(i);
  return out;
}

interface Target {
  note?: { id: number; title: string };
  paper?: { id: number; title: string };
}

/** Targets reached by picking a completion row, by their shown name: an
 * untitled note completes to "Untitled#", which no title search finds. */
const completed = new Map<string, NoteLinkProps>();

/** Remembers a completion row's target, so its `<name>#` finds it again. */
export function rememberCompletion(item: LinkItem): void {
  completed.set(fold(item.props.name), item.props);
}

/** The note and the paper titled exactly `name` (case-folded), if any. */
async function exactTarget(name: string, untitled: string): Promise<Target | null> {
  if (!name) return null;
  const known = completed.get(fold(name));
  if (known) {
    const t = { id: known.targetId, title: known.name };
    return known.targetKind === "note" ? { note: t } : { paper: t };
  }
  const [notes, papers] = await Promise.all([
    quietly(() => searchNotes(name, LIMIT)),
    quietly<LibraryItem>(() => listLibrary(0, name || undefined, LIMIT)),
  ]);
  const note = exact(notes.map((n) => ({ id: n.note_id, title: n.title || untitled })), name);
  const paper = exact(papers.map((p) => ({ id: p.paper_content_id, title: p.title || untitled })), name);
  return note || paper ? { note, paper } : null;
}

/** A target's headings / sections holding `heading`, as link rows. */
async function outlineRows({ note, paper }: Target, heading: string, label: string): Promise<LinkItem[]> {
  const [outline, sections] = await Promise.all([
    note ? quietly(() => getNoteOutline(note.id)) : Promise.resolve([]),
    paper ? quietly(() => getPaperSections(paper.id)) : Promise.resolve([]),
  ]);
  return [
    ...outline
      .filter((o) => has(o.text, heading))
      .map<LinkItem>((o) => ({
        group: "headings",
        icon: o.type,
        title: o.text,
        subtext: note!.title,
        props: props({ targetKind: "note", targetId: note!.id, blockId: o.block_id, name: note!.title + SEP + o.text, label }),
      })),
    ...sections
      .filter((s) => has(s, heading))
      .map<LinkItem>((s) => ({
        group: "sections",
        icon: "section",
        title: s,
        subtext: paper!.title,
        props: props({ targetKind: "paper", targetId: paper!.id, sectionKey: s, name: paper!.title + SEP + s, label }),
      })),
  ];
}

/** Picks a completion row: its target is remembered and the menu opens
 * again on `<name>#…`. */
export function completeLinkQuery(editor: Ed, item: LinkItem): void {
  rememberCompletion(item);
  reopenLinkPicker(editor, item.complete ?? "");
}

/** Opens the `[[` menu again at the caret with `query` typed after it
 * (a pick has just removed `[[query` and closed the menu). */
export function reopenLinkPicker(editor: Ed, query: string): void {
  editor.getExtension(SuggestionMenu)?.openSuggestionMenu("[[", { deleteTriggerCharacter: true });
  editor.transact((tr) => tr.insertText(query));
}

/** Puts a link chip at the caret, then a space (as the `@` menu does). */
export function insertNoteLink(editor: Ed, p: NoteLinkProps): void {
  editor.insertInlineContent([{ type: "noteLink", props: p } as never, " "]);
}
