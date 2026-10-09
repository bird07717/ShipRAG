import type {
  DocumentItem,
  DocumentDeleteResult,
  DocumentProfileContext,
  KnowledgeProfileSummary,
  ProfilePublishCheck,
  DocumentProfileIntent,
  BuildRequestResult,
  GarbageCollectionResult,
  KnowledgeBase,
  KnowledgeBaseUpdatePayload,
  KnowledgeAssistantConfig,
  KnowledgeAssistantCheck,
  KnowledgeIndex,
  IndexTask,
  MetricsLatency,
  MetricsOverview,
  MetricsTimeseriesPoint,
  ModelConfig,
  ModelConfigUpdatePayload,
  ModelProbeResult,
  PromptTemplate,
  ProblemProfile,
  KnowledgeRelease,
  ProfileDraft,
  ProfileDraftPatch,
  ProfileFeedback,
  ReleaseRebaseResult,
  ReleaseValidation,
  RagConfig,
  RagConfigUpdatePayload,
  EngineRetrievalConfig,
  RagTrace,
  RagDocumentCard,
  RagDocumentCardBatchConfirm,
  RagDocumentCardList,
  RagDocumentCardPayload,
  RagDocumentCardReadiness,
  RuntimePolicy,
  RequestErrorLog,
  RequestErrorLogList,
  FailureSummary,
  SupportMetrics,
  AssistantMetrics,
  ConversationMessageListResponse,
  SupportReleaseRollbackResult,
  SystemInfo,
  TaskRecord,
  TraceSource,
  ConversationListItem,
  ModelRuntimeStatus,
  RuntimeMetrics,
  TaskSubmission,
  TraceSummary,
  DocumentListQuery,
  Page,
} from "@/types/admin";
import { randomUUID } from "@/utils/uuid";
import {
  ApiRequestError,
  announceServiceCredentialRejected,
  readServiceCredential,
} from "@/api/serviceCredential";
import { trackedFetch } from "@/api/connectivity";

// Re-exported so existing `import { ApiRequestError } from "@/api/admin"`
// call sites keep working; the class now lives in the shared credential
// module so `@/api/chat` can throw the same type without importing `admin`.
export { ApiRequestError };

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

function buildMetricsQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (!value) continue;
    // Callers use camelCase keys; the API contract uses snake_case.
    search.set(key === "knowledgeId" ? "knowledge_id" : key, value);
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

