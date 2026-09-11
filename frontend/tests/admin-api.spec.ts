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
        json: () => Promise.resolve({ error: { message: "已有构建正在运行", details: { task_id: "task-1" } } }),
      }),
    );

    await expect(apiRequest("/indexes")).rejects.toEqual(
      new ApiRequestError("已有构建正在运行", 409, { task_id: "task-1" }),
    );
  });

  it("announces a rejected service credential so automatic requests can stop", async () => {
    const rejected = vi.fn();
    window.addEventListener("rag-service-auth-rejected", rejected, { once: true });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ error: { message: "未授权" } }),
      }),
    );

    await expect(apiRequest("/knowledge-bases")).rejects.toMatchObject({ status: 401 });
    expect(rejected).toHaveBeenCalledOnce();
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
    await adminApi.buildIndex("kb-1");
    await adminApi.activateIndex("index-1");
    await adminApi.listTraces();
    await adminApi.listTraces("kb-1");
    await adminApi.getTrace("trace-1");
    await adminApi.listModels();
    await adminApi.listPrompts();
    await adminApi.deleteKnowledgeBase("kb-1");

    expect(fetchMock).toHaveBeenCalledTimes(16);
    const upload = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(upload.body).toBeInstanceOf(FormData);
    expect(new Headers(upload.headers).get("Content-Type")).toBeNull();
    expect(new Headers(upload.headers).get("Idempotency-Key")).toBeTruthy();
    expect(fetchMock.mock.calls[3]?.[0]).toBe("/api/v1/documents/doc-1?request_build=true");
    expect((fetchMock.mock.calls[3]?.[1] as RequestInit).method).toBe("DELETE");
    expect(fetchMock.mock.calls[15]?.[0]).toBe("/api/v1/knowledge-bases/kb-1");
    expect((fetchMock.mock.calls[15]?.[1] as RequestInit).method).toBe("DELETE");
  });

  it("wires the complete management contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getSystemInfo();
    await adminApi.createKnowledgeBase("产品 B", "");
    await adminApi.getDocumentProfileContext("doc-1");
    await adminApi.getKnowledgeProfiles("kb-1");
    await adminApi.generateKnowledgeProfiles("kb-1");
    await adminApi.generateDocumentProfile("kb-1", "doc-1");
    await adminApi.updateDocumentProfile("kb-1", "doc-1", {} as never);
    await adminApi.checkKnowledgeProfiles("kb-1");
    await adminApi.publishKnowledgeProfiles("kb-1");
    await adminApi.discardPendingKnowledgeProfiles("kb-1");
    await adminApi.rebuildKnowledgeProfiles("kb-1", "idem-1");
    await adminApi.listChunks("doc-1", "idx-1");
    await adminApi.listIndexTasks("idx-1");
    await adminApi.getTask("task-1");
    await adminApi.cancelTask("task-1");
    await adminApi.gcIndexes("kb-1");
    await adminApi.deleteIndex("idx-1");
    await adminApi.deleteIndex("idx-1", true);
    await adminApi.listTraces(
      "kb-1",
      {
        status: "SUCCESS",
        responseType: "KNOWLEDGE",
        userId: "user-1",
        errorCode: "NONE",
        conversationId: "conv-1",
        engine: "RAG",
        source: "ASSISTANT_API",
        requestId: "req-1",
        from: "2026-09-01",
        to: "2026-09-02",
        limit: 50,
      },
      { signal: new AbortController().signal },
    );
    await adminApi.listNoAnswerTraces();
    await adminApi.getMetricsOverview();
    await adminApi.getMetricsTimeseries({
      knowledgeId: "kb-1",
      from: "2026-09-01",
      to: "2026-09-02",
      granularity: "day",
    });
    await adminApi.getMetricsLatency();
    await adminApi.getSupportMetrics({ knowledgeId: "kb-1", to: "2026-09-02" });
    await adminApi.getAssistantMetrics({ knowledgeId: "kb-1", days: 7 });
    await adminApi.getAssistantMetrics();
    await adminApi.listRequestErrors({ to: "2026-09-02" });
    await adminApi.listRequestErrors();
    await adminApi.getFailureSummary();
    await adminApi.listTasks({ from: "2026-09-01", to: "2026-09-02" });
    await adminApi.updateModel("model-1", {} as never);
    await adminApi.putApiKey("model-1", "test-api-key");
    await adminApi.deleteApiKey("model-1");
    await adminApi.probeModel("model-1");
    await adminApi.getRagConfig();
    await adminApi.updateRagConfig({} as never);
    await adminApi.listEngineRetrievalConfigs();
    await adminApi.updateEngineRetrievalConfig("RAG_ONLY", {} as never);
    await adminApi.getRuntimePolicy("kb-1");
    await adminApi.updateRuntimePolicy("kb-1", "RAG_ONLY");
    await adminApi.listProblemProfiles("kb-1");
    await adminApi.listKnowledgeReleases("kb-1");
    await adminApi.getKnowledgeRelease("release-1");
    await adminApi.generateProfileDrafts("idx-1");
    await adminApi.backfillProfileDrafts("idx-1");
    await adminApi.refreshProfileDrafts("idx-1");
    await adminApi.regenerateProfileDraft("draft-1");
    await adminApi.createProfileEditDraft("release-1", "profile-1");
    await adminApi.updateProfileDraft("draft-1", {} as never);
    await adminApi.validateKnowledgeRelease("release-1");
    await adminApi.publishKnowledgeRelease("release-1");
    await adminApi.rebaseKnowledgeRelease("release-1");
    await adminApi.rollbackSupportRelease("kb-1", "release-1");
    await adminApi.rollbackRagIndex("kb-1", "idx-1");
    await adminApi.listProfileFeedback("kb-1");

    expect(fetchMock).toHaveBeenCalledTimes(55);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(
      expect.arrayContaining([
        "/api/v1/system/info",
        "/api/v1/indexes/idx-1?force=true",
        "/api/v1/runtime-policies/kb-1",
        "/api/v1/knowledge-bases/kb-1/profile-feedback",
      ]),
    );
  });
});

