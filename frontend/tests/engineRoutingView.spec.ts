import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import { engineRoutingStore } from "@/composables/engineRoutingStore";
import type { KnowledgeBase, RuntimePolicy } from "@/types/admin";
import EngineRoutingView from "@/views/EngineRoutingView.vue";

const knowledgeBase: KnowledgeBase = {
  id: "kb-1",
  name: "产品库",
  description: null,
  status: "ENABLED",
  assistant_online: true,
  runtime_state: "READY",
  active_index_id: "index-1",
  building_index_id: null,
  rebuild_required: false,
  document_count: 1,
  active_chunk_count: 10,
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:00Z",
};

const policy: RuntimePolicy = {
  knowledge_id: "kb-1",
  execution_policy: "SUPPORT_WITH_RAG_FALLBACK",
  config_version: 1,
  fallback_conditions: ["NO_MATCH"],
  source: "DATABASE",
  readiness: {
    support: { ready: true, blockers: [], profile_count: 1, ready_profile_count: 1 },
    rag: { ready: true, blockers: [] },
  },
};

describe("EngineRoutingView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.knowledgeBases = [];
    adminStore.selectedKnowledgeId = "";
    engineRoutingStore.reset();
  });

  it("is the single UI that changes the selected knowledge-base policy", async () => {
    adminStore.knowledgeBases = [knowledgeBase];
    adminStore.selectedKnowledgeId = knowledgeBase.id;
    vi.spyOn(adminApi, "getRuntimePolicy").mockResolvedValue(policy);
    vi.spyOn(adminApi, "listProblemProfiles").mockResolvedValue([]);
    const update = vi.spyOn(adminApi, "updateRuntimePolicy").mockResolvedValue({
      ...policy,
      execution_policy: "RAG_ONLY",
      config_version: 2,
    });

    const wrapper = mount(EngineRoutingView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    await wrapper.get('input[value="RAG_ONLY"]').setValue();
    await wrapper.get("button.el-button--primary").trigger("click");
    await flushPromises();

    expect(update).toHaveBeenCalledWith("kb-1", "RAG_ONLY");
    expect(wrapper.text()).toContain("仅对新会话生效");
    wrapper.unmount();
  });
});
