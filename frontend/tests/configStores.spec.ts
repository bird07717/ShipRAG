import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import { engineRoutingStore } from "@/composables/engineRoutingStore";
import { ragAdminStore } from "@/composables/ragAdminStore";

const runtimePolicy = {
  knowledge_id: "kb-1",
  execution_policy: "RAG_ONLY" as const,
  config_version: 1,
  fallback_conditions: [],
  source: "DATABASE" as const,
  updated_at: "2026-09-11T00:00:00Z",
  readiness: {
    support: { ready: true, blockers: [], profile_count: 1, ready_profile_count: 1 },
    rag: { ready: true, blockers: [] },
  },
};

const engineConfig = {
  execution_policy: "RAG_ONLY" as const,
  vector_top_k: 10,
  bm25_top_k: 10,
  fusion_top_k: 20,
  rerank_top_n: 10,
};

describe("engineRoutingStore", () => {
  beforeEach(() => {
    engineRoutingStore.reset();
    adminStore.selectedKnowledgeId = "";
  });

  afterEach(() => vi.restoreAllMocks());

  it("resets when no knowledge base is selected", async () => {
    engineRoutingStore.policy = runtimePolicy;
    engineRoutingStore.error = "old";

    await engineRoutingStore.load();

    expect(engineRoutingStore.policy).toBeNull();
    expect(engineRoutingStore.error).toBe("");
  });

  it("loads and saves the selected knowledge policy", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    vi.spyOn(adminApi, "getRuntimePolicy").mockResolvedValue(runtimePolicy);
    vi.spyOn(adminApi, "updateRuntimePolicy").mockResolvedValue({
      ...runtimePolicy,
      execution_policy: "SUPPORT_WITH_RAG_FALLBACK",
    });

    await engineRoutingStore.load();
    await engineRoutingStore.savePolicy("SUPPORT_WITH_RAG_FALLBACK");

    expect(engineRoutingStore.policy?.execution_policy).toBe("SUPPORT_WITH_RAG_FALLBACK");
    expect(engineRoutingStore.notice).toContain("仅对新会话生效");
    expect(engineRoutingStore.loading).toBe(false);
  });

  it("reports load and save failures, including non-Error failures", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    vi.spyOn(adminApi, "getRuntimePolicy").mockRejectedValue(new Error("加载失败"));

    await engineRoutingStore.load();
    expect(engineRoutingStore.error).toBe("加载失败");

    vi.spyOn(adminApi, "updateRuntimePolicy").mockRejectedValue("offline");
    await engineRoutingStore.savePolicy("RAG_ONLY");
    expect(engineRoutingStore.error).toBe("问答引擎路由保存失败");

    adminStore.selectedKnowledgeId = "";
    await engineRoutingStore.savePolicy("RAG_ONLY");
    expect(adminApi.updateRuntimePolicy).toHaveBeenCalledTimes(1);
  });

  it("drops a response after the selected knowledge base changes", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    vi.spyOn(adminApi, "getRuntimePolicy").mockImplementation(async () => {
      adminStore.selectedKnowledgeId = "kb-2";
      return runtimePolicy;
    });

    await engineRoutingStore.load();

    expect(engineRoutingStore.policy).toBeNull();
    expect(engineRoutingStore.loading).toBe(true);
  });
});

describe("ragAdminStore", () => {
  beforeEach(() => {
    ragAdminStore.config = null;
    ragAdminStore.engineConfigs = [];
    ragAdminStore.loading = false;
    ragAdminStore.error = "";
    ragAdminStore.notice = "";
  });

  afterEach(() => vi.restoreAllMocks());

  it("loads and saves global and per-engine retrieval configuration", async () => {
    const config = { vector_top_k: 10 } as never;
    vi.spyOn(adminApi, "getRagConfig").mockResolvedValue(config);
    vi.spyOn(adminApi, "updateRagConfig").mockResolvedValue(config);
    vi.spyOn(adminApi, "listEngineRetrievalConfigs").mockResolvedValue([engineConfig] as never);
    vi.spyOn(adminApi, "updateEngineRetrievalConfig")
      .mockResolvedValueOnce({ ...engineConfig, vector_top_k: 12 } as never)
      .mockResolvedValueOnce({
        ...engineConfig,
        execution_policy: "SUPPORT_WITH_RAG_FALLBACK",
      } as never);

    await ragAdminStore.loadConfiguration();
    await ragAdminStore.saveConfig({ vector_top_k: 10 } as never);
    await ragAdminStore.loadEngineConfigurations();
    await ragAdminStore.saveEngineConfig("RAG_ONLY", { vector_top_k: 12 } as never);
    await ragAdminStore.saveEngineConfig(
      "SUPPORT_WITH_RAG_FALLBACK",
      { vector_top_k: 12 } as never,
    );

    expect(ragAdminStore.config).toEqual(config);
    expect(ragAdminStore.engineConfigs).toHaveLength(2);
    expect(ragAdminStore.engineConfigs[0]?.vector_top_k).toBe(12);
    expect(ragAdminStore.notice).toContain("下一轮请求生效");
  });

  it("normalizes Error and non-Error failures", async () => {
    await ragAdminStore.run(async () => {
      throw new Error("保存失败");
    });
    expect(ragAdminStore.error).toBe("保存失败");
    expect(ragAdminStore.loading).toBe(false);

    await ragAdminStore.run(async () => {
      throw "offline";
    });
    expect(ragAdminStore.error).toBe("RAG 管理操作失败");
  });
});
