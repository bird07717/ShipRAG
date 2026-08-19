import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi, apiRequest, ApiRequestError } from "@/api/admin";

describe("admin API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("adds the session-only bearer token and parses JSON", async () => {
    sessionStorage.setItem("rag_service_token", "session-token");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{ id: "kb-1" }]),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(adminApi.listKnowledgeBases()).resolves.toEqual([{ id: "kb-1" }]);
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(request.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("returns the safe backend error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ error: { message: "已有构建正在运行" } }),
      }),
    );

    await expect(apiRequest("/indexes")).rejects.toEqual(
      new ApiRequestError("已有构建正在运行", 409),
    );
  });

  it("sends whitelisted playground options as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.runPlayground("kb-1", "端口？", { vector_top_k: 5 });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      knowledge_id: "kb-1",
      question: "端口？",
      options: { vector_top_k: 5 },
    });
    expect(new Headers(request.headers).get("Content-Type")).toBe("application/json");
  });

  it("exposes every M6 management operation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.createKnowledgeBase("产品 A", "说明");
    await adminApi.listDocuments("kb-1");
    await adminApi.uploadDocument(
      "kb-1",
      new File(["docx"], "manual.docx", { type: "application/octet-stream" }),
    );
    await adminApi.deleteDocument("doc-1");
    await adminApi.listElements("doc-1");
    await adminApi.listElements("doc-1", "index-1");
    await adminApi.listChunks("doc-1");
    await adminApi.listIndexes("kb-1");
    await adminApi.buildIndex("kb-1", false);
    await adminApi.activateIndex("index-1");
    await adminApi.listTraces();
    await adminApi.listTraces("kb-1");
    await adminApi.getTrace("trace-1");
    await adminApi.listModels();
    await adminApi.listPrompts();

    expect(fetchMock).toHaveBeenCalledTimes(15);
    const upload = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(upload.body).toBeInstanceOf(FormData);
    expect(new Headers(upload.headers).get("Content-Type")).toBeNull();
    expect(fetchMock.mock.calls[3]?.[0]).toBe("/api/v1/documents/doc-1?request_build=true");
    expect((fetchMock.mock.calls[3]?.[1] as RequestInit).method).toBe("DELETE");
  });
});
