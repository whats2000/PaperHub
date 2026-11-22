/** Notes and reading marks (Plan H5b.1) — mirrors `api/notes.py` and
 * `api/marks.py`. Block payloads are BlockNote-shaped (`props`, `content`). */

export type NoteTargetKind = "paper" | "session" | "idea";

export type NoteBlockType =
  | "paragraph"
  | "heading"
  | "bulletListItem"
  | "numberedListItem"
  | "checkListItem"
  | "quote"
  | "callout"
  | "codeBlock"
  | "math"
  | "divider"
  | "section"
  | "paperQuote"
  | "paperFigure"
  | "paperEquation"
  | "paperRegion"
  | "table"
  | "toc";

/** A table cell: plain text, inline runs, or a `tableCell` node (only
 * `textAlignment` may differ from the defaults, 1:1 with Markdown). */
export type TableCell =
  | string
  | Record<string, unknown>[]
  | { type: "tableCell"; props?: Record<string, unknown>; content: Record<string, unknown>[] | string };

/** A `table` block's content (BlockNote 0.57 `tableContent`). */
export interface TableContent {
  type: "tableContent";
  columnWidths: (number | null)[];
  headerRows?: number;
  headerCols?: number;
  rows: { cells: TableCell[] }[];
}

/** BlockNote inline content as stored: text runs, links, custom inline nodes;
 * a `table` block holds a `TableContent`. */
export type NoteContent = Record<string, unknown>[] | TableContent | string | null;

export interface NoteBlock {
  id: string;
  note_id: number;
  parent_id: string | null;
  type: NoteBlockType;
  props: Record<string, unknown>;
  content: NoteContent;
  text: string;
  author: "user" | "ai";
  section_key: string | null;
  changed_rev: number;
}

export interface Note {
  id: number;
  target_kind: NoteTargetKind;
  target_id: string;
  title: string;
  revision: number;
  updated_at: string;
  deleted_at?: string | null;
}

export interface NoteSuggestion {
  id: string;
  block_id: string;
  change_set_id: string;
  kind: string;
  proposed: Record<string, unknown> | null;
  status: string;
  created_at: string;
}

export type NoteCommentStatus = "open" | "resolved" | "deleted";

export interface NoteCommentItem {
  id: number;
  text: string;
  created_at: string;
}

/** A comment thread on a text range of a block. The anchor is the inline
 * `comment` style carrying `id`; the thread text is not note text. */
export interface NoteComment {
  id: string;
  note_id: number;
  block_id: string;
  quote: string;
  status: NoteCommentStatus;
  created_at: string;
  items: NoteCommentItem[];
}

export interface NoteWithBlocks {
  note: Note;
  blocks: NoteBlock[];
  /** How many live chats the note is linked to ("Shared by N chats"). */
  shared_by: number;
  /** The page title shown when `note.title` is empty: the target's current
   *  title ("" when that is empty too; the editor then says "Untitled"). */
  default_title?: string;
}

export interface NoteDetail extends NoteWithBlocks {
  suggestions: NoteSuggestion[];
  comments: NoteComment[];
}

/** A target's note as `GET /notes/target` answers: `note: null` while it
 *  has none (looking creates nothing; the first write creates it). */
export type NoteTargetDetail = Omit<NoteDetail, "note"> & { note: Note | null };

export interface NoteBlockIn {
  type: NoteBlockType;
  props: Record<string, unknown>;
  content?: NoteContent;
  section_key?: string | null;
}

export interface NoteOp {
  op: "insert" | "update" | "delete" | "move";
  block_id?: string | null;
  block?: NoteBlockIn | null;
  parent_id?: string | null;
  after?: string | null;
  before?: string | null;
  under_heading?: string | null;
  divider_before?: boolean;
}

export interface NoteOpsBody {
  base_revision: number;
  ops: NoteOp[];
  /** Names one burst across its retries; the server applies it once. */
  idempotency_key?: string;
}

export interface RejectedOp {
  block_id: string;
  /** `stale`: an accepted suggestion whose block changed since it was made
   * (it stays open, shown against the current text). `depends_on_failed`:
   * an op naming a block an earlier op of the call failed to make.
   * `follows_mark`: an undo of a change that only redrew a mark's blocks
   * (undo the recolour that caused it instead). */
  reason: "changed_since_base" | "missing" | "invalid" | "stale" | "depends_on_failed" | "follows_mark";
  current: NoteBlock | null;
  /** Why an `invalid` op was refused (e.g. an unknown mark colour). */
  hint?: string;
}

/** A mark's colour / style change a call wrote to the mark (the mark is the
 * one place they live; its blocks draw them). */
export interface MarkRedraw {
  mark_id: string;
  paper_id: number;
  /** The block whose change recoloured the mark. */
  block_id: string;
  color?: { from: string | null; to: string | null };
  style?: { from: string | null; to: string | null };
  /** The other blocks showing the mark, redrawn with it. */
  blocks: string[];
}

/** That change's redraw of the mark's copies in another note (a change set
 * there): an open editor of that note reloads. */
export interface NoteRedrawn {
  note_id: number;
  revision: number;
  change_set_id: string;
  blocks: string[];
}

