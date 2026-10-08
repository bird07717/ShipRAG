import { reactive } from "vue";

/*
 * Backend reachability as observed by the request layer.
 *
 * Every API module sends requests through `trackedFetch`, which only records
 * what it sees: a response produced by the backend application marks it
 * reachable; a network failure, or a 5xx without a JSON body (the dev proxy's
 * ECONNREFUSED 500, an nginx 502/503/504 page), marks it unreachable. The
 * retry loop is owned by the banner component, so the request layer never
 * issues requests on its own.
 */

export type BackendStatus = "unknown" | "online" | "offline";

export const backendConnectivity = reactive({
  status: "unknown" as BackendStatus,
  /** ISO time the backend was first seen unreachable in the current outage. */
  offlineSince: null as string | null,
  lastError: "",
});

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

/** Human-readable target shown in the banner. */
export const backendTarget = apiBaseUrl || "同源 /api（开发环境经 Vite 代理转发）";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * True when the response did not come from the backend application itself.
 * Backend errors (including 503 SERVICE_BUSY and /health/ready 503) are JSON.
 * Responses without a headers object (test doubles) are treated as reachable.
 */
export function isGatewayFailure(response: Response): boolean {
  if (response.status < 500 || !response.headers || typeof response.headers.get !== "function") {
    return false;
  }
  const contentType = response.headers.get("content-type") ?? "";
  return !contentType.includes("json");
}

export function markBackendReachable(): void {
  backendConnectivity.status = "online";
  backendConnectivity.offlineSince = null;
  backendConnectivity.lastError = "";
}

export function markBackendUnreachable(reason: string): void {
  if (backendConnectivity.status !== "offline") {
    backendConnectivity.offlineSince = new Date().toISOString();
  }
  backendConnectivity.status = "offline";
  backendConnectivity.lastError = reason;
}

export async function trackedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(input, init);
  } catch (error) {
    if (!isAbort(error)) {
      markBackendUnreachable(error instanceof Error ? error.message : "网络请求失败");
    }
    throw error;
  }
  if (isGatewayFailure(response)) markBackendUnreachable(`网关返回 HTTP ${response.status}`);
  else markBackendReachable();
  return response;
}

/** One liveness probe; resolves to whether the backend answered. */
export async function probeBackend(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await trackedFetch(`${apiBaseUrl}/health/live`, {
      headers: { Accept: "application/json" },
      signal,
    });
    return response.ok;
  } catch {
    return false;
  }
}
