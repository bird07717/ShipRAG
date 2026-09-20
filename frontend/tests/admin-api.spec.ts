import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi, apiRequest, ApiRequestError } from "@/api/admin";

describe("admin API", () => {
  it("keeps Assistant configuration writes on the management API with revisions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    await adminApi.getKnowledgeAssistant("kb-a");
    await adminApi.saveKnowledgeAssistant("kb-b", 2, ["如何检查设备电源"]);
    await adminApi.setKnowledgeAssistantPublication("kb-b", 3, true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/knowledge-bases/kb-a/assistant-config",
      "/api/v1/knowledge-bases/kb-b/assistant-config",
      "/api/v1/knowledge-bases/kb-b/assistant-publication",
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ revision: 2, suggestions: ["如何检查设备电源"] });
    expect(JSON.parse(fetchMock.mock.calls[2]![1].body)).toEqual({ revision: 3, assistant_online: true });
  });
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

  it("updates only the public knowledge-base description with optimistic concurrency", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.updateKnowledgeBase("kb-1", {
      description: "VAU 图像配置和故障排查",
      expected_updated_at: "2026-09-13T00:00:00Z",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/knowledge-bases/kb-1");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.method).toBe("PATCH");
    expect(JSON.parse(String(request.body))).toEqual({
      description: "VAU 图像配置和故障排查",
      expected_updated_at: "2026-09-13T00:00:00Z",
    });
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

  it("uses the management-only RAG document-card contract with revisions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    const card = {
      document_type: "PROCEDURE" as const, summary: "配置 VAU 图像",
      components: ["VAU"], tasks: ["配置图像"], applicable_questions: ["如何配置 VAU 图像"],
      prerequisites: [], key_topics: ["网络"], exclusions: [],
      has_procedure: true, has_images: true,
    };
    await adminApi.listRagDocumentCards("kb-1", "idx-1");
    await adminApi.getRagDocumentCardReadiness("kb-1", "idx-1");
    await adminApi.getRagDocumentCard("kb-1", "doc-1", "idx-1");
    await adminApi.generateRagDocumentCards("kb-1", {
      index_id: "idx-1", document_id: "doc-1", replace_confirmed: true,
    });
    await adminApi.updateRagDocumentCard("kb-1", "doc-1", "idx-1", 3, card);
    await adminApi.reviewRagDocumentCard("kb-1", "doc-1", "idx-1", 4, "CONFIRMED");

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/knowledge-bases/kb-1/rag-document-cards?index_id=idx-1",
      "/api/v1/knowledge-bases/kb-1/rag-document-cards/readiness?index_id=idx-1",
      "/api/v1/knowledge-bases/kb-1/documents/doc-1/rag-document-card?index_id=idx-1",
      "/api/v1/knowledge-bases/kb-1/rag-document-cards/generate",
      "/api/v1/knowledge-bases/kb-1/documents/doc-1/rag-document-card",
      "/api/v1/knowledge-bases/kb-1/documents/doc-1/rag-document-card/review",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1].body))).toEqual({
      index_id: "idx-1", expected_revision: 3, card_payload: card,
    });
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
    await adminApi.listUnconfirmedTraces();
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

    await adminApi.getMetricsOverview({
      knowledgeId: "kb-1",
      from: "2026-08-14",
      engine: "RAG",
      source: "ASSISTANT_API",
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    // regression: previously emitted ?knowledgeId=... which the backend ignored
    expect(url).toContain("knowledge_id=kb-1");
    expect(url).not.toContain("knowledgeId");
    expect(url).toContain("from=2026-08-14");
    expect(url).toContain("engine=RAG");
    expect(url).toContain("source=ASSISTANT_API");
  });

  it("omits the query string when no filters are set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.getMetricsTimeseries();
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("?");
  });

  it("scopes unconfirmed traces to the quality window and external RAG", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);

    await adminApi.listUnconfirmedTraces("kb-1", {
      from: "2026-09-13T00:00:00Z",
      to: "2026-09-20T00:00:00Z",
      engine: "RAG",
      source: "ASSISTANT_API",
      limit: 25,
    });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("knowledge_id=kb-1");
    expect(url).toContain("response_type=UNCONFIRMED");
    expect(url).toContain("status=COMPLETED");
    expect(url).toContain("engine=RAG");
    expect(url).toContain("source=ASSISTANT_API");
    expect(url).toContain("from=2026-09-13T00%3A00%3A00Z");
    expect(url).toContain("to=2026-09-20T00%3A00%3A00Z");
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
