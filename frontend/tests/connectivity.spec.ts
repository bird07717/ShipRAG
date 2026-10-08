import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  backendConnectivity,
  isGatewayFailure,
  markBackendReachable,
  probeBackend,
  trackedFetch,
} from "@/api/connectivity";

function response(status: number, contentType: string | null): Response {
  const headers = new Headers();
  if (contentType) headers.set("content-type", contentType);
  return new Response(status === 204 ? null : "{}", { status, headers });
}

describe("backend connectivity tracking", () => {
  beforeEach(() => {
    backendConnectivity.status = "unknown";
    backendConnectivity.offlineSince = null;
    backendConnectivity.lastError = "";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("treats backend JSON errors (including 503 busy) as reachable", () => {
    expect(isGatewayFailure(response(503, "application/json"))).toBe(false);
    expect(isGatewayFailure(response(500, "application/json; charset=utf-8"))).toBe(false);
    expect(isGatewayFailure(response(404, null))).toBe(false);
  });

  it("treats proxy/gateway 5xx without a JSON body as unreachable", () => {
    expect(isGatewayFailure(response(500, null))).toBe(true); // Vite proxy ECONNREFUSED
    expect(isGatewayFailure(response(502, "text/html"))).toBe(true); // nginx
    expect(isGatewayFailure(response(504, "text/plain"))).toBe(true);
  });

  it("does not guess from test doubles without headers", () => {
    expect(isGatewayFailure({ ok: false, status: 500 } as Response)).toBe(false);
  });

  it("records network failures and recovers on the next real response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")));
    await expect(trackedFetch("/api/v1/x")).rejects.toThrow("Failed to fetch");
    expect(backendConnectivity.status).toBe("offline");
    expect(backendConnectivity.lastError).toBe("Failed to fetch");
    const since = backendConnectivity.offlineSince;
    expect(since).not.toBeNull();

    // A second failure keeps the original outage start.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(502, "text/html")));
    await trackedFetch("/api/v1/x");
    expect(backendConnectivity.offlineSince).toBe(since);
    expect(backendConnectivity.lastError).toContain("502");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(200, "application/json")));
    await trackedFetch("/api/v1/x");
    expect(backendConnectivity.status).toBe("online");
    expect(backendConnectivity.offlineSince).toBeNull();
  });

  it("ignores aborted requests", async () => {
    markBackendReachable();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    await expect(trackedFetch("/api/v1/x")).rejects.toThrow();
    expect(backendConnectivity.status).toBe("online");
  });

  it("probes the liveness endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(200, "application/json"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(probeBackend()).resolves.toBe(true);
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/\/health\/live$/);

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    await expect(probeBackend()).resolves.toBe(false);
    expect(backendConnectivity.status).toBe("offline");
  });
});
