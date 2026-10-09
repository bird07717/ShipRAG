export interface KnowledgeAssistantConfig {
  knowledge_id: string;
  assistant_online: boolean;
  suggestions: string[];
  revision: number;
}

export interface KnowledgeAssistantCheck extends KnowledgeAssistantConfig {
  ready: boolean;
  blockers: string[];
  checks?: PublicationCheck[];
}

export interface PublicationCheck {
  code: string;
  title: string;
  status: "PASSED" | "BLOCKED";
  messages: string[];
  action: { view: "knowledge" | "configuration" | "indexes" | "documents" | "engine-routing"; query: string; label: string };
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  status: string;
  assistant_online: boolean;
  assistant_config_revision?: number;
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

export interface KnowledgeBaseUpdatePayload {
  description: string | null;
  expected_updated_at: string;
}

export interface SystemInfo {
  name: string;
  environment: string;
  version: string;
  build?: BuildInfo;
}

export interface BuildInfo {
  commit: string | null;
  built_at: string | null;
  dirty: boolean | null;
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
  last_build_status?: string | null;
  last_build_error?: string | null;
  last_build_version?: number | null;
  /** Present only when listed with card_index_id. */
  card_state?: DocumentCardState | null;
}

/** RAG card review state of a document for one index (server-side filter). */
export type DocumentCardState =
  | "MISSING"
  | "GENERATING"
  | "FAILED"
  | "PENDING_REVIEW"
  | "REJECTED"
  | "CONFIRMED";

export interface DocumentListQuery {
  search?: string;
  buildStatus?: string;
  cardState?: DocumentCardState;
  /** Required with cardState; normally the active RAG index. */
  cardIndexId?: string;
  sort?: "created_at" | "display_name" | "file_size";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/** One page of a server-paged admin list plus the total matching rows. */
export interface Page<T> {
  items: T[];
  total: number;
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
  pruned_task_count?: number;
  released_conversation_count?: number;
}

export interface DocumentDeleteResult {
  document_id: string;
  deleted: boolean;
  build_request: BuildRequestResult;
}

export type RagDocumentCardType =
  | "PROCEDURE"
  | "REFERENCE"
  | "TROUBLESHOOTING"
  | "OVERVIEW"
  | "MIXED";

export interface RagDocumentCardPayload {
  document_type: RagDocumentCardType;
  summary: string;
  components: string[];
  tasks: string[];
  applicable_questions: string[];
  prerequisites: string[];
  key_topics: string[];
  exclusions: string[];
  has_procedure: boolean;
  has_images: boolean;
}

export interface RagDocumentCard {
  id: string;
  knowledge_id: string;
  index_id: string;
  index_version: number;
  document_id: string;
  document_name: string;
  schema_version: number;
  document_type: RagDocumentCardType | null;
  card_payload: RagDocumentCardPayload | null;
  generation_status: "PENDING" | "READY" | "FAILED" | "STALE";
  review_status: "AUTO" | "CONFIRMED" | "REJECTED";
  revision: number;
  content_fingerprint: string | null;
  generator_snapshot: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface RagDocumentCardReadiness {
  knowledge_id: string;
  index_id: string;
  expected_count: number;
  ready_count: number;
  confirmed_count: number;
  auto_count: number;
  rejected_count: number;
  missing_count: number;
  failed_count: number;
  stale_count: number;
  shadow_ready: boolean;
  ready: boolean;
  blockers: string[];
}

export interface RagDocumentCardBatchConfirm {
  confirmed_count: number;
  readiness: RagDocumentCardReadiness;
}

export interface RagDocumentCardList {
  knowledge_id: string;
  index_id: string;
  index_version: number;
  cards: RagDocumentCard[];
  readiness: RagDocumentCardReadiness;
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
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
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
  source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";
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
  runtime_snapshot?: Record<string, unknown>;
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
  supported_providers?: string[];
  parameters: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  rebuilds?: Array<{
    knowledge_id: string;
    index_id: string | null;
    task_id: string | null;
    coalesced: boolean;
    enqueued: boolean;
  }> | null;
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
  provider?: string;
  model_name?: string;
  base_url?: string;
  api_key?: string;
  enabled?: boolean;
  parameters?: Record<string, unknown>;
  rebuild_all_confirmed?: boolean;
};

export type RagConfigUpdatePayload = Partial<Omit<RagConfig, "updated_at">>;

export type ExecutionPolicy = "RAG_ONLY" | "SUPPORT_WITH_RAG_FALLBACK";

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
  canonical_question: string;
  aliases: string[];
  routing_summary: string;
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
  /** 参与统计的轮次数；零模型调用的输入门禁短路不计入延迟。 */
  samples: number;
}

export interface MetricsOverview {
  range: {
    from: string;
    to: string;
    mode: string;
    engine?: "SUPPORT" | "RAG" | null;
    source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND" | null;
  };
  total_traces: number;
  completed: number;
  failed: number;
  cancelled: number;
  response_type_counts: Record<string, number>;
  answer_mode_counts: Record<string, number>;
  answer_basis_traces: number;
  product_knowledge_rate: number;
  product_explained_rate: number;
  product_general_rate: number;
  classified_traces: number;
  in_scope_traces: number;
  direct_answer_rate: number;
  needs_information_rate: number;
  out_of_scope_rate: number;
  doc_delivered_rate: number;
  failure_rate: number;
  /** Legacy column never written by V2 — always 0 for current traffic; do not display. */
  citation_missing_rate: number;
  /** Legacy column never written by V2 — always 0 for current traffic; use v2_quality. */
  rerank_degraded_rate: number;
  /** Absent on backends older than this field. */
  v2_quality?: MetricsV2Quality;
  unique_users: number;
  latency: MetricsLatencySummary;
  tokens: {
    llm_prompt: number;
    llm_completion: number;
    llm_total: number;
    rerank_total: number;
  };
}

/** RAG V2 stage degradation, read from runtime_snapshot.generation. */
export interface MetricsV2Quality {
  query_interpreter_total: number;
  query_interpreter_degraded: number;
  query_interpreter_degraded_rate: number;
  document_rerank_attempted: number;
  document_rerank_degraded: number;
  document_rerank_degraded_rate: number;
  /** Knowledge turns with >=2 candidates where no Rerank model was usable. */
  document_rerank_unconfigured: number;
  /**
   * Knowledge turns whose retrieval ran, and how many lost the query vector and
   * answered from BM25-only evidence. Optional: a backend older than this field
   * does not send them, and the console must say so instead of showing zeros.
   */
  query_embedding_attempted?: number;
  query_embedding_degraded?: number;
  query_embedding_degraded_rate?: number;
}

export interface MetricsTimeseriesPoint {
  bucket: string;
  queries: number;
  failed: number;
  answered: number;
  unconfirmed: number;
  out_of_scope: number;
  doc_delivered: number;
  direct_answer_rate: number;
  needs_information_rate: number;
  out_of_scope_rate: number;
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

export interface AssistantSourceStat {
  source: string;
  turns: number;
  failed: number;
  error_rate: number;
  conversations: number;
  users: number;
  latency: MetricsLatencySummary;
}

export interface AssistantUserStat {
  user_id: string;
  turns: number;
  failed: number;
  last_seen: string;
}

export interface AssistantErrorCodeStat {
  code: string;
  count: number;
}

export interface AssistantHttpErrorStat {
  status_code: number;
  error_code: string;
  count: number;
}

export interface AssistantMetrics {
  days: number;
  sources: AssistantSourceStat[];
  top_users: AssistantUserStat[];
  turn_error_codes: AssistantErrorCodeStat[];
  http_errors: AssistantHttpErrorStat[];
  idempotency: {
    assistant_protected: number;
    assistant_replays: number;
    ingestion_operations: Record<string, number>;
  };
  retention: {
    retention_days: number;
    total: number;
    active: number;
    expired: number;
  };
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

export interface ConversationMessageListResponse {
  items: SupportMessage[];
}

export type ModelRuntimeKind = "REAL" | "FAKE" | "NOT_CONFIGURED" | "DISABLED" | "UNAVAILABLE";

/** What one model type actually runs with (backend resolve_provider_kind). */
export interface ModelRuntimeItem {
  model_type: "LLM" | "EMBEDDING" | "RERANK" | "OCR" | "VISION";
  /** ENV_FALLBACK: an unsupported provider row is ignored by index builds. */
  source: "DATABASE" | "ENV" | "ENV_FALLBACK" | "NONE";
  provider: string | null;
  model_name: string | null;
  stored_key_configured: boolean;
  environment_key_configured: boolean;
  kind: ModelRuntimeKind;
  reason: string;
}

export interface ModelRuntimeStatus {
  environment: string;
  models: ModelRuntimeItem[];
  simulated_active_index_count: number;
  simulated: boolean;
}

export type TraceSource = "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";

export interface ConversationListItem {
  id: string;
  knowledge_id: string;
  user_id: string | null;
  title: string | null;
  message_count: number;
  last_execution_engine: string | null;
  conversation_mode: string | null;
  created_at: string;
  updated_at: string;
  /** Channel of the latest turn; null when the conversation has no traces. */
  last_source: TraceSource | null;
}

/** Snapshot of the API process that served the request (not the whole service). */
export interface RuntimeMetrics {
  turn_gate: { limit: number; in_flight: number };
  database_pool: { size: number; checked_in: number; checked_out: number; overflow: number };
}
