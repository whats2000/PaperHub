export type Intent =
  | "paper_search"
  | "paper_qa"
  | "slides"
  | "library_stats"
  | "chitchat";

export type ModelTier = "small" | "flagship";
export type ToolStatus = "ok" | "error" | "rejected";
export type Branch = "" | "A" | "B";

export interface RoutingDecision {
  intent: Intent;
  model_tier: ModelTier;
  confidence: number;
  reasoning: string;
}

export interface ToolCallRecord {
  run_id: number;
  branch: Branch;
  step_index: number;
  parent_step: number | null;
  agent: string;
  tool: string;
  model: string | null;
  args_redacted_json: Record<string, unknown> | null;
  result_summary_json: Record<string, unknown> | null;
  latency_ms: number;
  token_in: number | null;
  token_out: number | null;
  status: ToolStatus;
  error: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  run_id: number | null;
  routing_decision?: RoutingDecision;
  trace?: ToolCallRecord[];
  status?: "streaming" | "ok" | "error";
  error?: string;
}

export interface ChatSession {
  id: number;
  title: string;
  messages: ChatMessage[];
  backend_session_id: number | null;
}
