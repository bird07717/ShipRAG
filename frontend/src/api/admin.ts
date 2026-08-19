import type {
  DocumentItem,
  DocumentDeleteResult,
  GarbageCollectionResult,
  KnowledgeBase,
  KnowledgeIndex,
  IndexTask,
  ModelConfig,
  PromptTemplate,
  RagTrace,
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
  listKnowledgeBases: () => apiRequest<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (name: string, description: string) =>
    apiRequest<KnowledgeBase>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name, description: description || null }),
    }),
  listDocuments: (knowledgeId: string) =>
    apiRequest<DocumentItem[]>(`/knowledge-bases/${knowledgeId}/documents`),
  deleteDocument: (documentId: string) =>
    apiRequest<DocumentDeleteResult>(`/documents/${documentId}?request_build=true`, {
      method: "DELETE",
    }),
  uploadDocument: (knowledgeId: string, file: File) => {
    const data = new FormData();
    data.append("file", file);
    data.append("request_build", "true");
    return apiRequest(`/knowledge-bases/${knowledgeId}/documents`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
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
  buildIndex: (knowledgeId: string, activateOnSuccess: boolean) =>
    apiRequest(`/knowledge-bases/${knowledgeId}/indexes/build`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ reason: "MANUAL", activate_on_success: activateOnSuccess }),
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
  listTraces: (knowledgeId?: string) =>
    apiRequest<TraceSummary[]>(`/traces${knowledgeId ? `?knowledge_id=${knowledgeId}` : ""}`),
  getTrace: (traceId: string) => apiRequest<RagTrace>(`/traces/${traceId}`),
  runPlayground: (knowledgeId: string, question: string, options: Record<string, number>) =>
    apiRequest<RagTrace>("/rag/playground", {
      method: "POST",
      body: JSON.stringify({ knowledge_id: knowledgeId, question, options }),
    }),
  listModels: () => apiRequest<ModelConfig[]>("/models"),
  listPrompts: () => apiRequest<PromptTemplate[]>("/prompts"),
};
