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
  | "paperRegion";

/** BlockNote inline content as stored: text runs, links, custom inline nodes. */
export type NoteContent = Record<string, unknown>[] | string | null;

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
}

export interface NoteDetail extends NoteWithBlocks {
  suggestions: NoteSuggestion[];
  comments: NoteComment[];
}

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
  reason: "changed_since_base" | "missing" | "invalid";
  current: NoteBlock | null;
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
}

export interface ChangeSetStatus {
  id: string;
  note_id: number;
  status: string;
  reapplied_by: string | null;
}

// ---- Reading marks ------------------------------------------------------

export type MarkKind = "highlight" | "quote" | "figure" | "equation" | "region";
export type MarkColor = "green" | "blue" | "pink" | "purple";

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
  [key: string]: unknown;
}

export interface Mark {
  id: string;
  paper_content_id: number;
  block_id: string;
  kind: MarkKind;
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
}

export interface MarkIn {
  paper_content_id: number;
  kind: MarkKind;
  color: MarkColor | null;
  view: "html" | "pdf";
  pieces: MarkPiece[];
  lang?: string | null;
  source_ref?: string | null;
  section_key: string | null;
  hint_block_id?: string | null;
  join_to?: string | null;
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
}

export interface MarkColorInfo {
  color: MarkColor;
  name: string;
  visible: boolean;
}

export interface MarkColorPatch {
  name?: string;
  visible?: boolean;
}

export interface PaperPageBlock {
  block_id: string;
  type: string;
  /** 0-based. */
  page: number;
  /** Page fractions x0, y0, x1, y1. */
  box: [number, number, number, number];
  text: string;
}
