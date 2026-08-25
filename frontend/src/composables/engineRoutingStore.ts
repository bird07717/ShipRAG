import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { ExecutionPolicy, RuntimePolicy } from "@/types/admin";

export const engineRoutingStore = reactive({
  policy: null as RuntimePolicy | null,
  loading: false,
  error: "",
  notice: "",

  reset(): void {
    this.policy = null;
    this.loading = false;
    this.error = "";
    this.notice = "";
  },

  async load(): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    if (!knowledgeId) {
      this.reset();
      return;
    }
    this.loading = true;
    this.error = "";
    this.notice = "";
    try {
      const policy = await adminApi.getRuntimePolicy(knowledgeId);
      if (adminStore.selectedKnowledgeId !== knowledgeId) return;
      this.policy = policy;
    } catch (cause) {
      if (adminStore.selectedKnowledgeId !== knowledgeId) return;
      this.error = cause instanceof Error ? cause.message : "引擎路由配置加载失败";
    } finally {
      if (adminStore.selectedKnowledgeId === knowledgeId) this.loading = false;
    }
  },

  async savePolicy(executionPolicy: ExecutionPolicy): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    if (!knowledgeId) return;
    this.loading = true;
    this.error = "";
    this.notice = "";
    try {
      const policy = await adminApi.updateRuntimePolicy(knowledgeId, executionPolicy);
      if (adminStore.selectedKnowledgeId !== knowledgeId) return;
      this.policy = policy;
      this.notice = "问答引擎路由已保存，仅对新会话生效";
    } catch (cause) {
      if (adminStore.selectedKnowledgeId !== knowledgeId) return;
      this.error = cause instanceof Error ? cause.message : "问答引擎路由保存失败";
    } finally {
      if (adminStore.selectedKnowledgeId === knowledgeId) this.loading = false;
    }
  },
});

export function useEngineRoutingStore() {
  return engineRoutingStore;
}
