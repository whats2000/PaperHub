// Plan H3 ideation contract — mirrors backend/src/paperhub/ideation/models.py.

/** The one citable "section" of a document without section headings (a short
 *  letter): it cites the whole body. Mirrors `FULL_TEXT_SECTION` in
 *  backend/src/paperhub/agents/sl_read.py; shown through `sectionLabel`. */
export const FULL_TEXT_SECTION = "Full text";

export const IDEA_STATUSES = [
  "new",
  "saved",
  "trying",
  "worked",
  "failed",
  "dismissed",
] as const;
export const DISMISS_REASONS = [
  "already_tried",
  "not_feasible",
  "analogy_fails",
  "not_novel",
  "other",
] as const;

export type IdeaStatus = (typeof IDEA_STATUSES)[number];
export type DismissReason = (typeof DISMISS_REASONS)[number];
export type ProblemStatus = "generating" | "ready" | "error";

export interface CitedField {
  text: string;
  sections: string[];
}

export interface MechanismCard {
  problem: CitedField;
  mechanism: CitedField;
  assumptions: CitedField[];
  limitations: CitedField[];
  domains: string[];
}

export interface Idea {
  id: number;
  problem_id: number | null;
  source_doc: number;
  source_section: string;
  source_chunk_ids: number[];
  target_doc: number | null;
  target_section: string | null;
  target_chunk_ids: number[];
  title: string;
  rationale: string;
  status: IdeaStatus;
  prev_status: IdeaStatus | null;
  note: string;
  dismiss_reasons: string[];
  session_ids: number[];
  created_at: string;
}

/** Card-phase progress of an in-flight run, and the latest run's skipped
 * documents (no mechanism card). `cards_ready`/`cards_total` are null when no
 * run is in flight; `cards_total` 0 = waiting for another run's cards. */
export interface CardProgress {
  cards_ready?: number | null;
  cards_total?: number | null;
  skipped_docs?: number;
}

export interface Problem extends CardProgress {
  id: number;
  text: string;
  tried: string;
  created_at: string;
  idea_count: number;
  status: ProblemStatus;
  /** The run finished and found nothing in the library relevant to this problem,
   *  with the proposer's one-sentence reason. */
  nothing_relevant?: boolean;
  nothing_relevant_reason?: string;
}

export interface GraphDocument {
  id: number;
  title: string;
  domains: number[];
  idea_count: number;
  is_private: boolean;
}

export interface GraphDomain {
  id: number;
  name: string;
  color_index: number | null; // null = gray "Other"
}

export interface GraphCitation {
  source_id: number;
  target_id: number;
}

export interface GraphResponse {
  documents: GraphDocument[];
  domains: GraphDomain[];
  citations: GraphCitation[];
  ideas: Idea[];
  problem: Problem | null;
  /** The whole library (every scope); `documents` is only the scope's. */
  library_count: number;
  private_count: number;
}

export interface ProblemCreate {
  text: string;
  tried: string;
}

export interface IdeaPatch {
  status?: IdeaStatus;
  note?: string;
  dismiss_reasons?: string[];
  dismiss_note?: string;
}

export interface IdeaChatResponse {
  session_id: number;
  title: string;
  prefill: string;
  paper_content_ids: number[];
}

export interface ExploreResponse {
  started: boolean;
}

/** `GET /ideation/explore/status`: an explore run in flight + the latest run's
 * outcome. `last_new_count` 0 = the re-run kept no new ideas (Ruling U3). */
export interface ExploreStatus extends CardProgress {
  running: boolean;
  nothing_relevant?: boolean;
  nothing_relevant_reason?: string;
  last_run_at: string | null;
  last_new_count: number | null;
  last_error: string | null;
}

export interface DocumentDomainsPut {
  domain_ids: number[];
  new_names: string[];
}

export interface DocumentOut {
  id: number;
  title: string;
  domains: number[];
  is_private: boolean;
}

export interface DomainPatch {
  name?: string;
  merge_into?: number;
}

export interface FeedbackToG1Response {
  appended: number;
}

/** GET /ideation/feedback-summary; `worked_rate` is null with no outcomes yet. */
export interface IdeationFeedbackSummary {
  save_rate: number;
  worked_rate: number | null;
  dismiss_reasons: { reason: string; count: number }[];
}

/** A legend category under the pointer: the graph lights what it names and dims the rest. */
export type LegendHighlight =
  | { kind: "domains"; ids: number[] }
  | { kind: "citations" }
  | { kind: "ideas" }
  | { kind: "status"; statuses: IdeaStatus[] }
  | { kind: "private" };