describe("metrics API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps camelCase filters to snake_case query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ total_traces: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getMetricsOverview({ knowledgeId: "kb-1", from: "2026-08-14" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    // regression: previously emitted ?knowledgeId=... which the backend ignored
    expect(url).toContain("knowledge_id=kb-1");
    expect(url).not.toContain("knowledgeId");
    expect(url).toContain("from=2026-08-14");
  });

  it("omits the query string when no filters are set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getMetricsTimeseries();
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("?");
  });

  it("scopes unanswered traces to the selected knowledge base", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listNoAnswerTraces("kb-1", 25);

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("knowledge_id=kb-1");
    expect(url).toContain("response_type=UNCONFIRMED");
    expect(url).toContain("limit=25");
  });

  it("fetches latency metrics with granularity passthrough", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getMetricsLatency({ knowledgeId: "kb-1" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/metrics/latency?knowledge_id=kb-1");
  });
});

describe("logs & review API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds request-error list filters with pagination", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ items: [], total: 0, limit: 10, offset: 20 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listRequestErrors({
      requestId: " req-1 ",
      path: "support",
      statusCode: 502,
      from: "2026-08-14",
      limit: 10,
      offset: 20,
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/logs/request-errors?");
    expect(url).toContain("request_id=req-1");
    expect(url).toContain("path=support");
    expect(url).toContain("status_code=502");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=20");
  });

  it("fetches a single request error and the failure summary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getRequestError("e-1");
    await adminApi.getFailureSummary({ knowledgeId: "kb-1" });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/logs/request-errors/e-1");
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      "/logs/failure-summary?knowledge_id=kb-1",
    );
  });

  it("lists failed tasks and conversation messages", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listTasks({ status: "FAILED", knowledgeId: "kb-1", limit: 100 });
    const messages = await adminApi.listConversationMessages("conv-1");

    const tasksUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(tasksUrl).toContain("/api/v1/tasks?");
    expect(tasksUrl).toContain("status=FAILED");
    expect(tasksUrl).toContain("knowledge_id=kb-1");
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe("/api/v1/conversations/conv-1/messages");
    expect(messages).toEqual({ items: [] });
  });

  it("passes error_code and conversation_id trace filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listTraces(undefined, {
      status: "FAILED",
      errorCode: "INVALID_EVIDENCE",
      conversationId: "conv-1",
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("status=FAILED");
    expect(url).toContain("error_code=INVALID_EVIDENCE");
    expect(url).toContain("conversation_id=conv-1");
  });
});
