import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import { engineRoutingStore } from "@/composables/engineRoutingStore";
import type { ProblemProfile, RuntimePolicy } from "@/types/admin";

const policy: RuntimePolicy = {
  knowledge_id: "kb-1",
  execution_policy: "SUPPORT_ONLY",
  config_version: 2,
  fallback_conditions: ["NO_MATCH"],
  source: "DATABASE",
};

const profile: ProblemProfile = {
  id: "profile-1",
  knowledge_id: "kb-1",
  document_id: "doc-1",
  problem_code: "P-001",
  canonical_question: "如何启动？",
  aliases: [],
  keywords: [],
  product_name: null,
  model_names: [],
  task_type: null,
  enabled: true,
  support_ready: true,
  readiness_reasons: [],
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:00Z",
};

describe("engine routing store", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
    engineRoutingStore.reset();
  });

  it("owns runtime policy loading for the selected knowledge base", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    vi.spyOn(adminApi, "getRuntimePolicy").mockResolvedValue(policy);
    vi.spyOn(adminApi, "listProblemProfiles").mockResolvedValue([profile]);

    await engineRoutingStore.load();

    expect(engineRoutingStore.policy).toEqual(policy);
    expect(engineRoutingStore.profiles).toEqual([profile]);
  });

  it("updates the single knowledge-base routing policy", async () => {
    adminStore.selectedKnowledgeId = "kb-1";
    const updated = { ...policy, execution_policy: "RAG_ONLY" as const, config_version: 3 };
    const update = vi.spyOn(adminApi, "updateRuntimePolicy").mockResolvedValue(updated);

    await engineRoutingStore.savePolicy("RAG_ONLY");

    expect(update).toHaveBeenCalledWith("kb-1", "RAG_ONLY");
    expect(engineRoutingStore.policy).toEqual(updated);
    expect(engineRoutingStore.notice).toContain("仅对新会话生效");
  });
});
