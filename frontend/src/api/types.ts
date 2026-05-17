/**
 * TypeScript discriminated union mirroring backend paperhub/api/sse.py.
 *
 * Hand-written for Phase A. Auto-generation via datamodel-code-generator
 * is a Phase B nice-to-have.
 */

// ---- Sub-models ----

export interface RoutingDecision {
  intent: "paper_qa" | "library_stats" | "research_suggest" | "slides" | "mcp_tool" | "chitchat";
  confidence: number;
  model_tier: "small" | "flagship";
  reasoning: string;
  fallback_to_user: boolean;
}

export interface ToolCall {
  run_id: string;
  step_index: number;
  parent_step: number | null;
  agent: string;
  tool: string;
  model: string | null;
  args_redacted: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  latency_ms: number;
  token_in: number | null;
  token_out: number | null;
  status: "ok" | "error" | "rejected";
  error: string | null;
}

// ---- SSE Events ----

export interface RoutingDecisionEvent {
  type: "routing_decision";
  data: RoutingDecision;
}

export interface ToolStepEvent {
  type: "tool_step";
  data: ToolCall;
}

export interface TokenEvent {
  type: "token";
  data: string;
}

export interface CitationEvent {
  type: "citation";
  chunk_id: string;
  section: string | null;
  page: number | null;
}

export interface FinalEvent {
  type: "final";
  run_id: string;
  answer: string;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

/** Discriminated union of all SSE event types. */
export type SseEvent =
  | RoutingDecisionEvent
  | ToolStepEvent
  | TokenEvent
  | CitationEvent
  | FinalEvent
  | ErrorEvent;
