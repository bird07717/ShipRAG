import {
  ApiRequestError,
  announceServiceCredentialRejected,
  readServiceCredential,
} from "@/api/serviceCredential";
import { trackedFetch } from "@/api/connectivity";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

function authHeaders(): Record<string, string> {
  const token = readServiceCredential();
  if (!token) {
    announceServiceCredentialRejected("missing");
    // Same shape as admin.ts's local guard: callers that care can check
    // `instanceof ApiRequestError && details.request_blocked` to tell this
    // apart from a real network or server failure below.
    throw new ApiRequestError("服务凭证缺失，请先配置后再访问管理功能", 401, {
      request_blocked: true,
    });
  }
  return { Authorization: "Bearer " + token };
}

function handleCredentialRejection(response: Response): void {
  if (response.status === 401) announceServiceCredentialRejected("invalid");
}

export interface ChatContentBlock {
  type: "text" | "table" | "image";
  sequence_no?: number;
  text?: string;
  source_id?: string;
  image_asset_id?: string;
  caption?: string;
  url?: string;
  mime_type?: string;
  width?: number;
  height?: number;
}

export interface ChatSupportSection {
  type: string;
  title: string;
  content: string;
  evidence_element_ids: string[];
  risk_level?: string | null;
  disclaimer?: string | null;
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
  trace_id: string;
  index_id?: string;
  answer: string;
  response_type: "ANSWERED" | "UNCONFIRMED" | "OUT_OF_SCOPE" | "DOC_DELIVERED" | "DOCUMENT_DELIVERED" | "CLARIFICATION_REQUIRED" | "NEED_MORE_INFORMATION" | "NO_MATCH" | "ESCALATION_REQUIRED";
  answer_mode?: string | null;
  support_mode?: string | null;
  disclaimer?: string | null;
  content: ChatContentBlock[];
  sections?: ChatSupportSection[];
  clarification?: { type: string; message: string; choices: Array<{ choice_id: string; title: string }> } | null;
  escalation?: { required: boolean; reason: string | null } | null;
  execution_engine?: "SUPPORT" | "RAG" | null;
  fallback?: {
    from: "SUPPORT" | "RAG";
    reason: string;
    support_trace_id?: string;
  } | null;
  references: ChatReference[];
  usage: Record<string, unknown>;
  latency_ms: number;
  /** Retry questions surfaced after NO_MATCH routing turns; empty otherwise. */
  next_suggestions?: string[];
}

export interface ChatStatusEvent {
  stage: string;
  engine?: "SUPPORT" | "RAG";
}

export interface ChatFallbackEvent {
  from: "SUPPORT" | "RAG";
  to: "SUPPORT" | "RAG";
  reason: string;
  support_trace_id?: string;
}

export interface ChatErrorEvent {
  code: string;
  message: string;
  request_id?: string;
  trace_id?: string;
  retryable?: boolean;
}

export interface ChatStreamCallbacks {
  onTrace: (traceId: string, conversationId: string) => void;
  onDelta: (text: string) => void;
  onDone: (event: ChatDoneEvent) => void;
  onError: (message: string, event?: ChatErrorEvent) => void;
  onStatus?: (event: ChatStatusEvent) => void;
  onFallback?: (event: ChatFallbackEvent) => void;
}

export interface WelcomePayload {
  knowledge_base: { id: string; name: string; ready: boolean };
  message: string;
  suggestions: string[];
}

export async function fetchWelcome(knowledgeId: string): Promise<WelcomePayload> {
  const response = await trackedFetch(
    apiBaseUrl + `/api/v1/chat/welcome?knowledge_id=${encodeURIComponent(knowledgeId)}`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    handleCredentialRejection(response);
    throw new Error(`welcome request failed: ${response.status}`);
  }
  return (await response.json()) as WelcomePayload;
}

function parseSSEBlock(block: string): { event: string; data: string } | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  return dataLines.length ? { event: event || "message", data: dataLines.join("\n") } : null;
}

