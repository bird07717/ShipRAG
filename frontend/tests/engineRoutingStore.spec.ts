import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import { engineRoutingStore } from "@/composables/engineRoutingStore";
import type { RuntimePolicy } from "@/types/admin";

const policy: RuntimePolicy = {
  knowledge_id: "kb-1",
  execution_policy: "SUPPORT_WITH_RAG_FALLBACK",
  config_version: 2,
  fallback_conditions: ["NO_MATCH"],
  source: "DATABASE",
  readiness: {
    support: { ready: true, blockers: [], profile_count: 1, ready_profile_count: 1 },
    rag: { ready: true, blockers: [] },
  },
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

    await engineRoutingStore.load();

    expect(engineRoutingStore.policy).toEqual(policy);
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
