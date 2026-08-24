export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  status: string;
  runtime_state: string;
  active_index_id: string | null;
  building_index_id: string | null;
  rebuild_required: boolean;
  document_count: number;
  active_chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentItem {
  id: string;
  knowledge_id: string;
  filename: string;
  display_name: string;
  file_size: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface BuildRequestResult {
  requested: boolean;
  coalesced: boolean;
  index_id: string | null;
  task_id: string | null;
  rebuild_required: boolean;
}

export interface GarbageCollectionResult {
  deleted_index_ids: string[];
  deleted_count: number;
}

export interface DocumentDeleteResult {
  document_id: string;
  deleted: boolean;
  build_request: BuildRequestResult;
}

export interface KnowledgeIndex {
  id: string;
  kb_id: string;
  version: number;
  status: string;
  embedding_model_name: string;
  embedding_dimension: number;
  bm25_engine: string;
  document_count: number;
  element_count: number;
  chunk_count: number;
  build_reason: string;
  activate_on_success: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
  activated_at: string | null;
}

export interface IndexTask {
  id: string;
  status: string;
  stage: string;
  progress: number;
  attempt: number;
  index_id: string;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TraceSummary {
  trace_id: string;
  mode: "CHAT" | "PLAYGROUND";
  kb_id: string;
  index_id: string;
  question: string;
  status: string;
  user_id?: string | null;
  response_type?: string | null;
  answer_mode?: string | null;
  latency: Record<string, number>;
  error: Record<string, unknown>;
  created_at: string;
}

export interface RagTrace extends TraceSummary {
  retrieval_result: Record<string, unknown>;
  rerank_result: Record<string, unknown>;
  selected_context: Array<Record<string, unknown>>;
  prompt: string | null;
  answer: string | null;
  sources: Array<Record<string, unknown>>;
  citation_result: Record<string, unknown>;
  model_usage: Record<string, unknown>;
}

export interface ModelConfig {
  id: string;
  name: string;
  model_type: string;
  provider: string;
  base_url: string;
  model_name: string;
  enabled: boolean;
  api_key_configured: boolean;
  api_key_source?: "DATABASE" | "ENV" | null;
  parameters: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ModelProbeResult {
  ok: boolean;
  model_type: string;
  provider: string;
  model_name: string;
  latency_ms: number;
  detail: string;
  dimension?: number | null;
}

export interface RagConfig {
  vector_top_k: number;
  bm25_top_k: number;
  fusion_top_k: number;
  rerank_top_n: number;
  context_max_chunks: number;
  updated_at?: string | null;
}

export type ModelConfigUpdatePayload = {
  model_name?: string;
  base_url?: string;
  enabled?: boolean;
  parameters?: Record<string, unknown>;
};

export type RagConfigUpdatePayload = Partial<Omit<RagConfig, "updated_at">>;

export interface PromptTemplate {
  id: string;
  name: string;
  version: number;
  content: string;
  active: boolean;
  source?: "DATABASE" | "RUNTIME";
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ProblemProfile {
  id: string;
  knowledge_id: string;
  document_id: string;
  problem_code: string;
  canonical_question: string;
  aliases: string[];
  keywords: string[];
  product_name: string | null;
  model_names: string[];
  task_type: string | null;
  enabled: boolean;
  support_ready: boolean;
  readiness_reasons: string[];
  created_at: string;
  updated_at: string;
}

export type ProblemProfilePayload = Omit<
  ProblemProfile,
  "id" | "knowledge_id" | "support_ready" | "readiness_reasons" | "created_at" | "updated_at"
>;

export interface MetricsLatencySummary {
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  avg_ms: number;
}

export interface MetricsOverview {
  range: { from: string; to: string; mode: string };
  total_traces: number;
  completed: number;
  failed: number;
  cancelled: number;
  response_type_counts: Record<string, number>;
  classified_traces: number;
  no_answer_rate: number;
  doc_delivered_rate: number;
  failure_rate: number;
  citation_missing_rate: number;
  rerank_degraded_rate: number;
  unique_users: number;
  latency: MetricsLatencySummary;
  tokens: {
    llm_prompt: number;
    llm_completion: number;
    llm_total: number;
    rerank_total: number;
  };
}

export interface MetricsTimeseriesPoint {
  bucket: string;
  queries: number;
  failed: number;
  answered: number;
  unconfirmed: number;
  out_of_scope: number;
  doc_delivered: number;
  no_answer_rate: number;
  p95_ms: number;
  llm_tokens: number;
  rerank_tokens: number;
}

export interface MetricsLatency {
  range: { from: string; to: string; mode: string };
  total: MetricsLatencySummary;
  stages: Record<string, number>;
}

export interface SupportMetrics {
  range: { from: string; to: string; mode: string };
  support_turns: number;
  first_turns: number;
  followup_turns: number;
  document_delivered: number;
  clarification_required: number;
  clarification_resolved: number;
  no_match: number;
  escalation_required: number;
  canonical_or_alias_matches: number;
  llm_catalog_matches: number;
  general_guidance_turns: number;
  evidence_missing: number;
  high_risk_intercepts: number;
  structured_output_retries: number;
  workflow_conflicts: number;
  model_calls: number;
  document_delivery_rate: number;
  clarification_rate: number;
  clarification_resolution_rate: number;
  escalation_rate: number;
  general_guidance_rate: number;
  workflow_conflict_rate: number;
  first_turn_latency: MetricsLatencySummary;
  followup_latency: MetricsLatencySummary;
  tokens: { llm_prompt: number; llm_completion: number; llm_total: number };
}