export interface ApplyResult {
  note_id: number;
  revision: number;
  /** Null when nothing was written (every op rejected). */
  change_set_id: string | null;
  applied: string[];
  suggested: { block_id: string; suggestion_id: string }[];
  rejected: RejectedOp[];
  reparented: { block_id: string; parent_id: string | null }[];
  /** Open suggestions this call withdrew. */
  withdrawn?: string[];
  /** Inserts whose id was already used elsewhere (block ids are global; a
   * block pasted from another note keeps its id): the id sent and the one
   * the block got. The client re-keys the block. */
  renamed?: { from: string; to: string }[];
  /** Marks this call recoloured or restyled. */
  marks?: MarkRedraw[];
  /** Other notes those marks were redrawn in. */
  redrawn?: NoteRedrawn[];
}

/** One line of a change set's Details. */
export interface ChangeSetChange {
  kind: "added" | "updated" | "deleted" | "suggested";
  block_id: string;
  /** The block's text after the change (a suggestion: the proposal). */
  text: string;
  /** An update: the text before; a suggestion: the block's text now. */
  before?: string;
  /** A suggestion's op. */
  op?: "update" | "delete";
  /** A suggestion's status. */
  status?: "open" | "accepted" | "rejected" | "withdrawn";
}

export interface ChangeSetStatus {
  id: string;
  note_id: number;
  status: "applied" | "undone";
  reapplied_by: string | null;
  /** The set live now: Apply again makes a new one. */
  head: { id: string; status: "applied" | "undone" };
  note: { target_kind: NoteTargetKind; target_id: string; title: string };
  changes: ChangeSetChange[];
}

// ---- Reading marks ------------------------------------------------------

/** WHAT was marked. (Marks saved before 2026-11-19 said "highlight" /
 * "quote"; `markStyleOf` reads those as text in a style.) */
export type MarkKind = "text" | "figure" | "equation" | "region";
/** HOW a text mark is drawn. Figure / equation / region marks have none. */
export type MarkStyle = "highlight" | "underline" | "wavy" | "bar";
/** A palette key: the four defaults, or a custom colour's `c_…` key. */
export type MarkColor = string;

/** One anchored piece of a mark; shape depends on the view (see the plan). */
export interface MarkPiece {
  quote: string;
  page?: number;
  box?: [number, number, number, number];
  latex?: string;
  ocr?: boolean;
  lang?: string;
  orig?: string;
  lost?: boolean;
  /** Which save it came from (set by the server): pieces of one stroke read
   * as paragraphs, pieces of strokes joined later with “…”. */
  stroke?: number;
  [key: string]: unknown;
}

export interface Mark {
  id: string;
  paper_content_id: number;
  block_id: string;
  kind: MarkKind;
  style?: MarkStyle | null;
  color: MarkColor | null;
  view: "html" | "pdf";
  pieces: MarkPiece[];
  lang: string | null;
  source_ref: string | null;
  section_key: string | null;
  status: "ok" | "lost" | "partly_lost" | "translation_changed";
  created_at: string;
  deleted_at: string | null;
  note_id?: number;
  part_label?: string;
  /** In the note as an inline citation: the block holding it (null: a
   * reference block, or in no form). */
  cite_block_id?: string | null;
  /** What its reference block holds (Turn into reference block). */
  block_props?: Record<string, unknown>;
}

/** Where an inline citation goes: a block and an offset in its text (null:
 * its end); no block: a new paragraph at the note's end. */
export interface CiteAt {
  block_id: string | null;
  offset: number | null;
}

/** A short mark landed as an inline citation: its block, the offset just
 * after the chip, and whether that paragraph was made for it. */
export interface CitePlaced {
  block_id: string;
  offset: number;
  new_block: boolean;
}

/** `POST /marks/{id}/split`: the new mark, and the original as it now is. */
export type SplitResult = Mark & { original: Mark };

export interface MarkIn {
  paper_content_id: number;
  kind: MarkKind;
  style?: MarkStyle | null;
  color: MarkColor | null;
  view: "html" | "pdf";
  pieces: MarkPiece[];
  lang?: string | null;
  source_ref?: string | null;
  section_key: string | null;
  hint_block_id?: string | null;
  join_to?: string | null;
  /** A short text mark lands here as an inline citation (the server checks
   * the length too). */
  cite_at?: CiteAt | null;
}

export interface SavedTo {
  note_id: number;
  paper_title: string;
  section: string | null;
  part_index: number;
  part_count: number;
}

export interface MarkCreated {
  mark: Mark;
  block: NoteBlock | null;
  saved_to: SavedTo;
  cite?: CitePlaced | null;
}

export interface MarkColorInfo {
  color: MarkColor;
  /** The user's label: what the colour MEANS ("Method", "To verify"). */
  name: string;
  visible: boolean;
  /** Light-mode colour; `dark` is derived from it by the server. */
  hex?: string;
  dark?: string;
  /** The four defaults: hidden, renamed or recoloured, never deleted. */
  builtin?: boolean;
}

export interface MarkColorPatch {
  name?: string;
  visible?: boolean;
  hex?: string;
}

export interface PaperPageBlock {
  block_id: string;
  type: string;
  /** 0-based. */
  page: number;
  /** Page fractions x0, y0, x1, y1. */
  box: [number, number, number, number];
  text: string;
  /** The section the block sits in (where a mark on it files). */
  section: string | null;
}