function consumeSSELines(
  text: string,
  state: { line: string; eventLines: string[] },
  onBlock: (block: string) => void,
  final = false,
): void {
  state.line += text;
  while (state.line) {
    const match = /[\r\n]/.exec(state.line);
    if (!match || match.index === undefined) break;
    const index = match.index;
    const delimiter = state.line[index];
    if (delimiter === "\r" && index === state.line.length - 1 && !final) break;
    const line = state.line.slice(0, index);
    const delimiterLength = delimiter === "\r" && state.line[index + 1] === "\n" ? 2 : 1;
    state.line = state.line.slice(index + delimiterLength);
    if (line) state.eventLines.push(line);
    else if (state.eventLines.length) {
      onBlock(state.eventLines.join("\n"));
      state.eventLines = [];
    }
  }
  if (final) {
    if (state.line) state.eventLines.push(state.line);
    state.line = "";
    if (state.eventLines.length) {
      onBlock(state.eventLines.join("\n"));
      state.eventLines = [];
    }
  }
}

export async function streamChat(
  knowledgeId: string,
  question: string,
  conversationId: string | undefined,
  callbacks: ChatStreamCallbacks,
  options: { choiceId?: string } = {},
  signal?: AbortSignal,
): Promise<void> {
  const response = await trackedFetch(apiBaseUrl + "/api/v1/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({
      knowledge_id: knowledgeId,
      conversation_id: conversationId ?? null,
      question,
      ...(options.choiceId ? { choice_id: options.choiceId } : {}),
    }),
    signal,
  });

  if (!response.ok) {
    handleCredentialRejection(response);
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(body?.error?.message ?? "HTTP " + response.status);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  const parserState = { line: "", eventLines: [] as string[] };
  let terminalReceived = false;
  let protocolErrorReported = false;

  const dispatchBlock = (block: string): void => {
    const evt = parseSSEBlock(block);
    if (!evt) return;
    try {
      const payload = JSON.parse(evt.data);
      if (evt.event === "trace") {
        callbacks.onTrace(payload.trace_id, payload.conversation_id);
      } else if (evt.event === "message") {
        callbacks.onDelta(payload.delta);
      } else if (evt.event === "done") {
        terminalReceived = true;
        callbacks.onDone(payload as ChatDoneEvent);
      } else if (evt.event === "status") {
        callbacks.onStatus?.(payload as ChatStatusEvent);
      } else if (evt.event === "fallback") {
        callbacks.onFallback?.(payload as ChatFallbackEvent);
      } else if (evt.event === "error") {
        terminalReceived = true;
        const error = payload as ChatErrorEvent;
        // 展示层只呈现消息本体（后端对 5xx 已统一为"服务繁忙，请稍后重试"）；
        // code / request_id 仍随错误对象携带，供控制台与工单排查使用。
        callbacks.onError(error.message ?? "服务繁忙，请稍后重试", error);
      }
    } catch {
      protocolErrorReported = true;
      callbacks.onError("服务繁忙，请稍后重试");
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    consumeSSELines(decoder.decode(value, { stream: true }), parserState, dispatchBlock);
  }
  consumeSSELines(decoder.decode(), parserState, dispatchBlock, true);
  if (!terminalReceived && !protocolErrorReported && !signal?.aborted) {
    callbacks.onError("问答流在完成前已断开");
  }
}

export async function fetchImageBlobUrl(imageAssetId: string, signal?: AbortSignal): Promise<string> {
  const response = await trackedFetch(
    apiBaseUrl + "/api/v1/image-assets/" + imageAssetId + "/content",
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    handleCredentialRejection(response);
    throw new ApiRequestError("图片加载失败：HTTP " + response.status, response.status);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function downloadDocument(
  documentId: string,
  filename: string,
): Promise<void> {
  const response = await trackedFetch(
    apiBaseUrl + "/api/v1/documents/" + documentId + "/content?download=true",
    { headers: authHeaders() },
  );
  if (!response.ok) {
    handleCredentialRejection(response);
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
  // Revoking synchronously can cancel the download in some browsers; release
  // the blob once the click has been handed to the download manager.
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}
