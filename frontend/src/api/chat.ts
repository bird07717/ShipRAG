const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

function authHeaders(): Record<string, string> {
  const token = sessionStorage.getItem("rag_service_token")?.trim();
  return token ? { Authorization: "Bearer " + token } : {};
}

export interface ChatContentBlock {
  type: "text" | "image";
  text?: string;
  source_id?: string;
  image_asset_id?: string;
}

export interface ChatReference {
  document_id: string;
  title: string;
  section_paths: string[][];
  source_ids: string[];
  download_url: string;
}

export interface ChatDoneEvent {
  conversation_id: string;
  message_id: string;
  answer: string;
  response_type: "ANSWERED" | "UNCONFIRMED" | "OUT_OF_SCOPE" | "DOC_DELIVERED";
  answer_mode: string | null;
  disclaimer: string | null;
  content: ChatContentBlock[];
  references: ChatReference[];
  usage: Record<string, unknown>;
  latency_ms: number;
}

export interface ChatStreamCallbacks {
  onTrace: (traceId: string, conversationId: string) => void;
  onDelta: (text: string) => void;
  onDone: (event: ChatDoneEvent) => void;
  onError: (message: string) => void;
}

export interface WelcomePayload {
  knowledge_base: { id: string; name: string; ready: boolean };
  message: string;
  suggestions: string[];
}

export async function fetchWelcome(knowledgeId: string): Promise<WelcomePayload> {
  const response = await fetch(
    apiBaseUrl + `/api/v1/chat/welcome?knowledge_id=${encodeURIComponent(knowledgeId)}`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(`welcome request failed: ${response.status}`);
  }
  return (await response.json()) as WelcomePayload;
}

function parseSSE(chunk: string): Array<{ event: string; data: string }> {
  const events: Array<{ event: string; data: string }> = [];
  const blocks = chunk.split("\n\n");
  for (const block of blocks) {
    const lines = block.split("\n");
    let event = "";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    if (event && data) events.push({ event, data });
  }
  return events;
}

export async function streamChat(
  knowledgeId: string,
  question: string,
  conversationId: string | undefined,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(apiBaseUrl + "/api/v1/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({
      knowledge_id: knowledgeId,
      conversation_id: conversationId ?? null,
      question,
    }),
    signal,
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(body?.error?.message ?? "HTTP " + response.status);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = parseSSE(buffer);
    buffer = "";
    for (const evt of events) {
      try {
        const payload = JSON.parse(evt.data);
        if (evt.event === "trace") {
          callbacks.onTrace(payload.trace_id, payload.conversation_id);
        } else if (evt.event === "message") {
          callbacks.onDelta(payload.delta);
        } else if (evt.event === "done") {
          callbacks.onDone(payload as ChatDoneEvent);
        } else if (evt.event === "error") {
          callbacks.onError(payload.message ?? "未知错误");
        }
      } catch {
        // partial JSON, keep in buffer
        buffer = evt.data;
      }
    }
  }
}

export async function fetchImageBlobUrl(imageAssetId: string): Promise<string> {
  const response = await fetch(
    apiBaseUrl + "/api/v1/image-assets/" + imageAssetId + "/content",
    { headers: authHeaders() },
  );
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function downloadDocument(
  documentId: string,
  filename: string,
): Promise<void> {
  const response = await fetch(
    apiBaseUrl + "/api/v1/documents/" + documentId + "/content?download=true",
    { headers: authHeaders() },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(body?.error?.message ?? "HTTP " + response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
