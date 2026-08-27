export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  status: string;
  runtime_state: string;
  active_index_id: string | null;
  rag_active_index_id?: string | null;
  support_active_index_id?: string | null;
  support_active_release_id?: string | null;
  building_index_id: string | null;
  rebuild_required: boolean;
  document_count: number;
  active_chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface SystemInfo {
  name: string;
  environment: string;
  version: string;
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

export interface TaskSubmission {
  task_id: string;
  task_type: string;
  status: "QUEUED" | "RUNNING";
  replayed: boolean;
}

export interface GarbageCollectionResult {
  deleted_index_ids: string[];
  deleted_count: number;
  skipped_index_ids?: string[];
  skipped_count?: number;
  purged_conversation_ids?: string[];
  purged_conversation_count?: number;
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
  task_type?: string;
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

export interface TaskRecord {
  id: string;
  task_type: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  stage: string;
  progress: number;
  attempt: number;
  kb_id: string | null;
  index_id: string | null;
  document_id: string | null;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface TraceSummary {
  trace_id: string;
  request_id?: string;
  mode: "CHAT" | "PLAYGROUND";
  kb_id: string;
  index_id: string;
  conversation_id?: string | null;
  question: string;
  status: string;
  user_id?: string | null;
  response_type?: string | null;
  answer_mode?: string | null;
  latency: Record<string, number>;
  error: Record<string, unknown>;
  created_at: string;
  engine?: "SUPPORT" | "RAG";
  stage?: string | null;
  parent_trace_id?: string | null;
  fallback_from?: "SUPPORT" | "RAG" | null;
  fallback_reason?: string | null;
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

export type ExecutionPolicy = "SUPPORT_ONLY" | "RAG_ONLY" | "SUPPORT_WITH_RAG_FALLBACK";

export interface EngineRetrievalConfig extends RagConfig {
  execution_policy: ExecutionPolicy;
}

export interface RuntimePolicy {
  knowledge_id: string;
  execution_policy: ExecutionPolicy;
  config_version: number;
  fallback_conditions: string[];
  source: "DATABASE" | "DEFAULT";
  updated_at?: string | null;
  readiness: {
    support: {
      ready: boolean;
      blockers: string[];
      profile_count: number;
      ready_profile_count: number;
    };
    rag: { ready: boolean; blockers: string[] };
  };
}

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
  routing_summary: string;
  symptoms: string[];
  exclusions: string[];
  profile_enrichment_version: number;
  enabled: boolean;
  support_ready: boolean;
  readiness_reasons: string[];
  created_at: string;
  updated_at: string;
}

export type ProfileDraftOperation = "CREATE" | "UPDATE" | "REMOVE" | "NONE";
export type ProfileDraftStatus = "DRAFT" | "APPROVED" | "REJECTED" | "PUBLISHED";
export type ProfileQualityStatus = "PASS" | "REVIEW_REQUIRED" | "FALLBACK" | "MANUAL_REVIEWED";

export interface PublishedProfileSummary {
  id: string;
  problem_code: string;
  canonical_question: string;
  aliases: string[];
  keywords: string[];
  product_name: string | null;
  model_names: string[];
  task_type: string | null;
  routing_summary: string;
  symptoms: string[];
  exclusions: string[];
  enabled: boolean;
  profile_enrichment_version: number;
  updated_at: string | null;
}

export interface CandidateProfileDraftSummary {
  id: string;
  release_id: string;
  index_id: string;
  index_version: number;
  release_status: string;
  status: ProfileDraftStatus;
  operation: ProfileDraftOperation;
  target_profile_id: string | null;
  problem_code: string;
  canonical_question: string;
  aliases: string[];
  keywords: string[];
  product_name: string | null;
  model_names: string[];
  task_type: string | null;
  routing_summary: string;
  enabled: boolean;
  quality_status: ProfileQualityStatus;
  quality_notes: string[];
  generation_source: "LLM" | "FALLBACK" | "MANUAL" | null;
  updated_at: string | null;
}

export interface DocumentProfileContext {
  document_id: string;
  published_profile: PublishedProfileSummary | null;
  candidate_draft: CandidateProfileDraftSummary | null;
  profile_status?: DocumentProfileStatus;
  active_profile?: DocumentProfilePayload | null;
  pending_profile?: DocumentProfilePayload | null;
  pending_state?: Exclude<DocumentProfileStatus, "ONLINE" | "NOT_GENERATED" | "GENERATING" | "DISABLED"> | null;
  pending_error_code?: string | null;
  pending_error_message?: string | null;
}

export type DocumentProfileStatus =
  | "NOT_GENERATED"
  | "GENERATING"
  | "PENDING_ONLINE"
  | "ONLINE"
  | "NEEDS_ATTENTION"
  | "DISABLED"
  | "FAILED";

export interface DocumentProfileIntent {
  problem_code: string;
  canonical_question: string;
  aliases: string[];
  keywords: string[];
  product_name?: string | null;
  model_names?: string[];
  task_type?: string | null;
  routing_summary?: string;
  symptoms?: string[];
  exclusions?: string[];
  enabled?: boolean;
  confidence?: number;
}

export interface DocumentProfilePayload {
  intents: DocumentProfileIntent[];
}

export interface DocumentProfileSummary {
  document_id: string;
  document_name: string;
  document_status: string;
  profile_status: DocumentProfileStatus;
  active_profile: DocumentProfilePayload | null;
  pending_profile: DocumentProfilePayload | null;
  pending_state: string | null;
  pending_error_code: string | null;
  pending_error_message: string | null;
  active_updated_at: string | null;
  pending_updated_at: string | null;
}

export interface KnowledgeProfileSummary {
  knowledge_id: string;
  online_count: number;
  pending_count: number;
  attention_count: number;
  failed_count: number;
  generating_count: number;
  profiles: DocumentProfileSummary[];
}

export interface ProfilePublishCheck {
  valid: boolean;
  publishable: boolean;
  pending_count: number;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface ValidationIssue {
  code: string;
  message: string;
}

export interface ReleaseValidation {
  valid?: boolean;
  publishable?: boolean;
  errors?: ValidationIssue[];
  warnings?: ValidationIssue[];
  review_required_count?: number;
  review_required?: Array<{
    id: string;
    document_name: string;
    status: string;
    quality_status: string;
  }>;
  checked_draft_count?: number;
}

export interface ProfileDraft {
  id: string;
  release_id: string;
  kb_id: string;
  document_id: string;
  document_name: string;
  operation: ProfileDraftOperation;
  target_profile_id: string | null;
  target_problem_code?: string | null;
  problem_code: string;
  canonical_question: string;
  aliases: string[];
  keywords: string[];
  product_name: string | null;
  model_names: string[];
  task_type: string | null;
  routing_summary: string;
  symptoms: string[];
  exclusions: string[];
  profile_enrichment_version: number;
  enabled: boolean;
  status: ProfileDraftStatus;
  generation_source: "LLM" | "FALLBACK" | "MANUAL";
  quality_status: "PASS" | "REVIEW_REQUIRED" | "FALLBACK" | "MANUAL_REVIEWED";
  quality_notes: string[];
  quality_evidence: Array<{ field: string; excerpt: string; element_hint?: string | null }>;
  confidence: number;
  reasoning: string | null;
  validation_result: ReleaseValidation;
  created_at: string;
  updated_at: string;
}

export type ProfileDraftPatch = Partial<
  Pick<
    ProfileDraft,
    | "operation"
    | "target_profile_id"
    | "problem_code"
    | "canonical_question"
    | "aliases"
    | "keywords"
    | "product_name"
    | "model_names"
    | "task_type"
    | "routing_summary"
    | "symptoms"
    | "exclusions"
    | "enabled"
    | "status"
  >
>;

export interface KnowledgeRelease {
  id: string;
  kb_id: string;
  index_id: string;
  index_version: number;
  index_status: string;
  status: string;
  validation_result: ReleaseValidation;
  generation_error: string | null;
  draft_count: number;
  approved_count: number;
  drafts?: ProfileDraft[];
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface ReleaseRebaseResult {
  source_release_id: string;
  target_release_id: string;
  target_index_id: string;
  target_index_version: number;
  reused_draft_count: number;
  review_required_draft_count: number;
  regenerate_document_count: number;
  remove_suggestion_count: number;
  create_suggestion_count: number;
  backfill_submitted: boolean;
}

export interface SupportReleaseRollbackResult {
  support_active_release_id: string | null;
  support_active_index_id: string | null;
  index_version: number | null;
  published_at: string | null;
  rolled_back_to_release_id: string;
}

export interface ProfileFeedback {
  question: string;
  occurrences: number;
  last_seen: string;
  recommendation: string;
}

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

export interface RequestErrorLog {
  id: string;
  request_id: string;
  method: string;
  path: string;
  status_code: number;
  error_code: string;
  message: string;
  detail?: Record<string, unknown>;
  created_at: string;
}

export interface RequestErrorLogList {
  items: RequestErrorLog[];
  total: number;
  limit: number;
  offset: number;
}

export interface TraceFailureGroup {
  error_code: string;
  count: number;
  last_seen: string;
}

export interface RequestErrorGroup {
  path: string;
  status_code: number;
  count: number;
  last_seen: string;
}

export interface RequestErrorsByDay {
  day: string;
  count: number;
}

export interface FailureSummary {
  from: string;
  to: string;
  scope: {
    trace_failures: "KNOWLEDGE_BASE" | "GLOBAL";
    request_errors: "GLOBAL";
    knowledge_id: string | null;
  };
  trace_failures: TraceFailureGroup[];
  request_errors: RequestErrorGroup[];
  request_errors_by_day: RequestErrorsByDay[];
}

export interface SupportMessage {
  id: string;
  conversation_id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  sources: Array<Record<string, unknown>>;
  status: string;
  content_blocks?: Array<Record<string, unknown>>;
  response_type?: string | null;
  support_mode?: string | null;
  created_at: string;
  updated_at: string;
}