interface TaskListFilters {
  status?: string;
  knowledgeId?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

function taskListQuery(filters: TaskListFilters): string {
  const search = new URLSearchParams();
  if (filters.status) search.set("status", filters.status);
  if (filters.knowledgeId) search.set("knowledge_id", filters.knowledgeId);
  if (filters.from) search.set("from", filters.from);
  if (filters.to) search.set("to", filters.to);
  if (filters.limit) search.set("limit", String(filters.limit));
  if (filters.offset) search.set("offset", String(filters.offset));
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = readServiceCredential();
  if (!token) {
    announceServiceCredentialRejected("missing");
    throw new ApiRequestError("服务凭证缺失，请先配置后再访问管理功能", 401, {
      request_blocked: true,
    });
  }
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await trackedFetch(`${apiBaseUrl}/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string; details?: Record<string, unknown> };
    } | null;
    if (response.status === 401 && typeof window !== "undefined") {
      announceServiceCredentialRejected("invalid");
    }
    throw new ApiRequestError(
      body?.error?.message ?? `请求失败：HTTP ${response.status}`,
      response.status,
      body?.error?.details ?? {},
    );
  }
  return response;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await (await apiFetch(path, init)).json()) as T;
}

/**
 * Array endpoints that page on the server return the total number of matching
 * rows in X-Total-Count (body unchanged). Without the header (older backend)
 * the page itself is reported as the whole result.
 */
export async function apiRequestPage<T>(path: string, init: RequestInit = {}): Promise<Page<T>> {
  const response = await apiFetch(path, init);
  const items = (await response.json()) as T[];
  const header = response.headers?.get?.("X-Total-Count");
  const total = header === null || header === undefined || header === "" ? Number.NaN : Number(header);
  return { items, total: Number.isFinite(total) ? total : items.length };
}

interface TraceListFilters {
  status?: string;
  responseType?: string;
  userId?: string;
  errorCode?: string;
  conversationId?: string;
  engine?: "SUPPORT" | "RAG";
  source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";
  requestId?: string;
  /** Case-insensitive substring of the question (server-side). */
  question?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

function traceListQuery(knowledgeId: string | undefined, filters: TraceListFilters): string {
  const search = new URLSearchParams();
  if (knowledgeId) search.set("knowledge_id", knowledgeId);
  if (filters.status) search.set("status", filters.status);
  if (filters.responseType) search.set("response_type", filters.responseType);
  if (filters.userId) search.set("user_id", filters.userId);
  if (filters.errorCode) search.set("error_code", filters.errorCode);
  if (filters.conversationId) search.set("conversation_id", filters.conversationId);
  if (filters.engine) search.set("engine", filters.engine);
  if (filters.source) search.set("source", filters.source);
  if (filters.requestId) search.set("request_id", filters.requestId);
  if (filters.question) search.set("q", filters.question);
  if (filters.from) search.set("from", filters.from);
  if (filters.to) search.set("to", filters.to);
  if (filters.limit) search.set("limit", String(filters.limit));
  if (filters.offset) search.set("offset", String(filters.offset));
  const query = search.toString();
  return query ? `?${query}` : "";
}

function documentListQuery(query: DocumentListQuery): string {
  const search = new URLSearchParams();
  if (query.search) search.set("q", query.search);
  if (query.buildStatus) search.set("build_status", query.buildStatus);
  if (query.cardIndexId) search.set("card_index_id", query.cardIndexId);
  if (query.cardState) search.set("card_state", query.cardState);
  if (query.sort) search.set("sort", query.sort);
  if (query.order) search.set("order", query.order);
  if (query.limit) search.set("limit", String(query.limit));
  if (query.offset) search.set("offset", String(query.offset));
  const value = search.toString();
  return value ? `?${value}` : "";
}

export const adminApi = {
  getSystemInfo: () => apiRequest<SystemInfo>("/system/info"),
  listKnowledgeBases: () => apiRequest<KnowledgeBase[]>("/knowledge-bases"),
  getKnowledgeAssistant: (knowledgeId: string) =>
    apiRequest<KnowledgeAssistantCheck>(`/knowledge-bases/${knowledgeId}/assistant-config`),
  saveKnowledgeAssistant: (knowledgeId: string, revision: number, suggestions: string[]) =>
    apiRequest<KnowledgeAssistantConfig>(`/knowledge-bases/${knowledgeId}/assistant-config`, {
      method: "PUT",
      body: JSON.stringify({ revision, suggestions }),
    }),
  setKnowledgeAssistantPublication: (knowledgeId: string, revision: number, online: boolean) =>
    apiRequest<KnowledgeAssistantConfig>(`/knowledge-bases/${knowledgeId}/assistant-publication`, {
      method: "PUT",
      body: JSON.stringify({ revision, assistant_online: online }),
    }),
  createKnowledgeBase: (name: string, description: string) =>
    apiRequest<KnowledgeBase>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name, description: description || null }),
    }),
  updateKnowledgeBase: (knowledgeId: string, payload: KnowledgeBaseUpdatePayload) =>
    apiRequest<KnowledgeBase>(`/knowledge-bases/${knowledgeId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteKnowledgeBase: (knowledgeId: string) =>
    apiRequest<TaskSubmission>(
      `/knowledge-bases/${knowledgeId}`,
      { method: "DELETE" },
    ),
  listDocuments: (knowledgeId: string) =>
    apiRequest<DocumentItem[]>(`/knowledge-bases/${knowledgeId}/documents`),
  /** Server-side filtered/sorted/paged documents of one knowledge base. */
  listDocumentsPage: (knowledgeId: string, query: DocumentListQuery = {}, init: RequestInit = {}) =>
    apiRequestPage<DocumentItem>(`/knowledge-bases/${knowledgeId}/documents${documentListQuery(query)}`, init),
  listRagDocumentCards: (knowledgeId: string, indexId?: string) =>
    apiRequest<RagDocumentCardList>(
      `/knowledge-bases/${knowledgeId}/rag-document-cards${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  getRagDocumentCardReadiness: (knowledgeId: string, indexId?: string) =>
    apiRequest<RagDocumentCardReadiness>(
      `/knowledge-bases/${knowledgeId}/rag-document-cards/readiness${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  getRagDocumentCard: (knowledgeId: string, documentId: string, indexId?: string) =>
    apiRequest<RagDocumentCard | null>(
      `/knowledge-bases/${knowledgeId}/documents/${documentId}/rag-document-card${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  generateRagDocumentCards: (
    knowledgeId: string,
    payload: { index_id?: string; document_id?: string; replace_confirmed?: boolean },
  ) => apiRequest<TaskSubmission>(`/knowledge-bases/${knowledgeId}/rag-document-cards/generate`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  updateRagDocumentCard: (
    knowledgeId: string, documentId: string, indexId: string,
    expectedRevision: number, cardPayload: RagDocumentCardPayload,
  ) => apiRequest<RagDocumentCard>(
    `/knowledge-bases/${knowledgeId}/documents/${documentId}/rag-document-card`, {
      method: "PUT",
      body: JSON.stringify({
        index_id: indexId, expected_revision: expectedRevision, card_payload: cardPayload,
      }),
    },
  ),
  reviewRagDocumentCard: (
    knowledgeId: string, documentId: string, indexId: string,
    expectedRevision: number, reviewStatus: "CONFIRMED" | "REJECTED",
  ) => apiRequest<RagDocumentCard>(
    `/knowledge-bases/${knowledgeId}/documents/${documentId}/rag-document-card/review`, {
      method: "POST",
      body: JSON.stringify({
        index_id: indexId, expected_revision: expectedRevision, review_status: reviewStatus,
      }),
    },
  ),
  confirmRagDocumentCards: (knowledgeId: string, indexId?: string) =>
    apiRequest<RagDocumentCardBatchConfirm>(
      `/knowledge-bases/${knowledgeId}/rag-document-cards/confirm-batch`, {
        method: "POST",
        body: JSON.stringify({ index_id: indexId ?? null }),
      },
    ),
  renameDocument: (documentId: string, displayName: string) =>
    apiRequest<DocumentItem>(`/documents/${documentId}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    }),
  deleteDocument: (documentId: string) =>
    apiRequest<DocumentDeleteResult>(`/documents/${documentId}?request_build=true`, {
      method: "DELETE",
    }),
  uploadDocument: (
    knowledgeId: string,
    file: File,
    idempotencyKey: string = randomUUID(),
    requestBuild = true,
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("request_build", requestBuild ? "true" : "false");
    return apiRequest(`/knowledge-bases/${knowledgeId}/documents`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: data,
    });
  },
  listElements: (documentId: string, indexId?: string) =>
    apiRequest<{ index_id: string; items: Array<Record<string, unknown>> }>(
      `/documents/${documentId}/elements${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  getDocumentProfileContext: (documentId: string) =>
    apiRequest<DocumentProfileContext>(`/documents/${documentId}/profile-context`),
  getKnowledgeProfiles: (knowledgeId: string) =>
    apiRequest<KnowledgeProfileSummary>(`/knowledge-bases/${knowledgeId}/profiles`),
  generateKnowledgeProfiles: (knowledgeId: string) =>
    apiRequest<TaskSubmission>(`/knowledge-bases/${knowledgeId}/profiles/generate`, { method: "POST" }),
  generateDocumentProfile: (knowledgeId: string, documentId: string) =>
    apiRequest<TaskSubmission>(`/knowledge-bases/${knowledgeId}/documents/${documentId}/profile/generate`, { method: "POST" }),
  updateDocumentProfile: (knowledgeId: string, documentId: string, payload: DocumentProfileIntent) =>
    apiRequest<DocumentProfileContext>(`/knowledge-bases/${knowledgeId}/documents/${documentId}/profile`, {
      method: "PUT", body: JSON.stringify(payload),
    }),
  checkKnowledgeProfiles: (knowledgeId: string) =>
    apiRequest<ProfilePublishCheck>(`/knowledge-bases/${knowledgeId}/profiles/check`, { method: "POST" }),
  publishKnowledgeProfiles: (knowledgeId: string) =>
    apiRequest<{ published_count: number; check: ProfilePublishCheck; summary: KnowledgeProfileSummary }>(
      `/knowledge-bases/${knowledgeId}/profiles/publish`, { method: "POST" },
    ),
  discardPendingKnowledgeProfiles: (knowledgeId: string) =>
    apiRequest<{ discarded_count: number }>(`/knowledge-bases/${knowledgeId}/profiles/discard-pending`, { method: "POST" }),
  rebuildKnowledgeProfiles: (knowledgeId: string, idempotencyKey: string = randomUUID()) =>
    apiRequest<TaskSubmission>(`/knowledge-bases/${knowledgeId}/profiles/rebuild`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  listChunks: (documentId: string, indexId?: string) =>
    apiRequest<{ index_id: string; items: Array<Record<string, unknown>> }>(
      `/documents/${documentId}/chunks${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  listIndexes: (knowledgeId: string) =>
    apiRequest<KnowledgeIndex[]>(`/knowledge-bases/${knowledgeId}/indexes`),
  listIndexTasks: (indexId: string) => apiRequest<IndexTask[]>(`/indexes/${indexId}/tasks`),
  getTask: (taskId: string) => apiRequest<TaskRecord>(`/tasks/${taskId}`),
  cancelTask: (taskId: string) =>
    apiRequest<TaskRecord>(`/tasks/${taskId}/cancel`, { method: "POST" }),
  buildIndex: (knowledgeId: string) =>
    apiRequest<BuildRequestResult>(`/knowledge-bases/${knowledgeId}/indexes/build`, {
      method: "POST",
      headers: { "Idempotency-Key": randomUUID() },
      body: JSON.stringify({ reason: "MANUAL", activate_on_success: false }),
    }),
  activateIndex: (indexId: string) =>
    apiRequest<KnowledgeIndex>(`/indexes/${indexId}/activate`, { method: "POST" }),
  retryIndex: (indexId: string) =>
    apiRequest<BuildRequestResult>(`/indexes/${indexId}/retry`, { method: "POST" }),
  gcIndexes: (knowledgeId: string) =>
    apiRequest<GarbageCollectionResult>(`/knowledge-bases/${knowledgeId}/indexes/gc`, {
      method: "POST",
    }),
  deleteIndex: (indexId: string, force = false) =>
    apiRequest<GarbageCollectionResult>(`/indexes/${indexId}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    }),
  listTraces: (knowledgeId?: string, filters: TraceListFilters = {}, init: RequestInit = {}) =>
    apiRequest<TraceSummary[]>(`/traces${traceListQuery(knowledgeId, filters)}`, init),
  /** Server-paged trace list; `total` counts every trace matching the filters. */
  listTracesPage: (knowledgeId?: string, filters: TraceListFilters = {}, init: RequestInit = {}) =>
    apiRequestPage<TraceSummary>(`/traces${traceListQuery(knowledgeId, filters)}`, init),
  listUnconfirmedTraces: (
    knowledgeId?: string,
    filters: {
      from?: string;
      to?: string;
      engine?: "SUPPORT" | "RAG";
      source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";
      limit?: number;
      excludeLowInformation?: boolean;
    } = {},
  ) => {
    const search = new URLSearchParams({
      response_type: "UNCONFIRMED",
      mode: "CHAT",
      status: "COMPLETED",
      limit: String(filters.limit ?? 20),
    });
    if (knowledgeId) search.set("knowledge_id", knowledgeId);
    if (filters.from) search.set("from", filters.from);
    if (filters.to) search.set("to", filters.to);
    if (filters.engine) search.set("engine", filters.engine);
    if (filters.source) search.set("source", filters.source);
    if (filters.excludeLowInformation) search.set("exclude_low_information", "true");
    return apiRequest<TraceSummary[]>(`/traces?${search.toString()}`);
  },
  getMetricsOverview: (
    params: {
      knowledgeId?: string;
      from?: string;
      to?: string;
      engine?: "SUPPORT" | "RAG";
      source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";
    } = {},
  ) =>
    apiRequest<MetricsOverview>(`/metrics/overview${buildMetricsQuery(params)}`),
  getMetricsTimeseries: (
    params: {
      knowledgeId?: string;
      from?: string;
      to?: string;
      granularity?: string;
      engine?: "SUPPORT" | "RAG";
      source?: "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";
    } = {},
  ) => apiRequest<MetricsTimeseriesPoint[]>(`/metrics/timeseries${buildMetricsQuery(params)}`),
  getMetricsLatency: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<MetricsLatency>(`/metrics/latency${buildMetricsQuery(params)}`),
  getSupportMetrics: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<SupportMetrics>(`/metrics/support${buildMetricsQuery(params)}`),
  getAssistantMetrics: (params: { knowledgeId?: string; days?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.knowledgeId) search.set("knowledge_id", params.knowledgeId);
    if (params.days) search.set("days", String(params.days));
    const query = search.toString();
    return apiRequest<AssistantMetrics>(`/metrics/assistant${query ? `?${query}` : ""}`);
  },
  getTrace: (traceId: string, init: RequestInit = {}) =>
    apiRequest<RagTrace>(`/traces/${traceId}`, init),
  listRequestErrors: (
    filters: {
      requestId?: string;
      path?: string;
      statusCode?: number;
      from?: string;
      to?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (filters.requestId) search.set("request_id", filters.requestId.trim());
    if (filters.path) search.set("path", filters.path.trim());
    if (filters.statusCode) search.set("status_code", String(filters.statusCode));
    if (filters.from) search.set("from", filters.from);
    if (filters.to) search.set("to", filters.to);
    if (filters.limit) search.set("limit", String(filters.limit));
    if (filters.offset) search.set("offset", String(filters.offset));
    const query = search.toString();
    return apiRequest<RequestErrorLogList>(`/logs/request-errors${query ? `?${query}` : ""}`);
  },
  getRequestError: (errorId: string) =>
    apiRequest<RequestErrorLog>(`/logs/request-errors/${errorId}`),
  getFailureSummary: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<FailureSummary>(`/logs/failure-summary${buildMetricsQuery(params)}`),
  listTasks: (filters: TaskListFilters = {}) => apiRequest<IndexTask[]>(`/tasks${taskListQuery(filters)}`),
  /** Same endpoint as listTasks, typed with the full row (kb_id, timestamps) it returns. */
  listTaskRecords: (filters: TaskListFilters = {}) =>
    apiRequest<TaskRecord[]>(`/tasks${taskListQuery(filters)}`),
  /** Server-paged task list; `total` counts every task matching the filters. */
  listTasksPage: (filters: TaskListFilters = {}, init: RequestInit = {}) =>
    apiRequestPage<TaskRecord>(`/tasks${taskListQuery(filters)}`, init),
  listConversations: (
    knowledgeId: string,
    filters: {
      userId?: string;
      source?: TraceSource;
      limit?: number;
      offset?: number;
      activityFrom?: string;
      activityTo?: string;
    } = {},
    init: RequestInit = {},
  ) => {
    const search = new URLSearchParams({ knowledge_id: knowledgeId });
    if (filters.userId) search.set("user_id", filters.userId);
    if (filters.source) search.set("source", filters.source);
    if (filters.limit) search.set("limit", String(filters.limit));
    if (filters.offset) search.set("offset", String(filters.offset));
    if (filters.activityFrom) search.set("activity_from", filters.activityFrom);
    if (filters.activityTo) search.set("activity_to", filters.activityTo);
    // `total` is reported by servers that page conversations (absent on older ones).
    return apiRequest<{ items: ConversationListItem[]; total?: number | null }>(
      `/conversations?${search.toString()}`,
      init,
    );
  },
  getModelRuntime: () => apiRequest<ModelRuntimeStatus>("/system/model-runtime"),
  getRuntimeMetrics: () => apiRequest<RuntimeMetrics>("/metrics/runtime"),
  listConversationMessages: (conversationId: string, init: RequestInit = {}) =>
    apiRequest<ConversationMessageListResponse>(`/conversations/${conversationId}/messages`, init),
  runPlayground: (knowledgeId: string, question: string, options: Record<string, number>) =>
    apiRequest<RagTrace>("/rag/playground", {
      method: "POST",
      body: JSON.stringify({ knowledge_id: knowledgeId, question, options }),
    }),
  listModels: () => apiRequest<ModelConfig[]>("/models"),
  updateModel: (modelId: string, payload: ModelConfigUpdatePayload) =>
    apiRequest<ModelConfig>(`/models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  putApiKey: (modelId: string, apiKey: string) =>
    apiRequest<ModelConfig>(`/models/${modelId}/api-key`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteApiKey: (modelId: string) =>
    apiRequest<ModelConfig>(`/models/${modelId}/api-key`, {
      method: "DELETE",
    }),
  probeModel: (modelId: string) =>
    apiRequest<ModelProbeResult>(`/models/${modelId}/probe`, {
      method: "POST",
    }),
  getRagConfig: () => apiRequest<RagConfig>("/rag-config"),
  updateRagConfig: (payload: RagConfigUpdatePayload) =>
    apiRequest<RagConfig>("/rag-config", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listEngineRetrievalConfigs: () =>
    apiRequest<EngineRetrievalConfig[]>("/engine-retrieval-configs"),
  updateEngineRetrievalConfig: (
    executionPolicy: EngineRetrievalConfig["execution_policy"],
    payload: RagConfigUpdatePayload,
  ) =>
    apiRequest<EngineRetrievalConfig>(`/engine-retrieval-configs/${executionPolicy}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getRuntimePolicy: (knowledgeId: string) =>
    apiRequest<RuntimePolicy>(`/runtime-policies/${knowledgeId}`),
  updateRuntimePolicy: (knowledgeId: string, executionPolicy: RuntimePolicy["execution_policy"]) =>
    apiRequest<RuntimePolicy>(`/runtime-policies/${knowledgeId}`, {
      method: "PUT",
      body: JSON.stringify({ execution_policy: executionPolicy }),
    }),
  listPrompts: () => apiRequest<PromptTemplate[]>("/prompts"),
  listProblemProfiles: (knowledgeId: string) =>
    apiRequest<ProblemProfile[]>(`/support/problem-profiles?knowledge_id=${knowledgeId}`),
  listKnowledgeReleases: (knowledgeId: string) =>
    apiRequest<KnowledgeRelease[]>(`/knowledge-bases/${knowledgeId}/releases`),
  getKnowledgeRelease: (releaseId: string) =>
    apiRequest<KnowledgeRelease>(`/releases/${releaseId}`),
  generateProfileDrafts: (indexId: string) =>
    apiRequest<TaskSubmission>(`/indexes/${indexId}/profile-drafts/generate`, {
      method: "POST",
    }),
  backfillProfileDrafts: (indexId: string) =>
    apiRequest<TaskSubmission>(`/indexes/${indexId}/profile-drafts/backfill`, {
      method: "POST",
    }),
  refreshProfileDrafts: (indexId: string) =>
    apiRequest<TaskSubmission>(`/indexes/${indexId}/profile-drafts/refresh`, {
      method: "POST",
    }),
  regenerateProfileDraft: (draftId: string) =>
    apiRequest<TaskSubmission>(`/profile-drafts/${draftId}/regenerate`, { method: "POST" }),
  createProfileEditDraft: (releaseId: string, profileId: string) =>
    apiRequest<ProfileDraft>(`/releases/${releaseId}/profiles/${profileId}/draft`, {
      method: "POST",
    }),
  updateProfileDraft: (draftId: string, payload: ProfileDraftPatch) =>
    apiRequest<ProfileDraft>(`/profile-drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  validateKnowledgeRelease: (releaseId: string) =>
    apiRequest<ReleaseValidation>(`/releases/${releaseId}/validate`, { method: "POST" }),
  publishKnowledgeRelease: (releaseId: string) =>
    apiRequest<KnowledgeRelease>(`/releases/${releaseId}/publish`, { method: "POST" }),
  rebaseKnowledgeRelease: (releaseId: string) =>
    apiRequest<ReleaseRebaseResult>(`/releases/${releaseId}/rebase`, { method: "POST" }),
  rollbackSupportRelease: (knowledgeId: string, releaseId: string) =>
    apiRequest<SupportReleaseRollbackResult>(
      `/knowledge-bases/${knowledgeId}/support-release/rollback`,
      {
        method: "POST",
        body: JSON.stringify({ target_release_id: releaseId }),
      },
    ),
  rollbackRagIndex: (knowledgeId: string, indexId: string) =>
    apiRequest<KnowledgeIndex>(`/knowledge-bases/${knowledgeId}/rag-index/rollback`, {
      method: "POST",
      body: JSON.stringify({ target_index_id: indexId }),
    }),
  listProfileFeedback: (knowledgeId: string) =>
    apiRequest<ProfileFeedback[]>(`/knowledge-bases/${knowledgeId}/profile-feedback`),
};
