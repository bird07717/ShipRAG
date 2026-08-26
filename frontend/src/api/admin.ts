import type {
  DocumentItem,
  DocumentDeleteResult,
  GarbageCollectionResult,
  KnowledgeBase,
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
  ReleaseValidation,
  RagConfig,
  RagConfigUpdatePayload,
  RagTrace,
  RuntimePolicy,
  RequestErrorLog,
  RequestErrorLogList,
  FailureSummary,
  SupportMetrics,
  SupportMessage,
  SystemInfo,
  TraceSummary,
} from "@/types/admin";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

function authorizationHeaders(): Record<string, string> {
  const token = sessionStorage.getItem("rag_service_token")?.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

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

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  for (const [name, value] of Object.entries(authorizationHeaders())) headers.set(name, value);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new ApiRequestError(body?.error?.message ?? `请求失败：HTTP ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export const adminApi = {
  getSystemInfo: () => apiRequest<SystemInfo>("/system/info"),
  listKnowledgeBases: () => apiRequest<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (name: string, description: string) =>
    apiRequest<KnowledgeBase>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name, description: description || null }),
    }),
  deleteKnowledgeBase: (knowledgeId: string) =>
    apiRequest<{ knowledge_id: string; deleted: boolean; cleanup_failed_count: number }>(
      `/knowledge-bases/${knowledgeId}`,
      { method: "DELETE" },
    ),
  listDocuments: (knowledgeId: string) =>
    apiRequest<DocumentItem[]>(`/knowledge-bases/${knowledgeId}/documents`),
  deleteDocument: (documentId: string) =>
    apiRequest<DocumentDeleteResult>(`/documents/${documentId}?request_build=true`, {
      method: "DELETE",
    }),
  uploadDocument: (
    knowledgeId: string,
    file: File,
    idempotencyKey: string = crypto.randomUUID(),
  ) => {
    const data = new FormData();
    data.append("file", file);
    data.append("request_build", "true");
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
  listChunks: (documentId: string, indexId?: string) =>
    apiRequest<{ index_id: string; items: Array<Record<string, unknown>> }>(
      `/documents/${documentId}/chunks${indexId ? `?index_id=${indexId}` : ""}`,
    ),
  listIndexes: (knowledgeId: string) =>
    apiRequest<KnowledgeIndex[]>(`/knowledge-bases/${knowledgeId}/indexes`),
  listIndexTasks: (indexId: string) => apiRequest<IndexTask[]>(`/indexes/${indexId}/tasks`),
  buildIndex: (knowledgeId: string) =>
    apiRequest(`/knowledge-bases/${knowledgeId}/indexes/build`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ reason: "MANUAL", activate_on_success: false }),
    }),
  activateIndex: (indexId: string) =>
    apiRequest<KnowledgeIndex>(`/indexes/${indexId}/activate`, { method: "POST" }),
  gcIndexes: (knowledgeId: string) =>
    apiRequest<GarbageCollectionResult>(`/knowledge-bases/${knowledgeId}/indexes/gc`, {
      method: "POST",
    }),
  deleteIndex: (indexId: string) =>
    apiRequest<GarbageCollectionResult>(`/indexes/${indexId}`, {
      method: "DELETE",
    }),
  listTraces: (
    knowledgeId?: string,
    filters: {
      status?: string;
      responseType?: string;
      userId?: string;
      errorCode?: string;
      conversationId?: string;
      engine?: "SUPPORT" | "RAG";
      from?: string;
      to?: string;
      limit?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (knowledgeId) search.set("knowledge_id", knowledgeId);
    if (filters.status) search.set("status", filters.status);
    if (filters.responseType) search.set("response_type", filters.responseType);
    if (filters.userId) search.set("user_id", filters.userId);
    if (filters.errorCode) search.set("error_code", filters.errorCode);
    if (filters.conversationId) search.set("conversation_id", filters.conversationId);
    if (filters.engine) search.set("engine", filters.engine);
    if (filters.from) search.set("from", filters.from);
    if (filters.to) search.set("to", filters.to);
    if (filters.limit) search.set("limit", String(filters.limit));
    const query = search.toString();
    return apiRequest<TraceSummary[]>(`/traces${query ? `?${query}` : ""}`);
  },
  listNoAnswerTraces: (knowledgeId?: string, limit = 20) => {
    const search = new URLSearchParams({
      response_type: "UNCONFIRMED",
      mode: "CHAT",
      limit: String(limit),
    });
    if (knowledgeId) search.set("knowledge_id", knowledgeId);
    return apiRequest<TraceSummary[]>(`/traces?${search.toString()}`);
  },
  getMetricsOverview: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<MetricsOverview>(`/metrics/overview${buildMetricsQuery(params)}`),
  getMetricsTimeseries: (
    params: { knowledgeId?: string; from?: string; to?: string; granularity?: string } = {},
  ) => apiRequest<MetricsTimeseriesPoint[]>(`/metrics/timeseries${buildMetricsQuery(params)}`),
  getMetricsLatency: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<MetricsLatency>(`/metrics/latency${buildMetricsQuery(params)}`),
  getSupportMetrics: (params: { knowledgeId?: string; from?: string; to?: string } = {}) =>
    apiRequest<SupportMetrics>(`/metrics/support${buildMetricsQuery(params)}`),
  getTrace: (traceId: string) => apiRequest<RagTrace>(`/traces/${traceId}`),
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
  listTasks: (
    filters: { status?: string; knowledgeId?: string; from?: string; to?: string; limit?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (filters.status) search.set("status", filters.status);
    if (filters.knowledgeId) search.set("knowledge_id", filters.knowledgeId);
    if (filters.from) search.set("from", filters.from);
    if (filters.to) search.set("to", filters.to);
    if (filters.limit) search.set("limit", String(filters.limit));
    const query = search.toString();
    return apiRequest<IndexTask[]>(`/tasks${query ? `?${query}` : ""}`);
  },
  listConversationMessages: (conversationId: string) =>
    apiRequest<SupportMessage[]>(`/conversations/${conversationId}/messages`),
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
    apiRequest<ProfileDraft[]>(`/indexes/${indexId}/profile-drafts/generate`, {
      method: "POST",
    }),
  backfillProfileDrafts: (indexId: string) =>
    apiRequest<ProfileDraft[]>(`/indexes/${indexId}/profile-drafts/backfill`, {
      method: "POST",
    }),
  refreshProfileDrafts: (indexId: string) =>
    apiRequest<ProfileDraft[]>(`/indexes/${indexId}/profile-drafts/refresh`, {
      method: "POST",
    }),
  regenerateProfileDraft: (draftId: string) =>
    apiRequest<ProfileDraft>(`/profile-drafts/${draftId}/regenerate`, { method: "POST" }),
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
  listProfileFeedback: (knowledgeId: string) =>
    apiRequest<ProfileFeedback[]>(`/knowledge-bases/${knowledgeId}/profile-feedback`),
};
