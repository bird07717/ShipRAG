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
  parameters: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
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
}
