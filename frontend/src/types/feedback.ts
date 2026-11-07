// Plan H2 feedback contract — mirrors backend/src/paperhub/feedback/models.py.

export const REASONS = [
  "wrong_route",
  "wrong_source",
  "citation",
  "factual",
  "off_topic",
  "language_format",
] as const;
export const STAGES = ["router", "retrieval", "citation", "answer"] as const;

export type Reason = (typeof REASONS)[number];
export type Stage = (typeof STAGES)[number];
export type Polarity = "up" | "down";

export const STAGE_OF_REASON: Record<Reason, Stage> = {
  wrong_route: "router",
  wrong_source: "retrieval",
  citation: "citation",
  factual: "answer",
  off_topic: "answer",
  language_format: "answer",
};

export interface FeedbackPut {
  polarity: Polarity | null;
  reasons: string[];
  other_text?: string | null;
  correction?: string | null;
}
export interface FeedbackGuess {
  reasons: string[];
}
export interface RunFeedback {
  run_id: number;
  polarity: Polarity;
  reasons: string[];
  other_text?: string | null;
  correction?: string | null;
}
export interface QueueTurn {
  role: string;
  content: string;
}
export interface QueueItem {
  feedback_id: number;
  run_id: number;
  session_id: number;
  session_title: string;
  papers: string[];
  earlier: QueueTurn[];
  question: string;
  answer: string;
  /** The flagged follow-up user message; "" when there is none (a bare fork). */
  flagged: string;
  next_reply: string | null;
  reviewer_doubt: string;
  guessed_reasons: string[];
}
export interface QueueAnswer {
  feedback_id: number;
  confirmed: boolean;
  reasons: string[];
}
export interface QueueSubmit {
  answers: QueueAnswer[];
}
export interface QueueSubmitResult {
  confirmed: number;
  rejected: number;
}
export interface Kpis {
  answers_with_feedback: number;
  /** Share of explicit feedback that is thumbs-up (0–1); null with none. */
  approval_rate: number | null;
  negatives_explicit: number;
  negatives_implicit: number;
  pending: number;
}
export interface StageSummary {
  stage: Stage;
  explicit: number;
  implicit: number;
  weekly: [number, number, number, number];
  delta: number;
}
export interface OtherReason {
  text: string;
  count: number;
}
export interface FeedbackSummary {
  kpis: Kpis;
  stages: StageSummary[];
  other_reasons: OtherReason[];
}
export interface FeedbackCase {
  feedback_id: number;
  run_id: number;
  session_id: number;
  question: string;
  kind: "explicit" | "implicit";
  created_at: string;
}
export interface PresetCreate {
  label: string;
  stage: Stage;
}
export interface Preset {
  label: string;
  stage: Stage;
}
export interface G1InboxRequest {
  feedback_ids: number[];
}
export interface G1InboxResult {
  appended: number;
}
export interface ExportPreview {
  sft: number;
  dpo: number;
  includes_private: boolean;
}
export interface ExportRequest {
  include_implicit?: boolean;
  include_private?: boolean;
  since?: string | null;
  until?: string | null;
  model?: string | null;
  cited_only: boolean;
}
export interface ExportResult {
  dir: string;
  files: string[];
  sft: number;
  dpo: number;
}
